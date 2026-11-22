// =============================================================================
// 节点面板组件（src/canvas/NodePalette.tsx）
//
// 职责：提供十一类节点模板，拖入画布时生成唯一 node_id。
// 设计意图（对应需求规格 5.5.1.1、任务 T18.2）：
//   1. 列出九类节点模板供用户拖拽至画布；
//   2. node_id 前端生成（UUID），后端校验双重保证唯一性；
//   3. 拖拽通过 HTML5 drag API 实现，画布侧监听 drop 事件接收。
// 视觉优化（对应需求规格 node_palette_cards/spec.md、design.md 2.1.3）：
//   彩色仅作功能点缀（左侧 4px 类型色条），层次靠灰阶三档，反馈克制（120ms 短过渡）。
// =============================================================================

import { useState } from "react";
import { type NodeType } from "../workflow-definition/definitionTypes";
import { darkTokens, useThemeMode } from "../theme/themeMode";
// 语义图标词汇表唯一出处（面板 ↔ 画布 NodeShell 同源，杜绝两套图标语义漂移）
import { NODE_ICON_PATHS } from "./nodeVisual";

/** 节点模板条目 */
interface NodeTemplate {
  type: NodeType;
  label: string;
  color: string;
}

/** 十一类节点模板（tool 已移除新建入口：工具并入 Agent 选项，存量画布经 nodeTypes 渲染兼容，merge_tool_into_agent_node DC-C；llm/code 新增新建入口，llm_code_nodes_v2 CHG-F05）。导出供测试与画布层做新建入口两键一致性断言（design 附录 A.4 表 B B1） */
export const NODE_TEMPLATES: NodeTemplate[] = [
  { type: "trigger", label: "触发器", color: "#1976d2" },
  { type: "data_source", label: "数据源", color: "#f57c00" },
  { type: "vector_knowledge_base", label: "向量知识库", color: "#2e7d32" },
  { type: "agent", label: "Agent", color: "#7b1fa2" },
  { type: "llm", label: "大模型", color: "#c2185b" },
  { type: "code", label: "代码", color: "#455a64" },
  { type: "data_transform", label: "数据转换", color: "#ff9800" },
  { type: "dynamic_injector", label: "动态注入", color: "#9c27b0" },
  { type: "control_flow", label: "控制流", color: "#c62828" },
  { type: "subworkflow", label: "子工作流", color: "#3f51b5" },
  { type: "output_display", label: "输出展示", color: "#00897b" },
];

/**
 * 节点面板卡片设计令牌（精炼极简工具风）。
 * 用途：paletteCss 注入块与卡片拖拽中态的取值来源；
 * 灰阶与动效取值与 App.tsx 的 topbarTokens 同源同值（cardBgHover ↔ btnBgHover、
 * cardBorder ↔ btnBorder、cardBorderHover ↔ btnBorderHover）——该常量为 App.tsx
 * 模块级私有不导出，反向引用将与 App.tsx ↔ NodePalette 现有 import 成环，
 * 故在本文件内以同值声明并注释溯源；类型色不入令牌（唯一来源为
 * NODE_TEMPLATES 的 color 字段，spec 4.4.4）。
 */
const paletteTokens = {
  /** 色彩：全部锁定中性灰阶（与顶栏 topbarTokens 同源协调） */
  color: {
    cardBg: "#ffffff", // 卡片默认背景（与现状一致）
    cardBgHover: "#f5f5f5", // 悬停背景：极浅灰位移
    cardBorder: "#d0d0d0", // 默认描边（与顶栏按钮描边同级）
    cardBorderHover: "#b8b8b8", // 悬停描边：加深一档强化边界
  },
  /** 字号：与现状一致不增大 */
  fontSize: { card: "13px" },
  /** 间距：与现状一致（8px 12px），色条占位已核算（design.md 2.1.3.2） */
  spacing: { cardPaddingY: "8px", cardPaddingX: "12px" },
  /** 圆角：与顶栏按钮同级（spec 5.1.1.4a） */
  radius: { card: "6px" },
  /** 动效：短时长即时感（同源 topbarTokens.motion） */
  motion: { duration: "120ms", easing: "ease" },
  /** 拖拽中态：透明度（design.md 2.1.3.2 选型方案 A） */
  dragging: { opacity: "0.5" },
  /** 深色分节：逐键引用 darkTokens（编译期强绑定，spec 5.3.1.7a） */
  dark: {
    color: {
      cardBg: darkTokens.surface.bg,
      cardBgHover: darkTokens.surface.bgHover,
      cardBorder: darkTokens.surface.border,
      cardBorderHover: darkTokens.surface.borderStrong,
    },
  },
} as const;

