// =============================================================================
// 画布导出 YAML（src/workflow-definition/canvasToYamlExporter.ts）
//
// 职责：将画布状态导出为满足规范五要素的 YAML 文本。
// 设计意图（对应需求规格 5.5.1.4、任务 T19.1）：
//   1. 字段顺序稳定：spec_version → definition_id → name → definition_version →
//      inputs → outputs → nodes → edges，与范例 YAML 一致；
//   2. 枚举以字符串输出（TypeScript 字面量联合天然为字符串）；
//   3. 节点 configuration 按类型仅输出有意义字段，省略空值。
// =============================================================================

import yaml from "yaml";
import type { WorkflowCanvasState } from "../canvas/canvasTypes";
import type {
  WorkflowDefinition,
  NodeDefinition,
  EdgeDefinition,
  NodeConfiguration,
  TriggerNodeConfiguration,
  AgentNodeConfiguration,
  AgentInputEntry,
  ToolNodeConfiguration,
  DataSourceNodeConfiguration,
  ControlFlowNodeConfiguration,
  SubWorkflowNodeConfiguration,
  DynamicInjectorNodeConfiguration,
  DataTransformNodeConfiguration,
  OutputDisplayNodeConfiguration,
  ModelLoadingConfig,
  VectorKnowledgeBaseNodeConfiguration,
  LlmNodeConfiguration,
  CodeNodeConfiguration,
  NodeTemplate,
  DataTransformOperationParams,
  DataTransformStep,
  DataTransformOutputFormatParams,
} from "./definitionTypes";
import { validateReference } from "../canvas/nodeIoTopology";
import { DATA_SOURCE_TYPE_META } from "../canvas/dataSourceConfig/dataSourceTypeMeta";
import { NODE_OUTPUT_CONTRACTS } from "../canvas/nodeOutputContracts";
import {
  normalizeOutputReferences,
  normalizeOutputMode,
  serializeOutputReference,
  serializeOutputMode,
} from "./outputDisplayConfig";

/**
 * 将画布状态导出为 YAML 文本。
 *
 * @param canvasState 画布状态（节点 + 连线）
 * @returns 满足规范五要素的 YAML 文本
 */
export function canvasToYaml(canvasState: WorkflowCanvasState): string {
  const definition = canvasToDefinition(canvasState);
  const yamlDocument = new yaml.Document(definition);
  return yamlDocument.toString();
}

/**
 * 将画布状态转换为工作流定义对象。
 * 导出时字段顺序固定，便于 diff 与版本管理。
 */
export function canvasToDefinition(
  canvasState: WorkflowCanvasState,
): WorkflowDefinition {
  const nodes: NodeDefinition[] = canvasState.nodes.map((node) => ({
    node_id: node.data.node_id,
    name: node.data.name,
    node_type: node.data.node_type,
    ...(node.data.output_name
      ? { output_name: node.data.output_name }
      : {}),
    configuration: sanitizeConfig(node.data.configuration),
  }));

  const edges: EdgeDefinition[] = canvasState.edges.map((edge, index) => ({
    edge_id: edge.data?.edge_id ?? `e${index + 1}`,
    source_node_id: edge.source,
    target_node_id: edge.target,
  }));

  return {
    spec_version: "1.0",
    definition_id: "exported_workflow",
    name: "导出工作流",
    definition_version: 1,
    inputs: [],
    outputs: [],
    nodes,
    edges,
  };
}

/** 清理配置对象：移除 undefined 字段，使 YAML 输出简洁 */
function sanitizeConfig(config: NodeConfiguration): NodeConfiguration {
  switch (config.node_type) {
    case "trigger":
      return sanitizeTriggerConfig(config);
    case "agent":
      return sanitizeAgentConfig(config);
    case "tool":
      return sanitizeToolConfig(config);
    case "data_source":
      return sanitizeDataSourceConfig(config);
    case "control_flow":
      return sanitizeControlFlowConfig(config);
    case "subworkflow":
      return sanitizeSubWorkflowConfig(config);
    case "dynamic_injector":
      return sanitizeDynamicInjectorConfig(config);
    case "data_transform":
      return sanitizeDataTransformConfig(config);
    case "output_display":
      return sanitizeOutputDisplayConfig(config);
    case "vector_knowledge_base":
      return sanitizeVectorKnowledgeBaseConfig(config);
    case "llm":
      return sanitizeLlmConfig(config);
    case "code":
      return sanitizeCodeConfig(config);
  }
}

