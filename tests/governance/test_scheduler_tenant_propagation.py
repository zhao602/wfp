# =============================================================================
# 调度器租户穿参测试（tests/governance/test_scheduler_tenant_propagation.py）
#
# 职责：验证 B7 W2a——业务执行链租户穿参（execute(tenant_id=...) →
# _run_node_lifecycle → node.*/并行分支领域事件按租户发布）。
# 设计意图（.codeartsdoer/specs/roadmap_b7_harvest spec W2a / design §4）：
#   1. execute 穿参 → node.succeeded / node.failed 领域事件 tenant=实参；
#   2. 并行扇出：PARALLEL_BRANCH_* 与分支内 node.succeeded 同样取实参；
#   3. 不传 tenant_id → 事件回退 DEFAULT_TENANT_ID（W2b 后 = tenant_default）；
#   4. resume_checkpoint 路径穿参保持；
#   5. run_and_record 集成形状：rt_run.tenant_id 流入 scheduler.execute。
# =============================================================================

from datetime import datetime, timezone
from typing import List, Optional

import pytest

from orchestration_platform.api.routers import execution_instance_router as router_mod
from orchestration_platform.domain.errors import NodeExecutionError
from orchestration_platform.domain.models.domain_event import DEFAULT_TENANT_ID
from orchestration_platform.domain.models.edge_definition import EdgeDefinition
from orchestration_platform.domain.models.enums import NodeType
from orchestration_platform.domain.models.node_configurations import (
    LlmNodeConfiguration,
)
from orchestration_platform.domain.models.node_definition import NodeDefinition
from orchestration_platform.domain.models.shared_state import SharedState
from orchestration_platform.engine.abstractions.compiled_workflow import (
    CompiledWorkflow,
)
from orchestration_platform.engine.graph_compiler import GraphCompiler
from orchestration_platform.engine.node_executor_registry import NodeExecutorRegistry
from orchestration_platform.engine.nodes.base import NodeExecutionResult
from orchestration_platform.engine.scheduler import Scheduler
from orchestration_platform.governance.metering.usage_recorder import (
    set_usage_recorder,
)
from orchestration_platform.workflow.yaml_workflow_parser import YamlWorkflowParser


# =============================================================================
# 构造助手：捕获发布器 / 检查点仓储 / 桩执行器 / 工作流构造
# =============================================================================

class _CaptureEventPublisher:
    """捕获领域事件的内存发布器。"""

    def __init__(self):
        self.events: List[object] = []

    async def publish_domain_event(self, event) -> None:
        self.events.append(event)


class _CaptureCheckpointRepo:
    """捕获检查点的内存仓储。"""

    def __init__(self):
        self.checkpoints: List[object] = []

    async def append(self, checkpoint) -> str:
        self.checkpoints.append(checkpoint)
        return checkpoint.checkpoint_id

    async def latest_for_instance(self, instance_id: str) -> Optional[object]:
        return None


def _shadow_success(scheduler: Scheduler) -> None:
    """替换 _execute_node 为恒成功桩（起止时间随返回值传递，B6 语义）。"""

    async def _fake_execute_node(
        node, state_manager, instance_id, trace_id, round_index, compiled
    ):
        now = datetime.now(timezone.utc)
        return (
            NodeExecutionResult(
                success=True,
                output_name=node.output_name or "out",
                output_value={"v": "ok"},
            ),
            now,
            now,
        )

    scheduler._execute_node = _fake_execute_node


def _shadow_failure(scheduler: Scheduler) -> None:
    """替换 _execute_node 为恒失败桩（触发 node.failed 事件路径）。"""

    async def _fake_execute_node(
        node, state_manager, instance_id, trace_id, round_index, compiled
    ):
        now = datetime.now(timezone.utc)
        return (
            NodeExecutionResult(
                success=False,
                error_code="WF_NODE_EXECUTION_FAILED",
                error_message="脚本化失败",
            ),
            now,
            now,
        )

    scheduler._execute_node = _fake_execute_node


