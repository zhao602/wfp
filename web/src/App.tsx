// =============================================================================
// 根组件（src/App.tsx）
//
// 职责：应用顶层布局，组合画布、节点面板、配置面板与范例库面板。
// 设计意图：单一根组件聚合各功能区域，子区域由各自组件管理内部状态。
// =============================================================================

import { useState, useCallback, useEffect, useRef } from "react";
import WorkflowCanvas from "./canvas/WorkflowCanvas";
import CanvasErrorBoundary from "./canvas/CanvasErrorBoundary";
import NodePalette from "./canvas/NodePalette";
import NodeConfigurationPanel from "./canvas/NodeConfigurationPanel";
import ResizableSplitter from "./canvas/ResizableSplitter";
import ExampleLibraryPanel from "./example-library/ExampleLibraryPanel";
import YamlFileImportButton from "./example-library/YamlFileImportButton";
import NodeExecutionStatusOverlay from "./execution-visualization/NodeExecutionStatusOverlay";
import type { WorkflowCanvasState, CanvasNodeData } from "./canvas/canvasTypes";
import { removeNode } from "./canvas/canvasMutations";
import { applyAgentTemplateToCanvas } from "./canvas/applyAgentTemplate";
import type { AgentTemplate } from "./market/marketTypes";
import { migrateCanvasState } from "./canvas/canvasStateMigrator";
import {
  createDebouncedSaver,
  type DebouncedSaver,
} from "./canvas/persistenceSaver";
import { useCanvasHistory, HISTORY_LIMIT } from "./canvas/useCanvasHistory";
import { canvasToYaml } from "./workflow-definition/canvasToYamlExporter";
import { orchestrationApiClient } from "./api/orchestrationApiClient";
import { ApiError } from "./api/apiError";
import { yamlToCanvas } from "./workflow-definition/yamlToCanvasImporter";
import { AdminConsole } from "./admin/AdminConsole";
import { useHashRoute, navigate, DEFAULT_ADMIN_PANEL } from "./router";
import {
  initialThemeMode,
  persistTheme,
  syncRootThemeClass,
  ThemeContext,
  darkTokens,
  THEME_ROOT_CLASS,
} from "./theme/themeMode";
import type { ThemeMode } from "./theme/themeMode";
import {
  PRODUCT_NAME,
  PRIMARY_COLOR,
  PRIMARY_COLOR_HOVER,
  PRIMARY_COLOR_ACTIVE,
  PRIMARY_COLOR_LIGHT,
  PRIMARY_COLOR_LIGHT_HOVER,
} from "./brand/brandConfig";

/**
 * 顶栏设计令牌（精炼极简工具风）。
 * 用途：header 内联 style 与 topbarCss 注入块的唯一取值来源；
 * 不使用 CSS 变量，避免全局样式污染（对应需求规格 4.4.2 / 1.4.4）。
 */
const topbarTokens = {
  /** 色彩：全部锁定在中性灰阶与现有红色系（spec 5.1.1.4a） */
  color: {
    surface: "#ffffff", // 顶栏底色：显式白底，与页面整体一致
    border: "#e0e0e0", // 顶栏底部分隔线：与页脚上边框同款浅灰
    textPrimary: "#1a1a1a", // 标题文字：近黑，确立顶栏视觉锚点
    textSecondary: "#666666", // 状态区标签色：沿用 ExampleLibraryPanel 现值
    textHint: "#999999", // 加载中等最弱提示色
    danger: "#d32f2f", // 错误警示：沿用现有红色系（spec 4.5.2）
    btnBg: "#ffffff", // 按钮默认背景
    btnBgHover: "#f5f5f5", // 按钮悬停背景：极浅灰，克制反馈
    btnBgActive: "#e8e8e8", // 按钮按压背景：比悬停深一档，确认响应
    btnBorder: "#d0d0d0", // 按钮默认描边：比 #e0e0e0 深半档，保证可辨识
    btnBorderHover: "#b8b8b8", // 悬停/按压描边：加深以强化边界
    btnFocus: "#333333", // 焦点轮廓：中性深灰（禁令色域内的最深可用色）
  },
  /** 字号：标题不增大（spec 5.2.1.2a），按钮不大于标题（spec 5.3.1.1a） */
  fontSize: { title: "16px", label: "13px", body: "12px", button: "13px" },
  /** 间距：paddingY 8→10px 顶栏总高 +4px，属"不显著增大"范围 */
  spacing: {
    paddingX: "16px",
    paddingY: "10px",
    gap: "16px",
    gapInner: "8px",
    btnPaddingY: "5px",
    btnPaddingX: "14px",
  },
  /** 圆角：克制的小圆角，工具风取向 */
  radius: { btn: "6px", btnSm: "4px" },
  /** 动效：短时长线性缓动，即时感优先（spec 4.1.1 无感知延迟） */
  motion: { duration: "120ms", easing: "ease" },
  /** 深色分节：逐键引用 darkTokens（编译期强绑定，spec 5.3.3.2） */
  dark: {
    color: {
      surface: darkTokens.surface.bg, // 顶栏底色
      border: darkTokens.surface.divider, // 分隔线
      textPrimary: darkTokens.text.primary, // 标题文字
      textSecondary: darkTokens.text.secondary, // 次级文字
      textHint: darkTokens.text.hint, // 提示文字
      danger: darkTokens.danger, // 错误警示
      btnBg: darkTokens.surface.bg, // 按钮背景
      btnBgHover: darkTokens.surface.bgHover, // 悬停背景
      btnBgActive: darkTokens.surface.bgActive, // 按压背景
      btnBorder: darkTokens.surface.border, // 描边
      btnBorderHover: darkTokens.surface.borderStrong, // 深描边
      btnFocus: darkTokens.text.focus, // 焦点轮廓
    },
  },
  /** 主题切换按钮图形指示：尺寸/间距/线宽三取值（值域单一，spec 5.2.1.2/5.2.1.3/6.3.6） */
  themeIcon: { size: 14, gap: "6px", strokeWidth: 1.25 },
} as const;

