// =============================================================================
// 工作流定义类型定义（src/workflow-definition/definitionTypes.ts）
//
// 职责：与后端 domain/models 对齐的 TypeScript 类型定义。
// 设计意图：
//   1. 五类节点配置以判别联合（discriminated union）表达，以 node_type 为判别字段；
//   2. 全程 TypeScript 严格模式，禁止 any，类型与后端 pydantic 模型一一对应；
//   3. 枚举以字符串字面量联合表达，确保 YAML 导出时以字符串输出。
// =============================================================================

import type { PresetConfiguration } from './frequencyPreset';
import type { ChunkStrategy } from '../knowledge_base/kbManagementTypes';

// ---------------------------------------------------------------------------
// 枚举（与后端 enums.py 逐项对应）
// ---------------------------------------------------------------------------

/** 节点类型：五类基础节点 + 三类第二阶段扩展节点 + 输出展示节点 + 向量知识库节点 + 大模型节点 + 代码节点 */
export type NodeType =
  | "trigger"
  | "agent"
  | "tool"
  | "data_source"
  | "control_flow"
  | "subworkflow"
  | "dynamic_injector"
  | "data_transform"
  | "output_display"
  | "vector_knowledge_base"
  | "llm"
  | "code";

/** 触发方式 */
export type TriggerType = "manual" | "scheduled" | "data_event";

/** 控制流子类型 */
export type ControlFlowKind =
  | "conditional_branch"
  | "loop"
  | "parallel_join"
  | "human_review_gate";

/** 并行失败策略 */
export type ParallelFailureStrategy = "fast_fail" | "wait_all";

/** 工具来源 */
export type ToolSource = "local" | "mcp";

// ---------------------------------------------------------------------------
// 输入输出参数定义
// ---------------------------------------------------------------------------

/** 工作流输入参数声明 */
export interface WorkflowInput {
  name: string;
  type: string;
  required: boolean;
  description: string;
}

/** 工作流输出参数声明 */
export interface WorkflowOutput {
  name: string;
  type: string;
  description: string;
}

// ---------------------------------------------------------------------------
// 节点配置：判别联合（discriminator = node_type）
// ---------------------------------------------------------------------------

/** 条件分支的单条分支条件 */
export interface BranchCondition {
  target_node_id: string;
  expression: string;
  label?: string;
}

/** 触发器节点配置 */
export interface TriggerNodeConfiguration {
  node_type: "trigger";
  trigger_type: TriggerType;
  schedule?: string;
  event_source?: string;
  preset_config?: PresetConfiguration;
  input_template: Record<string, string>;
}

/** Agent 节点输入选择集条目（与后端 AgentInputEntry 字段逐项对齐：reference / alias） */
export interface AgentInputEntry {
  /** 引用表达式（「命名输出名[.字段路径]」） */
  reference: string;
  /** 可选别名：组织用户消息上下文条目名；缺省为引用命名输出名 */
  alias?: string;
}

/** Agent 节点配置 */
export interface AgentNodeConfiguration {
  node_type: "agent";
  model_identifier: string;
  system_prompt: string;
  available_tools: string[];
  /** 输入选择集：未定义 = 隐式注入全部上游命名输出；[] = 显式无输入；非空 = 显式收窄 */
  inputs?: AgentInputEntry[];
  output_schema?: Record<string, string>;
  max_tool_rounds?: number;
  output_max_retries?: number;
  /** 上下文窗口策略（差异化扩展；未定义 = 执行器缺省滑动窗口） */
  context_strategy?: "sliding_window" | "summary_compression" | "importance_ranking" | "structural_lossless";
}

/** Tool 节点配置 */
export interface ToolNodeConfiguration {
  node_type: "tool";
  tool_identifier: string;
  tool_source: ToolSource;
  argument_mapping: Record<string, string>;
  static_arguments: Record<string, unknown>;
}

/** 数据源类型（与后端 enums.py DataSourceType 对齐，11 种） */
export type DataSourceType =
  | "csv"
  | "json"
  | "rest_api"
  | "database"
  | "hologres"
  | "kafka"
  | "flink"
  | "spark"
  | "crm"
  | "erp"
  | "oa";

