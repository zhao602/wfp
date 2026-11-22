// =============================================================================
// 数据转换操作元数据单一来源（src/canvas/dataTransformOperations.ts）
//
// 职责（expand_data_transform_ops design §2.0.2 DC-09.1 / DFX 4.4）：
//   1. 集中定义四分类（basic/cleaning/ai/advanced）→ 操作 → 参数表单描述元数据；
//   2. 操作枚举文案/分类单一权威，供 DataTransformConfigForm 四分类下拉与
//      OperationChainPanel 逐步骤参数区消费；
//   3. 分类归属依据 DC-09：基础操作 / 清洗校验 / AI 增强 / 高级脚本；
//      既有四项（map/filter/aggregate/join）按 FR-21 归入对应分类
//      （DC-09 枚举未列 aggregate，按"既有四项归类"归 basic）。
//   4. 与后端 enums.py DataTransformOperation（21 项）逐项对齐。
// =============================================================================

import type {
  DataTransformOperation,
  DataTransformOperationParams,
  DataTransformOutputFormat,
} from "../workflow-definition/definitionTypes";

/** 操作分类（四分类，DC-09.1） */
export type OperationCategory = "basic" | "cleaning" | "ai" | "advanced";

/** 参数控件类型（逐步骤参数区渲染口径，控件本体复用既有 Field/KeyValueMapEditor） */
export type ParamControlKind =
  /** 单行文本 */
  | "text"
  /** 多行文本 */
  | "textarea"
  /** 表达式（多行 + 预制表达式复用 dataTransformPresetExpressions） */
  | "expression"
  /** 数字 */
  | "number"
  /** 下拉选择（单选） */
  | "select"
  /** 逗号分隔文本 ↔ string[]（沿用 group_by 惯例） */
  | "stringList"
  /** 复选集合 ↔ string[] */
  | "stringSet"
  /** 键值映射 ↔ Record<string, string>（KeyValueMapEditor） */
  | "kvMap"
  /** 自定义脱敏模板列表 ↔ MaskingCustomPattern[]（pattern → mask_template） */
  | "patternPairs";

/** 单个参数字段的表单描述 */
export interface ParamFieldSpec {
  /** 参数字段名（与 DataTransformOperationParams 键逐字符对齐） */
  field: keyof DataTransformOperationParams;
  /** 展示标签 */
  label: string;
  /** 控件类型 */
  control: ParamControlKind;
  /** select/stringSet 候选值 */
  options?: string[];
  /** 占位提示 */
  placeholder?: string;
}

/** 操作元数据 */
export interface OperationMeta {
  /** 操作标识（与后端枚举值一致） */
  id: DataTransformOperation;
  /** 所属分类 */
  category: OperationCategory;
  /** 展示名 */
  label: string;
  /** 逐步骤参数区字段（按渲染顺序） */
  paramFields: ParamFieldSpec[];
  /** 是否挂载条件配置区（ConditionGroupEditor） */
  needsCondition: boolean;
  /** 缺口/降级行为提示（依据 design §2.4 各操作「可降级」口径） */
  degradableNote: string;
}

/** 分类展示顺序（四分类下拉 optgroup 顺序） */
export const OPERATION_CATEGORIES: readonly OperationCategory[] = [
  "basic",
  "cleaning",
  "ai",
  "advanced",
];

/** 分类展示名（四分类下拉 optgroup 标签） */
export const OPERATION_CATEGORY_LABELS: Record<OperationCategory, string> = {
  basic: "基础操作",
  cleaning: "清洗校验",
  ai: "AI 增强",
  advanced: "高级脚本",
};

/** 操作链步数上限（与后端 MAX_CHAIN_STEPS 对齐，超限 DATA_TRANSFORM_CHAIN_TOO_LONG） */
export const MAX_OPERATION_CHAIN_STEPS = 20;

