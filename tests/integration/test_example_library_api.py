# =============================================================================
# 范例库 API 集成测试（tests/integration/test_example_library_api.py）
#
# 沙箱内无法安装/运行 pytest，本文件留待非沙箱环境执行。
# 执行命令：pytest tests/integration/test_example_library_api.py -v
#
# 覆盖端点：
#   GET /api/v1/examples          —— 列出全部预置范例
#   GET /api/v1/examples/{id}     —— 加载指定范例的工作流定义
#
# 用例覆盖（对应 tasks.md 6.1 DoD / spec EX-*、AUTH-*）：
#   1. 列表顶层结构、条目 6 字段、12 个范例及注册表顺序
#   2. 详情响应体即工作流定义对象（无 definition 外壳）
#   3. 不存在标识 → 404 EXAMPLE_NOT_FOUND
#   4. 依赖缺失 → 422 EXAMPLE_DEPENDENCY_MISSING（details 含 example_id 与缺失变量清单）
#   5. YAML 解析失败 → 422
#   6. 路径穿越标识 → 请求被拒绝（404）
#   7. 治理开关关闭时无 Authorization 头 → 200（免鉴权放行）
#   8. 治理开关开启时无令牌 → 401 AUTH_TOKEN_MISSING
# =============================================================================

import pytest
from fastapi.testclient import TestClient

from orchestration_platform.api.application_factory import create_app
from orchestration_platform.api.routers import example_library_router
from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.errors import WorkflowValidationError

# 范例标识（按 example_library_service._EXAMPLE_REGISTRY 声明顺序：
# 先 5 个既有条目，后按追加约定登记的 7 个新条目）
EXPECTED_EXAMPLE_IDS = [
    "sequential_pipeline",
    "supervisor_routing",
    "conditional_loop_pipeline",
    "feature_engineering",
    "drift_detection",
    "parallel_execution",
    "hierarchical_delegation",
    "reflection_iteration",
    "data_engineering",
    "data_transform_pipeline",
    "subworkflow_nesting",
    "realtime_subscription",
]

# 范例条目响应字段（6 个，与前端契约逐字段一致）
EXAMPLE_ENTRY_FIELDS = {
    "example_id",
    "name",
    "yaml_path",
    "available",
    "missing_dependencies",
    "unavailable_reason",
}

# 工作流定义核心契约字段（详情响应体应直接包含这些字段、无 definition 外壳）
WORKFLOW_DEFINITION_CORE_FIELDS = {
    "definition_id",
    "name",
    "spec_version",
    "definition_version",
    "nodes",
    "edges",
    "inputs",
    "outputs",
}

# 范例所需的环境变量全集（用于"依赖齐全"夹具）
REQUIRED_ENV_VARS = [
    "LLM_PRIMARY_BASE_URL",
    "LLM_PRIMARY_API_KEY",
    "LLM_PRIMARY_MODEL",
    "DATA_SOURCE_CONNECTION_STRING",
]


@pytest.fixture
def app(monkeypatch):
    """构造应用实例（治理降级模式：两开关均 false，免鉴权放行）。

    每次测试前后清除 get_settings 的 lru_cache，确保环境变量变更生效。
    """
    monkeypatch.setenv("RBAC_ENABLED", "false")
    monkeypatch.setenv("TENANT_ISOLATION_ENABLED", "false")
    get_settings.cache_clear()
    yield create_app()
    get_settings.cache_clear()


@pytest.fixture
def client(app):
    """FastAPI TestClient，治理降级模式。"""
    with TestClient(app) as c:
        yield c


@pytest.fixture
def env_complete(monkeypatch):
    """设置范例所需全部环境变量，使范例可用。"""
    for key in REQUIRED_ENV_VARS:
        monkeypatch.setenv(key, "test-value")


@pytest.fixture
def env_empty(monkeypatch):
    """删除范例所需全部环境变量，触发依赖缺失。

    依赖判定除环境变量外还会回落到配置对象（.env 由 pydantic-settings 载入，
    不写入 os.environ）。故同时注入空配置，等价于"未配置任何模型/数据源"的
    干净部署环境，避免本机 .env 让缺失夹具失效。
    """
    for key in REQUIRED_ENV_VARS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(
        example_library_router._service, "_settings_source", lambda: None
    )


# =============================================================================
# 列表端点 GET /api/v1/examples
# =============================================================================


class TestListExamples:
    """列表端点结构与顺序验证。"""

    def test_top_level_contains_only_examples(self, client):
        """响应体顶层含且仅含 examples 字段（EX-02）。"""
        resp = client.get("/api/v1/examples")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) == {"examples"}

    def test_entry_has_six_contract_fields(self, client):
        """每个条目含 6 个契约字段（EX-03）。"""
        resp = client.get("/api/v1/examples")
        assert resp.status_code == 200
        for entry in resp.json()["examples"]:
            assert set(entry.keys()) == EXAMPLE_ENTRY_FIELDS

    def test_twelve_examples_in_registry_order(self, client):
        """返回 12 个范例，按注册表声明顺序（EX-06、EX-08）。"""
        resp = client.get("/api/v1/examples")
        assert resp.status_code == 200
        entries = resp.json()["examples"]
        assert len(entries) == 12
        assert [e["example_id"] for e in entries] == EXPECTED_EXAMPLE_IDS


