// =============================================================================
// 面板样式基元（src/canvas/panels/shared/panelStyle.ts）
//
// 职责：节点配置面板滚动 / 类型身份 / 空态样式的单一来源（自 NCP 上提，T1.4 收口）。
// 全部为纯函数 + as const 令牌表，深浅色经 isDark 双轨切换（INV-5/INV-10）。
// =============================================================================

import { darkTokens } from "../../../theme/themeMode";
import type { NodeType } from "../../../workflow-definition/definitionTypes";

/**
 * 滚动令牌（ncp_independent_scroll CHG-01）：节点配置面板独立垂直滚动取值的单一来源。
 * 深浅色档与既有滚动条范式 wfp-adm-nav（AdminConsole）同源同值——宽 5px、圆角 3px、
 * 浅色滑块中灰 #d8d8d8 / 悬停 #bdbdbd；深色走 darkTokens.surface.border / borderStrong
 * 双轨取色（编译期强绑定，杜绝深色数值漂移）。
 * 高度约束链为何必需：overflow-y:auto 只有在容器具备确定高度基准时才会真正产生滚动，
 * 该基准由父链提供（App.tsx 主内容区三列容器 min-height:0 + overflow:hidden 兜底，
 * 见 CHG-05），本令牌不硬编码任何像素高度。
 */
export const ncpScrollTokens = {
  /** 条件式溢出：内容未溢出时零滚动条零占位，溢出才出现（FR-1.1/1.2/1.3/4.3） */
  overflowY: "auto",
  /** 滚动链隔离：面板滚动到边界后不带动页面/其他区域联动滚动（FR-2.1，仅本容器生效） */
  overscrollBehavior: "contain",
  /** WebKit 滚动条宽度档（px），同 wfp-adm-nav 既有档 */
  scrollbarWidthPx: 5,
  /** 滚动条滑块圆角档（px），同 wfp-adm-nav 既有档 */
  thumbRadiusPx: 3,
  /** 浅色档：轨道透明 + 中灰滑块（同 wfp-adm-nav 基础档） */
  light: { track: "transparent", thumb: "#d8d8d8", thumbHover: "#bdbdbd" },
  /** 深色档：darkTokens 双轨取色，两种模式下滚动条均清晰可辨（FR-4.5） */
  dark: {
    track: "transparent",
    thumb: darkTokens.surface.border,
    thumbHover: darkTokens.surface.borderStrong,
  },
  /** Firefox 滚动条宽度档：scrollbar-width 不接受像素值，走 thin 标准档 */
  firefox: { width: "thin" },
} as const;

/**
 * 滚动样式函数（CHG-01）：生成 .wfp-ncp-scroll 滚动条与滚动隔离的纯 CSS 文本。
 * 设计意图：
 *   1. WebKit/Chromium 走 ::-webkit-scrollbar 族自定义（细档 5px、轨道透明、灰阶滑块）；
 *   2. Firefox 双写为何必需：Firefox 不支持 ::-webkit-scrollbar 伪元素，必须等效提供
 *      scrollbar-width / scrollbar-color，保证跨内核滚动条同族观感（FR-4.2）；
 *   3. overscroll-behavior 为何需要：仅作用于本滚动容器——面板滚动到边界后阻止滚动
 *      传播至页面（滚动链隔离），未溢出时亦拦截滚轮穿透，且不影响页面合法滚动能力；
 *   4. 全部为 wfp- 前缀类选择器（禁元素选择器），颜色一律取自 ncpScrollTokens 单一
 *      来源，深浅色经 isDark 双轨切换（INV-5/INV-10）。
 */
export function scrollCss(isDark: boolean): string {
  const tone = isDark ? ncpScrollTokens.dark : ncpScrollTokens.light;
  return `
        /* 面板独立滚动（ncp_independent_scroll）：Firefox 等效双写 + 滚动链隔离 */
        .wfp-ncp-scroll {
          scrollbar-width: ${ncpScrollTokens.firefox.width};
          scrollbar-color: ${tone.thumb} ${tone.track};
          overscroll-behavior: ${ncpScrollTokens.overscrollBehavior};
        }
        /* WebKit/Chromium：细档滚动条（同 wfp-adm-nav 既有档） */
        .wfp-ncp-scroll::-webkit-scrollbar {
          width: ${ncpScrollTokens.scrollbarWidthPx}px;
        }
        .wfp-ncp-scroll::-webkit-scrollbar-track {
          background: ${tone.track};
        }
        .wfp-ncp-scroll::-webkit-scrollbar-thumb {
          background: ${tone.thumb};
          border-radius: ${ncpScrollTokens.thumbRadiusPx}px;
        }
        .wfp-ncp-scroll::-webkit-scrollbar-thumb:hover {
          background: ${tone.thumbHover};
        }
      `;
}

