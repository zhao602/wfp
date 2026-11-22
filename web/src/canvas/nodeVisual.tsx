// =============================================================================
// 画布节点统一视觉体系（src/canvas/nodeVisual.tsx）
//
// 职责：为 12 类画布节点提供单一来源的视觉词汇——类型标签、语义图标、
//       独立剪影（形状语言）与统一外壳 NodeShell。设计意图：
//   1. 每类节点拥有独立轮廓（胶囊/半胶囊/芯片八角/六边形/圆柱/文档折角/
//      斜带/标签/堆叠面板/终端窗口/虚线矩形），形状本身即识别维度，
//      不再依赖颜色或圆角微调；
//   2. 图标升级为实色强调块（30×30 类型色底 + 18×18 高对比图标），
//      远看即辨类型，与节点浅色填充形成强对比；
//   3. 结构为三层：外层 wrapper（承载 Handle，不裁剪）→ 剪影填充层
//      （inset 2px，形成等宽描边）→ 内容层（图标块 + 名称 + 类型标签）；
//      选中态为同剪影强调色光环（scale 1.04），悬停态为剪影投影，
//      状态反馈跟随形状而非矩形外框；
//   4. 新增节点类型只需在 NODE_SHAPES / NODE_ICON_PATHS / NODE_TYPE_LABELS
//      各加一行，形状与图标自动生效；
//   5. 数据结构与交互零改动：外壳仅消费 data.name 与 selected，不改
//      canvasState 字段、Handle 位置与连线逻辑。
// =============================================================================

import type { CSSProperties, ReactNode } from "react";
import { Handle, Position } from "reactflow";
import type { NodeType } from "../workflow-definition/definitionTypes";
import { darkTokens } from "../theme/themeMode";

/** 各类型节点显示文案（测试与面板共用，逐字稳定） */
export const NODE_TYPE_LABELS: Record<NodeType, string> = {
  trigger: "触发器",
  agent: "Agent",
  tool: "Tool",
  data_source: "数据源",
  control_flow: "控制流",
  subworkflow: "子工作流",
  dynamic_injector: "动态注入",
  data_transform: "数据转换",
  output_display: "输出展示",
  vector_knowledge_base: "向量知识库",
  llm: "大模型",
  code: "代码",
} as const;

/**
 * 各类型节点语义图标路径（16×16 viewBox，stroke 风格）。
 * 与 NodePalette 共用同一出处（面板 ↔ 画布图标同源，杜绝语义漂移）。
 */
export const NODE_ICON_PATHS: Record<NodeType, readonly string[]> = {
  trigger: ["M13 2L4 9h5l-1 5 9-7h-5l1-5z"],
  agent: ["M8 4a4 4 0 1 0 0 8 4 4 0 1 0 0-8z", "M8 1v3", "M6 7h.01", "M10 7h.01"],
  tool: ["M8 4a4 4 0 1 0 0 8 4 4 0 1 0 0-8z", "M8 1v2", "M8 13v2", "M1 8h2", "M13 8h2"],
  data_source: ["M3 4c0-1.1 2.2-2 5-2s5 .9 5 2v8c0 1.1-2.2 2-5 2s-5-.9-5-2V4z", "M3 8c0 1.1 2.2 2 5 2s5-.9 5-2"],
  control_flow: ["M8 2L14 8l-6 6L2 8l6-6z"],
  subworkflow: ["M2 7l6-3 6 3-6 3-6-3z", "M4 11l4 2 4-2"],
  dynamic_injector: ["M3 8h7", "M8 5l3 3-3 3"],
  data_transform: ["M4 6l-2 2 2 2", "M2 8h6", "M12 10l2-2-2-2", "M14 8H8"],
  output_display: ["M2 3h12v8H2z", "M6 13h4"],
  vector_knowledge_base: [
    "M3 4.5c0-1.1 2.2-2 5-2s5 .9 5 2",
    "M3 4.5v3c0 1.1 2.2 2 5 2s5-.9 5-2v-3",
    "M11 8.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 1 0 0-5z",
    "M13 13l2 2",
  ],
  llm: ["M8 2l1.5 4.5L14 8l-4.5 1.5L8 14l-1.5-4.5L2 8l4.5-1.5L8 2z"],
  code: ["M6 4l-4 4 4 4", "M10 4l4 4-4 4", "M9 3l-2 10"],
} as const;

/**
 * 形状语言：每类节点独立剪影（颜色之外的强识别维度）。
 *   - 触发器：胶囊（流程起点）；
 *   - 输出展示：左直右圆半胶囊（流程终点）；
 *   - Agent：标准圆角矩形（流程主体）；
 *   - 大模型：芯片八角切角（模型/算力）；
 *   - 控制流：六边形左右尖角（判断/分支）；
 *   - 代码：近直角 + 顶部终端条（脚本窗口）；
 *   - 数据源：上下椭圆边的圆柱（数据库）；
 *   - 向量知识库：右上折角的文档形（知识文档）；
 *   - 数据转换：上下斜切的传送带形（数据流动）；
 *   - 动态注入：右侧尖角的标签形（注入流向）；
 *   - 子工作流：双层面板（嵌套容器）；
 *   - 存量 Tool：虚线矩形（已并入 Agent 的迁移标识）。
 * clipPath 优先级高于 radius（二选一）；contentPadding 保证文字避开斜边。
 */