def _single_node_compiled() -> CompiledWorkflow:
    node = NodeDefinition(
        node_id="llm-1",
        name="llm-1",
        node_type=NodeType.LLM,
        configuration=LlmNodeConfiguration(model_identifier="m-1"),
        output_name="out",
    )
    return CompiledWorkflow(
        definition_id="wf-1",
        definition_version=1,
        nodes=[node],
        edges=[],
        entry_node_id="llm-1",
    )


def _two_node_compiled() -> CompiledWorkflow:
    n1 = NodeDefinition(
        node_id="n1",
        name="n1",
        node_type=NodeType.LLM,
        configuration=LlmNodeConfiguration(model_identifier="m-1"),
        output_name="out1",
    )
    n2 = NodeDefinition(
        node_id="n2",
        name="n2",
        node_type=NodeType.LLM,
        configuration=LlmNodeConfiguration(model_identifier="m-1"),
        output_name="out2",
    )
    return CompiledWorkflow(
        definition_id="wf-2",
        definition_version=1,
        nodes=[n1, n2],
        edges=[
            EdgeDefinition(edge_id="e1", source_node_id="n1", target_node_id="n2")
        ],
        entry_node_id="n1",
    )


# =============================================================================
# A. execute 穿参 → node.* 领域事件租户
# =============================================================================

class TestExecuteTenantPropagation:

    async def test_execute_tenant_propagates_to_success_event(self):
        publisher = _CaptureEventPublisher()
        scheduler = Scheduler(
            NodeExecutorRegistry(), domain_event_publisher=publisher
        )
        _shadow_success(scheduler)

        await scheduler.execute(
            _single_node_compiled(),
            SharedState(instance_id="inst-1"),
            "inst-1",
            "trace-1",
            tenant_id="acme",
        )

        succeeded = [
            e for e in publisher.events if e.event_type == "node.succeeded"
        ]
        assert len(succeeded) == 1
        assert succeeded[0].tenant_id == "acme", "穿参租户流入 node.succeeded"

    async def test_execute_without_tenant_falls_back_to_default(self):
        publisher = _CaptureEventPublisher()
        scheduler = Scheduler(
            NodeExecutorRegistry(), domain_event_publisher=publisher
        )
        _shadow_success(scheduler)

        await scheduler.execute(
            _single_node_compiled(),
            SharedState(instance_id="inst-1"),
            "inst-1",
            "trace-1",
        )

        succeeded = [
            e for e in publisher.events if e.event_type == "node.succeeded"
        ]
        assert succeeded[0].tenant_id == DEFAULT_TENANT_ID
        assert succeeded[0].tenant_id == "tenant_default", "W2b 统一后回退口径"

    async def test_failure_event_uses_tenant(self):
        publisher = _CaptureEventPublisher()
        scheduler = Scheduler(
            NodeExecutorRegistry(), domain_event_publisher=publisher
        )
        _shadow_failure(scheduler)

        with pytest.raises(NodeExecutionError):
            await scheduler.execute(
                _single_node_compiled(),
                SharedState(instance_id="inst-1"),
                "inst-1",
                "trace-1",
                tenant_id="acme",
            )

        failed = [e for e in publisher.events if e.event_type == "node.failed"]
        assert len(failed) == 1
        assert failed[0].tenant_id == "acme", "穿参租户流入 node.failed"


# =============================================================================
# B. resume_checkpoint 路径穿参保持
# =============================================================================

class TestResumeTenantPropagation:

    async def test_resume_checkpoint_path_keeps_tenant(self):
        repo = _CaptureCheckpointRepo()
        scheduler = Scheduler(
            NodeExecutorRegistry(), checkpoint_repository=repo
        )
        _shadow_success(scheduler)
        compiled = _two_node_compiled()

        await scheduler.execute(
            compiled, SharedState(instance_id="inst-2"), "inst-2", "trace-2"
        )
        assert len(repo.checkpoints) == 2
        resume_cp = repo.checkpoints[0]  # completed=n1, next=n2

        publisher = _CaptureEventPublisher()
        scheduler2 = Scheduler(
            NodeExecutorRegistry(),
            checkpoint_repository=_CaptureCheckpointRepo(),
            domain_event_publisher=publisher,
        )
        _shadow_success(scheduler2)

        await scheduler2.execute(
            compiled,
            SharedState(instance_id="inst-2"),
            "inst-2",
            "trace-2b",
            resume_checkpoint=resume_cp,
            tenant_id="acme",
        )

        succeeded = [
            e for e in publisher.events if e.event_type == "node.succeeded"
        ]
        assert [e.node_id for e in succeeded] == ["n2"], "resume 自 n2 续跑不重跑 n1"
        assert succeeded[0].tenant_id == "acme", "resume 路径租户穿参保持"


