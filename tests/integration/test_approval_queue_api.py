# =============================================================================
# 审批队列 API 集成测试（tests/integration/test_approval_queue_api.py）
#
# 覆盖：B16 F4 审批运营面装配级验证（design D1-D4）——
#   GET  /api/v1/approvals             队列聚合（进程内行字段/租户过滤/清空）
#   POST /api/v1/approvals/batch-review 批量复核（approve 续跑/reject 终态/
#                                      未知 404 skipped/已决 409 skipped/
#                                      重复幂等去重/422 契约）
# 设计意图：
#   1. 生命周期：lifespan_context 手动驱动；请求经 ASGITransport 同环调用；
#   2. 全链路经 HTTP：POST /instances（审核门 YAML）→ 轮询 waiting_review →
#      队列断言 → 批量复核 → 轮询终态 → 队列清空断言；
#   3. agent 节点热替换计数桩（after_review 不依赖 LLM，
#      同 tests/unit/test_review_service.py 路由级端到端先例）；
#   4. 降级模式（RBAC 关）：行租户 None——tenant_id 查询参数过滤即
#      「租户缺失排除」路径（D3）。
# =============================================================================

import asyncio

import httpx
import pytest

from orchestration_platform.api.application_factory import create_app
from orchestration_platform.configuration.settings import get_settings

_REVIEW_GATE_YAML = """spec_version: "1.0"
definition_id: review_gate_flow
name: 人工审核门流水线
definition_version: 1

inputs:
  - name: topic
    type: string
    required: true
    description: 分析主题

outputs:
  - name: analysis
    type: object
    description: 分析结果

nodes:
  - node_id: trigger_manual
    name: 手动触发
    node_type: trigger
    configuration:
      trigger_type: manual

  - node_id: review_gate
    name: 人工审核门
    node_type: control_flow
    configuration:
      control_flow_kind: human_review_gate
      review_instructions: 请审阅执行计划后放行
      review_timeout_action: reject

  - node_id: after_review
    name: 审阅后节点
    node_type: agent
    output_name: analysis
    configuration:
      model_identifier: primary-model
      system_prompt: 你是一名数据分析师。

edges:
  - edge_id: e1
    source_node_id: trigger_manual
    target_node_id: review_gate
  - edge_id: e2
    source_node_id: review_gate
    target_node_id: after_review
"""


class _CountingStubExecutor:
    """计数桩执行器（agent 节点不依赖 LLM）。"""

    def __init__(self, node_type):
        self._node_type = node_type
        self.node_calls: dict = {}

    @property
    def node_type(self):
        return self._node_type

    async def execute(self, context):
        from orchestration_platform.engine.nodes.base import NodeExecutionResult

        node_id = context.node_definition.node_id
        self.node_calls[node_id] = self.node_calls.get(node_id, 0) + 1
        return NodeExecutionResult(
            success=True,
            output_name=getattr(
                context.node_definition, "effective_output_name", None
            ),
            output_value={"stub": node_id},
        )


# =============================================================================
# 夹具：隔离存储根 + agent 桩 + 应用实例
# =============================================================================


@pytest.fixture
def storage_root(tmp_path, monkeypatch):
    """LOCAL_STORAGE_ROOT 重定向（隔离 .data/）+ 治理降级（RBAC 关）。"""
    root = tmp_path / "storage"
    root.mkdir()
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", str(root))
    monkeypatch.setenv("RBAC_ENABLED", "false")
    monkeypatch.setenv("TENANT_ISOLATION_ENABLED", "false")
    get_settings.cache_clear()
    yield root
    get_settings.cache_clear()


@pytest.fixture
def stubbed_agent_executor():
    """热替换路由执行器注册表 agent 执行器为计数桩。"""
    import orchestration_platform.api.routers.execution_instance_router as router_mod
    from orchestration_platform.domain.models.enums import NodeType

    registry = router_mod._executor_registry
    originals = dict(registry._executors)
    agent_stub = _CountingStubExecutor(NodeType.AGENT)
    registry.register(agent_stub)
    try:
        yield agent_stub
    finally:
        registry._executors.clear()
        registry._executors.update(originals)


@pytest.fixture
def app(storage_root):
    yield create_app()


