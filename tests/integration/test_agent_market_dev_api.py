# =============================================================================
# Agent 市场用户自主开发 API 集成测试（tests/integration/test_agent_market_dev_api.py）
#
# 执行命令：PYTHONPATH=src python -m pytest tests/integration/test_agent_market_dev_api.py -q
#
# 覆盖（agent_market_user_development T24.1 / CHG-15 / design §2.2.2 DV-1~DV-8 / DC-03/04/07）：
#   1. 开发开关关闭 → DV-1~DV-8 全部 404；既有市场端点不受影响（DC-07）；
#   2. DV-1/DV-2 脚手架清单与详情 2xx（五要素 + 文件模板内联 + fetch_hint）；
#   3. DV-3 合法包 200 + state=submitted + accepted=true；
#      DV-3 非法包 200 + state=rejected + accepted=false + 逐项发现（业务性结果）；
#      DV-3 超限 422 LIMIT_EXCEEDED；DV-3 重复 409 DUPLICATE；非 ZIP 422；
#   4. DV-4 清单筛选；DV-5 详情（source_kind=user_dev）与未知 404；
#   5. DV-6 四步全链路 submitted→…→published；批准上架后条目在
#      GET /agent-market/entries 可检索（entry_id 前缀 dev. + status=available，
#      AC-DEV-14/15）；非法流转 409 且状态不变异；to_state 非法 422；
#      operator_id 空 422；未知 404；终态 409；
#   6. DV-7 重新校验报告追加（历史报告不覆盖）；DV-8 历史按 occurred_at 有序；
#   7. 既有 POST /agent-market/entries 行为零变化（created/replaced/422/409，
#      守护⑤ / INV-D-19）；
#   8. python-multipart 可选依赖（CHG-DEP 范式，对齐 vector_kb_management_router）：
#      DV-3/DV-6/DV-7 为 Form/File 端点，缺失时条件注册降级 404，
#      契约测试 skipUnless、降级测试 skipif 反向互斥。
# 偏差登记：register_entry（agent_market_service.py，零改动登记）硬编码
#   source_kind="registered"（:212），上架条目来源标注由 entry_id 前缀 dev.
#   承载（AC-DEV-14）；本测试按真相断言并在 tasks.md 登记。
# =============================================================================

import io
import os
import zipfile

import pytest
import yaml
from fastapi.testclient import TestClient

from orchestration_platform.api.application_factory import create_app
from orchestration_platform.api.routers import (
    agent_market_dev_router as dev_router_module,
)
from orchestration_platform.configuration.settings import Settings, get_settings

# DV-3/DV-6/DV-7 HTTP 契约依赖 python-multipart（CHG-DEP 可选依赖条件注册）
requires_multipart = pytest.mark.skipif(
    not dev_router_module.MULTIPART_AVAILABLE,
    reason="python-multipart 未安装（CHG-DEP 缺失降级 404，服务层链路由单测覆盖）",
)

# 内置模板派生条目 entry_id 基线（E-1 清单 5 条）
BUILTIN_ENTRY_COUNT = 5

_VALID_NODE_PY = "def run(query: str) -> str:\n    return query\n"

_EVIL_NODE_PY = "def run(q):\n    return eval(q)\n"


def _base_manifest() -> dict:
    """构造一份可通过三层校验的 manifest 数据（与单元测试夹具同源）。"""
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


def _make_package_bytes(
    *,
    node_name: str = "demo-node",
    version: str = "1.0.0",
    node_py: str = _VALID_NODE_PY,
    extra_files: dict | None = None,
) -> bytes:
    """构造提交包 ZIP 字节流（manifest.yaml + node.py [+ 附加文件]）。"""
    manifest = _base_manifest()
    manifest["node_name"] = node_name
    manifest["version"] = version
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.yaml", yaml.safe_dump(manifest, allow_unicode=True))
        zf.writestr("node.py", node_py)
        for name, content in (extra_files or {}).items():
            zf.writestr(name, content)
    return buf.getvalue()