/**
 * 卡片 `<style>` 注入块文本：内插 paletteTokens 生成 CSS 类族。
 * 仅使用类选择器（wfp-palette- 前缀限定作用域，与顶栏 wfp-topbar- 隔离共存），
 * 随 NodePalette 挂载注入、卸载移除，视图往返后天然复位（spec 5.1.3.2）。
 * 悬停态三边逐边显式声明、永不声明 border-color 简写与 border-left-color，
 * 从结构上保证悬停时左侧类型色条恒定不漂灰（spec 5.2.1.5a 强制检查点）。
 */
const paletteCss = `
.wfp-palette-card {
  /* 默认态：flex 布局（图标+标签水平排列）+ 中性灰描边 + 小圆角 + 四属性短过渡 */
  display: flex;
  align-items: center;
  gap: 7px;
  border: 1px solid ${paletteTokens.color.cardBorder};
  border-radius: ${paletteTokens.radius.card};
  transition: background-color ${paletteTokens.motion.duration} ${paletteTokens.motion.easing},
    border-color ${paletteTokens.motion.duration} ${paletteTokens.motion.easing},
    box-shadow ${paletteTokens.motion.duration} ${paletteTokens.motion.easing},
    opacity ${paletteTokens.motion.duration} ${paletteTokens.motion.easing};
}
.wfp-palette-card:hover:not(.wfp-palette-card--dragging) {
  /* 悬停态：极浅灰背景 + 三边描边加深 + 微妙投影深度感；
     :not 排除拖拽中卡片，左侧类型色条不参与悬停变化（spec 5.2.1.5a） */
  background: ${paletteTokens.color.cardBgHover};
  border-top-color: ${paletteTokens.color.cardBorderHover};
  border-right-color: ${paletteTokens.color.cardBorderHover};
  border-bottom-color: ${paletteTokens.color.cardBorderHover};
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.06);
}
.wfp-palette-card--dragging {
  /* 拖拽中态：背景与三边描边锁定回默认值（透明度由内联 opacity 承载） */
  background: ${paletteTokens.color.cardBg};
  border-top-color: ${paletteTokens.color.cardBorder};
  border-right-color: ${paletteTokens.color.cardBorder};
  border-bottom-color: ${paletteTokens.color.cardBorder};
}
.wfp-palette-card__icon {
  /* 语义图标：默认半透明，悬停渐显全不透明——克制反馈不喧宾夺主 */
  flex-shrink: 0;
  opacity: 0.6;
  transition: opacity ${paletteTokens.motion.duration} ${paletteTokens.motion.easing};
}
.wfp-palette-card:hover:not(.wfp-palette-card--dragging) .wfp-palette-card__icon {
  opacity: 1;
}
/* —— 深色作用域规则：追加于末尾，浅色规则文本逐字符不动（spec 5.3.1.5a/5.3.3.3） —— */
.wfp-theme--dark .wfp-palette-card {
  background: ${paletteTokens.dark.color.cardBg};
  border-color: ${paletteTokens.dark.color.cardBorder};
}
.wfp-theme--dark .wfp-palette-card:hover:not(.wfp-palette-card--dragging) {
  background: ${paletteTokens.dark.color.cardBgHover};
  border-top-color: ${paletteTokens.dark.color.cardBorderHover};
  border-right-color: ${paletteTokens.dark.color.cardBorderHover};
  border-bottom-color: ${paletteTokens.dark.color.cardBorderHover};
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.35);
}
.wfp-theme--dark .wfp-palette-card--dragging {
  background: ${paletteTokens.dark.color.cardBg};
  border-top-color: ${paletteTokens.dark.color.cardBorder};
  border-right-color: ${paletteTokens.dark.color.cardBorder};
  border-bottom-color: ${paletteTokens.dark.color.cardBorder};
}
`;

