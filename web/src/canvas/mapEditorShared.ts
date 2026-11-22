// =============================================================================
// 映射编辑器共享纯函数模块（src/canvas/mapEditorShared.ts）
//
// 职责：为 KeyValueMapEditor / OutputNameField / VariableReferenceSelector /
//       StringListEditor / StaticArgumentsEditor / BranchConditionEditor 提供
//       净化、校验、候选收集与值解析的纯函数集合。
// 设计意图：零 React 依赖、零副作用、零 any 类型，可独立单元测试。
// =============================================================================

import type { CanvasNode, WorkflowCanvasState } from "./canvasTypes";

// ---------------------------------------------------------------------------
// 常量
// ---------------------------------------------------------------------------

/** output_name 合法格式正则：小写字母开头，仅含小写字母/数字/下划线，1–64 字符 */
export const OUTPUT_NAME_REGEX = /^[a-z][a-z0-9_]{0,63}$/;

/** output_name 最大长度 */
export const OUTPUT_NAME_MAX_LENGTH = 64;

/** review_instructions 最大长度 */
export const REVIEW_INSTRUCTIONS_MAX_LENGTH = 2000;

/** output_schema 类型名预定义集合 */
export const PREDEFINED_TYPE_NAMES = ["string", "number", "boolean", "object", "array"] as const;

/** 静态参数值类型集合 */
export const STATIC_VALUE_TYPES = ["string", "number", "boolean"] as const;

// ---------------------------------------------------------------------------
// 类型
// ---------------------------------------------------------------------------

/** 静态参数值：经 parseStaticValue 缩窄后的合法类型 */
export type StaticValue = string | number | boolean;

/** 静态参数值类型标签 */
export type StaticValueType = (typeof STATIC_VALUE_TYPES)[number];

/** 校验结果：成功无载荷；失败携带原因文案 */
export type ValidationResult = { ok: true } | { ok: false; reason: string };

/** 命名输出候选项：供 VariableReferenceSelector 渲染 */
export interface NamedOutputCandidate {
  nodeId: string;
  nodeName: string;
  outputName: string;
  /** 输出类别文案（可选，由契约层填充；缺省时不展示徽标） */
  category?: string;
}

/** 输入参数候选项：上游节点可寻址输入位置（upstream_input_ref CHG-F02；
 *  与 NamedOutputCandidate 并列的候选来源，二者互不替代、互不修改） */
export interface InputParameterCandidate {
  /** 来源节点标识（输入位置所属节点） */
  readonly nodeId: string;
  /** 来源节点名称 */
  readonly nodeName: string;
  /** 输入位置名（映射键名 / 固定字段名 / agent alias；寻址键 `nodeId::positionName` 的右段） */
  readonly positionName: string;
  /** 该位置绑定的引用表达式原文（形态 A） */
  readonly reference: string;
  /** 分组类别文案（由候选收集层固定填充「输入参数」；缺省时不展示徽标） */
  readonly category?: string;
}

// ---------------------------------------------------------------------------
// 纯函数
// ---------------------------------------------------------------------------

/**
 * 键名首尾空白净化（INV-7）。
 * 去除首尾空白字符后返回；内部空白保留。
 */
export function trimKey(key: string): string {
  return key.trim();
}

/**
 * 键名唯一性校验（INV-3）。
 * @param existingKeys 当前已存在的键名列表
 * @param newKey 待校验的键名（应已 trim）
 * @param excludeIndex 排除自身索引（编辑场景下排除当前行）
 * @returns true 表示唯一（无冲突）
 */
export function validateKeyUnique(
  existingKeys: string[],
  newKey: string,
  excludeIndex?: number,
): boolean {
  return !existingKeys.some(
    (key, idx) => key === newKey && idx !== excludeIndex,
  );
}

/**
 * 键名互斥校验（INV-4）。
 * @param key 待校验的键名
 * @param excludedKeys 互斥键名集合
 * @returns true 表示不冲突（key 不在 excludedKeys 中）
 */
export function validateKeyNotExcluded(
  key: string,
  excludedKeys: ReadonlySet<string>,
): boolean {
  return !excludedKeys.has(key);
}

/**
 * output_name 格式校验（INV-1）。
 * 空字符串视为合法（表示清除命名输出）。
 * 非空字符串须匹配 OUTPUT_NAME_REGEX。
 */
export function validateOutputNameFormat(value: string): ValidationResult {
  if (value.length === 0) {
    return { ok: true };
  }
  if (!OUTPUT_NAME_REGEX.test(value)) {
    return {
      ok: false,
      reason: "仅允许小写字母、数字、下划线，以字母开头，不超过64字符",
    };
  }
  return { ok: true };
}

/**
 * output_name 全局唯一性校验（INV-2）。
 * @param currentNodeId 当前编辑节点的标识（排除自身）
 * @param value 待校验的命名输出名
 * @param allNodes 画布中所有节点
 * @returns true 表示全局唯一（无冲突）
 */
export function validateOutputNameUnique(
  currentNodeId: string,
  value: string,
  allNodes: CanvasNode[],
): boolean {
  if (value.length === 0) {
    return true;
  }
  return !allNodes.some(
    (node) =>
      node.data.node_id !== currentNodeId &&
      node.data.output_name === value,
  );
}

/**
 * 收集画布中所有已声明命名输出的节点，供变量引用选择器使用。
 * @param canvasState 当前画布状态
 * @returns 命名输出候选项列表（已排除未声明 output_name 的节点）
 */
export function collectNamedOutputs(
  canvasState: WorkflowCanvasState,
): NamedOutputCandidate[] {
  const result: NamedOutputCandidate[] = [];
  for (const node of canvasState.nodes) {
    if (node.data.output_name && node.data.output_name.length > 0) {
      result.push({
        nodeId: node.data.node_id,
        nodeName: node.data.name,
        outputName: node.data.output_name,
      });
    }
  }
  return result;
}

/**
 * 静态参数值类型缩窄（INV-8）。
 * @param raw 原始输入字符串
 * @param type 目标类型
 * @returns 缩窄后的值；解析失败返回 null
 */
export function parseStaticValue(
  raw: string,
  type: StaticValueType,
): StaticValue | null {
  switch (type) {
    case "string":
      return raw;
    case "number": {
      if (raw.trim().length === 0) {
        return null;
      }
      const parsed = Number(raw);
      if (Number.isNaN(parsed)) {
        return null;
      }
      return parsed;
    }
    case "boolean":
      if (raw === "true") {
        return true;
      }
      if (raw === "false") {
        return false;
      }
      return null;
    default:
      return null;
  }
}

/**
 * 推断静态参数值的类型标签（用于回显时恢复类型选择器状态）。
 * @param value 已存储的值
 * @returns 类型标签；无法推断时回落 "string"
 */
export function inferStaticValueType(value: unknown): StaticValueType {
  if (typeof value === "number") {
    return "number";
  }
  if (typeof value === "boolean") {
    return "boolean";
  }
  return "string";
}