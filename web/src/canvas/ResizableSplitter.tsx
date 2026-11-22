// =============================================================================
// 可调宽度分割线组件（src/canvas/ResizableSplitter.tsx）
//
// 职责：提供主内容区相邻两列之间的拖拽调宽能力（layout_resize spec 5.1.1.8）。
// 设计意图（对应 design.md 2.2）：
//   1. Pointer 事件驱动 + 拖拽期全屏透明覆盖层：光标锁定、禁文本选择、画布隔离三合一；
//   2. 组件不持有面板宽度状态（唯一持有者 App.tsx），仅持有拖拽中呈现态；
//   3. 位移经方向因子换算并钳制后逐事件回调，像素级跟手、越界停边界无回弹。
// =============================================================================

import { useEffect, useState } from "react";
import { darkTokens } from "../theme/themeMode";

/**
 * 分割线布局令牌（精炼极简工具风）。
 * 灰阶/动效与 App.tsx topbarTokens 同源同值（indicatorColor ↔ btnBorderHover、
 * duration/easing ↔ motion.*）——topbarTokens 为 App.tsx 模块级私有不导出，
 * 反向引用将与 App.tsx ↔ canvas 组件现有 import 成环，故同值声明并注释溯源。
 */
const splitterTokens = {
  /** 热区宽度：可抓取下限（spec 6.2.1，6px 级） */
  hitZoneWidth: "6px",
  /** 视觉指示条宽度：默认透明、悬停/拖拽中显现（spec 6.2.2，2px 级 ≤4px 约束） */
  indicatorWidth: "2px",
  /** 指示条颜色：中性灰禁彩色（spec 4.5.5，同源 topbarTokens.color.btnBorderHover） */
  indicatorColor: "#b8b8b8",
  /** 抓手标记尺寸：三圆点直径（Linear Aesthetic 精密风） */
  grabberSize: "2px",
  /** 抓手圆点间距：上下偏移量 */
  grabberSpacing: "4px",
  /** 拖拽态发光色：指示条外发光，同色系低透明度 */
  glowColor: "rgba(184, 184, 184, 0.3)",
  /** 三态过渡：仅 opacity（spec 6.2.3；宽度属性禁止过渡）；120ms 级 + ease，与 topbarTokens.motion 同源同值 */
  duration: "120ms",
  easing: "ease",
  /** 深色分节：指示条降为深色提示档 #8a8a8a（spec 6.3） */
  dark: {
    indicatorColor: darkTokens.text.hint,
    glowColor: "rgba(138, 138, 138, 0.3)",
  },
} as const;

/**
 * 分割线 `<style>` 注入块文本：内插 splitterTokens 生成 CSS 类族。
 * 仅使用类选择器（wfp-splitter- 前缀限定作用域，与顶栏 wfp-topbar- / 卡片
 * wfp-palette- 隔离共存），随组件挂载注入、卸载移除；
 * 不存在作用于宽度属性的 transition 声明（spec 5.2.1.5a 强制检查点）。
 */
const splitterCss = `
.wfp-splitter {
  /* 热区：默认完全透明，与既有 1px 分隔边框视觉重合（spec 5.1.1.1a 零侵入） */
  width: ${splitterTokens.hitZoneWidth};
  flex-shrink: 0;
  position: relative;
  cursor: col-resize;
  touch-action: none;
  user-select: none;
}
.wfp-splitter::after {
  /* 指示条：默认透明，悬停/拖拽中淡入（仅 opacity 过渡，无尺寸动效） */
  content: "";
  position: absolute;
  top: 0;
  bottom: 0;
  left: 50%;
  width: ${splitterTokens.indicatorWidth};
  transform: translateX(-50%);
  background: ${splitterTokens.indicatorColor};
  border-radius: 1px;
  opacity: 0;
  transition: opacity ${splitterTokens.duration} ${splitterTokens.easing};
}
.wfp-splitter::before {
  /* 抓手标记：中间三圆点，提示可拖拽；默认透明，悬停半显、拖拽全显 */
  content: "";
  position: absolute;
  top: 50%;
  left: 50%;
  width: ${splitterTokens.grabberSize};
  height: ${splitterTokens.grabberSize};
  border-radius: 50%;
  background: ${splitterTokens.indicatorColor};
  transform: translate(-50%, -50%);
  opacity: 0;
  transition: opacity ${splitterTokens.duration} ${splitterTokens.easing};
  box-shadow: 0 -${splitterTokens.grabberSpacing} 0 ${splitterTokens.indicatorColor}, 0 ${splitterTokens.grabberSpacing} 0 ${splitterTokens.indicatorColor};
}
.wfp-splitter:hover::after,
.wfp-splitter--dragging::after {
  /* 悬停态与拖拽中态：指示条显现（spec 5.1.1.2a / 5.1.1.3a） */
  opacity: 1;
}
.wfp-splitter:hover::before {
  /* 悬停态：抓手半透明显现，暗示可拖拽 */
  opacity: 0.5;
}
.wfp-splitter--dragging::after {
  /* 拖拽态：指示条外发光，增强视觉反馈 */
  box-shadow: 0 0 6px ${splitterTokens.glowColor};
}
.wfp-splitter--dragging::before {
  /* 拖拽态：抓手完全显现 */
  opacity: 1;
}
/* —— 深色作用域规则：指示条 + 抓手色，追加于末尾（spec 5.3.1.5a/5.3.3.3） —— */
.wfp-theme--dark .wfp-splitter::after {
  background: ${splitterTokens.dark.indicatorColor};
}
.wfp-theme--dark .wfp-splitter::before {
  background: ${splitterTokens.dark.indicatorColor};
  box-shadow: 0 -${splitterTokens.grabberSpacing} 0 ${splitterTokens.dark.indicatorColor}, 0 ${splitterTokens.grabberSpacing} 0 ${splitterTokens.dark.indicatorColor};
}
.wfp-theme--dark .wfp-splitter--dragging::after {
  box-shadow: 0 0 6px ${splitterTokens.dark.glowColor};
}
`;

