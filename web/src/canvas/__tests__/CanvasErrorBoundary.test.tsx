// =============================================================================
// 画布渲染兜底测试（canvas/__tests__/CanvasErrorBoundary.test.tsx）
//
// 留待非沙箱环境执行：cd web && npx vitest run
//
// 覆盖用例（fix-canvas-drag-clear-and-edge-loop-bugs / T13，TC-EB-01/02）：
//   TC-EB-01 子树渲染抛异常 → 兜底 UI 可见、整树不卸载（INV-12）、画布状态不被兜底改写
//   TC-EB-02 点击"恢复画布" → onResetCanvas 被调用、兜底退出、子树重新渲染
//
// 沙箱限制声明：沙箱内 tsc 全量编译超时、vitest 无法运行，本文件作为交付物入库，
// 请在非沙箱环境执行。
// =============================================================================

import { describe, it, expect, vi, afterEach } from "vitest";
import { useState } from "react";
import type { ReactNode } from "react";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import CanvasErrorBoundary from "../CanvasErrorBoundary";

/** 测试专用崩溃子组件：渲染期抛出异常（对齐 ErrorBoundary 能力边界：仅渲染期错误） */
function Bomb(): ReactNode {
  throw new Error("模拟渲染崩溃");
}

/** 兄弟锚点元素文本（INV-12 整树不卸载断言用） */
const SIBLING_TEXT = "顶栏锚点元素";
/** 恢复后正常子组件文本 */
const RECOVERED_TEXT = "画布已恢复内容";

describe("CanvasErrorBoundary 渲染兜底", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  // TC-EB-01（INV-12）：子树渲染抛异常 → 兜底 UI 可见（异常提示 + "恢复画布"按钮），
  // 整树不卸载（兄弟节点仍渲染），兜底不自动改写画布状态（onResetCanvas 零调用）
  it("TC-EB-01 子树渲染抛异常时兜底 UI 可见且整树不卸载（INV-12）", () => {
    // 静默 React 渲染错误与 didCatch 的 console.error 噪音
    vi.spyOn(console, "error").mockImplementation(() => {});

    const onResetCanvas = vi.fn();
    render(
      <div>
        <div>{SIBLING_TEXT}</div>
        <CanvasErrorBoundary onResetCanvas={onResetCanvas}>
          <Bomb />
        </CanvasErrorBoundary>
      </div>,
    );

    // 兜底 UI 可见：异常提示 + 恢复按钮
    expect(screen.getByText("画布渲染异常")).toBeDefined();
    expect(screen.getByText("恢复画布")).toBeDefined();
    // 整树不卸载：兄弟节点仍渲染（INV-12 关键落点）
    expect(screen.getByText(SIBLING_TEXT)).toBeDefined();
    // 兜底不自动改写状态：恢复仅由用户显式点击触发
    expect(onResetCanvas).not.toHaveBeenCalled();
  });

  // TC-EB-02（INV-12）：点击"恢复画布" → onResetCanvas 被调用（画布重置），
  // ErrorBoundary 退出兜底、子树重新渲染正常（父级换回正常子组件后可见）
  it("TC-EB-02 点击恢复画布时回调被调用且子树重新渲染（INV-12）", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});

    /** 测试挂架：恢复回调切换为正常子组件（模拟 App 层重置后的子树重挂载） */
    function Harness(): ReactNode {
      const [broken, setBroken] = useState(true);
      return (
        <CanvasErrorBoundary onResetCanvas={() => setBroken(false)}>
          {broken ? <Bomb /> : <div>{RECOVERED_TEXT}</div>}
        </CanvasErrorBoundary>
      );
    }

    render(<Harness />);
    expect(screen.getByText("画布渲染异常")).toBeDefined();

    fireEvent.click(screen.getByText("恢复画布"));

    // 兜底退出、子树重新渲染正常内容
    expect(screen.getByText(RECOVERED_TEXT)).toBeDefined();
    expect(screen.queryByText("画布渲染异常")).toBeNull();
  });
});