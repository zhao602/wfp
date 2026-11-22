// =============================================================================
// 通用键值对映射编辑器（src/canvas/KeyValueMapEditor.tsx）
//
// 职责：编辑 Record<string, string> 类型的映射字段，以表格形式呈现键列与值列，
//       支持新增/删除/修改条目。值列按 mode 分派渲染：自由文本、变量引用选择器、
//       类型名下拉选择。
// 设计意图：通用可复用组件，供 Trigger/Agent/Tool/SubWorkflow 等表单复用。
// =============================================================================

import { useCallback, useState } from "react";
import { useThemeMode, darkTokens } from "../theme/themeMode";
import type { WorkflowCanvasState } from "./canvasTypes";
import {
  trimKey,
  validateKeyUnique,
  validateKeyNotExcluded,
} from "./mapEditorShared";
import VariableReferenceSelector from "./VariableReferenceSelector";

/** 值列渲染模式（判别联合） */
export type ValueColumnMode =
  | { kind: "freeText" }
  | { kind: "variableReference"; canvasState: WorkflowCanvasState; targetNodeId?: string }
  | { kind: "typeNameSelect"; options: readonly string[] };

/** KeyValueMapEditor 属性 */
export interface KeyValueMapEditorProps {
  /** 当前映射值 */
  value: Record<string, string>;
  /** 值变更回调 */
  onChange: (value: Record<string, string>) => void;
  /** 键列标签 */
  keyLabel: string;
  /** 值列标签 */
  valueLabel: string;
  /** 值列渲染模式 */
  valueColumnMode: ValueColumnMode;
  /** 互斥键名集合（INV-4） */
  excludedKeys?: ReadonlySet<string>;
  /** 空值警告文案 */
  emptyValueWarning?: string;
  /** 区域标题 */
  sectionTitle?: string;
  /** 方向标注（如"父工作流 → 子工作流"） */
  directionHint?: string;
}

/** 错误信息映射：行索引 → 错误文案 */
type ErrorMap = Record<number, string>;