# =============================================================================
# C. 并行扇出：分支事件与分支内 node.* 事件租户
# =============================================================================

def _trigger_block() -> str:
    return "\n".join(
        [
            "  - node_id: trigger_start",
            "    name: 手动触发",
            "    node_type: trigger",
            "    configuration:",
            "      trigger_type: manual",
            "      input_template:",
            "        query: query",
        ]
    )


def _agent_block(node_id: str, output_name: str, references=None) -> str:
    lines = [
        f"  - node_id: {node_id}",
        f"    name: {node_id}",
        "    node_type: agent",
        f"    output_name: {output_name}",
        "    configuration:",
        "      model_identifier: qwen3.8-27b",
        "      system_prompt: >-",
        "        你是分析师，请输出 JSON：{result: string}。",
    ]
    if references:
        lines.append("      inputs:")
        lines.extend(f"        - reference: {r}" for r in references)
    return "\n".join(lines)


def _join_block(entries: List[str], config_lines: List[str]) -> str:
    lines = [
        "  - node_id: join",
        "    name: 并行汇聚",
        "    node_type: control_flow",
        "    configuration:",
        "      control_flow_kind: parallel_join",
        "      parallel_branches:",
    ]
    lines.extend(f"        - {entry}" for entry in entries)
    lines.extend(config_lines)
    return "\n".join(lines)


def _fanout_yaml() -> str:
    """两分支扇出：trigger → fork → {b1, b2} → join(wait_all) → after_join。"""
    return "\n".join(
        [
            'spec_version: "1.1"',
            "definition_id: tenant_fanout_flow",
            "name: 租户穿参扇出测试",
            "definition_version: 1",
            "",
            "inputs:",
            "  - name: query",
            "    type: string",
            "    required: true",
            "    description: 查询主题",
            "",
            "outputs:",
            "  - name: merged",
            "    type: object",
            "    description: 汇总输出",
            "",
            "nodes:",
            _trigger_block(),
            _agent_block("fork", "fork_out", references=["query"]),
            _agent_block("b1", "b1_out", references=["fork_out"]),
            _agent_block("b2", "b2_out", references=["fork_out"]),
            _join_block(
                ["b1", "b2"],
                [
                    "      parallel_failure_strategy: fast_fail",
                    "      wait_policy: wait_all",
                ],
            ),
            _agent_block(
                "after_join", "merged", references=["b1_out", "b2_out"]
            ),
            "",
            "edges:",
            "  - edge_id: e1",
            "    source_node_id: trigger_start",
            "    target_node_id: fork",
            "  - edge_id: e2",
            "    source_node_id: fork",
            "    target_node_id: b1",
            "  - edge_id: e3",
            "    source_node_id: fork",
            "    target_node_id: b2",
            "  - edge_id: e4",
            "    source_node_id: b1",
            "    target_node_id: join",
            "  - edge_id: e5",
            "    source_node_id: b2",
            "    target_node_id: join",
            "  - edge_id: e6",
            "    source_node_id: join",
            "    target_node_id: after_join",
        ]
    )


class _StubTriggerExecutor:
    @property
    def node_type(self):
        return NodeType.TRIGGER

    async def execute(self, ctx):
        return NodeExecutionResult(
            success=True, output_name="trigger", output_value={"started": True}
        )


class _StubAgentExecutor:
    @property
    def node_type(self):
        return NodeType.AGENT

    async def execute(self, ctx):
        return NodeExecutionResult(
            success=True,
            output_name=ctx.node_definition.effective_output_name,
            output_value={"result": f"done_{ctx.node_definition.node_id}"},
        )


class _PassThroughControlFlowExecutor:
    @property
    def node_type(self):
        return NodeType.CONTROL_FLOW

    async def execute(self, ctx):
        return NodeExecutionResult(success=True, output_name=None, output_value=None)


