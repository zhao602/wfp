// =============================================================================
// 工具勾选列表组件（src/canvas/ToolSelectionList.tsx）
//
// 职责：Agent 节点 available_tools 的受控勾选编辑器——挂载时经
//       GET /api/v1/tools 拉取三源聚合工具清单，按来源分组渲染勾选项。
// 设计意图（merge_tool_into_agent_node FR-2 / DC-B / CHG-13）：
//   1. 受控组件：勾选态完全由 value 驱动，onChange 全量数组回写（与被替换的
//      StringListEditor 同构，AgentConfigForm 无缝替换）；
//   2. 三重降级（AC-3）：清单拉取失败 → 「工具清单加载失败」提示条 + 空列表，
//      value 既有绑定以「（已绑定）」灰底行保留（不清空、可取消勾选）；
//   3. 失效标记（AC-4）：清单加载成功但 value 含清单外标识 → 「已失效」标记行
//      （警告色、不可再勾选但可移除），保存不受阻。
//   4. 收起/展开：区块头部常驻「收起/展开」按钮与「已选 N 项」摘要，折叠仅
//      隐藏列表主体（纯 UI 状态，不触碰 value / 拉取 / onChange）。
//   5. null/undefined 三态防御（fix_agent_null_length G-04 / DC-05，第二防线）：
//      组件入口单点归一（Array.isArray 真值判断），一处归一覆盖全部 value 访问点；
//      合法数组零回归（同一引用透传，onChange 回写语义不变）；props 签名不改
//      （运行时宽容、类型面收紧，承 fix_raw_not_iterable DC-03 同型取舍）。
// =============================================================================

import { useCallback, useEffect, useState } from "react";
import { useThemeMode, darkTokens } from "../theme/themeMode";
import { orchestrationApiClient } from "../api/orchestrationApiClient";
import type { ToolCatalogItem, ToolSource } from "../api/orchestrationApiClient";

/** 清单加载状态：加载中 / 成功 / 失败（失败走 AC-3 降级渲染） */
type CatalogStatus = "loading" | "ready" | "failed";

/** ToolSelectionList 属性（受控组件，与 StringListEditor 同构） */
export interface ToolSelectionListProps {
  /** 已勾选的工具标识列表（对应 available_tools） */
  value: string[];
  /** 勾选变更回调（全量数组） */
  onChange: (value: string[]) => void;
}

/** 来源分组标题（固定三组次序：平台 / MCP / 本地） */
const TOOL_SOURCE_GROUPS: Array<{ source: ToolSource; title: string }> = [
  { source: "platform", title: "平台工具" },
  { source: "mcp", title: "MCP 工具" },
  { source: "local", title: "本地工具" },
];

/** value 非法形态（null/undefined/非数组）时的共享空数组（稳定引用，G-04 单点归一回落值） */
const EMPTY_TOOLS: string[] = [];