/**
 * 主题切换按钮的模式指示图形（单一 SVG 定义，spec 5.1.1.2/5.2.1.5/5.4.1.3）。
 * 月/日两组图元共处同一个 <svg>，无分叉、无 React 状态：
 *   · 无任何文本节点（禁 text/title/desc），按钮唯一直接文本节点恒为两态互斥文案；
 *   · 着色使用 stroke="currentColor" 继承按钮文字色，无硬编码色值、双主题自动同步；
 *   · 浅色默认渲染「月」、日组由表现属性 display="none" 隐藏；深色由 topbarCss 末尾
 *     作用域规则反转（moon→none / sun→inline），纯由 .wfp-theme--dark 驱动（spec 6.2.2）。
 */
const themeIndicator = (
  <svg
    className="wfp-topbar-theme-icon"
    viewBox="0 0 14 14"
    width={topbarTokens.themeIcon.size}
    height={topbarTokens.themeIcon.size}
    aria-hidden="true"
    focusable="false"
    stroke="currentColor"
    fill="none"
    strokeWidth={topbarTokens.themeIcon.strokeWidth}
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    {/* 月：浅色态可见（默认渲染），极简新月单条 path */}
    <path
      className="wfp-topbar-theme-icon__moon"
      d="M12.25 7.46A5.25 5.25 0 1 1 6.54 1.75a4.08 4.08 0 0 0 5.71 5.71z"
    />
    {/* 日：深色态可见（浅色由 display="none" 隐藏，深色由 topbarCss 末尾规则反转） */}
    <g className="wfp-topbar-theme-icon__sun" display="none">
      <circle cx="7" cy="7" r="3" />
      <path d="M7 1v1.5 M7 11.5V13 M1 7h1.5 M11.5 7H13 M2.6 2.6l1.06 1.06 M10.34 10.34l1.06 1.06 M11.4 2.6l-1.06 1.06 M3.66 10.34l-1.06 1.06" />
    </g>
  </svg>
);

/**
 * 顶栏 `<style>` 注入块文本：内插 topbarTokens 生成 CSS 类族。
 * 仅使用类选择器（wfp-topbar- 前缀限定作用域），随 header 挂载注入、卸载移除，
 * 往返切换视图后样式天然复位、不污染全局（对应需求规格 4.4.2 / 5.3.3.1）。
 */
const topbarCss = `
.wfp-topbar-btn {
  /* 默认态：消除浏览器原生按钮外观残留（spec 5.3.1.1a） */
  font-family: inherit;
  font-size: ${topbarTokens.fontSize.button};
  line-height: 1.2;
  color: ${topbarTokens.color.textPrimary};
  background: ${topbarTokens.color.btnBg};
  border: 1px solid ${topbarTokens.color.btnBorder};
  border-radius: ${topbarTokens.radius.btn};
  padding: ${topbarTokens.spacing.btnPaddingY} ${topbarTokens.spacing.btnPaddingX};
  cursor: pointer;
  transition: background-color ${topbarTokens.motion.duration} ${topbarTokens.motion.easing},
    border-color ${topbarTokens.motion.duration} ${topbarTokens.motion.easing},
    box-shadow ${topbarTokens.motion.duration} ${topbarTokens.motion.easing};
}
.wfp-topbar-btn:hover {
  /* 悬停态：背景与描边可见变化（spec 5.3.1.3a） */
  background: ${topbarTokens.color.btnBgHover};
  border-color: ${topbarTokens.color.btnBorderHover};
}
.wfp-topbar-btn:active {
  /* 按压态：书写于 :hover 之后，层叠序保证"按压优先于悬停"（spec 6.1.1） */
  background: ${topbarTokens.color.btnBgActive};
  border-color: ${topbarTokens.color.btnBorderHover};
  box-shadow: inset 0 1px 2px rgba(0, 0, 0, 0.06);
}
.wfp-topbar-btn:focus-visible {
  /* 焦点态：仅键盘导航可见，鼠标点击不显圈（spec 5.3.1.5a） */
  outline: 2px solid ${topbarTokens.color.btnFocus};
  outline-offset: 2px;
}
.wfp-topbar-btn--sm {
  /* 小号变体：范例按钮复用基类后覆盖尺寸（spec 5.2.1.7a） */
  font-size: ${topbarTokens.fontSize.body};
  padding: 2px 10px;
  border-radius: ${topbarTokens.radius.btnSm};
}
/* —— 深色作用域规则：追加于末尾，浅色规则文本逐字符不动（spec 5.3.1.5a/5.3.3.3） —— */
.wfp-theme--dark .wfp-topbar-btn {
  /* 深色默认态：不声明 transition，继承浅色 120ms（spec 5.3.1.9a） */
  color: ${topbarTokens.dark.color.textPrimary};
  background: ${topbarTokens.dark.color.btnBg};
  border-color: ${topbarTokens.dark.color.btnBorder};
}
.wfp-theme--dark .wfp-topbar-btn:hover {
  background: ${topbarTokens.dark.color.btnBgHover};
  border-color: ${topbarTokens.dark.color.btnBorderHover};
}
.wfp-theme--dark .wfp-topbar-btn:active {
  /* 书写于 :hover 后，层叠序保持（spec 6.1.1） */
  background: ${topbarTokens.dark.color.btnBgActive};
  border-color: ${topbarTokens.dark.color.btnBorderHover};
  box-shadow: inset 0 1px 2px rgba(0, 0, 0, 0.4);
}
.wfp-theme--dark .wfp-topbar-btn:focus-visible {
  /* outline-offset 沿用浅色值，仅覆盖颜色（spec 5.6.1.2a） */
  outline-color: ${topbarTokens.dark.color.btnFocus};
}
/* —— 主题切换按钮：模式指示图形深色作用域显隐（仅末尾追加，浅色规则文本逐字符不动） —— */
.wfp-theme--dark .wfp-topbar-theme-icon__moon { display: none; }
.wfp-theme--dark .wfp-topbar-theme-icon__sun { display: inline; }
/* —— 标题图形标记：工作流图标的呼吸动效与焦点节点强调（Linear Aesthetic） —— */
.wfp-topbar-title__mark {
  flex-shrink: 0;
  animation: wfp-title-breathe 3s ease-in-out infinite;
}
.wfp-topbar-title__accent {
  fill: currentColor;
  fill-opacity: 0.15;
  stroke: none;
}
@keyframes wfp-title-breathe {
  0%, 100% { opacity: 0.8; }
  50% { opacity: 1; }
}
@media (prefers-reduced-motion: reduce) {
  .wfp-topbar-title__mark {
    animation: none;
  }
}
`;

