# =============================================================================
# 并行扇出编译期识别单测（tests/unit/test_parallel_fanout_compile.py）
#
# 覆盖：roadmap_b6_parallel_fanout T4（design §3.1 激活判定 / §3.3 静态冲突校验）
# 用例矩阵（≥8）：
#   1.  激活区域元数据提取（fork/entries/paths/wait/failure + 查询助手）；
#   2.  无唯一分叉源 → 非活跃 + 警告（回退单游标）；
#   3.  分支路径非线性（分支节点多条出边）→ 非活跃 + 警告；
#   4.  分支路径交叉 → 非活跃 + 警告；
#   5.  parallel_branches 超 parallel_branch_max → 拒绝编译；
#   6.  等待字段校验：wait_n_of_m 缺 wait_count / 非 wait_n_of_m 带 wait_count /
#       wait_count 超分支数 / 非活跃形状 + 显式等待语义 → 拒绝编译；
#       wait_n_of_m 合法取值 → 激活区域携带等待策略；
#   7.  兄弟分支有效输出重名 → 拒绝编译；
#   8.  兄弟分支交叉引用（多拥有者歧义：分叉前 + 兄弟同名）→ 拒绝编译；
#   9.  既有范例串行化形态（examples/parallel_execution）→ 零错误、零激活（回归锚点）；
#   10. 未声明 parallel_branches 的汇聚 → 不参与识别、无警告。
# =============================================================================

import logging
from pathlib import Path

import pytest

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.errors import (
    WF_VALIDATION_FAILED,
    WorkflowValidationError,
)
from orchestration_platform.engine.graph_compiler import GraphCompiler
from orchestration_platform.workflow.yaml_workflow_parser import YamlWorkflowParser


# =============================================================================
# YAML 构造助手（最小激活形状：trigger → fork → {b1 → b1_mid, b2} → join → after_join）
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


def _join_block(entries, extra_config: str = "") -> str:
    lines = [
        "  - node_id: join",
        "    name: 并行汇聚",
        "    node_type: control_flow",
        "    configuration:",
        "      control_flow_kind: parallel_join",
    ]
    if entries:
        lines.append("      parallel_branches:")
        lines.extend(f"        - {entry}" for entry in entries)
    lines.append("      parallel_failure_strategy: fast_fail")
    if extra_config:
        lines.append(extra_config)
    return "\n".join(lines)


def _edge(edge_id: str, source: str, target: str) -> str:
    return "\n".join(
        [
            f"  - edge_id: {edge_id}",
            f"    source_node_id: {source}",
            f"    target_node_id: {target}",
        ]
    )


def _fanout_doc(nodes, edges, outputs, definition_id: str = "fanout_flow") -> str:
    output_lines = "\n".join(
        f"  - name: {name}\n    type: object\n    description: {name} 输出" for name in outputs
    )
    return "\n".join(
        [
            'spec_version: "1.1"',
            f"definition_id: {definition_id}",
            "name: 并行扇出编译测试",
            "definition_version: 1",
            "",
            "inputs:",
            "  - name: query",
            "    type: string",
            "    required: true",
            "    description: 查询主题",
            "",
            "outputs:",
            output_lines,
            "",
            "nodes:",
            "\n".join(nodes),
            "",
            "edges:",
            "\n".join(edges),
        ]
    )


def _base_active_nodes(join_extra: str = ""):
    return [
        _trigger_block(),
        _agent_block("fork", "fork_out", references=["query"]),
        _agent_block("b1", "b1_out", references=["fork_out"]),
        _agent_block("b1_mid", "b1_mid_out", references=["b1_out"]),
        _agent_block("b2", "b2_out", references=["fork_out"]),
        _join_block(["b1", "b2"], extra_config=join_extra),
        _agent_block("after_join", "merged", references=["b1_out", "b2_out"]),
    ]


def _base_active_edges():
    return [
        _edge("e0", "trigger_start", "fork"),
        _edge("e1", "fork", "b1"),
        _edge("e2", "fork", "b2"),
        _edge("e3", "b1", "b1_mid"),
        _edge("e4", "b1_mid", "join"),
        _edge("e5", "b2", "join"),
        _edge("e6", "join", "after_join"),
    ]


def _fork_ambiguous_nodes(join_extra: str = ""):
    """无唯一分叉源形状：b1 ← fork，b2 ← fork2（分叉源候选二义）。"""
    return [
        _trigger_block(),
        _agent_block("fork", "fork_out", references=["query"]),
        _agent_block("fork2", "fork2_out", references=["query"]),
        _agent_block("b1", "b1_out", references=["fork_out"]),
        _agent_block("b2", "b2_out", references=["fork2_out"]),
        _join_block(["b1", "b2"], extra_config=join_extra),
        _agent_block("after_join", "merged", references=["b1_out", "b2_out"]),
    ]


