# =============================================================================
# 并行扇出执行集成测试（tests/integration/test_parallel_fanout_execution.py）
#
# B6 roadmap_b6_parallel_fanout —— T6 执行/重放集成层（design.md §7 测试矩阵）：
#   1.  wait_all 收敛：输出合并 / join 恰一次 / PARALLEL_BRANCH_* 领域事件
#   2.  wait_all 检查点 branch_id 语义：分支节点=分支入口，S/join/单游标=None
#   3.  wait_any：首个成功分支到达即触发，其余分支取消（SKIPPED 语义）
#   4.  wait_n_of_m：第 wait_count 个成功分支到达时触发，剩余取消
#   5.  fast_fail：任一分支失败立即裁撤兄弟分支，实例沿 NodeExecutionError 失败
#   6.  失败轴 wait_all：等全部分支结束后聚合失败（首个失败胜出），join 不执行
#   7.  区域中途恢复：在途分支按位点重跑（at-least-once），join 恰一次
#   8.  触发已达成恢复：直接执行 join，不重跑任何分支
#   9.  re_execute 分叉重放：前沿空时全区域并行重建，join 恰一次
#   10. 线性流门禁：无扇出定义不激活区域、单游标执行（无并发、无分支事件）
# =============================================================================

import asyncio
import time
from collections import Counter
from typing import Dict, List, Optional, Set

import pytest

from orchestration_platform.workflow.yaml_workflow_parser import YamlWorkflowParser
from orchestration_platform.engine.graph_compiler import GraphCompiler
from orchestration_platform.engine.scheduler import Scheduler
from orchestration_platform.engine.node_executor_registry import NodeExecutorRegistry
from orchestration_platform.engine.nodes.base import NodeExecutionResult
from orchestration_platform.domain.models.shared_state import SharedState
from orchestration_platform.domain.models.checkpoint import Checkpoint
from orchestration_platform.domain.models.enums import NodeType
from orchestration_platform.domain.errors import NodeExecutionError


# =============================================================================
# 构造助手：YAML 文档 / 桩执行器 / 捕获仓储
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
        lines.extend(f"        - reference: {reference}" for reference in references)
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


def _fanout_yaml(
    branch_specs: List[tuple],
    join_config: List[str],
    after_refs: List[str],
    definition_id: str = "fanout_exec_flow",
) -> str:
    """构造扇出执行文档。

    branch_specs：[(分支入口, [路径节点...])]，入口本身是路径首节点；
    形状 trigger → fork → {各分支路径} → join → after_join。
    """
    nodes = [
        _trigger_block(),
        _agent_block("fork", "fork_out", references=["query"]),
    ]
    edges = [
        "  - edge_id: e0",
        "    source_node_id: trigger_start",
        "    target_node_id: fork",
    ]
    edge_seq = 1
    for entry, path_nodes in branch_specs:
        edges.extend(
            [
                f"  - edge_id: e{edge_seq}",
                "    source_node_id: fork",
                f"    target_node_id: {entry}",
            ]
        )
        edge_seq += 1
        prev_output = "fork_out"
        prev_node = None
        for path_node in path_nodes:
            nodes.append(
                _agent_block(path_node, f"{path_node}_out", references=[prev_output])
            )
            if prev_node is not None:
                edges.extend(
                    [
                        f"  - edge_id: e{edge_seq}",
                        f"    source_node_id: {prev_node}",
                        f"    target_node_id: {path_node}",
                    ]
                )
                edge_seq += 1
            prev_output = f"{path_node}_out"
            prev_node = path_node
        edges.extend(
            [
                f"  - edge_id: e{edge_seq}",
                f"    source_node_id: {prev_node}",
                "    target_node_id: join",
            ]
        )
        edge_seq += 1
    nodes.append(_join_block([entry for entry, _ in branch_specs], join_config))
    nodes.append(_agent_block("after_join", "merged", references=after_refs))
    edges.extend(
        [
            f"  - edge_id: e{edge_seq}",
            "    source_node_id: join",
            "    target_node_id: after_join",
        ]
    )
    return "\n".join(
        [
            'spec_version: "1.1"',
            f"definition_id: {definition_id}",
            "name: 并行扇出执行测试",
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
            "\n".join(nodes),
            "",
            "edges:",
            "\n".join(edges),
        ]
    )


