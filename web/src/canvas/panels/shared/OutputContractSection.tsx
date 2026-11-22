// =============================================================================
// 输出格式契约展示区（src/canvas/panels/shared/OutputContractSection.tsx）
//
// 职责：跨类型公共渲染段——按节点类型查契约表展示输出格式（纯展示，无回写），
// 自 NCP 上提（T1.4 共享基元收口）；未命中契约表时降级提示。
// =============================================================================

import { useThemeMode, darkTokens } from "../../../theme/themeMode";
import type { NodeType } from "../../../workflow-definition/definitionTypes";
import { resolveOutputContract } from "../../nodeOutputContracts";
import { PanelIcon } from "./PanelIcon";

/** 输出格式（固定契约）展示区：按节点类型查契约表，未命中降级提示 */
export function OutputContractSection({ nodeType }: { nodeType: NodeType }): JSX.Element {
  const themeMode = useThemeMode();
  const isDark = themeMode === "dark";
  const contract = resolveOutputContract(nodeType);
  return (
    <>
      <style>{`
        /* 输出契约条：单行信息带（标题 + 类型 + 徽标 + 详情气泡），
           把原本常驻的两段说明文字收进悬停气泡，正文只留最关键的类别名。 */
        .wfp-ncp-contract {
          display: flex;
          align-items: center;
          gap: 8px;
          margin-bottom: 16px;
          padding: 7px 10px;
          border: 1px solid ${isDark ? darkTokens.surface.divider : "#ececec"};
          border-radius: 7px;
          background: ${isDark ? darkTokens.surface.bgHover : "#fafafa"};
        }
        .wfp-ncp-contract-title {
          flex-shrink: 0;
          font-size: 10px;
          font-weight: 500;
          letter-spacing: 0.04em;
          color: ${isDark ? darkTokens.text.secondary : "#6b6b6b"};
        }
        .wfp-ncp-contract-row {
          display: flex;
          align-items: center;
          gap: 6px;
          min-width: 0;
        }
        .wfp-ncp-contract-category {
          font-size: 12.5px;
          font-weight: 600;
          color: ${isDark ? darkTokens.text.primary : "#1a1a1a"};
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .wfp-ncp-contract-badge {
          flex-shrink: 0;
          font-size: 10px;
          font-weight: 500;
          padding: 1px 6px;
          border-radius: 8px;
        }
        .wfp-ncp-contract-badge--noref {
          color: ${isDark ? darkTokens.danger : "#d32f2f"};
          background: ${isDark ? "rgba(255,107,107,0.12)" : "#fdecea"};
        }
        .wfp-ncp-contract-detail {
          display: block;
          color: ${isDark ? darkTokens.text.secondary : "#5f5f5f"};
        }
        .wfp-ncp-contract-detail--muted {
          color: ${isDark ? darkTokens.text.hint : "#9a9a9a"};
        }
        .wfp-ncp-contract-hint {
          display: block;
          margin-top: 4px;
          color: ${isDark ? darkTokens.text.hint : "#9a9a9a"};
        }
        .wfp-ncp-contract-tip {
          position: absolute;
          top: calc(100% + 6px);
          right: 0;
          z-index: 60;
          box-sizing: border-box;
          width: max-content;
          max-width: 210px;
          padding: 8px 10px;
          border-radius: 6px;
          border: 1px solid ${isDark ? darkTokens.surface.divider : "#e2e2e2"};
          background: ${isDark ? darkTokens.surface.bgHover : "#ffffff"};
          font-size: 11px;
          font-weight: 400;
          line-height: 1.55;
          text-align: left;
          box-shadow: 0 8px 20px ${isDark ? "rgba(0, 0, 0, 0.55)" : "rgba(15, 15, 15, 0.12)"};
          pointer-events: none;
          opacity: 0;
          visibility: hidden;
          transform: translateY(-2px);
          transition: opacity 120ms ease, transform 120ms ease, visibility 120ms;
        }
        .wfp-ncp-hint-anchor:hover .wfp-ncp-contract-tip,
        .wfp-ncp-hint-anchor:focus-within .wfp-ncp-contract-tip {
          opacity: 1;
          visibility: visible;
          transform: translateY(0);
        }
      `}</style>
      <div className="wfp-ncp-contract">
        <span className="wfp-ncp-contract-title">输出格式（固定契约）</span>
        {contract === undefined ? (
          <span className="wfp-ncp-contract-detail wfp-ncp-contract-detail--muted">
            该类型输出格式待定义
          </span>
        ) : (
          <span className="wfp-ncp-contract-row">
            <span className="wfp-ncp-contract-category">{contract.category}</span>
            {!contract.referable && (
              <span className="wfp-ncp-contract-badge wfp-ncp-contract-badge--noref">
                不可引用
              </span>
            )}
            <span className="wfp-ncp-hint-anchor">
              <button type="button" className="wfp-ncp-hint-btn" aria-label="输出契约详情">
                <PanelIcon name="info" size={13} />
              </button>
              <span className="wfp-ncp-contract-tip">
                <span className="wfp-ncp-contract-detail">{contract.detail}</span>
                {contract.schemaEditable && (
                  <span className="wfp-ncp-contract-hint">
                    可受输出结构约束（output_schema）细化
                  </span>
                )}
              </span>
            </span>
          </span>
        )}
      </div>
    </>
  );
}
