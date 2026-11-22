// =============================================================================
// 节点配置字段可见性模块（src/canvas/nodeConfigFieldVisibility.ts）
//
// 职责：节点参数配置窗口中"字段是否渲染"判定的唯一权威出处——声明式谓词表 + 纯函数。
// 设计意图（对应需求 fix_trigger_display 的 FR-09 / INV-01 / DC-01）：
//   1. 可见性谓词与渲染组件解耦，与 nodeConfigHints.ts 成对（文案来源 + 可见性来源）；
//   2. 键与配置字段名逐字符对齐，机械可核对；
//   3. 查表缺失时安全降级（默认可见），防止误吞字段的灾难性回归。
// 约束：零 React、零副作用、仅类型依赖 definitionTypes 的 TriggerNodeConfiguration。
// 修复语义（OP-01）：定时触发（scheduled）无运行时入口、input_template 不生效故隐藏；
//   手动（manual）与数据事件（data_event）触发下应显示。
// 修改预期时只改本表一处（INV-01 单一来源）。
// =============================================================================

import type {
  TriggerNodeConfiguration,
  VectorKnowledgeBaseNodeConfiguration,
} from "../workflow-definition/definitionTypes";

/** 触发器可见性受控字段联合类型（与触发器配置字段名对齐） */
export type TriggerVisibilityField = "schedule" | "input_template";

/** 可见性谓词：以触发器配置为入参，返回该字段是否应渲染（纯函数，INV-10） */
type VisibilityPredicate = (config: TriggerNodeConfiguration) => boolean;

/**
 * 触发器字段可见性谓词表：键为字段名，值为该字段在给定配置下是否可见。
 * - schedule：仅定时触发下可见（与存量内联判定行为等价）；
 * - input_template：定时触发下隐藏（本次修复语义），其余触发方式可见。
 */
export const TRIGGER_FIELD_VISIBILITY: Readonly<
  Record<TriggerVisibilityField, VisibilityPredicate>
> = {
  schedule: (config) => config.trigger_type === "scheduled",
  input_template: (config) => config.trigger_type !== "scheduled",
};

/**
 * 查询触发器字段是否可见。
 * @param field 字段标识（与配置字段名对齐）；使用宽 string 以支撑缺项降级查询
 * @param config 触发器节点配置
 * @returns 命中谓词返回其结果；未声明字段返回 true（默认可见，安全降级，DC-03）
 */
export function isTriggerFieldVisible(
  field: string,
  config: TriggerNodeConfiguration,
): boolean {
  const table = TRIGGER_FIELD_VISIBILITY as Readonly<
    Record<string, VisibilityPredicate | undefined>
  >;
  const predicate = table[field];
  if (predicate === undefined) {
    return true;
  }
  return predicate(config);
}

// ---------------------------------------------------------------------------
// 向量知识库节点（vector_knowledge_base）：query 双模式互斥可见性（spec AC-06 / CHG-17）
// ---------------------------------------------------------------------------

/** 向量知识库可见性受控字段联合类型（与配置字段名对齐） */
export type VectorKbVisibilityField = "query_value" | "query_reference";

/** 向量知识库可见性谓词：以向量知识库配置为入参的纯函数（INV-10） */
type VectorKbVisibilityPredicate = (
  config: VectorKnowledgeBaseNodeConfiguration,
) => boolean;

/**
 * 向量知识库字段可见性谓词表：键为字段名，值为该字段在给定配置下是否可见。
 * - query_value：仅手动录入（manual）模式可见；
 * - query_reference：仅继承上游（reference）模式可见。
 */
export const VECTOR_KB_FIELD_VISIBILITY: Readonly<
  Record<VectorKbVisibilityField, VectorKbVisibilityPredicate>
> = {
  query_value: (config) => config.query_mode === "manual",
  query_reference: (config) => config.query_mode === "reference",
};

/**
 * 查询向量知识库字段是否可见。
 * @param field 字段标识（与配置字段名对齐）；使用宽 string 以支撑缺项降级查询
 * @param config 向量知识库节点配置
 * @returns 命中谓词返回其结果；未声明字段返回 true（默认可见，安全降级，DC-03）
 */
