# =============================================================================
# 语义 diff 引擎（workflow/semantic_diff/engine.py）
#
# 职责（arch_review_v2 6.1，CHG-16；卡 A8/A9；DC-12）：
#   消费两侧 CompiledWorkflow 编译产物（非 YAML 文本，评审已否定文本行 diff），
#   按锁定顺序完成三类判定并产出 SemanticDiffReport。
#
# 判定顺序锁定（DC-12）：拓扑 > 参数 > 布局——同一对输入只归一类：
#   ① topology-change：边集合差异（新增/删除/目标重定向 source 同 target 异）、
#      条件文本差异（source+target+condition 三元组比对）、节点增删、入口变更；
#      条件分支节点以 conditional_edges 元数据为权威迁移签名，
#      其声明边仅承担画布/校验定位，不参与拓扑比对；
#   ② param-change：拓扑签名一致，但节点 configuration dict 深比对不一致
#      （忽略键序），逐节点列差异键；
#   ③ layout-change：拓扑与参数均一致，仅声明顺序或画布位置元数据变化
#      → 汇总一条「仅布局变化，语义等价」；
#   ④ 全部一致 → 空变更（语义等价）。
#
# 确定性：变更条目按 入口 → 删除节点 → 新增节点 → 迁移差异（source 排序）
# 的固定顺序产出；集合比对前先排序，同输入必得同输出。
# =============================================================================

from typing import Dict, List, Optional, Set, Tuple

from orchestration_platform.engine.abstractions.compiled_workflow import CompiledWorkflow
from orchestration_platform.workflow.semantic_diff.models import (
    DiffChange,
    DiffChangeKind,
    SemanticDiffReport,
)

# 迁移三元组的尾部二元组：(目标节点, 条件文本)；source 作为映射键。
TransitionTriple = Tuple[str, Optional[str]]


def _format_conditions(conditions: List[Optional[str]]) -> str:
    """条件文本清单格式化：「a」；「b」；缺条件文本记「无条件」。"""
    return "；".join(f"「{condition if condition else '无条件'}」" for condition in conditions)


def _with_condition(prefix: str, condition: Optional[str]) -> str:
    """边明细附加条件文本（仅条件非空时）。"""
    if condition:
        return f"{prefix}（条件：「{condition}」）"
    return prefix


