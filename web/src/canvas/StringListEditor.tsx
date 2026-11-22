// =============================================================================
// 字符串列表编辑器（src/canvas/StringListEditor.tsx）
//
// 职责：编辑 string[] 类型的列表字段（如 Agent 节点的 available_tools），
//       支持新增/删除条目，含条目唯一性校验（INV-5）。
// =============================================================================

import { useCallback, useState } from "react";
import { useThemeMode, darkTokens } from "../theme/themeMode";
import { trimKey, validateKeyUnique } from "./mapEditorShared";

/** StringListEditor 属性 */
export interface StringListEditorProps {
  /** 当前列表值 */
  value: string[];
  /** 值变更回调 */
  onChange: (value: string[]) => void;
  /** 条目标签 */
  itemLabel: string;
  /** 区域标题 */
  sectionTitle?: string;
}

/** 字符串列表编辑器 */
export default function StringListEditor({
  value,
  onChange,
  itemLabel,
  sectionTitle,
}: StringListEditorProps): JSX.Element {
  const themeMode = useThemeMode();
  const isDark = themeMode === "dark";
  const [newItem, setNewItem] = useState("");
  const [error, setError] = useState<string | null>(null);

  const deleteItem = useCallback(
    (index: number) => {
      onChange(value.filter((_, i) => i !== index));
    },
    [value, onChange],
  );

  const addItem = useCallback(() => {
    const trimmed = trimKey(newItem);
    if (trimmed.length === 0) {
      setError("不能为空");
      return;
    }
    if (!validateKeyUnique(value, trimmed)) {
      setError("工具标识已存在");
      return;
    }
    setError(null);
    onChange([...value, trimmed]);
    setNewItem("");
  }, [newItem, value, onChange]);

  return (
    <>
      <style>{`
        .wfp-sle-section {
          margin-bottom: 12px;
        }
        .wfp-sle-title {
          font-size: 11px;
          font-weight: 600;
          color: ${isDark ? darkTokens.text.secondary : "#666"};
          margin-bottom: 4px;
        }
        .wfp-sle-item {
          display: flex;
          gap: 6px;
          align-items: center;
          margin-bottom: 4px;
        }
        .wfp-sle-text {
          flex: 1;
          min-width: 0;
          box-sizing: border-box;
          padding: 5px 8px;
          font-size: 12px;
          font-family: inherit;
          color: ${isDark ? darkTokens.text.primary : "#333"};
          background: ${isDark ? darkTokens.surface.bgHover : "#f5f5f5"};
          border: 1px solid ${isDark ? darkTokens.surface.divider : "#e0e0e0"};
          border-radius: 4px;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .wfp-sle-del {
          flex-shrink: 0;
          padding: 4px 7px;
          font-size: 11px;
          font-family: inherit;
          color: ${isDark ? darkTokens.danger : "#d32f2f"};
          background: transparent;
          border: 1px solid ${isDark ? darkTokens.surface.divider : "#e0e0e0"};
          border-radius: 4px;
          cursor: pointer;
        }
        .wfp-sle-del:hover {
          background: ${isDark ? "rgba(255,107,107,0.12)" : "#fdecea"};
        }
        .wfp-sle-add-row {
          display: flex;
          gap: 6px;
          align-items: center;
          margin-top: 4px;
        }
        .wfp-sle-input {
          flex: 1;
          min-width: 0;
          box-sizing: border-box;
          padding: 5px 8px;
          font-size: 12px;
          font-family: inherit;
          color: ${isDark ? darkTokens.text.primary : "#333"};
          background: ${isDark ? darkTokens.surface.bg : "#fff"};
          border: 1px solid ${isDark ? darkTokens.surface.divider : "#e0e0e0"};
          border-radius: 4px;
          outline: none;
        }
        .wfp-sle-input:focus {
          border-color: ${isDark ? darkTokens.surface.borderStrong : "#bdbdbd"};
        }
        .wfp-sle-input::placeholder {
          color: ${isDark ? darkTokens.text.hint : "#b0b0b0"};
        }
        .wfp-sle-add-btn {
          flex-shrink: 0;
          padding: 5px 10px;
          font-size: 11px;
          font-family: inherit;
          color: ${isDark ? darkTokens.text.primary : "#333"};
          background: ${isDark ? darkTokens.surface.bgHover : "#f5f5f5"};
          border: 1px solid ${isDark ? darkTokens.surface.divider : "#e0e0e0"};
          border-radius: 4px;
          cursor: pointer;
        }
        .wfp-sle-add-btn:hover {
          background: ${isDark ? darkTokens.surface.bgActive : "#efefef"};
        }
        .wfp-sle-err {
          font-size: 10px;
          color: ${isDark ? darkTokens.danger : "#d32f2f"};
          margin-bottom: 2px;
        }
        .wfp-sle-empty {
          font-size: 11px;
          color: ${isDark ? darkTokens.text.hint : "#999"};
          margin-bottom: 4px;
        }
      `}</style>
      <div className="wfp-sle-section">
        {sectionTitle && <div className="wfp-sle-title">{sectionTitle}</div>}
        {value.length === 0 && (
          <div className="wfp-sle-empty">暂无条目</div>
        )}
        {value.map((item, idx) => (
          <div key={idx} className="wfp-sle-item">
            <span className="wfp-sle-text">{item}</span>
            <button
              type="button"
              className="wfp-sle-del"
              onClick={() => deleteItem(idx)}
            >
              ✕
            </button>
          </div>
        ))}
        {error && <div className="wfp-sle-err">{error}</div>}
        <div className="wfp-sle-add-row">
          <input
            type="text"
            className="wfp-sle-input"
            value={newItem}
            placeholder={itemLabel}
            onChange={(e) => { setNewItem(e.target.value); setError(null); }}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                addItem();
              }
            }}
          />
          <button
            type="button"
            className="wfp-sle-add-btn"
            onClick={addItem}
          >
            添加
          </button>
        </div>
      </div>
    </>
  );
}