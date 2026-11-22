// =============================================================================
// Agent 市场与 Tool 市场类型定义（web/src/market/marketTypes.ts）
//
// 与后端 Pydantic 模型一一对应（TypeScript 严格模式，类型显式声明）；
// preset 用 Record<string, unknown>（键白名单由后端保证，前端不重复声明
// AgentNodeConfiguration 全量字段，INV-D10）。
// 三源语义对齐 orchestrationApiClient.ts 的 ToolSource（"platform"|"mcp"|"local"）；
// 既有 definitionTypes.ts 的二值 ToolSource（Tool 节点用）零改动（Z-15）。
// =============================================================================

/** Agent 市场条目来源（'user_dev' 为用户自主开发上架来源，CHG-30 / INV-D-14） */
export type MarketSourceKind = 'builtin' | 'registered' | 'user_dev';

/** 市场条目状态（与后端 MarketEntryStatus 逐项对应） */
export type MarketEntryStatus = 'builtin' | 'available' | 'draft' | 'pending_review';

/** 模板形态 */
export type AgentTemplateKind = 'single_node' | 'recipe';

/** 工具调用方式 */
export type TemplateInvocationMode = 'react_loop' | 'recipe_nodes';

/** Agent 市场条目 */
export interface AgentMarketEntry {
  entry_id: string;
  name: string;
  description: string;
  category: string;
  tags: string[];
  status: MarketEntryStatus;
  source_kind: MarketSourceKind;
  preset_ref?: string | null;
  tool_refs: string[];
  created_at: string;
  updated_at: string;
}

/** 模板五维度说明文档 */
export interface TemplateDoc {
  core_principle_flow: string;
  application_features: string;
  applicable_scenarios: string;
  advantages_limitations: string;
  comparison: string;
}

/** 模板工具调用配置 */
export interface TemplateToolConfig {
  tool_types: string[];
  invocation_mode: TemplateInvocationMode;
  parameter_constraints: string;
  retry_and_exception_policy: string;
}

/** Agent 模板（内置 5 个，详情含 preset / doc / recipe） */
export interface AgentTemplate {
  template_id: string;
  name: string;
  kind: AgentTemplateKind;
  /** 预设配置（Record 宽类型；应用侧经白名单过滤，context_strategy 为差异化扩展新增键） */
  preset: Record<string, unknown>;
  system_prompt: string;
  allowed_tools: string[];
  max_tool_rounds: number | null;
  output_max_retries: number | null;
  doc: TemplateDoc;
  tool_config: TemplateToolConfig;
  implementation_key_points: string[];
  dependency_conditions: string[];
  performance_boundaries: {
    max_tool_rounds: number | null;
    tool_timeout_seconds: number;
    output_max_retries: number | null;
  };
  security_boundaries: string[];
  evaluation_metrics: Array<{
    metric: string;
    collectable: 'static' | 'runtime' | 'not_quantifiable';
  }>;
  recipe?: {
    nodes: Array<{
      local_ref: string;
      node_type: string;
      name: string;
      configuration: Record<string, unknown>;
    }>;
    edges: Array<{
      source_ref: string;
      target_ref: string;
      condition_label?: string | null;
    }>;
  } | null;
}

/** Tool 市场条目（三源 1:1 派生，AC-7） */
export interface ToolMarketEntry {
  tool_identifier: string;
  name: string;
  description: string;
  source: 'platform' | 'mcp' | 'local';
  permission: string;
  version: string;
  tags: string[];
  input_schema_summary: Array<{ param: string; type: string }>;
  output_schema_summary: Array<{ param: string; type: string }>;
}