/**
 * 节点类型身份层（type identity）：面板身份取色与画布节点一致的单一来源。
 * 浅色档 = NodePalette 类型色条同源（tool 取 ToolNodeView 描边档）；
 * 深色档 = darkTokens.typeBorder 同源（色相不变、提亮一档）；
 * badge = 面板徽章文案，取 NodePalette 类型显示名 + "节点"
 * （Agent 为拉丁词，保留既有的中英间距；文案不得与任何 getByText 查询串重合）。
 * Record<NodeType, …> 使新增 NodeType 时编译期强制补全本表，杜绝身份色缺失降级。
 */
export const NCP_TYPE_IDENTITY: Record<
  NodeType,
  { light: string; dark: string; badge: string }
> = {
  trigger: { light: "#1976d2", dark: "#42a5f5", badge: "触发器节点" },
  agent: { light: "#7b1fa2", dark: "#ab47bc", badge: "Agent 节点" },
  tool: { light: "#388e3c", dark: "#66bb6a", badge: "工具节点" },
  data_source: { light: "#f57c00", dark: "#fb8c00", badge: "数据源节点" },
  control_flow: { light: "#c62828", dark: "#e53935", badge: "控制流节点" },
  subworkflow: { light: "#3f51b5", dark: "#5c6bc0", badge: "子工作流节点" },
  dynamic_injector: { light: "#9c27b0", dark: "#ba68c8", badge: "动态注入节点" },
  data_transform: { light: "#ff9800", dark: "#ffa726", badge: "数据转换节点" },
  output_display: { light: "#00897b", dark: "#26a69a", badge: "输出展示节点" },
  vector_knowledge_base: { light: "#2e7d32", dark: "#4caf50", badge: "向量知识库节点" },
  llm: { light: "#c2185b", dark: "#ec407a", badge: "大模型节点" },
  code: { light: "#455a64", dark: "#78909c", badge: "代码节点" },
};

/** 身份层 CSS 生成顺序（与视觉重要性无关，仅固定输出次序便于快照比对） */
const NCP_TYPE_IDENTITY_KEYS: readonly NodeType[] = [
  "trigger",
  "agent",
  "tool",
  "data_source",
  "control_flow",
  "subworkflow",
  "dynamic_injector",
  "data_transform",
  "output_display",
  "vector_knowledge_base",
  "llm",
  "code",
];

/**
 * 类型身份层 CSS 生成（type identity）：为全部 NodeType 生成
 * ".wfp-ncp-panel--<type>" 作用域的身份条渐变、徽章配色与输入聚焦环。
 * 设计意图：
 *   1. 十类节点与 Agent 面板同构：身份条 + 徽章 + 类型色聚焦环三者齐备，
 *      面板身份与画布节点描边取色一致（浅取 NodePalette、深取 typeBorder）；
 *   2. 透明度一律以 8 位 hex 后缀（accent+alpha）内联展开——禁 var(--x)
 *      自定义属性（H-08 契约：面板样式块禁 @keyframes / var(--）；
 *   3. 基类（.wfp-ncp-identity-bar / .wfp-ncp-type-badge）只承载几何，
 *      配色全部由本生成器按类型注入，避免任何类型被硬编码特判。
 */
export function typeIdentityCss(isDark: boolean): string {
  return NCP_TYPE_IDENTITY_KEYS.map((type) => {
    const identity = NCP_TYPE_IDENTITY[type];
    const accent = isDark ? identity.dark : identity.light;
    return `
        .wfp-ncp-panel--${type} .wfp-ncp-identity-bar {
          background: linear-gradient(90deg, ${accent}, ${accent}4D 55%, ${accent}00 100%);
        }
        .wfp-ncp-panel--${type} .wfp-ncp-type-badge {
          color: ${accent};
          background: ${accent}1A;
          border: 1px solid ${accent}40;
        }
        .wfp-ncp-panel--${type} .wfp-ncp-input:focus,
        .wfp-ncp-panel--${type} .wfp-ncp-select:focus {
          border-color: ${isDark ? accent : `${accent}8C`};
          box-shadow: 0 0 0 3px ${accent}${isDark ? "29" : "1A"};
        }`;
  }).join("");
}

