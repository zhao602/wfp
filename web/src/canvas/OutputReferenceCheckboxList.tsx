// =============================================================================
// 输出引用复选框列表组件（src/canvas/OutputReferenceCheckboxList.tsx）
//
// 职责：输出展示节点"输出内容（引用）"的受控多选编辑器——渲染上游有效输出
//       候选分组、候选外"已失效"标记行与依赖未满足灰显占位分组。
// 设计意图（optimize_output_display_node DC-03 / CHG-08）：
//   1. 受控组件：勾选态完全由 value 驱动，onChange 全量数组回写
//      （参照 ToolSelectionList 受控范式，不复用其工具 API 拉取逻辑）；
//   2. 数据源为传入候选列表（组件不自行收集、不拉 API、不触碰画布状态）；
//   3. 候选外 value 元素渲染"已失效"标记行（不可再勾选、可移除，保存不受阻）；
//   4. deferredGroups 渲染灰显、不可勾选的依赖未满足占位（INV-D19 确定性降级：
//      不阻塞保存、不报错）；
//   5. 样式前缀 wfp-orcl-*（wfp- + BEM），深色主题 darkTokens 双轨完整覆盖。
// =============================================================================

import { useCallback } from "react";
import { useThemeMode, darkTokens } from "../theme/themeMode";
import type { NamedOutputCandidate } from "./mapEditorShared";

/** 依赖未满足的占位分组（灰显、不可勾选；如"上游输入参数"待 upstream_input_ref 落地） */
export interface DeferredCandidateGroup {
  /** 分组标题（如"上游输入参数"） */
  readonly title: string;
  /** 依赖说明（如"待 upstream_input_ref 落地后启用"） */
  readonly note: string;
}

/** OutputReferenceCheckboxList 属性（受控组件） */
export interface OutputReferenceCheckboxListProps {
  /** 已勾选的引用表达式列表（对应 input_reference 归一化集合） */
  value: string[];
  /** 勾选变更回调（全量数组：追加保序 / 取消过滤） */
  onChange: (value: string[]) => void;
  /** 候选项列表（上游有效输出，由调用方收集传入） */
  candidates: NamedOutputCandidate[];
  /** 依赖未满足的占位分组（可选；灰显、不可勾选） */
  deferredGroups?: DeferredCandidateGroup[];
}