function sanitizeTriggerConfig(
  config: TriggerNodeConfiguration,
): TriggerNodeConfiguration {
  const result: TriggerNodeConfiguration = {
    node_type: "trigger",
    trigger_type: config.trigger_type,
    input_template: config.input_template,
  };
  if (config.schedule) result.schedule = config.schedule;
  if (config.event_source) result.event_source = config.event_source;
  if (config.preset_config) result.preset_config = config.preset_config;
  return result;
}

/** 清理 Agent 输入选择集条目：未设置别名时省略 alias 字段（导出 YAML 简洁） */
function sanitizeAgentInputEntry(entry: AgentInputEntry): AgentInputEntry {
  return entry.alias
    ? { reference: entry.reference, alias: entry.alias }
    : { reference: entry.reference };
}

function sanitizeAgentConfig(
  config: AgentNodeConfiguration,
): AgentNodeConfiguration {
  const result: AgentNodeConfiguration = {
    node_type: "agent",
    model_identifier: config.model_identifier,
    system_prompt: config.system_prompt,
    available_tools: config.available_tools,
  };
  // 输入选择集三态导出：undefined 省略（隐式）；[] 原样导出（显式无输入，spec §9.2.3）
  if (config.inputs) result.inputs = config.inputs.map(sanitizeAgentInputEntry);
  if (config.output_schema) result.output_schema = config.output_schema;
  if (config.max_tool_rounds) result.max_tool_rounds = config.max_tool_rounds;
  if (config.output_max_retries)
    result.output_max_retries = config.output_max_retries;
  return result;
}

function sanitizeToolConfig(
  config: ToolNodeConfiguration,
): ToolNodeConfiguration {
  return {
    node_type: "tool",
    tool_identifier: config.tool_identifier,
    tool_source: config.tool_source,
    argument_mapping: config.argument_mapping,
    static_arguments: config.static_arguments,
  };
}

function sanitizeDataSourceConfig(
  config: DataSourceNodeConfiguration,
): DataSourceNodeConfiguration {
  const result: DataSourceNodeConfiguration = {
    node_type: "data_source",
    connection_reference: config.connection_reference,
    query: config.query,
  };
  if (config.dataset_identifier)
    result.dataset_identifier = config.dataset_identifier;
  // BP-6 修复：导出 source_type（AC-5.1，INV-7）
  if (config.source_type) {
    result.source_type = config.source_type;
    // 按元数据表仅导出当前类型专属字段（AC-5.2，非本类型字段不导出）
    const meta = DATA_SOURCE_TYPE_META[config.source_type];
    for (const field of meta.fields) {
      const value = config[field.name];
      if (value === undefined || value === null) continue;
      const strValue = String(value).trim();
      if (strValue === "") continue;
      // Q-4 默认裁决：掩码字段导出为 "***"（不含明文）
      // ⚠️ 掩码导出后后端执行凭据缺失，需后端 credential_store 按 connector_id 补全
      if (field.mask) {
        (result as unknown as Record<string, unknown>)[field.name] = "***";
      } else {
        (result as unknown as Record<string, unknown>)[field.name] = value;
      }
    }
  }
  return result;
}

