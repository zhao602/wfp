# =============================================================================
# 共享状态管理器（engine/shared_state_manager.py）
#
# 职责：管理 SharedState 的读写，向节点执行器提供只读状态视图。
# 设计意图（对应 design 2.5.2、spec 5.2.1.2）：
#   1. 命名输出读写——节点通过 output_name 写入，下游通过 output_name 读取；
#   2. loop_counters 与 branch_selections 的维护——由调度器调用；
#   3. 向节点提供只读视图——节点执行器不能直接修改全局状态；
#   4. 不做持久化——检查点由调度器统一写入。
# =============================================================================

from typing import Any, Dict, List, Optional

from orchestration_platform.domain.models.common import JsonValue
from orchestration_platform.domain.models.shared_state import SharedState


class SharedStateManager:
    """共享状态管理器：封装 SharedState 的读写操作。"""

    def __init__(self, shared_state: SharedState) -> None:
        """构造管理器，绑定一个 SharedState 实例。"""
        self._state = shared_state

    @property
    def state(self) -> SharedState:
        """返回当前共享状态（调度器用于检查点写入）。"""
        return self._state

    # ------------------------------------------------------------------
    # 命名输出读写
    # ------------------------------------------------------------------
    def read_output(self, output_name: str) -> Optional[JsonValue]:
        """读取命名输出，不存在返回 None。"""
        return self._state.read_output(output_name)

    def write_output(self, output_name: str, value: JsonValue) -> None:
        """写入命名输出（覆盖同名的旧值）。"""
        self._state.write_output(output_name, value)

    def read_all_outputs(self) -> Dict[str, JsonValue]:
        """返回全部命名输出的只读副本。"""
        return dict(self._state.node_outputs)

    # ------------------------------------------------------------------
    # 循环计数维护
    # ------------------------------------------------------------------
    def get_loop_counter(self, node_id: str) -> int:
        """获取循环节点的已迭代次数，未记录返回 0。"""
        return self._state.loop_counters.get(node_id, 0)

    def increment_loop_counter(self, node_id: str) -> int:
        """递增循环节点的计数器，返回递增后的值。"""
        current = self._state.loop_counters.get(node_id, 0)
        new_value = current + 1
        self._state.loop_counters[node_id] = new_value
        return new_value

    # ------------------------------------------------------------------
    # 分支选择维护
    # ------------------------------------------------------------------
    def get_branch_selection(self, node_id: str) -> Optional[str]:
        """获取分支节点的选择结果，未记录返回 None。"""
        return self._state.branch_selections.get(node_id)

    def set_branch_selection(self, node_id: str, target_node_id: str) -> None:
        """记录分支节点的选择结果。"""
        self._state.branch_selections[node_id] = target_node_id

    # ------------------------------------------------------------------
    # B6 并行扇出：分支前沿维护（roadmap_b6_parallel_fanout design §3）
    # 三表均以激活区域的 join_node_id 为键；调度器在并行路径内调用，
    # 单游标路径不触碰这些方法。
    # ------------------------------------------------------------------
    def init_parallel_region(self, join_node_id: str, positions: Dict[str, str]) -> None:
        """初始化并行区域前沿：positions 为 {分支入口 -> 当前节点} 的初始位点表。"""
        self._state.parallel_positions[join_node_id] = dict(positions)

    def advance_branch_position(self, join_node_id: str, branch_entry_id: str, node_id: str) -> None:
        """推进某分支的执行位点（该分支节点完成时调用）。"""
        region = self._state.parallel_positions.get(join_node_id)
        if region is not None and branch_entry_id in region:
            region[branch_entry_id] = node_id

    def remove_branch_position(self, join_node_id: str, branch_entry_id: str) -> None:
        """移除某分支的前沿位点位（分支到达 join 后调用）。

        最后一个位位移除后清除该区域键（前沿为空 = 全部分支已收敛），
        arrivals/cancelled 记录保留至 clear_parallel_region。
        """
        region = self._state.parallel_positions.get(join_node_id)
        if region is not None:
            region.pop(branch_entry_id, None)
            if not region:
                self._state.parallel_positions.pop(join_node_id, None)

    def record_branch_arrival(self, join_node_id: str, branch_entry_id: str) -> None:
        """记录分支成功到达 join（等待计数事件；幂等防重）。"""
        arrivals = self._state.parallel_arrivals.setdefault(join_node_id, [])
        if branch_entry_id not in arrivals:
            arrivals.append(branch_entry_id)

    def record_branch_cancelled(self, join_node_id: str, branch_entry_id: str) -> None:
        """记录分支被取消（fast_fail 裁撤 / 等待触发后裁撤；幂等防重）。"""
        cancelled = self._state.parallel_cancelled.setdefault(join_node_id, [])
        if branch_entry_id not in cancelled:
            cancelled.append(branch_entry_id)

    def get_parallel_arrivals(self, join_node_id: str) -> List[str]:
        """返回已成功到达 join 的分支入口列表（副本）。"""
        return list(self._state.parallel_arrivals.get(join_node_id, []))

    def get_parallel_cancelled(self, join_node_id: str) -> List[str]:
        """返回已取消分支入口列表（副本）。"""
        return list(self._state.parallel_cancelled.get(join_node_id, []))

    def get_parallel_positions(self, join_node_id: str) -> Optional[Dict[str, str]]:
        """返回并行区域前沿位点表（副本）；区域不存在返回 None。"""
        region = self._state.parallel_positions.get(join_node_id)
        return dict(region) if region is not None else None

    def clear_parallel_region(self, join_node_id: str) -> None:
        """join 完成后清除该区域全部三表记录。"""
        self._state.parallel_positions.pop(join_node_id, None)
        self._state.parallel_arrivals.pop(join_node_id, None)
        self._state.parallel_cancelled.pop(join_node_id, None)

    # ------------------------------------------------------------------
    # 位点管理
    # ------------------------------------------------------------------
    @property
    def current_node_id(self) -> Optional[str]:
        """返回当前节点标识。"""
        return self._state.current_node_id

    def advance_to(self, node_id: str) -> None:
        """将位点前进到指定节点。"""
        self._state.current_node_id = node_id

    def increment_round(self) -> int:
        """递增执行轮次，返回新轮次。"""
        self._state.round_index += 1
        return self._state.round_index

    # ------------------------------------------------------------------
    # 快照
    # ------------------------------------------------------------------
    def snapshot(self) -> SharedState:
        """返回共享状态的深拷贝快照（用于检查点持久化）。"""
        return self._state.snapshot()

    # ------------------------------------------------------------------
    # 第二阶段新增：增量更新
    # ------------------------------------------------------------------
    def write_output_incremental(self, output_name: str, value: JsonValue) -> None:
        """增量写入：先记录到 pending_diff，不立即合并到 node_outputs。

        为什么分两步：批量节点执行时多个 write_output 累积在 diff 中，
        commit_diff 一次性合并，减少中间状态序列化开销。
        """
        self._state.pending_diff[output_name] = value

    def commit_diff(self) -> None:
        """提交增量：将 pending_diff 合并到 node_outputs 并清空 diff。"""
        self._state.node_outputs.update(self._state.pending_diff)
        self._state.pending_diff.clear()

    def has_pending_diff(self) -> bool:
        """是否有未提交的增量。"""
        return len(self._state.pending_diff) > 0

    # ------------------------------------------------------------------
    # 只读视图
    # ------------------------------------------------------------------
    def readonly_view(self) -> "ReadOnlyStateView":
        """返回只读状态视图（供节点执行器使用）。"""
        return ReadOnlyStateView(self._state)


class ReadOnlyStateView:
    """共享状态只读视图：节点执行器通过该视图读取上游输出，不能修改状态。

    设计意图：防止节点执行器直接修改全局状态，状态变更统一由调度器管理。
    """

    def __init__(self, state: SharedState) -> None:
        self._state = state

    def read_output(self, output_name: str) -> Optional[JsonValue]:
        """读取命名输出，不存在返回 None。"""
        return self._state.read_output(output_name)

    def read_all_outputs(self) -> Dict[str, JsonValue]:
        """返回全部命名输出的只读副本。"""
        return dict(self._state.node_outputs)

    @property
    def instance_id(self) -> str:
        return self._state.instance_id

    @property
    def current_node_id(self) -> Optional[str]:
        return self._state.current_node_id

    @property
    def round_index(self) -> int:
        return self._state.round_index

    def get_loop_counter(self, node_id: str) -> int:
        return self._state.loop_counters.get(node_id, 0)

    def get_branch_selection(self, node_id: str) -> Optional[str]:
        return self._state.branch_selections.get(node_id)
