// =============================================================================
// 条件配置区组件（src/canvas/ConditionGroupEditor.tsx）
//
// 职责（expand_data_transform_ops design §2.0.2 DC-09.3 / FR-23 / AC-20）：
//   1. 多条件（字段/运算符/值）+ AND/OR 组合的结构化编辑；
//   2. 条件行编译为受限布尔表达式（后端 RestrictedExpressionEvaluator 白名单
//      口径：Name/Compare/BoolOp/Constant），写回目标由挂载方决定
//      （DataTransformConfigForm 中仅 filter 步骤挂载，编译结果写 filter_condition，
//      方言与示例 YAML 一致：裸变量名比较，如 value >= min_value）；
//   3. 仅条件型操作显示（挂载判定在表单层，本组件不感知操作类型）。
//
// 编译口径（compileConditionGroup 纯函数）：
//   - 仅编译"完整行"（字段与值均非空白）；不完整行不参与编译；
//   - 数值型值（^-?\d+(\.\d+)?$）输出数字字面量，其余输出双引号字符串字面量；
//   - 零完整行且存在草稿行 → 不发射（避免覆盖用户既有表达式）；
//   - 零行（全部删除）→ 发射空串（显式清空语义）。
//
// 约束：零原生 select（避免数据转换面板"select 总数"语义漂移）；按钮/输入沿用
// 既有风格（wfp-ncp-input 输入类 + 独立 wfp-cge-* 按钮命名空间）。
// =============================================================================

import { useState, type CSSProperties, type JSX } from "react";
import { useThemeMode, darkTokens } from "../theme/themeMode";

/** 条件运算符（受限求值器 Compare 白名单子集） */
export type ConditionOperator = "==" | "!=" | ">" | ">=" | "<" | "<=";

/** 条件组合逻辑 */
export type ConditionCombinator = "and" | "or";

/** 单条结构化条件 */
export interface ConditionRow {
  /** 字段名（编译为裸变量名引用） */
  field: string;
  /** 比较运算符 */
  operator: ConditionOperator;
  /** 比较值（数值型输出数字字面量，其余输出字符串字面量） */
  value: string;
}

/** 条件组：条件行列表 + 组合逻辑 */
export interface ConditionGroup {
  rows: ConditionRow[];
  combinator: ConditionCombinator;
}

/** 运算符全集（渲染顺序） */
export const CONDITION_OPERATORS: readonly ConditionOperator[] = [
  "==",
  "!=",
  ">",
  ">=",
  "<",
  "<=",
];

/** 组合逻辑全集（渲染顺序） */
export const CONDITION_COMBINATORS: readonly ConditionCombinator[] = ["and", "or"];

/** 空条件组（初始形态：无行、AND 组合） */
export const EMPTY_CONDITION_GROUP: ConditionGroup = { rows: [], combinator: "and" };

/** 行是否完整（字段与值均非空白，可参与编译） */
export function isConditionRowComplete(row: ConditionRow): boolean {
  return row.field.trim().length > 0 && row.value.trim().length > 0;
}

/** 值字面量化：数值型原样输出，其余按双引号字符串字面量（JSON.stringify 转义） */
export function formatConditionValue(value: string): string {
  const trimmed = value.trim();
  if (/^-?\d+(\.\d+)?$/.test(trimmed)) {
    return trimmed;
  }
  return JSON.stringify(trimmed);
}

/** 条件组编译为受限布尔表达式；零完整行返回空串 */
export function compileConditionGroup(group: ConditionGroup): string {
  const parts: string[] = [];
  for (const row of group.rows) {
    if (!isConditionRowComplete(row)) {
      continue;
    }
    parts.push(`${row.field.trim()} ${row.operator} ${formatConditionValue(row.value)}`);
  }
  return parts.join(` ${group.combinator} `);
}

/** 条件配置区属性 */
export interface ConditionGroupEditorProps {
  /** 编译结果发射（条件组每次用户变更时调用；空串 = 显式清空） */
  onCompile: (expression: string) => void;
}