/**
 * 未选中态空视样式：垂直居中的轻量空态（图标 + 主句 + 弱化说明），
 * 与面板选中态共用同一表面/描边取色，避免两种态观感割裂。
 */
export function emptyStateCss(isDark: boolean): string {
  return `
        .wfp-ncp-panel--empty {
          display: flex;
          align-items: flex-start;
          justify-content: center;
          padding-top: 56px;
        }
        .wfp-ncp-empty {
          display: flex;
          flex-direction: column;
          align-items: center;
          gap: 6px;
          text-align: center;
        }
        .wfp-ncp-empty-glyph {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          width: 34px;
          height: 34px;
          margin-bottom: 2px;
          border-radius: 10px;
          color: ${isDark ? darkTokens.text.secondary : "#8a8a8a"};
          background: ${isDark ? darkTokens.surface.bgHover : "#f2f2f2"};
          border: 1px solid ${isDark ? darkTokens.surface.divider : "#ececec"};
        }
        .wfp-ncp-empty-title {
          font-size: 12px;
          font-weight: 600;
          color: ${isDark ? darkTokens.text.secondary : "#6b6b6b"};
        }
        .wfp-ncp-empty-text {
          font-size: 11px;
          line-height: 1.5;
          color: ${isDark ? darkTokens.text.hint : "#9a9a9a"};
        }
      `;
}


/**
 * 主面板样式（调度壳选中态全量面板 CSS）：头部 / 字段 / 说明锚点 / 控件族 / 按钮族 /
 * 分组分节 / 徽章基类 / 知识库标识组合框 / 子卡片 / 提示条 / 行内说明行等；
 * 类型身份层与滚动条样式在文末统一注入。模板内容自 NCP 调度壳 <style> 块原样上提
 * （T1.4 收口），字节级保持以确保渲染输出零变化。
 */
