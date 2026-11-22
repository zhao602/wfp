# =============================================================================
# 实例级步数护栏单测（tests/unit/test_step_guard.py）
#
# 覆盖：arch_review_v2 任务 3.7（CHG-27；卡 A2；DC-10）
#   1. 开关三态：默认关闭（基线零变化，HC-07）/ 开启超限阻断 / 关闭超限放行；
#   2. max_steps 触发 WF_BUDGET_EXCEEDED（消息含节点与轮次定位）；
#   3. 与 WF_LOOP_NOT_CONVERGED 独立触发（实例级全局限额 vs 节点级局部限额，
#      先触发先报告；「无限环可阻断」断言）；
#   4. 边界语义：max_steps=N 允许 N 步、阻断第 N+1 步。
# =============================================================================

from pathlib import Path
from typing import Optional

import pytest

from orchestration_platform.configuration.settings import Settings
from orchestration_platform.domain.errors import (
    WF_BUDGET_EXCEEDED,
    WF_LOOP_NOT_CONVERGED,
    DomainError,
)
from orchestration_platform.domain.models.enums import NodeType
from orchestration_platform.domain.models.shared_state import SharedState
from orchestration_platform.engine.graph_compiler import GraphCompiler
from orchestration_platform.engine.node_executor_registry import NodeExecutorRegistry
from orchestration_platform.engine.nodes.base import NodeExecutionResult
from orchestration_platform.engine.nodes.control_flow_node_executor import (
    ControlFlowNodeExecutor,
)
from orchestration_platform.engine.nodes.trigger_node_executor import TriggerNodeExecutor
from orchestration_platform.engine.scheduler import Scheduler
from orchestration_platform.workflow.yaml_workflow_parser import YamlWorkflowParser

_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
_LOOP_YAML = (
    _FIXTURES_DIR / "valid_workflow_definitions" / "valid_with_loop.yaml"
).read_text(encoding="utf-8")


class _PassThroughStubExecutor:
    """透传桩执行器：全部节点成功、输出恒为整型 0（供循环终止条件安全求值）。"""

    def __init__(self, node_type: NodeType) -> None:
        self._node_type = node_type
        self.node_calls: dict = {}

    @property
    def node_type(self) -> NodeType:
        return self._node_type

    async def execute(self, context) -> NodeExecutionResult:
        node_id = context.node_definition.node_id
        self.node_calls[node_id] = self.node_calls.get(node_id, 0) + 1
        return NodeExecutionResult(
            success=True,
            output_name=getattr(
                context.node_definition, "effective_output_name", None
            ),
            output_value=0,
        )


def _chain_yaml(node_count: int) -> str:
    """生成 trigger + N 个 agent 节点的顺序链 YAML（无限环测试的外层骨架复用）。"""
    lines = [
        'spec_version: "1.0"',
        f"definition_id: chain_{node_count}",
        "name: 步数护栏链",
        "definition_version: 1",
        "inputs: []",
        "outputs: []",
        "nodes:",
        "  - node_id: trigger_manual",
        "    name: 手动触发",
        "    node_type: trigger",
        "    configuration:",
        "      trigger_type: manual",
    ]
    for i in range(node_count):
        lines += [
            f"  - node_id: agent_{i:02d}",
            f"    name: 节点{i:02d}",
            "    node_type: agent",
            "    configuration:",
            "      model_identifier: primary-model",
        ]
    lines.append("edges:")
    lines += [
        "  - edge_id: e0",
        "    source_node_id: trigger_manual",
        "    target_node_id: agent_00",
    ]
    for i in range(node_count - 1):
        lines += [
            f"  - edge_id: e{i + 1}",
            f"    source_node_id: agent_{i:02d}",
            f"    target_node_id: agent_{i + 1:02d}",
        ]
    return "\n".join(lines) + "\n"


async def _compile(yaml_text: str):
    definition = YamlWorkflowParser().parse(yaml_text)
    return await GraphCompiler().compile(definition)


def _build_registry() -> NodeExecutorRegistry:
    registry = NodeExecutorRegistry()
    registry.register(TriggerNodeExecutor())
    registry.register(ControlFlowNodeExecutor())
    registry.register(_PassThroughStubExecutor(NodeType.AGENT))
    return registry


def _guard_settings(enabled: bool, max_steps: int) -> Settings:
    return Settings(
        _env_file=None,
        workflow_step_guard_enabled=enabled,
        workflow_max_steps=max_steps,
    )


async def _run_chain(
    scheduler: Scheduler,
    compiled,
    instance_id: str,
    inputs: Optional[dict] = None,
) -> None:
    await scheduler.execute(
        compiled,
        SharedState(instance_id=instance_id, node_outputs=inputs or {}),
        instance_id,
        f"trace-{instance_id}",
    )


