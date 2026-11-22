// =============================================================================
// 画布容器最小尺寸样式契约测试（src/__tests__/App.canvasContainerGuard.test.tsx）
//
// 执行：npx vitest run src/__tests__/App.canvasContainerGuard.test.tsx
//
// 覆盖（fix_agent_null_length tasks v2 §2.5，R-1：React Flow #004 防护）：
//   TC-GUARD-01 画布容器压缩下限在位：minWidth=280px / minHeight=240px（PZ-05/PZ-06 已拍板）
//   TC-GUARD-02 主内容区容器 ncp_independent_scroll 语义载体未改动：
//     minHeight:0 + overflow:hidden（ncp CHG-05 语义，R-1 仅动画布容器 style）
//
// 测试策略：桩 WorkflowCanvas 渲染标记元素（CanvasErrorBoundary 非 error 态直返
// children 无包裹 DOM，标记 parentElement 即画布容器 div），断言样式契约。
//
// 说明：jsdom 无法触发真实 React Flow 布局计算，本文件仅锁定样式契约；
//       #004 的真机渲染验证移交用户目检（M-4 项，tasks §4.4）。
// =============================================================================

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, cleanup, screen } from "@testing-library/react";
import App from "../App";
import { orchestrationApiClient } from "../api/orchestrationApiClient";

// 桩 WorkflowCanvas：渲染标记元素（画布容器定位锚点）
vi.mock("../canvas/WorkflowCanvas", () => ({
  default: () => <div data-testid="wc-stub" />,
}));

// 桩 ExampleLibraryPanel：范例库面板不参与本断言
vi.mock("../example-library/ExampleLibraryPanel", () => ({
  default: () => null,
}));

// jsdom 无 ResizeObserver
vi.stubGlobal(
  "ResizeObserver",
  class {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  },
);

// 后端返回 200 空对象
vi.stubGlobal(
  "fetch",
  vi.fn().mockResolvedValue(new Response(JSON.stringify({}), { status: 200 })),
);

describe("画布容器最小尺寸样式契约（R-1，React Flow #004 防护）", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("TC-GUARD-01 画布容器压缩下限在位：minWidth=280px / minHeight=240px（PZ-05/PZ-06）", () => {
    vi.spyOn(orchestrationApiClient, "listExamples").mockResolvedValue({
      examples: [],
    });

    render(<App />);

    const marker = screen.getByTestId("wc-stub");
    const canvasContainer = marker.parentElement;
    expect(canvasContainer).not.toBeNull();
    // R-1 压缩下限（tasks §2.2/§2.3，PZ-05=280 / PZ-06=240）
    expect(canvasContainer!.style.minWidth).toBe("280px");
    expect(canvasContainer!.style.minHeight).toBe("240px");
    // 既有布局语义保持（flex:1 占位 + relative 承载覆盖层；
    // jsdom CSSOM 将 flex 简写归一为 "1 1 0%"，故查 flexGrow 子属性）
    expect(canvasContainer!.style.flexGrow).toBe("1");
    expect(canvasContainer!.style.position).toBe("relative");
  });

  it("TC-GUARD-02 主内容区容器 ncp_independent_scroll 语义载体未改动：minHeight=0px / overflow=hidden", () => {
    vi.spyOn(orchestrationApiClient, "listExamples").mockResolvedValue({
      examples: [],
    });

    render(<App />);

    const marker = screen.getByTestId("wc-stub");
    const canvasContainer = marker.parentElement!;
    const mainContainer = canvasContainer.parentElement;
    expect(mainContainer).not.toBeNull();
    // ncp_independent_scroll CHG-05 语义载体（App.tsx 主内容区三列容器，R-1 零改动；
    // jsdom CSSOM 对零值 minHeight:0 保持无单位 "0"，与 scroll.test A2/A5 口径一致）
    expect(mainContainer!.style.minHeight).toBe("0");
    expect(mainContainer!.style.overflow).toBe("hidden");
    expect(mainContainer!.style.display).toBe("flex");
  });
});