export function isVectorKnowledgeBaseFieldVisible(
  field: string,
  config: VectorKnowledgeBaseNodeConfiguration,
): boolean {
  const table = VECTOR_KB_FIELD_VISIBILITY as Readonly<
    Record<string, VectorKbVisibilityPredicate | undefined>
  >;
  const predicate = table[field];
  if (predicate === undefined) {
    return true;
  }
  return predicate(config);
}

// ---------------------------------------------------------------------------
// 向量知识库节点：节点模式（retrieve/write）字段互斥可见性
// （vkb_write_mode FR-04 / AC-09）
// ---------------------------------------------------------------------------

/** 向量知识库模式互斥受控字段联合类型（与配置字段名对齐） */
export type VectorKbModeField =
  | "query_mode"
  | "query_value"
  | "query_reference"
  | "top_k"
  | "rerank_model"
  | "write_source_mode"
  | "write_text"
  | "write_reference"
  | "write_chunk_strategy"
  | "write_chunk_params"
  | "write_document_id"
  | "annotation_origin"
  | "annotation_published_at"
  | "annotation_custom"
  | "annotation_enabled";

/** 是否处于检索模式（mode 缺省 retrieve，与后端一致） */
function isRetrieveMode(config: VectorKnowledgeBaseNodeConfiguration): boolean {
  return (config.mode ?? "retrieve") !== "write";
}

/** 是否处于写入模式 */
function isWriteMode(config: VectorKnowledgeBaseNodeConfiguration): boolean {
  return !isRetrieveMode(config);
}

/**
 * 向量知识库节点模式字段可见性谓词表（vkb_write_mode）：
 * - 检索字段组（query_* / top_k / rerank_model）：仅检索模式可见；
 * - 写入字段组（write_*）：仅写入模式可见；
 * - write_text / write_reference：写入模式下再按 write_source_mode 互斥；
 * - embedding_model（绑定只读摘要）未入表：两模式恒可见（默认可见安全降级）。
 */
export const VECTOR_KB_MODE_FIELD_VISIBILITY: Readonly<
  Record<VectorKbModeField, VectorKbVisibilityPredicate>
> = {
  query_mode: (config) => isRetrieveMode(config),
  query_value: (config) =>
    isRetrieveMode(config) && config.query_mode === "manual",
  query_reference: (config) =>
    isRetrieveMode(config) && config.query_mode === "reference",
  top_k: (config) => isRetrieveMode(config),
  rerank_model: (config) => isRetrieveMode(config),
  write_source_mode: (config) => isWriteMode(config),
  write_text: (config) =>
    isWriteMode(config) && (config.write_source_mode ?? "manual") === "manual",
  write_reference: (config) =>
    isWriteMode(config) &&
    (config.write_source_mode ?? "manual") === "reference",
  write_chunk_strategy: (config) => isWriteMode(config),
  write_chunk_params: (config) => isWriteMode(config),
  write_document_id: (config) => isWriteMode(config),
  // vkb_doc_annotation：标注字段随写入模式显隐（CHG-30）
  annotation_origin: (config) => isWriteMode(config),
  annotation_published_at: (config) => isWriteMode(config),
  annotation_custom: (config) => isWriteMode(config),
  annotation_enabled: (config) => isWriteMode(config),
};

/**
 * 查询向量知识库节点模式字段是否可见（vkb_write_mode）。
 * @param field 字段标识（与配置字段名对齐）；使用宽 string 以支撑缺项降级查询
 * @param config 向量知识库节点配置
 * @returns 命中谓词返回其结果；未声明字段返回 true（默认可见，安全降级，DC-03）
 */
export function isVectorKbModeFieldVisible(
  field: string,
  config: VectorKnowledgeBaseNodeConfiguration,
): boolean {
  const table = VECTOR_KB_MODE_FIELD_VISIBILITY as Readonly<
    Record<string, VectorKbVisibilityPredicate | undefined>
  >;
  const predicate = table[field];
  if (predicate === undefined) {
    return true;
  }
  return predicate(config);
}