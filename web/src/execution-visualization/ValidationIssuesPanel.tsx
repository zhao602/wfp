// =============================================================================
// 校验问题面板（src/execution-visualization/ValidationIssuesPanel.tsx）
//
// 职责：运行前校验报告可视化（B13 U3）：问题行 = 严重级别色点 + code + message +
// `节点 X · 字段 Y · 行 Z` 字段级定位 +（有 node_id 时）「定位到画布」按钮。
// 纯展示组件（回调上抛）：报告获取、面板显隐与画布跳转编排归 App.tsx（design D5.2）。
// =============================================================================

import { useThemeMode, darkTokens } from "../theme/themeMode";
import type {
  ValidationIssue,
  ValidationReport,
  ValidationSeverity,
} from "../workflow-definition/definitionTypes";

/** 校验问题面板属性 */
interface ValidationIssuesPanelProps {
  /** 后端校验报告（裸报告形状，valid=false 时渲染） */
  report: ValidationReport;
  /** 「定位到画布」回调：入参为问题挂靠的 node_id */
  onLocateNode: (nodeId: string) => void;
  /** 关闭面板回调 */
  onClose: () => void;
}

/** 严重级别色点颜色（浅/深同值：红 = 既有 danger 红，琥珀 = 单点警示色） */
const SEVERITY_DOT_COLOR: Record<ValidationSeverity, string> = {
  error: "#d32f2f",
  warning: "#f59e0b",
};

/**
 * 定位文本拼装（B13 U3）：`节点 X · 字段 Y · 行 Z`。
 * 逐级省略缺失项；三项全缺返回 null（不渲染定位行）。
 */
function buildLocationText(issue: ValidationIssue): string | null {
  const parts: string[] = [];
  if (issue.node_id !== undefined) {
    parts.push(`节点 ${issue.node_id}`);
  }
  if (issue.field !== undefined) {
    parts.push(`字段 ${issue.field}`);
  }
  if (issue.location?.line !== undefined) {
    parts.push(`行 ${issue.location.line}`);
  }
  return parts.length > 0 ? parts.join(" · ") : null;
}

/**
 * 校验面板样式注入块（随面板挂载注入、卸载复位）：
 * 类族 `wfp-validation-panel` BEM（与 wfp-overlay- / wfp-footer- 前缀隔离）；
 * 深色规则追加 `.wfp-theme--dark` 作用域，取色复用 darkTokens。
 */
const validationPanelCss = `
.wfp-validation-panel__header{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:6px;}
.wfp-validation-panel__title{font-weight:600;font-size:12px;}
.wfp-validation-panel__close{display:inline-flex;align-items:center;justify-content:center;width:20px;height:20px;padding:0;border:1px solid #d0d0d0;border-radius:3px;background:#ffffff;color:#616161;cursor:pointer;line-height:1;flex:0 0 auto;}
.wfp-validation-panel__close:hover{background:#f0f0f0;color:#212121;}
.wfp-validation-panel__close:focus-visible{outline:2px solid #1976d2;outline-offset:1px;}
.wfp-validation-panel__list{margin:0;padding:0;list-style:none;display:flex;flex-direction:column;gap:6px;}
.wfp-validation-panel__item{display:flex;align-items:flex-start;gap:6px;}
.wfp-validation-panel__dot{display:inline-block;width:8px;height:8px;border-radius:50%;flex:0 0 auto;margin-top:4px;}
.wfp-validation-panel__body{flex:1 1 auto;min-width:0;}
.wfp-validation-panel__headline{font-size:11px;line-height:1.5;word-break:break-word;}
.wfp-validation-panel__code{font-weight:600;margin-right:4px;}
.wfp-validation-panel__location{font-size:11px;line-height:1.5;color:#616161;margin-top:1px;word-break:break-all;}
.wfp-validation-panel__locate{flex:0 0 auto;font-size:11px;padding:2px 8px;border:1px solid #1976d2;border-radius:3px;background:#ffffff;color:#1976d2;cursor:pointer;}
.wfp-validation-panel__locate:hover{background:#e3f2fd;}
.wfp-validation-panel__locate:focus-visible{outline:2px solid #1976d2;outline-offset:1px;}
.wfp-theme--dark .wfp-validation-panel__close{border-color:${darkTokens.surface.divider};background:${darkTokens.surface.bg};color:${darkTokens.text.secondary};}
.wfp-theme--dark .wfp-validation-panel__close:hover{background:${darkTokens.surface.bgHover};color:${darkTokens.text.primary};}
.wfp-theme--dark .wfp-validation-panel__close:focus-visible{outline-color:${darkTokens.status.running};}
.wfp-theme--dark .wfp-validation-panel__location{color:${darkTokens.text.secondary};}
.wfp-theme--dark .wfp-validation-panel__locate{border-color:${darkTokens.status.running};background:${darkTokens.surface.bg};color:${darkTokens.status.running};}
.wfp-theme--dark .wfp-validation-panel__locate:hover{background:${darkTokens.surface.bgHover};}
.wfp-theme--dark .wfp-validation-panel__locate:focus-visible{outline-color:${darkTokens.status.running};}
`;

/** 校验问题面板：渲染后端校验报告并上抛定位/关闭意图 */
export default function ValidationIssuesPanel({
  report,
  onLocateNode,
  onClose,
}: ValidationIssuesPanelProps) {
  const themeMode = useThemeMode();
  const isDark = themeMode === "dark";
  const errorCount = report.issues.filter(
    (issue) => issue.severity === "error",
  ).length;
  const warningCount = report.issues.length - errorCount;

  return (
    <div
      role="alert"
      style={{
        position: "absolute",
        top: "8px",
        left: "8px",
        background: isDark ? "rgba(30,30,30,0.95)" : "rgba(255, 255, 255, 0.95)",
        border: `1px solid ${isDark ? darkTokens.surface.divider : "#e0e0e0"}`,
        borderRadius: "4px",
        padding: "8px 12px",
        fontSize: "12px",
        color: isDark ? darkTokens.text.primary : undefined,
        zIndex: 10,
        maxWidth: "380px",
        maxHeight: "50%",
        overflowY: "auto",
      }}
    >
      <style>{validationPanelCss}</style>
      <div className="wfp-validation-panel__header">
        <span className="wfp-validation-panel__title">
          校验未通过（{errorCount} 错误 / {warningCount} 警告）
        </span>
        <button
          type="button"
          className="wfp-validation-panel__close"
          onClick={onClose}
          aria-label="关闭校验面板"
          title="关闭校验面板"
        >
          ✕
        </button>
      </div>
      <ul className="wfp-validation-panel__list">
        {report.issues.map((issue, index) => {
          const locationText = buildLocationText(issue);
          const nodeId = issue.node_id;
          return (
            <li
              key={`${issue.code}-${index}`}
              className="wfp-validation-panel__item"
            >
              <span
                className="wfp-validation-panel__dot"
                style={{ backgroundColor: SEVERITY_DOT_COLOR[issue.severity] }}
              />
              <div className="wfp-validation-panel__body">
                <div className="wfp-validation-panel__headline">
                  <span className="wfp-validation-panel__code">{issue.code}</span>
                  <span>{issue.message}</span>
                </div>
                {locationText !== null && (
                  <div className="wfp-validation-panel__location">
                    {locationText}
                  </div>
                )}
              </div>
              {nodeId !== undefined && (
                <button
                  type="button"
                  className="wfp-validation-panel__locate"
                  onClick={() => onLocateNode(nodeId)}
                >
                  定位到画布
                </button>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
