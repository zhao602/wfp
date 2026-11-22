# =============================================================================
# 用户开发提交受理服务单元测试（tests/unit/test_dev_submission_service.py，B3/B4 段）
#
# 覆盖（T14.2 / T11 / T15~T17 / AC-DEV-09~18 / INV-D-20/21 / DC-03/DC-04）：
#   - submit 成功 → SUBMITTED + accepted=true + 解压包持久化落盘；
#   - 缺字段 / node_name·version 格式非法 / 空/非 ZIP 包 → 422 SUBMISSION_INVALID；
#   - 重复提交 → 409 DUPLICATE；防御预检超限 → 422 LIMIT_EXCEEDED；
#     路径穿越包拒绝且不落任何越界/包目录；
#   - 校验不通过 → REJECTED + 200 业务性结果（accepted=false + rejection_reason，
#     非 5xx，AC-DEV-08）；
#   - 仓储异常语义（T11.3）：写入失败 → 500 STORAGE_UNAVAILABLE（不得静默成功）；
#     判重只读失败降级为不重复；清单读取失败降级空清单；详情读取失败 → 500；
#   - list 筛选（state / node_name）；get 未知 → 404 SUBMISSION_NOT_FOUND；
#   - 仓储实现（T10.2/T10.3/T10.4，AC-DEV-16）：本地 JSONL 仅追加、幂等、
#     损坏跳过、新实例磁盘读回；PG 顶层零驱动可构造；工厂三分支；
#   - 审核状态机（T15）：合法迁移追加 StateTransitionRecord；越级/终态 →
#     409 INVALID_TRANSITION 且主体不变异；SECURITY_SCAN 自环合法；
#   - 审核流转（T16.1 / DV-6）：四步全链路至 PUBLISHED；operator_id 空 → 422；
#     未知 → 404；越级 409 状态不变；历史 from/to 有序；
#   - 上架衔接（T17 / DC-04 路径 1）：APPROVED→PUBLISHED 先上架；
#     载荷 entry_id 前缀 dev. + source_kind=user_dev + status=available；
#     description 取 manifest 描述并含版本（AC-DEV-14）；manifest 缺失降级；
#     上架失败 → 500 PUBLISH_FAILED 保持 APPROVED 可重试（T17.2）；
#   - 重新校验（T16.2 / DV-7 / AC-DEV-16）：报告追加不覆盖、状态与历史不变；
#     包目录缺失 → 500；
#   - 流转历史（T16.3 / DV-8）：按 occurred_at 有序返回。
# =============================================================================

import zipfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List, Optional

import pytest
import yaml

from orchestration_platform.domain.errors import (
    MARKET_DEV_INVALID_TRANSITION,
    MARKET_DEV_LIMIT_EXCEEDED,
    MARKET_DEV_PUBLISH_FAILED,
    MARKET_DEV_SUBMISSION_DUPLICATE,
    MARKET_DEV_SUBMISSION_INVALID,
    MARKET_DEV_SUBMISSION_NOT_FOUND,
    MARKET_STORAGE_UNAVAILABLE,
    DomainError,
    InstanceStateConflictError,
    ResourceNotFoundError,
    WorkflowValidationError,
)
from orchestration_platform.domain.models.enums import ContributionState
from orchestration_platform.market.dev_models import (
    DevHistoryResponse,
    DevSubmission,
    DevSubmissionResponse,
    DevTransitionResponse,
    DevValidationReport,
)
from orchestration_platform.market.dev_package_guard import DevPackageGuard
from orchestration_platform.market.dev_state_machine import DevSubmissionStateMachine
from orchestration_platform.market.dev_submission_service import (
    DevSubmissionService,
)
from orchestration_platform.mcp_tool_market.contribution.models import (
    StateTransitionRecord,
)
from orchestration_platform.persistence.abstractions.dev_submission_repositories import (
    IDevSubmissionRepository,
)
from orchestration_platform.persistence.local_file.local_file_dev_submission_repository import (
    LocalFileDevSubmissionRepository,
)
from orchestration_platform.persistence.postgres.postgres_dev_submission_repository import (
    PostgresDevSubmissionRepository,
)
from orchestration_platform.persistence.repository_factory import RepositoryFactory

_VALID_NODE_PY = "def run(query: str) -> str:\n    return query\n"

_EVIL_NODE_PY = "def run(q):\n    return eval(q)\n"


def _base_manifest() -> Dict[str, object]:
    """构造一份可通过三层校验的 manifest 数据。"""
    return {
        "node_name": "demo-node",
        "version": "1.0.0",
        "description": "demo node",
        "input_schema": {"query": "string"},
        "output_schema": {"result": "string"},
        "dependencies": [],
        "declared_file_paths": ["./data/"],
        "declared_network_targets": [],
        "entry_point": "node.py",
    }


