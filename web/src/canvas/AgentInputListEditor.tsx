// =============================================================================
// Agent 输入选择集编辑器（src/canvas/AgentInputListEditor.tsx）
//
// 职责：编辑 AgentNodeConfiguration.inputs 三态输入选择集——
//       undefined（隐式注入全部上游命名输出）/ []（显式无输入）/ 非空清单（显式收窄）。
//       每条目 = 引用表达式值列（复用 VariableReferenceSelector，props 零改动）
//       + 可选别名输入框 + 删除操作。
// 设计意图（对应需求规格 FR-8 / AC-01 场景 3、任务 T5.3）：
//   1. 三态行为明示：隐式空态提示注入语义、显式空清单独立文案（OP-03 风险缓解）；
//   2. 节点内别名校验：别名与别名、别名与另一条目缺省输出名冲突时内联拒绝保存并提示；
//   3. 样式遵循 darkTokens 双轨 + wfp- 前缀规范（与既有控件风格统一）。
// =============================================================================

import { useCallback, useState } from "react";
import { useThemeMode, darkTokens } from "../theme/themeMode";
import type { WorkflowCanvasState } from "./canvasTypes";
import type { AgentInputEntry } from "../workflow-definition/definitionTypes";
import VariableReferenceSelector from "./VariableReferenceSelector";

/** AgentInputListEditor 属性 */
export interface AgentInputListEditorProps {
  /** 当前画布状态（供引用选择器收集上游命名输出候选） */
  canvasState: WorkflowCanvasState;
  /** 目标节点标识：传入时候选收窄为上游节点 */
  targetNodeId?: string;
  /** 输入选择集三态值：undefined = 隐式；[] = 显式无输入；非空 = 显式收窄 */
  inputs?: AgentInputEntry[];
  /** 值变更回调（三态感知） */
  onChange: (inputs: AgentInputEntry[] | undefined) => void;
}

/** 条目解析名：别名优先，缺省取引用表达式首段（与后端规则⑧判重口径一致） */
function resolvedEntryName(entry: AgentInputEntry): string {
  const alias = entry.alias?.trim() ?? "";
  if (alias.length > 0) {
    return alias;
  }
  /** 返回引用表达式首段（缺省名）；L-11：reference 运行时可为 null，宽容回落空串 */
  return (entry.reference ?? "").trim().split(".")[0] ?? "";
}

/** 计算解析名冲突行集合（名称为空的条目不参与判重） */
function findConflictIndexes(entries: AgentInputEntry[]): Set<number> {
  const conflicts = new Set<number>();
  const firstSeen = new Map<string, number>();
  entries.forEach((entry, index) => {
    const name = resolvedEntryName(entry);
    if (name.length === 0) {
      return;
    }
    const first = firstSeen.get(name);
    if (first === undefined) {
      firstSeen.set(name, index);
    } else {
      conflicts.add(first);
      conflicts.add(index);
    }
  });
  return conflicts;
}

/** 行索引 → 错误文案 */
type ErrorMap = Record<number, string>;

