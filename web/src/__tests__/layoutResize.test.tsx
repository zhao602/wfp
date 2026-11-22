// =============================================================================
// 主内容区布局可调回归测试（src/__tests__/layoutResize.test.tsx）
//
// 验证方式：V1 vitest 组件测试（jsdom），沙箱环境无法执行（esbuild 子进程被杀），
// 留待非沙箱环境运行（`npx vitest run src/__tests__/layoutResize.test.tsx`）。
//
// 覆盖验收（对应 spec.md 5.1/5.2/5.3，EARS 验收条件中的 jsdom 可断言子集）：
//   1. 默认渲染结构（分割线存在、透明热区、wfp-splitter 类挂载 → 5.3.1.1a/5.1.1.1a）
//   2. 悬停指示条规则（注入块含 :hover::after / opacity:1 / col-resize / 120ms → 5.1.1.2a）
//   3. 拖拽回调与钳制（pointerDown→pointerMove 断言 onResize 钳制值 → 5.2.1.1a/3a）
//   4. 方向语义（side="right" 左拖增宽 → 5.2.1.2a）
//   5. 宽度无过渡（注入块无作用于 width 的 transition → 5.2.1.5a）
//   6. 拖拽结束归位（pointerUp 后 --dragging 类移除、覆盖层卸载 → 5.2.3.2）
//   7. 样式共存隔离（注入块不含 wfp-topbar- / wfp-palette- 类名 → 5.3.3.2）
//   8. 向后兼容（NodePalette 无 props 渲染宽度仍 160px → 5.3.1.6a）
// =============================================================================

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, fireEvent, cleanup } from "@testing-library/react";
import ResizableSplitter from "../canvas/ResizableSplitter";
import NodePalette from "../canvas/NodePalette";

// jsdom 无 PointerEvent 实现：fireEvent.pointer* 回退裸 Event，clientX 坐标丢失（拖拽位移算出 NaN）。
// 以 jsdom MouseEvent 充当 PointerEvent 构造器，坐标 init 生效（仅测试环境补丁）。
vi.stubGlobal("PointerEvent", MouseEvent);

