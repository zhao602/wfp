# =============================================================================
# 图编译器实现（engine/graph_compiler.py）
#
# 职责：将 WorkflowDefinition 编译为 CompiledWorkflow 中间表示。
# 设计意图（对应 design 2.2.2.2、spec 5.2.1.1）：
#   1. 编译前校验拓扑合法性，不合法时返回校验报告中的错误；
#   2. 节点顺序固定为拓扑序（Kahn 算法），保证确定性调度；
#   3. 提取条件分支节点的出边元数据，供调度器求值。
# =============================================================================

import logging
from collections import deque
from typing import Dict, List, Optional, Set

from orchestration_platform.domain.errors import WF_VALIDATION_FAILED, WorkflowValidationError
from orchestration_platform.domain.models.common import ValidationReport
from orchestration_platform.domain.models.enums import (
    ControlFlowKind,
    JoinWaitPolicy,
    NodeType,
    Severity,
)
from orchestration_platform.domain.models.node_configurations import (
    PluginNodeConfiguration,
)
from orchestration_platform.domain.models.edge_definition import EdgeDefinition
from orchestration_platform.domain.models.node_definition import NodeDefinition
from orchestration_platform.domain.models.workflow_definition import WorkflowDefinition
from orchestration_platform.domain.validation.node_configuration_validator import (
    build_output_owner_index,
    collect_input_references,
)
from orchestration_platform.domain.validation.validation_report_builder import (
    ValidationReportBuilder,
)
from orchestration_platform.domain.values.reference_expression import parse_reference
from orchestration_platform.engine.abstractions.compiled_workflow import (
    CompiledWorkflow,
    ConditionalEdgeMeta,
    ParallelRegionMeta,
)
from orchestration_platform.engine.abstractions.engine_config import EngineSettings

_logger = logging.getLogger(__name__)


