// =============================================================================
// 执行失败信息格式化纯函数模块（web/src/execution-visualization/failureFeedback.ts）
//
// 职责：将执行事件 payload 中的失败原因提取、格式化为用户可读文本，并提供节点级
//       失败条目的去重合并辅助，统一承载"原因缺失兜底"。
// 设计意图（对应 design.md DC-2/DC-3/DC-10~DC-12、spec 7.3）：
//   1. 纯函数、无副作用、无第三方依赖，便于独立单测；
//   2. extractError 以类型守卫从 Record<string, unknown> 安全取 error 字符串，全程零 any；
//   3. formatNodeFailure / formatInstanceFailure 保证返回非空字符串（INV-1）；
//   4. mergeNodeFailure 按 nodeId 去重、保留最早失败，保证补偿重放下的幂等（DC-11）。
// =============================================================================

/** 节点级失败条目：承载失败节点标识与格式化后的失败原因文本 */
export interface NodeFailureEntry {
  /** 失败节点标识（node_id） */
  nodeId: string;
  /** 失败节点展示名（优先节点 name，回落 nodeId） */
  displayName?: string;
  /** 已格式化的失败原因文本（非空） */
  reason: string;
  /** 失败输入快照（B13 U3：payload.input，后端脱敏+截断后载荷；undefined = 无快照） */
  inputSnapshot?: unknown;
}

/** 执行失败态可辨识联合：区分节点级 / 实例级 / 连接异常三类失败（DC-7） */
export type ExecutionFailure =
  | { kind: "node"; entries: NodeFailureEntry[] }
  | { kind: "instance"; reason: string }
  | { kind: "connection" };

/**
 * 从事件 payload 中安全提取失败原因字符串。
 * 类型守卫：仅当 payload["error"] 为 string 时返回该串，其余（number/undefined/缺失键）一律返回空串。
 */
export function extractError(payload: Record<string, unknown>): string {
  const value = payload["error"];
  return typeof value === "string" ? value : "";
}

/**
 * 从事件 payload 中安全提取失败节点输入快照（B13 U3）。
 * 类型守卫：payload["input"] 为 undefined/null（缺失或显式空）返回 undefined，
 * 其余形态（对象/数组/字符串）原样返回，供可折叠 JSON 块渲染。
 */
export function extractInputSnapshot(payload: Record<string, unknown>): unknown {
  const value = payload["input"];
  return value === undefined || value === null ? undefined : value;
}

/**
 * 格式化节点级失败提示：`节点 <标识> 执行失败：<原因>`。
 * 原因为空/纯空白 → 兜底占位 `节点 <标识> 执行失败（未提供详细原因）`；始终返回非空字符串（INV-1）。
 * 标识优先取 displayName（节点名），为空时回落 nodeId，确保文本始终包含节点标识（AC-3）。
 */
export function formatNodeFailure(nodeId: string, rawError: string, displayName?: string): string {
  const label = displayName && displayName.trim() !== "" ? displayName : nodeId;
  const reason = rawError.trim();
  if (reason === "") {
    return `节点 ${label} 执行失败（未提供详细原因）`;
  }
  return `节点 ${label} 执行失败：${reason}`;
}

/**
 * 格式化实例级失败提示：`工作流执行失败：<原因>`。
 * 原因为空/纯空白 → 兜底占位 `工作流执行失败（未提供详细原因）`；始终返回非空字符串（INV-1）。
 */
export function formatInstanceFailure(rawError: string): string {
  const reason = rawError.trim();
  if (reason === "") {
    return "工作流执行失败（未提供详细原因）";
  }
  return `工作流执行失败：${reason}`;
}

/**
 * 节点级失败条目去重合并：按 nodeId 幂等去重、保留最早失败、稳定顺序追加（DC-11）。
 * 若 incoming.nodeId 已存在则原样返回（保留最早），否则追加至末尾并返回新数组。
 */
export function mergeNodeFailure(
  entries: NodeFailureEntry[],
  incoming: NodeFailureEntry,
): NodeFailureEntry[] {
  if (entries.some((entry) => entry.nodeId === incoming.nodeId)) {
    return entries;
  }
  return [...entries, incoming];
}