/** 条件配置区组件（内部维护条件组草稿，挂载方仅消费编译结果） */
export default function ConditionGroupEditor({
  onCompile,
}: ConditionGroupEditorProps): JSX.Element {
  const themeMode = useThemeMode();
  const isDark = themeMode === "dark";
  const [group, setGroup] = useState<ConditionGroup>(EMPTY_CONDITION_GROUP);

  /** 变更发射口径：零完整行且有草稿行不发射（保护既有表达式）；零行发射空串 */
  const emit = (next: ConditionGroup): void => {
    setGroup(next);
    const expression = compileConditionGroup(next);
    const hasComplete = next.rows.some(isConditionRowComplete);
    if (expression !== "" || hasComplete || next.rows.length === 0) {
      onCompile(expression);
    }
  };

  const setCombinator = (combinator: ConditionCombinator): void => {
    emit({ ...group, combinator });
  };

  const updateRow = (index: number, patch: Partial<ConditionRow>): void => {
    emit({
      ...group,
      rows: group.rows.map((row, i) => (i === index ? { ...row, ...patch } : row)),
    });
  };

  const removeRow = (index: number): void => {
    emit({ ...group, rows: group.rows.filter((_, i) => i !== index) });
  };

  const addRow = (): void => {
    emit({
      ...group,
      rows: [...group.rows, { field: "", operator: "==", value: "" }],
    });
  };

  const secondaryTextColor = isDark ? darkTokens.text.secondary : "#666";
  const borderColor = isDark ? darkTokens.surface.divider : "#e0e0e0";
  const buttonBg = isDark ? darkTokens.surface.bgHover : "#f5f5f5";
  const buttonActiveBg = isDark ? darkTokens.surface.divider : "#dcdcdc";

  const pillStyle = (active: boolean): CSSProperties => ({
    fontSize: "11px",
    padding: "1px 6px",
    color: secondaryTextColor,
    background: active ? buttonActiveBg : buttonBg,
    border: `1px solid ${borderColor}`,
  });

  return (
    <div
      className="wfp-cge-root"
      style={{
        marginTop: "6px",
        padding: "4px 6px",
        border: `1px solid ${borderColor}`,
        borderRadius: "4px",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "6px",
          marginBottom: "4px",
        }}
      >
        <span style={{ fontSize: "11px", fontWeight: 600, color: secondaryTextColor }}>
          条件配置
        </span>
        {CONDITION_COMBINATORS.map((combinator) => (
          <button
            key={combinator}
            type="button"
            className="wfp-cge-combinator"
            aria-pressed={group.combinator === combinator}
            style={pillStyle(group.combinator === combinator)}
            onClick={() => setCombinator(combinator)}
          >
            {combinator.toUpperCase()}
          </button>
        ))}
      </div>
      {group.rows.map((row, index) => (
        <div
          key={index}
          className="wfp-cge-row"
          style={{ display: "flex", alignItems: "center", gap: "4px", marginBottom: "4px" }}
        >
          <input
            type="text"
            className="wfp-ncp-input"
            style={{ width: "30%" }}
            placeholder="字段名"
            value={row.field}
            onChange={(e) => updateRow(index, { field: e.target.value })}
          />
          {CONDITION_OPERATORS.map((operator) => (
            <button
              key={operator}
              type="button"
              className="wfp-cge-op"
              aria-pressed={row.operator === operator}
              style={pillStyle(row.operator === operator)}
              onClick={() => updateRow(index, { operator })}
            >
              {operator}
            </button>
          ))}
          <input
            type="text"
            className="wfp-ncp-input"
            style={{ flex: 1 }}
            placeholder="值"
            value={row.value}
            onChange={(e) => updateRow(index, { value: e.target.value })}
          />
          <button
            type="button"
            className="wfp-cge-remove"
            style={pillStyle(false)}
            onClick={() => removeRow(index)}
          >
            ✕
          </button>
        </div>
      ))}
      <button
        type="button"
        className="wfp-cge-add"
        style={pillStyle(false)}
        onClick={addRow}
      >
        + 添加条件
      </button>
    </div>
  );
}