/** 数据源节点配置（与后端 DataSourceConnectionConfig models.py:47-80 对齐） */
export interface DataSourceNodeConfiguration {
  node_type: "data_source";
  connection_reference: string;
  query: string;
  dataset_identifier?: string;
  /** 数据源类型（DC-1 可选，兼容旧格式导入） */
  source_type?: DataSourceType;
  /** CSV / JSON */
  file_path?: string;
  encoding?: string;
  /** REST API */
  url?: string;
  method?: string;
  headers?: Record<string, string>;
  /** 数据库 */
  connection_string?: string;
  /** Hologres —— 实时数仓 */
  hologres_endpoint?: string;
  hologres_port?: number;
  hologres_database?: string;
  hologres_access_key?: string;
  /** Kafka —— 消息流 */
  kafka_brokers?: string;
  kafka_topic?: string;
  kafka_group_id?: string;
  kafka_offset?: string;
  /** Flink —— 流计算引擎 */
  flink_jobmanager_url?: string;
  flink_jar_id?: string;
  flink_program_args?: string;
  /** Spark —— 批计算引擎 */
  spark_master_url?: string;
  spark_submit_args?: string;
  spark_app_name?: string;
  /** 企业连接器（CRM/ERP/OA） */
  target_endpoint?: string;
}

/** 控制流节点配置 */
export interface ControlFlowNodeConfiguration {
  node_type: "control_flow";
  control_flow_kind: ControlFlowKind;
  // 条件分支
  conditions: BranchCondition[];
  default_branch_target?: string;
  // 循环
  loop_body_entry?: string;
  loop_termination_condition?: string;
  max_iterations?: number;
  // 并行汇聚
  parallel_failure_strategy: ParallelFailureStrategy;
  // 人工审核门
  review_instructions?: string;
  review_timeout_action: "reject" | "wait";
}

/** 上下文传递策略 */
export type ContextStrategy = "inherit" | "isolate";

/** 子工作流节点配置 */
export interface SubWorkflowNodeConfiguration {
  node_type: "subworkflow";
  subworkflow_definition_id: string;
  input_mapping: Record<string, string>;
  output_mapping: Record<string, string>;
  context_strategy: ContextStrategy;
  max_nesting_depth: number;
}

/** 动态注入模板节点定义（与后端 NodeTemplate 对齐） */
export interface NodeTemplate {
  template_id: string;
  node_type: NodeType;
  name: string;
  configuration: Record<string, unknown>;
}

/** 动态注入节点配置 */
export interface DynamicInjectorNodeConfiguration {
  node_type: "dynamic_injector";
  injection_condition: string;
  node_templates: NodeTemplate[];
  max_injection_count: number;
  target_position?: string;
}

/** 数据转换操作类型（与后端 enums.py DataTransformOperation 对齐，21 项；枚举只追加不删改） */
export type DataTransformOperation =
  | "map"
  | "filter"
  | "aggregate"
  | "join"
  | "sort"
  | "distinct"
  | "rename"
  | "select_columns"
  | "drop_columns"
  | "cast"
  | "row_number"
  | "flatten"
  | "explode"
  | "handle_nulls"
  | "regex_extract"
  | "regex_replace"
  | "validate"
  | "masking"
  | "json_path_extract"
  | "sampling"
  | "custom_code";

/** 聚合函数类型 */
export type AggregateFunction = "sum" | "count" | "avg" | "max" | "min";

/** 连接类型 */
export type JoinType = "inner" | "left" | "right" | "full";

/** 自定义脱敏正则模板（与后端 MaskingCustomPattern 对齐） */
export interface MaskingCustomPattern {
  /** 受限正则模式（长度 ≤ 512，禁回溯灾难模式） */
  pattern: string;
  /** 掩码模板（MASK=整段掩码；{n}=第 n 捕获组保留） */
  mask_template: string;
}