describe("主内容区布局可调", () => {
  afterEach(() => {
    cleanup();
  });

  it("默认渲染结构：分割线存在、透明热区无背景、wfp-splitter 类挂载（5.3.1.1a/5.1.1.1a）", () => {
    const onResize = vi.fn();
    const { container } = render(
      <ResizableSplitter side="left" width={160} min={160} max={320} onResize={onResize} />,
    );

    const splitter = container.querySelector(".wfp-splitter") as HTMLElement;
    expect(splitter).toBeTruthy();
    // 热区默认透明：无背景色（spec 5.1.1.1a 零侵入）
    expect(splitter.style.backgroundColor).toBe("");
    // 光标为 col-resize（spec 5.2.1.6a）
    expect(getComputedStyle(splitter).cursor).toBe("col-resize");
  });

  it("悬停指示条规则：注入块含 :hover::after / opacity:1 / col-resize / 120ms（5.1.1.2a）", () => {
    const onResize = vi.fn();
    const { container } = render(
      <ResizableSplitter side="left" width={160} min={160} max={320} onResize={onResize} />,
    );

    const styleText = container.querySelector("style")?.textContent ?? "";
    expect(styleText).toContain(".wfp-splitter:hover::after");
    expect(styleText).toContain("opacity: 1");
    expect(styleText).toContain("col-resize");
    expect(styleText).toContain("120ms");
  });

  it("拖拽回调与钳制：pointerDown→pointerMove 断言 onResize 钳制值（5.2.1.1a/3a）", () => {
    const onResize = vi.fn();
    const { container } = render(
      <ResizableSplitter side="left" width={160} min={160} max={320} onResize={onResize} />,
    );

    const splitter = container.querySelector(".wfp-splitter") as HTMLElement;
    // 按下分割线（clientX: 100）
    fireEvent.pointerDown(splitter, { clientX: 100, preventDefault: vi.fn() });

    // 拖拽 +50px：左侧面板右拖增宽，期望回调 160+50=210（spec 5.2.1.1a）
    const overlay = container.querySelector('[style*="fixed"]') as HTMLElement;
    fireEvent.pointerMove(overlay, { clientX: 150 });
    expect(onResize).toHaveBeenLastCalledWith(210);

    // 拖拽至远超上限（+500）：钳制停 max=320，无回弹（spec 5.2.1.3a）
    fireEvent.pointerMove(overlay, { clientX: 600 });
    expect(onResize).toHaveBeenLastCalledWith(320);

    // 拖拽至远低于下限（-500）：钳制停 min=160
    fireEvent.pointerMove(overlay, { clientX: -400 });
    expect(onResize).toHaveBeenLastCalledWith(160);
  });

  it("方向语义：side=\"right\" 左拖增宽（5.2.1.2a）", () => {
    const onResize = vi.fn();
    const { container } = render(
      <ResizableSplitter side="right" width={280} min={200} max={480} onResize={onResize} />,
    );

    const splitter = container.querySelector(".wfp-splitter") as HTMLElement;
    fireEvent.pointerDown(splitter, { clientX: 200, preventDefault: vi.fn() });

    // 右侧面板左拖（clientX 减小 50）增宽：方向因子 -1，Δ=(-50)×(-1)=+50，期望 280+50=330
    const overlay = container.querySelector('[style*="fixed"]') as HTMLElement;
    fireEvent.pointerMove(overlay, { clientX: 150 });
    expect(onResize).toHaveBeenLastCalledWith(330);
  });

  it("宽度无过渡：注入块无作用于 width 的 transition 声明（5.2.1.5a）", () => {
    const onResize = vi.fn();
    const { container } = render(
      <ResizableSplitter side="left" width={160} min={160} max={320} onResize={onResize} />,
    );

    const styleText = container.querySelector("style")?.textContent ?? "";
    // 仅 opacity 过渡，不存在 width 过渡（spec 5.2.1.5a 强制检查点）
    expect(styleText).toContain("transition: opacity");
    expect(styleText).not.toMatch(/transition[^;]*width/);
  });

  it("拖拽结束归位：pointerUp 后 --dragging 类移除、覆盖层卸载（5.2.3.2）", () => {
    const onResize = vi.fn();
    const { container } = render(
      <ResizableSplitter side="left" width={160} min={160} max={320} onResize={onResize} />,
    );

    const splitter = container.querySelector(".wfp-splitter") as HTMLElement;
    fireEvent.pointerDown(splitter, { clientX: 100, preventDefault: vi.fn() });

    // 拖拽中：--dragging 类挂载、覆盖层存在
    expect(splitter.className).toContain("wfp-splitter--dragging");
    expect(container.querySelector('[style*="fixed"]')).toBeTruthy();

    // 结束拖拽：类移除、覆盖层卸载（spec 4.2.1/5.2.3.2）
    const overlay = container.querySelector('[style*="fixed"]') as HTMLElement;
    fireEvent.pointerUp(overlay);
    expect(splitter.className).not.toContain("wfp-splitter--dragging");
    expect(container.querySelector('[style*="fixed"]')).toBeNull();
  });

  it("样式共存隔离：注入块不含 wfp-topbar- / wfp-palette- 类名（5.3.3.2）", () => {
    const onResize = vi.fn();
    const { container } = render(
      <ResizableSplitter side="left" width={160} min={160} max={320} onResize={onResize} />,
    );

    const styleText = container.querySelector("style")?.textContent ?? "";
    expect(styleText).not.toContain("wfp-topbar-");
    expect(styleText).not.toContain("wfp-palette-");
  });

  it("向后兼容：NodePalette 无 props 渲染宽度仍 160px（5.3.1.6a）", () => {
    const { container } = render(<NodePalette />);
    const sidebar = container.firstElementChild as HTMLElement;
    expect(sidebar).toHaveStyle({ width: "160px" });
  });
});