/**
 * 导出图标：13×13 内联 SVG，上箭头出顶线，stroke=currentColor 继承按钮文字色
 */
const exportIcon = (
  <svg
    viewBox="0 0 14 14"
    width={13}
    height={13}
    aria-hidden="true"
    focusable="false"
    stroke="currentColor"
    fill="none"
    strokeWidth={1.25}
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M7 12V5" />
    <path d="M3.5 8.5L7 5l3.5 3.5" />
    <path d="M2.5 2.5h9" />
  </svg>
);

const runIcon = (
  <svg
    viewBox="0 0 14 14"
    width={13}
    height={13}
    aria-hidden="true"
    focusable="false"
    fill="currentColor"
  >
    <path d="M3.5 2.5v9l8-4.5z" />
  </svg>
);

/**
 * 页脚设计令牌（精炼极简工具风）。
 * 用途：页脚导出按钮皮肤（footerCss 注入块）的唯一取值来源。
 * 同源策略：与 topbarTokens 同居本文件，按钮色谱/字号/圆角/动效
 * 直接引用 topbarTokens 对应键（同源同值编译期强绑定，杜绝数值漂移，
 * 对应 spec 5.3.1.1a / 5.3.3.2）；页脚独有的紧凑内边距与现状
 * 容器间距为本文件新声明值并注释依据 —— 沿用既有"注释溯源"写法，
 * 但同文件内以直接引用取代跨文件字面量复制（零成环风险，见 design.md 2.1.3.1）。
 */
const footerTokens = {
  /** 色彩：与顶栏按钮同语言（spec 5.2.1.10a），逐键同源引用 topbarTokens.color */
  color: {
    /** 按钮文字：近黑，与顶栏按钮同值 */
    btnText: topbarTokens.color.textPrimary,
    /** 按钮默认背景：白底 */
    btnBg: topbarTokens.color.btnBg,
    /** 按钮悬停背景：极浅灰，克制反馈 */
    btnBgHover: topbarTokens.color.btnBgHover,
    /** 按钮按压背景：比悬停深一档，确认响应 */
    btnBgActive: topbarTokens.color.btnBgActive,
    /** 按钮默认描边：与顶栏按钮同值 */
    btnBorder: topbarTokens.color.btnBorder,
    /** 悬停/按压描边：加深以强化边界 */
    btnBorderHover: topbarTokens.color.btnBorderHover,
    /** 焦点轮廓：中性深灰（仅键盘可见） */
    btnFocus: topbarTokens.color.btnFocus,
    /** 页脚上边框：与顶栏下边框同源（spec 5.1.1.2a 上下呼应；现值保持，仅作源记录） */
    containerBorder: topbarTokens.color.border,
  },
  /** 字号：按钮与顶栏同级 13px（spec 5.3.1.2a 字号节奏延续） */
  fontSize: { button: topbarTokens.fontSize.button },
  /** 间距：btn 为页脚次级紧凑内边距；footerPadding 为现状容器值记录
   *  （spec 6.2.3 保持，容器内联 style 零改动，仅作源记录） */
  spacing: {
    footerPaddingY: "4px", // 页脚现状纵向内边距（App.tsx:235 现值）
    footerPaddingX: "16px", // 页脚现状横向内边距（App.tsx:235 现值）
    btnPaddingY: "3px", // 按钮纵向内边距：较顶栏(5px)降 2px，落实次级定位
    btnPaddingX: "12px", // 按钮横向内边距：较顶栏(14px)降 2px
  },
  /** 圆角：与顶栏按钮同级 6px（spec 5.3.1.3a 圆角尺度延续） */
  radius: { btn: topbarTokens.radius.btn },
  /** 动效：与顶栏同款短过渡（spec 5.2.1.10a 同动效规格） */
  motion: {
    duration: topbarTokens.motion.duration,
    easing: topbarTokens.motion.easing,
  },
  /** 深色分节：与 topbarTokens.dark.color 同源同值（spec 5.3.3.2 杜绝漂移） */
  dark: {
    color: {
      btnText: darkTokens.text.primary,
      btnBg: darkTokens.surface.bg,
      btnBgHover: darkTokens.surface.bgHover,
      btnBgActive: darkTokens.surface.bgActive,
      btnBorder: darkTokens.surface.border,
      btnBorderHover: darkTokens.surface.borderStrong,
      btnFocus: darkTokens.text.focus,
      containerBorder: darkTokens.surface.divider,
    },
  },
} as const;