/** 数据转换操作参数（与后端 DataTransformOperationParams 对齐；新操作参数统一追加为可选字段，既有字段不删改） */
export interface DataTransformOperationParams {
  map_expression?: string;
  filter_condition?: string;
  group_by?: string[];
  aggregate_function?: AggregateFunction;
  aggregate_field?: string;
  join_type?: JoinType;
  join_key?: string;
  right_dataset_reference?: string;
  // ---- expand_data_transform_ops 仅追加（以下全部可选）----
  /** sort：排序键列表（先到优先） */
  sort_keys?: string[];
  /** sort：与 sort_keys 对齐的方向列表：asc/desc（缺省 asc） */
  sort_orders?: string[];
  /** distinct：去重键列表（空=全列去重） */
  distinct_keys?: string[];
  /** distinct：去重保留策略（first=保留首条，缺省 first） */
  keep?: string;
  /** rename：重命名映射（旧字段名 → 新字段名） */
  rename_map?: Record<string, string>;
  /** select_columns：保留列清单 */
  select_columns?: string[];
  /** drop_columns：删除列清单 */
  drop_columns?: string[];
  /** cast：强转目标字段名 */
  cast_field?: string;
  /** cast：强转目标类型：number/date */
  cast_target?: string;
  /** cast：date 解析格式（strptime 显式格式，如 %Y-%m-%d；date 目标必填） */
  date_format?: string;
  /** join：左数据集连接键列表（多键） */
  join_keys?: string[];
  /** join：右数据集连接键列表（空=与 join_keys 同名） */
  right_join_keys?: string[];
  /** row_number：窗口分组字段列表（空=全局） */
  window_group_by?: string[];
  /** row_number：窗口排序键列表 */
  window_order_by?: string[];
  /** row_number：与 window_order_by 对齐的方向列表：asc/desc */
  window_orders?: string[];
  /** row_number：行号写入字段名（缺省 row_number） */
  window_output_field?: string;
  /** flatten：打平目标字段名（空=整行打平） */
  flatten_field?: string;
  /** flatten：打平最大深度（缺省 3） */
  max_depth?: number;
  /** explode：展开目标字段名（值须为 list） */
  explode_field?: string;
  /** explode：展开生成行数上限（缺省 100000） */
  max_rows?: number;
  /** handle_nulls：空值处理字段列表（空=全部字段） */
  null_fields?: string[];
  /** handle_nulls：空值判定口径：none/empty/missing（缺省全部） */
  null_value_scope?: string[];
  /** handle_nulls：空值策略：fill_constant/fill_mean/fill_median/drop_row */
  strategy?: string;
  /** handle_nulls：fill_constant 填充值 */
  fill_value?: string | number | boolean | null;
  /** handle_nulls：drop_row 删行触发范围：field=按指定列空值删行 / row=整行任意字段空值删行（缺省 field） */
  drop_scope?: string;
  /** regex_extract / regex_replace：正则操作目标字段名 */
  regex_field?: string;
  /** regex_extract / regex_replace：正则模式（长度 ≤ 512，须通过安全校验） */
  pattern?: string;
  /** regex_extract：提取模式：first/all（缺省 first） */
  extract_mode?: string;
  /** regex_extract：捕获组序号（缺省 0=整段匹配） */
  group?: number;
  /** regex_replace：替换串（支持 \1 反向引用） */
  replacement?: string;
  /** regex_extract / regex_replace：结果写入字段名（缺省覆盖目标字段） */
  target_field?: string;
  /** validate：校验目标字段名 */
  validate_field?: string;
  /** validate：校验规则：email/json */
  validate_rule?: string;
  /** validate：非法值处置：drop=删行 / mark=标记（缺省 drop） */
  on_invalid?: string;
  /** masking：脱敏字段清单 */
  mask_fields?: string[];
  /** masking：脱敏规则名列表：phone/id_card/bank_card/email/name/address（内置规则集） */
  mask_rules?: string[];
  /** masking：自定义脱敏正则模板列表（pattern + mask_template，仅作用于 mask_fields） */
  custom_patterns?: MaskingCustomPattern[];
  /** json_path_extract：JSON Path 来源字段名 */
  source_field?: string;
  /** json_path_extract：JSON Path 表达式（受限语法） */
  path?: string;
  /** sampling：采样模式：head/random（缺省 head） */
  sample_mode?: string;
  /** sampling：采样条数（head/random 按条数） */
  sample_n?: number;
  /** sampling：采样比例（0<比例≤100；与 sample_n 二选一，sample_n 优先） */
  sample_percent?: number;
  /** sampling：random 模式随机种子（可复现） */
  random_seed?: number;
  /** custom_code：脚本语言（仅 python，仅登记） */
  language?: string;
  /** custom_code：自定义脚本内容（仅登记与静态校验，运行期不执行） */
  code?: string;
}

/** 数据转换操作链步骤（与后端 DataTransformStep 对齐） */
export interface DataTransformStep {
  /** 步骤操作类型（必填） */
  operation: DataTransformOperation;
  /** 步骤参数（缺省空参数，由各操作按需解释） */
  params?: DataTransformOperationParams;
}

