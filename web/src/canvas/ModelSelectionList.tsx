// =============================================================================
// 模型单选列表组件（src/canvas/ModelSelectionList.tsx）
//
// 职责：Agent 节点 model_identifier 的受控单选编辑器——挂载时经
//       GET /api/v1/models/available 拉取运行环境已配置模型候选，
//       以单击式候选卡片承载单选（单击即写回单值 string）。
// 设计意图（agent_model_selector DC-02 / DC-03 / DC-04 / DC-06 / OP-05）：
//   1. 受控单值组件：选中态完全由 value 驱动，onChange 单值 string 回写
//      （model_identifier 单值 string 类型零变更，INV-D01）；
//   2. 挂载期单次拉取（空依赖 useEffect），active 标志丢弃卸载后迟到响应
//      （与 ToolSelectionList.tsx:48-65 逐模式同构）；
//   3. 状态机 loading | ready | failed：初始 loading 立即渲染，首屏不阻塞；
//      ready 态候选逐项渲染为单击式卡片，当前 value 项呈选中高亮态；
//   4. 旧值回显（DC-04）：value 非空且不在候选内 → 候选末位追加「当前值」
//      选中态卡片并标注「未在已配置模型内」（警告色，不清空、不触发 onChange）；
//   5. 降级（DC-06）：ready 且空候选 / failed → 手工输入框 + 提示，
//      value 原值进入输入框，不重试、不阻断、无未捕获异常；
//   6. 自定义入口（OP-05）：ready 态候选末提供「自定义模型」切换行，
//      切换为手工输入写回 model_identifier。
// =============================================================================

import { useEffect, useState } from "react";
import { useThemeMode, darkTokens } from "../theme/themeMode";
import { orchestrationApiClient } from "../api/orchestrationApiClient";
import { NODE_HINTS } from "./nodeConfigHints";
import type { AvailableModelItem } from "../api/apiTypes";

/** 候选加载状态：加载中 / 成功 / 失败（失败走 DC-06 降级渲染） */
type CatalogStatus = "loading" | "ready" | "failed";

/** 降级/自定义手工输入框占位文案（唯一出处 nodeConfigHints，组件消费不自持副本） */
const MANUAL_INPUT_PLACEHOLDER =
  NODE_HINTS.agent.fields.model_identifier.placeholder ?? "";

/** 空候选降级提示（DC-06） */
const EMPTY_CANDIDATES_HINT = "未检测到已配置模型，请检查 .env 的 LLM_PRIMARY_MODEL";

/** 拉取失败降级提示（DC-06 / DFX §4.2.1） */
const FETCH_FAILED_HINT = "未能获取已配置模型，可手工填写";

/** 自定义模型入口提示（OP-05） */
const CUSTOM_MODEL_HINT = "未经 .env 配置的模型可能不可用";

/** 候选外当前值标注（DC-04，视觉与 wfp-tsl-invalid-tag 同族） */
const OUT_OF_CANDIDATES_TAG = "未在已配置模型内";

/** ModelSelectionList 属性（受控单值组件，与 ToolSelectionList 同构的受控风格） */
export interface ModelSelectionListProps {
  /** 当前模型标识（对应 AgentNodeConfiguration.model_identifier） */
  value: string;
  /** 选择变更回调（单值回写，保持 string 语义） */
  onChange: (value: string) => void;
}

/**
 * 运行时宽容归一（G-05，fix_agent_null_length 隐患 L-02 / DC-05）：
 * value 经导入/恢复链可为 null/undefined（类型面收紧、运行时宽容），
 * 非 string 回落空串；合法 string 同值零回归。
 */
function normalizeModelValue(value: string): string {
  return typeof value === "string" ? value : "";
}