/** Agent 输入选择集编辑器 */
export default function AgentInputListEditor({
  canvasState,
  targetNodeId,
  inputs,
  onChange,
}: AgentInputListEditorProps): JSX.Element {
  const themeMode = useThemeMode();
  const isDark = themeMode === "dark";
  const [errors, setErrors] = useState<ErrorMap>({});

  // L-11（fix_agent_null_length DC-05）：入口单点归一——
  // inputs 运行时可为 null/undefined/非数组（类型面收紧、运行时宽容），
  // 非数组一律回落空清单；合法数组同引用零回归。
  const entries = Array.isArray(inputs) ? inputs : [];

  const clearError = useCallback((index: number) => {
    setErrors((prev) => {
      if (!(index in prev)) {
        return prev;
      }
      const next = { ...prev };
      delete next[index];
      return next;
    });
  }, []);

  /** 更新指定行条目：被改行落入冲突集合时内联拒绝保存（AC-01 场景 3） */
  const updateEntry = useCallback(
    (index: number, patch: AgentInputEntry) => {
      const next = entries.map((entry, i) => (i === index ? patch : entry));
      if (findConflictIndexes(next).has(index)) {
        setErrors((prev) => ({
          ...prev,
          [index]: `条目名冲突：${resolvedEntryName(patch)}（别名与缺省名的冲突域为本节点条目名集合）`,
        }));
        return;
      }
      clearError(index);
      onChange(next);
    },
    [entries, onChange, clearError],
  );

  /** 删除指定行：清空后保持显式空清单语义（[]）；恢复隐式需显式操作 */
  const deleteEntry = useCallback(
    (index: number) => {
      const next = entries.filter((_, i) => i !== index);
      setErrors((prev) => {
        const reindexed: ErrorMap = {};
        for (const [k, v] of Object.entries(prev)) {
          const numK = Number(k);
          if (numK < index) reindexed[numK] = v;
          else if (numK > index) reindexed[numK - 1] = v;
        }
        return reindexed;
      });
      onChange(next);
    },
    [entries, onChange],
  );

  /** 追加未绑定引用条目（由隐式/空清单进入显式收窄模式） */
  const addEntry = useCallback(() => {
    onChange([...entries, { reference: "" }]);
  }, [entries, onChange]);

  // 三态判定（L-11：经 Array.isArray 口径，null 不再穿透 === undefined 判空）
  const implicitEmpty = !Array.isArray(inputs);
  const explicitEmpty = Array.isArray(inputs) && entries.length === 0;
  const hasEntries = entries.length > 0;

  return (
    <>
      <style>{`
        .wfp-aile-section {
          max-width: 100%;
          overflow-x: hidden;
          box-sizing: border-box;
          padding: 10px 12px;
          border: 1px solid ${isDark ? darkTokens.surface.divider : "#ececec"};
          border-radius: 6px;
          background: ${isDark ? darkTokens.surface.bgHover : "#fafafa"};
        }
        .wfp-aile-empty {
          font-size: 12px;
          color: ${isDark ? darkTokens.text.hint : "#9a9a9a"};
          text-align: center;
          padding: 10px 0;
          border: 1px dashed ${isDark ? darkTokens.surface.divider : "#e0e0e0"};
          border-radius: 5px;
          margin-bottom: 8px;
        }
        .wfp-aile-col-header {
          display: flex;
          gap: 8px;
          align-items: center;
          margin-bottom: 6px;
        }
        .wfp-aile-col-label {
          flex: 1;
          min-width: 0;
          font-size: 10px;
          font-weight: 500;
          color: ${isDark ? darkTokens.text.hint : "#9a9a9a"};
          letter-spacing: 0.06em;
          text-transform: uppercase;
        }
        .wfp-aile-col-spacer {
          flex-shrink: 0;
          width: 32px;
        }
        .wfp-aile-row {
          display: flex;
          gap: 8px;
          align-items: flex-start;
          width: 100%;
          margin-bottom: 6px;
        }
        .wfp-aile-ref {
          flex: 1.4;
          min-width: 0;
        }
        .wfp-aile-alias {
          flex: 1;
          min-width: 0;
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
        .wfp-aile-alias:focus {
          border-color: ${isDark ? darkTokens.surface.borderStrong : "#bdbdbd"};
        }
        .wfp-aile-alias::placeholder {
          color: ${isDark ? darkTokens.text.hint : "#b0b0b0"};
        }
        .wfp-aile-del {
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
        .wfp-aile-del:hover {
          color: ${isDark ? darkTokens.danger : "#d32f2f"};
          background: ${isDark ? "rgba(255,107,107,0.10)" : "#fdecea"};
          border-color: ${isDark ? darkTokens.danger : "#d32f2f"};
        }
        .wfp-aile-err {
          font-size: 11px;
          line-height: 1.45;
          color: ${isDark ? darkTokens.danger : "#d32f2f"};
          margin-bottom: 4px;
          padding: 4px 8px;
          background: ${isDark ? "rgba(255,107,107,0.08)" : "#fdecea"};
          border-left: 3px solid ${isDark ? darkTokens.danger : "#d32f2f"};
          border-radius: 0 3px 3px 0;
        }
        .wfp-aile-warn {
          font-size: 11px;
          line-height: 1.45;
          color: ${isDark ? "#ffa726" : "#f57c00"};
          margin-bottom: 4px;
          padding: 4px 8px;
          background: ${isDark ? "rgba(255,167,38,0.08)" : "#fff3e0"};
          border-left: 3px solid ${isDark ? "#ffa726" : "#f57c00"};
          border-radius: 0 3px 3px 0;
        }
        .wfp-aile-actions {
          display: flex;
          gap: 8px;
          align-items: center;
          flex-wrap: wrap;
          margin-top: 8px;
        }
        .wfp-aile-btn {
          display: inline-flex;
          align-items: center;
          gap: 4px;
          padding: 6px 12px;
          font-size: 12px;
          font-weight: 600;
          font-family: inherit;
          color: ${isDark ? darkTokens.text.primary : "#1a1a1a"};
          background: ${isDark ? darkTokens.surface.bgActive : "#f0f0f0"};
          border: 1px solid ${isDark ? darkTokens.surface.border : "#cccccc"};
          border-radius: 5px;
          cursor: pointer;
          outline: none;
          transition: background-color 120ms ease, border-color 120ms ease;
        }
        .wfp-aile-btn:hover {
          background: ${isDark ? darkTokens.surface.border : "#e5e5e5"};
          border-color: ${isDark ? darkTokens.surface.borderStrong : "#bdbdbd"};
        }
        .wfp-aile-btn--ghost {
          background: transparent;
          font-weight: 500;
          color: ${isDark ? darkTokens.text.hint : "#757575"};
        }
        .wfp-aile-btn--ghost:hover {
          color: ${isDark ? darkTokens.text.secondary : "#616161"};
          background: ${isDark ? darkTokens.surface.bgActive : "#f5f5f5"};
        }
      `}</style>
      <div className="wfp-aile-section">
        {implicitEmpty && (
          <>
            <div className="wfp-aile-empty">
              未配置输入引用，当前将注入全部上游命名输出
            </div>
            <div className="wfp-aile-actions">
              <button type="button" className="wfp-aile-btn" onClick={addEntry}>
                ＋ 添加输入引用
              </button>
              <button
                type="button"
                className="wfp-aile-btn wfp-aile-btn--ghost"
                onClick={() => onChange([])}
              >
                显式声明无输入
              </button>
            </div>
          </>
        )}
        {explicitEmpty && (
          <>
            <div className="wfp-aile-empty">已显式声明无输入</div>
            <div className="wfp-aile-actions">
              <button type="button" className="wfp-aile-btn" onClick={addEntry}>
                ＋ 添加输入引用
              </button>
              <button
                type="button"
                className="wfp-aile-btn wfp-aile-btn--ghost"
                onClick={() => onChange(undefined)}
              >
                恢复隐式注入
              </button>
            </div>
          </>
        )}
        {hasEntries && (
          <>
            <div className="wfp-aile-col-header">
              <span className="wfp-aile-col-label">引用表达式</span>
              <span className="wfp-aile-col-label">别名（可选）</span>
              <span className="wfp-aile-col-spacer" />
            </div>
            {entries.map((entry, index) => (
              <div key={index}>
                {errors[index] && <div className="wfp-aile-err">{errors[index]}</div>}
                <div className="wfp-aile-row">
                  <div className="wfp-aile-ref">
                    <VariableReferenceSelector
                      canvasState={canvasState}
                      targetNodeId={targetNodeId}
                      value={entry.reference ?? ""}
                      onChange={(reference) =>
                        updateEntry(index, { ...entry, reference })
                      }
                    />
                  </div>
                  <input
                    type="text"
                    className="wfp-aile-alias"
                    value={entry.alias ?? ""}
                    placeholder="别名"
                    onChange={(e) =>
                      updateEntry(index, {
                        ...entry,
                        alias: e.target.value.length > 0 ? e.target.value : undefined,
                      })
                    }
                  />
                  <button
                    type="button"
                    className="wfp-aile-del"
                    onClick={() => deleteEntry(index)}
                    aria-label="删除此条目"
                  >
                    ✕
                  </button>
                </div>
                {(entry.reference ?? "").trim().length === 0 && (
                  <div className="wfp-aile-warn">未绑定引用</div>
                )}
              </div>
            ))}
            <div className="wfp-aile-actions">
              <button type="button" className="wfp-aile-btn" onClick={addEntry}>
                ＋ 添加输入引用
              </button>
              <button
                type="button"
                className="wfp-aile-btn wfp-aile-btn--ghost"
                onClick={() => onChange(undefined)}
              >
                恢复隐式注入
              </button>
            </div>
          </>
        )}
      </div>
    </>
  );
}
