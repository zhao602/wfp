# =============================================================================
# 用户开发提交受理服务（market/dev_submission_service.py）
#
# 职责（agent_market_user_development T11 / CHG-06 / DC-03 / DC-04）：
#   1. submit()：字段校验 → 重复提交检查 exists_by_node_version →
#      DevPackageGuard.precheck() → safe_extract() →
#      DevValidationService.validate() → 追加报告 → 状态置位（T11.1 编排序）；
#   2. 状态置位语义（T11.2）：无 error → SUBMITTED（不直接上架）；有 error →
#      REJECTED + rejection_reason，返回 200 业务性结果（valid=false，非 5xx）；
#   3. 仓储异常语义（T11.3）：写入失败 → 500 MARKET_STORAGE_UNAVAILABLE
#      （不得静默成功）；只读查询异常 → 降级空清单且 2xx
#      （对齐 agent_market_service.py:330-347 范式，INV-D-21）；
#   4. 审核流转（T16 / DC-04）：transition() 合法性预检（纯函数只读调用）
#      → APPROVED→PUBLISHED 先上架（失败保持 APPROVED 可重试，T17.2）→
#      状态机迁移追加 StateTransitionRecord → 落盘；rescan() 重新三层
#      校验追加报告（不覆盖历史，AC-DEV-16）；get_history() 按时间有序；
#   5. 上架衔接（T17 / DC-04 路径 1）：_publish_to_agent_market() 调既有
#      AgentMarketService.register_entry（只调用、零改动）；entry_id 前缀
#      dev. 承载「用户开发」来源标注（spec AC-DEV-14）；register_entry
#      现版硬编码 source_kind="registered" 且属零改动登记，payload 携带
#      的 source_kind 键被其忽略不致错（design §1.2.7 路径 1）。
# 约束：不 import 用户模块、不装载用户包（DC-05 机检）；
#      Python 3.10 兼容；零第三方新增依赖。
# =============================================================================

import re
import shutil
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import yaml

from orchestration_platform.configuration.settings import get_settings
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
from orchestration_platform.market.agent_market_service import AgentMarketService
from orchestration_platform.market.dev_models import (
    DevFinding,
    DevHistoryResponse,
    DevSubmission,
    DevSubmissionResponse,
    DevTransitionResponse,
    DevValidationReport,
)
from orchestration_platform.market.dev_package_guard import DevPackageGuard
from orchestration_platform.market.dev_state_machine import DevSubmissionStateMachine
from orchestration_platform.market.dev_validation_service import DevValidationService
from orchestration_platform.market.models import MarketEntryStatus
from orchestration_platform.mcp_tool_market.contribution.state_machine import (
    ContributionStateMachine,
)
from orchestration_platform.persistence.abstractions.dev_submission_repositories import (
    IDevSubmissionRepository,
)
from orchestration_platform.persistence.repository_factory import RepositoryFactory

_NODE_NAME_PATTERN = re.compile(r"^[a-z0-9-]+$")
_VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")


def _utcnow() -> datetime:
    """当前 UTC 时间（带时区，可序列化）。"""
    return datetime.now(timezone.utc)


def _findings_details(findings: List[DevFinding]) -> List[Dict[str, Any]]:
    """防御发现项序列化为异常 details 附件（结构化逐项，AC-DEV-19）。"""
    return [finding.model_dump(mode="json") for finding in findings]


