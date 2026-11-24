# =============================================================================
# 既有工作流修改应用器（governance/nl_orchestration/workflow_modifier.py）
#
# 职责：在既有工作流定义基础上应用自然语言修改指令。
# 设计意图（对应偏差修正 spec 5.1.1.2 / design 模块A WorkflowModifier）：
#   - 节点插入：在指定锚点节点之后/之前插入新节点，自动重连边
#   - 节点删除：删除指定节点，将其入边重连至其出边目标
#   - 节点更新：更新指定节点的配置
#   - 引用不存在的节点名时抛出 NODE_NOT_FOUND
# =============================================================================

import copy
import logging

from orchestration_platform.domain.models.edge_definition import EdgeDefinition
from orchestration_platform.domain.models.node_definition import NodeDefinition
from orchestration_platform.domain.models.workflow_definition import WorkflowDefinition
from orchestration_platform.governance.nl_orchestration.models import (
    NodeModification,
    ParsedIntent,
)

_logger = logging.getLogger(__name__)


class WorkflowModifier:
    """既有工作流修改应用器。

    在既有 WorkflowDefinition 基础上应用 ParsedIntent 的修改操作列表，
    返回更新后的 WorkflowDefinition。
    """

    def apply_modification(
        self,
        base_definition: WorkflowDefinition,
        parsed_intent: ParsedIntent,
    ) -> WorkflowDefinition:
        """在既有定义基础上应用修改操作。

        前置条件：base_definition 存在且 parsed_intent.modifications 非空。
        后置条件：返回更新后的 WorkflowDefinition，definition_version 递增。
        异常映射：抛出 ValueError（含 NODE_NOT_FOUND 语义）当引用的节点不存在。
        """
        # 深拷贝基础定义，避免修改原对象
        definition = copy.deepcopy(base_definition)
        definition.definition_version = base_definition.definition_version + 1

        for modification in parsed_intent.modifications:
            definition = self._apply_single_modification(definition, modification)

        return definition

    def _apply_single_modification(
        self,
        definition: WorkflowDefinition,
        modification: NodeModification,
    ) -> WorkflowDefinition:
        """应用单条修改操作。"""
        if modification.action == "insert":
            return self._apply_insert(definition, modification)
        elif modification.action == "delete":
            return self._apply_delete(definition, modification)
        elif modification.action == "update":
            return self._apply_update(definition, modification)
        else:
            _logger.warning("未知修改动作: %s，跳过", modification.action)
            return definition

    def _apply_insert(
        self,
        definition: WorkflowDefinition,
        modification: NodeModification,
    ) -> WorkflowDefinition:
        """插入新节点。

        在 anchor_node_id 之后/之前插入 node_spec 指定的节点，
        自动创建边连接新节点与锚点节点。
        """
        if modification.node_spec is None:
            return definition

        anchor_id = modification.anchor_node_id
        if anchor_id is not None and anchor_id not in definition.node_index():
            raise ValueError(f"NODE_NOT_FOUND: 锚点节点 '{anchor_id}' 不存在")

        # 构造新节点
        new_node = self._build_node(modification.node_spec)
        definition.nodes.append(new_node)

        # 自动连接边
        if anchor_id is not None and modification.node_spec is not None:
            new_node_id = modification.node_spec.node_id
            position = modification.insert_position or "after"

            if position == "after":
                # 锚点 → 新节点
                edge_id = f"edge_{len(definition.edges)}"
                definition.edges.append(
                    EdgeDefinition(
                        edge_id=edge_id,
                        source_node_id=anchor_id,
                        target_node_id=new_node_id,
                    )
                )
                # 将锚点的原有出边重连至新节点
                for edge in definition.edges:
                    if edge.source_node_id == anchor_id and edge.target_node_id != new_node_id:
                        edge.source_node_id = new_node_id
            elif position == "before":
                # 新节点 → 锚点
                edge_id = f"edge_{len(definition.edges)}"
                definition.edges.append(
                    EdgeDefinition(
                        edge_id=edge_id,
                        source_node_id=new_node_id,
                        target_node_id=anchor_id,
                    )
                )
                # 将锚点的原有入边重连至新节点
                for edge in definition.edges:
                    if edge.target_node_id == anchor_id and edge.source_node_id != new_node_id:
                        edge.target_node_id = new_node_id

        # 添加伴随的额外边
        for edge_spec in modification.edge_specs:
            edge_id = f"edge_{len(definition.edges)}"
            definition.edges.append(
                EdgeDefinition(
                    edge_id=edge_id,
                    source_node_id=edge_spec.source_node_id,
                    target_node_id=edge_spec.target_node_id,
                    condition=edge_spec.condition,
                )
            )

        return definition

    def _apply_delete(
        self,
        definition: WorkflowDefinition,
        modification: NodeModification,
    ) -> WorkflowDefinition:
        """删除节点。

        删除 target_node_id 指定的节点，将其入边重连至其出边目标。
        """
        target_id = modification.target_node_id
        if target_id is None:
            return definition

        if target_id not in definition.node_index():
            raise ValueError(f"NODE_NOT_FOUND: 目标节点 '{target_id}' 不存在")

        # 收集入边源和出边目标
        in_sources = [e.source_node_id for e in definition.edges if e.target_node_id == target_id]
        out_targets = [e.target_node_id for e in definition.edges if e.source_node_id == target_id]

        # 删除节点
        definition.nodes = [n for n in definition.nodes if n.node_id != target_id]

        # 删除关联边
        definition.edges = [
            e for e in definition.edges
            if e.source_node_id != target_id and e.target_node_id != target_id
        ]

        # 重连：入边源 → 出边目标
        for src in in_sources:
            for tgt in out_targets:
                if src != tgt:
                    edge_id = f"edge_{len(definition.edges)}"
                    definition.edges.append(
                        EdgeDefinition(
                            edge_id=edge_id,
                            source_node_id=src,
                            target_node_id=tgt,
                        )
                    )

        return definition

    def _apply_update(
        self,
        definition: WorkflowDefinition,
        modification: NodeModification,
    ) -> WorkflowDefinition:
        """更新节点配置。"""
        target_id = modification.target_node_id
        if target_id is None or modification.node_spec is None:
            return definition

        node_index = definition.node_index()
        if target_id not in node_index:
            raise ValueError(f"NODE_NOT_FOUND: 目标节点 '{target_id}' 不存在")

        # 更新节点名称与配置
        existing_node = node_index[target_id]
        updated_node = self._build_node(modification.node_spec, existing_node)
        definition.nodes = [
            updated_node if n.node_id == target_id else n for n in definition.nodes
        ]

        return definition

    def _build_node(
        self,
        node_spec,
        existing_node: NodeDefinition = None,
    ) -> NodeDefinition:
        """从 ParsedNodeSpec 构造 NodeDefinition。

        若提供 existing_node 则保留其部分属性（如 timeout_seconds）。
        """
        config_dict = dict(node_spec.configuration)
        config_dict["node_type"] = node_spec.node_type

        node_data = {
            "node_id": node_spec.node_id,
            "name": node_spec.name or node_spec.node_id,
            "node_type": node_spec.node_type.value,
            "configuration": config_dict,
        }

        if existing_node is not None:
            if existing_node.timeout_seconds is not None:
                node_data["timeout_seconds"] = existing_node.timeout_seconds
            if existing_node.output_name is not None:
                node_data["output_name"] = existing_node.output_name

        return NodeDefinition.model_validate(node_data)