# =============================================================================
# 详情端点 GET /api/v1/examples/{example_id}
# =============================================================================


class TestGetExample:
    """详情端点结构、错误映射与路径穿越验证。"""

    def test_detail_is_workflow_definition_without_wrapper(self, client, env_complete):
        """详情响应体即工作流定义对象，无 definition 外壳（EX-10、EX-18）。"""
        resp = client.get("/api/v1/examples/sequential_pipeline")
        assert resp.status_code == 200
        body = resp.json()
        assert "definition" not in body
        assert WORKFLOW_DEFINITION_CORE_FIELDS.issubset(set(body.keys()))

    def test_unknown_id_returns_404_with_code(self, client):
        """不存在标识 → 404 且错误体含 code（EX-14）。"""
        resp = client.get("/api/v1/examples/nonexistent_example_id")
        assert resp.status_code == 404
        body = resp.json()
        assert body["code"] == "EXAMPLE_NOT_FOUND"
        assert body["details"]["example_id"] == "nonexistent_example_id"

    def test_dependency_missing_returns_422_with_details(self, client, env_empty):
        """依赖缺失 → 422，details 含 example_id 与缺失变量清单（EX-15）。"""
        resp = client.get("/api/v1/examples/supervisor_routing")
        assert resp.status_code == 422
        body = resp.json()
        assert body["code"] == "EXAMPLE_DEPENDENCY_MISSING"
        assert body["details"]["example_id"] == "supervisor_routing"
        missing = body["details"]["missing_variables"]
        assert isinstance(missing, list)
        assert len(missing) > 0

    def test_yaml_parse_failure_returns_422(self, client, monkeypatch):
        """YAML 解析失败 → 422（EX-16）。

        通过 monkeypatch 替换服务方法抛 WorkflowValidationError，
        验证路由层错误映射将其转为 422 统一结构。
        """

        def _raise_validation_error(example_id: str):
            raise WorkflowValidationError(
                code="WF_PARSE_FAILED",
                message="模拟 YAML 解析失败",
                details={"example_id": example_id},
            )

        monkeypatch.setattr(
            example_library_router._service,
            "load_example",
            _raise_validation_error,
        )
        resp = client.get("/api/v1/examples/sequential_pipeline")
        assert resp.status_code == 422
        assert "code" in resp.json()

    def test_path_traversal_identifier_rejected(self, client):
        """路径穿越标识 → 请求被拒绝（EX-17）。

        FastAPI 路由层将 `%2F` 解码为 `/`，多段路径不匹配 `/examples/{example_id}`
        单段路由模式，由框架返回默认 404（{"detail": "Not Found"}）。
        若框架版本将编码斜杠保持为单段，则由服务白名单校验返回
        404（{code: "EXAMPLE_NOT_FOUND", ...}）。两种路径均安全拒绝，不暴露文件内容。
        """
        resp = client.get("/api/v1/examples/..%2F..%2F.env")
        assert resp.status_code == 404
        body = resp.json()
        # 框架默认 404 返回 {"detail": "Not Found"}，
        # 路由层 404 返回 {code, message, details}——两者均安全
        assert "detail" in body or "code" in body


# =============================================================================
# 鉴权行为
# =============================================================================


class TestAuthBehavior:
    """治理开关与免鉴权放行/拒绝验证。"""

    def test_accessible_without_auth_when_governance_disabled(self, monkeypatch):
        """治理开关关闭时无 Authorization 头 → 200（AUTH-02）。"""
        monkeypatch.setenv("RBAC_ENABLED", "false")
        monkeypatch.setenv("TENANT_ISOLATION_ENABLED", "false")
        get_settings.cache_clear()
        try:
            app = create_app()
            with TestClient(app) as c:
                resp = c.get("/api/v1/examples")
                assert resp.status_code == 200
        finally:
            get_settings.cache_clear()

    def test_rejected_without_token_when_governance_enabled(self, monkeypatch):
        """治理开关开启时无令牌 → 401 AUTH_TOKEN_MISSING（AUTH-04）。"""
        monkeypatch.setenv("RBAC_ENABLED", "true")
        monkeypatch.setenv("TENANT_ISOLATION_ENABLED", "true")
        get_settings.cache_clear()
        try:
            app = create_app()
            with TestClient(app) as c:
                resp = c.get("/api/v1/examples")
                assert resp.status_code == 401
                assert resp.json()["code"] == "AUTH_TOKEN_MISSING"
        finally:
            get_settings.cache_clear()