// =============================================================================
// 知识库候选单选列表（src/canvas/KnowledgeBaseSelectionList.tsx）
//
// 职责：vector_knowledge_base 节点 knowledge_base_id 的"从后台知识库清单选择"
//       增强（AC-47，候选来源单一）——挂载时经
//       GET /api/v1/kb-management/knowledge-bases 拉取后台清单，
//       单击候选卡写回 knowledge_base_id（受控单值；自由文本输入保留在调用方）。
// 设计意图（design DC-08 / CHG-33，沿用 ModelSelectionList 形态）：
//   1. 挂载期单次拉取（空依赖 useEffect），active 标志丢弃卸载后迟到响应；
//   2. 状态机 loading | ready | failed：ready 态候选逐项渲染为单击式卡片，
//      当前 value 项呈选中高亮态；
//   3. 零破坏降级（DC-08）：拉取失败（后台开关未启用/网络异常）→ 整块不渲染，
//      回退自由文本输入，与改造前行为一致；
//   4. 候选外当前值回显：value 非空且不在清单内 → 清单末位追加「当前值」
//      选中态卡片并标注「未在后台清单内」（警告色，不清空、不触发 onChange）；
//   5. 零凭据（INV-D10）：仅渲染 knowledge_base_id / name，无服务地址/密钥位；
//   6. 前台不缓存（AC-49）：每次挂载按当前清单拉取。
// =============================================================================

import { useEffect, useState } from "react";
import { useThemeMode, darkTokens } from "../theme/themeMode";
import { kbManagementApiClient } from "../api/kbManagementApiClient";
import type { KnowledgeBaseView } from "../knowledge_base/kbManagementTypes";

/** 候选加载状态：加载中 / 成功 / 失败（失败走 DC-08 整块降级，不渲染） */
type CatalogStatus = "loading" | "ready" | "failed";

/** 空清单提示（自由文本输入仍在调用方渲染，不阻断） */
const EMPTY_LIST_HINT = "后台暂无知识库，可先在「向量知识库管理」中创建，或直接填写标识";

/** 候选外当前值标注（视觉与 wfp-msl-invalid-tag 同族） */
const OUT_OF_LIST_TAG = "未在后台清单内";

/** KnowledgeBaseSelectionList 属性（受控单值组件，与 ModelSelectionList 同构的受控风格） */
export interface KnowledgeBaseSelectionListProps {
  /** 当前知识库标识（对应 VectorKnowledgeBaseNodeConfiguration.knowledge_base_id） */
  value: string;
  /** 选择变更回调（单值回写，保持 string 语义） */
  onChange: (kbId: string) => void;
}

/** 知识库候选单选列表：候选渲染为单击式卡片，单击即 onChange 单值写回 */
export default function KnowledgeBaseSelectionList({
  value,
  onChange,
}: KnowledgeBaseSelectionListProps): JSX.Element {
  const themeMode = useThemeMode();
  const isDark = themeMode === "dark";
  const [items, setItems] = useState<KnowledgeBaseView[]>([]);
  const [status, setStatus] = useState<CatalogStatus>("loading");

  /** 挂载时拉取后台知识库清单，仅一次（无轮询）；卸载后丢弃迟到响应 */
  useEffect(() => {
    let active = true;
    kbManagementApiClient
      .listKnowledgeBases(1, 50)
      .then((res) => {
        if (!active) return;
        setItems(res.items);
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

  /** 候选外当前值：value 非空且不在清单集合内 */
  const outOfListValue =
    value.trim() !== "" && !items.some((kb) => kb.knowledge_base_id === value)
      ? value
      : "";

  return (
    <>
      <style>{`
        .wfp-kbsl-section {
          margin-bottom: 8px;
        }
        .wfp-kbsl-status {
          font-size: 11px;
          color: ${isDark ? darkTokens.text.hint : "#999"};
          margin-bottom: 4px;
        }
        .wfp-kbsl-card {
          display: flex;
          align-items: center;
          gap: 6px;
          padding: 4px 6px;
          margin-bottom: 2px;
          font-size: 12px;
          border-radius: 4px;
          border: 1px solid transparent;
          cursor: pointer;
          color: ${isDark ? darkTokens.text.primary : "#333"};
        }
        .wfp-kbsl-card:hover {
          background: ${isDark ? darkTokens.surface.bgHover : "#f5f5f5"};
        }
        .wfp-kbsl-card-selected {
          background: ${isDark ? darkTokens.surface.bgHover : "#f0f0f0"};
          border-color: ${isDark ? "#81c784" : "#2e7d32"};
        }
        .wfp-kbsl-card-current {
          cursor: default;
        }
        .wfp-kbsl-identifier {
          font-size: 12px;
          font-family: Consolas, Menlo, monospace;
          white-space: nowrap;
        }
        .wfp-kbsl-name {
          flex-shrink: 0;
          font-size: 10px;
          padding: 1px 5px;
          border-radius: 3px;
          color: ${isDark ? darkTokens.text.secondary : "#666"};
          background: ${isDark ? darkTokens.surface.bgHover : "#f0f0f0"};
        }
        .wfp-kbsl-invalid-tag {
          flex-shrink: 0;
          font-size: 10px;
          padding: 1px 5px;
          border-radius: 3px;
          color: ${isDark ? "#ffb74d" : "#ed6c02"};
          background: ${isDark ? "rgba(255,183,77,0.12)" : "#fff3e0"};
        }
      `}</style>
      {status === "loading" && (
        <div className="wfp-kbsl-section">
          <div className="wfp-kbsl-status">知识库清单加载中…</div>
        </div>
      )}
      {status === "failed" && null}
      {status === "ready" && (
        <div className="wfp-kbsl-section">
          {items.length === 0 && <div className="wfp-kbsl-status">{EMPTY_LIST_HINT}</div>}
          {items.map((kb) => {
            const selected = kb.knowledge_base_id === value;
            return (
              <div
                key={kb.knowledge_base_id}
                className={
                  selected
                    ? "wfp-kbsl-card wfp-kbsl-card-selected"
                    : "wfp-kbsl-card"
                }
                title={kb.knowledge_base_id}
                onClick={() => onChange(kb.knowledge_base_id)}
              >
                <span className="wfp-kbsl-identifier">{kb.knowledge_base_id}</span>
                <span className="wfp-kbsl-name">{kb.name}</span>
              </div>
            );
          })}
          {outOfListValue !== "" && (
            <div
              className="wfp-kbsl-card wfp-kbsl-card-selected wfp-kbsl-card-current"
              title={outOfListValue}
            >
              <span className="wfp-kbsl-identifier">{outOfListValue}</span>
              <span className="wfp-kbsl-invalid-tag">{OUT_OF_LIST_TAG}</span>
            </div>
          )}
        </div>
      )}
    </>
  );
}
