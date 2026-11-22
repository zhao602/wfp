# =============================================================================
# Agent 市场与 Tool 市场 API 集成测试（tests/integration/test_agent_tool_market_api.py）
#
# 执行命令：PYTHONPATH=src python -m pytest tests/integration/test_agent_tool_market_api.py -q
#
# 覆盖（agent_tool_marketplace T17.8 / T17.9、design §2.2.2 / §2.2.3）：
#   1. 开关关闭 → 市场端点 404（INV-D05/INV-12/DC-09；开关默认 False）；
#   2. 开关开启 → E-1~E-10 端点 2xx 与契约（响应键集合、错误码）；
#   3. AC-7 一致性：/tools 与 /tool-market/tools 标识集合与 total 相等
#      （回归层 test_ac07_tool_identifier_sets_identical + 降级层单源异常等号仍成立）；
#   4. 只读端点写方法 → 405（AC-6 规则 2/3）；
#   5. 响应不含凭据字段（递归键扫描 api_key/base_url/endpoint/timeout，INV-D08）；
#   6. 仓储不可读 → 降级 2xx（DC-12）；仓储不可写 → 500 MARKET_STORAGE_UNAVAILABLE；
#   7. T17.9：/api/v1/tools 与 /api/v1/mcp/server/tools 响应键集合与 HEAD 一致（INV-D01）。
# =============================================================================

import pytest
from fastapi.testclient import TestClient

from orchestration_platform.api.application_factory import create_app
from orchestration_platform.configuration.settings import Settings, get_settings
from orchestration_platform.mcp_tool_market.persistence.local_file_tool_market_repository import (
    LocalFileToolMarketRepository,
)
from orchestration_platform.persistence.local_file.local_file_agent_market_repository import (
    LocalFileAgentMarketRepository,
)

# 内置模板派生条目 entry_id（market/agent_market_service.py _BUILTIN_DERIVED_META）
BUILTIN_ENTRY_IDS = {
    "builtin.plan_and_execute",
    "builtin.basic_react",
    "builtin.multi_agent",
    "builtin.reflexive_agent",
    "builtin.autonomous_loop",
}

# 内置模板预置顺序（market/agent_templates.py AGENT_TEMPLATES）
TEMPLATE_IDS_IN_ORDER = [
    "plan_and_execute",
    "basic_react",
    "multi_agent",
    "reflexive_agent",
    "autonomous_loop",
]

# 冻结的既有端点契约（HEAD 基线，T17.9 / INV-D01 / C-1）
TOOLS_TOP_LEVEL_KEYS = {"tools", "total"}
TOOLS_ITEM_KEYS = {
    "tool_identifier",
    "name",
    "description",
    "source",
    "permission",
    "version",
}
MCP_SERVER_TOOLS_ITEM_KEYS = {
    "tool_identifier",
    "name",
    "description",
    "version",
    "permission",
    "input_schema",
    "output_schema",
    "transport",
    "endpoint",
    "tags",
}

# 市场响应凭据类字段黑名单（递归键扫描，INV-D08）
FORBIDDEN_CREDENTIAL_KEYS = {"api_key", "base_url", "endpoint"}


def _assert_no_credential_fields(node: object) -> None:
    """递归断言市场响应不含凭据类字段键（INV-D08）。

    tool_timeout_seconds 为模板性能边界字段（design §2.2.2 E-6 样例明示），
    非连接类超时凭据，予以豁免。
    """
    if isinstance(node, dict):
        for key, value in node.items():
            assert key not in FORBIDDEN_CREDENTIAL_KEYS, f"响应含凭据字段：{key}"
            if "timeout" in key and key != "tool_timeout_seconds":
                raise AssertionError(f"响应含超时类字段：{key}")
            _assert_no_credential_fields(value)
    elif isinstance(node, list):
        for item in node:
            _assert_no_credential_fields(item)


# =============================================================================
# 夹具：开关关闭 / 开关开启 两套应用实例（每次测试前后清 get_settings 缓存）
# =============================================================================


@pytest.fixture
def app_market_off(monkeypatch):
    """开关关闭的应用实例（两开关显式 false，市场路由不注册）。"""
    monkeypatch.setenv("RBAC_ENABLED", "false")
    monkeypatch.setenv("TENANT_ISOLATION_ENABLED", "false")
    monkeypatch.setenv("AGENT_MARKET_ENABLED", "false")
    monkeypatch.setenv("TOOL_MARKET_ENABLED", "false")
    get_settings.cache_clear()
    yield create_app()
    get_settings.cache_clear()


