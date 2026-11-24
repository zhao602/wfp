# =============================================================================
# LangGraph 状态映射层（engine/adapter/langgraph_state_mapping.py）
#
# 职责：完成 SharedState 与 LangGraph 框架状态字典的双向转换。
# 设计意图（对应 design 2.5.2、spec 4.4.3）：
#   1. 映射层不感知节点语义——仅做字段级搬运，不做业务逻辑；
#   2. 框架状态字典以 SharedState 字段为键，保证双向转换可逆；
#   3. 此文件是唯一允许感知 SharedState 内部结构与 LangGraph 状态格式的模块。
# =============================================================================

from typing import Any, Dict

from orchestration_platform.domain.models.shared_state import SharedState

# 框架状态字典的键名常量（与 SharedState 字段一一对应）
_KEY_INSTANCE_ID = "instance_id"
_KEY_NODE_OUTPUTS = "node_outputs"
_KEY_LOOP_COUNTERS = "loop_counters"
_KEY_BRANCH_SELECTIONS = "branch_selections"
_KEY_CURRENT_NODE_ID = "current_node_id"
_KEY_ROUND_INDEX = "round_index"
# 瞬态路由键（arch_review_gap_b4_architecture §4.4，DC-B4-02）：
# 节点函数决策结果的载体，供 add_conditional_edges 路由读取；
# 仅存在于框架状态字典内——from_framework_state 不读取（瞬态不出 adapter）。
_KEY_NEXT_NODE_ID = "next_node_id"


class LangGraphStateMapping:
    """SharedState ↔ LangGraph 框架状态字典 双向转换器。"""

    @staticmethod
    def to_framework_state(shared_state: SharedState) -> Dict[str, Any]:
        """将 SharedState 转换为 LangGraph 框架状态字典。

        转换规则：字段一一对应，不做语义变换。
        """
        return {
            _KEY_INSTANCE_ID: shared_state.instance_id,
            _KEY_NODE_OUTPUTS: dict(shared_state.node_outputs),
            _KEY_LOOP_COUNTERS: dict(shared_state.loop_counters),
            _KEY_BRANCH_SELECTIONS: dict(shared_state.branch_selections),
            _KEY_CURRENT_NODE_ID: shared_state.current_node_id,
            _KEY_ROUND_INDEX: shared_state.round_index,
        }

    @staticmethod
    def from_framework_state(state_dict: Dict[str, Any]) -> SharedState:
        """将 LangGraph 框架状态字典转换为 SharedState。

        转换规则：字段一一对应，缺失字段使用 SharedState 默认值。
        B12-D7.1：免验证构造（model_construct）——本路径为 langgraph
        每步转换热点，输入来自 to_framework_state/merge_state_update
        （上游已验证对象），消除 JsonValue 深验证的整份数据集复制。
        """
        return SharedState.model_construct(
            instance_id=state_dict.get(_KEY_INSTANCE_ID, ""),
            node_outputs=state_dict.get(_KEY_NODE_OUTPUTS, {}),
            loop_counters=state_dict.get(_KEY_LOOP_COUNTERS, {}),
            branch_selections=state_dict.get(_KEY_BRANCH_SELECTIONS, {}),
            current_node_id=state_dict.get(_KEY_CURRENT_NODE_ID),
            round_index=state_dict.get(_KEY_ROUND_INDEX, 0),
        )

    @staticmethod
    def merge_state_update(
        current_state: Dict[str, Any], update: Dict[str, Any]
    ) -> Dict[str, Any]:
        """合并状态更新到当前状态字典（LangGraph 节点返回值的合并语义）。

        设计意图：node_outputs 采用覆盖语义（新值替换旧值），
        其余字段直接覆盖。
        """
        merged = dict(current_state)
        for key, value in update.items():
            if key == _KEY_NODE_OUTPUTS and isinstance(value, dict):
                existing = merged.get(_KEY_NODE_OUTPUTS, {})
                existing.update(value)
                merged[_KEY_NODE_OUTPUTS] = existing
            else:
                merged[key] = value
        return merged
