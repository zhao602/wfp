// =============================================================================
// 连线环校验器（src/canvas/ConnectionValidator.ts）
//
// 职责：在建立连线前检测新连线是否引入新环，引入则拒绝创建。
// 设计意图（对应需求规格 5.5.1.2、任务 T18.3、design CHG-7/DC-5）：
//   1. 前端即时环检测，避免无效连线提交到后端；
//   2. 局部可达性检测：仅拒绝 target 沿既有边可达 source 的新环引入，
//      单源单次搜索，时间复杂度 O(V+E)；
//   3. 既有回边（循环工作流结构）不参与对无关新连线的判定，
//      更精确的"环路径含循环节点则合法"判定由后端拓扑校验器承担。
// =============================================================================

import type { CanvasEdge } from "./canvasTypes";

/** 连线校验结果 */
export interface ConnectionValidationResult {
  /** 是否允许建立连线 */
  ok: boolean;
  /** 拒绝原因（ok=false 时提供） */
  reason?: string;
}

/**
 * 检测从 source 到 target 的新连线是否会构成环。
 *
 * 算法（局部可达性检测）：从 target 出发沿既有边集合（不含候选边）搜索，
 * 若可达 source，则新建边 source→target 会与既有路径构成新环，拒绝创建；
 * 否则放行。既有回边不参与对无关新连线的判定（FR-3.3）。
 *
 * @param edges 现有连线集合
 * @param source 候选连线起点节点标识
 * @param target 候选连线终点节点标识
 * @returns 校验结果
 */
export function validateConnection(
  edges: CanvasEdge[],
  source: string,
  target: string,
): ConnectionValidationResult {
  // 自环直接拒绝（文案保持不变，INV-9）
  if (source === target) {
    return { ok: false, reason: "不允许自环连线" };
  }

  // 局部可达性检测：target 沿既有边可达 source 则新连线引入新环（文案保持不变，INV-9）
  if (isReachable(edges, target, source)) {
    return { ok: false, reason: "该连线会构成环，已拒绝创建" };
  }

  return { ok: true };
}

/**
 * 局部可达性检测：从 start 出发沿既有边集合做 BFS，判断是否可达 goal。
 * 纯函数、无副作用、只遍历既有边集合，复杂度 O(V+E)。
 */
function isReachable(
  edges: CanvasEdge[],
  start: string,
  goal: string,
): boolean {
  // 邻接表仅由既有边构建（不含候选边，避免退回全图语义）
  const adjacency = new Map<string, string[]>();
  for (const edge of edges) {
    const neighbors = adjacency.get(edge.source);
    if (neighbors) {
      neighbors.push(edge.target);
    } else {
      adjacency.set(edge.source, [edge.target]);
    }
  }

  // BFS 搜索（索引进队，避免 shift 的 O(n) 开销）
  const visited = new Set<string>([start]);
  const queue: string[] = [start];
  for (let head = 0; head < queue.length; head++) {
    const current = queue[head]!;
    if (current === goal) {
      return true;
    }
    for (const neighbor of adjacency.get(current) ?? []) {
      if (!visited.has(neighbor)) {
        visited.add(neighbor);
        queue.push(neighbor);
      }
    }
  }
  return false;
}