@pytest.fixture(autouse=True)
def _registry_isolation():
    """测试间注册表隔离：摘除前序模块残留挂起实例 + 本测试新增逐测试回滚。

    模块级单例 _instance_registry 跨模块/跨测试存活——同会话前序套件
    （如 test_review_service）未决的 waiting_review 实例会污染本模块
    精确队列断言（空队列/单行/清空）；setup 摘除残留（teardown 恢复，
    会话内其他模块状态零破坏），teardown 清除本测试新增实例。
    """
    import orchestration_platform.api.routers.execution_instance_router as router_mod

    registry = router_mod._instance_registry
    before = dict(registry._instances)
    stale = [rt for rt in before.values() if rt.status == "waiting_review"]
    for rt in stale:
        registry.remove(rt.instance_id)
    yield
    for instance_id in set(registry._instances) - set(before):
        registry.remove(instance_id)
    for rt in stale:
        registry.register(rt)


# =============================================================================
# HTTP 辅助
# =============================================================================


async def _request(app, method: str, path: str, json_body=None) -> httpx.Response:
    """同环 ASGI 请求（异常处理器在应用栈内正常生效）。"""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        return await client.request(method, path, json=json_body)


async def _wait_status(
    app, instance_id: str, expected: str, timeout: float = 10.0
) -> dict:
    """轮询 GET /instances/{id} 直至目标状态。"""
    deadline = asyncio.get_event_loop().time() + timeout
    last: dict = {}
    while asyncio.get_event_loop().time() < deadline:
        resp = await _request(app, "GET", f"/api/v1/instances/{instance_id}")
        if resp.status_code == 200:
            last = resp.json()
            if last.get("status") == expected:
                return last
        await asyncio.sleep(0.05)
    raise AssertionError(f"实例 {instance_id} 未在 {timeout}s 内达到 {expected}: {last}")


async def _start_and_suspend(app, yaml_text=None) -> str:
    """HTTP 启动审核门流水线并等待挂起，返回 instance_id。"""
    resp = await _request(
        app,
        "POST",
        "/api/v1/instances",
        json_body={
            "definition_id": "review_gate_flow",
            "yaml_text": yaml_text or _REVIEW_GATE_YAML,
            "inputs": {"topic": "审批流"},
        },
    )
    assert resp.status_code == 200, resp.text
    instance_id = resp.json()["instance_id"]
    await _wait_status(app, instance_id, "waiting_review")
    return instance_id


# =============================================================================
# A. 队列查询
# =============================================================================


class TestApprovalQueueQuery:
    async def test_empty_queue(self, app):
        async with app.router.lifespan_context(app):
            resp = await _request(app, "GET", "/api/v1/approvals")

        assert resp.status_code == 200
        assert resp.json() == {"approvals": []}

    async def test_suspended_instance_row_fields(
        self, app, stubbed_agent_executor
    ):
        async with app.router.lifespan_context(app):
            instance_id = await _start_and_suspend(app)

            resp = await _request(app, "GET", "/api/v1/approvals")

        assert resp.status_code == 200
        rows = resp.json()["approvals"]
        assert len(rows) == 1
        row = rows[0]
        assert row["instance_id"] == instance_id
        assert row["definition_id"] == "review_gate_flow"
        assert row["status"] == "waiting_review"
        assert row["checkpoint_id"], "挂起检查点标识随行"
        assert row["suspended_at"], "挂起时刻随行"
        assert row["review_timeout_seconds"] > 0
        assert row["deadline"], "deadline = suspended_at + review_timeout_seconds"
        assert row["timeout_action"] == "reject"
        assert row["review_instructions"] == "请审阅执行计划后放行"

    async def test_tenant_query_param_filters_unowned_rows(
        self, app, stubbed_agent_executor
    ):
        """降级模式（RBAC 关）：行租户 None，显式 tenant 过滤即排除。"""
        async with app.router.lifespan_context(app):
            await _start_and_suspend(app)

            scoped = await _request(
                app, "GET", "/api/v1/approvals?tenant_id=t-other"
            )
            unscoped = await _request(app, "GET", "/api/v1/approvals")

        assert scoped.status_code == 200
        assert scoped.json()["approvals"] == [], "租户缺失行不入过滤结果"
        assert len(unscoped.json()["approvals"]) == 1


# =============================================================================
# B. 批量复核
# =============================================================================


