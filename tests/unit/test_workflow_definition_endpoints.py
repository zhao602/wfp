# =============================================================================
# 工作流定义端点测试（tests/unit/test_workflow_definition_endpoints.py）
#
# 职责：B11 定义版本与发布态——API 端点行为验收（此前路由零测试覆盖，
#       本文件为首覆盖）：
#   POST /workflows — 真落库（版本号 = max+1，draft 态，响应富化 ref）
#   POST /workflows/{id}/publish — 发布（缺省最新；幂等；404）
#   GET  /workflows/{id} — 查询（version 可选；404 全局处理器）
#   GET  /workflows/{id}/versions — 版本历史（降序富化）
#   POST /workflows/{id}/rollback — 发布回滚 drill（内容等价 + 历史不可变
#                                   + draft_of 溯源 + 404）
#   POST /workflows/validate — 校验（首覆盖：合法 200 / 语法错 200-invalid）
# 设计意图（B11 design D3/D5/D7/D8）：
#   1. 同环 ASGI 请求（httpx.ASGITransport——test_cancel_endpoint_worker.py
#      先例）：404/422/409 同构错误体可断言；
#   2. 本地降级 profile（RBAC/租户隔离关闭）+ 独立 LOCAL_STORAGE_ROOT
#      临时目录（落库真实磁盘文件，不依赖全局 .data）；
#   3. 载荷走真实夹具 YAML + 真实解析器（诚实路径——解析/校验/落库全链）；
#   4. API 面结构性不触发 409（每次保存新版本号）——发布后新保存回归 +
#      发布态版本内容不可变断言（仓储级 409 守卫 drill 由
#      test_workflow_publish_state.py 仓储用例覆盖，不重复）。
# =============================================================================

from pathlib import Path

import httpx
import pytest

from orchestration_platform.api.application_factory import create_app
from orchestration_platform.configuration.settings import get_settings

BASE = "/api/v1/workflows"

_FIXTURE_YAML = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "valid_workflow_definitions"
    / "valid_sequential_four_nodes.yaml"
).read_text(encoding="utf-8")


# =============================================================================
# 夹具与助手
# =============================================================================


@pytest.fixture
def app(tmp_path, monkeypatch):
    """本地降级 profile 应用 + 独立存储根目录（落库隔离于全局 .data）。"""
    monkeypatch.setenv("RBAC_ENABLED", "false")
    monkeypatch.setenv("TENANT_ISOLATION_ENABLED", "false")
    monkeypatch.setenv("STORAGE_BACKEND", "local_file")
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", str(tmp_path / "data"))
    get_settings.cache_clear()
    yield create_app()
    get_settings.cache_clear()


async def _request(app, method: str, path: str, json_body=None) -> httpx.Response:
    """同环 ASGI 请求（非流式端点）。"""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, json=json_body)


def _yaml_with(definition_id: str, name: str) -> str:
    """由夹具 YAML 派生指定标识/名称的定义文本（真实解析路径）。"""
    return _FIXTURE_YAML.replace("sequential_four_nodes", definition_id).replace(
        "四节点顺序流水线", name
    )


async def _save(app, definition_id: str, name: str) -> httpx.Response:
    """保存一个定义版本（POST /workflows）。"""
    return await _request(
        app, "POST", BASE, {"yaml_text": _yaml_with(definition_id, name)}
    )


# =============================================================================
# POST /workflows/validate（首覆盖）
# =============================================================================