/** 通用键值对映射编辑器 */
export default function KeyValueMapEditor({
  value,
  onChange,
  keyLabel,
  valueLabel,
  valueColumnMode,
  excludedKeys,
  emptyValueWarning,
  sectionTitle,
  directionHint,
}: KeyValueMapEditorProps): JSX.Element {
  const themeMode = useThemeMode();
  const isDark = themeMode === "dark";
  const [errors, setErrors] = useState<ErrorMap>({});
  const [newKey, setNewKey] = useState("");
  const [newValue, setNewValue] = useState("");

  // L-15（fix_agent_null_length DC-05）：value（mapping 类配置）经导入/恢复链
  // 可为 null/undefined（Object.entries(null) 抛 TypeError），nullish 回落空映射；
  // 合法对象同值零回归。
  const entries = Object.entries(value ?? {});
  const existingKeys = entries.map(([k]) => k);

  /** 回写映射：从条目数组构造 Record */
  const writeMap = useCallback(
    (items: Array<[string, string]>) => {
      const map: Record<string, string> = {};
      for (const [k, v] of items) {
        map[k] = v;
      }
      onChange(map);
    },
    [onChange],
  );

  /** 更新指定行的键 */
  const updateKey = useCallback(
    (index: number, rawKey: string) => {
      const trimmed = trimKey(rawKey);
      if (!validateKeyUnique(existingKeys, trimmed, index)) {
        setErrors((prev) => ({ ...prev, [index]: "键名已存在" }));
        return;
      }
      if (excludedKeys && !validateKeyNotExcluded(trimmed, excludedKeys)) {
        setErrors((prev) => ({ ...prev, [index]: "该键名已在其他映射中定义" }));
        return;
      }
      setErrors((prev) => {
        const next = { ...prev };
        delete next[index];
        return next;
      });
      const items = entries.map(([k, v], i) =>
        i === index ? [trimmed, v] : [k, v],
      ) as Array<[string, string]>;
      writeMap(items);
    },
    [entries, existingKeys, excludedKeys, writeMap],
  );

  /** 更新指定行的值 */
  const updateValue = useCallback(
    (index: number, val: string) => {
      const items = entries.map(([k, v], i) =>
        i === index ? [k, val] : [k, v],
      ) as Array<[string, string]>;
      writeMap(items);
    },
    [entries, writeMap],
  );

  /** 删除指定行 */
  const deleteRow = useCallback(
    (index: number) => {
      const items = entries.filter((_, i) => i !== index);
      setErrors((prev) => {
        const next: ErrorMap = {};
        for (const [k, v] of Object.entries(prev)) {
          const numK = Number(k);
          if (numK < index) next[numK] = v;
          else if (numK > index) next[numK - 1] = v;
        }
        return next;
      });
      writeMap(items);
    },
    [entries, writeMap],
  );

  /** 新增条目 */
  const addEntry = useCallback(() => {
    const trimmed = trimKey(newKey);
    if (trimmed.length === 0) {
      setErrors((prev) => ({ ...prev, [-1]: "键名不能为空" }));
      return;
    }
    if (!validateKeyUnique(existingKeys, trimmed)) {
      setErrors((prev) => ({ ...prev, [-1]: "键名已存在" }));
      return;
    }
    if (excludedKeys && !validateKeyNotExcluded(trimmed, excludedKeys)) {
      setErrors((prev) => ({ ...prev, [-1]: "该键名已在其他映射中定义" }));
      return;
    }
    setErrors((prev) => {
      const next = { ...prev };
      delete next[-1];
      return next;
    });
    writeMap([...entries, [trimmed, newValue]]);
    setNewKey("");
    setNewValue("");
  }, [newKey, newValue, entries, existingKeys, excludedKeys, writeMap]);

  return (
    <>
      <style>{`
        .wfp-kvme-section {
          max-width: 100%;
          overflow-x: hidden;
          box-sizing: border-box;
          margin-bottom: 14px;
          padding: 10px 12px;
          border: 1px solid ${isDark ? darkTokens.surface.divider : "#ececec"};
          border-radius: 6px;
          background: ${isDark ? darkTokens.surface.bgHover : "#fafafa"};
        }
        /* 标题与面板 .wfp-ncp-section-label 同族：小字距标题 + 细线延伸，
           使 KVME 子区在节点配置面板内与 Agent/VKB 分节标题观感一致 */
        .wfp-kvme-title {
          display: flex;
          align-items: center;
          gap: 8px;
          font-size: 10px;
          font-weight: 600;
          color: ${isDark ? darkTokens.text.secondary : "#6b6b6b"};
          margin-bottom: 8px;
          letter-spacing: 0.08em;
          user-select: none;
        }
        .wfp-kvme-title::after {
          content: "";
          flex: 1;
          height: 1px;
          background: ${isDark ? darkTokens.surface.divider : "#ececec"};
        }
        .wfp-kvme-direction {
          font-size: 11px;
          font-weight: 500;
          color: ${isDark ? darkTokens.text.hint : "#9a9a9a"};
          margin-bottom: 8px;
          letter-spacing: 0.02em;
        }
        .wfp-kvme-col-header {
          display: flex;
          gap: 8px;
          align-items: center;
          margin-bottom: 6px;
        }
        .wfp-kvme-col-label {
          flex: 1;
          min-width: 0;
          font-size: 10px;
          font-weight: 500;
          color: ${isDark ? darkTokens.text.hint : "#9a9a9a"};
          letter-spacing: 0.06em;
          text-transform: uppercase;
        }
        .wfp-kvme-col-spacer {
          flex-shrink: 0;
          width: 32px;
        }
        .wfp-kvme-row {
          display: flex;
          gap: 8px;
          align-items: flex-start;
          width: 100%;
          margin-bottom: 6px;
        }
        .wfp-kvme-key {
          flex: 1;
          min-width: 0;
          width: 100%;
          box-sizing: border-box;
          padding: 7px 10px;
          font-size: 13px;
          font-weight: 500;
          font-family: inherit;
          color: ${isDark ? darkTokens.text.primary : "#333333"};
          background-color: ${isDark ? darkTokens.surface.bg : "#ffffff"};
          border: 1px solid ${isDark ? darkTokens.surface.divider : "#e0e0e0"};
          border-radius: 5px;
          outline: none;
          transition: background-color 120ms ease, border-color 120ms ease;
        }
        .wfp-kvme-key:hover {
          background-color: ${isDark ? darkTokens.surface.bgHover : "#fafafa"};
        }
        .wfp-kvme-key:focus {
          border-color: ${isDark ? darkTokens.surface.borderStrong : "#bdbdbd"};
          background-color: ${isDark ? darkTokens.surface.bg : "#ffffff"};
        }
        .wfp-kvme-key::placeholder {
          color: ${isDark ? darkTokens.text.hint : "#b0b0b0"};
        }
        .wfp-kvme-val {
          flex: 1;
          min-width: 0;
        }
        .wfp-kvme-del {
          flex-shrink: 0;
          width: 32px;
          height: 32px;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          font-size: 14px;
          font-family: inherit;
          color: ${isDark ? darkTokens.text.hint : "#999999"};
          background: transparent;
          border: 1px solid ${isDark ? darkTokens.surface.divider : "#e0e0e0"};
          border-radius: 5px;
          cursor: pointer;
          outline: none;
          transition: color 120ms ease, background-color 120ms ease, border-color 120ms ease;
        }
        .wfp-kvme-del:hover {
          color: ${isDark ? darkTokens.danger : "#d32f2f"};
          background: ${isDark ? "rgba(255,107,107,0.10)" : "#fdecea"};
          border-color: ${isDark ? darkTokens.danger : "#d32f2f"};
        }
        .wfp-kvme-del:active {
          background: ${isDark ? "rgba(255,107,107,0.18)" : "#f8d7da"};
        }
        .wfp-kvme-err {
          font-size: 11px;
          line-height: 1.45;
          color: ${isDark ? darkTokens.danger : "#d32f2f"};
          margin-bottom: 4px;
          padding: 4px 8px;
          background: ${isDark ? "rgba(255,107,107,0.08)" : "#fdecea"};
          border-left: 3px solid ${isDark ? darkTokens.danger : "#d32f2f"};
          border-radius: 0 3px 3px 0;
        }
        .wfp-kvme-warn {
          font-size: 11px;
          line-height: 1.45;
          color: ${isDark ? "#ffa726" : "#f57c00"};
          margin-bottom: 4px;
          padding: 4px 8px;
          background: ${isDark ? "rgba(255,167,38,0.08)" : "#fff3e0"};
          border-left: 3px solid ${isDark ? "#ffa726" : "#f57c00"};
          border-radius: 0 3px 3px 0;
        }
        .wfp-kvme-add-row {
          display: flex;
          gap: 8px;
          align-items: flex-start;
          width: 100%;
          margin-top: 8px;
          padding-top: 8px;
          border-top: 1px dashed ${isDark ? darkTokens.surface.divider : "#e0e0e0"};
        }
        .wfp-kvme-add-btn {
          flex-shrink: 0;
          display: inline-flex;
          align-items: center;
          gap: 4px;
          padding: 7px 14px;
          font-size: 12px;
          font-weight: 600;
          font-family: inherit;
          color: ${isDark ? darkTokens.text.primary : "#1a1a1a"};
          background: ${isDark ? darkTokens.surface.bgActive : "#f0f0f0"};
          border: 1px solid ${isDark ? darkTokens.surface.border : "#cccccc"};
          border-radius: 5px;
          cursor: pointer;
          outline: none;
          transition: background-color 120ms ease, border-color 120ms ease, box-shadow 120ms ease;
        }
        .wfp-kvme-add-btn:hover {
          background: ${isDark ? darkTokens.surface.border : "#e5e5e5"};
          border-color: ${isDark ? darkTokens.surface.borderStrong : "#bdbdbd"};
        }
        .wfp-kvme-add-btn:active {
          background: ${isDark ? darkTokens.surface.borderStrong : "#dcdcdc"};
        }
        .wfp-kvme-add-btn:focus-visible {
          box-shadow: 0 0 0 2px ${isDark ? darkTokens.text.focus : "#bdbdbd"};
        }
        .wfp-kvme-add-icon {
          font-size: 14px;
          font-weight: 700;
          line-height: 1;
        }
        .wfp-kvme-empty {
          font-size: 12px;
          color: ${isDark ? darkTokens.text.hint : "#9a9a9a"};
          text-align: center;
          padding: 12px 0;
          border: 1px dashed ${isDark ? darkTokens.surface.divider : "#e0e0e0"};
          border-radius: 5px;
          margin-bottom: 6px;
        }
      `}</style>
      <div className="wfp-kvme-section">
        {sectionTitle && <div className="wfp-kvme-title">{sectionTitle}</div>}
        {directionHint && <div className="wfp-kvme-direction">{directionHint}</div>}
        {entries.length === 0 && (
          <div className="wfp-kvme-empty">暂无映射条目</div>
        )}
        {entries.length > 0 && (
          <div className="wfp-kvme-col-header">
            <span className="wfp-kvme-col-label">{keyLabel}</span>
            <span className="wfp-kvme-col-label">{valueLabel}</span>
            <span className="wfp-kvme-col-spacer" />
          </div>
        )}
        {entries.map(([k, v], idx) => (
          <div key={idx}>
            {errors[idx] && <div className="wfp-kvme-err">{errors[idx]}</div>}
            <div className="wfp-kvme-row">
              <input
                type="text"
                className="wfp-kvme-key"
                value={k}
                placeholder={keyLabel}
                onChange={(e) => updateKey(idx, e.target.value)}
              />
              <div className="wfp-kvme-val">
                {valueColumnMode.kind === "freeText" && (
                  <input
                    type="text"
                    className="wfp-kvme-key"
                    value={v}
                    placeholder={valueLabel}
                    onChange={(e) => updateValue(idx, e.target.value)}
                  />
                )}
                {valueColumnMode.kind === "variableReference" && (
                  <VariableReferenceSelector
                    canvasState={valueColumnMode.canvasState}
                    targetNodeId={valueColumnMode.targetNodeId}
                    value={v}
                    onChange={(val) => updateValue(idx, val)}
                  />
                )}
                {valueColumnMode.kind === "typeNameSelect" && (
                  <select
                    className="wfp-kvme-key"
                    value={v}
                    onChange={(e) => updateValue(idx, e.target.value)}
                  >
                    <option value="">—</option>
                    {valueColumnMode.options.map((opt) => (
                      <option key={opt} value={opt}>{opt}</option>
                    ))}
                  </select>
                )}
              </div>
              <button
                type="button"
                className="wfp-kvme-del"
                onClick={() => deleteRow(idx)}
                aria-label="删除此条目"
              >
                ✕
              </button>
            </div>
            {v.length === 0 && emptyValueWarning && (
              <div className="wfp-kvme-warn">{emptyValueWarning}</div>
            )}
          </div>
        ))}
        {errors[-1] && <div className="wfp-kvme-err">{errors[-1]}</div>}
        <div className="wfp-kvme-add-row">
          <input
            type="text"
            className="wfp-kvme-key"
            value={newKey}
            placeholder={keyLabel}
            onChange={(e) => setNewKey(e.target.value)}
          />
          <div className="wfp-kvme-val">
            {valueColumnMode.kind === "freeText" && (
              <input
                type="text"
                className="wfp-kvme-key"
                value={newValue}
                placeholder={valueLabel}
                onChange={(e) => setNewValue(e.target.value)}
              />
            )}
            {valueColumnMode.kind === "variableReference" && (
              <VariableReferenceSelector
                canvasState={valueColumnMode.canvasState}
                targetNodeId={valueColumnMode.targetNodeId}
                value={newValue}
                onChange={setNewValue}
              />
            )}
            {valueColumnMode.kind === "typeNameSelect" && (
              <select
                className="wfp-kvme-key"
                value={newValue}
                onChange={(e) => setNewValue(e.target.value)}
              >
                <option value="">—</option>
                {valueColumnMode.options.map((opt) => (
                  <option key={opt} value={opt}>{opt}</option>
                ))}
              </select>
            )}
          </div>
          <button
            type="button"
            className="wfp-kvme-add-btn"
            onClick={addEntry}
          >
            <span className="wfp-kvme-add-icon">＋</span>
            添加
          </button>
        </div>
      </div>
    </>
  );
}