/** 工具勾选列表：按来源分组展示工具清单，勾选/取消即 onChange 全量数组 */
export default function ToolSelectionList({
  value,
  onChange,
}: ToolSelectionListProps): JSX.Element {
  // G-04 入口单点归一（DC-05）：一处归一覆盖下方全部访问点；
  // 合法数组保持原引用（INV-02 零回归），非法形态回落共享空数组。
  // 命名避开下方 ready 态清单分组的既有局部变量 items（防遮蔽）
  const selectedTools = Array.isArray(value) ? value : EMPTY_TOOLS;
  const themeMode = useThemeMode();
  const isDark = themeMode === "dark";
  const [catalog, setCatalog] = useState<ToolCatalogItem[]>([]);
  const [status, setStatus] = useState<CatalogStatus>("loading");
  const [collapsed, setCollapsed] = useState(false);

  /** 挂载时拉取工具清单，仅一次（无轮询）；卸载后丢弃迟到响应 */
  useEffect(() => {
    let active = true;
    orchestrationApiClient
      .listTools()
      .then((res) => {
        if (!active) return;
        setCatalog(res.tools);
        setStatus("ready");
      })
      .catch(() => {
        if (!active) return;
        setStatus("failed");
      });
    return () => {
      active = false;
    };
  }, []);

  /** 勾选/取消：全量数组回写（追加保持次序，取消按标识过滤） */
  const toggle = useCallback(
    (identifier: string, checked: boolean) => {
      if (checked) {
        onChange([...selectedTools, identifier]);
      } else {
        onChange(selectedTools.filter((v) => v !== identifier));
      }
    },
    [selectedTools, onChange],
  );

  /** 失效标识：清单加载成功但 value 含清单外标识（AC-4） */
  const invalidIds =
    status === "ready"
      ? selectedTools.filter((id) => !catalog.some((t) => t.tool_identifier === id))
      : [];

  return (
    <>
      <style>{`
        .wfp-tsl-section {
          margin-bottom: 12px;
        }
        .wfp-tsl-header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 8px;
          margin-bottom: 6px;
        }
        .wfp-tsl-summary {
          font-size: 11px;
          color: ${isDark ? darkTokens.text.hint : "#999"};
        }
        .wfp-tsl-toggle {
          flex-shrink: 0;
          padding: 2px 8px;
          font-size: 11px;
          font-family: inherit;
          color: ${isDark ? darkTokens.text.secondary : "#666"};
          background: transparent;
          border: 1px solid ${isDark ? darkTokens.surface.bgHover : "#e0e0e0"};
          border-radius: 4px;
          cursor: pointer;
        }
        .wfp-tsl-toggle:hover {
          background: ${isDark ? darkTokens.surface.bgHover : "#f5f5f5"};
        }
        .wfp-tsl-status {
          font-size: 11px;
          color: ${isDark ? darkTokens.text.hint : "#999"};
          margin-bottom: 4px;
        }
        .wfp-tsl-error {
          font-size: 11px;
          color: ${isDark ? darkTokens.danger : "#d32f2f"};
          margin-bottom: 6px;
        }
        .wfp-tsl-group {
          margin-bottom: 8px;
        }
        .wfp-tsl-group-title {
          font-size: 11px;
          font-weight: 600;
          color: ${isDark ? darkTokens.text.secondary : "#666"};
          margin-bottom: 4px;
        }
        .wfp-tsl-row {
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
        .wfp-tsl-row:hover {
          background: ${isDark ? darkTokens.surface.bgHover : "#f5f5f5"};
        }
        .wfp-tsl-row-bound {
          background: ${isDark ? darkTokens.surface.bgHover : "#f0f0f0"};
        }
        .wfp-tsl-row-invalid {
          cursor: default;
          color: ${isDark ? "#ffb74d" : "#ed6c02"};
        }
        .wfp-tsl-row-invalid:hover {
          background: transparent;
        }
        .wfp-tsl-name {
          flex-shrink: 0;
          font-size: 12px;
          white-space: nowrap;
        }
        .wfp-tsl-identifier {
          flex-shrink: 0;
          font-size: 11px;
          font-family: Consolas, Menlo, monospace;
          white-space: nowrap;
          color: ${isDark ? darkTokens.text.secondary : "#666"};
        }
        .wfp-tsl-perm {
          flex-shrink: 0;
          font-size: 10px;
          padding: 1px 5px;
          border-radius: 3px;
          color: ${isDark ? darkTokens.text.secondary : "#666"};
          background: ${isDark ? darkTokens.surface.bgHover : "#f0f0f0"};
        }
        .wfp-tsl-desc {
          flex: 1;
          min-width: 0;
          font-size: 11px;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
          color: ${isDark ? darkTokens.text.hint : "#999"};
        }
        .wfp-tsl-bound-tag {
          flex-shrink: 0;
          font-size: 10px;
          color: ${isDark ? darkTokens.text.hint : "#999"};
        }
        .wfp-tsl-invalid-tag {
          flex-shrink: 0;
          font-size: 10px;
          padding: 1px 5px;
          border-radius: 3px;
          color: ${isDark ? "#ffb74d" : "#ed6c02"};
          background: ${isDark ? "rgba(255,183,77,0.12)" : "#fff3e0"};
        }
        .wfp-tsl-remove {
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
        .wfp-tsl-remove:hover {
          background: ${isDark ? "rgba(255,107,107,0.12)" : "#fdecea"};
        }
      `}</style>
      <div className="wfp-tsl-section">
        <div className="wfp-tsl-header">
          <span className="wfp-tsl-summary">已选 {selectedTools.length} 项</span>
          <button
            type="button"
            className="wfp-tsl-toggle"
            aria-expanded={!collapsed}
            onClick={() => setCollapsed((c) => !c)}
          >
            {collapsed ? "展开" : "收起"}
          </button>
        </div>
        {!collapsed && status === "loading" && (
          <div className="wfp-tsl-status">工具清单加载中…</div>
        )}
        {!collapsed && status === "failed" && (
          <>
            <div className="wfp-tsl-error">工具清单加载失败</div>
            {selectedTools.length === 0 && (
              <div className="wfp-tsl-status">暂无绑定工具</div>
            )}
            {selectedTools.map((id) => (
              <label key={id} className="wfp-tsl-row wfp-tsl-row-bound" title={id}>
                <input
                  type="checkbox"
                  checked
                  onChange={(e) => toggle(id, e.target.checked)}
                />
                <span className="wfp-tsl-identifier">{id}</span>
                <span className="wfp-tsl-bound-tag">（已绑定）</span>
              </label>
            ))}
          </>
        )}
        {!collapsed && status === "ready" && (
          <>
            {TOOL_SOURCE_GROUPS.map((group) => {
              const items = catalog.filter((t) => t.source === group.source);
              return (
                <div key={group.source} className="wfp-tsl-group">
                  <div className="wfp-tsl-group-title">{group.title}</div>
                  {items.length === 0 && (
                    <div className="wfp-tsl-status">暂无工具</div>
                  )}
                  {items.map((tool) => {
                    const checked = selectedTools.includes(tool.tool_identifier);
                    const fullText = `${tool.name}（${tool.tool_identifier}）：${tool.description}`;
                    return (
                      <label
                        key={tool.tool_identifier}
                        className="wfp-tsl-row"
                        title={fullText}
                      >
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={(e) => toggle(tool.tool_identifier, e.target.checked)}
                        />
                        <span className="wfp-tsl-name">{tool.name}</span>
                        <span className="wfp-tsl-identifier">{tool.tool_identifier}</span>
                        <span className="wfp-tsl-perm">{tool.permission}</span>
                        <span className="wfp-tsl-desc">{tool.description}</span>
                      </label>
                    );
                  })}
                </div>
              );
            })}
            {invalidIds.map((id) => (
              <div key={id} className="wfp-tsl-row wfp-tsl-row-invalid" title={id}>
                <input type="checkbox" checked disabled onChange={() => toggle(id, false)} />
                <span className="wfp-tsl-identifier">{id}</span>
                <span className="wfp-tsl-invalid-tag">已失效</span>
                <button
                  type="button"
                  className="wfp-tsl-remove"
                  onClick={() => toggle(id, false)}
                >
                  ✕
                </button>
              </div>
            ))}
          </>
        )}
      </div>
    </>
  );
}
