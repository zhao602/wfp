// =============================================================================
// 节点执行状态叠加层失败输入快照测试（execution-visualization/__tests__/NodeExecutionStatusOverlay.inputSnapshot.test.tsx）
//
// 覆盖（B13 U3 / design D6，新文件——既有 overlay 三测试文件零修改）：
//   - NODE_FAILED payload 含 input：失败条目呈现「失败输入快照」折叠按钮，
//     点击展开 JSON 块（aria-expanded 翻转、载荷内容可见），再点收起；
//   - NODE_FAILED payload 不含 input：不渲染快照按钮与折叠块（旧事件兼容）。
//
// 实现方式：mock ExecutionEventStreamClient 捕获配置，手动触发 onEvent
//（沿 NodeExecutionStatusOverlay.parallel.test.tsx 既有模式）。
// =============================================================================

import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen, cleanup, act, fireEvent } from "@testing-library/react";
import type { ExecutionEvent } from "../../api/apiTypes";
import type { CanvasNode, WorkflowCanvasState } from "../../canvas/canvasTypes";
import type {
  ControlFlowKind,
  ControlFlowNodeConfiguration,
  NodeConfiguration,
} from "../../workflow-definition/definitionTypes";
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

// ---------------------------------------------------------------------------
// 构造辅助
// ---------------------------------------------------------------------------

function controlFlowConfig(controlFlowKind: ControlFlowKind): ControlFlowNodeConfiguration {
  return {
    node_type: "control_flow",
    control_flow_kind: controlFlowKind,
    conditions: [],
    parallel_failure_strategy: "fast_fail",
    review_timeout_action: "wait",
  };
}

function makeNode(id: string, configuration: NodeConfiguration): CanvasNode {
  return {
    id,
    type: "default",
    position: { x: 0, y: 0 },
    data: {
      node_id: id,
      name: id,
      node_type: "control_flow",
      configuration,
    },
  };
}

/** 含失败节点 doomed 的画布 */
function failureCanvas(): WorkflowCanvasState {
  return {
    nodes: [makeNode("doomed", controlFlowConfig("loop"))],
    edges: [],
  };
}

function nodeFailedEvent(withInput: boolean): ExecutionEvent {
  return {
    event_id: "evt-node-failed-doomed",
    instance_id: "i1",
    sequence: 0,
    event_type: "NODE_FAILED",
    node_id: "doomed",
    timestamp: "2026-01-01T00:00:00Z",
    payload: withInput
      ? { error: "boom", input: { query: "topic" } }
      : { error: "boom" },
  };
}

beforeEach(() => {
  captured.config = null;
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// 失败输入快照折叠块
// ---------------------------------------------------------------------------

describe("NodeExecutionStatusOverlay — 失败输入快照", () => {
  it("payload 含 input：呈现折叠按钮，展开/收起 JSON 块（aria-expanded 翻转）", () => {
    const { container } = render(
      <NodeExecutionStatusOverlay instanceId="i1" canvasState={failureCanvas()} />,
    );
    act(() => {
      captured.config?.onEvent(nodeFailedEvent(true));
    });

    // 失败原因条目呈现
    expect(screen.getByText("节点 doomed 执行失败：boom")).toBeTruthy();
    // 默认收起：按钮在、JSON 块不在
    const toggle = screen.getByRole("button", { name: "展开失败输入快照" });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(container.querySelector(".wfp-overlay-feedback__snapshot-json")).toBeNull();

    // 展开快照：按钮文案翻转、JSON 块呈现输入载荷
    fireEvent.click(toggle);
    const collapse = screen.getByRole("button", { name: "收起失败输入快照" });
    expect(collapse.getAttribute("aria-expanded")).toBe("true");
    const jsonBlock = container.querySelector(".wfp-overlay-feedback__snapshot-json");
    expect(jsonBlock).toBeTruthy();
    expect(jsonBlock?.textContent).toContain('"query": "topic"');

    // 再次点击收起：JSON 块移除
    fireEvent.click(collapse);
    expect(screen.getByRole("button", { name: "展开失败输入快照" })).toBeTruthy();
    expect(container.querySelector(".wfp-overlay-feedback__snapshot-json")).toBeNull();
  });

  it("payload 不含 input：不渲染快照按钮与折叠块（旧事件兼容）", () => {
    const { container } = render(
      <NodeExecutionStatusOverlay instanceId="i1" canvasState={failureCanvas()} />,
    );
    act(() => {
      captured.config?.onEvent(nodeFailedEvent(false));
    });

    expect(screen.getByText("节点 doomed 执行失败：boom")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /失败输入快照/ })).toBeNull();
    expect(container.querySelector(".wfp-overlay-feedback__snapshot")).toBeNull();
    expect(container.querySelector(".wfp-overlay-feedback__snapshot-json")).toBeNull();
  });
});
