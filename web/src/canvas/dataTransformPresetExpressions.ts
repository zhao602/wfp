// =============================================================================
// 数据转换预制表达式单一来源模块（src/canvas/dataTransformPresetExpressions.ts）
//
// 职责：data_transform 各操作类型"表达式输入框预制初值"的唯一权威出处——
//       只读常量表 + 查询/判定/补空纯函数。
// 设计意图（对应需求 data_transform_preset_expressions 的 FR-02/FR-09/INV-13/DC-01/DC-03）：
//   1. 预制常量仅在此一处定义，表单/工厂以引用取值，禁止 JSX 内联字符串（INV-D01）；
//   2. 常量表键为受控联合 "map" | "filter"，使 aggregate/join 无预制值在类型层即不可能（INV-D07）；
//   3. "是否等于预制值"（isPresetExpression）、"是否为空"（isBlank）、"如何补空"
//      （buildOperationChange）的判定集中于本模块，与 nodeConfigHints.ts（文案来源）、
//      nodeConfigFieldVisibility.ts（可见性来源）同构的"数据来源模块族"惯例。
//
// ⚠ 白名单绑定契约（DC-06/FR-09）：预制值合法性由后端受限表达式求值器保证——
//   `src/orchestration_platform/engine/scheduler.py` 的
//   `RestrictedExpressionEvaluator._ALLOWED_NODES` 白名单（ast.parse(mode="eval")）。
//   后端白名单收窄/扩展导致现有预制值不再合法时，须在同一变更中更新本模块
//   与 __tests__/dataTransformPresetExpressions.test.ts 的对应断言。
//   预制值必须保持"可解析 + 确定无副作用"的最小受限表达式形态（纯字面量，INV-D08）。
//
// 约束：零 React、零副作用、无 IO；仅类型依赖 workflow-definition/definitionTypes；
//       所有函数不抛异常，未知/无表达式框类型安全降级（FR-01/spec §4.2）。
// =============================================================================

import type {
  DataTransformOperation,
  DataTransformNodeConfiguration,
} from "../workflow-definition/definitionTypes";

/** 受控键联合：仅"存在表达式输入框"的操作类型（FR-01/DC-01，INV-D07 禁止臆造扩展） */
export type PresetExpressionOperation = "map" | "filter";

/**
 * 预制表达式常量表（FR-02/AC-02）：每操作类型且仅一条。
 * 取值依据：True 为 ast.Constant，是白名单内唯一"可解析 + 确定无副作用 +
 * 语义可预期"的最小受限表达式（map 原样保留行 / filter 保留全部行）。
 * 修改取值时必须同步核对后端白名单（见文件头契约）。
 */
export const PRESET_EXPRESSIONS: Readonly<Record<PresetExpressionOperation, string>> = {
  map: "True",
  filter: "True",
};

/**
 * 按操作类型取预制表达式。
 * @param operation 数据转换操作类型（全集 map/filter/aggregate/join）
 * @returns 命中返回预制值；aggregate/join 等无表达式框类型返回 undefined（安全降级，不抛异常）
 */
export function getPresetExpression(operation: DataTransformOperation): string | undefined {
  const table = PRESET_EXPRESSIONS as Readonly<Record<string, string | undefined>>;
  return table[operation];
}

/**
 * 当前值是否等于该操作类型的预制表达式（恢复默认入口的可用性判定，DC-05/FR-07）。
 * @param operation 数据转换操作类型
 * @param value 当前表达式字段值
 * @returns 等于预制值返回 true；无预制类型（aggregate/join）恒 false
 */
export function isPresetExpression(
  operation: DataTransformOperation,
  value: string | undefined,
): boolean {
  const preset = getPresetExpression(operation);
  return preset !== undefined && value === preset;
}

/**
 * 空值口径（DC-03/FR-04 原文"空字符串或缺失"）：
 * undefined 与 "" 均判为空；null（类型上不可达）防御性按空处理；
 * 空白串 "   " 视为非空（保留用户刻意输入，不静默覆盖）。
 */
export function isBlank(value: string | undefined | null): boolean {
  return value === undefined || value === null || value === "";
}

/**
 * 切换操作类型时的「仅补空、不覆盖」补全（DC-03/FR-04/FR-06）：
 * - 仅当目标类型的表达式字段为空（isBlank）时补入对应预制值；
 * - 非空值（用户值或预制值）一律保留；
 * - 绝不删除/重置任何其它操作类型已填写的字段；
 * - aggregate/join 无表达式字段 → 仅切换 operation，params 逐字段等价（INV-D07）。
 * 返回新对象、不修改入参；任何输入均不抛异常（INV-D02，spec §4.2）。
 */
export function buildOperationChange(
  config: DataTransformNodeConfiguration,
  nextOperation: DataTransformOperation,
): DataTransformNodeConfiguration {
  const params = config.params ?? {};
  if (nextOperation === "map" && isBlank(params.map_expression)) {
    return {
      ...config,
      operation: nextOperation,
      params: { ...params, map_expression: PRESET_EXPRESSIONS.map },
    };
  }
  if (nextOperation === "filter" && isBlank(params.filter_condition)) {
    return {
      ...config,
      operation: nextOperation,
      params: { ...params, filter_condition: PRESET_EXPRESSIONS.filter },
    };
  }
  return { ...config, operation: nextOperation };
}
