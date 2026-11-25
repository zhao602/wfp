// =============================================================================
// 节点执行状态叠加层失败反馈单元测试（execution-visualization/__tests__/NodeExecutionStatusOverlay.test.tsx）
//
// 留待非沙箱环境执行：vitest run src/execution-visualization/__tests__/NodeExecutionStatusOverlay.test.tsx
//
// 覆盖（CHG-5 / AC-9 / AC-10 / AC-13 / AC-16 / AC-17）：
//   - node_id 为空的 INSTANCE_FAILED 不被丢弃，呈现实例级失败提示（AC-9/AC-6/AC-7）
//   - 实例级空原因呈现兜底占位文本（AC-8）
//   - 无失败时不渲染反馈区（Q-2/AC-10）
//   - SSE 重连耗尽出现"执行状态连接已断开"（AC-16），收到事件后清除（AC-17）
//
// 实现方式：mock ExecutionEventStreamClient 捕获配置，手动触发 onEvent/onRetryExhausted。
// =============================================================================

import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen, cleanup, act, fireEvent } from "@testing-library/react";
import type { ExecutionEvent } from "../../api/apiTypes";
import type { WorkflowCanvasState } from "../../canvas/canvasTypes";
import NodeExecutionStatusOverlay from "../NodeExecutionStatusOverlay";

/** Overlay 创建的 SSE 客户端配置（测试手动触发回调） */
interface MockClientConfig {
  instanceId: string;
  onEvent: (event: ExecutionEvent) => void;
  onRetryExhausted?: () => void;
}

const { captured } = vi.hoisted(() => ({
  captured: { config: null as MockClientConfig | null },
}));

vi.mock("../ExecutionEventStreamClient", () => ({
  ExecutionEventStreamClient: class {
    constructor(config: MockClientConfig) {
      captured.config = config;
    }
    connect(): void {}
    disconnect(): void {}
  },
}));

/** 构造最小可用执行事件 */
function makeEvent(overrides: Partial<ExecutionEvent>): ExecutionEvent {
  return {
    event_id: "e1",
    instance_id: "i1",
    sequence: 1,
    event_type: "INSTANCE_FAILED",
    timestamp: "2026-01-01T00:00:00Z",
    payload: {},
    ...overrides,
  };
}

const emptyCanvas: WorkflowCanvasState = { nodes: [], edges: [] };

beforeEach(() => {
  captured.config = null;
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("NodeExecutionStatusOverlay — 执行失败反馈区", () => {
  it("node_id 为空的 INSTANCE_FAILED 不被丢弃，呈现实例级失败提示", () => {
    render(<NodeExecutionStatusOverlay instanceId="i1" canvasState={emptyCanvas} />);
    act(() => {
      captured.config?.onEvent(makeEvent({ event_type: "INSTANCE_FAILED", payload: { error: "编译失败" } }));
    });
    expect(screen.getByText("工作流执行失败：编译失败")).toBeTruthy();
  });

  it("实例级空原因呈现兜底占位文本", () => {
    render(<NodeExecutionStatusOverlay instanceId="i1" canvasState={emptyCanvas} />);
    act(() => {
      captured.config?.onEvent(makeEvent({ event_type: "INSTANCE_FAILED", payload: {} }));
    });
    expect(screen.getByText("工作流执行失败（未提供详细原因）")).toBeTruthy();
  });

  it("无失败时不渲染反馈区", () => {
    const { container } = render(
      <NodeExecutionStatusOverlay instanceId="i1" canvasState={emptyCanvas} />,
    );
    expect(container.querySelector(".wfp-overlay-feedback")).toBeNull();
  });

  it("重连耗尽出现连接异常提示，收到事件后清除", () => {
    render(<NodeExecutionStatusOverlay instanceId="i1" canvasState={emptyCanvas} />);
    act(() => {
      captured.config?.onRetryExhausted?.();
    });
    expect(screen.getByText("执行状态连接已断开")).toBeTruthy();

    act(() => {
      captured.config?.onEvent(makeEvent({ event_type: "NODE_STARTED", node_id: "n1" }));
    });
    expect(screen.queryByText("执行状态连接已断开")).toBeNull();
  });
});

describe("NodeExecutionStatusOverlay — 收起/展开按钮", () => {
  it("默认展开：标题与计数可见，按钮 aria-expanded=true", () => {
    render(<NodeExecutionStatusOverlay instanceId="i1" canvasState={emptyCanvas} />);
    expect(screen.getByText("执行状态")).toBeTruthy();
    const toggle = screen.getByRole("button", { name: "收起执行状态与输出展示" });
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
  });

  it("点击收起后仅保留图标按钮（标题与计数隐藏），再点恢复", () => {
    const { container } = render(
      <NodeExecutionStatusOverlay instanceId="i1" canvasState={emptyCanvas} />,
    );
    act(() => {
      captured.config?.onEvent(makeEvent({ event_type: "NODE_COMPLETED", node_id: "n1" }));
    });
    expect(container.querySelector(".wfp-overlay-head")?.textContent).toContain("执行状态");

    fireEvent.click(screen.getByRole("button", { name: "收起执行状态与输出展示" }));
    // 收起态：标题消失，按钮变为展开语义，且容器内容只剩按钮
    expect(screen.queryByText("执行状态")).toBeNull();
    const expandButton = screen.getByRole("button", { name: "展开执行状态与输出展示" });
    expect(expandButton.getAttribute("aria-expanded")).toBe("false");
    expect(container.querySelector(".wfp-overlay-head--collapsed")).toBeTruthy();
    expect(container.querySelector(".wfp-overlay-output")).toBeNull();

    fireEvent.click(expandButton);
    expect(screen.getByText("执行状态")).toBeTruthy();
    expect(container.querySelector(".wfp-overlay-head--collapsed")).toBeNull();
  });
});
