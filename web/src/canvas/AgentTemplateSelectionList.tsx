// =============================================================================
// Agent 模板选择列表组件（web/src/canvas/AgentTemplateSelectionList.tsx）
//
// 职责：Agent 节点上下文的「Agent 模板」动作型选择控件——挂载拉取 single_node
//       模板清单（E-5），单击卡片经详情（E-6）取完整 preset 后上抛应用意图，
//       由父级以函数式原子回写调用既有 applyAgentTemplateToCanvas
//       （targetNodeId = selectedNode.node_id，本组件不直接调用纯函数）。
// 设计意图（per_node_agent_template DC-01/DC-04/DC-05/DC-06/DC-07/DC-08）：
//   1. 动作型组件（DC-01）：单击 = 应用一组字段预设（非写单个字段），
//      复用 ModelSelectionList 范式（挂载拉取 + active 防迟到 +
//      loading/ready/failed 状态机 + darkTokens 双轨），不复用其本体；
//   2. 清单获取（DC-04）：仅复用只读端点 E-5/E-6，不缓存、不轮询；
//      E-5 清单不含 preset → 应用必然经 E-6 取详情；
//   3. recipe 引导（DC-05）：节点上下文仅列 single_node 模板，
//      recipe 以只读引导行指向 Agent 市场面板，不作可点击项；
//   4. 降级（DC-06）：清单失败（含开关关闭 404）→ 只读提示块，
//      不崩溃、不阻断、不清空配置、不提供手工输入；
//   5. 一次性应用反馈（DC-07）：瞬态只读提示，随节点切换重置，不落盘；
//   6. 与模型选择共存（DC-08）：同名键模板优先，应用后节点字段仍可单独调整。
// =============================================================================

import { useEffect, useRef, useState } from "react";
import { useThemeMode, darkTokens } from "../theme/themeMode";
import type { AgentTemplate } from "../market/marketTypes";
import type { ApplyTemplateResult } from "./applyAgentTemplate";
import { getAgentTemplate, listAgentTemplates } from "../market/marketApiClient";

/** 清单加载状态：加载中 / 成功 / 失败（失败走 DC-06 只读降级渲染） */
type CatalogStatus = "loading" | "ready" | "failed";

/** E-5 清单条目（与 marketApiClient.listAgentTemplates 返回条目结构一致） */
type TemplateListItem = Pick<
  AgentTemplate,
  "template_id" | "name" | "kind" | "max_tool_rounds" | "allowed_tools"
>;

// ---- 文案单一出处（T4.1/T4.2/DC-08；采纳「组件内自持常量」路径，nodeConfigHints 不动） ----

/** 加载中提示（首屏非阻塞，DFX §4.1.2） */
const LOADING_HINT = "模板清单加载中…";

/** 清单失败降级提示（DC-06；含开关关闭 404——marketApiClient 不暴露状态码，单一文案覆盖） */
const FETCH_FAILED_HINT = "未能获取 Agent 模板清单（模板服务可能未启用），可稍后重试";

/** 空清单提示（E-5 total=0 或无 single_node 条目） */
const EMPTY_LIST_HINT = "当前无可用 Agent 模板";

/** 与模型选择共存提示（DC-08/OP-05） */
const COEXISTENCE_HINT =
  "应用模板将按模板预设覆盖同名字段（含模型标识）；应用后仍可在下方单独调整模型。";

/** 详情失败反馈（E-6 失败，不改节点配置，DFX §4.2.3） */
const DETAIL_FAILED_HINT = "应用失败：模板详情加载失败";

/** 应用失败前缀与原因文案（applied=false / 无目标节点） */
const APPLY_FAILED_PREFIX = "应用失败：";
const APPLY_FAILED_NO_TARGET = "目标节点不可用";
const APPLY_FAILED_FALLBACK = "模板不可应用";

/** 应用成功反馈（DC-07 瞬态只读；reason 非空时附越界键说明） */
const APPLY_SUCCESS_PREFIX = "已应用「";
const APPLY_SUCCESS_SUFFIX = "」到当前节点";

/** 只读来源标注行前缀（DC-07 第 2 项，不落盘） */
const SOURCE_LINE_PREFIX = "当前节点最近应用的模板：";

/** recipe 只读引导文案（DC-05，非可点击项；配方计数运行期推导，文案唯一出处） */
function buildRecipeGuidance(recipeCount: number): string {
  return `另有 ${recipeCount} 个多节点配方模板，会产生多个节点与连线，请在 Agent 市场面板应用`;
}