def _fork_ambiguous_edges():
    return [
        _edge("e0", "trigger_start", "fork"),
        _edge("e1", "trigger_start", "fork2"),
        _edge("e2", "fork", "b1"),
        _edge("e3", "fork2", "b2"),
        _edge("e4", "b1", "join"),
        _edge("e5", "b2", "join"),
        _edge("e6", "join", "after_join"),
    ]


_BASE_OUTPUTS = ["b1_out", "b2_out", "merged"]


async def _compile_yaml(yaml_text: str):
    definition = YamlWorkflowParser().parse(yaml_text)
    return await GraphCompiler().compile(definition)


def _fanout_warning_records(caplog):
    return [
        record
        for record in caplog.records
        if "未激活并行扇出" in record.getMessage()
    ]


# =============================================================================
# 用例
# =============================================================================

class TestParallelFanoutCompile:
    """并行扇出区域编译期识别（激活判定 / 等待字段 / 静态冲突 / 兼容回退）。"""

    async def test_active_region_meta_extracted(self):
        """用例 1：严格扇出形状激活，元数据与查询助手就位。"""
        compiled = await _compile_yaml(
            _fanout_doc(_base_active_nodes(), _base_active_edges(), _BASE_OUTPUTS)
        )
        assert set(compiled.parallel_regions.keys()) == {"join"}
        region = compiled.parallel_regions["join"]
        assert region.join_node_id == "join"
        assert region.fork_source_node_id == "fork"
        assert region.branch_entry_ids == ["b1", "b2"]
        assert region.branch_paths == {"b1": ["b1", "b1_mid"], "b2": ["b2"]}
        assert region.wait_policy == "wait_all"
        assert region.wait_count is None
        assert region.failure_strategy == "fast_fail"
        assert compiled.region_by_fork_source("fork") is region
        assert compiled.region_containing("b1_mid") is region
        assert compiled.region_containing("join") is region
        assert compiled.region_containing("after_join") is None

    async def test_no_unique_fork_source_inactive_with_warning(self, caplog):
        """用例 2：分叉源二义 → 非活跃 + 警告，回退单游标语义。"""
        with caplog.at_level(
            logging.WARNING, logger="orchestration_platform.engine.graph_compiler"
        ):
            compiled = await _compile_yaml(
                _fanout_doc(
                    _fork_ambiguous_nodes(), _fork_ambiguous_edges(), _BASE_OUTPUTS
                )
            )
        assert compiled.parallel_regions == {}
        assert len(_fanout_warning_records(caplog)) == 1
        assert "join" in _fanout_warning_records(caplog)[0].getMessage()

    async def test_nonlinear_branch_path_inactive_with_warning(self, caplog):
        """用例 3：分支节点多条出边（非线性路径）→ 非活跃 + 警告。"""
        nodes = [
            _trigger_block(),
            _agent_block("fork", "fork_out", references=["query"]),
            _agent_block("b1", "b1_out", references=["fork_out"]),
            _agent_block("b2", "b2_out", references=["fork_out"]),
            _join_block(["b1", "b2"]),
            _agent_block("after_join", "merged", references=["b1_out", "b2_out"]),
        ]
        edges = [
            _edge("e0", "trigger_start", "fork"),
            _edge("e1", "fork", "b1"),
            _edge("e2", "fork", "b2"),
            _edge("e3", "b1", "join"),
            _edge("e4", "b1", "after_join"),
            _edge("e5", "b2", "join"),
            _edge("e6", "join", "after_join"),
        ]
        with caplog.at_level(
            logging.WARNING, logger="orchestration_platform.engine.graph_compiler"
        ):
            compiled = await _compile_yaml(_fanout_doc(nodes, edges, _BASE_OUTPUTS))
        assert compiled.parallel_regions == {}
        assert len(_fanout_warning_records(caplog)) == 1

    async def test_branch_path_crossing_inactive_with_warning(self, caplog):
        """用例 4：兄弟分支路径交叉（共享中段节点）→ 非活跃 + 警告。"""
        nodes = [
            _trigger_block(),
            _agent_block("fork", "fork_out", references=["query"]),
            _agent_block("b1", "b1_out", references=["fork_out"]),
            _agent_block("b1m", "b1m_out", references=["b1_out"]),
            _agent_block("b2", "b2_out", references=["fork_out"]),
            _agent_block("b2m", "b2m_out", references=["b2_out"]),
            _join_block(["b1", "b2"]),
            _agent_block("after_join", "merged", references=["b1m_out", "b2m_out"]),
        ]
        edges = [
            _edge("e0", "trigger_start", "fork"),
            _edge("e1", "fork", "b1"),
            _edge("e2", "fork", "b2"),
            _edge("e3", "b1", "b1m"),
            _edge("e4", "b2", "b2m"),
            _edge("e5", "b1m", "b2m"),
            _edge("e6", "b2m", "join"),
            _edge("e7", "join", "after_join"),
        ]
        with caplog.at_level(
            logging.WARNING, logger="orchestration_platform.engine.graph_compiler"
        ):
            compiled = await _compile_yaml(
                _fanout_doc(nodes, edges, ["b1m_out", "b2m_out", "merged"])
            )
        assert compiled.parallel_regions == {}
        assert len(_fanout_warning_records(caplog)) == 1

    async def test_branch_count_over_limit_rejected(self):
        """用例 5：parallel_branches 超 parallel_branch_max → 拒绝编译。"""
        nodes = [
            _trigger_block(),
            _agent_block("fork", "fork_out", references=["query"]),
        ]
        edges = [_edge("e0", "trigger_start", "fork")]
        entries = []
        for index in range(1, 12):
            node_id = f"b{index:02d}"
            entries.append(node_id)
            nodes.append(_agent_block(node_id, f"{node_id}_out", references=["fork_out"]))
            edges.append(_edge(f"ef{index}", "fork", node_id))
            edges.append(_edge(f"ej{index}", node_id, "join"))
        nodes.append(_join_block(entries))
        nodes.append(_agent_block("after_join", "merged"))
        edges.append(_edge("ez", "join", "after_join"))

        with pytest.raises(WorkflowValidationError) as exc_info:
            await _compile_yaml(_fanout_doc(nodes, edges, ["merged"]))
        assert exc_info.value.code == WF_VALIDATION_FAILED
        assert exc_info.value.details["rule"] == "parallel_branches_over_limit"
        assert exc_info.value.details["limit"] == get_settings().parallel_branch_max

    async def test_wait_n_of_m_without_count_rejected(self):
        """用例 6a：wait_n_of_m 缺 wait_count → 拒绝编译。"""
        nodes = _base_active_nodes(
            join_extra="      wait_policy: wait_n_of_m"
        )
        with pytest.raises(WorkflowValidationError) as exc_info:
            await _compile_yaml(_fanout_doc(nodes, _base_active_edges(), _BASE_OUTPUTS))
        assert exc_info.value.code == WF_VALIDATION_FAILED
        assert exc_info.value.details["rule"] == "wait_n_of_m_requires_wait_count"

    async def test_wait_count_on_wait_all_rejected(self):
        """用例 6b：wait_all 显式设置 wait_count → 拒绝编译。"""
        nodes = _base_active_nodes(
            join_extra="      wait_policy: wait_all\n      wait_count: 1"
        )
        with pytest.raises(WorkflowValidationError) as exc_info:
            await _compile_yaml(_fanout_doc(nodes, _base_active_edges(), _BASE_OUTPUTS))
        assert exc_info.value.code == WF_VALIDATION_FAILED
        assert exc_info.value.details["rule"] == "wait_count_requires_wait_n_of_m"

    async def test_wait_count_over_branches_rejected(self):
        """用例 6c：wait_count 超分支数（3 > 2）→ 拒绝编译。"""
        nodes = _base_active_nodes(
            join_extra="      wait_policy: wait_n_of_m\n      wait_count: 3"
        )
        with pytest.raises(WorkflowValidationError) as exc_info:
            await _compile_yaml(_fanout_doc(nodes, _base_active_edges(), _BASE_OUTPUTS))
        assert exc_info.value.code == WF_VALIDATION_FAILED
        assert exc_info.value.details["rule"] == "wait_count_over_branches"

    async def test_explicit_wait_on_inactive_shape_rejected(self):
        """用例 6d：非活跃形状 + 显式等待语义（wait_any）→ 拒绝编译（宁拒勿骗）。"""
        nodes = _fork_ambiguous_nodes(join_extra="      wait_policy: wait_any")
        with pytest.raises(WorkflowValidationError) as exc_info:
            await _compile_yaml(
                _fanout_doc(nodes, _fork_ambiguous_edges(), _BASE_OUTPUTS)
            )
        assert exc_info.value.code == WF_VALIDATION_FAILED
        assert exc_info.value.details["rule"] == "explicit_wait_requires_strict_fanout_shape"

    async def test_wait_n_of_m_valid_region_active(self):
        """用例 6e：wait_n_of_m + 合法 wait_count → 激活区域携带等待策略。"""
        nodes = _base_active_nodes(
            join_extra="      wait_policy: wait_n_of_m\n      wait_count: 1"
        )
        compiled = await _compile_yaml(
            _fanout_doc(nodes, _base_active_edges(), _BASE_OUTPUTS)
        )
        region = compiled.parallel_regions["join"]
        assert region.wait_policy == "wait_n_of_m"
        assert region.wait_count == 1

    async def test_sibling_output_name_collision_rejected(self):
        """用例 7：兄弟分支有效输出重名 → 拒绝编译（状态合并覆盖竞态）。"""
        nodes = [
            _trigger_block(),
            _agent_block("fork", "fork_out", references=["query"]),
            _agent_block("b1", "same_out", references=["fork_out"]),
            _agent_block("b2", "same_out", references=["fork_out"]),
            _join_block(["b1", "b2"]),
            _agent_block("after_join", "merged", references=["same_out"]),
        ]
        edges = [
            _edge("e0", "trigger_start", "fork"),
            _edge("e1", "fork", "b1"),
            _edge("e2", "fork", "b2"),
            _edge("e3", "b1", "join"),
            _edge("e4", "b2", "join"),
            _edge("e5", "join", "after_join"),
        ]
        with pytest.raises(WorkflowValidationError) as exc_info:
            await _compile_yaml(
                _fanout_doc(nodes, edges, ["same_out", "merged"])
            )
        assert exc_info.value.code == WF_VALIDATION_FAILED
        assert exc_info.value.details["rule"] == "sibling_output_name_collision"
        assert exc_info.value.details["output_name"] == "same_out"

    async def test_sibling_branch_reference_rejected(self):
        """用例 8：兄弟分支交叉引用（多拥有者歧义：fork 与 b1 同名 shared）→ 拒绝编译。

        既有规则 4「任一拥有者上游可达即通过」对本形态宽松放行（fork 为上游），
        本用例锚定 B6 更严口径：任一拥有者属兄弟分支即拒绝。
        """
        nodes = [
            _trigger_block(),
            _agent_block("fork", "shared", references=["query"]),
            _agent_block("b1", "shared", references=["query"]),
            _agent_block("b2", "b2_out", references=["shared"]),
            _join_block(["b1", "b2"]),
            _agent_block("after_join", "merged", references=["b2_out"]),
        ]
        edges = [
            _edge("e0", "trigger_start", "fork"),
            _edge("e1", "fork", "b1"),
            _edge("e2", "fork", "b2"),
            _edge("e3", "b1", "join"),
            _edge("e4", "b2", "join"),
            _edge("e5", "join", "after_join"),
        ]
        with pytest.raises(WorkflowValidationError) as exc_info:
            await _compile_yaml(
                _fanout_doc(nodes, edges, ["shared", "b2_out", "merged"])
            )
        assert exc_info.value.code == WF_VALIDATION_FAILED
        assert exc_info.value.details["rule"] == "sibling_branch_reference"
        assert exc_info.value.details["referencing_node"] == "b2"
        assert exc_info.value.details["referenced_node"] == "b1"

    async def test_example_serialized_form_inactive_zero_error(self, caplog):
        """用例 9：既有范例串行化形态 → 零错误、零激活（回归锚点，零修改守护）。"""
        example_path = (
            Path(__file__).resolve().parents[2]
            / "examples"
            / "parallel_execution"
            / "parallel_execution.yaml"
        )
        definition = YamlWorkflowParser().parse(
            example_path.read_text(encoding="utf-8")
        )
        with caplog.at_level(
            logging.WARNING, logger="orchestration_platform.engine.graph_compiler"
        ):
            compiled = await GraphCompiler().compile(definition)
        assert compiled.parallel_regions == {}
        assert len(_fanout_warning_records(caplog)) == 1

    async def test_join_without_branches_no_region_no_warning(self, caplog):
        """用例 10：未声明 parallel_branches 的汇聚 → 不参与识别、无警告。"""
        nodes = [
            _trigger_block(),
            _join_block([]),
            _agent_block("after_join", "merged"),
        ]
        edges = [
            _edge("e0", "trigger_start", "join"),
            _edge("e1", "join", "after_join"),
        ]
        with caplog.at_level(
            logging.WARNING, logger="orchestration_platform.engine.graph_compiler"
        ):
            compiled = await _compile_yaml(_fanout_doc(nodes, edges, ["merged"]))
        assert compiled.parallel_regions == {}
        assert _fanout_warning_records(caplog) == []