def _linear_yaml() -> str:
    """线性五节点链（无扇出）：trigger → a1 → a2 → a3 → a4。"""
    nodes = [
        _trigger_block(),
        _agent_block("a1", "a1_out", references=["query"]),
        _agent_block("a2", "a2_out", references=["a1_out"]),
        _agent_block("a3", "a3_out", references=["a2_out"]),
        _agent_block("a4", "a4_out", references=["a3_out"]),
    ]
    edges = []
    chain = ["trigger_start", "a1", "a2", "a3", "a4"]
    for seq, (source, target) in enumerate(zip(chain, chain[1:])):
        edges.extend(
            [
                f"  - edge_id: le{seq}",
                f"    source_node_id: {source}",
                f"    target_node_id: {target}",
            ]
        )
    return "\n".join(
        [
            'spec_version: "1.1"',
            "definition_id: linear_gate_flow",
            "name: 线性流门禁测试",
            "definition_version: 1",
            "",
            "inputs:",
            "  - name: query",
            "    type: string",
            "    required: true",
            "    description: 查询主题",
            "",
            "outputs:",
            "  - name: a4_out",
            "    type: object",
            "    description: 末端输出",
            "",
            "nodes:",
            "\n".join(nodes),
            "",
            "edges:",
            "\n".join(edges),
        ]
    )


async def _compile_doc(yaml_text: str):
    parser = YamlWorkflowParser()
    definition = parser.parse(yaml_text)
    return await GraphCompiler().compile(definition)


class _CaptureCheckpointRepo:
    """捕获检查点的内存仓储（latest 按 round_index 最大）。"""

    def __init__(self):
        self.checkpoints: List[Checkpoint] = []

    async def append(self, checkpoint: Checkpoint) -> str:
        self.checkpoints.append(checkpoint)
        return checkpoint.checkpoint_id

    async def latest_for_instance(self, instance_id: str) -> Optional[Checkpoint]:
        best = None
        for cp in self.checkpoints:
            if cp.instance_id == instance_id and (
                best is None or cp.round_index > best.round_index
            ):
                best = cp
        return best


class _CaptureEventPublisher:
    """捕获领域事件的内存发布器。"""

    def __init__(self):
        self.events: List[object] = []

    async def publish_domain_event(self, event) -> None:
        self.events.append(event)


class _StubTriggerExecutor:
    @property
    def node_type(self):
        return NodeType.TRIGGER

    async def execute(self, ctx):
        return NodeExecutionResult(
            success=True, output_name="trigger", output_value={"started": True}
        )


class _ScriptedAgentExecutor:
    """脚本化 agent 执行器：按节点配置睡眠/失败，记录调用与并发度。"""

    def __init__(
        self,
        sleep_map: Optional[Dict[str, float]] = None,
        fail_nodes: Optional[Set[str]] = None,
    ):
        self.sleep_map = sleep_map or {}
        self.fail_nodes = fail_nodes or set()
        self.call_counts: Counter = Counter()
        self.active = 0
        self.max_active = 0

    @property
    def node_type(self):
        return NodeType.AGENT

    async def execute(self, ctx):
        node_id = ctx.node_definition.node_id
        self.call_counts[node_id] += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(self.sleep_map.get(node_id, 0.0))
            if node_id in self.fail_nodes:
                return NodeExecutionResult(
                    success=False,
                    error_code="WF_TEST_BRANCH_FAIL",
                    error_message=f"脚本化失败：{node_id}",
                )
            return NodeExecutionResult(
                success=True,
                output_name=ctx.node_definition.effective_output_name,
                output_value={"result": f"done_{node_id}"},
            )
        finally:
            self.active -= 1


class _JoinCountingControlFlowExecutor:
    """控制流执行器：join 计数（其余控制流节点直通）。"""

    def __init__(self):
        self.join_calls = 0

    @property
    def node_type(self):
        return NodeType.CONTROL_FLOW

    async def execute(self, ctx):
        if ctx.node_definition.node_id == "join":
            self.join_calls += 1
        return NodeExecutionResult(success=True, output_name=None, output_value=None)


def _build_scheduler(
    agent_exec: _ScriptedAgentExecutor,
    cf_exec: _JoinCountingControlFlowExecutor,
    repo: _CaptureCheckpointRepo,
    publisher: _CaptureEventPublisher,
) -> Scheduler:
    registry = NodeExecutorRegistry()
    registry.register(_StubTriggerExecutor())
    registry.register(agent_exec)
    registry.register(cf_exec)
    return Scheduler(
        executor_registry=registry,
        checkpoint_repository=repo,
        domain_event_publisher=publisher,
    )


