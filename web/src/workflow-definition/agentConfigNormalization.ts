// =============================================================================
// Agent 节点配置归一（src/workflow-definition/agentConfigNormalization.ts）
//
// 职责：将 YAML 导入 / 旧持久化 / 模板合并等数据入口的 agent 节点配置归一为安全形态
//（fix_agent_null_length design DC-04）。
// 设计意图：
//   1. 纯函数、不修改入参、不抛异常（承 canvasStateMigrator 纯函数约定）；
//   2. 真值判断实现：Array.isArray 对 null/undefined 恒 false 天然双拦，
//      禁 `!== undefined` 单独作守卫（INV-01，fix_raw_not_iterable 先例教训）；
//   3. 静默归一，不 console.warn：归一是合法容错（旧数据/缺键形态）而非错误事件，
//      与后端 default_factory=list 静默归一行为对齐（R2 取舍登记）；
//   4. 归一后配置恒满足 AgentNodeConfiguration 类型契约：
//      available_tools 恒为 string[]，model_identifier/system_prompt 恒为 string
//      （design §3.1 归一后形态列：缺键/非法 → 容错回落，对齐后端必备语义）。
// =============================================================================

import type {
  AgentInputEntry,
  AgentNodeConfiguration,
} from "./definitionTypes";

/**
 * 字符串数组字段归一（DC-04）。
 * 判定顺序：Array.isArray(value) 且逐项为 string → 保序返回；
 * 其余（null / undefined / 非数组 / 含非字符串项）→ 返回 []。
 */
export function normalizeStringArrayField(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  for (const item of value) {
    if (typeof item !== "string") {
      return [];
    }
  }
  return value;
}

/** 普通对象判定（非 null / 非数组）：output_schema 等记录字段的形态守卫 */
function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Agent 节点配置整体归一（DC-04）：
 * - available_tools → normalizeStringArrayField（null/undefined/非数组 → []，恒为 string[]）；
 * - inputs 保持三态语义（undefined=隐式 / []=显式空 / 非空=显式收窄），
 *   但 null 及非法形态归一为 undefined（对齐后端 Optional=None=隐式语义，避免语义分裂）；
 * - output_schema 为 null / 非对象时归一为 undefined（渲染点已有 ?? {} 守卫承接）；
 * - max_tool_rounds / output_max_retries 非 number（含 null）时归一为 undefined（§3.1 低优先一并归一）；
 * - model_identifier / system_prompt 缺键或非 string 时容错回落 ""（对齐后端必备语义）；
 * - 其余字段原样保留（spread 透传）。
 * 纯函数、不修改入参、不抛异常、静默归一。
 */
export function normalizeAgentNodeConfiguration(
  config: unknown,
): AgentNodeConfiguration {
  if (!isPlainObject(config)) {
    // 整体非法（configuration 为 null / 标量）：回落最小安全配置（不抛异常契约）
    return {
      node_type: "agent",
      model_identifier: "",
      system_prompt: "",
      available_tools: [],
    };
  }
  const source = config as Record<string, unknown>;
  const result: AgentNodeConfiguration = {
    ...(source as unknown as AgentNodeConfiguration),
    node_type: "agent",
    model_identifier:
      typeof source.model_identifier === "string" ? source.model_identifier : "",
    system_prompt:
      typeof source.system_prompt === "string" ? source.system_prompt : "",
    available_tools: normalizeStringArrayField(source.available_tools),
    inputs: Array.isArray(source.inputs)
      ? (source.inputs as AgentInputEntry[])
      : undefined,
    output_schema: isPlainObject(source.output_schema)
      ? (source.output_schema as Record<string, string>)
      : undefined,
    max_tool_rounds:
      typeof source.max_tool_rounds === "number"
        ? source.max_tool_rounds
        : undefined,
    output_max_retries:
      typeof source.output_max_retries === "number"
        ? source.output_max_retries
        : undefined,
  };
  return result;
}