def _submit(
    client: TestClient,
    *,
    developer_id: str = "alice",
    node_name: str = "demo-node",
    version: str = "1.0.0",
    package_bytes: bytes | None = None,
    node_py: str = _VALID_NODE_PY,
) -> object:
    """发起一次 DV-3 multipart 提交。"""
    payload = package_bytes or _make_package_bytes(
        node_name=node_name, version=version, node_py=node_py
    )
    return client.post(
        "/api/v1/agent-market/dev-submissions",
        files={"package": ("package.zip", payload, "application/zip")},
        data={
            "developer_id": developer_id,
            "node_name": node_name,
            "version": version,
        },
    )


def _transition(
    client: TestClient,
    submission_id: str,
    to_state: str,
    *,
    operator_id: str = "reviewer-1",
    reason: str = "",
) -> object:
    """发起一次 DV-6 流转（urlencoded Form）。"""
    return client.post(
        f"/api/v1/agent-market/dev-submissions/{submission_id}/transition",
        data={"to_state": to_state, "operator_id": operator_id, "reason": reason},
    )


# =============================================================================
# 夹具：开关关闭 / 开启 / 超限专用 三套应用实例（每次测试前后清 get_settings 缓存）
# =============================================================================


@pytest.fixture
def app_dev_off(monkeypatch, tmp_path):
    """开发开关关闭（基础市场开启）：DV 端点不注册 → 404。"""
    monkeypatch.setenv("RBAC_ENABLED", "false")
    monkeypatch.setenv("TENANT_ISOLATION_ENABLED", "false")
    monkeypatch.setenv("AGENT_MARKET_ENABLED", "true")
    monkeypatch.setenv("TOOL_MARKET_ENABLED", "true")
    monkeypatch.setenv("STORAGE_BACKEND", "local_file")
    monkeypatch.setenv("AGENT_MARKET_STORAGE_ROOT", str(tmp_path / "agent_market"))
    monkeypatch.setenv("AGENT_MARKET_DEV_ENABLED", "false")
    monkeypatch.setenv("AGENT_MARKET_DEV_STORAGE_ROOT", str(tmp_path / "agent_market_dev"))
    get_settings.cache_clear()
    yield create_app()
    get_settings.cache_clear()


@pytest.fixture
def client_dev_off(app_dev_off):
    with TestClient(app_dev_off) as c:
        yield c


@pytest.fixture
def app_dev_on(monkeypatch, tmp_path):
    """开发开关开启：DV-1~DV-8 注册，存储根指向临时目录不污染工作区。"""
    monkeypatch.setenv("RBAC_ENABLED", "false")
    monkeypatch.setenv("TENANT_ISOLATION_ENABLED", "false")
    monkeypatch.setenv("AGENT_MARKET_ENABLED", "true")
    monkeypatch.setenv("TOOL_MARKET_ENABLED", "true")
    monkeypatch.setenv("STORAGE_BACKEND", "local_file")
    monkeypatch.setenv("AGENT_MARKET_STORAGE_ROOT", str(tmp_path / "agent_market"))
    monkeypatch.setenv("AGENT_MARKET_DEV_ENABLED", "true")
    monkeypatch.setenv("AGENT_MARKET_DEV_STORAGE_ROOT", str(tmp_path / "agent_market_dev"))
    get_settings.cache_clear()
    yield create_app()
    get_settings.cache_clear()


@pytest.fixture
def client_dev_on(app_dev_on):
    with TestClient(app_dev_on) as c:
        yield c