def _parallel_events(publisher: _CaptureEventPublisher, event_type: str) -> List[object]:
    return [e for e in publisher.events if e.event_type == event_type]


def _event_statuses(publisher: _CaptureEventPublisher, event_type: str) -> List[tuple]:
    return [
        (e.payload["branch_entry_id"], e.payload["status"])
        for e in _parallel_events(publisher, event_type)
    ]


# =============================================================================
# 1/2：wait_all 收敛 + 检查点 branch_id 语义
# =============================================================================


class TestParallelFanoutWaitAll:
    """用例组 1/2：wait_all 收敛正确（输出合并 / join 恰一次 / 事件 / branch_id）。"""

    async def test_wait_all_convergence_and_branch_id(self):
        """wait_all 双分支收敛：并发执行、输出合并、join 恰一次、事件与 branch_id 正确。"""
        yaml_text = _fanout_yaml(
            branch_specs=[("b1", ["b1", "b1_mid"]), ("b2", ["b2"])],
            join_config=["      parallel_failure_strategy: fast_fail", "      wait_policy: wait_all"],
            after_refs=["b1_out", "b2_out"],
        )
        compiled = await _compile_doc(yaml_text)
        assert "join" in compiled.parallel_regions
        region = compiled.parallel_regions["join"]
        assert region.branch_entry_ids == ["b1", "b2"]
        assert region.wait_policy == "wait_all"

        agent_exec = _ScriptedAgentExecutor(sleep_map={"b1": 0.02, "b2": 0.02})
        cf_exec = _JoinCountingControlFlowExecutor()
        repo = _CaptureCheckpointRepo()
        publisher = _CaptureEventPublisher()
        scheduler = _build_scheduler(agent_exec, cf_exec, repo, publisher)

        initial_state = SharedState(
            instance_id="pf-wait-all",
            node_outputs={"query": "topic"},
            current_node_id=compiled.entry_node_id,
        )
        final_state = await scheduler.execute(
            compiled, initial_state, "pf-wait-all", "pf-trace-1"
        )

        # 输出合并：全部分支输出 + 汇合后输出对 join 后继可见
        for name in ("fork_out", "b1_out", "b1_mid_out", "b2_out", "merged"):
            assert name in final_state.node_outputs, f"缺输出 {name}"

        # join 恰一次；分支真实并发
        assert cf_exec.join_calls == 1
        assert agent_exec.max_active >= 2

        # 检查点 branch_id 语义：分支节点=分支入口；S/join/单游标=None
        cps = {cp.completed_node_id: cp for cp in repo.checkpoints}
        assert cps["fork"].branch_id is None
        assert cps["join"].branch_id is None
        assert cps["after_join"].branch_id is None
        assert cps["b1"].branch_id == "b1"
        assert cps["b1_mid"].branch_id == "b1"
        assert cps["b2"].branch_id == "b2"

        # 轮次唯一（CHG-15 (instance_id, round_index) 唯一约束）
        rounds = [cp.round_index for cp in repo.checkpoints]
        assert len(rounds) == len(set(rounds))

        # 领域事件：每分支 started + completed(succeeded)；每节点 node.succeeded
        started = _event_statuses(publisher, "node.parallel_branch_started")
        completed = _event_statuses(publisher, "node.parallel_branch_completed")
        assert sorted(started) == [("b1", "started"), ("b2", "started")]
        assert sorted(completed) == [("b1", "succeeded"), ("b2", "succeeded")]
        succeeded_events = [
            e for e in publisher.events if e.event_type == "node.succeeded"
        ]
        assert len(succeeded_events) == 7  # trigger+fork+b1+b1_mid+b2+join+after_join

        # 收敛后前沿三表清空
        assert final_state.parallel_positions == {}
        assert final_state.parallel_arrivals == {}
        assert final_state.parallel_cancelled == {}


# =============================================================================
# 3/4：wait_any / wait_n_of_m 触发与裁撤
# =============================================================================


