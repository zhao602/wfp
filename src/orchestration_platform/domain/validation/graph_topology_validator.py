# =============================================================================
# 图拓扑校验器（domain/validation/graph_topology_validator.py）
#
# 职责：校验工作流定义的图拓扑性质——节点标识唯一性、连线引用合法性、
#      入口唯一性、可达性、无环检测。
# 设计意图（对应需求规格 5.1.1.2~5.1.1.5、5.1.3.3）：
#   1. 节点标识唯一性：重复时返回重复标识清单（5.1.1.2）；
#   2. 连线引用合法性：起点/终点必须存在于节点列表，指明具体连线（5.1.1.3a）；
#   3. 入口唯一性：有且仅有一个触发器节点（5.1.1.4）；
#   4. 可达性：正向 BFS 找从入口不可达的节点 + 反向 BFS 找无法到达终点的节点（5.1.1.5）；
#   5. 无环检测：DFS 三色标记，发现回边即记录环路径（5.1.1.3b）。
#   全部问题一次性收集返回，不因首错终止（5.1.1.7）。
# =============================================================================

from collections import deque
from typing import Dict, List, Set, Tuple

from orchestration_platform.domain.errors import WF_CYCLIC_GRAPH, WF_VALIDATION_FAILED
from orchestration_platform.domain.models.common import ValidationIssue
from orchestration_platform.domain.models.enums import ControlFlowKind, NodeType, Severity
from orchestration_platform.domain.models.workflow_definition import WorkflowDefinition
from orchestration_platform.domain.validation.node_configuration_validator import (
    build_output_owner_index,
    collect_input_references,
)
from orchestration_platform.domain.values.reference_expression import parse_reference

# DFS 三色标记常量（不用枚举以减少开销，内部实现细节）
_WHITE = 0  # 未访问
_GRAY = 1   # 在当前 DFS 路径上（正在递归处理中）
_BLACK = 2  # 已完全处理