/** 输出格式选项（与后端 DataTransformOutputFormat 4 项对齐；dataset 为缺省行为不变形态） */
export const OUTPUT_FORMAT_OPTIONS: ReadonlyArray<{
  id: DataTransformOutputFormat;
  label: string;
}> = [
  { id: "dataset", label: "数据集（默认）" },
  { id: "single_object", label: "单对象" },
  { id: "text", label: "文本" },
  { id: "kv", label: "键值对" },
];

const OPERATION_METAS: readonly OperationMeta[] = [
  // ---- 基础操作（basic）----
  {
    id: "map",
    category: "basic",
    label: "映射",
    paramFields: [
      { field: "map_expression", label: "映射表达式", control: "expression" },
    ],
    needsCondition: false,
    degradableNote: "行级求值失败置空 + 计数，不中断链",
  },
  {
    id: "filter",
    category: "basic",
    label: "过滤",
    paramFields: [
      { field: "filter_condition", label: "过滤条件", control: "expression" },
    ],
    needsCondition: true,
    degradableNote: "行级求值失败保留原行 + 计数，不中断链",
  },
  {
    id: "aggregate",
    category: "basic",
    label: "聚合",
    paramFields: [
      {
        field: "group_by",
        label: "分组字段",
        control: "stringList",
        placeholder: "逗号分隔，如 user_id,city",
      },
      {
        field: "aggregate_function",
        label: "聚合函数",
        control: "select",
        options: ["sum", "count", "avg", "max", "min"],
      },
      { field: "aggregate_field", label: "聚合字段", control: "text" },
    ],
    needsCondition: false,
    degradableNote: "缺必填参数（分组/聚合函数/字段）fail-fast",
  },
  {
    id: "join",
    category: "basic",
    label: "连接",
    paramFields: [
      {
        field: "join_type",
        label: "连接类型",
        control: "select",
        options: ["inner", "left", "right", "full"],
      },
      { field: "join_key", label: "连接键（单键）", control: "text" },
      {
        field: "right_dataset_reference",
        label: "右数据集引用",
        control: "text",
      },
      {
        field: "join_keys",
        label: "连接键列表（多键，与单键二选一）",
        control: "stringList",
      },
      {
        field: "right_join_keys",
        label: "右连接键列表（空 = 与左侧同名）",
        control: "stringList",
      },
    ],
    needsCondition: false,
    degradableNote: "缺参/缺右表 fail-fast",
  },
  {
    id: "sort",
    category: "basic",
    label: "排序",
    paramFields: [
      {
        field: "sort_keys",
        label: "排序键列表",
        control: "stringList",
        placeholder: "逗号分隔，先到优先",
      },
      {
        field: "sort_orders",
        label: "排序方向",
        control: "stringList",
        placeholder: "与排序键对齐：asc,desc（缺省全升序）",
      },
    ],
    needsCondition: false,
    degradableNote: "参数非法 fail-fast",
  },
  {
    id: "distinct",
    category: "basic",
    label: "去重",
    paramFields: [
      {
        field: "distinct_keys",
        label: "去重键列表",
        control: "stringList",
        placeholder: "逗号分隔，空 = 全列去重",
      },
      {
        field: "keep",
        label: "保留策略",
        control: "select",
        options: ["first"],
      },
    ],
    needsCondition: false,
    degradableNote: "参数非法 fail-fast",
  },
  {
    id: "rename",
    category: "basic",
    label: "字段重命名",
    paramFields: [
      {
        field: "rename_map",
        label: "重命名映射（旧字段名 → 新字段名）",
        control: "kvMap",
      },
    ],
    needsCondition: false,
    degradableNote: "目标列缺失忽略 + 告警；命名冲突 fail-fast",
  },
  {
    id: "select_columns",
    category: "basic",
    label: "保留列",
    paramFields: [
      {
        field: "select_columns",
        label: "保留列清单",
        control: "stringList",
        placeholder: "逗号分隔，如 user_id,city",
      },
    ],
    needsCondition: false,
    degradableNote: "缺失列忽略 + 告警",
  },
  {
    id: "drop_columns",
    category: "basic",
    label: "删除列",
    paramFields: [
      {
        field: "drop_columns",
        label: "删除列清单",
        control: "stringList",
        placeholder: "逗号分隔，如 temp,debug_flag",
      },
    ],
    needsCondition: false,
    degradableNote: "缺失列忽略 + 告警",
  },
  {
    id: "cast",
    category: "basic",
    label: "类型强转",
    paramFields: [
      { field: "cast_field", label: "强转目标字段", control: "text" },
      {
        field: "cast_target",
        label: "目标类型",
        control: "select",
        options: ["number", "date"],
      },
      {
        field: "date_format",
        label: "日期格式",
        control: "text",
        placeholder: "date 目标必填，如 %Y-%m-%d",
      },
    ],
    needsCondition: false,
    degradableNote: "单元格失败置空 + 计数，不中断链",
  },
  {
    id: "row_number",
    category: "basic",
    label: "窗口行号",
    paramFields: [
      {
        field: "window_group_by",
        label: "窗口分组字段",
        control: "stringList",
        placeholder: "逗号分隔，空 = 全局",
      },
      {
        field: "window_order_by",
        label: "窗口排序键",
        control: "stringList",
      },
      {
        field: "window_orders",
        label: "窗口排序方向",
        control: "stringList",
        placeholder: "与排序键对齐：asc,desc",
      },
      {
        field: "window_output_field",
        label: "行号写入字段",
        control: "text",
        placeholder: "缺省 row_number",
      },
    ],
    needsCondition: false,
    degradableNote: "参数非法 fail-fast",
  },
  {
    id: "flatten",
    category: "basic",
    label: "嵌套打平",
    paramFields: [
      {
        field: "flatten_field",
        label: "打平目标字段",
        control: "text",
        placeholder: "空 = 整行打平",
      },
      { field: "max_depth", label: "最大深度", control: "number", placeholder: "缺省 3" },
    ],
    needsCondition: false,
    degradableNote: "参数非法 fail-fast",
  },
  {
    id: "explode",
    category: "basic",
    label: "数组展开",
    paramFields: [
      { field: "explode_field", label: "展开目标字段", control: "text" },
      { field: "max_rows", label: "行数上限", control: "number", placeholder: "缺省 100000" },
    ],
    needsCondition: false,
    degradableNote: "非数组置空 + 计数，不中断链",
  },
  // ---- 清洗校验（cleaning）----
  {
    id: "handle_nulls",
    category: "cleaning",
    label: "空值处理",
    paramFields: [
      {
        field: "null_fields",
        label: "空值处理字段",
        control: "stringList",
        placeholder: "逗号分隔，空 = 全部字段",
      },
      {
        field: "null_value_scope",
        label: "空值判定口径",
        control: "stringSet",
        options: ["none", "empty", "missing"],
      },
      {
        field: "strategy",
        label: "空值策略",
        control: "select",
        options: ["fill_constant", "fill_mean", "fill_median", "drop_row"],
      },
      { field: "fill_value", label: "固定填充值", control: "text" },
      {
        field: "drop_scope",
        label: "删行触发范围",
        control: "select",
        options: ["field", "row"],
      },
    ],
    needsCondition: false,
    degradableNote: "缺策略 fail-fast；统计缺数置空 + 告警",
  },
  {
    id: "regex_extract",
    category: "cleaning",
    label: "正则提取",
    paramFields: [
      { field: "regex_field", label: "目标字段", control: "text" },
      { field: "pattern", label: "正则模式", control: "text", placeholder: "长度 ≤ 512，须通过安全校验" },
      {
        field: "extract_mode",
        label: "提取模式",
        control: "select",
        options: ["first", "all"],
      },
      { field: "group", label: "捕获组序号", control: "number", placeholder: "缺省 0=整段匹配" },
      { field: "target_field", label: "结果写入字段", control: "text", placeholder: "缺省覆盖目标字段" },
    ],
    needsCondition: false,
    degradableNote: "无匹配置空 + 计数，不中断链",
  },
  {
    id: "regex_replace",
    category: "cleaning",
    label: "正则替换",
    paramFields: [
      { field: "regex_field", label: "目标字段", control: "text" },
      { field: "pattern", label: "正则模式", control: "text", placeholder: "长度 ≤ 512，须通过安全校验" },
      { field: "replacement", label: "替换串", control: "text", placeholder: "支持 \\1 反向引用" },
      { field: "target_field", label: "结果写入字段", control: "text", placeholder: "缺省覆盖目标字段" },
    ],
    needsCondition: false,
    degradableNote: "无匹配置空 + 计数，不中断链",
  },
  {
    id: "validate",
    category: "cleaning",
    label: "格式校验",
    paramFields: [
      { field: "validate_field", label: "校验目标字段", control: "text" },
      {
        field: "validate_rule",
        label: "校验规则",
        control: "select",
        options: ["email", "json"],
      },
      {
        field: "on_invalid",
        label: "非法值处置",
        control: "select",
        options: ["drop", "mark"],
      },
    ],
    needsCondition: false,
    degradableNote: "缺规则 fail-fast；非法值按处置策略过滤/标记",
  },
  {
    id: "masking",
    category: "cleaning",
    label: "数据脱敏",
    paramFields: [
      {
        field: "mask_fields",
        label: "脱敏字段清单",
        control: "stringList",
        placeholder: "逗号分隔",
      },
      {
        field: "mask_rules",
        label: "内置脱敏规则",
        control: "stringSet",
        options: ["phone", "id_card", "bank_card", "email", "name", "address"],
      },
      {
        field: "custom_patterns",
        label: "自定义脱敏模板（正则 → 掩码模板）",
        control: "patternPairs",
      },
    ],
    needsCondition: false,
    degradableNote: "不可逆掩码；缺口告警",
  },
  // ---- AI 增强（ai）----
  {
    id: "json_path_extract",
    category: "ai",
    label: "JSON Path 提取",
    paramFields: [
      { field: "source_field", label: "来源字段", control: "text" },
      {
        field: "path",
        label: "JSON Path 表达式",
        control: "text",
        placeholder: "如 $.data.items[0].name",
      },
      { field: "target_field", label: "结果写入字段", control: "text" },
    ],
    needsCondition: false,
    degradableNote: "路径缺失置空 + 计数，不中断链",
  },
  // ---- 高级脚本（advanced）----
  {
    id: "sampling",
    category: "advanced",
    label: "数据抽样",
    paramFields: [
      {
        field: "sample_mode",
        label: "采样模式",
        control: "select",
        options: ["head", "random"],
      },
      { field: "sample_n", label: "采样条数", control: "number" },
      {
        field: "sample_percent",
        label: "采样比例",
        control: "number",
        placeholder: "0 < 比例 ≤ 100，与条数二选一（条数优先）",
      },
      { field: "random_seed", label: "随机种子", control: "number", placeholder: "random 模式可复现" },
    ],
    needsCondition: false,
    degradableNote: "参数非法 fail-fast",
  },
  {
    id: "custom_code",
    category: "advanced",
    label: "自定义脚本",
    paramFields: [
      { field: "language", label: "脚本语言", control: "text", placeholder: "仅 python" },
      { field: "code", label: "脚本内容", control: "textarea" },
    ],
    needsCondition: false,
    degradableNote: "仅登记 + 静态校验，运行期不执行",
  },
];

const META_BY_ID: ReadonlyMap<string, OperationMeta> = new Map(
  OPERATION_METAS.map((meta) => [meta.id, meta]),
);

/** 按操作标识取元数据（未知操作安全降级 undefined，不抛错） */
export function getOperationMeta(id: string): OperationMeta | undefined {
  return META_BY_ID.get(id);
}

/** 按分类分组取操作元数据（四分类键齐全，值保持定义顺序） */
export function getOperationsByCategory(): Record<OperationCategory, OperationMeta[]> {
  const grouped: Record<OperationCategory, OperationMeta[]> = {
    basic: [],
    cleaning: [],
    ai: [],
    advanced: [],
  };
  for (const meta of OPERATION_METAS) {
    grouped[meta.category].push(meta);
  }
  return grouped;
}