interface NodeShapeSpec {
  /** 圆角剪影（与 clipPath 二选一） */
  radius?: string;
  /** 多边形剪影（斜边/切角/尖角） */
  clipPath?: string;
  /** 内容内边距（斜边形状留出更多横向空间） */
  contentPadding: string;
  /** 图标块圆角（默认 9px） */
  iconTileRadius?: string;
  /** 虚线描边（存量 tool） */
  dashed?: boolean;
  /** 顶部终端条（code） */
  terminalBar?: boolean;
  /** 背面堆叠页（subworkflow） */
  stacked?: boolean;
}

const NODE_SHAPES: Record<NodeType, NodeShapeSpec> = {
  trigger: {
    radius: "999px",
    contentPadding: "9px 22px 9px 12px",
    iconTileRadius: "50%",
  },
  output_display: {
    radius: "0 999px 999px 0",
    contentPadding: "9px 22px 9px 14px",
    iconTileRadius: "50%",
  },
  agent: {
    radius: "14px",
    contentPadding: "9px 16px 9px 12px",
    iconTileRadius: "10px",
  },
  llm: {
    clipPath:
      "polygon(16px 0, calc(100% - 16px) 0, 100% 16px, 100% calc(100% - 16px), calc(100% - 16px) 100%, 16px 100%, 0 calc(100% - 16px), 0 16px)",
    contentPadding: "9px 20px",
    iconTileRadius: "8px",
  },
  control_flow: {
    clipPath:
      "polygon(16px 0, calc(100% - 16px) 0, 100% 50%, calc(100% - 16px) 100%, 16px 100%, 0 50%)",
    contentPadding: "9px 22px 9px 20px",
    iconTileRadius: "8px",
  },
  code: {
    radius: "4px",
    contentPadding: "12px 16px 9px 12px",
    iconTileRadius: "6px",
    terminalBar: true,
  },
  data_source: {
    radius: "50% / 18%",
    contentPadding: "10px 20px",
    iconTileRadius: "50%",
  },
  vector_knowledge_base: {
    clipPath:
      "polygon(0 0, calc(100% - 20px) 0, 100% 20px, 100% 100%, 0 100%)",
    contentPadding: "9px 22px 9px 12px",
    iconTileRadius: "8px",
  },
  data_transform: {
    clipPath: "polygon(0 12px, 100% 0, 100% calc(100% - 12px), 0 100%)",
    contentPadding: "13px 18px",
    iconTileRadius: "8px",
  },
  dynamic_injector: {
    clipPath:
      "polygon(0 0, calc(100% - 18px) 0, 100% 50%, calc(100% - 18px) 100%, 0 100%)",
    contentPadding: "9px 24px 9px 12px",
    iconTileRadius: "8px",
  },
  subworkflow: {
    radius: "12px",
    contentPadding: "9px 16px 9px 12px",
    iconTileRadius: "10px",
    stacked: true,
  },
  tool: {
    radius: "8px",
    contentPadding: "9px 16px 9px 12px",
    iconTileRadius: "9px",
    dashed: true,
  },
} as const;