class TestParallelWaitAnyAndNOfM:
    """用例组 3/4：等待轴部分触发——剩余分支取消（SKIPPED 语义）。"""

    async def test_wait_any_first_success_cancels_sibling(self):
        """wait_any：首个成功分支（b2，快）触发 join；慢分支 b1 取消且不出输出。"""
        yaml_text = _fanout_yaml(
            branch_specs=[("b1", ["b1"]), ("b2", ["b2"])],
            join_config=["      parallel_failure_strategy: fast_fail", "      wait_policy: wait_any"],
            after_refs=["b2_out"],
        )
        compiled = await _compile_doc(yaml_text)
        assert compiled.parallel_regions["join"].wait_policy == "wait_any"

        agent_exec = _ScriptedAgentExecutor(sleep_map={"b1": 0.15, "b2": 0.01})
        cf_exec = _JoinCountingControlFlowExecutor()
        repo = _CaptureCheckpointRepo()
        publisher = _CaptureEventPublisher()
        scheduler = _build_scheduler(agent_exec, cf_exec, repo, publisher)

        initial_state = SharedState(
            instance_id="pf-wait-any",
            node_outputs={"query": "topic"},
            current_node_id=compiled.entry_node_id,
        )
        final_state = await scheduler.execute(
            compiled, initial_state, "pf-wait-any", "pf-trace-2"
        )

        assert cf_exec.join_calls == 1
        assert agent_exec.call_counts["b1"] == 1  # 已启动后被裁撤
        assert agent_exec.call_counts["b2"] == 1
        assert "b2_out" in final_state.node_outputs
        assert "b1_out" not in final_state.node_outputs  # 取消发生在 await 点，未写输出
        assert "merged" in final_state.node_outputs

        completed = _event_statuses(publisher, "node.parallel_branch_completed")
        assert sorted(completed) == [("b1", "cancelled"), ("b2", "succeeded")]

        # 被取消分支无检查点（裁撤后不再产生输出/检查点）
        assert all(cp.completed_node_id != "b1" for cp in repo.checkpoints)

    async def test_wait_n_of_m_triggers_at_count(self):
        """wait_n_of_m（n=2/3）：第 2 个成功分支到达触发，剩余分支取消。"""
        yaml_text = _fanout_yaml(
            branch_specs=[("b1", ["b1"]), ("b2", ["b2"]), ("b3", ["b3"])],
            join_config=[
                "      parallel_failure_strategy: fast_fail",
                "      wait_policy: wait_n_of_m",
                "      wait_count: 2",
            ],
            after_refs=["b2_out", "b3_out"],
        )
        compiled = await _compile_doc(yaml_text)
        region = compiled.parallel_regions["join"]
        assert region.wait_policy == "wait_n_of_m"
        assert region.wait_count == 2

        # 到达顺序固定：b3(0.01) → b2(0.05) → 触发；b1(0.15) 被裁撤
        agent_exec = _ScriptedAgentExecutor(
            sleep_map={"b1": 0.15, "b2": 0.05, "b3": 0.01}
        )
        cf_exec = _JoinCountingControlFlowExecutor()
        repo = _CaptureCheckpointRepo()
        publisher = _CaptureEventPublisher()
        scheduler = _build_scheduler(agent_exec, cf_exec, repo, publisher)

        initial_state = SharedState(
            instance_id="pf-n-of-m",
            node_outputs={"query": "topic"},
            current_node_id=compiled.entry_node_id,
        )
        final_state = await scheduler.execute(
            compiled, initial_state, "pf-n-of-m", "pf-trace-3"
        )

        assert cf_exec.join_calls == 1
        assert "b2_out" in final_state.node_outputs
        assert "b3_out" in final_state.node_outputs
        assert "b1_out" not in final_state.node_outputs

        completed = _event_statuses(publisher, "node.parallel_branch_completed")
        assert sorted(completed) == [
            ("b1", "cancelled"),
            ("b2", "succeeded"),
            ("b3", "succeeded"),
        ]


# =============================================================================
# 5/6：失败轴（fast_fail / wait_all 聚合）
# =============================================================================