async def _compile_doc(yaml_text: str) -> CompiledWorkflow:
    parser = YamlWorkflowParser()
    definition = parser.parse(yaml_text)
    return await GraphCompiler().compile(definition)


class TestParallelTenantPropagation:

    async def test_parallel_events_use_tenant(self):
        publisher = _CaptureEventPublisher()
        registry = NodeExecutorRegistry()
        registry.register(_StubTriggerExecutor())
        registry.register(_StubAgentExecutor())
        registry.register(_PassThroughControlFlowExecutor())
        scheduler = Scheduler(
            executor_registry=registry,
            checkpoint_repository=_CaptureCheckpointRepo(),
            domain_event_publisher=publisher,
        )
        compiled = await _compile_doc(_fanout_yaml())

        await scheduler.execute(
            compiled,
            SharedState(
                instance_id="inst-3",
                node_outputs={"query": "topic"},
                current_node_id=compiled.entry_node_id,
            ),
            "inst-3",
            "trace-3",
            tenant_id="acme",
        )

        started = [
            e
            for e in publisher.events
            if e.event_type == "node.parallel_branch_started"
        ]
        completed = [
            e
            for e in publisher.events
            if e.event_type == "node.parallel_branch_completed"
        ]
        assert len(started) == 2
        assert len(completed) == 2
        assert all(e.tenant_id == "acme" for e in started + completed), (
            "PARALLEL_BRANCH_* 事件取穿参租户"
        )

        succeeded = [
            e for e in publisher.events if e.event_type == "node.succeeded"
        ]
        assert {e.node_id for e in succeeded} >= {
            "fork",
            "b1",
            "b2",
            "join",
            "after_join",
        }
        assert all(e.tenant_id == "acme" for e in succeeded), (
            "分支/汇聚/后继节点 node.succeeded 均取穿参租户"
        )


# =============================================================================
# D. run_and_record 集成形状：rt_run.tenant_id 流入 scheduler.execute
# =============================================================================

@pytest.fixture(autouse=True)
def _isolate_usage_recorder():
    """模块级记录器注入状态隔离：每个用例前后复位为 None（不泄漏他处）。"""
    set_usage_recorder(None)
    yield
    set_usage_recorder(None)


class _FakeRegistry:
    """run_and_record 最小假注册表：update_status/get/append_event。"""

    def __init__(self, rt):
        self.rt = rt
        self.events: List[dict] = []
        self.statuses: List[str] = []

    def update_status(self, instance_id, status):
        self.statuses.append(status)

    def get(self, instance_id):
        return self.rt

    def append_event(self, instance_id, event):
        self.events.append(event)


class TestRunAndRecordTenantPropagation:

    async def test_run_and_record_passes_rt_tenant_to_scheduler(
        self, monkeypatch
    ):
        monkeypatch.setattr(router_mod, "_domain_event_publisher", None)

        calls: List[dict] = []

        class _SchedulerSpy:
            async def execute(
                self,
                compiled,
                initial_state,
                instance_id,
                trace_id,
                resume_checkpoint=None,
                tenant_id=None,
            ):
                calls.append(
                    {
                        "instance_id": instance_id,
                        "trace_id": trace_id,
                        "resume_checkpoint": resume_checkpoint,
                        "tenant_id": tenant_id,
                    }
                )
                return SharedState(instance_id=instance_id)

        rt = type("RtStub", (), {})()
        rt.instance_id = "inst-4"
        rt.tenant_id = "acme"
        rt.definition_id = "wf-1"
        rt.business_category = None
        rt.events = []
        registry = _FakeRegistry(rt)

        await router_mod.run_and_record(
            _SchedulerSpy(),
            object(),
            SharedState(instance_id="inst-4"),
            "inst-4",
            "trace-4",
            registry,
        )

        assert calls == [
            {
                "instance_id": "inst-4",
                "trace_id": "trace-4",
                "resume_checkpoint": None,
                "tenant_id": "acme",
            }
        ], "rt_run.tenant_id 流入 scheduler.execute（W2a 穿参）"
        assert registry.statuses == ["running", "succeeded"]
