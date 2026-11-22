// =============================================================================
// 输出展示节点配置归一化/序列化纯函数
//（workflow-definition/outputDisplayConfig.ts，optimize_output_display_node CHG-07）
//
// 职责：input_reference（联合类型）/ output_mode 的唯一归一化与导出序列化权威。
// 设计意图（DC-01/DC-02/DC-10）：
//   1. 与后端 node_configurations.normalize_output_references /
//      normalize_output_mode 语义逐字对齐（前端归一化解集中，不与后端分歧；
//      该一致性由 web/src/__tests__/outputDisplayConfig.test.ts 对照测试守护）；
//   2. 零 React、零副作用、零 any；不修改入参；
//   3. 序列化形态（DC-10）：引用集合 空→省略 / 1 元素→标量 / N 元素→数组保序；
//      output_mode 等价默认 ["screen"] → 省略，否则写数组。
// =============================================================================

/** 归一化输出引用为字符串集合（undefined / null / "" / [] → []；标量→单元素；剔除空白空元素、保序不去重） */
export function normalizeOutputReferences(
  raw: string | string[] | null | undefined,
): string[] {
  if (raw === undefined || raw === null) {
    return [];
  }
  if (typeof raw === "string") {
    return raw ? [raw] : [];
  }
  const result: string[] = [];
  if (!Array.isArray(raw)) {
    return [];
  }
  for (const item of raw) {
    if (typeof item === "string" && item.trim() !== "") {
      result.push(item);
    }
  }
  return result;
}

/** 归一化输出方式集合（缺省/非法净化 → ["screen"]；仅保留 {"screen","file"}、保序去重） */
export function normalizeOutputMode(raw: string[] | null | undefined): string[] {
  if (!Array.isArray(raw) || raw.length === 0) {
    return ["screen"];
  }
  const result: string[] = [];
  for (const item of raw) {
    if ((item === "screen" || item === "file") && !result.includes(item)) {
      result.push(item);
    }
  }
  return result.length > 0 ? result : ["screen"];
}

/** 序列化输出引用（导出用，DC-10）：空 → undefined（省略）；1 元素 → 标量；N 元素 → 数组保序 */
export function serializeOutputReference(
  refs: string[],
): string | string[] | undefined {
  if (refs.length === 0) {
    return undefined;
  }
  if (refs.length === 1) {
    return refs[0];
  }
  return [...refs];
}

/** 序列化输出方式（导出用，DC-10）：等价默认 ["screen"] → undefined（省略）；否则数组 */
export function serializeOutputMode(
  modes: string[],
): string[] | undefined {
  if (modes.length === 1 && modes[0] === "screen") {
    return undefined;
  }
  return [...modes];
}