class TestStepGuard:
    """G6 实例级步数护栏（CHG-27；卡 A2；DC-10）。"""

    async def test_guard_disabled_over_limit_runs_to_completion(self):
        """开关关闭：超限工作流照常执行完成（默认关闭基线零变化，HC-07）。"""
        scheduler = Scheduler(executor_registry=_build_registry())
        scheduler._settings = _guard_settings(enabled=False, max_steps=2)
        compiled = await _compile(_chain_yaml(4))

        await _run_chain(scheduler, compiled, "inst-guard-off")

        agent_stub = scheduler._registry.get_executor(NodeType.AGENT)
        assert sum(agent_stub.node_calls.values()) == 4, "关闭时 4 节点全部执行"

    async def test_guard_enabled_blocks_over_limit(self):
        """开关开启且超限 → WF_BUDGET_EXCEEDED，消息含节点与轮次定位。

        max_steps=2：trigger(轮0) + agent_00(轮1) 两步放行，
        第 3 步 agent_01 被阻断。
        """
        scheduler = Scheduler(executor_registry=_build_registry())
        scheduler._settings = _guard_settings(enabled=True, max_steps=2)
        compiled = await _compile(_chain_yaml(4))

        with pytest.raises(DomainError) as exc_info:
            await _run_chain(scheduler, compiled, "inst-guard-on")
        assert exc_info.value.code == WF_BUDGET_EXCEEDED
        message = str(exc_info.value)
        assert "agent_01" in message, "消息含被阻断节点定位"
        assert "workflow_max_steps=2" in message, "消息含限额值"
        assert "第 3 轮" in message, "消息含被阻断轮次定位"

        agent_stub = scheduler._registry.get_executor(NodeType.AGENT)
        assert agent_stub.node_calls.get("agent_00", 0) == 1, "限额内节点已执行"
        assert "agent_01" not in agent_stub.node_calls, "超限节点未执行"

    async def test_guard_within_limit_completes(self):
        """开关开启但未超限 → 正常完成。"""
        scheduler = Scheduler(executor_registry=_build_registry())
        scheduler._settings = _guard_settings(enabled=True, max_steps=4)
        compiled = await _compile(_chain_yaml(3))

        await _run_chain(scheduler, compiled, "inst-guard-within")

        agent_stub = scheduler._registry.get_executor(NodeType.AGENT)
        assert sum(agent_stub.node_calls.values()) == 3

    async def test_guard_boundary_exact_limit_succeeds_and_next_blocked(self):
        """边界语义：max_steps=N 允许 N 步完成、阻断第 N+1 步。

        _chain_yaml(k) = trigger + k 个 agent = k+1 步：
        - max_steps=4 + 4 步链（k=3）→ 恰好完成；
        - max_steps=4 + 5 步链（k=4）→ 第 5 步被阻断。
        """
        scheduler_ok = Scheduler(executor_registry=_build_registry())
        scheduler_ok._settings = _guard_settings(enabled=True, max_steps=4)
        compiled_ok = await _compile(_chain_yaml(3))
        await _run_chain(scheduler_ok, compiled_ok, "inst-guard-boundary-ok")
        assert sum(
            scheduler_ok._registry.get_executor(NodeType.AGENT).node_calls.values()
        ) == 3, "恰好 4 步（含 trigger）全部执行"

        scheduler_over = Scheduler(executor_registry=_build_registry())
        scheduler_over._settings = _guard_settings(enabled=True, max_steps=4)
        compiled_over = await _compile(_chain_yaml(4))
        with pytest.raises(DomainError) as exc_info:
            await _run_chain(scheduler_over, compiled_over, "inst-guard-boundary-over")
        assert exc_info.value.code == WF_BUDGET_EXCEEDED
        assert "agent_03" in str(exc_info.value), "第 5 步（agent_03）被阻断"

    async def test_guard_independent_from_loop_not_converged(self):
        """与 WF_LOOP_NOT_CONVERGED 独立：无限环可被实例级护栏先行阻断。

        循环夹具 max_iterations=5、终止条件永不满足（quality_score 恒 0，
        预置输入避开「首次求值引用未产出输出」）：
        - 护栏关闭：loop 节点级限额触发 WF_LOOP_NOT_CONVERGED；
        - 护栏开启（max_steps=3）：实例级全局限额先行触发 WF_BUDGET_EXCEEDED
          （先触发先报告，两限额解耦、互不合并）。
        """
        # 护栏关闭：节点级局部限额触发
        scheduler_loop = Scheduler(executor_registry=_build_registry())
        scheduler_loop._settings = _guard_settings(enabled=False, max_steps=500)
        compiled_loop = await _compile(_LOOP_YAML)
        with pytest.raises(DomainError) as exc_info:
            await _run_chain(
                scheduler_loop,
                compiled_loop,
                "inst-loop-only",
                inputs={"quality_score": 0},
            )
        assert exc_info.value.code == WF_LOOP_NOT_CONVERGED

        # 护栏开启：实例级全局限额先行阻断（无限环可阻断）
        scheduler_guard = Scheduler(executor_registry=_build_registry())
        scheduler_guard._settings = _guard_settings(enabled=True, max_steps=3)
        with pytest.raises(DomainError) as exc_info:
            await _run_chain(
                scheduler_guard,
                compiled_loop,
                "inst-loop-guard",
                inputs={"quality_score": 0},
            )
        assert exc_info.value.code == WF_BUDGET_EXCEEDED, (
            "实例级护栏先于节点级限额触发（解耦、先触发先报告）"
        )

    def test_guard_default_settings_disabled(self):
        """默认配置：workflow_step_guard_enabled=False（HC-07 基线零变化）。"""
        settings = Settings(_env_file=None)
        assert settings.workflow_step_guard_enabled is False
        assert settings.workflow_max_steps == 500
        assert settings.workflow_max_steps >= 1