def _make_package_zip(
    path: Path,
    manifest: Optional[Dict[str, object]] = None,
    node_py: str = _VALID_NODE_PY,
) -> Path:
    """构造提交包 ZIP（manifest.yaml + node.py）。"""
    data = _base_manifest() if manifest is None else manifest
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.yaml", yaml.safe_dump(data, allow_unicode=True))
        zf.writestr("node.py", node_py)
    return path


class _FakeRepository:
    """内存提交仓储（IDevSubmissionRepository 测试替身，可注入读/写失败）。"""

    def __init__(self, *, fail_save: bool = False, fail_read: bool = False) -> None:
        self._store: Dict[str, DevSubmission] = {}
        self.fail_save = fail_save
        self.fail_read = fail_read

    async def save(self, submission: DevSubmission) -> None:
        if self.fail_save:
            raise OSError("disk full")
        self._store[submission.submission_id] = submission

    async def get(self, submission_id: str) -> Optional[DevSubmission]:
        if self.fail_read:
            raise OSError("io error")
        return self._store.get(submission_id)

    async def list_all(self) -> List[DevSubmission]:
        if self.fail_read:
            raise OSError("io error")
        return list(self._store.values())

    async def list_by_state(
        self, state: ContributionState
    ) -> List[DevSubmission]:
        if self.fail_read:
            raise OSError("io error")
        return [s for s in self._store.values() if s.state == state]

    async def exists_by_node_version(
        self, developer_id: str, node_name: str, version: str
    ) -> bool:
        if self.fail_read:
            raise OSError("io error")
        return any(
            s.developer_id == developer_id
            and s.node_name == node_name
            and s.version == version
            for s in self._store.values()
        )


@pytest.fixture()
def service(tmp_path: Path) -> DevSubmissionService:
    """缺省受理服务（内存仓储 + 临时包根目录 + settings 缺省上限）。"""
    return DevSubmissionService(
        repository=_FakeRepository(),
        package_root=tmp_path / "packages",
    )


class TestSubmitSuccess:
    """提交成功路径（T11.1 / T11.2 无 error 分支）。"""

    async def test_valid_package_accepted_as_submitted(
        self, tmp_path: Path
    ) -> None:
        repo = _FakeRepository()
        svc = DevSubmissionService(
            repository=repo, package_root=tmp_path / "packages"
        )
        zip_path = _make_package_zip(tmp_path / "pkg.zip")

        response = await svc.submit("alice", "demo-node", "1.0.0", zip_path)

        assert isinstance(response, DevSubmissionResponse)
        assert response.state == ContributionState.SUBMITTED
        assert response.accepted is True
        assert response.validation_report.valid is True
        assert response.rejection_reason == ""
        assert response.package_size == zip_path.stat().st_size
        assert response.file_count == 2
        assert len(response.package_digest) == 64  # SHA-256 hex

        saved = await repo.get(response.submission_id)
        assert saved is not None
        assert saved.state == ContributionState.SUBMITTED
        assert saved.developer_id == "alice"
        assert len(saved.validation_reports) == 1

        # 解压包持久化于 {package_root}/{submission_id}/（支持 rescan，DC-03）
        package_dir = tmp_path / "packages" / response.submission_id
        assert (package_dir / "manifest.yaml").is_file()
        assert (package_dir / "node.py").is_file()

    async def test_fields_are_stripped_before_use(self, tmp_path: Path) -> None:
        svc = DevSubmissionService(
            repository=_FakeRepository(), package_root=tmp_path / "packages"
        )
        zip_path = _make_package_zip(tmp_path / "pkg.zip")
        response = await svc.submit("  alice  ", " demo-node ", " 1.0.0 ", zip_path)
        assert response.accepted is True