/** 数据转换节点输出格式（与后端 DataTransformOutputFormat 对齐，4 项） */
export type DataTransformOutputFormat =
  | "dataset"
  | "single_object"
  | "text"
  | "kv";

/** 数据转换输出格式参数（与后端 DataTransformOutputFormatParams 对齐；全部可选） */
export interface DataTransformOutputFormatParams {
  /** single_object：目标记录索引（缺省 0=首条） */
  record_index?: number;
  /** text：拼接字段名（空=全文 JSON 序列化） */
  text_field?: string;
  /** text：行间分隔符（缺省换行） */
  text_separator?: string;
  /** kv：键字段名（空=取记录首列） */
  kv_key_field?: string;
  /** kv：值字段名（空=取记录第二列） */
  kv_value_field?: string;
}

/** 数据转换节点配置（operations/output_format/output_format_params 可选追加；既有字段不删改） */
export interface DataTransformNodeConfiguration {
  node_type: "data_transform";
  operation: DataTransformOperation;
  input_dataset_reference: string;
  params: DataTransformOperationParams;
  /** 操作链（空/缺失=单步链，运行期以链为权威，顶层 operation/params 镜像首步） */
  operations?: DataTransformStep[];
  /** 输出格式（缺省 dataset：List[Dict]，行为不变） */
  output_format?: DataTransformOutputFormat;
  /** 输出格式参数（text/kv/single_object 细化） */
  output_format_params?: DataTransformOutputFormatParams;
}

/** 输出展示节点配置（与后端 OutputDisplayNodeConfiguration 对齐） */
export interface OutputDisplayNodeConfiguration {
  node_type: "output_display";
  /** 输出引用：共享状态命名输出名（多选集合；旧单值=单元素集合，留空展示上游全部命名输出） */
  input_reference?: string | string[] | null;
  /** 展示标题：可选，≤100 字符，留空以节点名称为标题 */
  display_title?: string;
  /** 输出方式：screen=屏幕显示 / file=输出为文件；缺省视为 ["screen"] */
  output_mode?: string[] | null;
}

/** 模型加载方式（与后端 ModelLoadingConfig.mode 对齐） */
export type ModelLoadingMode = "local" | "api";

/** 模型加载配置（与后端 ModelLoadingConfig 对齐；无 api_key —— 密钥仅经后端 settings 只读） */
export interface ModelLoadingConfig {
  /** 加载方式：api = 远程模型服务；local = 本地运行时 */
  mode: ModelLoadingMode;
  /** 模型标识 */
  model_identifier: string;
  /** api 模式：模型服务基础地址 */
  base_url?: string;
  /** api 模式：服务调用超时（秒），≥1 */
  timeout_seconds?: number;
  /** local 模式：本地模型文件路径 */
  model_path?: string;
  /** local 模式：推理设备，缺省 cpu */
  device?: "cpu" | "gpu";
  /** local 模式：依赖缺失时是否确定性降级兜底，缺省 false */
  allow_degraded_fallback?: boolean;
}

/** 向量知识库查询录入模式（与后端 QueryMode 对齐） */
export type VectorQueryMode = "manual" | "reference";

/** 向量知识库节点模式（与后端 VectorKnowledgeBaseNodeConfiguration.mode 对齐） */
export type VectorNodeMode = "retrieve" | "write";

/** 待写入文本录入模式（与后端 write_source_mode 对齐） */
export type WriteSourceMode = "manual" | "reference";

/** 写入模式分片参数（与后端 WriteChunkParams 对齐，全部可选） */
export interface WriteChunkParams {
  /** 单分片长度上限，50..2000，缺省 500 */
  max_chunk_length?: number;
  /** 相邻分片重叠字符数，缺省 50 */
  overlap?: number;
  /** 语义相似度阈值，0..1，缺省取服务默认 */
  similarity_threshold?: number;
  /** structural 标题行正则清单（缺省用内置清单） */
  hierarchy?: string[];
}