class TestBatchReview:
    async def test_approve_full_flow_to_success(self, app, stubbed_agent_executor):
        """AC 端到端：挂起 → 队列可见 → 批量 approve（重复幂等去重）→
        succeeded → 队列清空。"""
        async with app.router.lifespan_context(app):
            instance_id = await _start_and_suspend(app)

            resp = await _request(
                app,
                "POST",
                "/api/v1/approvals/batch-review",
                json_body={
                    "instance_ids": [instance_id, instance_id],
                    "decision": "approve",
                    "actor": "reviewer-1",
                    "comment": "同意放行",
                },
            )

            assert resp.status_code == 200
            body = resp.json()
            assert body["summary"] == {
                "total": 2,
                "ok": 1,
                "skipped": 1,
                "error": 0,
            }
            assert body["results"][0]["instance_id"] == instance_id
            assert body["results"][0]["status"] == "ok"
            assert body["results"][0]["result"]["status"] == "running"
            assert body["results"][1]["status"] == "skipped"
            assert body["results"][1]["error"] == "duplicate"

            await _wait_status(app, instance_id, "succeeded")
            assert stubbed_agent_executor.node_calls.get("after_review") == 1

            queue = await _request(app, "GET", "/api/v1/approvals")
            assert queue.json()["approvals"] == [], "决策完成后队列清空"

    async def test_reject_maps_to_cancelled_and_queue_drains(
        self, app, stubbed_agent_executor
    ):
        async with app.router.lifespan_context(app):
            instance_id = await _start_and_suspend(app)

            resp = await _request(
                app,
                "POST",
                "/api/v1/approvals/batch-review",
                json_body={
                    "instance_ids": [instance_id],
                    "decision": "reject",
                    "actor": "reviewer-1",
                    "comment": "驳回",
                },
            )

            assert resp.status_code == 200
            assert resp.json()["summary"]["ok"] == 1
            await _wait_status(app, instance_id, "cancelled")

            queue = await _request(app, "GET", "/api/v1/approvals")
            assert queue.json()["approvals"] == []

    async def test_unknown_instance_skipped_with_404(self, app):
        async with app.router.lifespan_context(app):
            resp = await _request(
                app,
                "POST",
                "/api/v1/approvals/batch-review",
                json_body={
                    "instance_ids": ["no-such-instance"],
                    "decision": "approve",
                    "actor": "reviewer-1",
                },
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["summary"] == {"total": 1, "ok": 0, "skipped": 1, "error": 0}
        assert body["results"][0]["status"] == "skipped"
        assert body["results"][0]["http_status"] == 404

    async def test_resolved_instance_conflict_skipped(
        self, app, stubbed_agent_executor
    ):
        """已决实例二次复核 → 409 归 skipped（不放大为批失败）。"""
        async with app.router.lifespan_context(app):
            instance_id = await _start_and_suspend(app)
            first = await _request(
                app,
                "POST",
                "/api/v1/approvals/batch-review",
                json_body={
                    "instance_ids": [instance_id],
                    "decision": "approve",
                    "actor": "reviewer-1",
                },
            )
            assert first.json()["summary"]["ok"] == 1
            await _wait_status(app, instance_id, "succeeded")

            second = await _request(
                app,
                "POST",
                "/api/v1/approvals/batch-review",
                json_body={
                    "instance_ids": [instance_id],
                    "decision": "approve",
                    "actor": "reviewer-2",
                },
            )

        assert second.status_code == 200
        body = second.json()
        assert body["summary"] == {"total": 1, "ok": 0, "skipped": 1, "error": 0}
        assert body["results"][0]["status"] == "skipped"
        assert body["results"][0]["http_status"] == 409

    async def test_validation_422(self, app):
        """契约校验：批量超限（>100）/空列表/决策值域外/缺 actor → 422。"""
        async with app.router.lifespan_context(app):
            oversized = await _request(
                app,
                "POST",
                "/api/v1/approvals/batch-review",
                json_body={
                    "instance_ids": [f"inst-{n}" for n in range(101)],
                    "decision": "approve",
                    "actor": "a",
                },
            )
            empty = await _request(
                app,
                "POST",
                "/api/v1/approvals/batch-review",
                json_body={"instance_ids": [], "decision": "approve", "actor": "a"},
            )
            bad_decision = await _request(
                app,
                "POST",
                "/api/v1/approvals/batch-review",
                json_body={
                    "instance_ids": ["x"],
                    "decision": "bogus",
                    "actor": "a",
                },
            )
            missing_actor = await _request(
                app,
                "POST",
                "/api/v1/approvals/batch-review",
                json_body={"instance_ids": ["x"], "decision": "approve"},
            )

        assert oversized.status_code == 422
        assert empty.status_code == 422
        assert bad_decision.status_code == 422
        assert missing_actor.status_code == 422
