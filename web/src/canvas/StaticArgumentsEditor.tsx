// =============================================================================
// 静态参数编辑器（src/canvas/StaticArgumentsEditor.tsx）
//
// 职责：编辑 Record<string, unknown> 类型的静态参数字段，值列含类型选择器
//       （string/number/boolean），值经 parseStaticValue 缩窄后回写（INV-8）。
// =============================================================================

import { useCallback, useState } from "react";
import { useThemeMode, darkTokens } from "../theme/themeMode";
import {
  trimKey,
  validateKeyUnique,
  validateKeyNotExcluded,
  parseStaticValue,
  inferStaticValueType,
  STATIC_VALUE_TYPES,
  type StaticValueType,
} from "./mapEditorShared";

/** StaticArgumentsEditor 属性 */
export interface StaticArgumentsEditorProps {
  /** 当前静态参数值 */
  value: Record<string, unknown>;
  /** 值变更回调 */
  onChange: (value: Record<string, unknown>) => void;
  /** 互斥键名集合（INV-4） */
  excludedKeys?: ReadonlySet<string>;
  /** 键列标签 */
  keyLabel: string;
  /** 区域标题 */
  sectionTitle?: string;
}

type ErrorMap = Record<number, string>;

/** 静态参数编辑器 */
export default function StaticArgumentsEditor({
  value,
  onChange,
  excludedKeys,
  keyLabel,
  sectionTitle,
}: StaticArgumentsEditorProps): JSX.Element {
  const themeMode = useThemeMode();
  const isDark = themeMode === "dark";
  const [errors, setErrors] = useState<ErrorMap>({});
  const [newKey, setNewKey] = useState("");
  const [newType, setNewType] = useState<StaticValueType>("string");
  const [newVal, setNewVal] = useState("");

  // L-15（fix_agent_null_length DC-05）：value（static_arguments 等）经导入/恢复链
  // 可为 null/undefined（Object.entries(null) 抛 TypeError），nullish 回落空映射；
  // 合法对象同值零回归。
  const entries = Object.entries(value ?? {});
  const existingKeys = entries.map(([k]) => k);

  const writeMap = useCallback(
    (items: Array<[string, unknown]>) => {
      const map: Record<string, unknown> = {};
      for (const [k, v] of items) {
        map[k] = v;
      }
      onChange(map);
    },
    [onChange],
  );

  const updateKey = useCallback(
    (index: number, rawKey: string) => {
      const trimmed = trimKey(rawKey);
      if (!validateKeyUnique(existingKeys, trimmed, index)) {
        setErrors((prev) => ({ ...prev, [index]: "参数名已存在" }));
        return;
      }
      if (excludedKeys && !validateKeyNotExcluded(trimmed, excludedKeys)) {
        setErrors((prev) => ({ ...prev, [index]: "该参数名已在动态映射中定义" }));
        return;
      }
      setErrors((prev) => {
        const next = { ...prev };
        delete next[index];
        return next;
      });
      const items = entries.map(([k, v], i) =>
        i === index ? [trimmed, v] : [k, v],
      ) as Array<[string, unknown]>;
      writeMap(items);
    },
    [entries, existingKeys, excludedKeys, writeMap],
  );

  const updateValue = useCallback(
    (index: number, raw: string, type: StaticValueType) => {
      const parsed = parseStaticValue(raw, type);
      if (parsed === null) {
        setErrors((prev) => ({
          ...prev,
          [index]: type === "number" ? "请输入有效数字" : "无效的布尔值",
        }));
        return;
      }
      setErrors((prev) => {
        const next = { ...prev };
        delete next[index];
        return next;
      });
      const items = entries.map(([k, v], i) =>
        i === index ? [k, parsed] : [k, v],
      ) as Array<[string, unknown]>;
      writeMap(items);
    },
    [entries, writeMap],
  );

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

  const addEntry = useCallback(() => {
    const trimmed = trimKey(newKey);
    if (trimmed.length === 0) {
      setErrors((prev) => ({ ...prev, [-1]: "参数名不能为空" }));
      return;
    }
    if (!validateKeyUnique(existingKeys, trimmed)) {
      setErrors((prev) => ({ ...prev, [-1]: "参数名已存在" }));
      return;
    }
    if (excludedKeys && !validateKeyNotExcluded(trimmed, excludedKeys)) {
      setErrors((prev) => ({ ...prev, [-1]: "该参数名已在动态映射中定义" }));
      return;
    }
    const parsed = parseStaticValue(newVal, newType);
    if (parsed === null) {
      setErrors((prev) => ({
        ...prev,
        [-1]: newType === "number" ? "请输入有效数字" : "无效的布尔值",
      }));
      return;
    }
    setErrors((prev) => {
      const next = { ...prev };
      delete next[-1];
      return next;
    });
    writeMap([...entries, [trimmed, parsed]]);
    setNewKey("");
    setNewVal("");
  }, [newKey, newVal, newType, entries, existingKeys, excludedKeys, writeMap]);

  /** 渲染值输入控件 */
  const renderValueInput = (
    type: StaticValueType,
    currentVal: unknown,
    onRawChange: (raw: string, type: StaticValueType) => void,
  ) => {
    if (type === "boolean") {
      const boolVal = currentVal === true ? "true" : currentVal === false ? "false" : "";
      return (
        <select
          className="wfp-sae-input"
          value={boolVal}
          onChange={(e) => onRawChange(e.target.value, "boolean")}
        >
          <option value="">—</option>
          <option value="true">true</option>
          <option value="false">false</option>
        </select>
      );
    }
    return (
      <input
        type={type === "number" ? "number" : "text"}
        className="wfp-sae-input"
        value={typeof currentVal === "string" || typeof currentVal === "number" ? String(currentVal) : ""}
        onChange={(e) => onRawChange(e.target.value, type)}
      />
    );
  };

  return (
    <>
      <style>{`
        .wfp-sae-section { margin-bottom: 12px; }
        .wfp-sae-title {
          font-size: 11px; font-weight: 600;
          color: ${isDark ? darkTokens.text.secondary : "#666"};
          margin-bottom: 4px;
        }
        .wfp-sae-row {
          display: flex; gap: 4px; align-items: flex-start; margin-bottom: 4px;
        }
        .wfp-sae-key, .wfp-sae-input, .wfp-sae-type {
          box-sizing: border-box; padding: 5px 6px;
          font-size: 12px; font-family: inherit;
          color: ${isDark ? darkTokens.text.primary : "#333"};
          background: ${isDark ? darkTokens.surface.bg : "#fff"};
          border: 1px solid ${isDark ? darkTokens.surface.divider : "#e0e0e0"};
          border-radius: 4px; outline: none;
        }
        .wfp-sae-key { flex: 1; min-width: 0; }
        .wfp-sae-type { flex-shrink: 0; width: 70px; }
        .wfp-sae-input { flex: 1; min-width: 0; }
        .wfp-sae-key:focus, .wfp-sae-input:focus, .wfp-sae-type:focus {
          border-color: ${isDark ? darkTokens.surface.borderStrong : "#bdbdbd"};
        }
        .wfp-sae-key::placeholder, .wfp-sae-input::placeholder {
          color: ${isDark ? darkTokens.text.hint : "#b0b0b0"};
        }
        .wfp-sae-del {
          flex-shrink: 0; padding: 5px 7px; font-size: 11px; font-family: inherit;
          color: ${isDark ? darkTokens.danger : "#d32f2f"};
          background: transparent;
          border: 1px solid ${isDark ? darkTokens.surface.divider : "#e0e0e0"};
          border-radius: 4px; cursor: pointer;
        }
        .wfp-sae-del:hover { background: ${isDark ? "rgba(255,107,107,0.12)" : "#fdecea"}; }
        .wfp-sae-err {
          font-size: 10px; color: ${isDark ? darkTokens.danger : "#d32f2f"};
          margin-bottom: 2px; margin-left: 2px;
        }
        .wfp-sae-add-row { display: flex; gap: 4px; align-items: flex-start; margin-top: 4px; }
        .wfp-sae-add-btn {
          flex-shrink: 0; padding: 5px 8px; font-size: 11px; font-family: inherit;
          color: ${isDark ? darkTokens.text.primary : "#333"};
          background: ${isDark ? darkTokens.surface.bgHover : "#f5f5f5"};
          border: 1px solid ${isDark ? darkTokens.surface.divider : "#e0e0e0"};
          border-radius: 4px; cursor: pointer;
        }
        .wfp-sae-add-btn:hover { background: ${isDark ? darkTokens.surface.bgActive : "#efefef"}; }
        .wfp-sae-empty {
          font-size: 11px; color: ${isDark ? darkTokens.text.hint : "#999"};
          margin-bottom: 4px;
        }
      `}</style>
      <div className="wfp-sae-section">
        {sectionTitle && <div className="wfp-sae-title">{sectionTitle}</div>}
        {entries.length === 0 && <div className="wfp-sae-empty">暂无静态参数</div>}
        {entries.map(([k, v], idx) => {
          const vType = inferStaticValueType(v);
          return (
            <div key={idx}>
              {errors[idx] && <div className="wfp-sae-err">{errors[idx]}</div>}
              <div className="wfp-sae-row">
                <input
                  type="text"
                  className="wfp-sae-key"
                  value={k}
                  placeholder={keyLabel}
                  onChange={(e) => updateKey(idx, e.target.value)}
                />
                <select
                  className="wfp-sae-type"
                  value={vType}
                  onChange={(e) => {
                    const newType = e.target.value as StaticValueType;
                    const raw = newType === "boolean" ? "" : String(v ?? "");
                    updateValue(idx, raw, newType);
                  }}
                >
                  {STATIC_VALUE_TYPES.map((t) => (
                    <option key={t} value={t}>{t}</option>
                  ))}
                </select>
                {renderValueInput(vType, v, (raw, type) => updateValue(idx, raw, type))}
                <button
                  type="button"
                  className="wfp-sae-del"
                  onClick={() => deleteRow(idx)}
                >✕</button>
              </div>
            </div>
          );
        })}
        {errors[-1] && <div className="wfp-sae-err">{errors[-1]}</div>}
        <div className="wfp-sae-add-row">
          <input
            type="text"
            className="wfp-sae-key"
            value={newKey}
            placeholder={keyLabel}
            onChange={(e) => setNewKey(e.target.value)}
          />
          <select
            className="wfp-sae-type"
            value={newType}
            onChange={(e) => setNewType(e.target.value as StaticValueType)}
          >
            {STATIC_VALUE_TYPES.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
          {renderValueInput(newType, newVal, (raw) => setNewVal(raw))}
          <button
            type="button"
            className="wfp-sae-add-btn"
            onClick={addEntry}
          >添加</button>
        </div>
      </div>
    </>
  );
}