class SemanticDiffEngine:
    """语义 diff 引擎：消费两侧 CompiledWorkflow，产出三类变更报告。"""

    def diff(self, base: CompiledWorkflow, head: CompiledWorkflow) -> SemanticDiffReport:
        """三段判定（顺序锁定 拓扑 > 参数 > 布局，DC-12）。

        同一对输入只归一类：先拓扑、再参数、后布局；全部一致输出空变更。
        """
        topology_changes = self._diff_topology(base, head)
        if topology_changes:
            return SemanticDiffReport(
                changes=topology_changes,
                summary=f"检测到拓扑变更 {len(topology_changes)} 处（topology-change）",
            )

        param_changes = self._diff_params(base, head)
        if param_changes:
            return SemanticDiffReport(
                changes=param_changes,
                summary=f"检测到参数变更 {len(param_changes)} 处（param-change）",
            )

        if self._layout_differs(base, head):
            return SemanticDiffReport(
                changes=[
                    DiffChange(
                        kind=DiffChangeKind.LAYOUT_CHANGE,
                        target=head.definition_id,
                        detail="仅布局变化，语义等价",
                    )
                ],
                summary="检测到布局变更 1 处（layout-change），语义等价",
            )

        return SemanticDiffReport(changes=[], summary="无三类变更，语义等价")

    # ------------------------------------------------------------------
    # 第一段：拓扑变更（边集合 / 节点集合 / 入口）
    # ------------------------------------------------------------------
    def _diff_topology(
        self, base: CompiledWorkflow, head: CompiledWorkflow
    ) -> List[DiffChange]:
        """拓扑段判定：入口变更 → 节点增删 → 迁移差异（确定性顺序）。"""
        changes: List[DiffChange] = []

        # 入口变更
        if base.entry_node_id != head.entry_node_id:
            changes.append(
                DiffChange(
                    kind=DiffChangeKind.TOPOLOGY_CHANGE,
                    target="入口节点",
                    detail=(
                        f"入口由 {base.entry_node_id} 变更为 {head.entry_node_id}"
                    ),
                )
            )

        base_nodes = {node.node_id: node for node in base.nodes}
        head_nodes = {node.node_id: node for node in head.nodes}

        # 节点增删（按 node_id 排序保证确定性）
        for node_id in sorted(set(base_nodes) - set(head_nodes)):
            changes.append(
                DiffChange(
                    kind=DiffChangeKind.TOPOLOGY_CHANGE,
                    target=node_id,
                    detail=f"删除节点（类型：{base_nodes[node_id].node_type.value}）",
                )
            )
        for node_id in sorted(set(head_nodes) - set(base_nodes)):
            changes.append(
                DiffChange(
                    kind=DiffChangeKind.TOPOLOGY_CHANGE,
                    target=node_id,
                    detail=f"新增节点（类型：{head_nodes[node_id].node_type.value}）",
                )
            )

        # 迁移差异（声明边 + 条件边元数据，按 source 归并）
        base_transitions = self._transition_map(base)
        head_transitions = self._transition_map(head)
        for source in sorted(set(base_transitions) | set(head_transitions)):
            changes.extend(
                self._diff_source_transitions(
                    source,
                    base_transitions.get(source, set()),
                    head_transitions.get(source, set()),
                )
            )
        return changes

    def _transition_map(
        self, compiled: CompiledWorkflow
    ) -> Dict[str, Set[TransitionTriple]]:
        """迁移签名：source -> {(target, 条件文本)} 集合。

        条件分支节点（conditional_edges 的键）以条件边元数据为权威
        （source+target+condition 文本三元组比对）；其余节点取声明边。
        分支节点的声明边仅承担画布/校验定位，不参与拓扑签名。
        """
        transitions: Dict[str, Set[TransitionTriple]] = {}
        for source_node_id, metas in compiled.conditional_edges.items():
            transitions[source_node_id] = {
                (meta.target_node_id, meta.expression) for meta in metas
            }
        for edge in compiled.edges:
            if edge.source_node_id in compiled.conditional_edges:
                continue
            transitions.setdefault(edge.source_node_id, set()).add(
                (edge.target_node_id, edge.condition)
            )
        return transitions

    def _diff_source_transitions(
        self,
        source: str,
        base_triples: Set[TransitionTriple],
        head_triples: Set[TransitionTriple],
    ) -> List[DiffChange]:
        """单 source 的迁移差异：条件文本变更 → 目标重定向 → 边增删。"""
        if base_triples == head_triples:
            return []

        changes: List[DiffChange] = []
        base_targets = {target for target, _ in base_triples}
        head_targets = {target for target, _ in head_triples}
        common_targets = base_targets & head_targets

        # 条件文本变更：同一 (source, target) 两侧均有迁移但条件文本不同
        for target in sorted(common_targets):
            base_conditions = sorted(
                (condition for item, condition in base_triples if item == target),
                key=lambda condition: condition or "",
            )
            head_conditions = sorted(
                (condition for item, condition in head_triples if item == target),
                key=lambda condition: condition or "",
            )
            if base_conditions == head_conditions:
                continue
            changes.append(
                DiffChange(
                    kind=DiffChangeKind.TOPOLOGY_CHANGE,
                    target=f"{source} -> {target}",
                    detail=(
                        f"条件文本变更：{_format_conditions(base_conditions)} → "
                        f"{_format_conditions(head_conditions)}"
                    ),
                )
            )

        # 剩余差异（目标节点在两侧不重叠的部分），排序保证确定性
        removed = sorted(
            (
                triple
                for triple in (base_triples - head_triples)
                if triple[0] not in common_targets
            ),
            key=lambda triple: (triple[0], triple[1] or ""),
        )
        added = sorted(
            (
                triple
                for triple in (head_triples - base_triples)
                if triple[0] not in common_targets
            ),
            key=lambda triple: (triple[0], triple[1] or ""),
        )

        # 目标重定向：同 source 恰一旧目标移除且恰一新目标新增（一一对应）
        if len(removed) == 1 and len(added) == 1:
            old_target = removed[0][0]
            new_target = added[0][0]
            changes.append(
                DiffChange(
                    kind=DiffChangeKind.TOPOLOGY_CHANGE,
                    target=f"{source} -> {new_target}",
                    detail=(
                        f"边目标重定向：{source} -> {old_target} "
                        f"改为 {source} -> {new_target}"
                    ),
                )
            )
            return changes

        # 边删除 / 边新增
        for target, condition in removed:
            changes.append(
                DiffChange(
                    kind=DiffChangeKind.TOPOLOGY_CHANGE,
                    target=f"{source} -> {target}",
                    detail=_with_condition("删除边", condition),
                )
            )
        for target, condition in added:
            changes.append(
                DiffChange(
                    kind=DiffChangeKind.TOPOLOGY_CHANGE,
                    target=f"{source} -> {target}",
                    detail=_with_condition("新增边", condition),
                )
            )
        return changes

    # ------------------------------------------------------------------
    # 第二段：参数变更（configuration dict 深比对，忽略键序）
    # ------------------------------------------------------------------
    def _diff_params(
        self, base: CompiledWorkflow, head: CompiledWorkflow
    ) -> List[DiffChange]:
        """参数段判定：逐节点 configuration 深比对，列差异键。

        仅拓扑段无变更时被调用（节点集合两侧一致）；dict 相等比较
        天然忽略键序。configuration 之外的节点级字段（名称/超时/重试/
        输出命名）不参与本段，按锁定算法落布局桶。
        """
        head_nodes = {node.node_id: node for node in head.nodes}
        changes: List[DiffChange] = []
        for node in sorted(base.nodes, key=lambda item: item.node_id):
            counterpart = head_nodes.get(node.node_id)
            if counterpart is None:
                continue  # 节点增删已在拓扑段裁决，此处防御性跳过
            base_config = node.configuration.model_dump(mode="json")
            head_config = counterpart.configuration.model_dump(mode="json")
            if base_config == head_config:
                continue
            diff_keys = [
                key
                for key in sorted(set(base_config) | set(head_config))
                if base_config.get(key) != head_config.get(key)
            ]
            changes.append(
                DiffChange(
                    kind=DiffChangeKind.PARAM_CHANGE,
                    target=node.node_id,
                    detail=f"配置差异键：{'、'.join(diff_keys)}",
                )
            )
        return changes

    # ------------------------------------------------------------------
    # 第三段：布局变更（声明顺序 / 画布位置元数据）
    # ------------------------------------------------------------------
    def _layout_differs(
        self, base: CompiledWorkflow, head: CompiledWorkflow
    ) -> bool:
        """布局段判定：拓扑与参数均一致时，比较布局签名。"""
        return self._layout_signature(base) != self._layout_signature(head)

    @staticmethod
    def _layout_signature(compiled: CompiledWorkflow) -> tuple:
        """布局签名：拓扑序 + 节点画布元数据 + 声明边序列。

        覆盖：node_order（声明顺序经拓扑排序平局打破后的可见形态）、
        节点名称（画布展示）、超时/重试/输出命名（configuration 之外的
        节点级注释字段）、边序列（edge_id 供画布定位 + 声明顺序 + 条件文本）。
        """
        node_meta = tuple(
            (
                node.node_id,
                node.name,
                node.timeout_seconds,
                (
                    node.retry_policy.model_dump(mode="json")
                    if node.retry_policy
                    else None
                ),
                node.output_name,
            )
            for node in compiled.nodes
        )
        edge_sequence = tuple(
            (edge.edge_id, edge.source_node_id, edge.target_node_id, edge.condition)
            for edge in compiled.edges
        )
        return (tuple(compiled.node_order), node_meta, edge_sequence)
