// =============================================================================
// 知识库绑定模型只读摘要（src/canvas/KnowledgeBaseBindingSummary.tsx）
//
// 职责：vector_knowledge_base 节点"向量化模型"区的只读展示（AC-46/AC-48）——
//       按 knowledge_base_id 经 GET /api/v1/kb-management/knowledge-bases/{kb_id}
//       拉取后台详情，只读展示绑定模型摘要（mode + model_identifier），
//       移除前台独立选模/编辑入口（DC-08）。
// 设计意图（design DC-08 / CHG-33）：
//   1. 绑定优先（AC-48）：展示知识库当前绑定模型，非节点配置遗留值；
//   2. 前台不缓存（AC-49）：knowledge_base_id 变化即按当前绑定重新拉取；
//   3. 旧配置携带 embedding_model（CHG-35 之前的 YAML）→ 追加「已由知识库绑定覆盖」
//      冲突提示（遗留值保留在配置中不动，导出往返不变，INV-D05）；
//   4. 零破坏降级：标识为空 → 引导提示；拉取失败（开关未启用/知识库不存在/网络异常）
//      → 降级提示，不阻断面板其余字段（DFX §4.2.1）；
//   5. 零凭据（INV-D10）：仅展示 mode + model_identifier，无服务地址/密钥位。
// =============================================================================

import { useEffect, useState } from "react";
import { useThemeMode, darkTokens } from "../theme/themeMode";
import { kbManagementApiClient } from "../api/kbManagementApiClient";
import type { ModelLoadingConfig } from "../workflow-definition/definitionTypes";
import type { KBEmbeddingBindingSummary } from "../knowledge_base/kbManagementTypes";

/** 摘要加载状态：空标识 / 加载中 / 成功 / 失败（失败走降级提示） */
type SummaryStatus = "idle" | "loading" | "ready" | "failed";

/** 标识为空引导提示 */
const EMPTY_ID_HINT = "请先填写或选择知识库标识；向量化模型由知识库绑定决定";

/** 拉取失败降级提示（DC-08 / DFX §4.2.1） */
const FETCH_FAILED_HINT = "向量化模型由知识库绑定决定；暂无法获取当前绑定（功能未启用或知识库不存在）";

/** 旧配置被绑定覆盖的冲突提示（AC-48 绑定优先，遗留值保留不动） */
const OVERRIDE_TAG = "已由知识库绑定覆盖";

/** KnowledgeBaseBindingSummary 属性（受控展示组件，无编辑入口） */
export interface KnowledgeBaseBindingSummaryProps {
  /** 当前知识库标识（对应 VectorKnowledgeBaseNodeConfiguration.knowledge_base_id） */
  knowledgeBaseId: string;
  /** 旧配置遗留的 embedding_model（CHG-35 之前的 YAML）；存在则展示覆盖提示 */
  legacyEmbeddingModel?: ModelLoadingConfig;
}

/** 绑定模型只读摘要：mode + model_identifier，不可编辑 */
export default function KnowledgeBaseBindingSummary({
  knowledgeBaseId,
  legacyEmbeddingModel,
}: KnowledgeBaseBindingSummaryProps): JSX.Element {
  const themeMode = useThemeMode();
  const isDark = themeMode === "dark";
  const [binding, setBinding] = useState<KBEmbeddingBindingSummary | null>(null);
  const [status, setStatus] = useState<SummaryStatus>("idle");

  /** 标识变化即按当前绑定重新拉取（AC-49 前台不缓存）；卸载后丢弃迟到响应 */
  useEffect(() => {
    const trimmed = knowledgeBaseId.trim();
    if (trimmed === "") {
      setStatus("idle");
      setBinding(null);
      return;
    }
    let active = true;
    setStatus("loading");
    setBinding(null);
    kbManagementApiClient
      .getKnowledgeBase(trimmed)
      .then((res) => {
        if (!active) return;
        setBinding(res.embedding_model);
        setStatus("ready");
      })
      .catch(() => {
        if (!active) return;
        setStatus("failed");
      });
    return () => {
      active = false;
    };
  }, [knowledgeBaseId]);

  const hintStyle = {
    fontSize: "11px",
    color: isDark ? darkTokens.text.hint : "#999",
  } as const;

  return (
    <>
      <style>{`
        .wfp-kbb-summary {
          display: flex;
          align-items: center;
          gap: 6px;
          font-size: 12px;
          color: ${isDark ? darkTokens.text.primary : "#333"};
        }
        .wfp-kbb-mode {
          flex-shrink: 0;
          font-size: 10px;
          padding: 1px 5px;
          border-radius: 3px;
          font-family: Consolas, Menlo, monospace;
          color: ${isDark ? darkTokens.text.secondary : "#666"};
          background: ${isDark ? darkTokens.surface.bgHover : "#f0f0f0"};
        }
        .wfp-kbb-identifier {
          font-size: 12px;
          font-family: Consolas, Menlo, monospace;
          white-space: nowrap;
        }
        .wfp-kbb-override {
          font-size: 10px;
          margin-top: 2px;
          padding: 1px 5px;
          border-radius: 3px;
          display: inline-block;
          color: ${isDark ? "#ffb74d" : "#ed6c02"};
          background: ${isDark ? "rgba(255,183,77,0.12)" : "#fff3e0"};
        }
      `}</style>
      {status === "idle" && <div className="wfp-kbb-empty" style={hintStyle}>{EMPTY_ID_HINT}</div>}
      {status === "loading" && <div className="wfp-kbb-loading" style={hintStyle}>绑定模型获取中…</div>}
      {status === "failed" && <div className="wfp-kbb-degraded" style={hintStyle}>{FETCH_FAILED_HINT}</div>}
      {status === "ready" && binding && (
        <div>
          <div className="wfp-kbb-summary">
            <span className="wfp-kbb-mode">{binding.mode}</span>
            <span className="wfp-kbb-identifier" title={binding.model_identifier}>
              {binding.model_identifier}
            </span>
          </div>
          {legacyEmbeddingModel && (
            <span className="wfp-kbb-override">{OVERRIDE_TAG}</span>
          )}
        </div>
      )}
    </>
  );
}
