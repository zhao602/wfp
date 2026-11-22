// =============================================================================
// 主题模式来源模块（src/theme/themeMode.ts）
//
// 职责：深色/浅色双模式的单一事实来源——状态类型、深色令牌总表、初始值解析、
// 持久化、根容器主题标识同步与 Context 传播（spec 5.3.1.7）。
// 设计意图：
//   1. 零依赖叶子模块——仅 import react 基础与 type { NodeType }，被 App/NodePalette/
//      ResizableSplitter/WorkflowCanvas/nodes×8/面板×3/AdminConsole 全部单向 import 而
//      不反向依赖任何 canvas/admin 模块（无成环风险，spec 5.3.1.7a）；
//   2. darkTokens 为全站深色取值唯一出处，各令牌对象 dark 分节与组件内联查表全部
//      引用其键（编译期强绑定，杜绝深色数值漂移，spec 5.3.3.2）；
//   3. 模块副作用在加载时同步设置 #root 主题类，早于 React 首帧 commit，
//      杜绝"先浅后深"闪变（spec 4.1.1 / 5.4.3.2）。
// =============================================================================

import { createContext, useContext } from "react";
import type { NodeType } from "../workflow-definition/definitionTypes";

/** 主题模式：两态强约束，无第三态（spec 6.1.1） */
export type ThemeMode = "light" | "dark";

/** 持久化键名（spec 6.1.3） */
export const THEME_STORAGE_KEY = "wfp-theme-mode";

/** 根容器深色主题标识类：BEM 修饰类风格与 wfp-topbar-btn--sm/--dragging 一致（spec 6.4.1） */
export const THEME_ROOT_CLASS = "wfp-theme--dark";

/**
 * 深色令牌总表：全站深色取值唯一出处（spec 5.3.1.7）。
 * 各令牌对象 dark 分节与组件内联查表全部引用其键（编译期强绑定）。
 */
export const darkTokens = {
  /** 表面灰阶：面<悬停<按压<分隔<描边<深描边次序保持（spec 6.3.1） */
  surface: {
    bg: "#1e1e1e", // 深色面：主背景
    bgHover: "#2a2a2a", // 悬停背景
    bgActive: "#333333", // 按压背景
    divider: "#3d3d3d", // 分隔线
    border: "#4a4a4a", // 描边
    borderStrong: "#666666", // 深描边
  },
  /** 文字色阶：主≥4.5:1 / 次≥4.5:1 / 提示≥3:1 / 焦点浅色系（spec 5.6.1.1 / 6.3.7） */
  text: {
    primary: "#f5f5f5", // 主文字：近白
    secondary: "#b8b8b8", // 次级文字
    hint: "#8a8a8a", // 提示文字
    focus: "#e0e0e0", // 焦点轮廓：浅色系（深色背景上可见，spec 6.3.7）
  },
  /** 危险/错误色：提亮档（spec 6.3.3） */
  danger: "#ff6b6b",
  /** 各类型节点深色填充变体：色相不变、降饱和提明度（spec 6.3.4） */
  typeFill: {
    trigger: "#1a2a3a",
    agent: "#3a1a4c",
    tool: "#1a3a28",
    data_source: "#4a301a",
    control_flow: "#4a1a1a",
    subworkflow: "#1f2a52",
    dynamic_injector: "#3b1f52",
    data_transform: "#4a361a",
    output_display: "#1a3a32",
    vector_knowledge_base: "#1a3a22",
    llm: "#4a1a30",
    code: "#1f2e36",
  } as Record<NodeType, string>,
  /** 各类型节点描边提亮档：色相不变、提亮一档（spec 5.5.1.6 / 6.3.4） */
  typeBorder: {
    trigger: "#42a5f5",
    agent: "#ab47bc",
    tool: "#66bb6a",
    data_source: "#fb8c00",
    control_flow: "#e53935",
    subworkflow: "#5c6bc0",
    dynamic_injector: "#ba68c8",
    data_transform: "#ffa726",
    output_display: "#26a69a",
    vector_knowledge_base: "#4caf50",
    llm: "#ec407a",
    code: "#78909c",
  } as Record<NodeType, string>,
  /** 执行状态色提亮档（spec 5.5.1.8 / 6.3.5） */
  status: {
    pending: "#9e9e9e",
    running: "#4fc3f7",
    succeeded: "#81c784",
    failed: "#e57373",
    skipped: "#8a8a8a",
  },
  /** ReactFlow 画布深色取值（spec 6.3.6） */
  reactflow: {
    viewportBg: "#1e1e1e", // 视口背景
    dotColor: "#666666", // 背景点阵：柔和档，在 #1e1e1e 上对比度 ~3.0:1 装饰可见（spec 6.3.6）
    edgeStroke: "#555555", // 边默认描边
    edgeStrokeSelected: "#e0e0e0", // 选中边描边
    controlsBg: "#2a2a2a", // 控件背景
    controlsBgHover: "#333333", // 控件悬停
    controlsBorder: "#3d3d3d", // 控件描边
    controlsIcon: "#f5f5f5", // 控件图标
    attributionBg: "rgba(30, 30, 30, 0.75)", // 归属标识背景
    attributionLink: "#8a8a8a", // 归属链接
  },
} as const;