/** 分割线属性：被调面板侧向 + 当前宽度 + 边界 + 宽度回调；组件不持有面板宽度状态 */
interface ResizableSplitterProps {
  /** 被调面板位于分割线的哪一侧："left"=左侧面板（右拖增宽）；"right"=右侧面板（左拖增宽） */
  side: "left" | "right";
  /** 被调面板当前宽度（px），由 App.tsx 注入（宽度状态唯一持有者） */
  width: number;
  /** 允许的最小宽度（px） */
  min: number;
  /** 允许的最大宽度（px） */
  max: number;
  /** 宽度变更回调：携带钳制后的像素级新宽度，逐指针事件触发 */
  onResize: (width: number) => void;
}

/**
 * 可调宽度分割线：默认透明热区，悬停显示指示条，拖拽逐事件回调已钳制的像素级新宽度。
 * 拖拽期间挂载全屏透明覆盖层——光标锁定、禁文本选择、画布隔离三合一（spec 5.2.1.6a）。
 */
export default function ResizableSplitter({
  side,
  width,
  min,
  max,
  onResize,
}: ResizableSplitterProps) {
  /** 拖拽中呈现态（临时交互态，非宽度态；任一结束路径即复位，spec 4.2.1） */
  const [isDragging, setIsDragging] = useState(false);
  /** 拖拽起始指针横坐标 */
  const [startX, setStartX] = useState(0);
  /** 拖拽起始面板宽度 */
  const [startWidth, setStartWidth] = useState(0);

  /** 按下分割线：阻止文本选择启动，记录起始值并进入拖拽中态（覆盖层随之挂载） */
  const handlePointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    setStartX(event.clientX);
    setStartWidth(width);
    setIsDragging(true);
  };

  /** 覆盖层指针移动：位移经方向因子换算并钳制，逐事件回调像素级新宽度（spec 5.2.1.4a） */
  const handlePointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    // 方向因子：左侧面板右拖（Δx>0）增宽 +1；右侧面板左拖（Δx<0）增宽 -1（spec 5.2.1.1a/2a）
    const directionFactor = side === "left" ? 1 : -1;
    const deltaX = (event.clientX - startX) * directionFactor;
    // 越界停边界、无回弹（spec 5.2.1.3a）
    const nextWidth = Math.min(max, Math.max(min, startWidth + deltaX));
    onResize(nextWidth);
  };

  /** 结束拖拽：复位呈现态，覆盖层卸载，宽度保留最后一次合法值（spec 4.2.1） */
  const endDrag = () => {
    setIsDragging(false);
  };

  /** 窗口失焦兜底：指针移出窗口后释放等异常路径（spec 5.2.3.2） */
  useEffect(() => {
    if (!isDragging) return;
    window.addEventListener("blur", endDrag);
    return () => window.removeEventListener("blur", endDrag);
  }, [isDragging]);

  return (
    <>
      {/* 样式注入块随组件同生命周期：挂载即注入、卸载即复位（spec 5.1.3.2） */}
      <style>{splitterCss}</style>
      {/* 热区：默认透明，与既有 1px 分隔边框视觉重合（spec 5.1.1.1a 零侵入） */}
      <div
        className={`wfp-splitter${isDragging ? " wfp-splitter--dragging" : ""}`}
        onPointerDown={handlePointerDown}
      />
      {isDragging && (
        /* 拖拽期全屏透明覆盖层：锁定 col-resize 光标、禁文本选择、
           拦截全部指针事件实现画布隔离与"同刻至多一条拖拽"（spec 5.2.1.6a / 5.1.3.3a）；
           组件内 JSX，结束即卸载，零 document.body 全局样式副作用 */
        <div
          style={{
            position: "fixed",
            top: 0,
            right: 0,
            bottom: 0,
            left: 0,
            cursor: "col-resize",
            userSelect: "none",
            zIndex: 9999,
          }}
          onPointerMove={handlePointerMove}
          onPointerUp={endDrag}
          onPointerCancel={endDrag}
        />
      )}
    </>
  );
}