class GraphCompiler:
    """工作流定义 → CompiledWorkflow 编译器。"""

    def __init__(
        self,
        report_builder: ValidationReportBuilder | None = None,
        plugin_schema_provider=None,
        settings: Optional[EngineSettings] = None,
    ) -> None:
        """构造编译器。

        参数：
            report_builder          —— 自定义校验报告构建器
            plugin_schema_provider  —— 插件节点 schema 提供器（可选），
                                       提供 get_input_schema(node_name)/get_output_schema(node_name)；
                                       缺省为 None，跳过插件节点 schema 校验（降级形态）。
            settings                —— 引擎运行配置（M4 B10：缺省 None →
                                       EngineSettings() 默认值；宿主注入生效
                                       env 覆盖）。
        """
        self._report_builder = report_builder or ValidationReportBuilder()
        self._plugin_schema_provider = plugin_schema_provider
        self._settings = settings or EngineSettings()

    async def compile(self, definition: WorkflowDefinition) -> CompiledWorkflow:
        """将已校验的工作流定义编译为 CompiledWorkflow。

        流程：拓扑校验 → 拓扑排序 → 提取条件边元数据 → 构建中间表示。
        异常：拓扑不合法时抛出 WorkflowValidationError。
        """
        # 1. 拓扑校验
        report = self._report_builder.validate(definition)
        if not report.valid:
            error_messages = [i.message for i in report.issues if i.severity == Severity.ERROR]
            raise WorkflowValidationError(
                WF_VALIDATION_FAILED,
                f"工作流定义拓扑校验未通过：{'; '.join(error_messages)}",
                {"issues": [i.model_dump(mode="json") for i in report.issues]},
            )

        # 1.5 插件节点 schema 校验（偏差修正 T150，仅扩展不改变既有行为）
        # 当 plugin_schema_provider 提供时，校验 PLUGIN 节点的 input_mapping 引用
        # 与 manifest input_schema 一致；未提供时跳过（降级形态）。
        if self._plugin_schema_provider is not None:
            self._validate_plugin_node_schemas(definition)

        # 2. 拓扑排序（Kahn 算法，保证确定性）
        node_order = self._topological_sort(definition)

        # 3. 按拓扑序重排节点
        node_index = definition.node_index()
        sorted_nodes = [node_index[nid] for nid in node_order]

        # 4. 提取条件边元数据
        conditional_edges = self._extract_conditional_edges(definition)

        # 4.5 提取并行扇出区域元数据（B6 roadmap_b6_parallel_fanout）
        # 仅严格扇出形状激活；非活跃声明回退单游标语义 + 警告，
        # 等待字段显式声明或超上限时拒绝编译（宁拒勿骗，design §3.1）。
        parallel_regions = self._extract_parallel_regions(definition)

        # 5. 确定入口与终点
        triggers = definition.trigger_nodes()
        entry_node_id = triggers[0].node_id
        terminal_node_ids = definition.terminal_node_ids()

        return CompiledWorkflow(
            definition_id=definition.definition_id,
            definition_version=definition.definition_version,
            nodes=sorted_nodes,
            edges=definition.edges,
            entry_node_id=entry_node_id,
            terminal_node_ids=terminal_node_ids,
            conditional_edges=conditional_edges,
            node_order=node_order,
            parallel_regions=parallel_regions,
        )

    def validate_topology(self, definition: WorkflowDefinition) -> ValidationReport:
        """校验工作流定义的拓扑合法性。"""
        return self._report_builder.validate(definition)

    # ------------------------------------------------------------------
    # 拓扑排序（Kahn 算法）
    # ------------------------------------------------------------------
    def _topological_sort(self, definition: WorkflowDefinition) -> List[str]:
        """Kahn 算法拓扑排序，保证确定性。

        确定性保证：
        - 入度为 0 的节点按声明顺序处理（不使用集合迭代）；
        - 同层节点按节点在定义中的声明顺序选择。

        循环处理：循环节点（control_flow kind=loop）到 loop_body_entry 的边
        视为回边，不参与拓扑排序的入度计算，从而打破环使排序可行。
        """
        node_ids = [node.node_id for node in definition.nodes]
        node_id_set: Set[str] = set(node_ids)
        declaration_order: Dict[str, int] = {nid: idx for idx, nid in enumerate(node_ids)}

        # 标识循环节点的回边目标（loop_body_entry），这些边不参与拓扑排序
        back_edge_targets: Dict[str, Set[str]] = {}
        for node in definition.nodes:
            if node.node_type == NodeType.CONTROL_FLOW:
                config = node.configuration
                if getattr(config, "control_flow_kind", None) == ControlFlowKind.LOOP:
                    loop_body = getattr(config, "loop_body_entry", None)
                    if loop_body and loop_body in node_id_set:
                        back_edge_targets.setdefault(node.node_id, set()).add(loop_body)

        # 计算入度（跳过回边）
        in_degree: Dict[str, int] = {nid: 0 for nid in node_ids}
        adjacency: Dict[str, List[str]] = {nid: [] for nid in node_ids}
        for edge in definition.edges:
            if edge.source_node_id not in node_id_set or edge.target_node_id not in node_id_set:
                continue
            # 跳过循环节点的回边
            if edge.target_node_id in back_edge_targets.get(edge.source_node_id, set()):
                continue
            adjacency[edge.source_node_id].append(edge.target_node_id)
            in_degree[edge.target_node_id] += 1

        # 初始队列：入度为 0 的节点，按声明顺序排列
        ready = sorted(
            [nid for nid in node_ids if in_degree[nid] == 0],
            key=lambda nid: declaration_order[nid],
        )
        queue = deque(ready)

        result: List[str] = []
        while queue:
            current = queue.popleft()
            result.append(current)

            # 收集后继节点中入度降为 0 的，按声明顺序排列
            newly_ready: List[str] = []
            for neighbor in adjacency[current]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    newly_ready.append(neighbor)

            # 按声明顺序插入队列（保证确定性）
            newly_ready.sort(key=lambda nid: declaration_order[nid])
            queue.extend(newly_ready)

        return result

    # ------------------------------------------------------------------
    # 条件边元数据提取
    # ------------------------------------------------------------------
    def _extract_conditional_edges(
        self, definition: WorkflowDefinition
    ) -> Dict[str, List[ConditionalEdgeMeta]]:
        """提取条件分支节点的出边元数据。

        仅对 control_flow 且 control_flow_kind=conditional_branch 的节点
        生成条件边元数据；循环节点的出边不生成条件边（由调度器按循环语义处理）。
        """
        conditional_edges: Dict[str, List[ConditionalEdgeMeta]] = {}

        for node in definition.nodes:
            if node.node_type != NodeType.CONTROL_FLOW:
                continue
            config = node.configuration
            if getattr(config, "control_flow_kind", None) != ControlFlowKind.CONDITIONAL_BRANCH:
                continue

            # 从配置中获取分支条件列表
            conditions = getattr(config, "conditions", [])
            default_target = getattr(config, "default_branch_target", None)

            edge_list: List[ConditionalEdgeMeta] = []
            for condition in conditions:
                edge_list.append(
                    ConditionalEdgeMeta(
                        edge_id=f"cond_{node.node_id}_{condition.target_node_id}",
                        source_node_id=node.node_id,
                        target_node_id=condition.target_node_id,
                        expression=condition.expression,
                        is_default=False,
                    )
                )

            if default_target:
                edge_list.append(
                    ConditionalEdgeMeta(
                        edge_id=f"default_{node.node_id}_{default_target}",
                        source_node_id=node.node_id,
                        target_node_id=default_target,
                        expression=None,
                        is_default=True,
                    )
                )

            conditional_edges[node.node_id] = edge_list

        return conditional_edges

    # ------------------------------------------------------------------
    # 并行扇出区域提取（B6 roadmap_b6_parallel_fanout，design §3.1）
    # ------------------------------------------------------------------
    def _extract_parallel_regions(
        self, definition: WorkflowDefinition
    ) -> Dict[str, ParallelRegionMeta]:
        """识别严格并行扇出形状，产出激活区域元数据表（join_node_id 键）。

        激活判定全项满足才产出元数据（design §3.1）：
        1. 分支入口非空、去重、在图内且非控制流节点；
        2. 存在唯一分叉源 S（非控制流；每个分支入口的唯一入边来自 S；
           S 的出边目标集合 == 分支入口集合）；
        3. 每条分支路径线性（每个节点恰一条出边）、无控制流节点、终于 J；
        4. 兄弟分支路径互不相交；
        5. J 的入边源集合 == 各分支尾节点集合（严格扇出形状）。

        错误（拒绝编译，宁拒勿骗）：
        - parallel_branches 数量超过 parallel_branch_max；
        - 等待字段不自洽（wait_n_of_m 缺 wait_count / 其他策略设置 wait_count）；
        - 已激活区域：wait_count 超分支数 / 兄弟分支输出重名 / 兄弟分支交叉引用；
        - 形状不满足且显式声明了非默认等待语义（无法安全回退单游标）。

        非活跃回退：形状不满足且等待语义为缺省（wait_all、无 wait_count）时，
        不产出区域 + 警告日志，维持既有单游标语义（examples/模板零回归）。
        """
        max_branches = self._settings.parallel_branch_max
        node_index = definition.node_index()
        node_ids: Set[str] = set(node_index.keys())
        out_edges: Dict[str, List[EdgeDefinition]] = {}
        in_edges: Dict[str, List[EdgeDefinition]] = {}
        for edge in definition.edges:
            out_edges.setdefault(edge.source_node_id, []).append(edge)
            in_edges.setdefault(edge.target_node_id, []).append(edge)
        owner_index = build_output_owner_index(definition)

        regions: Dict[str, ParallelRegionMeta] = {}
        for node in definition.nodes:
            if node.node_type != NodeType.CONTROL_FLOW:
                continue
            config = node.configuration
            if getattr(config, "control_flow_kind", None) != ControlFlowKind.PARALLEL_JOIN:
                continue
            entries = list(getattr(config, "parallel_branches", None) or [])
            if not entries:
                continue  # 未声明分支入口的汇聚：不参与识别（保持既有语义）

            join_id = node.node_id
            wait_policy = getattr(config, "wait_policy", JoinWaitPolicy.WAIT_ALL)
            wait_count = getattr(config, "wait_count", None)

            # 等待字段一致性（显式声明即校验，与形状无关）
            if wait_policy == JoinWaitPolicy.WAIT_N_OF_M and wait_count is None:
                raise WorkflowValidationError(
                    WF_VALIDATION_FAILED,
                    f"并行汇聚节点 {join_id} 的 wait_policy=wait_n_of_m 须设置 wait_count",
                    {"node_id": join_id, "rule": "wait_n_of_m_requires_wait_count"},
                )
            if wait_policy != JoinWaitPolicy.WAIT_N_OF_M and wait_count is not None:
                raise WorkflowValidationError(
                    WF_VALIDATION_FAILED,
                    f"并行汇聚节点 {join_id} 仅 wait_policy=wait_n_of_m 允许设置 wait_count",
                    {"node_id": join_id, "rule": "wait_count_requires_wait_n_of_m"},
                )

            # 分支数上限（显式声明超过安全上限即拒绝）
            if len(entries) > max_branches:
                raise WorkflowValidationError(
                    WF_VALIDATION_FAILED,
                    f"并行汇聚节点 {join_id} 的 parallel_branches 数量 {len(entries)} 超过上限 {max_branches}",
                    {
                        "node_id": join_id,
                        "rule": "parallel_branches_over_limit",
                        "limit": max_branches,
                    },
                )

            # 结构激活判定
            region = self._try_activate_parallel_region(
                node, entries, node_index, node_ids, out_edges, in_edges
            )
            if region is None:
                if wait_policy != JoinWaitPolicy.WAIT_ALL or wait_count is not None:
                    raise WorkflowValidationError(
                        WF_VALIDATION_FAILED,
                        f"并行汇聚节点 {join_id} 不满足严格扇出形状，且已显式声明等待语义，无法安全回退单游标执行",
                        {
                            "node_id": join_id,
                            "rule": "explicit_wait_requires_strict_fanout_shape",
                        },
                    )
                _logger.warning(
                    "并行汇聚节点 %s 不满足严格扇出形状，未激活并行扇出，回退单游标语义",
                    join_id,
                )
                continue

            # wait_n_of_m 数值范围（激活后分支数已知）
            if wait_policy == JoinWaitPolicy.WAIT_N_OF_M and wait_count > len(entries):
                raise WorkflowValidationError(
                    WF_VALIDATION_FAILED,
                    f"并行汇聚节点 {join_id} 的 wait_count={wait_count} 超过分支数 {len(entries)}",
                    {"node_id": join_id, "rule": "wait_count_over_branches"},
                )

            # 激活区域静态冲突校验（兄弟输出重名 / 兄弟交叉引用）
            self._validate_active_region(region, node_index, owner_index)

            regions[join_id] = region
        return regions

    def _try_activate_parallel_region(
        self,
        join_node: NodeDefinition,
        entries: List[str],
        node_index: Dict[str, NodeDefinition],
        node_ids: Set[str],
        out_edges: Dict[str, List[EdgeDefinition]],
        in_edges: Dict[str, List[EdgeDefinition]],
    ) -> Optional[ParallelRegionMeta]:
        """结构激活判定（design §3.1 五项）；任一项不满足返回 None。"""
        join_id = join_node.node_id

        # 1. 分支入口：去重、在图内、非 join 自身、非控制流
        if len(set(entries)) != len(entries):
            return None
        for entry in entries:
            if entry not in node_ids or entry == join_id:
                return None
            if node_index[entry].node_type == NodeType.CONTROL_FLOW:
                return None

        # 2. 唯一分叉源 S：每个分支入口恰一条入边且源一致；
        #    S 非控制流、非 join、非分支入口；S 出边目标集合 == 分支入口集合
        fork_sources: Set[str] = set()
        for entry in entries:
            entry_in = in_edges.get(entry, [])
            if len(entry_in) != 1:
                return None
            fork_sources.add(entry_in[0].source_node_id)
        if len(fork_sources) != 1:
            return None
        fork_id = fork_sources.pop()
        if fork_id == join_id or fork_id in entries:
            return None
        fork_node = node_index.get(fork_id)
        if fork_node is None or fork_node.node_type == NodeType.CONTROL_FLOW:
            return None
        fork_targets = [edge.target_node_id for edge in out_edges.get(fork_id, [])]
        if len(fork_targets) != len(entries) or set(fork_targets) != set(entries):
            return None

        # 3. 分支路径：线性（每节点恰一条出边）、无控制流、终于 join
        branch_paths: Dict[str, List[str]] = {}
        for entry in entries:
            path = [entry]
            current = entry
            reached_join = False
            for _ in range(len(node_ids) + 1):
                outs = out_edges.get(current, [])
                if len(outs) != 1:
                    break
                next_id = outs[0].target_node_id
                if next_id == join_id:
                    reached_join = True
                    break
                if next_id not in node_ids or next_id in entries:
                    break
                if node_index[next_id].node_type == NodeType.CONTROL_FLOW:
                    break
                path.append(next_id)
                current = next_id
            if not reached_join:
                return None
            branch_paths[entry] = path

        # 4. 兄弟分支路径互不相交
        path_owner_branch: Dict[str, str] = {}
        for entry, path in branch_paths.items():
            for path_node in path:
                if path_node in path_owner_branch and path_owner_branch[path_node] != entry:
                    return None
                path_owner_branch[path_node] = entry

        # 5. join 入边源集合 == 分支尾节点集合（严格扇出形状）
        join_in_sources = {edge.source_node_id for edge in in_edges.get(join_id, [])}
        tail_nodes = {path[-1] for path in branch_paths.values()}
        if join_in_sources != tail_nodes:
            return None

        config = join_node.configuration
        return ParallelRegionMeta(
            join_node_id=join_id,
            fork_source_node_id=fork_id,
            branch_entry_ids=list(entries),
            branch_paths=branch_paths,
            wait_policy=getattr(config, "wait_policy", JoinWaitPolicy.WAIT_ALL).value,
            wait_count=getattr(config, "wait_count", None),
            failure_strategy=getattr(config, "parallel_failure_strategy").value,
        )

    def _validate_active_region(
        self,
        region: ParallelRegionMeta,
        node_index: Dict[str, NodeDefinition],
        owner_index: Dict[str, List[str]],
    ) -> None:
        """激活区域静态冲突校验（design §3.3；命中即抛 WorkflowValidationError）。

        1. 兄弟分支有效输出名冲突：并行路径内并发写同名输出存在覆盖竞态，
           兄弟间重名直接拒绝（引用消歧按「任一拥有者属兄弟分支即拒绝」从严口径）；
        2. 兄弟分支交叉引用：分支内节点引用兄弟分支节点（形态 A 命名输出 /
           形态 B 来源节点），兄弟分支间禁止数据依赖。
        """
        join_id = region.join_node_id

        # 1. 兄弟有效输出名冲突
        output_owner_branch: Dict[str, str] = {}
        for entry in region.branch_entry_ids:
            for path_node in region.branch_paths[entry]:
                output_name = node_index[path_node].effective_output_name
                if (
                    output_name in output_owner_branch
                    and output_owner_branch[output_name] != entry
                ):
                    raise WorkflowValidationError(
                        WF_VALIDATION_FAILED,
                        f"并行扇出区域 {join_id} 的兄弟分支存在重名输出 {output_name}"
                        f"（{output_owner_branch[output_name]} 与 {entry}），状态合并存在覆盖竞态",
                        {
                            "node_id": join_id,
                            "rule": "sibling_output_name_collision",
                            "output_name": output_name,
                        },
                    )
                output_owner_branch[output_name] = entry

        # 2. 兄弟分支交叉引用
        branch_of: Dict[str, str] = {}
        for entry, path in region.branch_paths.items():
            for path_node in path:
                branch_of[path_node] = entry
        for entry in region.branch_entry_ids:
            for path_node in region.branch_paths[entry]:
                for field_path, reference in collect_input_references(node_index[path_node]):
                    parsed = parse_reference(reference)
                    if parsed is None:
                        continue
                    if parsed.kind == "output":
                        owners = owner_index.get(parsed.output_name, [])
                    else:
                        owners = (
                            [parsed.output_name] if parsed.output_name in node_index else []
                        )
                    for owner in owners:
                        owner_branch = branch_of.get(owner)
                        if owner_branch is not None and owner_branch != entry:
                            raise WorkflowValidationError(
                                WF_VALIDATION_FAILED,
                                f"并行扇出区域 {join_id} 的分支 {entry} 中节点 {path_node}"
                                f"（{field_path}）引用了兄弟分支 {owner_branch} 的节点 {owner}，"
                                f"兄弟分支间禁止数据依赖",
                                {
                                    "node_id": join_id,
                                    "rule": "sibling_branch_reference",
                                    "referencing_node": path_node,
                                    "referenced_node": owner,
                                    "field_path": field_path,
                                },
                            )

    # ------------------------------------------------------------------
    # 插件节点 schema 校验（偏差修正 T150）
    # ------------------------------------------------------------------
    def _validate_plugin_node_schemas(self, definition: WorkflowDefinition) -> None:
        """校验 PLUGIN 节点的 input_mapping 引用与 manifest input_schema 一致。

        仅当 plugin_schema_provider 提供时调用；未提供时跳过（降级形态）。
        既有编译校验逻辑不变，此方法仅扩展。
        """
        for node in definition.nodes:
            if node.node_type != NodeType.PLUGIN:
                continue
            config: PluginNodeConfiguration = node.configuration
            try:
                input_schema = self._plugin_schema_provider.get_input_schema(
                    config.plugin_ref
                )
            except Exception:
                # schema 提供器无法获取时跳过该节点校验（降级）
                continue

            if not input_schema:
                continue

            # 校验 input_mapping 的参数名是否在 manifest input_schema 中声明
            for param_name in config.input_mapping:
                if param_name not in input_schema:
                    raise WorkflowValidationError(
                        WF_VALIDATION_FAILED,
                        f"插件节点 {config.plugin_ref} 的入参 {param_name} 未在 manifest input_schema 中声明",
                        {
                            "node_id": node.node_id,
                            "plugin_ref": config.plugin_ref,
                            "param_name": param_name,
                        },
                    )