/** Agent 模板选择列表属性（动作型控件：单击即上抛应用意图） */
export interface AgentTemplateSelectionListProps {
  /** 目标 Agent 节点 id（= selectedNode.node_id）；空串时应用被拒绝（spec §5.9.3 无目标歧义防护） */
  targetNodeId: string;
  /** 应用模板回调：父级以函数式原子回写实现；返回应用结果供反馈渲染，null 表示不可应用 */
  onApplyTemplate: (template: AgentTemplate) => ApplyTemplateResult | null;
  /** 可选只读来源标注：当前节点最近一次在本面板应用的模板名（DC-07，不落盘） */
  appliedTemplateName?: string;
}

/** Agent 模板选择列表：single_node 模板卡片 + recipe 只读引导 + 三态降级 + 应用反馈 */
export default function AgentTemplateSelectionList({
  targetNodeId,
  onApplyTemplate,
  appliedTemplateName,
}: AgentTemplateSelectionListProps): JSX.Element {
  const themeMode = useThemeMode();
  const isDark = themeMode === "dark";
  const [templates, setTemplates] = useState<readonly TemplateListItem[]>([]);
  const [recipeCount, setRecipeCount] = useState(0);
  const [status, setStatus] = useState<CatalogStatus>("loading");
  const [feedback, setFeedback] = useState<string | null>(null);

  /** 组件存活标志：单击链路无 effect cleanup，经 ref 丢弃卸载后迟到的详情响应（T1.3） */
  const aliveRef = useRef(true);
  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  /** 挂载时拉取模板清单，仅一次（无轮询、不缓存）；卸载后丢弃迟到响应（DC-04） */
  useEffect(() => {
    let active = true;
    listAgentTemplates()
      .then((res) => {
        if (!active) return;
        const singleNode = res.templates.filter((t) => t.kind === "single_node");
        setTemplates(singleNode);
        setRecipeCount(res.templates.length - singleNode.length);
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

  /** 节点切换时重置应用反馈（DC-07：瞬态反馈随节点切换重置，不落盘） */
  useEffect(() => {
    setFeedback(null);
  }, [targetNodeId]);

  /** 单击卡片：E-6 取详情 → 上抛应用意图 → 依返回值渲染反馈；失败不改节点配置（T1.3） */
  const handleApply = (templateId: string): void => {
    if (targetNodeId.trim() === "") {
      setFeedback(`${APPLY_FAILED_PREFIX}${APPLY_FAILED_NO_TARGET}`);
      return;
    }
    getAgentTemplate(templateId)
      .then((resolved) => {
        if (!aliveRef.current) return;
        const result = onApplyTemplate(resolved);
        if (result === null) {
          setFeedback(`${APPLY_FAILED_PREFIX}${APPLY_FAILED_NO_TARGET}`);
          return;
        }
        if (result.applied) {
          const success = `${APPLY_SUCCESS_PREFIX}${resolved.name}${APPLY_SUCCESS_SUFFIX}`;
          setFeedback(result.reason === undefined ? success : `${success}；${result.reason}`);
          return;
        }
        setFeedback(`${APPLY_FAILED_PREFIX}${result.reason ?? APPLY_FAILED_FALLBACK}`);
      })
      .catch(() => {
        if (!aliveRef.current) return;
        setFeedback(DETAIL_FAILED_HINT);
      });
  };

  return (
    <>
      <style>{`
        .wfp-atsl-section {
          margin-bottom: 12px;
        }
        .wfp-atsl-status {
          font-size: 11px;
          color: ${isDark ? darkTokens.text.hint : "#999"};
          margin-bottom: 6px;
        }
        .wfp-atsl-notice {
          font-size: 11px;
          line-height: 1.5;
          padding: 7px 9px;
          border-radius: 6px;
          color: ${isDark ? darkTokens.text.secondary : "#666"};
          background: ${isDark ? darkTokens.surface.bgHover : "#f5f5f5"};
          border: 1px solid ${isDark ? darkTokens.surface.divider : "#e0e0e0"};
          border-left: 2px solid ${isDark ? darkTokens.surface.borderStrong : "#bdbdbd"};
        }
        .wfp-atsl-card {
          display: flex;
          align-items: center;
          gap: 8px;
          padding: 6px 9px;
          margin-bottom: 4px;
          font-size: 12px;
          border-radius: 6px;
          border: 1px solid transparent;
          cursor: pointer;
          color: ${isDark ? darkTokens.text.primary : "#333"};
          transition: background-color 120ms ease, border-color 120ms ease, box-shadow 120ms ease;
        }
        .wfp-atsl-card:hover {
          background: ${isDark ? "rgba(171, 71, 188, 0.08)" : "#f7f1fa"};
          border-color: ${isDark ? "rgba(171, 71, 188, 0.35)" : "rgba(123, 31, 162, 0.25)"};
          box-shadow: ${isDark ? "0 1px 3px rgba(0, 0, 0, 0.3)" : "0 1px 3px rgba(123, 31, 162, 0.08)"};
        }
        .wfp-atsl-dot {
          width: 5px;
          height: 5px;
          border-radius: 50%;
          flex-shrink: 0;
          background: ${isDark ? "#ab47bc" : "#7b1fa2"};
          opacity: 0.4;
          transition: opacity 120ms ease;
        }
        .wfp-atsl-card:hover .wfp-atsl-dot {
          opacity: 1;
        }
        .wfp-atsl-name {
          font-size: 12px;
          font-weight: 500;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .wfp-atsl-recipe-row {
          font-size: 11px;
          line-height: 1.5;
          padding: 5px 9px;
          margin-top: 6px;
          border-radius: 6px;
          cursor: default;
          color: ${isDark ? darkTokens.text.secondary : "#666"};
          background: ${isDark ? darkTokens.surface.bgHover : "#f0f0f0"};
          border-left: 2px dashed ${isDark ? darkTokens.surface.borderStrong : "#bdbdbd"};
        }
        .wfp-atsl-source {
          font-size: 11px;
          line-height: 1.5;
          margin-top: 8px;
          padding: 5px 8px;
          border-radius: 5px;
          color: ${isDark ? "#ce93d8" : "#7b1fa2"};
          background: ${isDark ? "rgba(171, 71, 188, 0.1)" : "#f7f1fa"};
          border-left: 2px solid ${isDark ? "#ab47bc" : "#7b1fa2"};
        }
        .wfp-atsl-feedback {
          font-size: 11px;
          line-height: 1.5;
          margin-top: 8px;
          padding: 5px 8px;
          border-radius: 5px;
        }
        .wfp-atsl-feedback--ok {
          color: ${isDark ? "#81c784" : "#2e7d32"};
          background: ${isDark ? "rgba(129, 199, 132, 0.1)" : "#edf7ed"};
        }
        .wfp-atsl-feedback--err {
          color: ${isDark ? darkTokens.danger : "#d32f2f"};
          background: ${isDark ? "rgba(255, 107, 107, 0.1)" : "#fdecea"};
        }
        .wfp-atsl-coexist-hint {
          font-size: 11px;
          line-height: 1.5;
          margin-top: 6px;
          color: ${isDark ? darkTokens.text.hint : "#9a9a9a"};
        }
      `}</style>
      <div className="wfp-atsl-section">
        {status === "loading" && <div className="wfp-atsl-status">{LOADING_HINT}</div>}
        {status === "failed" && <div className="wfp-atsl-notice">{FETCH_FAILED_HINT}</div>}
        {status === "ready" && templates.length === 0 && (
          <div className="wfp-atsl-notice">{EMPTY_LIST_HINT}</div>
        )}
        {status === "ready" &&
          templates.map((template) => (
            <div
              key={template.template_id}
              className="wfp-atsl-card"
              title={template.name}
              onClick={() => handleApply(template.template_id)}
            >
              <span className="wfp-atsl-dot" />
              <span className="wfp-atsl-name">{template.name}</span>
            </div>
          ))}
        {status === "ready" && recipeCount > 0 && (
          <div className="wfp-atsl-recipe-row">{buildRecipeGuidance(recipeCount)}</div>
        )}
        {appliedTemplateName !== undefined && appliedTemplateName !== "" && (
          <div className="wfp-atsl-source">{`${SOURCE_LINE_PREFIX}${appliedTemplateName}`}</div>
        )}
        {feedback !== null && (
          <div
            className={
              feedback.startsWith(APPLY_SUCCESS_PREFIX)
                ? "wfp-atsl-feedback wfp-atsl-feedback--ok"
                : "wfp-atsl-feedback wfp-atsl-feedback--err"
            }
          >
            {feedback}
          </div>
        )}
        <div className="wfp-atsl-coexist-hint">{COEXISTENCE_HINT}</div>
      </div>
    </>
  );
}
