// =============================================================================
// 操作链面板组件（src/canvas/OperationChainPanel.tsx）
//
// 职责（expand_data_transform_ops design §2.0.2 DC-09.2 / FR-22 / AC-19）：
//   1. 操作链步骤列表的结构化呈现：增步/删步/上移/下移；
//   2. 逐步骤内容经 renderStep 渲染属性注入（操作选择/参数区/条件区由
//      NodeConfigurationPanel 的 Field 体系渲染，本组件不重复实现字段控件）；
//   3. 旧配置按"链长 1"呈现为单步骤链（步骤派生在表单层，本组件零感知）。
//
// 约束：
//   - 链长恒 ≥ 1（末步不可删除，归一化口径与后端 normalize_operations 一致）；
//   - 步数上限 MAX_OPERATION_CHAIN_STEPS（与后端 MAX_CHAIN_STEPS 对齐）；
//   - 结构按钮使用独立 wfp-ocp-* 命名空间，不占用 wfp-ncp-btn
//     （后者为"恢复默认表达式"按钮既有查询锚点）；
//   - 零原生 select（操作选择 select 由 renderStep 内容提供）。
// =============================================================================

import type { CSSProperties, ReactNode, JSX } from "react";
import { useThemeMode, darkTokens } from "../theme/themeMode";
import type { DataTransformStep } from "../workflow-definition/definitionTypes";
import { MAX_OPERATION_CHAIN_STEPS } from "./dataTransformOperations";

/** 逐步骤渲染上下文（renderStep 入参） */
export interface OperationStepRenderContext {
  /** 当前步骤 */
  step: DataTransformStep;
  /** 步骤下标（0 起，提示 id 消歧用） */
  stepIndex: number;
  /** 整步替换更新 */
  updateStep: (step: DataTransformStep) => void;
}

/** 操作链面板属性 */
export interface OperationChainPanelProps {
  /** 步骤列表（链长 ≥ 1，由表单层派生保证） */
  steps: DataTransformStep[];
  /** 步骤列表整体变更回调 */
  onChangeSteps: (steps: DataTransformStep[]) => void;
  /** 逐步骤内容渲染属性（操作选择 + 参数区 + 条件区） */
  renderStep: (context: OperationStepRenderContext) => ReactNode;
}

/** 空步骤链常量（稳定引用，规避每次渲染新数组） */
const EMPTY_STEPS: DataTransformStep[] = [];