/** 向量知识库节点配置（与后端 VectorKnowledgeBaseNodeConfiguration 对齐） */
export interface VectorKnowledgeBaseNodeConfiguration {
  node_type: "vector_knowledge_base";
  /** 知识库标识 */
  knowledge_base_id: string;
  /** 查询录入模式：manual = 手动填写；reference = 继承上游命名输出 */
  query_mode: VectorQueryMode;
  /** manual 模式：查询文本，≤2000 字符 */
  query_value?: string;
  /** reference 模式：上游命名输出引用表达式 */
  query_reference?: string;
  /** 召回片段数上限，1..100，缺省 5 */
  top_k: number;
  /** 向量化模型（可选，CHG-35/AC-46：缺省由知识库绑定提供，运行时绑定优先） */
  embedding_model?: ModelLoadingConfig;
  /** 重排模型（可选，缺省不重排） */
  rerank_model?: ModelLoadingConfig;
  // ---- 双模式与写入字段组（vkb_write_mode / design §2.2.2(7)；全部可选，保证旧 YAML 类型守卫通过）----
  /** 节点模式：retrieve=检索（缺省）/ write=写入 */
  mode?: VectorNodeMode;
  /** 待写入文本录入模式：manual=手动 / reference=继承上游 */
  write_source_mode?: WriteSourceMode;
  /** 手动录入的待写入文本（长度上界由运行期配置约束） */
  write_text?: string;
  /** 继承录入的待写入文本引用表达式 */
  write_reference?: string;
  /** 写入模式分片策略（缺省取知识库默认策略） */
  write_chunk_strategy?: ChunkStrategy;
  /** 写入模式分片参数（可选） */
  write_chunk_params?: WriteChunkParams;
  /** 写入幂等键显式覆盖（缺省 f'{knowledge_base_id}:{node_id}'，同键覆盖） */
  write_document_id?: string;
  // ---- 文档标注字段组（vkb_doc_annotation / CHG-27；全部可选，保旧 YAML 类型守卫）----
  /** 标注-来源信息（节点写入缺省"工作流写入"，可显式覆盖） */
  annotation_origin?: string;
  /** 标注-文档发布时间（ISO-8601 或 null=未知） */
  annotation_published_at?: string | null;
  /** 标注-用户自定义标注（名称→值） */
  annotation_custom?: Record<string, unknown>;
  /** 标注能力是否启用（缺省=跟随全局开关） */
  annotation_enabled?: boolean;
}

/** 大模型节点配置（与后端 LlmNodeConfiguration 对齐，spec §6.1；llm_code_nodes_v2） */
export interface LlmNodeConfiguration {
  node_type: "llm";
  /** 模型标识（必备，缺失时加载期拒绝） */
  model_identifier: string;
  /** 系统提示词（默认空串；纯文本 + 装配产物拼接，不做模板引擎解释） */
  system_prompt: string;
  /** 用户提示词（默认空串；与装配产物拼接） */
  user_prompt: string;
  /** 输入选择集：三态语义与 agent 节点一致（None=隐式全量平铺；[]=显式空清单；非空=显式选择集） */
  inputs?: AgentInputEntry[];
  /** 输出结构约束：v2 降级为登记不生效（字段保留保证旧 YAML 零迁移；执行层不消费、不校验、不重生成，DC-09） */
  output_schema?: Record<string, string>;
  // ---- 六采样参数（None=未配置，与「显式配置」区分；取值域与发送规则由既有采样规格唯一权威提供）----
  /** 温度 [0.0,2.0]（未配置=语义缺省 0.7） */
  temperature?: number;
  /** Top P (0.0,1.0]（未配置=语义缺省 1.0） */
  top_p?: number;
  /** Top K：0 或 [1,100]（未配置=不启用） */
  top_k?: number;
  /** 最大生成长度 [1,32768]（未配置=语义缺省 4096） */
  max_tokens?: number;
  /** 停止序列（≤4 条且每条 1..128 字符，去重保序；未配置=不发送） */
  stop?: string[];
  /** 随机种子 [0,2147483647]（未配置=不发送） */
  seed?: number;
}

/** 代码节点配置（与后端 CodeNodeConfiguration 对齐，spec §6.2；llm_code_nodes_v2） */
export interface CodeNodeConfiguration {
  node_type: "code";
  /** 脚本语言（本期取值域仅 python，由加载期校验器把关） */
  language: string;
  /** 脚本文本（必备非空；本期仅 python 语法） */
  code: string;
  /** 入口函数名（展示语义，不参与执行，FR-18） */
  entry_function: string;
  /** 依赖名称清单（仅名称不含版本号，去重保序；仅登记展示，不安装，FR-19） */
  dependencies?: string[];
  /** 输入选择集：三态语义与 agent 节点一致；装配产物以 params 注入代码域（FR-14） */
  inputs?: AgentInputEntry[];
  /** 输出结构声明（声明语义为主，执行层不据此校验 result 结构） */
  output_schema?: Record<string, string>;
  /** 沙箱策略标识（可选；v2 起生效——超时/输出上限取策略与 settings 默认较小值；未引用时按 settings 默认护栏，FR-26） */
  sandbox_policy_id?: string;
}

