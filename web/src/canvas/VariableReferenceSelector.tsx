// =============================================================================
// 变量引用内联下拉选择器（src/canvas/VariableReferenceSelector.tsx）
//
// 职责：辅助编排者构造变量引用表达式——传入 targetNodeId 时仅列出上游节点
//       已声明命名输出（受输出契约可引用性门控）与上游输入参数（形态 B，
//       upstream_input_ref T5.1 / CHG-F04：并列候选 + 分组渲染 + 文案派生，
//       props 接口零改）；未传入时回落全节点候选。
//       同时支持手动输入引用表达式字符串，并对非法引用给出分类警告。
// 设计意图：内联下拉菜单形态（开放问题 8.2 决策 A），复用 nodeIoTopology
//           纯函数层，零新依赖。
// =============================================================================

import { useMemo, useState } from "react";
import { useThemeMode, darkTokens } from "../theme/themeMode";
import type { WorkflowCanvasState } from "./canvasTypes";
import {
  collectUpstreamInputCandidates,
  collectUpstreamNodeIds,
  collectUpstreamOutputCandidates,
  validateReference,
} from "./nodeIoTopology";
import type { ReferenceValidation } from "./nodeIoTopology";
import { NODE_OUTPUT_CONTRACTS } from "./nodeOutputContracts";

/** VariableReferenceSelector 属性 */
export interface VariableReferenceSelectorProps {
  /** 当前画布状态（用于收集命名输出候选） */
  canvasState: WorkflowCanvasState;
  /** 当前引用表达式值 */
  value: string;
  /** 值变更回调 */
  onChange: (value: string) => void;
  /** 目标节点标识（可选）：传入时候选收窄为上游节点；未传入时回落全节点候选 */
  targetNodeId?: string;
}

/** 非法引用警告文案（与 spec §5.4.1.4 逐字对齐）；合法/未绑定返回 null */
function warningText(validation: ReferenceValidation): string | null {
  switch (validation.kind) {
    case "notExist":
      return "引用的输出不存在";
    case "notUpstream":
      return "引用的输出不在上游范围内";
    case "selfReference":
      return "不能引用节点自身的输出";
    default:
      return null;
  }
}

/** 形态 B（输入位置引用）判定：消歧只看首段（DC-01），与
 *  nodeIoTopology.validateReference 文法分支同口径 */
function isFormBExpression(value: string): boolean {
  return value.trim().split(".")[0].includes("::");
}

/** 警告文案派生（upstream_input_ref T5.1 / CHG-F04）：值本身为形态 B 时按
 *  「上游输入项」语义替换三类非法文案；形态 A 及空值沿用 warningText 既有
 *  五态文案，warningText 逐字零改 */
function deriveWarningText(
  value: string,
  validation: ReferenceValidation,
): string | null {
  const base = warningText(validation);
  if (base === null || !isFormBExpression(value)) {
    return base;
  }
  switch (validation.kind) {
    case "notExist":
      return "引用的上游输入项不存在";
    case "notUpstream":
      return "引用的上游输入项不在上游范围内";
    case "selfReference":
      return "不能引用节点自身的输入项";
    default:
      return base;
  }
}