/** 生成唯一节点标识（前端 UUID v4 简化版） */
export function generateNodeId(): string {
  return "node-" + crypto.randomUUID();
}

/** 节点面板默认宽度（px），未注入 width prop 时回落（向后兼容，spec 4.5.2） */
const DEFAULT_PALETTE_WIDTH = 160;

/** 节点面板属性 */
interface NodePaletteProps {
  /** 可选宽度注入（px），未传时回落默认 160px（向后兼容） */
  width?: number;
}

/** 节点面板：展示十一类节点模板，支持拖拽至画布 */
export default function NodePalette({ width = DEFAULT_PALETTE_WIDTH }: NodePaletteProps) {
  const themeMode = useThemeMode();
  const isDark = themeMode === "dark";
  /** 拖拽中类型状态位：仅承载视觉呈现，不入 props、重挂载天然复位（spec 6.3） */
  const [draggingType, setDraggingType] = useState<NodeType | null>(null);

  /** 拖拽开始时设置数据传输，携带节点类型 */
  const handleDragStart = (event: React.DragEvent, type: NodeType) => {
    event.dataTransfer.setData("application/node-type", type);
    event.dataTransfer.effectAllowed = "move";
  };

  return (
    <div
      style={{
        width: `${width}px`,
        borderRight: `1px solid ${isDark ? darkTokens.surface.divider : "#e0e0e0"}`,
        backgroundColor: isDark ? darkTokens.surface.bg : "#ffffff",
        padding: "8px",
        display: "flex",
        flexDirection: "column",
        gap: "8px",
      }}
    >
      {/* 样式注入块随组件同生命周期：挂载即注入、卸载即复位（spec 5.1.3.2） */}
      <style>{paletteCss}</style>
      <div style={{ fontWeight: 600, fontSize: "13px", marginBottom: "4px" }}>
        节点面板
      </div>
      {NODE_TEMPLATES.map((template) => {
        const isDragging = draggingType === template.type;
        return (
          <div
            key={template.type}
            draggable
            onDragStart={(e) => {
              // 先完整执行既有业务数据写入（函数体与调用链零改动），
              // 后追加视觉状态置位——业务与呈现互不影响（spec 5.3.1.2a）
              handleDragStart(e, template.type);
              setDraggingType(template.type);
            }}
            onDragEnd={() => setDraggingType(null)}
            className={`wfp-palette-card${
              isDragging ? " wfp-palette-card--dragging" : ""
            }`}
            style={{
              padding: "8px 12px",
              borderLeft: `4px solid ${
                isDark ? darkTokens.typeBorder[template.type] : template.color
              }`, // 类型色条：深色用提亮档（spec 5.5.1.6）
              background: isDark ? darkTokens.surface.bg : "#fff",
              cursor: "grab",
              fontSize: "13px",
              userSelect: "none",
              opacity: isDragging ? paletteTokens.dragging.opacity : 1,
            }}
          >
            <svg
              className="wfp-palette-card__icon"
              width="14"
              height="14"
              viewBox="0 0 16 16"
              fill="none"
              stroke={isDark ? darkTokens.text.secondary : "#5a5a5a"}
              strokeWidth={1.5}
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              {NODE_ICON_PATHS[template.type].map((d, i) => (
                <path key={i} d={d} />
              ))}
            </svg>
            {template.label}
          </div>
        );
      })}
    </div>
  );
}