class TestSubmitRejections:
    """提交拒绝路径（字段 / 判重 / 防御预检）。"""

    async def test_missing_fields_invalid(self, service: DevSubmissionService,
                                          tmp_path: Path) -> None:
        zip_path = _make_package_zip(tmp_path / "pkg.zip")
        with pytest.raises(WorkflowValidationError) as exc_info:
            await service.submit("", "demo-node", "1.0.0", zip_path)
        assert exc_info.value.code == MARKET_DEV_SUBMISSION_INVALID

    async def test_node_name_format_invalid(
        self, service: DevSubmissionService, tmp_path: Path
    ) -> None:
        zip_path = _make_package_zip(tmp_path / "pkg.zip")
        with pytest.raises(WorkflowValidationError) as exc_info:
            await service.submit("alice", "Bad_Name", "1.0.0", zip_path)
        assert exc_info.value.code == MARKET_DEV_SUBMISSION_INVALID

    async def test_version_format_invalid(
        self, service: DevSubmissionService, tmp_path: Path
    ) -> None:
        zip_path = _make_package_zip(tmp_path / "pkg.zip")
        with pytest.raises(WorkflowValidationError) as exc_info:
            await service.submit("alice", "demo-node", "v1", zip_path)
        assert exc_info.value.code == MARKET_DEV_SUBMISSION_INVALID

    async def test_duplicate_rejected(
        self, service: DevSubmissionService, tmp_path: Path
    ) -> None:
        zip_path = _make_package_zip(tmp_path / "pkg.zip")
        await service.submit("alice", "demo-node", "1.0.0", zip_path)

        zip_path_2 = _make_package_zip(tmp_path / "pkg2.zip")
        with pytest.raises(InstanceStateConflictError) as exc_info:
            await service.submit("alice", "demo-node", "1.0.0", zip_path_2)
        assert exc_info.value.code == MARKET_DEV_SUBMISSION_DUPLICATE

    async def test_empty_package_invalid(
        self, service: DevSubmissionService, tmp_path: Path
    ) -> None:
        empty = tmp_path / "empty.zip"
        empty.write_bytes(b"")
        with pytest.raises(WorkflowValidationError) as exc_info:
            await service.submit("alice", "demo-node", "1.0.0", empty)
        assert exc_info.value.code == MARKET_DEV_SUBMISSION_INVALID

    async def test_not_a_zip_invalid(
        self, service: DevSubmissionService, tmp_path: Path
    ) -> None:
        bad = tmp_path / "bad.zip"
        bad.write_text("not a zip", encoding="utf-8")
        with pytest.raises(WorkflowValidationError) as exc_info:
            await service.submit("alice", "demo-node", "1.0.0", bad)
        assert exc_info.value.code == MARKET_DEV_SUBMISSION_INVALID

    async def test_guard_limit_exceeded(self, tmp_path: Path) -> None:
        with zipfile.ZipFile(tmp_path / "pkg.zip", "w") as zf:
            zf.writestr(
                "manifest.yaml",
                yaml.safe_dump(_base_manifest(), allow_unicode=True),
            )
            zf.writestr("node.py", _VALID_NODE_PY)
            zf.writestr("big.bin", b"x" * 2048)  # 超 1KB 单文件上限
        svc = DevSubmissionService(
            repository=_FakeRepository(),
            guard=DevPackageGuard(max_file_kb=1),
            package_root=tmp_path / "packages",
        )
        with pytest.raises(WorkflowValidationError) as exc_info:
            await svc.submit("alice", "demo-node", "1.0.0", tmp_path / "pkg.zip")
        assert exc_info.value.code == MARKET_DEV_LIMIT_EXCEEDED
        assert exc_info.value.details["findings"]

    async def test_traversal_package_rejected_without_extraction(
        self, tmp_path: Path
    ) -> None:
        zip_path = tmp_path / "evil.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr(
                "manifest.yaml",
                yaml.safe_dump(_base_manifest(), allow_unicode=True),
            )
            zf.writestr("node.py", _VALID_NODE_PY)
            zf.writestr("../evil.txt", b"x")
        svc = DevSubmissionService(
            repository=_FakeRepository(), package_root=tmp_path / "packages"
        )
        with pytest.raises(WorkflowValidationError) as exc_info:
            await svc.submit("alice", "demo-node", "1.0.0", zip_path)
        assert exc_info.value.code == MARKET_DEV_LIMIT_EXCEEDED
        assert not (tmp_path / "evil.txt").exists()
        # 预检阶段即拒绝：未创建任何包目录
        assert not (tmp_path / "packages").exists()


class TestValidationOutcome:
    """校验结果置位（T11.2：不合规 → REJECTED + 200 业务性结果）。"""

    async def test_invalid_package_rejected_with_business_result(
        self, tmp_path: Path
    ) -> None:
        repo = _FakeRepository()
        svc = DevSubmissionService(
            repository=repo, package_root=tmp_path / "packages"
        )
        zip_path = _make_package_zip(
            tmp_path / "pkg.zip", node_py=_EVIL_NODE_PY
        )  # L3 dangerous_call

        response = await svc.submit("alice", "demo-node", "1.0.0", zip_path)

        assert response.state == ContributionState.REJECTED
        assert response.accepted is False
        assert response.validation_report.valid is False
        assert response.rejection_reason != ""
        rule_ids = [
            f.rule_id for f in response.validation_report.findings
        ]
        assert "dangerous_call" in rule_ids

        saved = await repo.get(response.submission_id)
        assert saved is not None
        assert saved.state == ContributionState.REJECTED
        assert saved.rejection_reason != ""