/** hex → rgba（选中光环/虚线描边取色；仅运行时颜色计算，不涉 CSS 变量） */
function hexToRgba(hex: string, alpha: number): string {
  const value = hex.replace("#", "");
  const r = parseInt(value.slice(0, 2), 16);
  const g = parseInt(value.slice(2, 4), 16);
  const b = parseInt(value.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

/** 剪影样式（填充层/描边层/光环共用，保证轮廓完全一致） */
function silhouette(shape: NodeShapeSpec): CSSProperties {
  return shape.clipPath
    ? { clipPath: shape.clipPath }
    : { borderRadius: shape.radius };
}

/** NodeShell 属性：调用方只传类型、名称与双主题取值，外壳统一排版 */
interface NodeShellProps {
  nodeType: NodeType;
  name: string;
  /** 节点填充色（浅/深主题取值由调用方查表，保持令牌同源） */
  fill: string;
  /** 类型强调色：描边 / 图标块底 / 光环同源（浅/深主题查表值） */
  accent: string;
  isDark: boolean;
  /** React Flow 选中态：同剪影强调色光环 + 轻微放大 */
  selected?: boolean;
  /** 隐藏左侧输入句柄（trigger 入口节点） */
  hideLeftHandle?: boolean;
  /** 类型标签行的附加内容（tool 的「已并入 Agent」徽标） */
  extraTag?: ReactNode;
  /** 节点名等宽字体（code 代码语义） */
  monoName?: boolean;
}

/** 12 类节点统一外壳：独立剪影 + 实色图标块 + 名称 + 类型标签 + 状态反馈 */
export function NodeShell({
  nodeType,
  name,
  fill,
  accent,
  isDark,
  selected = false,
  hideLeftHandle = false,
  extraTag,
  monoName = false,
}: NodeShellProps) {
  const shape = NODE_SHAPES[nodeType];
  const shapeStyle = silhouette(shape);

  /** 描边层：实线 → 类型色实底（由填充层压出 2px 边）；
      虚线 → 透明底 + 2px 虚线描边，压在填充层之上 */
  const outlineStyle: CSSProperties = shape.dashed
    ? {
        ...shapeStyle,
        background: "transparent",
        border: `2px dashed ${accent}`,
      }
    : { ...shapeStyle, background: accent };
  const fillStyle: CSSProperties = {
    ...shapeStyle,
    inset: shape.dashed ? 0 : 2,
    background: fill,
  };

  return (
    <div
      className="wfp-node-shell"
      data-testid="node-shell"
      style={{ position: "relative", minWidth: "160px" }}
    >
      {/* 选中光环：同剪影强调色，轻微放大后从节点轮廓外缘透出 */}
      <div
        aria-hidden="true"
        style={{
          position: "absolute",
          inset: "-2px",
          ...silhouette(shape),
          background: accent,
          opacity: selected ? 0.32 : 0,
          transform: selected ? "scale(1.035)" : "scale(1)",
          transition: "opacity 120ms ease, transform 120ms ease",
          pointerEvents: "none",
        }}
      />

      {/* 子工作流：背面堆叠页，表达「嵌套容器」 */}
      {shape.stacked && (
        <div
          aria-hidden="true"
          style={{
            position: "absolute",
            top: "6px",
            left: "6px",
            right: "-6px",
            bottom: "-6px",
            borderRadius: shape.radius,
            background: isDark ? darkTokens.surface.bgHover : "#ffffff",
            border: `1px solid ${hexToRgba(accent, 0.45)}`,
          }}
        />
      )}

      {/* 剪影层：填充 + 描边（两层同轮廓，形成等宽描边） */}
      <div
        aria-hidden="true"
        style={{ position: "absolute", inset: 0 }}
      >
        {shape.dashed ? (
          <>
            {/* 虚线：填充在下、虚线描边在上，虚线间隙透出填充色 */}
            <div
              data-testid="node-fill"
              style={{ position: "absolute", ...fillStyle }}
            />
            <div
              data-testid="node-outline"
              style={{ position: "absolute", inset: 0, ...outlineStyle }}
            />
          </>
        ) : (
          <>
            {/* 实线：类型色描边在下、填充层上移 2px 压出等宽边 */}
            <div
              data-testid="node-outline"
              style={{ position: "absolute", inset: 0, ...outlineStyle }}
            />
            <div
              data-testid="node-fill"
              style={{ position: "absolute", ...fillStyle }}
            />
          </>
        )}
        {shape.terminalBar && (
          <div
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              right: 0,
              height: "5px",
              background: accent,
              borderRadius: "4px 4px 0 0",
            }}
          />
        )}
      </div>

      {/* 内容层：实色图标块 + 名称 + 类型标签 */}
      <div
        style={{
          position: "relative",
          display: "flex",
          alignItems: "center",
          gap: "10px",
          padding: shape.contentPadding,
        }}
      >
        <span
          style={{
            width: "30px",
            height: "30px",
            flexShrink: 0,
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            borderRadius: shape.iconTileRadius ?? "9px",
            background: accent,
          }}
        >
          <svg
            width="18"
            height="18"
            viewBox="0 0 16 16"
            fill="none"
            stroke={isDark ? "#141414" : "#ffffff"}
            strokeWidth={2}
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            {NODE_ICON_PATHS[nodeType].map((d, i) => (
              <path key={i} d={d} />
            ))}
          </svg>
        </span>
        <span
          style={{
            minWidth: 0,
            display: "inline-flex",
            flexDirection: "column",
            alignItems: "flex-start",
          }}
        >
          <span
            style={{
              display: "block",
              fontWeight: 600,
              fontSize: "13px",
              lineHeight: 1.3,
              whiteSpace: "nowrap",
              color: isDark ? darkTokens.text.primary : "#1a1a1a",
              ...(monoName
                ? {
                    fontFamily:
                      '"SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace',
                  }
                : {}),
            }}
          >
            {name}
          </span>
          <span
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "6px",
              marginTop: "3px",
              fontSize: "10px",
              lineHeight: "14px",
              color: isDark ? darkTokens.text.secondary : "#555555",
              background: isDark
                ? "rgba(255, 255, 255, 0.08)"
                : "rgba(0, 0, 0, 0.05)",
              borderRadius: "999px",
              padding: "1px 8px",
            }}
          >
            {NODE_TYPE_LABELS[nodeType]}
          </span>
          {extraTag && <span style={{ marginLeft: "6px" }}>{extraTag}</span>}
        </span>
      </div>

      {hideLeftHandle ? (
        <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
      ) : (
        <Handle type="target" position={Position.Left} />
      )}
      <Handle type="source" position={Position.Right} />
    </div>
  );
}
