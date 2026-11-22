// =============================================================================
// 画布状态变换纯函数（src/canvas/canvasMutations.ts）
//
// 职责：提供画布状态的纯数据变换，供按钮与键盘两条删除入口共享同一语义。
// 设计意图（对应 spec add_delete_node_feature、design DC-1/DC-2/DC-5）：
//   1. 无副作用、不触碰 DOM/弹窗，便于单测；
//   2. 删除节点时同批剔除其全部关联连线（出边 + 入边），杜绝悬挂死边；
//   3. 以本模块为最小节点守卫的单一权威判据，UI 禁用态与 handleNodesChange 兜底为其等价投影。
// =============================================================================

import type { WorkflowCanvasState } from "./canvasTypes";

/** 删除失败的判别原因：目标不存在 / 删除后将导致节点数为零 */
export type RemoveNodeFailureReason = "not_found" | "last_node";

/** 删除结果：成功携带新状态；失败携带原因 */
export type RemoveNodeOutcome =
  | { ok: true; nextState: WorkflowCanvasState }
  | { ok: false; reason: RemoveNodeFailureReason };

/**
 * 从画布状态中移除指定节点，并剔除其全部关联连线。
 *
 * 纯函数：不修改入参 state 及其数组/元素引用；失败时不构造新状态。
 * @param state  当前画布状态（唯一事实源）
 * @param nodeId 待删除节点标识（可为不存在值）
 * @returns 成功返回 `{ ok: true, nextState }`；目标不存在返回 `not_found`；将删至零返回 `last_node`
 */
export function removeNode(
  state: WorkflowCanvasState,
  nodeId: string,
): RemoveNodeOutcome {
  // 存在性判定：目标不存在时静默失败，保证重复删除幂等安全
  const targetExists = state.nodes.some((node) => node.id === nodeId);
  if (!targetExists) {
    return { ok: false, reason: "not_found" };
  }

  // 最小节点守卫：禁止删除最后一个节点（对齐后端「nodes 不能为空」校验）
  if (state.nodes.length <= 1) {
    return { ok: false, reason: "last_node" };
  }

  // 节点过滤：仅移除目标节点，其余节点对象引用逐字段不变
  const nextNodes = state.nodes.filter((node) => node.id !== nodeId);

  // 关联连线双向过滤：剔除 source 或 target 命中被删节点的连线，无关连线原样保留
  const nextEdges = state.edges.filter(
    (edge) => edge.source !== nodeId && edge.target !== nodeId,
  );

  return { ok: true, nextState: { nodes: nextNodes, edges: nextEdges } };
}