class GraphTopologyValidator:
    """图拓扑校验器：唯一性 / 引用合法性 / 入口唯一性 / 可达性 / 无环。"""

    def validate(self, definition: WorkflowDefinition) -> List[ValidationIssue]:
        """校验图拓扑性质，返回问题清单（空列表表示通过）。"""
        issues: List[ValidationIssue] = []

        node_ids = [node.node_id for node in definition.nodes]
        node_id_set: Set[str] = set(node_ids)

        # 1. 节点标识唯一性
        issues.extend(self._check_node_id_uniqueness(node_ids))

        # 2. 连线引用合法性
        issues.extend(self._check_edge_references(definition, node_id_set))

        # 3. 入口唯一性（触发器节点有且仅有一个）
        trigger_ids = self._check_entry_uniqueness(definition)
        issues.extend(trigger_ids[1])
        entry_node_id = trigger_ids[0]

        # 4. 可达性（仅在入口存在且唯一时有意义）
        if entry_node_id is not None:
            issues.extend(self._check_reachability(definition, entry_node_id, node_id_set))

        # 5. 无环检测（DFS 三色标记）
        issues.extend(self._check_acyclic(definition, node_id_set))

        return issues

    # ------------------------------------------------------------------
    # 1. 节点标识唯一性
    # ------------------------------------------------------------------
    def _check_node_id_uniqueness(self, node_ids: List[str]) -> List[ValidationIssue]:
        """检测重复节点标识，返回包含重复标识清单的校验问题。"""
        seen: Dict[str, int] = {}
        duplicates: List[str] = []
        for node_id in node_ids:
            seen[node_id] = seen.get(node_id, 0) + 1
            if seen[node_id] == 2:
                duplicates.append(node_id)

        if not duplicates:
            return []

        return [
            ValidationIssue(
                code=WF_VALIDATION_FAILED,
                severity=Severity.ERROR,
                message=f"节点标识重复：{', '.join(duplicates)}",
                node_id=duplicates[0],
            )
        ]

    # ------------------------------------------------------------------
    # 2. 连线引用合法性
    # ------------------------------------------------------------------
    def _check_edge_references(
        self, definition: WorkflowDefinition, node_id_set: Set[str]
    ) -> List[ValidationIssue]:
        """校验每条连线的起点和终点是否都存在于节点列表中。"""
        issues: List[ValidationIssue] = []
        for edge in definition.edges:
            if edge.source_node_id not in node_id_set:
                issues.append(
                    ValidationIssue(
                        code=WF_VALIDATION_FAILED,
                        severity=Severity.ERROR,
                        message=f"连线 {edge.edge_id} 的起点 {edge.source_node_id} 不存在于节点列表",
                        edge_id=edge.edge_id,
                    )
                )
            if edge.target_node_id not in node_id_set:
                issues.append(
                    ValidationIssue(
                        code=WF_VALIDATION_FAILED,
                        severity=Severity.ERROR,
                        message=f"连线 {edge.edge_id} 的终点 {edge.target_node_id} 不存在于节点列表",
                        edge_id=edge.edge_id,
                    )
                )
        return issues

    # ------------------------------------------------------------------
    # 3. 入口唯一性
    # ------------------------------------------------------------------
    def _check_entry_uniqueness(
        self, definition: WorkflowDefinition
    ) -> tuple:
        """校验触发器节点有且仅有一个。

        返回：(入口节点标识或 None, 问题列表)
        """
        issues: List[ValidationIssue] = []
        triggers = definition.trigger_nodes()

        if len(triggers) == 0:
            issues.append(
                ValidationIssue(
                    code=WF_VALIDATION_FAILED,
                    severity=Severity.ERROR,
                    message="缺少入口节点：工作流必须包含且仅包含一个触发器节点",
                )
            )
            return (None, issues)

        if len(triggers) > 1:
            trigger_ids = [t.node_id for t in triggers]
            issues.append(
                ValidationIssue(
                    code=WF_VALIDATION_FAILED,
                    severity=Severity.ERROR,
                    message=f"入口节点不唯一：检测到 {len(triggers)} 个触发器节点（{', '.join(trigger_ids)}），应当有且仅有一个",
                    node_id=trigger_ids[0],
                )
            )
            return (None, issues)

        return (triggers[0].node_id, issues)

    # ------------------------------------------------------------------
    # 4. 可达性（正向 BFS + 反向 BFS）
    # ------------------------------------------------------------------
    def _check_reachability(
        self, definition: WorkflowDefinition, entry_node_id: str, node_id_set: Set[str]
    ) -> List[ValidationIssue]:
        """可达性校验：
        - 正向 BFS 从入口出发，找出不可达节点；
        - 反向 BFS 从终点出发，找出无法到达任何终点的节点。
        合并输出。
        """
        issues: List[ValidationIssue] = []

        # 构建邻接表与反向邻接表
        adjacency: Dict[str, List[str]] = {nid: [] for nid in node_id_set}
        reverse_adjacency: Dict[str, List[str]] = {nid: [] for nid in node_id_set}
        for edge in definition.edges:
            if edge.source_node_id in node_id_set and edge.target_node_id in node_id_set:
                adjacency[edge.source_node_id].append(edge.target_node_id)
                reverse_adjacency[edge.target_node_id].append(edge.source_node_id)

        # 正向 BFS：从入口可达的节点集合
        forward_reachable = self._bfs(adjacency, [entry_node_id])
        unreachable_from_entry = node_id_set - forward_reachable
        if unreachable_from_entry:
            issues.append(
                ValidationIssue(
                    code=WF_VALIDATION_FAILED,
                    severity=Severity.ERROR,
                    message=f"存在从入口不可达的节点：{', '.join(sorted(unreachable_from_entry))}",
                )
            )

        # 反向 BFS：从终点（无出边节点）反向可达的节点集合
        terminal_ids = definition.terminal_node_ids()
        if terminal_ids:
            backward_reachable = self._bfs(reverse_adjacency, terminal_ids)
            cannot_reach_terminal = node_id_set - backward_reachable
            if cannot_reach_terminal:
                issues.append(
                    ValidationIssue(
                        code=WF_VALIDATION_FAILED,
                        severity=Severity.ERROR,
                        message=f"存在无法到达任何终点的节点：{', '.join(sorted(cannot_reach_terminal))}",
                    )
                )

        return issues

    def _bfs(self, adjacency: Dict[str, List[str]], start_ids: List[str]) -> Set[str]:
        """从给定起始节点集合做 BFS，返回可达节点集合。"""
        visited: Set[str] = set()
        queue = deque(start_ids)
        visited.update(start_ids)
        while queue:
            current = queue.popleft()
            for neighbor in adjacency.get(current, []):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)
        return visited

    # ------------------------------------------------------------------
    # 5. 无环检测（DFS 三色标记）
    # ------------------------------------------------------------------
    def _check_acyclic(
        self, definition: WorkflowDefinition, node_id_set: Set[str]
    ) -> List[ValidationIssue]:
        """DFS 三色标记检测非法环。

        发现回边（指向 GRAY 节点的边）时，从该 GRAY 节点到当前节点
        的路径即为一个环，记录环路径节点序列。
        循环语义必须由循环控制流节点表达：若环路径中包含循环节点（control_flow
        且 control_flow_kind=loop），则该环合法（由循环节点内部驱动）；否则非法。

        邻接表为「连线边 + 输入引用依赖边（owner → referencer）」合并图
        （unified_node_input T4.3，FR-16 / DC-08）：引用边提取复用
        NodeConfigurationValidator 的引用提取登记表（T4.1 收集器）与
        命名输出拥有者索引；自引用边（owner == referencer）不纳入——
        自引用由配置校验器规则 3 报告，避免同一缺陷以「环」误述。
        形态 B 引用边（upstream_input_ref T3.3 / DC-05）：scope_node_id
        （来源节点）→ 引用节点，与 owner → referencer 同向同构；自引用边
        同样不纳入（由配置校验器规则 3′ 报告）；合法回边豁免零改动。
        """
        issues: List[ValidationIssue] = []

        # 构建邻接表：连线边
        adjacency: Dict[str, List[str]] = {nid: [] for nid in node_id_set}
        for edge in definition.edges:
            if edge.source_node_id in node_id_set and edge.target_node_id in node_id_set:
                adjacency[edge.source_node_id].append(edge.target_node_id)

        # 引用依赖边（owner → referencer）；(src, dst) → 引用定位描述（环消息用）
        reference_arcs: Dict[Tuple[str, str], List[str]] = {}
        owner_index = build_output_owner_index(definition)
        for node in definition.nodes:
            for field_path, reference in collect_input_references(node):
                parsed = parse_reference(reference)
                if parsed is None:
                    continue  # 引用语法非法由配置校验器规则 1 报告
                if parsed.kind == "input_position":
                    # 形态 B 引用依赖边（upstream_input_ref T3.3 / DC-05）：
                    # scope_node_id（来源节点）→ 引用节点，与既有
                    # owner → referencer 同向同构；自引用边不纳入
                    # （由配置校验器规则 3′ 报告，避免同一缺陷以「环」误述）
                    scope_node_id = parsed.output_name
                    if (
                        scope_node_id in node_id_set
                        and node.node_id in node_id_set
                        and scope_node_id != node.node_id
                    ):
                        adjacency[scope_node_id].append(node.node_id)
                        reference_arcs.setdefault(
                            (scope_node_id, node.node_id), []
                        ).append(f"{node.node_id} 的 {field_path} -> {reference}")
                    continue
                for owner_id in owner_index.get(parsed.output_name, []):
                    if (
                        owner_id in node_id_set
                        and node.node_id in node_id_set
                        and owner_id != node.node_id
                    ):
                        adjacency[owner_id].append(node.node_id)
                        reference_arcs.setdefault(
                            (owner_id, node.node_id), []
                        ).append(f"{node.node_id} 的 {field_path} -> {reference}")

        # 标识循环节点集合：环路径中包含这些节点的环为合法环
        loop_node_ids: Set[str] = set()
        for node in definition.nodes:
            if node.node_type == NodeType.CONTROL_FLOW:
                config = node.configuration
                if getattr(config, "control_flow_kind", None) == ControlFlowKind.LOOP:
                    loop_node_ids.add(node.node_id)

        color: Dict[str, int] = {nid: _WHITE for nid in node_id_set}

        # 按节点声明顺序迭代起点，保证环检测路径与报告顺序确定可复现（DC-17）
        for start_node in [n.node_id for n in definition.nodes]:
            if color[start_node] == _WHITE:
                cycle = self._dfs_find_cycle(
                    start_node, adjacency, color, loop_node_ids
                )
                if cycle:
                    # _dfs_find_cycle 仅返回不含循环节点的非法环
                    arc_references = self._collect_cycle_reference_arcs(
                        cycle, reference_arcs
                    )
                    if arc_references:
                        # 引用环（spec §9.4.3）：「由输入引用构成」前缀 +
                        # 环上节点序列与引用序列
                        issues.append(
                            ValidationIssue(
                                code=WF_CYCLIC_GRAPH,
                                severity=Severity.ERROR,
                                message=(
                                    f"由输入引用构成：检测到非法环："
                                    f"{' -> '.join(cycle)} -> {cycle[0]}"
                                    f"（引用：{'；'.join(arc_references)}）"
                                ),
                                node_id=cycle[0],
                            )
                        )
                    else:
                        issues.append(
                            ValidationIssue(
                                code=WF_CYCLIC_GRAPH,
                                severity=Severity.ERROR,
                                message=f"检测到非法环：{' -> '.join(cycle)} -> {cycle[0]}",
                                node_id=cycle[0],
                            )
                        )
                    # 发现一个非法环即可终止
                    break

        return issues

    @staticmethod
    def _collect_cycle_reference_arcs(
        cycle: List[str],
        reference_arcs: Dict[Tuple[str, str], List[str]],
    ) -> List[str]:
        """收集非法环上各弧的引用依赖描述（环节点序列 + 引用序列，spec §9.4.3）。

        环的弧 = 相邻节点对 (cycle[i] → cycle[i+1])，含闭合弧 (cycle[-1] → cycle[0])。
        """
        arc_references: List[str] = []
        for index, source_id in enumerate(cycle):
            target_id = cycle[(index + 1) % len(cycle)]
            for description in reference_arcs.get((source_id, target_id), []):
                if description not in arc_references:
                    arc_references.append(description)
        return arc_references

    def _dfs_find_cycle(
        self,
        start: str,
        adjacency: Dict[str, List[str]],
        color: Dict[str, int],
        loop_node_ids: Set[str],
    ) -> List[str]:
        """从 start 开始 DFS，发现非法环时返回环路径节点列表，无非法环返回空列表。

        合法环（环路径中包含循环节点）不返回——跳过该回边继续探索，
        确保所有节点被正确标记为 BLACK，避免残留 GRAY 节点导致后续 DFS 误判。
        """
        # parent 用于回溯环路径——每个 DFS 起点独立创建，避免跨起点污染（DC-16）
        parent: Dict[str, str] = {}
        # 用显式栈模拟递归，避免深图递归溢出
        stack: List[tuple] = [(start, iter(adjacency[start]))]
        color[start] = _GRAY

        while stack:
            current, neighbors_iter = stack[-1]
            next_neighbor = next(neighbors_iter, None)

            if next_neighbor is None:
                # 当前节点所有邻居已处理完毕
                color[current] = _BLACK
                stack.pop()
                continue

            if color[next_neighbor] == _GRAY:
                # 发现回边：next_neighbor 在当前 DFS 路径上 → 存在环
                # 回溯从 next_neighbor 到 current 的路径
                cycle_path = [next_neighbor]
                node = current
                while node != next_neighbor:
                    cycle_path.append(node)
                    node = parent.get(node, next_neighbor)
                cycle_path.reverse()

                # 环路径中包含循环节点 → 合法环，跳过此回边继续探索
                if loop_node_ids.intersection(cycle_path):
                    continue

                # 非法环（不含循环节点）→ 返回环路径
                return cycle_path

            if color[next_neighbor] == _WHITE:
                color[next_neighbor] = _GRAY
                parent[next_neighbor] = current
                stack.append((next_neighbor, iter(adjacency[next_neighbor])))

        return []