class TestParallelFailureStrategies:
    """用例组 5/6：失败语义——fast_fail 立即失败；失败轴 wait_all 聚合。"""

    async def test_fast_fail_cancels_sibling_and_fails_instance(self):
        """fast_fail：分支失败立即裁撤兄弟分支，实例沿 NodeExecutionError 失败。"""
        yaml_text = _fanout_yaml(
            branch_specs=[("b1", ["b1"]), ("b2", ["b2"])],
            join_config=["      parallel_failure_strategy: fast_fail", "      wait_policy: wait_all"],
            after_refs=["b1_out", "b2_out"],
        )
        compiled = await _compile_doc(yaml_text)

        agent_exec = _ScriptedAgentExecutor(
            sleep_map={"b1": 0.01, "b2": 0.15}, fail_nodes={"b1"}
        )
        cf_exec = _JoinCountingControlFlowExecutor()
        repo = _CaptureCheckpointRepo()
        publisher = _CaptureEventPublisher()
        scheduler = _build_scheduler(agent_exec, cf_exec, repo, publisher)

        initial_state = SharedState(
            instance_id="pf-fast-fail",
            node_outputs={"query": "topic"},
            current_node_id=compiled.entry_node_id,
        )
        with pytest.raises(NodeExecutionError):
            await scheduler.execute(
                compiled, initial_state, "pf-fast-fail", "pf-trace-4"
            )

        assert cf_exec.join_calls == 0  # join 未执行
        assert agent_exec.call_counts["b2"] == 1  # b2 已启动后被裁撤
        completed = _event_statuses(publisher, "node.parallel_branch_completed")
        assert sorted(completed) == [("b1", "failed"), ("b2", "cancelled")]
        # 失败节点无检查点；无 join 检查点
        assert all(cp.completed_node_id not in ("b1", "join") for cp in repo.checkpoints)

    async def test_failure_wait_all_aggregates_after_all_end(self):
        """失败轴 wait_all：等全部分支结束后聚合失败（首个失败胜出），join 不执行。"""
        yaml_text = _fanout_yaml(
            branch_specs=[("b1", ["b1"]), ("b2", ["b2"])],
            join_config=["      parallel_failure_strategy: wait_all", "      wait_policy: wait_all"],
            after_refs=["b1_out", "b2_out"],
        )
        compiled = await _compile_doc(yaml_text)

        agent_exec = _ScriptedAgentExecutor(
            sleep_map={"b1": 0.01, "b2": 0.15}, fail_nodes={"b1"}
        )
        cf_exec = _JoinCountingControlFlowExecutor()
        repo = _CaptureCheckpointRepo()
        publisher = _CaptureEventPublisher()
        scheduler = _build_scheduler(agent_exec, cf_exec, repo, publisher)

        initial_state = SharedState(
            instance_id="pf-fail-wait",
            node_outputs={"query": "topic"},
            current_node_id=compiled.entry_node_id,
        )
        with pytest.raises(NodeExecutionError):
            await scheduler.execute(
                compiled, initial_state, "pf-fail-wait", "pf-trace-5"
            )

        assert cf_exec.join_calls == 0
        # b2 等待至自然结束（聚合语义）：完整执行并写输出
        assert agent_exec.call_counts["b2"] == 1
        completed = _event_statuses(publisher, "node.parallel_branch_completed")
        assert sorted(completed) == [("b1", "failed"), ("b2", "succeeded")]


# =============================================================================
# 7/8/9：检查点恢复 / re_execute 分叉重放（roadmap:155 缓解项：replay 用例先行）
# =============================================================================