/** 操作链面板组件（纯结构：步骤容器 + 增删移动；字段控件经 renderStep 注入） */
export default function OperationChainPanel({
  steps,
  onChangeSteps,
  renderStep,
}: OperationChainPanelProps): JSX.Element {
  const themeMode = useThemeMode();
  const isDark = themeMode === "dark";

  // L-03（G-06，fix_agent_null_length DC-05）：入口单点归一——
  // steps 运行时可为 null/undefined/非数组（类型面收紧、运行时宽容），
  // 回落空链安全渲染；合法数组同引用零回归
  // （上游 deriveDataTransformSteps 已派生守卫，此处组件级兜底）。
  const safeSteps = Array.isArray(steps) ? steps : EMPTY_STEPS;

  const secondaryTextColor = isDark ? darkTokens.text.secondary : "#666";
  const borderColor = isDark ? darkTokens.surface.divider : "#e0e0e0";
  const buttonBg = isDark ? darkTokens.surface.bgHover : "#f5f5f5";

  /** 图标钮几何：22×22 方块，禁用降透明度，与面板图标钮同族 */
  const buttonStyle = (disabled: boolean): CSSProperties => ({
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    width: "22px",
    height: "22px",
    padding: 0,
    color: secondaryTextColor,
    background: buttonBg,
    border: `1px solid ${borderColor}`,
    borderRadius: "5px",
    cursor: disabled ? "not-allowed" : "pointer",
    opacity: disabled ? 0.45 : 1,
  });

  /** 指定下标步骤整步替换 */
  const replaceStep = (index: number, step: DataTransformStep): void => {
    onChangeSteps(safeSteps.map((old, i) => (i === index ? step : old)));
  };

  /** 增步：追加默认 map 步骤（参数零填充，切换/恢复默认语义由字段层承接） */
  const addStep = (): void => {
    if (safeSteps.length >= MAX_OPERATION_CHAIN_STEPS) {
      return;
    }
    onChangeSteps([...safeSteps, { operation: "map", params: {} }]);
  };

  /** 删步：链长 > 1 才可删（链长恒 ≥ 1） */
  const removeStep = (index: number): void => {
    if (safeSteps.length <= 1) {
      return;
    }
    onChangeSteps(safeSteps.filter((_, i) => i !== index));
  };

  /** 上移（首步不可移） */
  const moveUp = (index: number): void => {
    if (index <= 0) {
      return;
    }
    const next = [...safeSteps];
    const [moved] = next.splice(index, 1);
    next.splice(index - 1, 0, moved);
    onChangeSteps(next);
  };

  /** 下移（末步不可移） */
  const moveDown = (index: number): void => {
    if (index >= safeSteps.length - 1) {
      return;
    }
    const next = [...safeSteps];
    const [moved] = next.splice(index, 1);
    next.splice(index + 1, 0, moved);
    onChangeSteps(next);
  };

  return (
    <div className="wfp-ocp-root" data-step-count={safeSteps.length}>
      {safeSteps.map((step, index) => (
        <div
          key={index}
          className="wfp-ocp-step"
          data-step-index={index}
          style={{
            border: `1px solid ${borderColor}`,
            borderRadius: "6px",
            padding: "8px 8px 4px 8px",
            marginBottom: "8px",
            background: isDark ? "rgba(255,255,255,0.02)" : "#fcfcfc",
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "4px",
              marginBottom: "4px",
            }}
          >
            <span
              style={{
                fontSize: "11px",
                fontWeight: 600,
                letterSpacing: "0.02em",
                color: secondaryTextColor,
              }}
            >
              步骤 {index + 1}
            </span>
            <button
              type="button"
              className="wfp-ocp-up"
              disabled={index === 0}
              title="上移该步骤"
              aria-label="上移该步骤"
              style={buttonStyle(index === 0)}
              onClick={() => moveUp(index)}
            >
              <svg width={12} height={12} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M6 15l6-6 6 6" />
              </svg>
            </button>
            <button
              type="button"
              className="wfp-ocp-down"
              disabled={index === safeSteps.length - 1}
              title="下移该步骤"
              aria-label="下移该步骤"
              style={buttonStyle(index === safeSteps.length - 1)}
              onClick={() => moveDown(index)}
            >
              <svg width={12} height={12} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M6 9l6 6 6-6" />
              </svg>
            </button>
            <button
              type="button"
              className="wfp-ocp-remove"
              disabled={safeSteps.length <= 1}
              title="删除该步骤"
              aria-label="删除该步骤"
              style={{
                ...buttonStyle(safeSteps.length <= 1),
                marginLeft: "auto",
                color: safeSteps.length <= 1 ? secondaryTextColor : isDark ? darkTokens.danger : "#d32f2f",
                borderColor: safeSteps.length <= 1 ? borderColor : isDark ? darkTokens.danger : "#d32f2f",
                background: "transparent",
              }}
              onClick={() => removeStep(index)}
            >
              <svg width={12} height={12} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M4 7h16M9 7V5h6v2M6 7l1 13h10l1-13M10 11v6M14 11v6" />
              </svg>
            </button>
          </div>
          {renderStep({ step, stepIndex: index, updateStep: (s) => replaceStep(index, s) })}
        </div>
      ))}
      <button
        type="button"
        className="wfp-ocp-add"
        disabled={safeSteps.length >= MAX_OPERATION_CHAIN_STEPS}
        title="在链尾追加一步"
        aria-label="添加步骤"
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          gap: "4px",
          width: "100%",
          padding: "4px 8px",
          fontSize: "11px",
          fontWeight: 500,
          fontFamily: "inherit",
          color: secondaryTextColor,
          background: buttonBg,
          border: `1px dashed ${borderColor}`,
          borderRadius: "5px",
          cursor: safeSteps.length >= MAX_OPERATION_CHAIN_STEPS ? "not-allowed" : "pointer",
          opacity: safeSteps.length >= MAX_OPERATION_CHAIN_STEPS ? 0.45 : 1,
        }}
        onClick={addStep}
      >
        <svg width={12} height={12} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" aria-hidden="true">
          <path d="M12 5v14M5 12h14" />
        </svg>
        + 添加步骤
      </button>
      {safeSteps.length >= MAX_OPERATION_CHAIN_STEPS && (
        <span style={{ fontSize: "11px", color: secondaryTextColor, marginLeft: "6px" }}>
          已达步数上限（{MAX_OPERATION_CHAIN_STEPS}）
        </span>
      )}
    </div>
  );
}