/** 模型单选列表：候选渲染为单击式卡片，单击即 onChange 单值写回 */
export default function ModelSelectionList({
  value,
  onChange,
}: ModelSelectionListProps): JSX.Element {
  const themeMode = useThemeMode();
  const isDark = themeMode === "dark";
  const [models, setModels] = useState<AvailableModelItem[]>([]);
  const [status, setStatus] = useState<CatalogStatus>("loading");
  const [customMode, setCustomMode] = useState(false);
  // L-02（G-05）：入口单点归一，后续访问全部经 modelValue（合法 string 同值）
  const modelValue = normalizeModelValue(value);

  /** 挂载时拉取模型候选，仅一次（无轮询）；卸载后丢弃迟到响应 */
  useEffect(() => {
    let active = true;
    orchestrationApiClient
      .listAvailableModels()
      .then((res) => {
        if (!active) return;
        setModels(res.models);
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

  /** 降级/自定义态手工输入框：占位文案自持，value 原值进入输入框 */
  const renderManualInput = (hint: string): JSX.Element => (
    <>
      <input
        type="text"
        className="wfp-msl-input"
        placeholder={MANUAL_INPUT_PLACEHOLDER}
        value={modelValue}
        onChange={(e) => onChange(e.target.value)}
      />
      <div className="wfp-msl-hint">{hint}</div>
    </>
  );

  /** 候选外当前值：value 非空且不在候选集合内（DC-04） */
  const outOfCandidatesValue =
    modelValue.trim() !== "" && !models.some((m) => m.model_identifier === modelValue)
      ? modelValue
      : "";

  return (
    <>
      <style>{`
        .wfp-msl-section {
          margin-bottom: 12px;
        }
        .wfp-msl-status {
          font-size: 11px;
          color: ${isDark ? darkTokens.text.hint : "#999"};
          margin-bottom: 4px;
        }
        .wfp-msl-card {
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
          transition: background-color 120ms ease, border-color 120ms ease;
        }
        .wfp-msl-card:hover {
          background: ${isDark ? darkTokens.surface.bgHover : "#f5f5f5"};
        }
        .wfp-msl-card:not(.wfp-msl-card-selected):hover {
          border-color: ${isDark ? darkTokens.surface.border : "#e0e0e0"};
        }
        .wfp-msl-card-selected {
          background: ${isDark ? darkTokens.surface.bgHover : "#f0f0f0"};
          border-color: ${isDark ? "#64b5f6" : "#1976d2"};
        }
        .wfp-msl-card-current {
          cursor: default;
        }
        .wfp-msl-identifier {
          font-size: 12px;
          font-family: Consolas, Menlo, monospace;
          white-space: nowrap;
        }
        .wfp-msl-source {
          flex-shrink: 0;
          font-size: 10px;
          padding: 1px 5px;
          border-radius: 3px;
          color: ${isDark ? darkTokens.text.secondary : "#666"};
          background: ${isDark ? darkTokens.surface.bgHover : "#f0f0f0"};
        }
        .wfp-msl-invalid-tag {
          flex-shrink: 0;
          font-size: 10px;
          padding: 1px 5px;
          border-radius: 3px;
          color: ${isDark ? "#ffb74d" : "#ed6c02"};
          background: ${isDark ? "rgba(255,183,77,0.12)" : "#fff3e0"};
        }
        .wfp-msl-custom-row {
          font-size: 11px;
          padding: 4px 6px;
          margin-top: 2px;
          border-radius: 4px;
          cursor: pointer;
          color: ${isDark ? "#64b5f6" : "#1976d2"};
        }
        .wfp-msl-custom-row:hover {
          background: ${isDark ? darkTokens.surface.bgHover : "#f5f5f5"};
        }
        .wfp-msl-input {
          width: 100%;
          box-sizing: border-box;
          padding: 7px 10px;
          font-size: 13px;
          font-weight: 500;
          font-family: inherit;
          color: ${isDark ? darkTokens.text.primary : "#333333"};
          background-color: ${isDark ? darkTokens.surface.bg : "#ffffff"};
          border: 1px solid ${isDark ? darkTokens.surface.divider : "#e0e0e0"};
          border-radius: 4px;
        }
        .wfp-msl-hint {
          font-size: 11px;
          margin-top: 4px;
          color: ${isDark ? darkTokens.text.hint : "#999"};
        }
      `}</style>
      <div className="wfp-msl-section">
        {status === "loading" && (
          <div className="wfp-msl-status">模型候选加载中…</div>
        )}
        {status === "failed" && renderManualInput(FETCH_FAILED_HINT)}
        {status === "ready" && customMode && (
          <>
            {renderManualInput(CUSTOM_MODEL_HINT)}
            <div
              className="wfp-msl-custom-row"
              onClick={() => setCustomMode(false)}
            >
              返回候选列表
            </div>
          </>
        )}
        {status === "ready" && !customMode && (
          <>
            {models.length === 0 && renderManualInput(EMPTY_CANDIDATES_HINT)}
            {models.length > 0 && (
              <>
                {models.map((model) => {
                  const selected = model.model_identifier === modelValue;
                  return (
                    <div
                      key={model.model_identifier}
                      className={
                        selected
                          ? "wfp-msl-card wfp-msl-card-selected"
                          : "wfp-msl-card"
                      }
                      title={model.model_identifier}
                      onClick={() => onChange(model.model_identifier)}
                    >
                      <span className="wfp-msl-identifier">
                        {model.model_identifier}
                      </span>
                      <span className="wfp-msl-source">
                        {model.source === "primary" ? "主模型" : "备用"}
                      </span>
                    </div>
                  );
                })}
                {outOfCandidatesValue !== "" && (
                  <div
                    className="wfp-msl-card wfp-msl-card-selected wfp-msl-card-current"
                    title={outOfCandidatesValue}
                  >
                    <span className="wfp-msl-identifier">
                      {outOfCandidatesValue}
                    </span>
                    <span className="wfp-msl-invalid-tag">
                      {OUT_OF_CANDIDATES_TAG}
                    </span>
                  </div>
                )}
                <div
                  className="wfp-msl-custom-row"
                  onClick={() => setCustomMode(true)}
                >
                  自定义模型
                </div>
              </>
            )}
          </>
        )}
      </div>
    </>
  );
}