class TestParallelRegionResume:
    """用例组 7/8/9：区域中途恢复——前沿重建、触发直入 join、分叉重放。"""

    @staticmethod
    def _fanout_shape() -> str:
        return _fanout_yaml(
            branch_specs=[("b1", ["b1", "b1_mid"]), ("b2", ["b2"])],
            join_config=["      parallel_failure_strategy: fast_fail", "      wait_policy: wait_all"],
            after_refs=["b1_out", "b2_out"],
            definition_id="pf_resume_flow",
        )

    async def test_resume_mid_region_rebuilds_frontier(self):
        """区域中途恢复：在途分支按位点重跑（b1 自 b1_mid），已完成分支不重跑，join 恰一次。"""
        compiled = await _compile_doc(self._fanout_shape())

        # 合成中途检查点：b1 已完成（输出已写、位点推进到 b1_mid），b2 在途于 b2
        snapshot = SharedState(
            instance_id="pf-resume-mid",
            node_outputs={"query": "topic", "fork_out": {"v": 1}, "b1_out": {"v": 2}},
            current_node_id="b1_mid",
            round_index=3,
            parallel_positions={"join": {"b1": "b1_mid", "b2": "b2"}},
            parallel_arrivals={"join": []},
            parallel_cancelled={"join": []},
        )
        resume_cp = Checkpoint(
            checkpoint_id="cp_pf_resume_mid",
            instance_id="pf-resume-mid",
            completed_node_id="b1",
            next_node_id="b1_mid",
            state_snapshot=snapshot,
            round_index=3,
            branch_id="b1",
        )

        agent_exec = _ScriptedAgentExecutor(sleep_map={"b1_mid": 0.02, "b2": 0.02})
        cf_exec = _JoinCountingControlFlowExecutor()
        repo = _CaptureCheckpointRepo()
        publisher = _CaptureEventPublisher()
        scheduler = _build_scheduler(agent_exec, cf_exec, repo, publisher)

        final_state = await scheduler.execute(
            compiled,
            SharedState(instance_id="pf-resume-mid"),
            "pf-resume-mid",
            "pf-trace-6",
            resume_checkpoint=resume_cp,
        )

        # b1 不重跑（已完成的分支路径前缀）；b1_mid / b2 按位点执行
        assert agent_exec.call_counts["b1"] == 0
        assert agent_exec.call_counts["b1_mid"] == 1
        assert agent_exec.call_counts["b2"] == 1
        assert cf_exec.join_calls == 1

        # 输出合并与检查点 branch_id 语义保持
        for name in ("fork_out", "b1_out", "b1_mid_out", "b2_out", "merged"):
            assert name in final_state.node_outputs, f"缺输出 {name}"
        cps = {cp.completed_node_id: cp for cp in repo.checkpoints}
        assert cps["b1_mid"].branch_id == "b1"
        assert cps["b2"].branch_id == "b2"
        assert cps["join"].branch_id is None

        # 恢复轮次自检查点轮次 + 1 续算，轮次唯一
        rounds = [cp.round_index for cp in repo.checkpoints]
        assert min(rounds) == 4  # round_index=3 的检查点续跑自第 4 轮
        assert len(rounds) == len(set(rounds))

        completed = _event_statuses(publisher, "node.parallel_branch_completed")
        assert sorted(completed) == [("b1", "succeeded"), ("b2", "succeeded")]

    async def test_resume_with_wait_satisfied_runs_join_directly(self):
        """触发已达成恢复（wait_any，b2 已到达，b1 在途）：直接执行 join，不重跑任何分支。"""
        yaml_text = _fanout_yaml(
            branch_specs=[("b1", ["b1", "b1_mid"]), ("b2", ["b2"])],
            join_config=["      parallel_failure_strategy: fast_fail", "      wait_policy: wait_any"],
            after_refs=["b2_out"],
            definition_id="pf_resume_satisfied",
        )
        compiled = await _compile_doc(yaml_text)

        # 合成检查点：b2 已到达（arrivals），b1 在途于 b1_mid（位点残留）
        snapshot = SharedState(
            instance_id="pf-resume-sat",
            node_outputs={"query": "topic", "fork_out": {"v": 1}, "b2_out": {"v": 2}},
            current_node_id="join",
            round_index=3,
            parallel_positions={"join": {"b1": "b1_mid"}},
            parallel_arrivals={"join": ["b2"]},
            parallel_cancelled={"join": []},
        )
        resume_cp = Checkpoint(
            checkpoint_id="cp_pf_resume_sat",
            instance_id="pf-resume-sat",
            completed_node_id="b2",
            next_node_id="join",
            state_snapshot=snapshot,
            round_index=3,
            branch_id="b2",
        )

        agent_exec = _ScriptedAgentExecutor()
        cf_exec = _JoinCountingControlFlowExecutor()
        repo = _CaptureCheckpointRepo()
        publisher = _CaptureEventPublisher()
        scheduler = _build_scheduler(agent_exec, cf_exec, repo, publisher)

        final_state = await scheduler.execute(
            compiled,
            SharedState(instance_id="pf-resume-sat"),
            "pf-resume-sat",
            "pf-trace-7",
            resume_checkpoint=resume_cp,
        )

        # 触发预检命中：零分支重跑、零分支事件，join 恰一次后收敛
        assert agent_exec.call_counts["b1"] == 0
        assert agent_exec.call_counts["b1_mid"] == 0
        assert agent_exec.call_counts["b2"] == 0
        assert agent_exec.call_counts["after_join"] == 1
        assert cf_exec.join_calls == 1
        assert _parallel_events(publisher, "node.parallel_branch_started") == []
        assert _parallel_events(publisher, "node.parallel_branch_completed") == []
        assert "merged" in final_state.node_outputs
        assert final_state.parallel_positions == {}
        assert final_state.parallel_arrivals == {}
        assert final_state.parallel_cancelled == {}

    async def test_reexecute_fork_from_mid_region_reruns_all(self):
        """re_execute 分叉重放：前沿空 + 分支部分输出 → 全区域并行重建，join 恰一次。"""
        compiled = await _compile_doc(self._fanout_shape())

        # 合成分叉源检查点：仅 fork 完成，b1 有残留输出（重放自 fork 重新执行全区域）
        snapshot = SharedState(
            instance_id="pf-reexec-fork",
            node_outputs={"query": "topic", "fork_out": {"v": 1}, "b1_out": {"v": "旧"}},
            current_node_id="b1",
            round_index=1,
        )
        resume_cp = Checkpoint(
            checkpoint_id="cp_pf_reexec_fork",
            instance_id="pf-reexec-fork",
            completed_node_id="fork",
            next_node_id="b1",
            state_snapshot=snapshot,
            round_index=1,
            branch_id=None,
        )

        agent_exec = _ScriptedAgentExecutor(sleep_map={"b1": 0.02, "b2": 0.02})
        cf_exec = _JoinCountingControlFlowExecutor()
        repo = _CaptureCheckpointRepo()
        publisher = _CaptureEventPublisher()
        scheduler = _build_scheduler(agent_exec, cf_exec, repo, publisher)

        final_state = await scheduler.execute(
            compiled,
            SharedState(instance_id="pf-reexec-fork"),
            "pf-reexec-fork",
            "pf-trace-8",
            resume_checkpoint=resume_cp,
        )

        # 全分支重建：b1/b1_mid/b2 全部重执行，旧输出被覆盖，join 恰一次
        assert agent_exec.call_counts["b1"] == 1
        assert agent_exec.call_counts["b1_mid"] == 1
        assert agent_exec.call_counts["b2"] == 1
        assert cf_exec.join_calls == 1
        assert final_state.node_outputs["b1_out"] == {"result": "done_b1"}
        for name in ("b1_mid_out", "b2_out", "merged"):
            assert name in final_state.node_outputs

        completed = _event_statuses(publisher, "node.parallel_branch_completed")
        assert sorted(completed) == [("b1", "succeeded"), ("b2", "succeeded")]
        assert final_state.parallel_positions == {}