/** 十二类节点配置的判别联合 */
export type NodeConfiguration =
  | TriggerNodeConfiguration
  | AgentNodeConfiguration
  | ToolNodeConfiguration
  | DataSourceNodeConfiguration
  | ControlFlowNodeConfiguration
  | SubWorkflowNodeConfiguration
  | DynamicInjectorNodeConfiguration
  | DataTransformNodeConfiguration
  | OutputDisplayNodeConfiguration
  | VectorKnowledgeBaseNodeConfiguration
  | LlmNodeConfiguration
  | CodeNodeConfiguration;

// ---------------------------------------------------------------------------
// 节点定义与连线定义
// ---------------------------------------------------------------------------

/** 节点定义 */
export interface NodeDefinition {
  node_id: string;
  name: string;
  node_type: NodeType;
  output_name?: string;
  configuration: NodeConfiguration;
}

/** 连线定义 */
export interface EdgeDefinition {
  edge_id: string;
  source_node_id: string;
  target_node_id: string;
}

// ---------------------------------------------------------------------------
// 工作流定义（五要素）
// ---------------------------------------------------------------------------

/** 工作流定义：规范版本号 + 定义标识 + 名称 + 版本 + 输入 + 输出 + 节点 + 连线 */
export interface WorkflowDefinition {
  spec_version: string;
  definition_id: string;
  name: string;
  definition_version: number;
  inputs: WorkflowInput[];
  outputs: WorkflowOutput[];
  nodes: NodeDefinition[];
  edges: EdgeDefinition[];
}

// ---------------------------------------------------------------------------
// 校验结果类型
// ---------------------------------------------------------------------------

/** 校验问题严重级别 */
export type ValidationSeverity = "error" | "warning";

/** 单条校验问题 */
export interface ValidationIssue {
  code: string;
  severity: ValidationSeverity;
  message: string;
  node_id?: string;
  edge_id?: string;
  location?: { line?: number; column?: number };
}

/** 校验报告 */
export interface ValidationReport {
  valid: boolean;
  issues: ValidationIssue[];
}

// ---------------------------------------------------------------------------
// 类型守卫：按 node_type 缩窄配置类型
// ---------------------------------------------------------------------------

export function isTriggerConfig(
  config: NodeConfiguration,
): config is TriggerNodeConfiguration {
  return config.node_type === "trigger";
}

export function isAgentConfig(
  config: NodeConfiguration,
): config is AgentNodeConfiguration {
  return config.node_type === "agent";
}

export function isToolConfig(
  config: NodeConfiguration,
): config is ToolNodeConfiguration {
  return config.node_type === "tool";
}

export function isDataSourceConfig(
  config: NodeConfiguration,
): config is DataSourceNodeConfiguration {
  return config.node_type === "data_source";
}

export function isControlFlowConfig(
  config: NodeConfiguration,
): config is ControlFlowNodeConfiguration {
  return config.node_type === "control_flow";
}

export function isSubWorkflowConfig(
  config: NodeConfiguration,
): config is SubWorkflowNodeConfiguration {
  return config.node_type === "subworkflow";
}

export function isDynamicInjectorConfig(
  config: NodeConfiguration,
): config is DynamicInjectorNodeConfiguration {
  return config.node_type === "dynamic_injector";
}

export function isDataTransformConfig(
  config: NodeConfiguration,
): config is DataTransformNodeConfiguration {
  return config.node_type === "data_transform";
}
export function isOutputDisplayConfig(
  config: NodeConfiguration,
): config is OutputDisplayNodeConfiguration {
  return config.node_type === "output_display";
}

export function isVectorKnowledgeBaseConfig(
  config: NodeConfiguration,
): config is VectorKnowledgeBaseNodeConfiguration {
  return config.node_type === "vector_knowledge_base";
}

export function isLlmConfig(
  config: NodeConfiguration,
): config is LlmNodeConfiguration {
  return config.node_type === "llm";
}

export function isCodeConfig(
  config: NodeConfiguration,
): config is CodeNodeConfiguration {
  return config.node_type === "code";
}