/**
 * 页脚 `<style>` 注入块文本：内插 footerTokens 生成 CSS 类族。
 * 仅使用类选择器（wfp-footer- 前缀限定作用域，与 wfp-topbar-/wfp-palette-/
 * wfp-splitter- 隔离共存），随 footer 挂载注入、卸载移除，往返视图后样式
 * 天然复位（spec 4.4.2 / 5.1.3.2）；transition 仅允许 background-color /
 * border-color / box-shadow 三属性，禁尺寸/布局类过渡（spec 5.2.1.7a/8a）。
 */
const footerCss = `
.wfp-footer-btn {
  /* 默认态：消除浏览器原生按钮外观残留（spec 5.2.1.1a） */
  font-family: inherit;
  font-size: ${footerTokens.fontSize.button};
  line-height: 1.2;
  color: ${footerTokens.color.btnText};
  background: ${footerTokens.color.btnBg};
  border: 1px solid ${footerTokens.color.btnBorder};
  border-radius: ${footerTokens.radius.btn};
  padding: ${footerTokens.spacing.btnPaddingY} ${footerTokens.spacing.btnPaddingX};
  cursor: pointer;
  transition: background-color ${footerTokens.motion.duration} ${footerTokens.motion.easing},
    border-color ${footerTokens.motion.duration} ${footerTokens.motion.easing},
    box-shadow ${footerTokens.motion.duration} ${footerTokens.motion.easing};
}
.wfp-footer-btn:hover {
  /* 悬停态：背景与描边可见变化（spec 5.2.1.3a） */
  background: ${footerTokens.color.btnBgHover};
  border-color: ${footerTokens.color.btnBorderHover};
}
.wfp-footer-btn:active {
  /* 按压态：书写于 :hover 之后，层叠序保证“按压优先于悬停”（spec 6.1.1） */
  background: ${footerTokens.color.btnBgActive};
  border-color: ${footerTokens.color.btnBorderHover};
  box-shadow: inset 0 1px 2px rgba(0, 0, 0, 0.06);
}
.wfp-footer-btn:focus-visible {
  /* 焦点态：仅键盘导航可见，鼠标点击不显圈（spec 5.2.1.5a） */
  outline: 2px solid ${footerTokens.color.btnFocus};
  outline-offset: 2px;
}
/* —— 深色作用域规则：追加于末尾，浅色规则文本逐字符不动（spec 5.3.1.5a/5.3.3.3） —— */
.wfp-theme--dark .wfp-footer-btn {
  color: ${footerTokens.dark.color.btnText};
  background: ${footerTokens.dark.color.btnBg};
  border-color: ${footerTokens.dark.color.btnBorder};
}
.wfp-theme--dark .wfp-footer-btn:hover {
  background: ${footerTokens.dark.color.btnBgHover};
  border-color: ${footerTokens.dark.color.btnBorderHover};
}
.wfp-theme--dark .wfp-footer-btn:active {
  background: ${footerTokens.dark.color.btnBgActive};
  border-color: ${footerTokens.dark.color.btnBorderHover};
  box-shadow: inset 0 1px 2px rgba(0, 0, 0, 0.4);
}
.wfp-theme--dark .wfp-footer-btn:focus-visible {
  outline-color: ${footerTokens.dark.color.btnFocus};
}
.wfp-footer-btn--primary {
  color: #ffffff;
  background: ${PRIMARY_COLOR};
  border-color: ${PRIMARY_COLOR};
}
.wfp-footer-btn--primary:hover {
  background: ${PRIMARY_COLOR_HOVER};
  border-color: ${PRIMARY_COLOR_HOVER};
}
.wfp-footer-btn--primary:active {
  background: ${PRIMARY_COLOR_ACTIVE};
  border-color: ${PRIMARY_COLOR_ACTIVE};
}
.wfp-footer-btn--primary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.wfp-theme--dark .wfp-footer-btn--primary {
  color: #ffffff;
  background: ${PRIMARY_COLOR_LIGHT};
  border-color: ${PRIMARY_COLOR_LIGHT};
}
.wfp-theme--dark .wfp-footer-btn--primary:hover {
  background: ${PRIMARY_COLOR_LIGHT_HOVER};
  border-color: ${PRIMARY_COLOR_LIGHT_HOVER};
}
.wfp-theme--dark .wfp-footer-btn--primary:active {
  background: ${PRIMARY_COLOR};
  border-color: ${PRIMARY_COLOR};
}
.wfp-footer-error {
  font-size: ${footerTokens.fontSize.button};
  color: #d32f2f;
  margin-left: 4px;
}
.wfp-theme--dark .wfp-footer-error {
  color: ${darkTokens.danger};
}
`;

/**
 * 主内容区布局边界常量（layout_resize spec 第 6 章取值）。
 * 用途：两路宽度 state 初值与两条分割线 min/max 的唯一来源（spec 4.4.4 不散落魔法数字）。
 * 边界依据：palette 下限即现值 160px 保证卡片文字单行零回归、上限 320px 为默认 2 倍；
 * configPanel 下限 200px 保证表单控件可用、上限 480px 约默认 1.7 倍。
 */
const LAYOUT_LIMITS = {
  palette: { min: 160, max: 320, default: 160 },
  configPanel: { min: 200, max: 480, default: 280 },
} as const;

