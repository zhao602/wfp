// =============================================================================
// output_name 编辑控件（src/canvas/OutputNameField.tsx）
//
// 职责：为所有节点类型提供命名输出（output_name）的编辑能力，
//       含格式校验（INV-1）与全局唯一性校验（INV-2）。
// 设计意图：纯展示+回写组件，校验失败时不回写，显示内联错误提示。
// =============================================================================

import { useCallback, useState } from "react";
import { useThemeMode, darkTokens } from "../theme/themeMode";
import type { CanvasNode } from "./canvasTypes";
import {
  validateOutputNameFormat,
  validateOutputNameUnique,
} from "./mapEditorShared";
import { PANEL_COMMON_HINTS } from "./nodeConfigHints";

/** OutputNameField 属性 */
export interface OutputNameFieldProps {
  /** 当前编辑节点的标识（排除自身做全局唯一校验） */
  currentNodeId: string;
  /** 当前 output_name 值 */
  value: string | undefined;
  /** 画布中所有节点（用于全局唯一校验） */
  allNodes: CanvasNode[];
  /** 值变更回调；空值回写 undefined */
  onChange: (value: string | undefined) => void;
}

/** output_name 编辑控件 */
export default function OutputNameField({
  currentNodeId,
  value,
  allNodes,
  onChange,
}: OutputNameFieldProps): JSX.Element {
  const themeMode = useThemeMode();
  const isDark = themeMode === "dark";
  const [error, setError] = useState<string | null>(null);

  /** blur/Enter 提交：先格式校验，再唯一校验，通过后回写 */
  const commit = useCallback(() => {
    const trimmed = (value ?? "").trim();
    const formatResult = validateOutputNameFormat(trimmed);
    if (!formatResult.ok) {
      setError(formatResult.reason);
      return;
    }
    if (!validateOutputNameUnique(currentNodeId, trimmed, allNodes)) {
      setError("该命名输出已被其他节点使用");
      return;
    }
    setError(null);
    onChange(trimmed.length === 0 ? undefined : trimmed);
  }, [value, currentNodeId, allNodes, onChange]);

  return (
    <>
      <style>{`
        /* 与面板字段体系同构：错误态内联可见，说明收进行内提示气泡 */
        .wfp-onf-error {
          display: block;
          font-size: 11px;
          line-height: 1.45;
          margin-top: 5px;
          color: ${isDark ? darkTokens.danger : "#d32f2f"};
        }
      `}</style>
      <label className="wfp-ncp-field">
        <span className="wfp-ncp-labelrow">
          <span className="wfp-ncp-field-label">命名输出（可选）</span>
          <span className="wfp-ncp-hint-anchor">
            <button type="button" className="wfp-ncp-hint-btn" aria-label="查看命名输出说明">
              <svg
                width={13}
                height={13}
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth={2}
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
                focusable="false"
              >
                <circle cx="12" cy="12" r="9" />
                <path d="M12 16v-4M12 8h.01" />
              </svg>
            </button>
            <span id="wfp-onf-hint" className="wfp-ncp-field-hint" role="tooltip">
              {PANEL_COMMON_HINTS.output_name.helper}
            </span>
          </span>
        </span>
        <span className="wfp-ncp-control">
          <input
            type="text"
            className="wfp-ncp-input"
            value={value ?? ""}
            placeholder={PANEL_COMMON_HINTS.output_name.placeholder}
            aria-describedby="wfp-onf-hint"
            onChange={(e) => {
              onChange(e.target.value.length === 0 ? undefined : e.target.value);
              setError(null);
            }}
            onBlur={commit}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                commit();
              }
            }}
          />
        </span>
        {error ? <span className="wfp-onf-error">{error}</span> : null}
      </label>
    </>
  );
}