function sanitizeControlFlowConfig(
  config: ControlFlowNodeConfiguration,
): ControlFlowNodeConfiguration {
  const result: ControlFlowNodeConfiguration = {
    node_type: "control_flow",
    control_flow_kind: config.control_flow_kind,
    conditions: config.conditions,
    parallel_failure_strategy: config.parallel_failure_strategy,
    review_timeout_action: config.review_timeout_action,
  };
  if (config.default_branch_target)
    result.default_branch_target = config.default_branch_target;
  if (config.loop_body_entry) result.loop_body_entry = config.loop_body_entry;
  if (config.loop_termination_condition)
    result.loop_termination_condition = config.loop_termination_condition;
  if (config.max_iterations) result.max_iterations = config.max_iterations;
  if (config.review_instructions)
    result.review_instructions = config.review_instructions;
  return result;
}

function sanitizeSubWorkflowConfig(
  config: SubWorkflowNodeConfiguration,
): SubWorkflowNodeConfiguration {
  const result: SubWorkflowNodeConfiguration = {
    node_type: "subworkflow",
    subworkflow_definition_id: config.subworkflow_definition_id,
    input_mapping: config.input_mapping,
    output_mapping: config.output_mapping,
    context_strategy: config.context_strategy,
    max_nesting_depth: config.max_nesting_depth,
  };
  return result;
}

function sanitizeNodeTemplate(template: NodeTemplate): NodeTemplate {
  return {
    template_id: template.template_id,
    node_type: template.node_type,
    name: template.name,
    configuration: template.configuration,
  };
}

function sanitizeDynamicInjectorConfig(
  config: DynamicInjectorNodeConfiguration,
): DynamicInjectorNodeConfiguration {
  const result: DynamicInjectorNodeConfiguration = {
    node_type: "dynamic_injector",
    injection_condition: config.injection_condition,
    // L-13（fix_agent_null_length DC-05）：node_templates 经导入中间态可为 null/undefined
    // （.map 崩溃），非数组回落空清单；合法数组零回归。
    node_templates: (Array.isArray(config.node_templates) ? config.node_templates : []).map(
      sanitizeNodeTemplate,
    ),
    max_injection_count: config.max_injection_count,
  };
  if (config.target_position) result.target_position = config.target_position;
  return result;
}