export function mainPanelCss(isDark: boolean): string {
  return `
        /* —— 头部身份区：徽章 / 说明锚点 / 图标删除钮 / 标题 —— */
        .wfp-ncp-head {
          display: flex;
          flex-direction: column;
          gap: 7px;
          margin-bottom: 16px;
        }
        .wfp-ncp-head-row {
          position: relative;
          display: flex;
          align-items: center;
          gap: 6px;
          min-height: 20px;
        }
        .wfp-ncp-head-row .wfp-ncp-btn--icon {
          margin-left: auto;
        }
        .wfp-ncp-title {
          font-size: 15px;
          font-weight: 600;
          line-height: 1.35;
          margin: 0 0 14px 0;
          color: ${isDark ? darkTokens.text.primary : "#1a1a1a"};
          letter-spacing: -0.01em;
          overflow-wrap: anywhere;
        }
        .wfp-ncp-head .wfp-ncp-title {
          margin: 0;
        }
        /* —— 字段容器：标签行（标签 + 说明锚点）+ 控件 + 内联错误 —— */
        .wfp-ncp-field {
          display: block;
          position: relative;
          margin-bottom: 14px;
        }
        .wfp-ncp-field:last-child {
          margin-bottom: 0;
        }
        .wfp-ncp-labelrow {
          display: flex;
          align-items: center;
          gap: 5px;
          margin-bottom: 5px;
        }
        .wfp-ncp-field-label {
          display: block;
          font-size: 11px;
          font-weight: 600;
          letter-spacing: 0.02em;
          color: ${isDark ? darkTokens.text.secondary : "#5f5f5f"};
        }
        .wfp-ncp-control {
          display: block;
        }
        .wfp-ncp-error {
          display: flex;
          align-items: flex-start;
          gap: 5px;
          margin-top: 5px;
          font-size: 11px;
          line-height: 1.45;
          color: ${isDark ? darkTokens.danger : "#d32f2f"};
        }
        /* —— 说明锚点（悬停/聚焦出气泡）与气泡本体 ——
           气泡 DOM 常驻（aria-describedby 目标不随交互消失），
           仅以 opacity/visibility 切换可见性，避免读屏与命中区抖动。 */
        .wfp-ncp-hint-anchor {
          position: relative;
          display: inline-flex;
          align-items: center;
        }
        .wfp-ncp-hint-anchor--row {
          position: static;
        }
        .wfp-ncp-hint-btn {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          width: 16px;
          height: 16px;
          padding: 0;
          border: none;
          border-radius: 4px;
          background: transparent;
          color: ${isDark ? darkTokens.text.secondary : "#8f8f8f"};
          cursor: help;
          transition: color 120ms ease, background-color 120ms ease;
        }
        .wfp-ncp-hint-btn:hover {
          color: ${isDark ? darkTokens.text.secondary : "#6b6b6b"};
          background: ${isDark ? darkTokens.surface.bgHover : "#f2f2f2"};
        }
        .wfp-ncp-hint-btn:focus-visible {
          outline: none;
          color: ${isDark ? darkTokens.text.secondary : "#6b6b6b"};
          box-shadow: 0 0 0 2px ${isDark ? "rgba(102,102,102,0.45)" : "rgba(150,150,150,0.35)"};
        }
        .wfp-ncp-field-hint,
        .wfp-ncp-node-hint {
          position: absolute;
          top: calc(100% + 6px);
          left: 0;
          z-index: 60;
          box-sizing: border-box;
          width: max-content;
          max-width: 210px;
          margin: 0;
          padding: 8px 10px;
          border-radius: 6px;
          border: 1px solid ${isDark ? darkTokens.surface.divider : "#e2e2e2"};
          background: ${isDark ? darkTokens.surface.bgHover : "#ffffff"};
          color: ${isDark ? darkTokens.text.secondary : "#5f5f5f"};
          font-size: 11px;
          font-weight: 400;
          line-height: 1.55;
          letter-spacing: 0;
          text-align: left;
          white-space: normal;
          box-shadow: 0 8px 20px ${isDark ? "rgba(0, 0, 0, 0.55)" : "rgba(15, 15, 15, 0.12)"};
          pointer-events: none;
          opacity: 0;
          visibility: hidden;
          transform: translateY(-2px);
          transition: opacity 120ms ease, transform 120ms ease, visibility 120ms;
        }
        .wfp-ncp-hint-anchor:hover .wfp-ncp-field-hint,
        .wfp-ncp-hint-anchor:focus-within .wfp-ncp-field-hint,
        .wfp-ncp-hint-anchor:hover .wfp-ncp-node-hint,
        .wfp-ncp-hint-anchor:focus-within .wfp-ncp-node-hint {
          opacity: 1;
          visibility: visible;
          transform: translateY(0);
        }
        /* 节点级说明：整行宽气泡（锚点行即定位块） */
        .wfp-ncp-node-hint {
          left: 0;
          right: 0;
          width: auto;
          max-width: none;
        }
        /* —— 控件族：只读 / 下拉 / 可编辑 —— */
        .wfp-ncp-readonly-input {
          width: 100%;
          box-sizing: border-box;
          padding: 7px 10px;
          font-size: 12.5px;
          font-weight: 500;
          font-family: inherit;
          color: ${isDark ? darkTokens.text.primary : "#333333"};
          background: ${isDark ? darkTokens.surface.bgHover : "#f5f5f5"};
          border: 1px solid ${isDark ? darkTokens.surface.divider : "#e0e0e0"};
          border-radius: 6px;
          cursor: default;
          outline: none;
          transition: background-color 120ms ease, border-color 120ms ease;
        }
        .wfp-ncp-readonly-input:focus {
          border-color: ${isDark ? darkTokens.surface.borderStrong : "#bdbdbd"};
          background: ${isDark ? darkTokens.surface.bgActive : "#efefef"};
        }
        /* 通用 select 样式：几何对齐 wfp-ncp-input，cursor 为 pointer、自定义箭头确保深色可见 */
        .wfp-ncp-select {
          width: 100%;
          box-sizing: border-box;
          padding: 7px 26px 7px 10px;
          font-size: 12.5px;
          font-weight: 500;
          font-family: inherit;
          color: ${isDark ? darkTokens.text.primary : "#333333"};
          background-color: ${isDark ? darkTokens.surface.bgHover : "#f7f7f7"};
          background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='8' height='5' viewBox='0 0 8 5'%3E%3Cpath d='M0 0 L4 5 L8 0 Z' fill='${isDark ? "%238a8a8a" : "%23757575"}'/%3E%3C/svg%3E");
          background-repeat: no-repeat;
          background-position: right 9px center;
          border: 1px solid ${isDark ? darkTokens.surface.divider : "#e0e0e0"};
          border-radius: 6px;
          cursor: pointer;
          outline: none;
          -webkit-appearance: none;
          -moz-appearance: none;
          appearance: none;
          transition: background-color 120ms ease, border-color 120ms ease;
        }
        .wfp-ncp-select:hover {
          background-color: ${isDark ? darkTokens.surface.bgActive : "#efefef"};
        }
        .wfp-ncp-select:focus {
          border-color: ${isDark ? darkTokens.surface.borderStrong : "#bdbdbd"};
          background-color: ${isDark ? darkTokens.surface.bgActive : "#efefef"};
          box-shadow: 0 0 0 3px ${isDark ? "rgba(102, 102, 102, 0.18)" : "rgba(189, 189, 189, 0.22)"};
        }
        /* 可编辑输入框：亮面背景与只读灰面形成可编辑/只读语义区分 */
        .wfp-ncp-input {
          width: 100%;
          box-sizing: border-box;
          padding: 7px 10px;
          font-size: 12.5px;
          font-weight: 500;
          font-family: inherit;
          color: ${isDark ? darkTokens.text.primary : "#333333"};
          background-color: ${isDark ? darkTokens.surface.bg : "#ffffff"};
          border: 1px solid ${isDark ? darkTokens.surface.divider : "#e0e0e0"};
          border-radius: 6px;
          outline: none;
          transition: background-color 120ms ease, border-color 120ms ease;
        }
        .wfp-ncp-input:hover {
          background-color: ${isDark ? darkTokens.surface.bgHover : "#fafafa"};
        }
        .wfp-ncp-input:focus {
          border-color: ${isDark ? darkTokens.surface.borderStrong : "#bdbdbd"};
          background-color: ${isDark ? darkTokens.surface.bg : "#ffffff"};
          box-shadow: 0 0 0 3px ${isDark ? "rgba(102, 102, 102, 0.18)" : "rgba(189, 189, 189, 0.22)"};
        }
        .wfp-ncp-input::placeholder {
          color: ${isDark ? darkTokens.text.hint : "#b0b0b0"};
        }
        /* —— 按钮族：基类承载几何，--icon 收成方形图标钮 —— */
        .wfp-ncp-btn {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          gap: 5px;
          font-size: 12px;
          font-weight: 500;
          font-family: inherit;
          padding: 5px 10px;
          border: 1px solid transparent;
          border-radius: 6px;
          background: transparent;
          cursor: pointer;
          outline: none;
          flex-shrink: 0;
          transition: background-color 120ms ease, border-color 120ms ease, box-shadow 120ms ease;
        }
        .wfp-ncp-btn--icon {
          width: 26px;
          height: 26px;
          padding: 0;
          gap: 0;
        }
        .wfp-ncp-btn--ghost {
          color: ${isDark ? darkTokens.text.secondary : "#5f5f5f"};
          background: ${isDark ? darkTokens.surface.bgHover : "#f5f5f5"};
          border-color: ${isDark ? darkTokens.surface.divider : "#e0e0e0"};
        }
        .wfp-ncp-btn--ghost:hover {
          background: ${isDark ? darkTokens.surface.bgActive : "#ececec"};
        }
        /* 危险态：浅色 #d32f2f，白底透明 */
        .wfp-ncp-btn--danger {
          color: #d32f2f;
          background: transparent;
          border: 1px solid #d32f2f;
        }
        .wfp-ncp-btn--danger:hover {
          background: #fdecea;
        }
        .wfp-ncp-btn--danger:active {
          background: #f8d7da;
        }
        .wfp-ncp-btn:focus-visible {
          box-shadow: 0 0 0 2px #f0a8a3;
        }
        .wfp-ncp-btn:disabled {
          color: #bdbdbd;
          border-color: #e0e0e0;
          background: transparent;
          cursor: not-allowed;
          opacity: 0.6;
        }
        /* 深色作用域覆盖：危险色提亮档，浅色规则文本逐字符不动 */
        .wfp-theme--dark .wfp-ncp-btn--danger {
          color: ${darkTokens.danger};
          border-color: ${darkTokens.danger};
        }
        .wfp-theme--dark .wfp-ncp-btn--danger:hover {
          background: rgba(255, 107, 107, 0.12);
        }
        .wfp-theme--dark .wfp-ncp-btn--danger:active {
          background: rgba(255, 107, 107, 0.2);
        }
        .wfp-theme--dark .wfp-ncp-btn:focus-visible {
          box-shadow: 0 0 0 2px ${darkTokens.text.focus};
        }
        .wfp-theme--dark .wfp-ncp-btn:disabled {
          color: #666666;
          border-color: ${darkTokens.surface.divider};
        }
        /* —— 分组与分节：分组留白 + 小节标题（右侧细线延伸）—— */
        .wfp-ncp-group {
          margin-bottom: 16px;
        }
        .wfp-ncp-group:last-child {
          margin-bottom: 0;
        }
        .wfp-ncp-divider {
          height: 1px;
          margin: 16px 0;
          background: ${isDark ? darkTokens.surface.divider : "#efefef"};
        }
        .wfp-ncp-agent-form {
          font-size: 12px;
        }
        .wfp-ncp-agent-section {
          margin-bottom: 16px;
        }
        .wfp-ncp-agent-section:last-child {
          margin-bottom: 0;
        }
        .wfp-ncp-section-label {
          display: flex;
          align-items: center;
          gap: 8px;
          font-size: 10px;
          font-weight: 600;
          letter-spacing: 0.08em;
          margin: 0 0 10px 0;
          user-select: none;
          color: ${isDark ? darkTokens.text.secondary : "#6b6b6b"};
        }
        .wfp-ncp-section-label::after {
          content: "";
          flex: 1;
          height: 1px;
          background: ${isDark ? darkTokens.surface.divider : "#ececec"};
        }
        /* —— 输出方式复选框行（optimize_output_display_node CHG-09）—— */
        .wfp-ncp-odc-mode-row {
          display: flex;
          align-items: center;
          gap: 6px;
          padding: 4px 6px;
          margin-bottom: 2px;
          font-size: 12px;
          cursor: pointer;
          border-radius: 4px;
          color: ${isDark ? darkTokens.text.primary : "#333"};
        }
        .wfp-ncp-odc-mode-row:hover {
          background: ${isDark ? darkTokens.surface.bgHover : "#f5f5f5"};
        }
        /* —— 节点类型身份层：基类只承载几何，配色由 typeIdentityCss 按类型注入 —— */
        .wfp-ncp-identity-bar {
          height: 2px;
          margin: -16px -16px 14px -16px;
        }
        .wfp-ncp-type-badge {
          display: inline-flex;
          align-items: center;
          font-size: 10px;
          font-weight: 600;
          letter-spacing: 0.06em;
          padding: 2px 8px;
          border-radius: 999px;
          user-select: none;
        }
        /* —— 知识库标识组合框：自由文本 + 已配置知识库下拉候选（自动检测）——
           候选经 kbManagementApiClient.listKnowledgeBases 拉取；失败/为空时调用方
           不渲染触发钮与菜单，降级为纯文本输入（与 KBSL 零破坏降级同族）。
           选中项左轨取 KB 类型色（浅 #2e7d32 / 深 #4caf50，typeBorder 同源）。 */
        .wfp-kbid-combo {
          position: relative;
        }
        .wfp-kbid-input {
          padding-right: 28px;
        }
        .wfp-kbid-trigger {
          position: absolute;
          top: 50%;
          right: 2px;
          transform: translateY(-50%);
          display: flex;
          align-items: center;
          justify-content: center;
          width: 24px;
          height: 22px;
          padding: 0;
          border: none;
          border-radius: 4px;
          background: transparent;
          cursor: pointer;
          color: ${isDark ? darkTokens.text.hint : "#9a9a9a"};
        }
        .wfp-kbid-trigger:hover {
          color: ${isDark ? darkTokens.text.secondary : "#666666"};
          background: ${isDark ? darkTokens.surface.bgHover : "#f5f5f5"};
        }
        .wfp-kbid-trigger svg {
          transition: transform 120ms ease;
        }
        .wfp-kbid-trigger[aria-expanded="true"] svg {
          transform: rotate(180deg);
        }
        .wfp-kbid-menu {
          position: absolute;
          top: calc(100% + 4px);
          left: 0;
          right: 0;
          z-index: 30;
          max-height: 192px;
          overflow-y: auto;
          padding: 4px;
          border: 1px solid ${isDark ? darkTokens.surface.divider : "#e0e0e0"};
          border-radius: 6px;
          background: ${isDark ? darkTokens.surface.bg : "#ffffff"};
          box-shadow: 0 6px 16px ${isDark ? "rgba(0, 0, 0, 0.5)" : "rgba(0, 0, 0, 0.12)"};
        }
        .wfp-kbid-option {
          display: flex;
          align-items: center;
          gap: 6px;
          width: 100%;
          box-sizing: border-box;
          padding: 5px 8px;
          font-size: 12px;
          font-family: inherit;
          text-align: left;
          border: none;
          border-left: 2px solid transparent;
          border-radius: 4px;
          cursor: pointer;
          color: ${isDark ? darkTokens.text.primary : "#333333"};
          background: transparent;
        }
        .wfp-kbid-option:hover {
          background: ${isDark ? darkTokens.surface.bgHover : "#f5f5f5"};
        }
        .wfp-kbid-option-selected {
          border-left-color: ${isDark ? "#4caf50" : "#2e7d32"};
          background: ${isDark ? "rgba(76, 175, 80, 0.1)" : "#f1f8f2"};
        }
        .wfp-kbid-option-id {
          flex: 1;
          min-width: 0;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
          font-family: Consolas, Menlo, monospace;
        }
        .wfp-kbid-option-name {
          flex-shrink: 0;
          max-width: 40%;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
          font-size: 10px;
          padding: 1px 5px;
          border-radius: 3px;
          color: ${isDark ? darkTokens.text.secondary : "#666666"};
          background: ${isDark ? darkTokens.surface.bgHover : "#f0f0f0"};
        }
        /* —— 子分块与子卡片（动态注入模板等可增删条目）—— */
        .wfp-ncp-subblock {
          margin-top: 4px;
        }
        .wfp-ncp-subcard {
          border: 1px solid ${isDark ? darkTokens.surface.divider : "#e8e8e8"};
          border-radius: 7px;
          padding: 9px 9px 4px 9px;
          margin-bottom: 8px;
          background: ${isDark ? "rgba(255, 255, 255, 0.02)" : "#fcfcfc"};
        }
        .wfp-ncp-subcard-head {
          display: flex;
          align-items: center;
          gap: 6px;
          margin-bottom: 8px;
        }
        .wfp-ncp-subcard-title {
          flex: 1;
          min-width: 0;
          font-size: 11px;
          font-weight: 600;
          letter-spacing: 0.02em;
          color: ${isDark ? darkTokens.text.secondary : "#5f5f5f"};
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .wfp-ncp-subcard-add {
          width: 100%;
          margin-top: 2px;
        }
        /* 提示条（迁移/降级等状态说明）：左轨 + 中性底，不与错误红混淆 */
        .wfp-ncp-notice {
          display: flex;
          align-items: flex-start;
          gap: 6px;
          margin-bottom: 12px;
          padding: 7px 9px;
          border-radius: 6px;
          border: 1px solid ${isDark ? darkTokens.surface.divider : "#ececec"};
          border-left: 2px solid ${isDark ? darkTokens.text.hint : "#bdbdbd"};
          background: ${isDark ? darkTokens.surface.bgHover : "#f7f7f7"};
          color: ${isDark ? darkTokens.text.secondary : "#5f5f5f"};
          font-size: 11px;
          line-height: 1.5;
        }
        .wfp-ncp-notice-glyph {
          flex-shrink: 0;
          margin-top: 1px;
          color: ${isDark ? darkTokens.text.hint : "#9a9a9a"};
        }
        /* 行内说明行：图标 + 短标签，长文案收进悬停气泡 */
        .wfp-ncp-inline-row {
          display: flex;
          align-items: center;
          gap: 4px;
          margin: -4px 0 10px 0;
        }
        .wfp-ncp-inline-row--warn .wfp-ncp-inline-label {
          color: ${isDark ? darkTokens.danger : "#d32f2f"};
        }
        .wfp-ncp-inline-label {
          font-size: 11px;
          line-height: 1.45;
          color: ${isDark ? darkTokens.text.hint : "#9a9a9a"};
        }
        ${typeIdentityCss(isDark)}
        ${scrollCss(isDark)}
      `;
}