class TestStorageSemantics:
    """仓储异常语义（T11.3 / INV-D-21 / AC-DEV-18）。"""

    async def test_save_failure_raises_storage_unavailable(
        self, tmp_path: Path
    ) -> None:
        repo = _FakeRepository(fail_save=True)
        svc = DevSubmissionService(
            repository=repo, package_root=tmp_path / "packages"
        )
        zip_path = _make_package_zip(tmp_path / "pkg.zip")
        with pytest.raises(DomainError) as exc_info:
            await svc.submit("alice", "demo-node", "1.0.0", zip_path)
        assert exc_info.value.code == MARKET_STORAGE_UNAVAILABLE
        # 保存失败不残留解压包目录
        packages_dir = tmp_path / "packages"
        assert not packages_dir.exists() or list(packages_dir.iterdir()) == []

    async def test_duplicate_check_read_failure_degrades_to_not_duplicate(
        self, tmp_path: Path
    ) -> None:
        repo = _FakeRepository(fail_read=True)  # 判重只读失败；save 正常
        svc = DevSubmissionService(
            repository=repo, package_root=tmp_path / "packages"
        )
        zip_path = _make_package_zip(tmp_path / "pkg.zip")
        response = await svc.submit("alice", "demo-node", "1.0.0", zip_path)
        assert response.accepted is True

    async def test_list_read_failure_degrades_to_empty(
        self, tmp_path: Path
    ) -> None:
        svc = DevSubmissionService(
            repository=_FakeRepository(fail_read=True),
            package_root=tmp_path / "packages",
        )
        assert await svc.list_submissions() == []
        assert (
            await svc.list_submissions(state=ContributionState.SUBMITTED) == []
        )

    async def test_get_read_failure_raises_storage_unavailable(
        self, tmp_path: Path
    ) -> None:
        svc = DevSubmissionService(
            repository=_FakeRepository(fail_read=True),
            package_root=tmp_path / "packages",
        )
        with pytest.raises(DomainError) as exc_info:
            await svc.get_submission("any-id")
        assert exc_info.value.code == MARKET_STORAGE_UNAVAILABLE

    async def test_get_unknown_raises_not_found(
        self, service: DevSubmissionService
    ) -> None:
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await service.get_submission("no-such-id")
        assert exc_info.value.code == MARKET_DEV_SUBMISSION_NOT_FOUND


class TestListAndGet:
    """清单与详情（DV-4 / DV-5 服务层）。"""

    async def test_list_and_filter(self, service: DevSubmissionService,
                                   tmp_path: Path) -> None:
        zip_a = _make_package_zip(tmp_path / "a.zip")
        resp_a = await service.submit("alice", "demo-node", "1.0.0", zip_a)

        manifest_b = _base_manifest()
        manifest_b["version"] = "1.1.0"
        zip_b = _make_package_zip(
            tmp_path / "b.zip", manifest=manifest_b, node_py=_EVIL_NODE_PY
        )
        resp_b = await service.submit("alice", "demo-node", "1.1.0", zip_b)
        assert resp_b.state == ContributionState.REJECTED

        all_submissions = await service.list_submissions()
        assert len(all_submissions) == 2

        submitted = await service.list_submissions(
            state=ContributionState.SUBMITTED
        )
        assert [s.submission_id for s in submitted] == [resp_a.submission_id]

        rejected = await service.list_submissions(
            state=ContributionState.REJECTED
        )
        assert [s.submission_id for s in rejected] == [resp_b.submission_id]

        by_node = await service.list_submissions(node_name="demo-node")
        assert len(by_node) == 2
        assert await service.list_submissions(node_name="other-node") == []

    async def test_get_submission_returns_saved(
        self, service: DevSubmissionService, tmp_path: Path
    ) -> None:
        zip_path = _make_package_zip(tmp_path / "pkg.zip")
        resp = await service.submit("alice", "demo-node", "1.0.0", zip_path)
        submission = await service.get_submission(resp.submission_id)
        assert submission.submission_id == resp.submission_id
        assert submission.state == ContributionState.SUBMITTED
        assert len(submission.validation_reports) == 1


# ---------------------------------------------------------------------------
# 仓储实现（T10.2 / T10.3 / T10.4 / AC-DEV-16）
# ---------------------------------------------------------------------------


def _make_report(report_id: str = "r1") -> DevValidationReport:
    """构造校验报告（仓储测试用）。"""
    return DevValidationReport(
        report_id=report_id,
        valid=True,
        layers=["L1", "L2", "L3"],
        findings=[],
        scanner_version="dev-validation/1.0",
        checked_at=datetime(2026, 11, 11, tzinfo=timezone.utc),
    )


def _make_submission(
    submission_id: str = "sub-1",
    developer_id: str = "alice",
    node_name: str = "demo-node",
    version: str = "1.0.0",
    state: ContributionState = ContributionState.SUBMITTED,
) -> DevSubmission:
    """构造开发提交主体（仓储测试用）。"""
    now = datetime(2026, 11, 11, tzinfo=timezone.utc)
    return DevSubmission(
        submission_id=submission_id,
        developer_id=developer_id,
        node_name=node_name,
        version=version,
        package_digest="d" * 64,
        package_size=100,
        file_count=2,
        state=state,
        validation_reports=[_make_report()],
        state_history=[],
        created_at=now,
        updated_at=now,
    )