@pytest.fixture
def app_dev_small_limit(monkeypatch, tmp_path):
    """DV-3 超限专用：包体上限压至 1MB（其余与 app_dev_on 一致）。"""
    monkeypatch.setenv("RBAC_ENABLED", "false")
    monkeypatch.setenv("TENANT_ISOLATION_ENABLED", "false")
    monkeypatch.setenv("AGENT_MARKET_ENABLED", "true")
    monkeypatch.setenv("TOOL_MARKET_ENABLED", "true")
    monkeypatch.setenv("STORAGE_BACKEND", "local_file")
    monkeypatch.setenv("AGENT_MARKET_STORAGE_ROOT", str(tmp_path / "agent_market"))
    monkeypatch.setenv("AGENT_MARKET_DEV_ENABLED", "true")
    monkeypatch.setenv("AGENT_MARKET_DEV_STORAGE_ROOT", str(tmp_path / "agent_market_dev"))
    monkeypatch.setenv("AGENT_MARKET_DEV_MAX_PACKAGE_MB", "1")
    get_settings.cache_clear()
    yield create_app()
    get_settings.cache_clear()


@pytest.fixture
def client_dev_small_limit(app_dev_small_limit):
    with TestClient(app_dev_small_limit) as c:
        yield c


# =============================================================================
# 开关：关闭 404 / 默认 False（DC-07 / INV-D-09）
# =============================================================================


class TestDevSwitchOff:
    """开发开关关闭 → DV 端点 404；既有市场端点不受影响。"""

    def test_dev_endpoints_404_when_disabled(self, client_dev_off):
        cases = [
            ("get", "/api/v1/agent-market/dev-templates"),
            ("get", "/api/v1/agent-market/dev-templates/agent-node-basic"),
            ("post", "/api/v1/agent-market/dev-submissions"),
            ("get", "/api/v1/agent-market/dev-submissions"),
            ("get", "/api/v1/agent-market/dev-submissions/sub-1"),
            ("post", "/api/v1/agent-market/dev-submissions/sub-1/transition"),
            ("post", "/api/v1/agent-market/dev-submissions/sub-1/rescan"),
            ("get", "/api/v1/agent-market/dev-submissions/sub-1/history"),
        ]
        for method, path in cases:
            resp = getattr(client_dev_off, method)(path)
            assert resp.status_code == 404, f"{method.upper()} {path} → {resp.status_code}"

    def test_existing_market_endpoints_unaffected(self, client_dev_off):
        assert (
            client_dev_off.get("/api/v1/agent-market/entries").status_code == 200
        )
        assert (
            client_dev_off.get("/api/v1/agent-market/templates").status_code == 200
        )
        assert (
            client_dev_off.get("/api/v1/agent-market/categories").status_code == 200
        )

    def test_dev_switch_default_off(self, monkeypatch):
        # 密封化：_env_file=None 隔离根 .env 本地覆盖，仅验证代码默认值
        monkeypatch.delenv("AGENT_MARKET_DEV_ENABLED", raising=False)
        settings = Settings(_env_file=None)
        assert settings.agent_market_dev_enabled is False


# =============================================================================
# DV-1 / DV-2：开发脚手架（五要素 + 文件模板内联，AC-DEV-01~03）
# =============================================================================