@pytest.fixture
def client_market_off(app_market_off):
    """开关关闭的 TestClient（市场端点 404，既有端点不受影响）。"""
    with TestClient(app_market_off) as c:
        yield c


@pytest.fixture
def app_market_on(monkeypatch, tmp_path):
    """开关开启的应用实例（存储根指向临时目录，测试不污染工作区）。"""
    monkeypatch.setenv("RBAC_ENABLED", "false")
    monkeypatch.setenv("TENANT_ISOLATION_ENABLED", "false")
    monkeypatch.setenv("AGENT_MARKET_ENABLED", "true")
    monkeypatch.setenv("TOOL_MARKET_ENABLED", "true")
    monkeypatch.setenv("STORAGE_BACKEND", "local_file")
    monkeypatch.setenv("AGENT_MARKET_STORAGE_ROOT", str(tmp_path / "agent_market"))
    monkeypatch.setenv("TOOL_MARKET_STORAGE_ROOT", str(tmp_path / "tool_market"))
    # MCP 工具注册仓储重定向至临时目录（E-10 注册落盘隔离，不改生产代码路径）
    from orchestration_platform.api.routers import (
        tool_market_router as _tm_router_module,
    )

    _mcp_market_dir = tmp_path / "mcp_tool_market"

    class _TmpLocalFileToolMarketRepository(LocalFileToolMarketRepository):
        def __init__(self) -> None:
            super().__init__(storage_dir=str(_mcp_market_dir))

    monkeypatch.setattr(
        _tm_router_module,
        "LocalFileToolMarketRepository",
        _TmpLocalFileToolMarketRepository,
    )
    get_settings.cache_clear()
    yield create_app()
    get_settings.cache_clear()


@pytest.fixture
def client_market_on(app_market_on):
    """开关开启的 TestClient（市场端点 2xx）。"""
    with TestClient(app_market_on) as c:
        yield c


# =============================================================================
# 开关：关闭 404 / 默认 False（INV-D05/INV-12/DC-09）
# =============================================================================


class TestMarketSwitchOff:
    """开关关闭 → 市场端点 404；既有端点不受影响。"""

    def test_agent_market_endpoints_404_when_disabled(self, client_market_off):
        cases = [
            ("get", "/api/v1/agent-market/entries"),
            ("get", "/api/v1/agent-market/entries/team.some_entry"),
            ("get", "/api/v1/agent-market/categories"),
            ("post", "/api/v1/agent-market/entries"),
            ("get", "/api/v1/agent-market/templates"),
            ("get", "/api/v1/agent-market/templates/plan_and_execute"),
        ]
        for method, path in cases:
            resp = getattr(client_market_off, method)(path)
            assert resp.status_code == 404, f"{method.upper()} {path} → {resp.status_code}"

    def test_tool_market_endpoints_404_when_disabled(self, client_market_off):
        cases = [
            ("get", "/api/v1/tool-market/tools"),
            ("get", "/api/v1/tool-market/tools/get_workflow"),
            ("get", "/api/v1/tool-market/categories"),
            ("post", "/api/v1/tool-market/tools"),
        ]
        for method, path in cases:
            resp = getattr(client_market_off, method)(path)
            assert resp.status_code == 404, f"{method.upper()} {path} → {resp.status_code}"

    def test_existing_tools_endpoint_still_available_when_market_disabled(
        self, client_market_off
    ):
        resp = client_market_off.get("/api/v1/tools")
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1


class TestMarketSwitchDefaults:
    """市场开关默认 False（T11.1 验收）。"""

    def test_market_switches_default_off(self, monkeypatch):
        # 密封化：_env_file=None 隔离根 .env 的本地覆盖（AGENT_MARKET_ENABLED/
        # TOOL_MARKET_ENABLED=true），仅验证代码默认值（T11.1/INV-D05）。
        for var in ("AGENT_MARKET_ENABLED", "TOOL_MARKET_ENABLED"):
            monkeypatch.delenv(var, raising=False)
        settings = Settings(_env_file=None)
        assert settings.agent_market_enabled is False
        assert settings.tool_market_enabled is False