function sanitizeDataTransformParams(
  params: DataTransformOperationParams,
): DataTransformOperationParams {
  const result: DataTransformOperationParams = {};
  if (params.map_expression) result.map_expression = params.map_expression;
  if (params.filter_condition) result.filter_condition = params.filter_condition;
  if (params.group_by && params.group_by.length > 0)
    result.group_by = params.group_by;
  if (params.aggregate_function)
    result.aggregate_function = params.aggregate_function;
  if (params.aggregate_field) result.aggregate_field = params.aggregate_field;
  if (params.join_type) result.join_type = params.join_type;
  if (params.join_key) result.join_key = params.join_key;
  if (params.right_dataset_reference)
    result.right_dataset_reference = params.right_dataset_reference;
  // ---- expand_data_transform_ops 追加（非空才写出；数值 0 为合法值按 typeof 保留）----
  if (params.sort_keys && params.sort_keys.length > 0)
    result.sort_keys = params.sort_keys;
  if (params.sort_orders && params.sort_orders.length > 0)
    result.sort_orders = params.sort_orders;
  if (params.distinct_keys && params.distinct_keys.length > 0)
    result.distinct_keys = params.distinct_keys;
  if (params.keep) result.keep = params.keep;
  if (params.rename_map && Object.keys(params.rename_map).length > 0)
    result.rename_map = params.rename_map;
  if (params.select_columns && params.select_columns.length > 0)
    result.select_columns = params.select_columns;
  if (params.drop_columns && params.drop_columns.length > 0)
    result.drop_columns = params.drop_columns;
  if (params.cast_field) result.cast_field = params.cast_field;
  if (params.cast_target) result.cast_target = params.cast_target;
  if (params.date_format) result.date_format = params.date_format;
  if (params.join_keys && params.join_keys.length > 0)
    result.join_keys = params.join_keys;
  if (params.right_join_keys && params.right_join_keys.length > 0)
    result.right_join_keys = params.right_join_keys;
  if (params.window_group_by && params.window_group_by.length > 0)
    result.window_group_by = params.window_group_by;
  if (params.window_order_by && params.window_order_by.length > 0)
    result.window_order_by = params.window_order_by;
  if (params.window_orders && params.window_orders.length > 0)
    result.window_orders = params.window_orders;
  if (params.window_output_field) result.window_output_field = params.window_output_field;
  if (params.flatten_field) result.flatten_field = params.flatten_field;
  if (typeof params.max_depth === "number") result.max_depth = params.max_depth;
  if (params.explode_field) result.explode_field = params.explode_field;
  if (typeof params.max_rows === "number") result.max_rows = params.max_rows;
  if (params.null_fields && params.null_fields.length > 0)
    result.null_fields = params.null_fields;
  if (params.null_value_scope && params.null_value_scope.length > 0)
    result.null_value_scope = params.null_value_scope;
  if (params.strategy) result.strategy = params.strategy;
  // fill_value 各字面量形态（含 ""/0/false/null）均为合法填充值，仅 undefined 视为未设置
  if (params.fill_value !== undefined) result.fill_value = params.fill_value;
  if (params.drop_scope) result.drop_scope = params.drop_scope;
  if (params.regex_field) result.regex_field = params.regex_field;
  if (params.pattern) result.pattern = params.pattern;
  if (params.extract_mode) result.extract_mode = params.extract_mode;
  if (typeof params.group === "number") result.group = params.group;
  if (params.replacement) result.replacement = params.replacement;
  if (params.target_field) result.target_field = params.target_field;
  if (params.validate_field) result.validate_field = params.validate_field;
  if (params.validate_rule) result.validate_rule = params.validate_rule;
  if (params.on_invalid) result.on_invalid = params.on_invalid;
  if (params.mask_fields && params.mask_fields.length > 0)
    result.mask_fields = params.mask_fields;
  if (params.mask_rules && params.mask_rules.length > 0)
    result.mask_rules = params.mask_rules;
  if (params.custom_patterns && params.custom_patterns.length > 0)
    result.custom_patterns = params.custom_patterns;
  if (params.source_field) result.source_field = params.source_field;
  if (params.path) result.path = params.path;
  if (params.sample_mode) result.sample_mode = params.sample_mode;
  if (typeof params.sample_n === "number") result.sample_n = params.sample_n;
  if (typeof params.sample_percent === "number")
    result.sample_percent = params.sample_percent;
  if (typeof params.random_seed === "number") result.random_seed = params.random_seed;
  if (params.language) result.language = params.language;
  if (params.code) result.code = params.code;
  return result;
}