class TestDevScaffolds:
    """DV-1 清单 / DV-2 详情。"""

    def test_dv1_lists_scaffolds(self, client_dev_on):
        resp = client_dev_on.get("/api/v1/agent-market/dev-templates")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) == {"scaffolds", "total"}
        assert body["total"] >= 1
        expected_item_keys = {
            "scaffold_id",
            "name",
            "version",
            "summary",
            "required_files",
        }
        assert all(set(s.keys()) == expected_item_keys for s in body["scaffolds"])
        assert any(s["scaffold_id"] == "agent-node-basic" for s in body["scaffolds"])

    def test_dv2_detail_five_elements_and_inline_templates(self, client_dev_on):
        resp = client_dev_on.get(
            "/api/v1/agent-market/dev-templates/agent-node-basic"
        )
        assert resp.status_code == 200
        body = resp.json()
        # 五要素全覆盖 + 获取指引（AC-DEV-01~03）
        for key in (
            "scaffold_id",
            "name",
            "version",
            "summary",
            "directory_structure",
            "required_files",
            "optional_files",
            "manifest_spec",
            "interface_contract",
            "dependency_and_runtime",
            "file_templates",
            "fetch_hint",
        ):
            assert body.get(key), f"五要素字段缺失或为空：{key}"
        # 文件模板 content 内联（无 ZIP 下载端点，DC-01）
        paths = {t["path"] for t in body["file_templates"]}
        assert {"manifest.yaml", "node.py"} <= paths
        assert all(t["content"] for t in body["file_templates"])

    def test_dv2_unknown_scaffold_404(self, client_dev_on):
        resp = client_dev_on.get(
            "/api/v1/agent-market/dev-templates/unknown_scaffold"
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == "MARKET_DEV_SCAFFOLD_NOT_FOUND"


# =============================================================================
# DV-3：提交受理（multipart，CHG-DEP 可选依赖）
# =============================================================================


@requires_multipart
class TestDevSubmit:
    """DV-3 提交受理契约（200/409/422 矩阵）。"""

    def test_dv3_valid_package_submitted(self, client_dev_on):
        resp = _submit(client_dev_on)
        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) == {
            "submission_id",
            "state",
            "accepted",
            "validation_report",
            "rejection_reason",
            "package_digest",
            "package_size",
            "file_count",
        }
        assert body["state"] == "submitted"
        assert body["accepted"] is True
        assert body["rejection_reason"] == ""
        assert body["validation_report"]["valid"] is True
        assert body["validation_report"]["error"] is None
        assert len(body["package_digest"]) == 64
        assert body["file_count"] == 2
        assert body["package_size"] > 0

    def test_dv3_invalid_package_rejected_with_findings(self, client_dev_on):
        resp = _submit(client_dev_on, node_name="evil-node", node_py=_EVIL_NODE_PY)
        assert resp.status_code == 200  # 业务性结果，非 5xx（AC-DEV-08）
        body = resp.json()
        assert body["accepted"] is False
        assert body["state"] == "rejected"
        report = body["validation_report"]
        assert report["valid"] is False
        findings = report["findings"]
        assert any(
            f["layer"] == "L3" and f["severity"] == "error" for f in findings
        ), "应包含 L3 静态安全 error 发现"
        assert all(
            set(f.keys()) == {"layer", "rule_id", "severity", "message", "location"}
            for f in findings
        )
        assert body["rejection_reason"]

    def test_dv3_oversize_package_422(self, client_dev_small_limit):
        # 1.2MB 不可压缩载荷 → 超 1MB 上限（随机字节不被 DEFLATE 压缩）
        big = _make_package_bytes(
            node_name="oversize-node",
            extra_files={"payload.bin": os.urandom(1024 * 1200)},
        )
        resp = _submit(client_dev_small_limit, node_name="oversize-node", package_bytes=big)
        assert resp.status_code == 422
        assert resp.json()["code"] == "MARKET_DEV_LIMIT_EXCEEDED"

    def test_dv3_duplicate_submission_409(self, client_dev_on):
        first = _submit(client_dev_on)
        assert first.status_code == 200
        second = _submit(client_dev_on)
        assert second.status_code == 409
        assert second.json()["code"] == "MARKET_DEV_SUBMISSION_DUPLICATE"

    def test_dv3_non_zip_package_422(self, client_dev_on):
        resp = client_dev_on.post(
            "/api/v1/agent-market/dev-submissions",
            files={"package": ("package.zip", b"not a zip file", "application/zip")},
            data={
                "developer_id": "alice",
                "node_name": "demo-node",
                "version": "1.0.0",
            },
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "MARKET_DEV_SUBMISSION_INVALID"

    def test_dv3_missing_form_field_422(self, client_dev_on):
        resp = client_dev_on.post(
            "/api/v1/agent-market/dev-submissions",
            files={
                "package": (
                    "package.zip",
                    _make_package_bytes(),
                    "application/zip",
                )
            },
            data={"developer_id": "alice", "version": "1.0.0"},  # 缺 node_name
        )
        assert resp.status_code == 422


# =============================================================================
# DV-4 / DV-5：提交清单与详情
# =============================================================================


class TestDevSubmissionReadEndpoints:
    """DV-4/DV-5/DV-8 只读端点的错误契约（不依赖提交数据）。"""

    def test_dv4_invalid_state_filter_422(self, client_dev_on):
        resp = client_dev_on.get(
            "/api/v1/agent-market/dev-submissions?state=bogus"
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "MARKET_DEV_SUBMISSION_INVALID"

    def test_dv5_unknown_submission_404(self, client_dev_on):
        resp = client_dev_on.get(
            "/api/v1/agent-market/dev-submissions/nonexistent"
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == "MARKET_DEV_SUBMISSION_NOT_FOUND"

    def test_dv8_unknown_submission_404(self, client_dev_on):
        resp = client_dev_on.get(
            "/api/v1/agent-market/dev-submissions/nonexistent/history"
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == "MARKET_DEV_SUBMISSION_NOT_FOUND"


@requires_multipart
class TestDevSubmissionQueries:
    """DV-4 清单筛选 / DV-5 详情（source_kind=user_dev）。"""

    def test_dv4_list_and_filters(self, client_dev_on):
        assert _submit(client_dev_on, node_name="list-node").status_code == 200
        assert (
            _submit(client_dev_on, node_name="other-node", version="0.9.0").status_code
            == 200
        )
        resp = client_dev_on.get("/api/v1/agent-market/dev-submissions")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) == {"submissions", "total"}
        assert body["total"] == 2
        expected_item_keys = {
            "submission_id",
            "developer_id",
            "node_name",
            "version",
            "package_digest",
            "package_size",
            "file_count",
            "state",
            "validation_reports",
            "rejection_reason",
            "state_history",
            "created_at",
            "updated_at",
        }
        assert all(set(s.keys()) == expected_item_keys for s in body["submissions"])

        by_state = client_dev_on.get(
            "/api/v1/agent-market/dev-submissions?state=submitted"
        ).json()
        assert by_state["total"] == 2

        by_node = client_dev_on.get(
            "/api/v1/agent-market/dev-submissions?node_name=list-node"
        ).json()
        assert by_node["total"] == 1
        assert by_node["submissions"][0]["node_name"] == "list-node"

    def test_dv5_detail_with_source_kind(self, client_dev_on):
        submission_id = _submit(client_dev_on, node_name="detail-node").json()[
            "submission_id"
        ]
        resp = client_dev_on.get(
            f"/api/v1/agent-market/dev-submissions/{submission_id}"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) == {"submission", "latest_report", "source_kind"}
        assert body["source_kind"] == "user_dev"
        assert body["submission"]["submission_id"] == submission_id
        assert body["submission"]["state"] == "submitted"
        assert body["latest_report"]["valid"] is True


# =============================================================================
# DV-6：审核流转与上架衔接（DC-04 路径 1）
# =============================================================================


@requires_multipart
class TestDevTransitionFlow:
    """DV-6 流转契约（409/422/404 矩阵 + 全链路上架可检索）。"""

    def test_dv6_full_chain_to_published_and_market_entry(self, client_dev_on):
        submission_id = _submit(client_dev_on).json()["submission_id"]
        chain = ["security_scan", "reviewing", "approved", "published"]
        for to_state in chain:
            resp = _transition(client_dev_on, submission_id, to_state)
            assert resp.status_code == 200, f"{to_state}: {resp.text}"
            body = resp.json()
            assert set(body.keys()) == {
                "submission_id",
                "from_state",
                "to_state",
                "success",
                "message",
            }
            assert body["to_state"] == to_state
            assert body["success"] is True
        published = resp.json()
        assert "上架成功" in published["message"]

        # 上架后市场条目可检索（AC-DEV-14/15）：entry_id 前缀 dev. + status=available
        listed = client_dev_on.get("/api/v1/agent-market/entries").json()
        assert listed["total"] == BUILTIN_ENTRY_COUNT + 1
        dev_entries = [
            e for e in listed["entries"] if e["entry_id"] == "dev.demo-node"
        ]
        assert dev_entries, "上架后条目未在市场清单出现"
        entry = dev_entries[0]
        assert entry["status"] == "available"
        assert entry["name"] == "demo-node"
        assert "demo node" in entry["description"]
        assert "1.0.0" in entry["description"]
        # 偏差登记：register_entry 硬编码 source_kind="registered"（零改动登记），
        # 「用户开发」来源由 entry_id 前缀 dev. 承载（AC-DEV-14，tasks.md 登记）
        assert entry["source_kind"] == "registered"

        # 可检索（E-2 详情 + status 过滤）
        detail = client_dev_on.get("/api/v1/agent-market/entries/dev.demo-node")
        assert detail.status_code == 200
        assert detail.json()["status"] == "available"
        by_status = client_dev_on.get(
            "/api/v1/agent-market/entries?status=available"
        ).json()
        assert any(e["entry_id"] == "dev.demo-node" for e in by_status["entries"])

    def test_dv6_invalid_transition_409_state_unchanged(self, client_dev_on):
        submission_id = _submit(client_dev_on).json()["submission_id"]
        resp = _transition(client_dev_on, submission_id, "approved")  # 越级
        assert resp.status_code == 409
        assert resp.json()["code"] == "MARKET_DEV_INVALID_TRANSITION"
        detail = client_dev_on.get(
            f"/api/v1/agent-market/dev-submissions/{submission_id}"
        ).json()
        assert detail["submission"]["state"] == "submitted"  # 状态不变异（INV-D-20）

    def test_dv6_invalid_to_state_422(self, client_dev_on):
        submission_id = _submit(client_dev_on).json()["submission_id"]
        resp = _transition(client_dev_on, submission_id, "bogus")
        assert resp.status_code == 422
        assert resp.json()["code"] == "MARKET_DEV_SUBMISSION_INVALID"

    def test_dv6_empty_operator_422(self, client_dev_on):
        submission_id = _submit(client_dev_on).json()["submission_id"]
        # 空串表单值在本 FastAPI 版本按「缺失」处理（HTTP 层 422，AC-DEV-20）；
        # 服务层空 operator 分支（code=MARKET_DEV_SUBMISSION_INVALID）由
        # tests/unit/test_dev_submission_service.py B4 段覆盖
        resp = _transition(
            client_dev_on, submission_id, "security_scan", operator_id=""
        )
        assert resp.status_code == 422

    def test_dv6_unknown_submission_404(self, client_dev_on):
        resp = _transition(client_dev_on, "nonexistent", "security_scan")
        assert resp.status_code == 404
        assert resp.json()["code"] == "MARKET_DEV_SUBMISSION_NOT_FOUND"

    def test_dv6_terminal_state_409(self, client_dev_on):
        submission_id = _submit(client_dev_on).json()["submission_id"]
        for to_state in ("security_scan", "reviewing", "approved", "published"):
            assert _transition(client_dev_on, submission_id, to_state).status_code == 200
        resp = _transition(client_dev_on, submission_id, "rejected")
        assert resp.status_code == 409  # 终态不可流转
        assert resp.json()["code"] == "MARKET_DEV_INVALID_TRANSITION"


# =============================================================================
# DV-7：重新校验（报告追加不覆盖，AC-DEV-16）
# =============================================================================


@requires_multipart
class TestDevRescan:
    """DV-7 重新校验契约。"""

    def test_dv7_rescan_appends_report(self, client_dev_on):
        submission_id = _submit(client_dev_on).json()["submission_id"]
        resp = client_dev_on.post(
            f"/api/v1/agent-market/dev-submissions/{submission_id}/rescan",
            data={"operator_id": "scanner-1"},
        )
        assert resp.status_code == 200
        report = resp.json()
        assert set(report.keys()) == {
            "report_id",
            "valid",
            "layers",
            "findings",
            "scanner_version",
            "checked_at",
            "error",
        }
        assert report["valid"] is True
        detail = client_dev_on.get(
            f"/api/v1/agent-market/dev-submissions/{submission_id}"
        ).json()
        assert len(detail["submission"]["validation_reports"]) == 2  # 追加不覆盖
        assert detail["submission"]["state"] == "submitted"  # 状态不变

    def test_dv7_unknown_submission_404(self, client_dev_on):
        resp = client_dev_on.post(
            "/api/v1/agent-market/dev-submissions/nonexistent/rescan",
            data={"operator_id": "op-1"},
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == "MARKET_DEV_SUBMISSION_NOT_FOUND"


# =============================================================================
# DV-8：流转历史（按 occurred_at 有序）
# =============================================================================


@requires_multipart
class TestDevHistory:
    """DV-8 历史契约。"""

    def test_dv8_history_ordered_after_transitions(self, client_dev_on):
        submission_id = _submit(client_dev_on).json()["submission_id"]
        for to_state in ("security_scan", "reviewing", "approved"):
            assert _transition(client_dev_on, submission_id, to_state).status_code == 200
        resp = client_dev_on.get(
            f"/api/v1/agent-market/dev-submissions/{submission_id}/history"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) == {"submission_id", "history"}
        assert [h["to_state"] for h in body["history"]] == [
            "security_scan",
            "reviewing",
            "approved",
        ]
        assert all(
            set(h.keys())
            == {"from_state", "to_state", "operator_id", "reason", "occurred_at"}
            for h in body["history"]
        )
        timestamps = [h["occurred_at"] for h in body["history"]]
        assert timestamps == sorted(timestamps)  # 有序


# =============================================================================
# python-multipart 缺失降级（CHG-DEP；与契约测试互斥 skipif）
# =============================================================================


@pytest.mark.skipif(
    dev_router_module.MULTIPART_AVAILABLE,
    reason="python-multipart 已安装（CHG-DEP 条件注册生效，走完整契约测试）",
)
class TestMultipartDegradation:
    """python-multipart 缺失 → DV-3/DV-6/DV-7 端点不注册（404），只读端点不受影响。"""

    def test_write_endpoints_404_when_multipart_missing(self, client_dev_on):
        cases = [
            ("post", "/api/v1/agent-market/dev-submissions"),
            (
                "post",
                "/api/v1/agent-market/dev-submissions/sub-1/transition",
            ),
            ("post", "/api/v1/agent-market/dev-submissions/sub-1/rescan"),
        ]
        for method, path in cases:
            resp = getattr(client_dev_on, method)(path)
            assert resp.status_code == 404, f"{method.upper()} {path} → {resp.status_code}"

    def test_read_endpoints_available_when_multipart_missing(self, client_dev_on):
        assert (
            client_dev_on.get("/api/v1/agent-market/dev-templates").status_code == 200
        )
        assert (
            client_dev_on.get("/api/v1/agent-market/dev-submissions").status_code
            == 200
        )


# =============================================================================
# 守护⑤：既有 POST /agent-market/entries 行为零变化（INV-D-19）
# =============================================================================


class TestExistingEntriesEndpointUnchanged:
    """既有 E-4 注册语义在开发开关开启时保持不变（created/replaced/422/409）。"""

    def test_e4_created_then_replaced_semantics_preserved(self, client_dev_on):
        payload = {"entry_id": "team.guard_probe", "name": "守护探针"}
        first = client_dev_on.post("/api/v1/agent-market/entries", json=payload)
        assert first.status_code == 200
        assert first.json()["result"] == "created"
        assert first.json()["source_kind"] == "registered"
        assert first.json()["status"] == "draft"
        second = client_dev_on.post(
            "/api/v1/agent-market/entries", json=payload
        ).json()
        assert second["result"] == "replaced"
        assert "覆盖" in second["message"]

    def test_e4_invalid_and_builtin_immutable_preserved(self, client_dev_on):
        resp = client_dev_on.post(
            "/api/v1/agent-market/entries", json={"entry_id": "team.x"}
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "MARKET_ENTRY_INVALID"
        resp = client_dev_on.post(
            "/api/v1/agent-market/entries",
            json={"entry_id": "builtin.plan_and_execute", "name": "篡改"},
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "MARKET_ENTRY_BUILTIN_IMMUTABLE"