class TestLocalFileDevSubmissionRepository:
    """本地文件仓储（T10.2：JSONL 仅追加 + 损坏跳过 + AC-DEV-16 单调递增）。"""

    async def test_save_get_roundtrip_and_jsonl_append(
        self, tmp_path: Path
    ) -> None:
        repo = LocalFileDevSubmissionRepository(storage_root=str(tmp_path))
        submission = _make_submission()
        await repo.save(submission)

        submission_dir = tmp_path / "submissions" / "sub-1"
        reports_path = submission_dir / "reports.jsonl"
        assert (submission_dir / "submission.json").is_file()
        assert len(reports_path.read_text(encoding="utf-8").splitlines()) == 1

        # 追加第二份报告 + 首条流转历史 → JSONL 行数单调递增（AC-DEV-16）
        submission.validation_reports.append(_make_report("r2"))
        submission.state_history.append(
            StateTransitionRecord(
                from_state=ContributionState.SUBMITTED,
                to_state=ContributionState.REVIEWING,
                operator_id="reviewer-1",
            )
        )
        await repo.save(submission)
        assert len(reports_path.read_text(encoding="utf-8").splitlines()) == 2
        history_lines = (
            (submission_dir / "state_history.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        )
        assert len(history_lines) == 1

        # 幂等重存不重复追加
        await repo.save(submission)
        assert len(reports_path.read_text(encoding="utf-8").splitlines()) == 2

    async def test_read_back_from_disk_with_fresh_instance(
        self, tmp_path: Path
    ) -> None:
        """新实例（空缓存）从磁盘读回，证明持久化非内存态。"""
        await LocalFileDevSubmissionRepository(storage_root=str(tmp_path)).save(
            _make_submission()
        )
        fresh = LocalFileDevSubmissionRepository(storage_root=str(tmp_path))
        loaded = await fresh.get("sub-1")
        assert loaded is not None
        assert loaded.node_name == "demo-node"
        assert len(loaded.validation_reports) == 1
        assert await fresh.exists_by_node_version(
            "alice", "demo-node", "1.0.0"
        )
        assert not await fresh.exists_by_node_version(
            "bob", "demo-node", "1.0.0"
        )
        assert await fresh.list_by_state(ContributionState.SUBMITTED) == [
            loaded
        ]
        assert await fresh.list_by_state(ContributionState.REJECTED) == []

    async def test_corrupt_json_skipped(self, tmp_path: Path) -> None:
        repo = LocalFileDevSubmissionRepository(storage_root=str(tmp_path))
        bad_dir = tmp_path / "submissions" / "bad-id"
        bad_dir.mkdir(parents=True)
        (bad_dir / "submission.json").write_text(
            "{not valid json", encoding="utf-8"
        )
        assert await repo.get("bad-id") is None
        assert await repo.list_all() == []
        assert not await repo.exists_by_node_version(
            "alice", "demo-node", "1.0.0"
        )

    async def test_list_all_sorted_by_created_at(self, tmp_path: Path) -> None:
        repo = LocalFileDevSubmissionRepository(storage_root=str(tmp_path))
        # 目录名顺序（sub-1, sub-2）与创建时间顺序（sub-2 早于 sub-1）相反
        for index, second in ((1, 2), (2, 1)):
            submission = _make_submission(submission_id=f"sub-{index}")
            submission.created_at = datetime(
                2026, 11, 11, 0, 0, second, tzinfo=timezone.utc
            )
            await repo.save(submission)
        ids = [s.submission_id for s in await repo.list_all()]
        assert ids == ["sub-2", "sub-1"]


class TestPostgresDevSubmissionRepositoryStructure:
    """PostgreSQL 仓储结构约束（T10.3 / INV-DEV-08/11）。"""

    def test_constructible_without_driver(self) -> None:
        repo = PostgresDevSubmissionRepository(connection_string="host=x")
        assert repo._conninfo() == "host=x"

    def test_no_driver_symbol_at_module_level(self) -> None:
        """顶层零 psycopg / 零 asyncpg（方法内延迟导入不进模块全局）。"""
        from orchestration_platform.persistence.postgres import (
            postgres_dev_submission_repository as pg_module,
        )

        assert not hasattr(pg_module, "psycopg")
        assert not hasattr(pg_module, "asyncpg")


class TestRepositoryFactoryDevSubmission:
    """仓储工厂扩展（T10.4 / CHG-19 / DC-03）。"""

    def test_create_local_file_repository(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        repo = RepositoryFactory.create_dev_submission_repository("local_file")
        assert isinstance(repo, LocalFileDevSubmissionRepository)
        assert isinstance(repo, IDevSubmissionRepository)

    def test_create_postgres_repository(self):
        repo = RepositoryFactory.create_dev_submission_repository("postgres")
        assert isinstance(repo, PostgresDevSubmissionRepository)
        assert isinstance(repo, IDevSubmissionRepository)

    def test_unknown_backend_raises_value_error(self):
        with pytest.raises(ValueError):
            RepositoryFactory.create_dev_submission_repository(
                "unsupported_backend"
            )


# ---------------------------------------------------------------------------
# 审核链路（T15 / T16 / T17 / DC-04，B4 段）
# ---------------------------------------------------------------------------


class _FakeMarketService:
    """AgentMarketService 测试替身：记录上架载荷，可注入上架失败。"""

    def __init__(self, *, fail_publish: bool = False) -> None:
        self.payloads: List[Dict[str, object]] = []
        self.fail_publish = fail_publish

    async def register_entry(
        self, payload: Dict[str, object]
    ) -> SimpleNamespace:
        if self.fail_publish:
            raise RuntimeError("market down")
        self.payloads.append(payload)
        return SimpleNamespace(result="created")


def _seed_package_dir(
    package_root: Path, submission_id: str = "sub-1"
) -> Path:
    """落一份可通过三层校验的解压包目录（rescan / 上架描述用）。"""
    package_dir = package_root / submission_id
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "manifest.yaml").write_text(
        yaml.safe_dump(_base_manifest(), allow_unicode=True), encoding="utf-8"
    )
    (package_dir / "node.py").write_text(_VALID_NODE_PY, encoding="utf-8")
    return package_dir


class TestDevSubmissionStateMachine:
    """状态机薄适配直测（T15 / AC-DEV-11）。"""

    def test_legal_transition_appends_record(self) -> None:
        submission = _make_submission(state=ContributionState.SUBMITTED)
        result = DevSubmissionStateMachine.transition(
            submission,
            ContributionState.SECURITY_SCAN,
            "scanner-1",
            "安全扫描开始",
        )
        assert result is submission
        assert submission.state == ContributionState.SECURITY_SCAN
        assert len(submission.state_history) == 1
        record = submission.state_history[0]
        assert record.from_state == ContributionState.SUBMITTED
        assert record.to_state == ContributionState.SECURITY_SCAN
        assert record.operator_id == "scanner-1"
        assert record.reason == "安全扫描开始"
        assert record.occurred_at is not None

    def test_illegal_transition_rejected_without_mutation(self) -> None:
        submission = _make_submission(state=ContributionState.SUBMITTED)
        with pytest.raises(InstanceStateConflictError) as exc_info:
            DevSubmissionStateMachine.transition(
                submission, ContributionState.PUBLISHED, "reviewer-1"
            )
        assert exc_info.value.code == MARKET_DEV_INVALID_TRANSITION
        assert submission.state == ContributionState.SUBMITTED
        assert submission.state_history == []

    def test_terminal_state_rejected(self) -> None:
        for terminal in (
            ContributionState.PUBLISHED,
            ContributionState.REJECTED,
        ):
            submission = _make_submission(state=terminal)
            with pytest.raises(InstanceStateConflictError) as exc_info:
                DevSubmissionStateMachine.transition(
                    submission, ContributionState.REVIEWING, "reviewer-1"
                )
            assert exc_info.value.code == MARKET_DEV_INVALID_TRANSITION
            assert submission.state == terminal
            assert submission.state_history == []

    def test_security_scan_self_loop_allowed(self) -> None:
        submission = _make_submission(state=ContributionState.SECURITY_SCAN)
        DevSubmissionStateMachine.transition(
            submission, ContributionState.SECURITY_SCAN, "scanner-1"
        )
        assert submission.state == ContributionState.SECURITY_SCAN
        assert len(submission.state_history) == 1


class TestTransitionReviewFlow:
    """审核流转（T16.1 / DV-6 / AC-DEV-11/13/20）。"""

    async def test_full_chain_to_published(self, tmp_path: Path) -> None:
        market = _FakeMarketService()
        repo = _FakeRepository()
        svc = DevSubmissionService(
            repository=repo,
            package_root=tmp_path / "packages",
            market_service=market,
        )
        await repo.save(_make_submission(state=ContributionState.SUBMITTED))

        steps = [
            ContributionState.SECURITY_SCAN,
            ContributionState.REVIEWING,
            ContributionState.APPROVED,
            ContributionState.PUBLISHED,
        ]
        for to_state in steps:
            response = await svc.transition(
                "sub-1", to_state, "reviewer-1"
            )
            assert isinstance(response, DevTransitionResponse)
            assert response.success is True
            assert response.to_state == to_state
            assert response.message == (
                "上架成功（created）" if to_state == ContributionState.PUBLISHED else ""
            )

        assert len(market.payloads) == 1
        saved = await repo.get("sub-1")
        assert saved is not None
        assert saved.state == ContributionState.PUBLISHED
        assert [r.to_state for r in saved.state_history] == steps
        assert saved.state_history[0].from_state == ContributionState.SUBMITTED
        assert saved.state_history[-1].from_state == ContributionState.APPROVED

    async def test_skip_level_transition_rejected_state_unchanged(
        self, tmp_path: Path
    ) -> None:
        market = _FakeMarketService()
        repo = _FakeRepository()
        svc = DevSubmissionService(
            repository=repo,
            package_root=tmp_path / "packages",
            market_service=market,
        )
        await repo.save(_make_submission(state=ContributionState.SUBMITTED))

        with pytest.raises(InstanceStateConflictError) as exc_info:
            await svc.transition(
                "sub-1", ContributionState.PUBLISHED, "reviewer-1"
            )
        assert exc_info.value.code == MARKET_DEV_INVALID_TRANSITION
        saved = await repo.get("sub-1")
        assert saved is not None
        assert saved.state == ContributionState.SUBMITTED
        assert saved.state_history == []
        assert market.payloads == []  # 预检先于上架：未触达市场（INV-D-20）

    async def test_terminal_submission_rejects_further_transition(
        self, tmp_path: Path
    ) -> None:
        repo = _FakeRepository()
        svc = DevSubmissionService(
            repository=repo, package_root=tmp_path / "packages"
        )
        await repo.save(_make_submission(state=ContributionState.REJECTED))
        with pytest.raises(InstanceStateConflictError) as exc_info:
            await svc.transition(
                "sub-1", ContributionState.REVIEWING, "reviewer-1"
            )
        assert exc_info.value.code == MARKET_DEV_INVALID_TRANSITION

    async def test_blank_operator_id_rejected(self, tmp_path: Path) -> None:
        repo = _FakeRepository()
        svc = DevSubmissionService(
            repository=repo, package_root=tmp_path / "packages"
        )
        await repo.save(_make_submission(state=ContributionState.SUBMITTED))
        for blank in ("", "   "):
            with pytest.raises(WorkflowValidationError) as exc_info:
                await svc.transition(
                    "sub-1", ContributionState.SECURITY_SCAN, blank
                )
            assert exc_info.value.code == MARKET_DEV_SUBMISSION_INVALID
        saved = await repo.get("sub-1")
        assert saved is not None
        assert saved.state == ContributionState.SUBMITTED

    async def test_unknown_submission_not_found(self, tmp_path: Path) -> None:
        svc = DevSubmissionService(
            repository=_FakeRepository(), package_root=tmp_path / "packages"
        )
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await svc.transition(
                "no-such-id", ContributionState.REVIEWING, "reviewer-1"
            )
        assert exc_info.value.code == MARKET_DEV_SUBMISSION_NOT_FOUND

    async def test_reason_recorded_in_history(self, tmp_path: Path) -> None:
        repo = _FakeRepository()
        svc = DevSubmissionService(
            repository=repo, package_root=tmp_path / "packages"
        )
        await repo.save(_make_submission(state=ContributionState.REVIEWING))
        await svc.transition(
            "sub-1",
            ContributionState.REJECTED,
            "reviewer-1",
            reason="扫描发现高危调用",
        )
        saved = await repo.get("sub-1")
        assert saved is not None
        assert saved.state == ContributionState.REJECTED
        assert saved.state_history[0].reason == "扫描发现高危调用"


class TestPublishToAgentMarket:
    """上架衔接（T17 / DC-04 路径 1 / AC-DEV-13/14）。"""

    async def test_publish_payload_shape_and_description(
        self, tmp_path: Path
    ) -> None:
        market = _FakeMarketService()
        repo = _FakeRepository()
        svc = DevSubmissionService(
            repository=repo,
            package_root=tmp_path / "packages",
            market_service=market,
        )
        _seed_package_dir(tmp_path / "packages")
        await repo.save(_make_submission(state=ContributionState.APPROVED))

        response = await svc.transition(
            "sub-1", ContributionState.PUBLISHED, "reviewer-1"
        )
        assert response.message == "上架成功（created）"

        assert len(market.payloads) == 1
        payload = market.payloads[0]
        assert payload["entry_id"] == "dev.demo-node"  # 前缀承载来源标注
        assert payload["name"] == "demo-node"
        assert payload["source_kind"] == "user_dev"
        assert payload["status"] == "available"
        assert payload["category"] == "general"
        assert payload["tags"] == []
        # description 取 manifest 描述并含版本（AC-DEV-14）
        assert payload["description"] == "demo node（版本 1.0.0）"

    async def test_publish_description_fallback_without_manifest(
        self, tmp_path: Path
    ) -> None:
        market = _FakeMarketService()
        repo = _FakeRepository()
        svc = DevSubmissionService(
            repository=repo,
            package_root=tmp_path / "packages",
            market_service=market,
        )
        await repo.save(_make_submission(state=ContributionState.APPROVED))

        await svc.transition(
            "sub-1", ContributionState.PUBLISHED, "reviewer-1"
        )
        payload = market.payloads[0]
        assert (
            payload["description"]
            == "用户开发节点 demo-node（版本 1.0.0）"
        )

    async def test_publish_failure_keeps_approved_and_retryable(
        self, tmp_path: Path
    ) -> None:
        market = _FakeMarketService(fail_publish=True)
        repo = _FakeRepository()
        svc = DevSubmissionService(
            repository=repo,
            package_root=tmp_path / "packages",
            market_service=market,
        )
        await repo.save(_make_submission(state=ContributionState.APPROVED))

        with pytest.raises(DomainError) as exc_info:
            await svc.transition(
                "sub-1", ContributionState.PUBLISHED, "reviewer-1"
            )
        assert exc_info.value.code == MARKET_DEV_PUBLISH_FAILED
        saved = await repo.get("sub-1")
        assert saved is not None
        assert saved.state == ContributionState.APPROVED  # 保持可重试（T17.2）
        assert saved.state_history == []
        assert market.payloads == []

        # 上架恢复后重试成功
        market.fail_publish = False
        response = await svc.transition(
            "sub-1", ContributionState.PUBLISHED, "reviewer-1"
        )
        assert response.to_state == ContributionState.PUBLISHED
        assert len(market.payloads) == 1


class TestRescan:
    """重新校验（T16.2 / DV-7 / AC-DEV-16）。"""

    async def test_rescan_appends_report_without_state_change(
        self, tmp_path: Path
    ) -> None:
        repo = _FakeRepository()
        svc = DevSubmissionService(
            repository=repo, package_root=tmp_path / "packages"
        )
        _seed_package_dir(tmp_path / "packages")
        await repo.save(
            _make_submission(state=ContributionState.SECURITY_SCAN)
        )

        report = await svc.rescan("sub-1", "scanner-1")
        assert isinstance(report, DevValidationReport)
        assert report.valid is True

        saved = await repo.get("sub-1")
        assert saved is not None
        assert len(saved.validation_reports) == 2  # 追加不覆盖（AC-DEV-16）
        assert saved.state == ContributionState.SECURITY_SCAN  # 状态不变
        assert saved.state_history == []

        # 再次 rescan → 报告数继续单调递增
        await svc.rescan("sub-1", "scanner-1")
        saved = await repo.get("sub-1")
        assert saved is not None
        assert len(saved.validation_reports) == 3

    async def test_rescan_missing_package_dir_storage_unavailable(
        self, tmp_path: Path
    ) -> None:
        repo = _FakeRepository()
        svc = DevSubmissionService(
            repository=repo, package_root=tmp_path / "packages"
        )
        await repo.save(_make_submission(state=ContributionState.REVIEWING))
        with pytest.raises(DomainError) as exc_info:
            await svc.rescan("sub-1", "reviewer-1")
        assert exc_info.value.code == MARKET_STORAGE_UNAVAILABLE
        saved = await repo.get("sub-1")
        assert saved is not None
        assert len(saved.validation_reports) == 1  # 未追加

    async def test_rescan_blank_operator_id_rejected(
        self, tmp_path: Path
    ) -> None:
        svc = DevSubmissionService(
            repository=_FakeRepository(), package_root=tmp_path / "packages"
        )
        with pytest.raises(WorkflowValidationError) as exc_info:
            await svc.rescan("sub-1", "  ")
        assert exc_info.value.code == MARKET_DEV_SUBMISSION_INVALID

    async def test_rescan_unknown_submission_not_found(
        self, tmp_path: Path
    ) -> None:
        svc = DevSubmissionService(
            repository=_FakeRepository(), package_root=tmp_path / "packages"
        )
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await svc.rescan("no-such-id", "scanner-1")
        assert exc_info.value.code == MARKET_DEV_SUBMISSION_NOT_FOUND


class TestGetHistory:
    """流转历史（T16.3 / DV-8 / AC-DEV-12）。"""

    async def test_history_sorted_and_shaped(self, tmp_path: Path) -> None:
        repo = _FakeRepository()
        svc = DevSubmissionService(
            repository=repo, package_root=tmp_path / "packages"
        )
        submission = _make_submission(state=ContributionState.SECURITY_SCAN)
        submission.state_history = [
            StateTransitionRecord(
                from_state=ContributionState.SUBMITTED,
                to_state=ContributionState.SECURITY_SCAN,
                operator_id="scanner-1",
                occurred_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
            )
        ]
        await repo.save(submission)

        history = await svc.get_history("sub-1")
        assert isinstance(history, DevHistoryResponse)
        assert history.submission_id == "sub-1"
        assert [r.to_state for r in history.history] == [
            ContributionState.SECURITY_SCAN
        ]

        await svc.transition(
            "sub-1", ContributionState.REVIEWING, "reviewer-1"
        )
        history = await svc.get_history("sub-1")
        assert [r.to_state for r in history.history] == [
            ContributionState.SECURITY_SCAN,
            ContributionState.REVIEWING,
        ]  # occurred_at 稳定有序
        assert history.history[0].operator_id == "scanner-1"
        assert history.history[1].operator_id == "reviewer-1"

    async def test_history_unknown_submission_not_found(
        self, tmp_path: Path
    ) -> None:
        svc = DevSubmissionService(
            repository=_FakeRepository(), package_root=tmp_path / "packages"
        )
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await svc.get_history("no-such-id")
        assert exc_info.value.code == MARKET_DEV_SUBMISSION_NOT_FOUND