/** 键序无关深序列化（undefined 剔除、数组保序）：单步与顶层 params 等价判定用 */
function stableParamJson(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map((item) => stableParamJson(item)).join(",")}]`;
  }
  if (value !== null && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>)
      .filter(([, v]) => v !== undefined)
      .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
    return `{${entries
      .map(([k, v]) => `${JSON.stringify(k)}:${stableParamJson(v)}`)
      .join(",")}}`;
  }
  return JSON.stringify(value) ?? "null";
}

function sanitizeDataTransformConfig(
  config: DataTransformNodeConfiguration,
): DataTransformNodeConfiguration {
  const result: DataTransformNodeConfiguration = {
    node_type: "data_transform",
    operation: config.operation,
    input_dataset_reference: config.input_dataset_reference,
    params: sanitizeDataTransformParams(config.params),
  };
  // CHG-F07（DC-01）：链长 > 1 或单步与顶层不等价才写 operations（链长 1 等价不写，旧导出形态保真）
  if (config.operations && config.operations.length > 0) {
    const sanitizedSteps: DataTransformStep[] = config.operations.map((step) => ({
      operation: step.operation,
      params: sanitizeDataTransformParams(step.params ?? {}),
    }));
    const singleStepEquivalentToTop =
      sanitizedSteps.length === 1 &&
      sanitizedSteps[0].operation === config.operation &&
      stableParamJson(sanitizedSteps[0].params) === stableParamJson(result.params);
    if (!singleStepEquivalentToTop) {
      result.operations = sanitizedSteps;
    }
  }
  // CHG-F07：非默认输出格式才写出（默认 dataset 等价缺省，INV-05③ 不回填）
  if (config.output_format !== undefined && config.output_format !== "dataset") {
    result.output_format = config.output_format;
    const formatParams = config.output_format_params;
    const sanitizedFormatParams: DataTransformOutputFormatParams = {};
    if (typeof formatParams?.record_index === "number") {
      sanitizedFormatParams.record_index = formatParams.record_index;
    }
    if (formatParams?.text_field) sanitizedFormatParams.text_field = formatParams.text_field;
    if (formatParams?.text_separator)
      sanitizedFormatParams.text_separator = formatParams.text_separator;
    if (formatParams?.kv_key_field) sanitizedFormatParams.kv_key_field = formatParams.kv_key_field;
    if (formatParams?.kv_value_field)
      sanitizedFormatParams.kv_value_field = formatParams.kv_value_field;
    if (Object.keys(sanitizedFormatParams).length > 0) {
      result.output_format_params = sanitizedFormatParams;
    }
  }
  return result;
}

function sanitizeOutputDisplayConfig(
  config: OutputDisplayNodeConfiguration,
): OutputDisplayNodeConfiguration {
  const result: OutputDisplayNodeConfiguration = {
    node_type: "output_display",
  };
  // CHG-10（DC-10）：归一化 → 序列化——引用集合 空→省略字段 / 1 元素→标量
  // （最大兼容 + 过既有断言 INV-D15）/ N 元素→数组保序；禁字段名 input_references（R-03）
  const reference = serializeOutputReference(
    normalizeOutputReferences(config.input_reference),
  );
  if (reference !== undefined) result.input_reference = reference;
  if (config.display_title) result.display_title = config.display_title;
  // CHG-10（DC-10）：output_mode 等价默认 ["screen"] → 省略，否则写数组
  const mode = serializeOutputMode(normalizeOutputMode(config.output_mode));
  if (mode !== undefined) result.output_mode = mode;
  return result;
}

/** 清理模型加载配置：mode / model_identifier 直出，空可选字段省略（无 api_key 字段，INV-D04） */
function sanitizeModelLoadingConfig(
  config: ModelLoadingConfig,
): ModelLoadingConfig {
  const result: ModelLoadingConfig = {
    mode: config.mode,
    model_identifier: config.model_identifier,
  };
  if (config.base_url) result.base_url = config.base_url;
  if (config.timeout_seconds) result.timeout_seconds = config.timeout_seconds;
  if (config.model_path) result.model_path = config.model_path;
  if (config.device) result.device = config.device;
  // false 为后端缺省值，省略不改变语义（导出简洁）
  if (config.allow_degraded_fallback) {
    result.allow_degraded_fallback = config.allow_degraded_fallback;
  }
  return result;
}

function sanitizeVectorKnowledgeBaseConfig(
  config: VectorKnowledgeBaseNodeConfiguration,
): VectorKnowledgeBaseNodeConfiguration {
  const result: VectorKnowledgeBaseNodeConfiguration = {
    node_type: "vector_knowledge_base",
    knowledge_base_id: config.knowledge_base_id,
    query_mode: config.query_mode,
    top_k: config.top_k,
  };
  // CHG-36（DC-08/§2.8.5）：embedding_model 已可选（CHG-35）——存在则导出（旧 YAML 往返一致），
  // 缺省不导出（避免产生空 embedding_model: {} 脏字段导致再次加载解析失败）
  if (config.embedding_model) {
    result.embedding_model = sanitizeModelLoadingConfig(config.embedding_model);
  }
  // query 双模式：仅导出生效模式下的查询字段，空值省略（AC-07）
  if (config.query_mode === "manual" && config.query_value) {
    result.query_value = config.query_value;
  }
  if (config.query_mode === "reference" && config.query_reference) {
    result.query_reference = config.query_reference;
  }
  // rerank 未配置或模型标识为空 → 省略（等同未启用重排）
  if (config.rerank_model && config.rerank_model.model_identifier) {
    result.rerank_model = sanitizeModelLoadingConfig(config.rerank_model);
  }
  // vkb_write_mode：写入字段组保留（旧 YAML 无写入字段 → 往返一致，AC-07）
  // mode 缺省 retrieve（省略即缺省，不产生脏字段）
  if (config.mode === "write") {
    result.mode = config.mode;
  }
  // write_source_mode 缺省 manual（仅非缺省值导出）
  if (config.write_source_mode === "reference") {
    result.write_source_mode = config.write_source_mode;
  }
  if (config.write_text) {
    result.write_text = config.write_text;
  }
  if (config.write_reference) {
    result.write_reference = config.write_reference;
  }
  if (config.write_chunk_strategy) {
    result.write_chunk_strategy = config.write_chunk_strategy;
  }
  if (config.write_chunk_params) {
    result.write_chunk_params = config.write_chunk_params;
  }
  if (config.write_document_id) {
    result.write_document_id = config.write_document_id;
  }
  // vkb_doc_annotation：标注字段保留（旧 YAML 无标注字段 → 往返一致，AC-64）
  if (config.annotation_origin) {
    result.annotation_origin = config.annotation_origin;
  }
  if (
    config.annotation_published_at !== undefined &&
    config.annotation_published_at !== null
  ) {
    result.annotation_published_at = config.annotation_published_at;
  }
  if (config.annotation_custom) {
    result.annotation_custom = config.annotation_custom;
  }
  if (config.annotation_enabled !== undefined) {
    result.annotation_enabled = config.annotation_enabled;
  }
  return result;
}

/** 大模型节点配置导出净化：逐字段拷贝、仅剔除 undefined；采样 None 不落盘（None=省略键、显式值=保留，与后端 model_dump 口径对称，DC-14） */
function sanitizeLlmConfig(
  config: LlmNodeConfiguration,
): LlmNodeConfiguration {
  const result: LlmNodeConfiguration = {
    node_type: "llm",
    model_identifier: config.model_identifier,
    system_prompt: config.system_prompt,
    user_prompt: config.user_prompt,
  };
  // inputs 三态导出：undefined 省略（隐式全量平铺）；[] 与非空原样导出（显式清单），与 agent 同口径（spec §6.1）
  if (config.inputs) {
    result.inputs = config.inputs.map(sanitizeAgentInputEntry);
  }
  // output_schema v2 起登记不生效，但原样透传（旧 YAML 零迁移，DC-09/DC-14-3）
  if (config.output_schema) {
    result.output_schema = config.output_schema;
  }
  // 六采样参数：仅显式值落盘（0 为合法显式值，不得丢弃）
  if (config.temperature !== undefined) {
    result.temperature = config.temperature;
  }
  if (config.top_p !== undefined) {
    result.top_p = config.top_p;
  }
  if (config.top_k !== undefined) {
    result.top_k = config.top_k;
  }
  if (config.max_tokens !== undefined) {
    result.max_tokens = config.max_tokens;
  }
  if (config.stop !== undefined) {
    result.stop = config.stop;
  }
  if (config.seed !== undefined) {
    result.seed = config.seed;
  }
  return result;
}

/** 代码节点配置导出净化：逐字段拷贝、仅剔除 undefined（dependencies 显式空清单原样保留） */
function sanitizeCodeConfig(
  config: CodeNodeConfiguration,
): CodeNodeConfiguration {
  const result: CodeNodeConfiguration = {
    node_type: "code",
    language: config.language,
    code: config.code,
    entry_function: config.entry_function,
  };
  if (config.dependencies !== undefined) {
    result.dependencies = config.dependencies;
  }
  // inputs 三态导出：与 agent/llm 同口径（装配产物以 params 注入代码域，FR-14）
  if (config.inputs) {
    result.inputs = config.inputs.map(sanitizeAgentInputEntry);
  }
  if (config.output_schema) {
    result.output_schema = config.output_schema;
  }
  // sandbox_policy_id 空串等同未配置（面板空值 → undefined 口径一致，FR-26）；显式值原样导出
  if (config.sandbox_policy_id) {
    result.sandbox_policy_id = config.sandbox_policy_id;
  }
  return result;
}

// ---------------------------------------------------------------------------
// 导出期引用校验（仅警告，不阻断导出，不改写引用原值）
// ---------------------------------------------------------------------------

/** 非法输入引用条目（导出前汇总提示用） */
export interface ReferenceIssue {
  /** 所在节点标识 */
  nodeId: string;
  /** 所在节点名称 */
  nodeName: string;
  /** 映射字段名（如 argument_mapping / input_mapping） */
  field: string;
  /** 映射键名 */
  key: string;
  /** 引用表达式原值（不被改写） */
  reference: string;
  /** 警告文案 */
  message: string;
}

/** 非法类型 → 警告文案（与 spec §5.4.1.4 逐字对齐） */
function referenceIssueMessage(
  kind: "notExist" | "notUpstream" | "selfReference",
): string {
  switch (kind) {
    case "notExist":
      return "引用的输出不存在";
    case "notUpstream":
      return "引用的输出不在上游范围内";
    case "selfReference":
      return "不能引用节点自身的输出";
  }
}

/**
 * 收集画布中全部非法输入引用（tool.argument_mapping / subworkflow.input_mapping /
 * agent.inputs 输入选择集）。
 * 仅汇总提示，不阻断导出，不修改引用原值。
 * @param canvasState 画布状态
 * @returns 非法引用条目列表（声明顺序，确定性输出）
 */
export function collectReferenceIssues(
  canvasState: WorkflowCanvasState,
): ReferenceIssue[] {
  const issues: ReferenceIssue[] = [];
  for (const node of canvasState.nodes) {
    const config = node.data.configuration;
    const mappings: Array<{ field: string; map: Record<string, string> }> = [];
    if (config.node_type === "tool") {
      mappings.push({ field: "argument_mapping", map: config.argument_mapping });
    } else if (config.node_type === "subworkflow") {
      mappings.push({ field: "input_mapping", map: config.input_mapping });
    }
    for (const { field, map } of mappings) {
      // L-14（fix_agent_null_length DC-05）：mapping 值经导入链可为 null/undefined
      // （Object.entries(null) 抛 TypeError），nullish 回落空映射；条目值非 string 跳过。
      for (const [key, reference] of Object.entries(map ?? {})) {
        if (typeof reference !== "string" || reference.trim().length === 0) {
          continue;
        }
        const result = validateReference(
          reference,
          node.data.node_id,
          canvasState,
          NODE_OUTPUT_CONTRACTS,
        );
        if (
          result.kind === "notExist" ||
          result.kind === "notUpstream" ||
          result.kind === "selfReference"
        ) {
          issues.push({
            nodeId: node.data.node_id,
            nodeName: node.data.name,
            field,
            key,
            reference,
            message: referenceIssueMessage(result.kind),
          });
        }
      }
    }
    // Agent 输入选择集引用（unified_node_input CHG-F03 可选增强：复用既有警告通道）
    if (config.node_type === "agent" && config.inputs) {
      for (const entry of config.inputs) {
        // L-14（fix_agent_null_length DC-05）：条目 reference 运行时可为 null，非 string 跳过
        if (typeof entry.reference !== "string" || entry.reference.trim().length === 0) {
          continue;
        }
        const result = validateReference(
          entry.reference,
          node.data.node_id,
          canvasState,
          NODE_OUTPUT_CONTRACTS,
        );
        if (
          result.kind === "notExist" ||
          result.kind === "notUpstream" ||
          result.kind === "selfReference"
        ) {
          issues.push({
            nodeId: node.data.node_id,
            nodeName: node.data.name,
            field: "inputs",
            key: entry.alias ?? entry.reference,
            reference: entry.reference,
            message: referenceIssueMessage(result.kind),
          });
        }
      }
    }
  }
  return issues;
}