class TestValidateWorkflow:
    async def test_valid_yaml_returns_valid_report(self, app):
        resp = await _request(
            app, "POST", f"{BASE}/validate", {"yaml_text": _FIXTURE_YAML}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is True
        assert body["definition_id"] == "sequential_four_nodes"

    async def test_syntax_error_returns_invalid_report_not_500(self, app):
        resp = await _request(
            app, "POST", f"{BASE}/validate", {"yaml_text": ":: not [valid yaml"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is False
        assert body["definition_id"] is None


# =============================================================================
# POST /workflows —— 真落库 + 版本递增 + draft 态
# =============================================================================


class TestSaveWorkflow:
    async def test_save_persists_draft_v1_with_enriched_ref(self, app):
        resp = await _save(app, "wf-b11-ep", "端点保存测试")
        assert resp.status_code == 200
        body = resp.json()
        assert body["definition_id"] == "wf-b11-ep"
        assert body["definition_version"] == 1
        assert body["status"] == "draft"
        assert body["published_at"] is None
        assert body["created_at"]
        # 真落库：GET 读回等价定义（非仅回显）
        loaded = await _request(app, "GET", f"{BASE}/wf-b11-ep")
        assert loaded.status_code == 200
        assert loaded.json()["name"] == "端点保存测试"
        assert loaded.json()["status"] == "draft"

    async def test_save_increments_version_on_each_save(self, app):
        first = await _save(app, "wf-b11-inc", "递增测试一")
        second = await _save(app, "wf-b11-inc", "递增测试二")
        assert first.json()["definition_version"] == 1
        assert second.json()["definition_version"] == 2
        assert second.json()["status"] == "draft"

    async def test_save_after_publish_allocates_new_draft_not_rewrite(self, app):
        """发布后新保存：结构性分配新版本号（不触发 409 改写守卫）。"""
        await _save(app, "wf-b11-chain", "链式测试一")
        publish = await _request(app, "POST", f"{BASE}/wf-b11-chain/publish", {})
        assert publish.status_code == 200
        assert publish.json()["status"] == "published"
        second = await _save(app, "wf-b11-chain", "链式测试二")
        assert second.status_code == 200
        assert second.json()["definition_version"] == 2
        assert second.json()["status"] == "draft"
        # 发布态版本一内容不可变（改写走新号，历史保留）
        v1 = await _request(app, "GET", f"{BASE}/wf-b11-chain?version=1")
        assert v1.json()["name"] == "链式测试一"
        assert v1.json()["status"] == "published"

    async def test_save_yaml_syntax_error_returns_422(self, app):
        resp = await _request(
            app, "POST", BASE, {"yaml_text": ":: not [valid yaml"}
        )
        assert resp.status_code == 422

    async def test_save_definition_without_trigger_returns_422(self, app):
        """可解析但校验不通过（无触发器节点）→ WF_VALIDATION_FAILED 422。"""
        yaml_text = (
            "spec_version: \"1.0\"\n"
            "definition_id: no_trigger_wf\n"
            "name: 无触发器定义\n"
            "definition_version: 1\n"
            "nodes:\n"
            "  - node_id: tool_only\n"
            "    node_type: tool\n"
            "    configuration:\n"
            "      tool_identifier: report_builder\n"
            "      tool_source: local\n"
        )
        resp = await _request(app, "POST", BASE, {"yaml_text": yaml_text})
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "WF_VALIDATION_FAILED"


# =============================================================================
# POST /workflows/{id}/publish
# =============================================================================


class TestPublishWorkflow:
    async def test_publish_defaults_to_latest_version(self, app):
        await _save(app, "wf-b11-pub", "发布测试")
        resp = await _request(app, "POST", f"{BASE}/wf-b11-pub/publish", {})
        assert resp.status_code == 200
        body = resp.json()
        assert body["definition_version"] == 1
        assert body["status"] == "published"
        assert body["published_at"]

    async def test_publish_explicit_version_keeps_other_draft(self, app):
        await _save(app, "wf-b11-exp", "显式发布一")
        await _save(app, "wf-b11-exp", "显式发布二")
        resp = await _request(
            app, "POST", f"{BASE}/wf-b11-exp/publish", {"version": 1}
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "published"
        v2 = await _request(app, "GET", f"{BASE}/wf-b11-exp?version=2")
        assert v2.json()["status"] == "draft"

    async def test_publish_idempotent_keeps_published_at(self, app):
        await _save(app, "wf-b11-idem", "幂等发布")
        first = await _request(app, "POST", f"{BASE}/wf-b11-idem/publish", {})
        second = await _request(app, "POST", f"{BASE}/wf-b11-idem/publish", {})
        assert first.status_code == 200
        assert second.status_code == 200
        assert second.json()["status"] == "published"
        assert second.json()["published_at"] == first.json()["published_at"]

    async def test_publish_unknown_definition_returns_404(self, app):
        resp = await _request(app, "POST", f"{BASE}/wf-b11-none/publish", {})
        assert resp.status_code == 404
        assert resp.json()["code"] == "WF_NOT_FOUND"


# =============================================================================
# GET /workflows/{id}
# =============================================================================


class TestGetWorkflow:
    async def test_get_returns_latest_by_default_and_specific_version(self, app):
        await _save(app, "wf-b11-get", "查询测试一")
        await _save(app, "wf-b11-get", "查询测试二")
        latest = await _request(app, "GET", f"{BASE}/wf-b11-get")
        assert latest.json()["definition_version"] == 2
        v1 = await _request(app, "GET", f"{BASE}/wf-b11-get?version=1")
        assert v1.json()["definition_version"] == 1
        assert v1.json()["name"] == "查询测试一"

    async def test_get_unknown_returns_404_global_handler(self, app):
        resp = await _request(app, "GET", f"{BASE}/wf-b11-missing")
        assert resp.status_code == 404
        assert resp.json()["code"] == "WF_NOT_FOUND"


# =============================================================================
# GET /workflows/{id}/versions
# =============================================================================


class TestListVersions:
    async def test_versions_descending_with_publish_state_enriched(self, app):
        await _save(app, "wf-b11-vers", "版本列表一")
        await _save(app, "wf-b11-vers", "版本列表二")
        await _request(app, "POST", f"{BASE}/wf-b11-vers/publish", {"version": 1})
        resp = await _request(app, "GET", f"{BASE}/wf-b11-vers/versions")
        assert resp.status_code == 200
        versions = resp.json()
        assert [v["definition_version"] for v in versions] == [2, 1]
        assert versions[0]["status"] == "draft"
        assert versions[1]["status"] == "published"
        assert versions[1]["published_at"]

    async def test_versions_unknown_definition_returns_empty_list(self, app):
        resp = await _request(app, "GET", f"{BASE}/wf-b11-none/versions")
        assert resp.status_code == 200
        assert resp.json() == []


# =============================================================================
# POST /workflows/{id}/rollback —— 发布回滚 drill
# =============================================================================


class TestRollbackWorkflow:
    async def test_rollback_republishes_old_content_as_new_version(self, app):
        """回滚 drill：旧内容发新号（内容等价）+ 历史线性不可变 + 溯源。"""
        await _save(app, "wf-b11-rb", "回滚前内容")
        await _request(app, "POST", f"{BASE}/wf-b11-rb/publish", {})
        await _save(app, "wf-b11-rb", "回滚后内容")
        await _request(
            app, "POST", f"{BASE}/wf-b11-rb/publish", {"version": 2}
        )

        resp = await _request(
            app, "POST", f"{BASE}/wf-b11-rb/rollback", {"to_version": 1}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["definition_version"] == 3  # 新号 = max+1
        assert body["status"] == "published"
        assert body["published_at"]

        # 内容等价：新版本内容 == to_version 内容
        v3 = await _request(app, "GET", f"{BASE}/wf-b11-rb?version=3")
        v1 = await _request(app, "GET", f"{BASE}/wf-b11-rb?version=1")
        assert v3.json()["name"] == "回滚前内容"
        assert v3.json()["nodes"] == v1.json()["nodes"]
        assert v3.json()["draft_of"] == 1  # 溯源

        # 历史线性不可变：版本一/二保持原内容与发布态
        assert v1.json()["name"] == "回滚前内容"
        assert v1.json()["status"] == "published"
        v2 = await _request(app, "GET", f"{BASE}/wf-b11-rb?version=2")
        assert v2.json()["name"] == "回滚后内容"
        assert v2.json()["status"] == "published"

        # 版本历史完整（三条，降序）
        versions = await _request(app, "GET", f"{BASE}/wf-b11-rb/versions")
        assert [v["definition_version"] for v in versions.json()] == [3, 2, 1]

    async def test_rollback_unknown_version_returns_404(self, app):
        await _save(app, "wf-b11-rb404", "回滚目标缺失")
        resp = await _request(
            app, "POST", f"{BASE}/wf-b11-rb404/rollback", {"to_version": 99}
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == "WF_NOT_FOUND"

    async def test_rollback_unknown_definition_returns_404(self, app):
        resp = await _request(
            app, "POST", f"{BASE}/wf-b11-none/rollback", {"to_version": 1}
        )
        assert resp.status_code == 404
