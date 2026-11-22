# =============================================================================
# 共享状态模型（domain/models/shared_state.py）
#
# 职责：定义节点之间传递数据的共享状态对象——命名输出、循环计数、分支选择、位点。
# 设计意图（对应需求规格 5.2.1.2）：
#   节点之间通过共享状态传递数据，每个节点可读取上游写入的命名输出；
#   共享状态随检查点序列化，是故障恢复与重放的状态载体。
# =============================================================================

from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from orchestration_platform.domain.models.common import JsonValue


class SharedState(BaseModel):
    """工作流实例的共享状态（随检查点序列化）。

    字段：
        instance_id       —— 所属实例标识
        node_outputs      —— 命名输出表：输出名 -> 节点输出值
        loop_counters     —— 循环计数表：循环节点标识 -> 已迭代次数
        branch_selections —— 分支选择表：分支节点标识 -> 选中目标节点标识
        current_node_id   —— 当前节点标识（位点）
        round_index       —— 执行轮次（同一实例内递增）
    """

    instance_id: str = Field(description="所属实例标识")
    node_outputs: Dict[str, JsonValue] = Field(default_factory=dict, description="命名输出表")
    loop_counters: Dict[str, int] = Field(default_factory=dict, description="循环计数表")
    branch_selections: Dict[str, str] = Field(default_factory=dict, description="分支选择表")
    current_node_id: Optional[str] = Field(default=None, description="当前节点标识（位点）")
    round_index: int = Field(default=0, ge=0, description="执行轮次")
    # ---- 第二阶段新增：增量更新 ----
    pending_diff: Dict[str, JsonValue] = Field(
        default_factory=dict, description="待提交增量（write_output 先写入此表，commit_diff 时合并）"
    )
    # ---- B6 并行扇出前沿（roadmap_b6_parallel_fanout；随检查点序列化，免 DB 迁移）----
    # 三表键均为激活区域的 join_node_id；旧检查点快照无此三字段时反序列化
    # 得空 dict（default_factory），单游标 resume 行为不变（向后兼容）。
    parallel_positions: Dict[str, Dict[str, str]] = Field(
        default_factory=dict,
        description="并行区域前沿位点：join_node_id -> {分支入口 -> 当前节点}",
    )
    parallel_arrivals: Dict[str, List[str]] = Field(
        default_factory=dict,
        description="并行区域已成功到达 join 的分支入口列表：join_node_id -> [分支入口]",
    )
    parallel_cancelled: Dict[str, List[str]] = Field(
        default_factory=dict,
        description="并行区域已取消（SKIPPED）分支入口列表：join_node_id -> [分支入口]",
    )

    def read_output(self, output_name: str) -> Optional[JsonValue]:
        """读取命名输出，不存在返回 None。"""
        return self.node_outputs.get(output_name)

    def write_output(self, output_name: str, value: JsonValue) -> None:
        """写入命名输出（覆盖同名的旧值）。"""
        self.node_outputs[output_name] = value

    def snapshot(self) -> "SharedState":
        """返回状态深拷贝快照，用于检查点持久化，避免后续修改影响历史快照。"""
        return self.model_copy(deep=True)