/** 变量引用内联下拉选择器 */
export default function VariableReferenceSelector({
  canvasState,
  value,
  onChange,
  targetNodeId,
}: VariableReferenceSelectorProps): JSX.Element {
  const themeMode = useThemeMode();
  const isDark = themeMode === "dark";
  const [open, setOpen] = useState(false);

  const hasTargetContext = targetNodeId !== undefined;

  const candidates = useMemo(
    () =>
      collectUpstreamOutputCandidates(
        canvasState,
        targetNodeId ?? "",
        NODE_OUTPUT_CONTRACTS,
        !hasTargetContext,
      ),
    [canvasState, targetNodeId, hasTargetContext],
  );

  // 上游输入参数候选（形态 B，CHG-F04）：无 targetNodeId 时收集器自然返回空，
  // 不进入面板渲染路径
  const inputCandidates = useMemo(
    () =>
      collectUpstreamInputCandidates(
        canvasState,
        targetNodeId ?? "",
        NODE_OUTPUT_CONTRACTS,
      ),
    [canvasState, targetNodeId],
  );

  const valueMatchesCandidate = useMemo(
    () => candidates.some((c) => c.outputName === value),
    [candidates, value],
  );

  const validation = useMemo<ReferenceValidation>(() => {
    if (!hasTargetContext) {
      if (value.trim().length === 0) {
        return { kind: "unbound" };
      }
      return valueMatchesCandidate
        ? { kind: "valid" }
        : { kind: "notExist", outputName: value.trim().split(".")[0] };
    }
    return validateReference(
      value,
      targetNodeId,
      canvasState,
      NODE_OUTPUT_CONTRACTS,
    );
  }, [hasTargetContext, value, valueMatchesCandidate, targetNodeId, canvasState]);

  const invalidWarning = deriveWarningText(value, validation);

  const upstreamEmpty = useMemo(
    () =>
      hasTargetContext &&
      collectUpstreamNodeIds(canvasState.edges, targetNodeId ?? "").size === 0,
    [canvasState, targetNodeId, hasTargetContext],
  );

  const emptyHint = upstreamEmpty
    ? "该节点暂无上游节点，无法引用上游输出"
    : hasTargetContext
      ? "上游节点尚未声明命名输出，请先为其声明输出命名"
      : "当前工作流无可用命名输出，请先为上游节点声明输出命名";

  return (
    <>
      <style>{`
        .wfp-vrs-wrap {
          position: relative;
          width: 100%;
        }
        .wfp-vrs-input {
          width: 100%;
          box-sizing: border-box;
          padding: 6px 10px;
          font-size: 12px;
          font-family: inherit;
          color: ${isDark ? darkTokens.text.primary : "#333333"};
          background-color: ${isDark ? darkTokens.surface.bg : "#ffffff"};
          border: 1px solid ${isDark ? darkTokens.surface.divider : "#e0e0e0"};
          border-radius: 4px;
          outline: none;
          transition: border-color 120ms ease;
        }
        .wfp-vrs-input:focus {
          border-color: ${isDark ? darkTokens.surface.borderStrong : "#bdbdbd"};
        }
        .wfp-vrs-input--warn {
          border-color: ${isDark ? darkTokens.danger : "#d32f2f"};
        }
        .wfp-vrs-input::placeholder {
          color: ${isDark ? darkTokens.text.hint : "#b0b0b0"};
        }
        .wfp-vrs-toggle {
          position: absolute;
          right: 4px;
          top: 50%;
          transform: translateY(-50%);
          background: none;
          border: none;
          cursor: pointer;
          padding: 2px 4px;
          font-size: 10px;
          color: ${isDark ? darkTokens.text.hint : "#999"};
        }
        .wfp-vrs-panel {
          position: absolute;
          top: 100%;
          left: 0;
          right: 0;
          z-index: 10;
          max-height: 160px;
          overflow-y: auto;
          background: ${isDark ? darkTokens.surface.bgHover : "#ffffff"};
          border: 1px solid ${isDark ? darkTokens.surface.divider : "#e0e0e0"};
          border-radius: 4px;
          box-shadow: 0 2px 8px rgba(0, 0, 0, ${isDark ? "0.4" : "0.12"});
        }
        .wfp-vrs-option {
          display: block;
          width: 100%;
          box-sizing: border-box;
          padding: 6px 10px;
          font-size: 12px;
          font-family: inherit;
          text-align: left;
          background: none;
          border: none;
          cursor: pointer;
          color: ${isDark ? darkTokens.text.primary : "#333333"};
        }
        .wfp-vrs-option:hover {
          background: ${isDark ? darkTokens.surface.bgActive : "#f5f5f5"};
        }
        .wfp-vrs-option-name {
          font-weight: 500;
        }
        .wfp-vrs-option-category {
          display: inline-block;
          font-size: 10px;
          margin-left: 6px;
          padding: 1px 6px;
          border-radius: 8px;
          color: ${isDark ? darkTokens.text.secondary : "#616161"};
          background: ${isDark ? darkTokens.surface.bgActive : "#f0f0f0"};
        }
        .wfp-vrs-option-node {
          font-size: 10px;
          color: ${isDark ? darkTokens.text.hint : "#999"};
          margin-left: 6px;
        }
        .wfp-vrs-empty {
          padding: 8px 10px;
          font-size: 11px;
          color: ${isDark ? darkTokens.text.hint : "#999"};
        }
        .wfp-vrs-group-label {
          padding: 6px 10px 2px;
          font-size: 10px;
          color: ${isDark ? darkTokens.text.hint : "#999"};
        }
        .wfp-vrs-warn {
          display: block;
          font-size: 10px;
          margin-top: 2px;
          color: ${isDark ? darkTokens.danger : "#d32f2f"};
        }
      `}</style>
      <div className="wfp-vrs-wrap">
        <input
          type="text"
          className={`wfp-vrs-input${invalidWarning !== null ? " wfp-vrs-input--warn" : ""}`}
          value={value}
          placeholder="输入引用名或点击 ▾ 选择"
          onChange={(e) => onChange(e.target.value)}
          onFocus={() => setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 150)}
        />
        <button
          type="button"
          className="wfp-vrs-toggle"
          onClick={() => setOpen(!open)}
          tabIndex={-1}
        >
          ▾
        </button>
        {open && (
          <div className="wfp-vrs-panel">
            {candidates.length === 0 && inputCandidates.length === 0 ? (
              <div className="wfp-vrs-empty">{emptyHint}</div>
            ) : (
              <>
                {candidates.length > 0 && (
                  <>
                    <div className="wfp-vrs-group-label">上游命名输出</div>
                    {candidates.map((c) => (
                      <button
                        type="button"
                        key={c.nodeId}
                        className="wfp-vrs-option"
                        onMouseDown={(e) => {
                          e.preventDefault();
                          onChange(c.outputName);
                          setOpen(false);
                        }}
                      >
                        <span className="wfp-vrs-option-name">{c.outputName}</span>
                        {c.category !== undefined && (
                          <span className="wfp-vrs-option-category">{c.category}</span>
                        )}
                        <span className="wfp-vrs-option-node">← {c.nodeName}</span>
                      </button>
                    ))}
                  </>
                )}
                {inputCandidates.length > 0 && (
                  <>
                    <div className="wfp-vrs-group-label">上游输入参数</div>
                    {inputCandidates.map((c) => (
                      <button
                        type="button"
                        key={`${c.nodeId}::${c.positionName}`}
                        className="wfp-vrs-option"
                        onMouseDown={(e) => {
                          e.preventDefault();
                          onChange(`${c.nodeId}::${c.positionName}`);
                          setOpen(false);
                        }}
                      >
                        <span className="wfp-vrs-option-name">{`${c.nodeId}::${c.positionName}`}</span>
                        {c.category !== undefined && (
                          <span className="wfp-vrs-option-category">{c.category}</span>
                        )}
                        <span className="wfp-vrs-option-node">← {c.nodeName}</span>
                      </button>
                    ))}
                  </>
                )}
              </>
            )}
          </div>
        )}
      </div>
      {invalidWarning !== null && (
        <span className="wfp-vrs-warn">{invalidWarning}</span>
      )}
    </>
  );
}