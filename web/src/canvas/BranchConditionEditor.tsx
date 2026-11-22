// =============================================================================
// 分支条件列表编辑器（src/canvas/BranchConditionEditor.tsx）
//
// 职责：编辑 BranchCondition[] 类型的分支条件列表，每条含目标节点 select +
//       表达式 textarea + 可选标签 input。目标节点不存在时标注警告（INV-6）。
// =============================================================================

import { useCallback } from "react";
import { useThemeMode, darkTokens } from "../theme/themeMode";
import type { CanvasNode } from "./canvasTypes";
import type { BranchCondition } from "../workflow-definition/definitionTypes";

/** 空分支条件列表常量（稳定引用） */
const EMPTY_CONDITIONS: BranchCondition[] = [];

/** BranchConditionEditor 属性 */
export interface BranchConditionEditorProps {
  /** 当前分支条件列表 */
  value: BranchCondition[];
  /** 值变更回调 */
  onChange: (value: BranchCondition[]) => void;
  /** 画布中所有节点（用于目标节点 select 候选） */
  allNodes: CanvasNode[];
}

/** 分支条件列表编辑器 */
export default function BranchConditionEditor({
  value,
  onChange,
  allNodes,
}: BranchConditionEditorProps): JSX.Element {
  const themeMode = useThemeMode();
  const isDark = themeMode === "dark";

  // L-08（fix_agent_null_length DC-05）：入口单点归一——
  // value（config.conditions）经导入/恢复链可为 null/undefined/非数组，
  // 回落空清单安全渲染；合法数组同引用零回归。
  const conditions = Array.isArray(value) ? value : EMPTY_CONDITIONS;

  const nodeExists = useCallback(
    (nodeId: string) => allNodes.some((n) => n.data.node_id === nodeId),
    [allNodes],
  );

  const updateCondition = useCallback(
    (index: number, patch: Partial<BranchCondition>) => {
      const next = conditions.map((c, i) => (i === index ? { ...c, ...patch } : c));
      onChange(next);
    },
    [conditions, onChange],
  );

  const deleteCondition = useCallback(
    (index: number) => {
      onChange(conditions.filter((_, i) => i !== index));
    },
    [conditions, onChange],
  );

  const addCondition = useCallback(() => {
    onChange([...conditions, { target_node_id: "", expression: "" }]);
  }, [conditions, onChange]);

  return (
    <>
      <style>{`
        .wfp-bce-section { margin-bottom: 12px; }
        .wfp-bce-title {
          font-size: 11px; font-weight: 600;
          color: ${isDark ? darkTokens.text.secondary : "#666"};
          margin-bottom: 4px;
        }
        .wfp-bce-cond {
          border: 1px solid ${isDark ? darkTokens.surface.divider : "#e0e0e0"};
          border-radius: 5px;
          padding: 8px;
          margin-bottom: 6px;
        }
        .wfp-bce-row {
          display: flex; gap: 6px; align-items: center; margin-bottom: 4px;
        }
        .wfp-bce-select, .wfp-bce-input {
          box-sizing: border-box; padding: 5px 8px;
          font-size: 12px; font-family: inherit;
          color: ${isDark ? darkTokens.text.primary : "#333"};
          background: ${isDark ? darkTokens.surface.bg : "#fff"};
          border: 1px solid ${isDark ? darkTokens.surface.divider : "#e0e0e0"};
          border-radius: 4px; outline: none;
        }
        .wfp-bce-select { flex: 1; min-width: 0; }
        .wfp-bce-input { flex: 1; min-width: 0; }
        .wfp-bce-select:focus, .wfp-bce-input:focus {
          border-color: ${isDark ? darkTokens.surface.borderStrong : "#bdbdbd"};
        }
        .wfp-bce-textarea {
          width: 100%; box-sizing: border-box; padding: 5px 8px;
          font-size: 12px; font-family: inherit;
          color: ${isDark ? darkTokens.text.primary : "#333"};
          background: ${isDark ? darkTokens.surface.bg : "#fff"};
          border: 1px solid ${isDark ? darkTokens.surface.divider : "#e0e0e0"};
          border-radius: 4px; outline: none; resize: vertical;
          min-height: 48px;
        }
        .wfp-bce-textarea:focus {
          border-color: ${isDark ? darkTokens.surface.borderStrong : "#bdbdbd"};
        }
        .wfp-bce-del {
          flex-shrink: 0; padding: 5px 7px; font-size: 11px; font-family: inherit;
          color: ${isDark ? darkTokens.danger : "#d32f2f"};
          background: transparent;
          border: 1px solid ${isDark ? darkTokens.surface.divider : "#e0e0e0"};
          border-radius: 4px; cursor: pointer;
        }
        .wfp-bce-del:hover { background: ${isDark ? "rgba(255,107,107,0.12)" : "#fdecea"}; }
        .wfp-bce-warn {
          font-size: 10px; color: ${isDark ? "#ffa726" : "#f57c00"};
          margin-top: 2px;
        }
        .wfp-bce-label {
          font-size: 10px; font-weight: 500;
          color: ${isDark ? darkTokens.text.hint : "#9a9a9a"};
          margin-bottom: 2px; display: block;
        }
        .wfp-bce-add-btn {
          padding: 5px 10px; font-size: 11px; font-family: inherit;
          color: ${isDark ? darkTokens.text.primary : "#333"};
          background: ${isDark ? darkTokens.surface.bgHover : "#f5f5f5"};
          border: 1px solid ${isDark ? darkTokens.surface.divider : "#e0e0e0"};
          border-radius: 4px; cursor: pointer;
        }
        .wfp-bce-add-btn:hover { background: ${isDark ? darkTokens.surface.bgActive : "#efefef"}; }
        .wfp-bce-empty {
          font-size: 11px; color: ${isDark ? darkTokens.text.hint : "#999"};
          margin-bottom: 4px;
        }
      `}</style>
      <div className="wfp-bce-section">
        <div className="wfp-bce-title">分支条件</div>
        {conditions.length === 0 && (
          <div className="wfp-bce-empty">至少需要一条分支条件</div>
        )}
        {conditions.map((cond, idx) => (
          <div key={idx} className="wfp-bce-cond">
            <div className="wfp-bce-row">
              <select
                className="wfp-bce-select"
                value={cond.target_node_id}
                onChange={(e) => updateCondition(idx, { target_node_id: e.target.value })}
              >
                <option value="">— 选择目标节点 —</option>
                {allNodes.map((n) => (
                  <option key={n.data.node_id} value={n.data.node_id}>
                    {n.data.name}
                  </option>
                ))}
              </select>
              <button
                type="button"
                className="wfp-bce-del"
                onClick={() => deleteCondition(idx)}
              >✕</button>
            </div>
            {(cond.target_node_id ?? "").length > 0 && !nodeExists(cond.target_node_id ?? "") && (
              <div className="wfp-bce-warn">目标节点不存在</div>
            )}
            <label>
              <span className="wfp-bce-label">条件表达式</span>
              <textarea
                className="wfp-bce-textarea"
                value={cond.expression}
                placeholder="如：draft.score >= 80"
                onChange={(e) => updateCondition(idx, { expression: e.target.value })}
                rows={2}
              />
            </label>
            {(cond.expression ?? "").length === 0 && (
              <div className="wfp-bce-warn">条件表达式为空，将始终匹配</div>
            )}
            <label>
              <span className="wfp-bce-label">标签（可选）</span>
              <input
                type="text"
                className="wfp-bce-input"
                value={cond.label ?? ""}
                placeholder="如：高分路径"
                onChange={(e) => updateCondition(idx, { label: e.target.value })}
              />
            </label>
          </div>
        ))}
        <button
          type="button"
          className="wfp-bce-add-btn"
          onClick={addCondition}
        >+ 添加分支条件</button>
      </div>
    </>
  );
}