# =============================================================================
# Agent 市场：E-1 ~ E-4
# =============================================================================


class TestAgentMarketEntries:
    """E-1 清单 / E-2 详情 / E-3 分类 / E-4 注册。"""

    def test_e1_lists_builtin_entries(self, client_market_on):
        resp = client_market_on.get("/api/v1/agent-market/entries")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) == {"entries", "total"}
        assert body["total"] == 5
        assert {e["entry_id"] for e in body["entries"]} == BUILTIN_ENTRY_IDS
        expected_item_keys = {
            "entry_id",
            "name",
            "description",
            "category",
            "tags",
            "status",
            "source_kind",
            "preset_ref",
            "tool_refs",
            "created_at",
            "updated_at",
        }
        assert all(set(e.keys()) == expected_item_keys for e in body["entries"])

    def test_e1_category_filter(self, client_market_on):
        resp = client_market_on.get("/api/v1/agent-market/entries?category=planning")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert all(e["category"] == "planning" for e in body["entries"])

    def test_e1_tag_filter(self, client_market_on):
        resp = client_market_on.get("/api/v1/agent-market/entries?tag=template")
        assert resp.status_code == 200
        # 差异化定位后仅 multi_agent / autonomous_loop 保留通用 "template" 标签
        assert resp.json()["total"] == 2

    def test_e1_status_filter(self, client_market_on):
        resp = client_market_on.get("/api/v1/agent-market/entries?status=builtin")
        assert resp.status_code == 200
        assert resp.json()["total"] == 5

    def test_e1_invalid_status_rejected(self, client_market_on):
        resp = client_market_on.get("/api/v1/agent-market/entries?status=bogus")
        assert resp.status_code == 422

    def test_e2_builtin_entry_detail(self, client_market_on):
        resp = client_market_on.get(
            "/api/v1/agent-market/entries/builtin.plan_and_execute"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["preset_ref"] == "plan_and_execute"
        assert body["preset"] is not None
        assert body["template_ref"] == (
            "/api/v1/agent-market/templates/plan_and_execute"
        )

    def test_e2_unknown_entry_404(self, client_market_on):
        resp = client_market_on.get("/api/v1/agent-market/entries/team.missing")
        assert resp.status_code == 404
        assert resp.json()["code"] == "MARKET_ENTRY_NOT_FOUND"

    def test_e3_categories(self, client_market_on):
        resp = client_market_on.get("/api/v1/agent-market/categories")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) == {"categories", "total"}
        assert body["total"] == 5
        counts = {c["category"]: c["count"] for c in body["categories"]}
        assert counts == {
            "planning": 1,
            "starter": 1,
            "collaboration": 1,
            "quality": 1,
            "autonomy": 1,
        }

    def test_e4_register_entry_created_then_replaced(self, client_market_on):
        payload = {
            "entry_id": "team.retrieval_agent",
            "name": "检索增强 Agent",
            "description": "结合知识库检索的问答 Agent",
            "category": "collaboration",
            "tags": ["collaboration", "retrieval"],
            "tool_refs": ["get_instance"],
        }
        resp = client_market_on.post("/api/v1/agent-market/entries", json=payload)
        assert resp.status_code == 200
        first = resp.json()
        assert first["result"] == "created"
        assert first["source_kind"] == "registered"
        assert first["status"] == "draft"
        listed = client_market_on.get("/api/v1/agent-market/entries").json()
        assert listed["total"] == 6
        second = client_market_on.post(
            "/api/v1/agent-market/entries", json=payload
        ).json()
        assert second["result"] == "replaced"
        assert "覆盖" in second["message"]

    def test_e4_missing_name_rejected(self, client_market_on):
        resp = client_market_on.post(
            "/api/v1/agent-market/entries", json={"entry_id": "team.x"}
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "MARKET_ENTRY_INVALID"

    def test_e4_builtin_entry_immutable(self, client_market_on):
        resp = client_market_on.post(
            "/api/v1/agent-market/entries",
            json={"entry_id": "builtin.plan_and_execute", "name": "篡改"},
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "MARKET_ENTRY_BUILTIN_IMMUTABLE"


# =============================================================================
# Agent 市场：E-5 / E-6（模板只读，total 恒 5，AC-12）
# =============================================================================


class TestAgentMarketTemplates:
    """E-5 模板清单 / E-6 模板详情。"""

    def test_e5_lists_five_templates(self, client_market_on):
        resp = client_market_on.get("/api/v1/agent-market/templates")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) == {"templates", "total"}
        assert body["total"] == 5
        assert [t["template_id"] for t in body["templates"]] == TEMPLATE_IDS_IN_ORDER
        expected_item_keys = {
            "template_id",
            "name",
            "kind",
            "summary",
            "allowed_tools",
            "max_tool_rounds",
        }
        assert all(set(t.keys()) == expected_item_keys for t in body["templates"])
        assert all(t["summary"] for t in body["templates"])

    def test_e6_single_node_template_detail_recipe_null(self, client_market_on):
        # 差异化改造后仅 basic_react 保持 single_node（其余四个模板 recipe 化/升级）
        resp = client_market_on.get(
            "/api/v1/agent-market/templates/basic_react"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["kind"] == "single_node"
        assert body["recipe"] is None
        assert body["preset"]["system_prompt"]
        assert body["doc"]["core_principle_flow"]
        assert "tool_config" in body

    def test_e6_recipe_template_detail_has_nodes_and_edges(self, client_market_on):
        resp = client_market_on.get("/api/v1/agent-market/templates/plan_and_execute")
        assert resp.status_code == 200
        body = resp.json()
        assert body["kind"] == "recipe"
        assert body["recipe"] is not None
        assert len(body["recipe"]["nodes"]) >= 2
        assert len(body["recipe"]["edges"]) >= 1

    def test_e6_unknown_template_404(self, client_market_on):
        resp = client_market_on.get(
            "/api/v1/agent-market/templates/unknown_template"
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == "MARKET_TEMPLATE_NOT_FOUND"


# =============================================================================
# Tool 市场：E-7 ~ E-10
# =============================================================================


class TestToolMarketTools:
    """E-7 清单 / E-8 详情 / E-9 分类 / E-10 注册。"""

    def test_e7_lists_tools_with_contract_keys(self, client_market_on):
        resp = client_market_on.get("/api/v1/tool-market/tools")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) == {"tools", "total"}
        assert body["total"] >= 1
        expected_item_keys = {
            "tool_identifier",
            "name",
            "description",
            "source",
            "permission",
            "version",
            "tags",
            "input_schema_summary",
            "output_schema_summary",
        }
        assert all(set(t.keys()) == expected_item_keys for t in body["tools"])

    def test_e7_q_filter_returns_subset(self, client_market_on):
        all_tools = client_market_on.get("/api/v1/tool-market/tools").json()
        first = all_tools["tools"][0]
        keyword = first["name"][:2]
        resp = client_market_on.get(f"/api/v1/tool-market/tools?q={keyword}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 1
        assert all(keyword in t["name"] for t in body["tools"])
        assert {t["tool_identifier"] for t in body["tools"]} <= {
            t["tool_identifier"] for t in all_tools["tools"]
        }

    def test_e7_permission_filter(self, client_market_on):
        resp = client_market_on.get(
            "/api/v1/tool-market/tools?permission=readonly"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert all(t["permission"] == "readonly" for t in body["tools"])

    def test_e8_known_tool_detail(self, client_market_on):
        tools = client_market_on.get("/api/v1/tool-market/tools").json()["tools"]
        target = tools[0]["tool_identifier"]
        resp = client_market_on.get(f"/api/v1/tool-market/tools/{target}")
        assert resp.status_code == 200
        assert resp.json()["tool_identifier"] == target

    def test_e8_unknown_tool_404(self, client_market_on):
        resp = client_market_on.get("/api/v1/tool-market/tools/nonexistent.tool")
        assert resp.status_code == 404
        assert resp.json()["code"] == "MARKET_TOOL_NOT_FOUND"

    def test_e9_categories_shape(self, client_market_on):
        resp = client_market_on.get("/api/v1/tool-market/categories")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) == {"categories", "total"}
        assert body["total"] == len(body["categories"])
        assert all(set(c.keys()) == {"category", "count"} for c in body["categories"])

    def test_e10_register_tool_success_then_conflict(self, client_market_on):
        payload = {
            "tool_identifier": "team.custom_tool",
            "name": "团队自定义工具",
            "version": "1.0.0",
            "tags": ["team"],
        }
        resp = client_market_on.post("/api/v1/tool-market/tools", json=payload)
        assert resp.status_code == 200
        first = resp.json()
        assert set(first.keys()) == {
            "success",
            "tool_identifier",
            "version",
            "conflict_reason",
        }
        assert first["success"] is True
        assert first["conflict_reason"] is None

        conflict_payload = dict(payload)
        conflict_payload["description"] = "被篡改的描述"
        second = client_market_on.post(
            "/api/v1/tool-market/tools", json=conflict_payload
        ).json()
        assert second["success"] is False
        assert second["conflict_reason"] is not None

    def test_e10_missing_identifier_400(self, client_market_on):
        resp = client_market_on.post(
            "/api/v1/tool-market/tools", json={"name": "无标识工具"}
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "MCP_TOOL_REGISTRATION_CONFLICT"


# =============================================================================
# AC-7 一致性（回归层 + 降级层，design §2.2.3）
# =============================================================================


class TestAc07Consistency:
    """/tools 与 /tool-market/tools 标识集合一致（AC-7）。"""

    def test_ac07_tool_identifier_sets_identical(self, client_market_on):
        catalog = client_market_on.get("/api/v1/tools")
        market = client_market_on.get("/api/v1/tool-market/tools")
        assert catalog.status_code == 200
        assert market.status_code == 200
        catalog_body = catalog.json()
        market_body = market.json()
        assert {t["tool_identifier"] for t in market_body["tools"]} == {
            t["tool_identifier"] for t in catalog_body["tools"]
        }
        assert market_body["total"] == catalog_body["total"]

    def test_ac07_holds_when_platform_source_fails(
        self, client_market_on, monkeypatch
    ):
        """降级层：单源（platform）异常时两端点同时缺失同一源，等号仍成立。"""

        def _failing_provider():
            raise RuntimeError("platform source unavailable")

        import orchestration_platform.market.tool_market_service as _tms_module
        import orchestration_platform.tool_discovery.tool_catalog_service as _tcs_module

        monkeypatch.setattr(
            _tms_module, "get_platform_tool_declarations", _failing_provider
        )
        monkeypatch.setattr(
            _tcs_module, "get_platform_tool_declarations", _failing_provider
        )

        catalog = client_market_on.get("/api/v1/tools").json()
        market = client_market_on.get("/api/v1/tool-market/tools").json()
        assert {t["tool_identifier"] for t in market["tools"]} == {
            t["tool_identifier"] for t in catalog["tools"]
        }
        assert market["total"] == catalog["total"]


# =============================================================================
# 只读端点写方法 → 405（AC-6 规则 2/3）
# =============================================================================


class TestReadOnlyMethodNotAllowed:
    """只读端点仅注册 GET（E-4/E-10 为唯一写入口），其余写方法 → 405。"""

    def test_agent_market_write_methods_405(self, client_market_on):
        cases = [
            ("delete", "/api/v1/agent-market/entries/builtin.plan_and_execute"),
            ("put", "/api/v1/agent-market/entries/builtin.plan_and_execute"),
            ("put", "/api/v1/agent-market/entries"),
            ("delete", "/api/v1/agent-market/templates/plan_and_execute"),
            ("put", "/api/v1/agent-market/templates"),
            ("delete", "/api/v1/agent-market/categories"),
        ]
        for method, path in cases:
            resp = getattr(client_market_on, method)(path)
            assert resp.status_code == 405, f"{method.upper()} {path} → {resp.status_code}"

    def test_tool_market_write_methods_405(self, client_market_on):
        cases = [
            ("delete", "/api/v1/tool-market/tools/get_workflow"),
            ("put", "/api/v1/tool-market/tools/get_workflow"),
            ("put", "/api/v1/tool-market/tools"),
            ("delete", "/api/v1/tool-market/categories"),
        ]
        for method, path in cases:
            resp = getattr(client_market_on, method)(path)
            assert resp.status_code == 405, f"{method.upper()} {path} → {resp.status_code}"


# =============================================================================
# 响应凭据字段扫描（INV-D08/AC-25）
# =============================================================================


class TestCredentialFieldScan:
    """市场响应不含凭据字段（递归键扫描 api_key/base_url/endpoint/timeout）。"""

    def test_agent_market_responses_free_of_credential_fields(self, client_market_on):
        responses = [
            client_market_on.get("/api/v1/agent-market/entries").json(),
            client_market_on.get(
                "/api/v1/agent-market/entries/builtin.plan_and_execute"
            ).json(),
            client_market_on.get("/api/v1/agent-market/categories").json(),
            client_market_on.get("/api/v1/agent-market/templates").json(),
            client_market_on.get(
                "/api/v1/agent-market/templates/plan_and_execute"
            ).json(),
            client_market_on.get("/api/v1/agent-market/templates/multi_agent").json(),
            client_market_on.post(
                "/api/v1/agent-market/entries",
                json={"entry_id": "team.scan_probe", "name": "扫描探针"},
            ).json(),
        ]
        for body in responses:
            _assert_no_credential_fields(body)

    def test_tool_market_responses_free_of_credential_fields(self, client_market_on):
        tools = client_market_on.get("/api/v1/tool-market/tools").json()
        target = tools["tools"][0]["tool_identifier"]
        responses = [
            tools,
            client_market_on.get(f"/api/v1/tool-market/tools/{target}").json(),
            client_market_on.get("/api/v1/tool-market/categories").json(),
            client_market_on.post(
                "/api/v1/tool-market/tools",
                json={"tool_identifier": "team.scan_probe", "name": "扫描探针"},
            ).json(),
        ]
        for body in responses:
            _assert_no_credential_fields(body)


# =============================================================================
# 仓储读取降级（DC-12）：不可读 → 2xx；不可写 → 500
# =============================================================================


class TestRepositoryDegradation:
    """Agent 市场仓储异常降级（design §2.2.4 降级矩阵）。"""

    def test_agent_market_repository_unreadable_degrades(
        self, client_market_on, monkeypatch
    ):
        async def _failing_list_all(self):
            raise OSError("storage unreadable")

        monkeypatch.setattr(LocalFileAgentMarketRepository, "list_all", _failing_list_all)
        listed = client_market_on.get("/api/v1/agent-market/entries")
        assert listed.status_code == 200
        body = listed.json()
        assert body["total"] == 5
        assert {e["entry_id"] for e in body["entries"]} == BUILTIN_ENTRY_IDS
        assert (
            client_market_on.get("/api/v1/agent-market/categories").status_code == 200
        )
        detail = client_market_on.get(
            "/api/v1/agent-market/entries/builtin.plan_and_execute"
        )
        assert detail.status_code == 200

    def test_agent_market_repository_unwritable_register_500(
        self, client_market_on, monkeypatch
    ):
        async def _failing_exists(self, entry_id):
            raise OSError("storage unwritable")

        monkeypatch.setattr(LocalFileAgentMarketRepository, "exists", _failing_exists)
        resp = client_market_on.post(
            "/api/v1/agent-market/entries",
            json={"entry_id": "team.unwritable", "name": "不可写探针"},
        )
        assert resp.status_code == 500
        assert resp.json()["code"] == "MARKET_STORAGE_UNAVAILABLE"


# =============================================================================
# T17.9：既有工具端点响应键集合与 HEAD 一致（INV-D01/AC-8）
# =============================================================================


class TestExistingToolsEndpointUnchanged:
    """/api/v1/tools 与 /api/v1/mcp/server/tools 契约冻结（市场开关不影响既有端点）。"""

    def test_tools_endpoint_keys_frozen(self, client_market_off):
        resp = client_market_off.get("/api/v1/tools")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) == TOOLS_TOP_LEVEL_KEYS
        assert body["total"] == len(body["tools"])
        assert all(set(t.keys()) == TOOLS_ITEM_KEYS for t in body["tools"])

    def test_mcp_server_tools_endpoint_items_consistent(self, client_market_off):
        tools = client_market_off.get("/api/v1/tools").json()
        resp = client_market_off.get("/api/v1/mcp/server/tools")
        assert resp.status_code == 200
        declarations = resp.json()
        assert isinstance(declarations, list)
        assert all(set(d.keys()) == MCP_SERVER_TOOLS_ITEM_KEYS for d in declarations)
        platform_ids = {
            t["tool_identifier"] for t in tools["tools"] if t["source"] == "platform"
        }
        assert platform_ids == {d["tool_identifier"] for d in declarations}