/** 输出引用复选框列表：候选分组勾选 + 失效标记 + 依赖占位 */
export default function OutputReferenceCheckboxList({
  value,
  onChange,
  candidates,
  deferredGroups,
}: OutputReferenceCheckboxListProps): JSX.Element {
  const themeMode = useThemeMode();
  const isDark = themeMode === "dark";

  /** 勾选/取消：全量数组回写（追加保持次序，取消按引用过滤），不修改入参数组 */
  const toggle = useCallback(
    (reference: string, checked: boolean) => {
      if (checked) {
        onChange([...value, reference]);
      } else {
        onChange(value.filter((v) => v !== reference));
      }
    },
    [value, onChange],
  );

  /** 候选按引用表达式去重（同名命名输出多拥有者取首见；勾选值域为引用表达式） */
  const uniqueCandidates: NamedOutputCandidate[] = [];
  const candidateReferences = new Set<string>();
  for (const candidate of candidates) {
    if (!candidateReferences.has(candidate.outputName)) {
      candidateReferences.add(candidate.outputName);
      uniqueCandidates.push(candidate);
    }
  }

  /** 失效引用：value 含候选外标识（渲染时去重；可移除、不可再勾选，保存不受阻） */
  const invalidReferences = Array.from(
    new Set(value.filter((ref) => !candidateReferences.has(ref))),
  );

  return (
    <>
      <style>{`
        .wfp-orcl-section {
          margin-bottom: 4px;
        }
        .wfp-orcl-summary {
          font-size: 11px;
          color: ${isDark ? darkTokens.text.hint : "#999"};
          margin-bottom: 6px;
        }
        .wfp-orcl-group {
          margin-bottom: 8px;
        }
        .wfp-orcl-group-title {
          font-size: 11px;
          font-weight: 600;
          color: ${isDark ? darkTokens.text.secondary : "#666"};
          margin-bottom: 4px;
        }
        .wfp-orcl-group-title--deferred {
          color: ${isDark ? darkTokens.text.hint : "#999"};
        }
        .wfp-orcl-row {
          display: flex;
          align-items: center;
          gap: 6px;
          padding: 4px 6px;
          margin-bottom: 2px;
          font-size: 12px;
          border-radius: 4px;
          cursor: pointer;
          color: ${isDark ? darkTokens.text.primary : "#333"};
        }
        .wfp-orcl-row:hover {
          background: ${isDark ? darkTokens.surface.bgHover : "#f5f5f5"};
        }
        .wfp-orcl-row--invalid {
          cursor: default;
          color: ${isDark ? "#ffb74d" : "#ed6c02"};
        }
        .wfp-orcl-row--invalid:hover {
          background: transparent;
        }
        .wfp-orcl-row--deferred {
          cursor: default;
          color: ${isDark ? darkTokens.text.hint : "#999"};
        }
        .wfp-orcl-row--deferred:hover {
          background: transparent;
        }
        .wfp-orcl-name {
          flex-shrink: 0;
          font-size: 12px;
          white-space: nowrap;
        }
        .wfp-orcl-ref {
          flex-shrink: 0;
          font-size: 11px;
          font-family: Consolas, Menlo, monospace;
          white-space: nowrap;
          color: ${isDark ? darkTokens.text.secondary : "#666"};
        }
        .wfp-orcl-category {
          flex-shrink: 0;
          font-size: 10px;
          padding: 1px 5px;
          border-radius: 3px;
          color: ${isDark ? darkTokens.text.secondary : "#666"};
          background: ${isDark ? darkTokens.surface.bgHover : "#f0f0f0"};
        }
        .wfp-orcl-invalid-tag {
          flex-shrink: 0;
          font-size: 10px;
          padding: 1px 5px;
          border-radius: 3px;
          color: ${isDark ? "#ffb74d" : "#ed6c02"};
          background: ${isDark ? "rgba(255,183,77,0.12)" : "#fff3e0"};
        }
        .wfp-orcl-remove {
          flex-shrink: 0;
          margin-left: auto;
          padding: 2px 6px;
          font-size: 11px;
          font-family: inherit;
          color: ${isDark ? darkTokens.danger : "#d32f2f"};
          background: transparent;
          border: none;
          border-radius: 4px;
          cursor: pointer;
        }
        .wfp-orcl-remove:hover {
          background: ${isDark ? "rgba(255,107,107,0.12)" : "#fdecea"};
        }
        .wfp-orcl-empty {
          font-size: 11px;
          color: ${isDark ? darkTokens.text.hint : "#999"};
        }
        .wfp-orcl-deferred-note {
          font-size: 11px;
          color: ${isDark ? darkTokens.text.hint : "#999"};
        }
      `}</style>
      <div className="wfp-orcl-section">
        <div className="wfp-orcl-summary">已选 {value.length} 项</div>
        <div className="wfp-orcl-group">
          <div className="wfp-orcl-group-title">上游有效输出</div>
          {uniqueCandidates.length === 0 && (
            <div className="wfp-orcl-empty">暂无上游可引用输出</div>
          )}
          {uniqueCandidates.map((candidate) => {
            const reference = candidate.outputName;
            const checked = value.includes(reference);
            const fullText = `${candidate.nodeName}（${candidate.outputName}）`;
            return (
              <label
                key={`${candidate.nodeId}:${candidate.outputName}`}
                className="wfp-orcl-row"
                title={fullText}
              >
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={(e) => toggle(reference, e.target.checked)}
                />
                <span className="wfp-orcl-name">{candidate.nodeName}</span>
                <span className="wfp-orcl-ref">{candidate.outputName}</span>
                {candidate.category && (
                  <span className="wfp-orcl-category">{candidate.category}</span>
                )}
              </label>
            );
          })}
        </div>
        {invalidReferences.map((reference) => (
          <div
            key={reference}
            className="wfp-orcl-row wfp-orcl-row--invalid"
            title={reference}
          >
            <input type="checkbox" checked disabled readOnly />
            <span className="wfp-orcl-ref">{reference}</span>
            <span className="wfp-orcl-invalid-tag">已失效</span>
            <button
              type="button"
              className="wfp-orcl-remove"
              onClick={() => toggle(reference, false)}
            >
              ✕
            </button>
          </div>
        ))}
        {deferredGroups?.map((group) => (
          <div key={group.title} className="wfp-orcl-group">
            <div className="wfp-orcl-group-title wfp-orcl-group-title--deferred">
              {group.title}
            </div>
            <div className="wfp-orcl-row wfp-orcl-row--deferred" title={group.note}>
              <input type="checkbox" disabled />
              <span className="wfp-orcl-deferred-note">{group.note}</span>
            </div>
          </div>
        ))}
      </div>
    </>
  );
}