/** 应用根组件：管理画布状态并协调各面板交互，可切换至管理后台。 */
export default function App() {
  // 主题状态：应用级唯一持有者（spec 6.1.4）；initialThemeMode 为模块求值期解析值，
  // 模块副作用已同步设置 #root 主题类（首帧即正确主题，spec 5.4.3.2）
  const [themeMode, setThemeMode] = useState<ThemeMode>(initialThemeMode);
  // 单帧完成三件事——状态翻转、根容器主题类同步、持久化写入（spec 4.1.1/5.2.1.3）
  const toggleTheme = useCallback(() => {
    const next: ThemeMode = themeMode === "light" ? "dark" : "light";
    setThemeMode(next);
    syncRootThemeClass(next);
    persistTheme(next);
  }, [themeMode]);
  const isDark = themeMode === "dark";
  const route = useHashRoute();
  const [canvasState, setCanvasState] = useState<WorkflowCanvasState>(() => {
    try {
      const stored = localStorage.getItem("op_canvas_state");
      if (stored) {
        const parsed = JSON.parse(stored) as WorkflowCanvasState;
        if (parsed && Array.isArray(parsed.nodes) && Array.isArray(parsed.edges)) {
          return migrateCanvasState(parsed);
        }
      }
    } catch {
      // localStorage 不可用或数据损坏，回落空画布
    }
    return { nodes: [], edges: [] };
  });
  // 画布持久化防抖器（B2-C3/DC-B2-03）：300ms trailing 去抖，latest-wins；
  // 白名单载荷 {nodes, edges}——WorkflowCanvasState 类型即完整形状（选中项/
  // 面板宽度/视口等 UI 临时态不入盘；视口键独立见 canvasViewport.ts，不并入）；
  // 旧全量键读取兼容：读路径 :439 经 migrateCanvasState 归一，载荷形状不变。
  const canvasSaverRef = useRef<DebouncedSaver<WorkflowCanvasState> | null>(null);
  if (canvasSaverRef.current === null) {
    canvasSaverRef.current = createDebouncedSaver<WorkflowCanvasState>((state) => {
      try {
        localStorage.setItem("op_canvas_state", JSON.stringify(state));
      } catch {
        // 写入失败（隐私模式/沙箱/容量超限），静默处理
      }
    });
  }
  const canvasSaver = canvasSaverRef.current;
  useEffect(() => {
    canvasSaver.schedule(canvasState);
  }, [canvasState, canvasSaver]);
  useEffect(() => {
    const flushWhenHidden = (): void => {
      if (document.visibilityState === "hidden") {
        canvasSaver.flush();
      }
    };
    const flushBeforeUnload = (): void => {
      canvasSaver.flush();
    };
    document.addEventListener("visibilitychange", flushWhenHidden);
    window.addEventListener("beforeunload", flushBeforeUnload);
    return () => {
      document.removeEventListener("visibilitychange", flushWhenHidden);
      window.removeEventListener("beforeunload", flushBeforeUnload);
      // 卸载兜底：flush 落盘 pending。不停用（dispose）——ref 持有的防抖器
      // 跨 StrictMode 双挂载存续，cleanup 内 dispose 会令 dev 模式防抖器在
      // 首次模拟卸载后永久停用，画布编辑不再落盘（e2e B8d 缺陷 D2 修复）。
      canvasSaver.flush();
    };
  }, [canvasSaver]);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  /**
   * 画布内容代际号（CHG-6/DC-10）：仅"整批外部载入"（范例加载 / YAML 导入成功）自增，
   * 用于驱动画布容器 (a) 代际级视口适配、(b) ReactFlow 子树重挂载以清空库内部注册表。
   * 用户增量操作（拖拽/放置/删除/连线）不得自增（保"首节点 onDrop 不重置视口"）。
   */
  const [canvasGeneration, setCanvasGeneration] = useState<number>(0);
  // 撤销/重做快照栈（CHG-E10/DC-04）：自建 useCanvasHistory（零外部撤销库，NFR-018/INV-D02）；
  // past/future 双 useRef 栈（NFR-004），容量 HISTORY_LIMIT=50 溢出丢最旧（OP-007）；
  // push/undo/redo 纯栈操作、外置于 setCanvasState updater（INV-D09，StrictMode 幂等）
  const history = useCanvasHistory<WorkflowCanvasState>(HISTORY_LIMIT);
  const [activeInstanceId, setActiveInstanceId] = useState<string | null>(null);
  const [isRunning, setIsRunning] = useState<boolean>(false);
  const [runError, setRunError] = useState<string | null>(null);
  // 导出失败提示（DC-05/FR-2.6 失败不静默）：成功路径恒 null → 提示 span 不渲染
  const [exportError, setExportError] = useState<string | null>(null);
  // 两路面板宽度 state：宽度唯一持有者为 App.tsx（spec 5.1.1.8a）；
  // 随组件卸载（切换管理后台）自动复位，会话内保持、不持久化、往返/刷新复位（spec 5.2.1.8a/9a）；
  // 首次渲染宽度即现值，无"先宽后窄"闪变（spec 5.3.3.3a）
  const [paletteWidth, setPaletteWidth] = useState<number>(
    LAYOUT_LIMITS.palette.default,
  );
  const [configPanelWidth, setConfigPanelWidth] = useState<number>(
    LAYOUT_LIMITS.configPanel.default,
  );

  // —— 所有 useCallback 必须在 early return 之前声明，确保 hooks 调用顺序在所有视图路径中一致 ——
  const handleExportYaml = useCallback(() => {
    // 修复根因：原 handler 仅返回 YAML 文本、无下载副作用（返回值被 onClick 丢弃）；
    // 此处补齐下载六步，与 AgentPerformancePanel.downloadFile 语义一致（DC-01~DC-04）；
    // try/catch 兜底失败不静默（DC-05/FR-2.6）。
    try {
      const yamlText = canvasToYaml(canvasState);
      const blob = new Blob([yamlText], { type: "text/yaml;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "workflow.yaml";
      anchor.click();
      URL.revokeObjectURL(url);
      setExportError(null);
    } catch {
      setExportError("导出失败，请重试");
    }
  }, [canvasState]);

  const handleImportYaml = useCallback((yamlText: string) => {
    const result = yamlToCanvas(yamlText);
    if (result.ok) {
      // CHG-3/DC-5：统一为函数式原子替换（结果确定，语义等价于传值）
      setCanvasState(() => result.canvasState);
      // CHG-6/DC-10：整批外部载入成功 → 代际自增（触发重挂载 + 代际级视口适配）
      setCanvasGeneration((g) => g + 1);
    }
    return result;
  }, []);

  const handleRunWorkflow = useCallback(async () => {
    setIsRunning(true);
    setRunError(null);
    try {
      if (canvasState.nodes.length === 0) {
        setRunError("画布为空，请先添加节点");
        return;
      }
      const yamlText = canvasToYaml(canvasState);
      const definitionId = `canvas_${Date.now()}`;
      const response = await orchestrationApiClient.startInstance({
        definition_id: definitionId,
        inputs: {},
        yaml_text: yamlText,
      });
      setActiveInstanceId(response.instance_id);
    } catch (err) {
      if (err instanceof ApiError) {
        setRunError(`启动失败：${err.message}`);
      } else if (err instanceof Error) {
        setRunError(`启动失败：${err.message}`);
      } else {
        setRunError("启动失败：网络错误，请检查后端服务是否可用");
      }
    } finally {
      setIsRunning(false);
    }
  }, [canvasState, isRunning, activeInstanceId]);

  /**
   * 删除节点编排：按钮与键盘两入口的共同终点。
   * 流程：removeNode 预判（含存在性/最小节点守卫）→ 失败提示 → 二次确认 → 函数式原子回写 → 选中态清理。
   * CHG-4/DC-5：副作用（alert/confirm）外置，updater 内以 prev 重算保证 StrictMode 幂等。
   */
  const handleDeleteNode = useCallback(
    (nodeId: string) => {
      // 副作用外置预判（DC-2）：基于当前快照即时给出提示 / 二次确认，StrictMode 安全
      const preflight = removeNode(canvasState, nodeId);
      if (!preflight.ok) {
        // 目标不存在：静默返回（幂等安全）；将删至零：提示并拒绝
        if (preflight.reason === "last_node") {
          alert("至少保留一个节点，无法删除");
        }
        return;
      }
      // 二次确认：删除不可撤销，须显式告知
      if (!window.confirm("确定删除该节点吗？删除后不可恢复，且会同时移除其关联连线。")) {
        return;
      }
      // 函数式原子提交（CHG-4/DC-5）：updater 内以 prev 重算，竞态命中失败时无损返回 prev
      setCanvasState((prev) => {
        const outcome = removeNode(prev, nodeId);
        return outcome.ok ? outcome.nextState : prev;
      });
      // 删除的恰为当前选中节点：清理悬空选中态
      if (nodeId === selectedNodeId) {
        setSelectedNodeId(null);
      }
    },
    [canvasState, selectedNodeId],
  );

  /**
   * 删除连线编排（CHG-E11/DC-05/DC-08）：键盘与右键菜单两入口的共同终点。
   * 流程：存在性预判 → 快照入栈（B3 接入）→ 一次原子回写（仅改 edges、nodes 引用透传）；
   * 不弹 confirm（边删除可撤销，DC-08 与节点删除唯一差异）；updater 纯函数（StrictMode 幂等）。
   */
  const handleDeleteEdge = useCallback(
    (edgeId: string) => {
      // 存在性预判（AC-023，幂等安全）：目标不存在静默返回、不入栈
      if (!canvasState.edges.some((edge) => edge.id === edgeId)) {
        return;
      }
      // 变更前快照入栈（CHG-E11/DC-04）：外置于 updater（INV-D09）；新变更清空 redo 分支（AC-019）
      history.push(canvasState);
      // 一次原子回写（DC-05/INV-D01）：nodes 引用透传，仅过滤目标边
      setCanvasState((prev) => ({
        nodes: prev.nodes,
        edges: prev.edges.filter((edge) => edge.id !== edgeId),
      }));
    },
    [canvasState, history],
  );

  /**
   * 画布恢复回调（CHG-8/DC-9）：供 CanvasErrorBoundary 的"恢复画布"按钮显式触发。
   * 清空画布状态（空画布为合法初始态，与 useState 初值同语义）+ 清空选中态；
   * 恢复顺序为先清空再退出兜底（避免异常数据保留导致崩溃死循环）；
   * 恢复为低频显式动作，不经过画布变更高频路径。
   */
  /**
   * 撤销/重做编排（CHG-E11/DC-04）：「先取快照（同步段）→ 再 setCanvasState(快照)」两段式，
   * history 调用外置于 setCanvasState updater（INV-D09）；空栈 undo/redo 返回 undefined →
   * 安全返回不动状态（AC-017/异常场景 6）；快照逐字段恢复（INV-D08，撤销后 == 变更前）。
   */
  const handleUndo = useCallback(() => {
    const snapshot = history.undo(canvasState);
    if (snapshot !== undefined) {
      setCanvasState(snapshot);
    }
  }, [history, canvasState]);

  const handleRedo = useCallback(() => {
    const snapshot = history.redo(canvasState);
    if (snapshot !== undefined) {
      setCanvasState(snapshot);
    }
  }, [history, canvasState]);

  // 全局撤销/重做快捷键（CHG-E12/DC-04 决策 3）：Ctrl/Cmd+Z → 撤销；Ctrl+Y 或 Ctrl/Cmd+Shift+Z → 重做。
  // 输入控件聚焦守卫首行前置（INV-D07/risk R2）：焦点在 INPUT/TEXTAREA/SELECT/可编辑区时直接交还
  // 浏览器/控件原生行为，不 preventDefault、不触发画布撤销（AC-020 精神一致，G-06 同源）。
  useEffect(() => {
    const handleGlobalKeyDown = (event: KeyboardEvent) => {
      const target = event.target;
      if (
        target instanceof HTMLElement &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.tagName === "SELECT" ||
          target.isContentEditable)
      ) {
        return;
      }
      if (!event.ctrlKey && !event.metaKey) {
        return;
      }
      const key = event.key.toLowerCase();
      if (key === "z" && !event.shiftKey) {
        event.preventDefault();
        handleUndo();
        return;
      }
      if (key === "y" || (key === "z" && event.shiftKey)) {
        event.preventDefault();
        handleRedo();
      }
    };
    document.addEventListener("keydown", handleGlobalKeyDown);
    return () => {
      document.removeEventListener("keydown", handleGlobalKeyDown);
    };
  }, [handleUndo, handleRedo]);

  const handleResetCanvas = useCallback(() => {
    setCanvasState({ nodes: [], edges: [] });
    setSelectedNodeId(null);
    // CHG-6/DC-10：显式恢复画布 → 代际自增（重置适配消费态、清空尺寸缓存；
    // 清空态节点数为 0 不 arm 适配，随后拖入首节点仍走"永久禁止自动适配"分支，保 FR-4.2）
    setCanvasGeneration((g) => g + 1);
  }, []);

  /**
   * 应用 Agent 模板到画布（agent_tool_marketplace DC-06/T15.3）：
   * 纯函数不可变更新 —— setCanvasState(prev => applyAgentTemplateToCanvas(prev, template, targetNodeId).canvasState)。
   * 仅追加回调：canvasState / canvasGeneration / localStorage 持久化逻辑零改动（Z-16/INV-D13，风险 R-6）。
   */
  const handleApplyAgentTemplate = useCallback(
    (template: AgentTemplate, targetNodeId?: string | null) => {
      setCanvasState((prev) =>
        applyAgentTemplateToCanvas(prev, template, targetNodeId).canvasState,
      );
    },
    [],
  );

  if (route.view === "admin") {
    return (
      <ThemeContext.Provider value={themeMode}>
        <AdminConsole
          activePanel={route.panelId}
          onPanelChange={(panelId) => navigate({ view: "admin", panelId })}
          onBackToCanvas={() => navigate({ view: "canvas" })}
          onApplyTemplate={handleApplyAgentTemplate}
        />
      </ThemeContext.Provider>
    );
  }

  if (route.view === "not-found") {
    return (
      <ThemeContext.Provider value={themeMode}>
        <AdminConsole
          notFoundSegment={route.segment}
          onBackToCanvas={() => navigate({ view: "canvas" })}
          onApplyTemplate={handleApplyAgentTemplate}
        />
      </ThemeContext.Provider>
    );
  }

  const selectedNode: CanvasNodeData | null =
    canvasState.nodes.find((n) => n.id === selectedNodeId)?.data ?? null;

  return (
    <ThemeContext.Provider value={themeMode}>
      <div
        className={isDark ? THEME_ROOT_CLASS : undefined}
        style={{
          display: "flex",
          flexDirection: "column",
          height: "100vh",
          backgroundColor: isDark ? darkTokens.surface.bg : undefined,
          color: isDark ? darkTokens.text.primary : undefined,
        }}
      >
        <header
        style={{
          backgroundColor: isDark
            ? topbarTokens.dark.color.surface
            : topbarTokens.color.surface, // 显式白底（spec 5.1.1.2a）
          padding: `${topbarTokens.spacing.paddingY} ${topbarTokens.spacing.paddingX}`,
          borderBottom: `1px solid ${
            isDark ? topbarTokens.dark.color.border : topbarTokens.color.border
          }`, // 与页脚分隔线同款
          display: "flex",
          alignItems: "center",
          flexWrap: "wrap", // 窄窗口允许换行，按钮不被挤出可视区（spec 5.1.3.1b）
          gap: topbarTokens.spacing.gap,
        }}
      >
        {/* 样式注入块随 header 同生命周期：挂载即注入、卸载即复位 */}
        <style>{topbarCss}</style>
        <h1
          className="wfp-topbar-title"
          style={{
            fontSize: topbarTokens.fontSize.title, // 16px，与现状一致不增大
            fontWeight: 600, // 与正文 400 拉开层级，确立视觉锚点
            color: isDark
              ? topbarTokens.dark.color.textPrimary
              : topbarTokens.color.textPrimary, // 近黑锚点色
            letterSpacing: "0.2px", // 精炼工具风的微字距
            margin: 0,
            whiteSpace: "nowrap", // 窄窗不换行
            flexShrink: 0, // 标题不受挤压
            display: "inline-flex",
            alignItems: "center",
            gap: topbarTokens.spacing.gapInner, // 图标与文字间距复用内间距令牌
          }}
        >
          <svg
            className="wfp-topbar-title__mark"
            viewBox="0 0 18 18"
            width="18"
            height="18"
            aria-hidden="true"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            {/* 工作流图：三角拓扑——输入节点、处理节点、输出节点，三边全连通 */}
            <circle cx="4" cy="4" r="2" />
            <circle cx="14" cy="4" r="2" />
            <circle cx="9" cy="14" r="2" />
            <path d="M6 4h6" />
            <path d="M5.2 5.2L7.8 12" />
            <path d="M12.8 5.2L10.2 12" />
            {/* 输出节点强调：低透明度填充标记焦点 */}
            <circle cx="9" cy="14" r="1.2" className="wfp-topbar-title__accent" />
          </svg>
          <span>{PRODUCT_NAME}</span>
        </h1>
        <ExampleLibraryPanel onLoadExample={handleImportYaml} />
        <button
          className="wfp-topbar-btn"
          onClick={() => navigate({ view: "admin", panelId: DEFAULT_ADMIN_PANEL })}
          style={{ marginLeft: "auto", flexShrink: 0 }} // 右对齐保持；窄窗下按钮不收缩
        >
          管理后台 →
        </button>
        {/* 主题切换控件：位于"管理后台 →"之后，复用 wfp-topbar-btn 皮肤（spec 5.1.1.2a） */}
        <button
          className="wfp-topbar-btn"
          onClick={toggleTheme}
          style={{
            flexShrink: 0,
            display: "inline-flex",
            alignItems: "center",
            gap: topbarTokens.themeIcon.gap,
            whiteSpace: "nowrap",
          }}
          aria-pressed={themeMode === "dark"}
          aria-label={
            themeMode === "dark"
              ? "当前为深色模式，点击切换至浅色主题"
              : "当前为浅色模式，点击切换至深色主题"
          }
        >
          {themeIndicator}
          {themeMode === "dark" ? "浅色" : "深色"}
        </button>
      </header>
      {/* 主内容区三列容器（ncp_independent_scroll CHG-05）：minHeight:0 解除 column 方向
          min-height:auto 的不可收缩，overflow:hidden 兜底对齐 AdminConsole 既有范式——
          二者共同钉住高度基准，使右侧配置面板内容溢出时被约束在面板内独立滚动，
          不再撑高本区导致 body 整体纵向滚动（FR-3.1/3.2/3.3） */}
      <div style={{ display: "flex", flex: 1, minHeight: 0, overflow: "hidden" }}>
        <NodePalette width={paletteWidth} />
        <ResizableSplitter
          side="left"
          width={paletteWidth}
          min={LAYOUT_LIMITS.palette.min}
          max={LAYOUT_LIMITS.palette.max}
          onResize={setPaletteWidth}
        />
        {/* fix_agent_null_length R-1（React Flow #004 防护，PZ-05=280/PZ-06=240 已拍板）：
            minWidth/minHeight 是压缩下限保护——防窄视口/双面板拖满（或 header 换行压高）时
            画布容器被压至 0 尺寸触发 React Flow #004 拒绝渲染；与 ncp_independent_scroll
            的 minWidth:0「防面板内容撑宽」语义正交（仍可在下限内自由收缩） */}
        <div style={{ flex: 1, position: "relative", minWidth: 280, minHeight: 240 }}>
          {/* 局部渲染兜底（INV-12）：仅包裹画布，崩溃时 header/范例库/配置面板仍可用，
              NodeExecutionStatusOverlay 不在缺陷路径上不包裹（最小侵入不扩权） */}
          <CanvasErrorBoundary onResetCanvas={handleResetCanvas}>
            <WorkflowCanvas
              canvasState={canvasState}
              onCanvasStateChange={setCanvasState}
              selectedNodeId={selectedNodeId}
              onSelectNode={setSelectedNodeId}
              onDeleteNode={handleDeleteNode}
              onDeleteEdge={handleDeleteEdge}
              canvasGeneration={canvasGeneration}
            />
          </CanvasErrorBoundary>
          {activeInstanceId && (
            <NodeExecutionStatusOverlay
              instanceId={activeInstanceId}
              canvasState={canvasState}
            />
          )}
        </div>
        <ResizableSplitter
          side="right"
          width={configPanelWidth}
          min={LAYOUT_LIMITS.configPanel.min}
          max={LAYOUT_LIMITS.configPanel.max}
          onResize={setConfigPanelWidth}
        />
        <NodeConfigurationPanel
          selectedNode={selectedNode}
          canvasState={canvasState}
          onCanvasStateChange={setCanvasState}
          width={configPanelWidth}
          onDeleteNode={handleDeleteNode}
        />
      </div>
      <footer
        style={{
          padding: "4px 16px",
          borderTop: `1px solid ${isDark ? darkTokens.surface.divider : "#e0e0e0"}`,
          backgroundColor: isDark
            ? topbarTokens.dark.color.surface
            : topbarTokens.color.surface,
          display: "flex",
          alignItems: "center",
          gap: "8px",
        }}
      >
        {/* 样式注入块随 footer 同生命周期：挂载即注入、卸载即复位 */}
        <style>{footerCss}</style>
        <YamlFileImportButton onLoadExample={handleImportYaml} />
        <button
          className="wfp-footer-btn wfp-footer-btn--primary"
          onClick={handleRunWorkflow}
          disabled={isRunning}
          style={{ display: "inline-flex", alignItems: "center", gap: "5px" }}
        >
          {runIcon}
          {isRunning ? "运行中…" : "运行"}
        </button>
        <button
          className="wfp-footer-btn"
          onClick={handleExportYaml}
          style={{ display: "inline-flex", alignItems: "center", gap: "5px" }}
        >
          {exportIcon}
          导出 YAML
        </button>
        {runError && <span className="wfp-footer-error">{runError}</span>}
        {exportError && <span className="wfp-footer-error">{exportError}</span>}
      </footer>
      </div>
    </ThemeContext.Provider>
  );
}