# =============================================================================
# 10：线性流门禁（无扇出走原单游标路径，宽松上界防 flaky）
# =============================================================================


class TestLinearFlowGate:
    """用例组 10：线性流门禁——无扇出定义零区域激活、单游标执行。"""

    async def test_linear_flow_single_cursor_no_dispatch(self):
        """线性五节点链：区域表空、无并发、无分支事件、执行耗时在宽松上界内。"""
        compiled = await _compile_doc(_linear_yaml())
        assert compiled.parallel_regions == {}

        agent_exec = _ScriptedAgentExecutor(sleep_map={"a1": 0.01, "a2": 0.01, "a3": 0.01, "a4": 0.01})
        cf_exec = _JoinCountingControlFlowExecutor()
        repo = _CaptureCheckpointRepo()
        publisher = _CaptureEventPublisher()
        scheduler = _build_scheduler(agent_exec, cf_exec, repo, publisher)

        initial_state = SharedState(
            instance_id="pf-linear",
            node_outputs={"query": "topic"},
            current_node_id=compiled.entry_node_id,
        )
        started = time.perf_counter()
        final_state = await scheduler.execute(
            compiled, initial_state, "pf-linear", "pf-trace-9"
        )
        elapsed = time.perf_counter() - started

        # 单游标：执行器永不并发、无任何并行分支事件、前沿三表保持为空
        assert agent_exec.max_active == 1
        assert _parallel_events(publisher, "node.parallel_branch_started") == []
        assert _parallel_events(publisher, "node.parallel_branch_completed") == []
        assert final_state.parallel_positions == {}
        assert final_state.parallel_arrivals == {}
        assert final_state.parallel_cancelled == {}

        # 输出齐全 + 宽松耗时上界（纯执行 4×0.01s；上界防 flaky，仅拦截结构性退化）
        for name in ("a1_out", "a2_out", "a3_out", "a4_out"):
            assert name in final_state.node_outputs
        assert elapsed < 2.0, f"线性流耗时 {elapsed:.3f}s 超宽松上界"

        # 检查点 branch_id 全 None（单游标路径）
        assert all(cp.branch_id is None for cp in repo.checkpoints)