class DevSubmissionService:
    """用户开发提交受理服务（DC-03 / DV-3~DV-5 编排层）。"""

    def __init__(
        self,
        repository: IDevSubmissionRepository,
        validation_service: Optional[DevValidationService] = None,
        guard: Optional[DevPackageGuard] = None,
        package_root: Optional[Union[str, Path]] = None,
        market_service: Optional[AgentMarketService] = None,
    ) -> None:
        """构造受理服务。

        参数：
            repository         —— 提交仓储（IDevSubmissionRepository）
            validation_service —— 三层校验服务；缺省按 settings 构造
            guard              —— 提交侧防御器；缺省按 settings 构造
            package_root       —— 解压包落盘根目录；缺省取
                                   {agent_market_dev_storage_root}/packages
            market_service     —— Agent 市场服务（上架衔接，只调用）；
                                   缺省在上架时经仓储工厂延迟构造
        """
        settings = get_settings()
        self._repository = repository
        self._validation = validation_service or DevValidationService(
            max_manifest_kb=settings.agent_market_dev_max_manifest_kb
        )
        self._guard = guard or DevPackageGuard.from_settings()
        self._market = market_service
        if package_root is not None:
            self._package_root = Path(package_root)
        else:
            self._package_root = (
                Path(settings.agent_market_dev_storage_root) / "packages"
            )

    async def submit(
        self,
        developer_id: str,
        node_name: str,
        version: str,
        zip_path: Union[str, Path],
    ) -> DevSubmissionResponse:
        """受理一次提交（T11.1 编排序，DV-3）。"""
        developer = (developer_id or "").strip()
        node = (node_name or "").strip()
        ver = (version or "").strip()
        if not developer or not node or not ver:
            raise WorkflowValidationError(
                code=MARKET_DEV_SUBMISSION_INVALID,
                message="提交关键字段缺失：developer_id / node_name / version 均为必填",
                details={
                    "developer_id": developer_id,
                    "node_name": node_name,
                    "version": version,
                },
            )
        if not _NODE_NAME_PATTERN.match(node):
            raise WorkflowValidationError(
                code=MARKET_DEV_SUBMISSION_INVALID,
                message=f"提交关键字段非法：node_name 须匹配 ^[a-z0-9-]+$: {node!r}",
                details={"node_name": node},
            )
        if not _VERSION_PATTERN.match(ver):
            raise WorkflowValidationError(
                code=MARKET_DEV_SUBMISSION_INVALID,
                message=f"提交关键字段非法：version 须为语义化格式（主.次.修订）: {ver!r}",
                details={"version": ver},
            )

        zip_file = Path(zip_path)
        if not zip_file.is_file() or zip_file.stat().st_size == 0:
            raise WorkflowValidationError(
                code=MARKET_DEV_SUBMISSION_INVALID,
                message="包体不可读：文件不存在、非普通文件或为空",
                details={"package": zip_file.name},
            )

        # 重复提交检查（只读探测失败降级为「不重复」，对齐 T11.3 只读降级范式）
        try:
            duplicated = await self._repository.exists_by_node_version(
                developer, node, ver
            )
        except Exception:
            duplicated = False
        if duplicated:
            raise InstanceStateConflictError(
                code=MARKET_DEV_SUBMISSION_DUPLICATE,
                message=f"重复提交：{developer}/{node}@{ver} 已存在",
                details={
                    "developer_id": developer,
                    "node_name": node,
                    "version": ver,
                },
            )

        # 提交侧防御预检（非 ZIP / BadZipFile → 422 SUBMISSION_INVALID）
        try:
            guard_findings = self._guard.precheck(zip_file)
        except zipfile.BadZipFile:
            raise WorkflowValidationError(
                code=MARKET_DEV_SUBMISSION_INVALID,
                message="包体不是合法 ZIP 文件",
                details={"package": zip_file.name},
            ) from None
        if guard_findings:
            raise WorkflowValidationError(
                code=MARKET_DEV_LIMIT_EXCEEDED,
                message="包体未通过提交侧防御预检（超限或安全向量）",
                details={"findings": _findings_details(guard_findings)},
            )

        # 流式安全解压（持久化落盘以支持 rescan，DC-03）
        submission_id = uuid.uuid4().hex
        package_dir = self._package_root / submission_id
        try:
            extract_findings = self._guard.safe_extract(zip_file, package_dir)
        except zipfile.BadZipFile:
            shutil.rmtree(package_dir, ignore_errors=True)
            raise WorkflowValidationError(
                code=MARKET_DEV_SUBMISSION_INVALID,
                message="包体损坏或不是合法 ZIP 文件",
                details={"package": zip_file.name},
            ) from None
        except RuntimeError:
            # zipfile 对加密成员抛 RuntimeError（File is encrypted）
            shutil.rmtree(package_dir, ignore_errors=True)
            raise WorkflowValidationError(
                code=MARKET_DEV_SUBMISSION_INVALID,
                message="包体包含加密成员，不支持受理",
                details={"package": zip_file.name},
            ) from None
        except OSError as storage_exc:
            shutil.rmtree(package_dir, ignore_errors=True)
            raise DomainError(
                code=MARKET_STORAGE_UNAVAILABLE,
                message="市场存储不可用，请稍后重试",
                details={"reason": str(storage_exc)},
            ) from storage_exc
        if extract_findings:
            shutil.rmtree(package_dir, ignore_errors=True)
            raise WorkflowValidationError(
                code=MARKET_DEV_LIMIT_EXCEEDED,
                message="包体解压防御复核未通过（路径逃逸或体积异常）",
                details={"findings": _findings_details(extract_findings)},
            )

        # 三层校验 + 包摘要 + 规模（DC-02）
        report = self._validation.validate(package_dir)
        digest = self._validation.compute_digest(package_dir)
        package_size = zip_file.stat().st_size
        with zipfile.ZipFile(zip_file) as zf:
            file_count = sum(1 for info in zf.infolist() if not info.is_dir())

        # 状态置位（T11.2）：无 error → SUBMITTED；有 error → REJECTED（200 业务性结果）
        now = _utcnow()
        state = (
            ContributionState.SUBMITTED if report.valid else ContributionState.REJECTED
        )
        rejection_reason = "" if report.valid else self._rejection_reason(report)
        submission = DevSubmission(
            submission_id=submission_id,
            developer_id=developer,
            node_name=node,
            version=ver,
            package_digest=digest,
            package_size=package_size,
            file_count=file_count,
            state=state,
            validation_reports=[report],
            rejection_reason=rejection_reason,
            state_history=[],
            created_at=now,
            updated_at=now,
        )
        try:
            await self._repository.save(submission)
        except Exception as storage_exc:
            shutil.rmtree(package_dir, ignore_errors=True)
            raise DomainError(
                code=MARKET_STORAGE_UNAVAILABLE,
                message="市场存储不可用，请稍后重试",
                details={"reason": str(storage_exc)},
            ) from storage_exc

        return DevSubmissionResponse(
            submission_id=submission_id,
            state=state,
            accepted=report.valid,
            validation_report=report,
            rejection_reason=rejection_reason,
            package_digest=digest,
            package_size=package_size,
            file_count=file_count,
        )

    async def list_submissions(
        self,
        state: Optional[ContributionState] = None,
        node_name: Optional[str] = None,
    ) -> List[DevSubmission]:
        """列出提交（DV-4，可按状态/节点名筛选）；仓储读取异常降级空清单（INV-D-21）。"""
        try:
            if state is None:
                submissions = await self._repository.list_all()
            else:
                submissions = await self._repository.list_by_state(state)
        except Exception:
            return []
        if node_name:
            submissions = [
                s for s in submissions if s.node_name == node_name
            ]
        return submissions

    async def get_submission(self, submission_id: str) -> DevSubmission:
        """读取单条提交（DV-5）；未知 → 404；仓储读取异常 → 500。"""
        try:
            submission = await self._repository.get(submission_id)
        except Exception as storage_exc:
            raise DomainError(
                code=MARKET_STORAGE_UNAVAILABLE,
                message="市场存储不可用，请稍后重试",
                details={"reason": str(storage_exc)},
            ) from storage_exc
        if submission is None:
            raise ResourceNotFoundError(
                MARKET_DEV_SUBMISSION_NOT_FOUND,
                f"用户开发提交不存在: {submission_id}",
            )
        return submission

    async def transition(
        self,
        submission_id: str,
        to_state: ContributionState,
        operator_id: str,
        reason: str = "",
    ) -> DevTransitionResponse:
        """审核流转（DV-6，T16.1 / design §2.1.3.2）。

        编排：operator_id 非空校验（422）→ 读取提交（404）→ 合法性预检
        （ContributionStateMachine 纯函数只读调用；终态/非法 → 409，
        状态不变不部分写入，INV-D-20）→ APPROVED→PUBLISHED 先上架
        （失败 → 500 MARKET_DEV_PUBLISH_FAILED，主体保持 APPROVED
        可重试，T17.2）→ 状态机迁移追加 StateTransitionRecord → 落盘
        （失败 → 500 MARKET_STORAGE_UNAVAILABLE）。
        """
        operator = (operator_id or "").strip()
        if not operator:
            raise WorkflowValidationError(
                code=MARKET_DEV_SUBMISSION_INVALID,
                message="审核写操作须携带非空 operator_id（AC-DEV-20）",
                details={"operator_id": operator_id},
            )
        submission = await self.get_submission(submission_id)
        from_state = submission.state
        if ContributionStateMachine.is_terminal(from_state):
            raise InstanceStateConflictError(
                code=MARKET_DEV_INVALID_TRANSITION,
                message=(
                    f"提交 {submission_id} 处于终态 "
                    f"{from_state.value}，不可流转"
                ),
                details={
                    "submission_id": submission_id,
                    "from_state": from_state.value,
                    "to_state": to_state.value,
                },
            )
        if not ContributionStateMachine.can_transition(from_state, to_state):
            raise InstanceStateConflictError(
                code=MARKET_DEV_INVALID_TRANSITION,
                message=f"非法状态流转：{from_state.value} → {to_state.value}",
                details={
                    "submission_id": submission_id,
                    "from_state": from_state.value,
                    "to_state": to_state.value,
                },
            )
        message = ""
        if to_state == ContributionState.PUBLISHED:
            message = await self._publish_to_agent_market(submission)
        DevSubmissionStateMachine.transition(submission, to_state, operator, reason)
        try:
            await self._repository.save(submission)
        except Exception as storage_exc:
            raise DomainError(
                code=MARKET_STORAGE_UNAVAILABLE,
                message="市场存储不可用，请稍后重试",
                details={"reason": str(storage_exc)},
            ) from storage_exc
        return DevTransitionResponse(
            submission_id=submission_id,
            from_state=from_state,
            to_state=to_state,
            success=True,
            message=message,
        )

    async def rescan(
        self, submission_id: str, operator_id: str
    ) -> DevValidationReport:
        """重新执行三层校验并追加报告（DV-7，T16.2 / AC-DEV-16）。

        历史报告不可覆盖（仅追加）；状态与流转历史不变；
        解压包目录缺失 → 500 MARKET_STORAGE_UNAVAILABLE。
        """
        operator = (operator_id or "").strip()
        if not operator:
            raise WorkflowValidationError(
                code=MARKET_DEV_SUBMISSION_INVALID,
                message="审核写操作须携带非空 operator_id（AC-DEV-20）",
                details={"operator_id": operator_id},
            )
        submission = await self.get_submission(submission_id)
        package_dir = self._package_root / submission.submission_id
        if not package_dir.is_dir():
            raise DomainError(
                code=MARKET_STORAGE_UNAVAILABLE,
                message="解压包目录缺失，无法重新校验",
                details={"submission_id": submission.submission_id},
            )
        report = self._validation.validate(package_dir)
        submission.validation_reports.append(report)
        submission.updated_at = datetime.now(timezone.utc)
        try:
            await self._repository.save(submission)
        except Exception as storage_exc:
            raise DomainError(
                code=MARKET_STORAGE_UNAVAILABLE,
                message="市场存储不可用，请稍后重试",
                details={"reason": str(storage_exc)},
            ) from storage_exc
        return report

    async def get_history(self, submission_id: str) -> DevHistoryResponse:
        """流转历史（DV-8，T16.3；按 occurred_at 时间稳定有序）。"""
        submission = await self.get_submission(submission_id)
        history = sorted(
            submission.state_history,
            key=lambda record: record.occurred_at,
        )
        return DevHistoryResponse(
            submission_id=submission.submission_id,
            history=history,
        )

    @staticmethod
    def _rejection_reason(report: DevValidationReport) -> str:
        """构造拒绝原因（T11.2）：校验器异常优先，否则 error 项计数 + 首项定位。"""
        if report.error:
            return f"校验器异常：{report.error}"
        error_findings = [f for f in report.findings if f.severity == "error"]
        if not error_findings:
            return "校验不通过"
        first = error_findings[0]
        return (
            f"校验不通过：{len(error_findings)} 项 error"
            f"（首项 {first.layer}/{first.rule_id}：{first.message}）"
        )

    async def _publish_to_agent_market(self, submission: DevSubmission) -> str:
        """上架衔接（T17 / DC-04 路径 1）：调既有 register_entry（只调用、零改动）。

        - entry_id 前缀 dev. 承载「用户开发」来源标注（spec AC-DEV-14）；
          register_entry 现版硬编码 source_kind="registered" 且属零改动
          登记，payload 携带的 source_kind 键被其忽略不致错（design §1.2.7
          路径 1）；覆盖语义（created/replaced）由既有服务层承担；
        - 上架失败 → 500 MARKET_DEV_PUBLISH_FAILED，主体保持 APPROVED
          可重试（T17.2）；market_service 未注入时经仓储工厂延迟构造。
        """
        if self._market is None:
            self._market = AgentMarketService(
                RepositoryFactory.create_agent_market_repository()
            )
        payload = {
            "entry_id": f"dev.{submission.node_name}",
            "name": submission.node_name,
            "description": self._publish_description(submission),
            "category": "general",
            "tags": [],
            "status": MarketEntryStatus.AVAILABLE.value,
            "source_kind": "user_dev",
        }
        try:
            result = await self._market.register_entry(payload)
        except Exception as publish_exc:
            raise DomainError(
                code=MARKET_DEV_PUBLISH_FAILED,
                message="上架失败，提交保持 APPROVED 可重试",
                details={"reason": str(publish_exc)},
            ) from publish_exc
        return f"上架成功（{result.result}）"

    def _publish_description(self, submission: DevSubmission) -> str:
        """上架描述（AC-DEV-14 含版本）：manifest 描述 + 版本标注。

        manifest 不可读时降级为「用户开发节点 {node_name}」，不阻断上架。
        """
        manifest_description = ""
        manifest_path = (
            self._package_root / submission.submission_id / "manifest.yaml"
        )
        if manifest_path.is_file():
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    manifest_data = yaml.safe_load(f) or {}
                if isinstance(manifest_data, dict):
                    raw = manifest_data.get("description", "")
                    if isinstance(raw, str):
                        manifest_description = raw.strip()
            except Exception:
                manifest_description = ""
        base = manifest_description or f"用户开发节点 {submission.node_name}"
        return f"{base}（版本 {submission.version}）"