/** 读取持久化主题值：无效或异常返回 null（spec 5.2.1.5 / 5.2.3.1/2） */
function readStoredTheme(): ThemeMode | null {
  try {
    const stored = localStorage.getItem(THEME_STORAGE_KEY);
    if (stored === "light" || stored === "dark") return stored;
    return null;
  } catch {
    return null;
  }
}

/**
 * 解析初始主题模式：三级优先级（spec 5.2.1.2）
 * 1. localStorage 有效值 → 2. prefers-color-scheme 能力检测 → 3. 回落 "light"
 */
export function resolveInitialThemeMode(): ThemeMode {
  const stored = readStoredTheme();
  if (stored !== null) return stored;
  try {
    if (typeof window.matchMedia === "function") {
      if (window.matchMedia("(prefers-color-scheme: dark)").matches) {
        return "dark";
      }
    }
  } catch {
    // 能力缺失或异常：回落浅色（spec 5.2.3.3）
  }
  return "light";
}

/** 模块求值期即确定的初始主题（与首帧防闪烁共用，spec 5.4.3.2） */
export const initialThemeMode: ThemeMode = resolveInitialThemeMode();

/** 持久化主题写入：异常静默（spec 5.2.3.1） */
export function persistTheme(mode: ThemeMode): void {
  try {
    localStorage.setItem(THEME_STORAGE_KEY, mode);
  } catch {
    // 隐私模式或配额不足：静默忽略
  }
}

/** 同步根容器主题标识类：幂等 toggle（spec 6.4.1） */
export function syncRootThemeClass(mode: ThemeMode): void {
  try {
    document
      .getElementById("root")
      ?.classList.toggle(THEME_ROOT_CLASS, mode === "dark");
  } catch {
    // DOM 不可用：忽略
  }
}

/**
 * 主题 Context：默认浅色，未提供 Provider 时组件降级浅色（向后兼容独立渲染测试）。
 */
export const ThemeContext = createContext<ThemeMode>("light");

/** 主题模式 hook：memo 组件中 context 变化强制重渲染不受 props 浅比较阻断（spec 4.4.5） */
export function useThemeMode(): ThemeMode {
  return useContext(ThemeContext);
}

// 模块副作用：加载时同步设置 #root 主题类。
// type="module" defer 执行于 DOM 解析后 → #root 已存在，同步设置早于 React 首帧 commit，
// 杜绝"先浅后深"闪变（spec 4.1.1 / 5.4.3.2）。
syncRootThemeClass(initialThemeMode);