// =============================================================================
// 节点执行状态叠加层并行分支泳道测试（execution-visualization/__tests__/NodeExecutionStatusOverlay.parallel.test.tsx）
//
// 覆盖（B13 U2 / design D3，新文件——既有 overlay 两测试文件零修改）：
//   - PARALLEL_BRANCH_STARTED/COMPLETED 事件呈现泳道（标题/行状态/wait_all 等待原因）
//   - 分支事件不污染节点状态计数（node_id 指向 join，不得进节点状态映射）
//   - 全部到达后终态保持：INSTANCE_COMPLETED 后泳道与完成态共存
//   - 无并行区域的画布不渲染泳道；收起态隐藏泳道
//
// 实现方式：mock ExecutionEventStreamClient 捕获配置，手动触发 onEvent
//（沿 NodeExecutionStatusOverlay.test.tsx 既有模式）。
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

function controlFlowConfig(
  controlFlowKind: ControlFlowKind,
  overrides: Partial<
    Pick<
      ControlFlowNodeConfiguration,
      "parallel_branches" | "wait_policy" | "wait_count"
    >
  > = {},
): ControlFlowNodeConfiguration {
  return {
    node_type: "control_flow",
    control_flow_kind: controlFlowKind,
    conditions: [],
    parallel_failure_strategy: "fast_fail",
    review_timeout_action: "wait",
    ...overrides,
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

/** 含 parallel_join（b1/b2 wait_all）的画布 */
function parallelCanvas(): WorkflowCanvasState {
  return {
    nodes: [
      makeNode("b1", controlFlowConfig("loop")),
      makeNode("b2", controlFlowConfig("loop")),
      makeNode(
        "join",
        controlFlowConfig("parallel_join", {
          parallel_branches: ["b1", "b2"],
          wait_policy: "wait_all",
        }),
      ),
    ],
    edges: [
      { id: "e0", source: "b1", target: "join" },
      { id: "e1", source: "b2", target: "join" },
    ],
  };
}

function branchEvent(
  eventType: "PARALLEL_BRANCH_STARTED" | "PARALLEL_BRANCH_COMPLETED",
  branchId: string,
  status: string,
): ExecutionEvent {
  return {
    event_id: `evt-${eventType}-${branchId}`,
    instance_id: "i1",
    sequence: 0,
    event_type: eventType,
    node_id: "join",
    timestamp: "2026-01-01T00:00:00Z",
    payload: { join_node_id: "join", branch_entry_id: branchId, status },
  };
}

function instanceCompletedEvent(): ExecutionEvent {
  return {
    event_id: "evt-instance-completed",
    instance_id: "i1",
    sequence: 9,
    event_type: "INSTANCE_COMPLETED",
    timestamp: "2026-01-01T00:00:01Z",
    payload: {},
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
// 泳道渲染
// ---------------------------------------------------------------------------

describe("NodeExecutionStatusOverlay — 并行分支泳道", () => {
  it("分支事件呈现泳道：行状态与 wait_all 等待原因，且不污染节点状态计数", () => {
    const { container } = render(
      <NodeExecutionStatusOverlay instanceId="i1" canvasState={parallelCanvas()} />,
    );
    act(() => {
      captured.config?.onEvent(branchEvent("PARALLEL_BRANCH_STARTED", "b1", "started"));
      captured.config?.onEvent(branchEvent("PARALLEL_BRANCH_STARTED", "b2", "started"));
      captured.config?.onEvent(branchEvent("PARALLEL_BRANCH_COMPLETED", "b1", "succeeded"));
    });

    expect(container.querySelector(".wfp-overlay-parallel")).toBeTruthy();
    expect(screen.getByText("join · 并行分支")).toBeTruthy();
    expect(screen.getByText("b1")).toBeTruthy();
    expect(screen.getByText("b2")).toBeTruthy();
    expect(screen.getByText("wait_all：已到达 1/2，等待 b2")).toBeTruthy();
    // 分支事件不进入节点状态映射：join 不因分支事件计入"执行中"
    expect(screen.getByText("执行中: 0")).toBeTruthy();
  });

  it("全部到达后终态保持：INSTANCE_COMPLETED 后泳道与完成态共存", () => {
    const { container } = render(
      <NodeExecutionStatusOverlay instanceId="i1" canvasState={parallelCanvas()} />,
    );
    act(() => {
      captured.config?.onEvent(branchEvent("PARALLEL_BRANCH_STARTED", "b1", "started"));
      captured.config?.onEvent(branchEvent("PARALLEL_BRANCH_STARTED", "b2", "started"));
      captured.config?.onEvent(branchEvent("PARALLEL_BRANCH_COMPLETED", "b1", "succeeded"));
      captured.config?.onEvent(branchEvent("PARALLEL_BRANCH_COMPLETED", "b2", "succeeded"));
      captured.config?.onEvent(instanceCompletedEvent());
    });

    expect(container.querySelector(".wfp-overlay-parallel")).toBeTruthy();
    expect(screen.getByText("已收敛")).toBeTruthy();
    // 完成态提示与泳道共存（finished 空输出块仍渲染）
    expect(
      screen.getByText(
        "本轮执行已完成，但没有输出展示内容：画布中未放置「输出展示」节点，或其输入引用为空。",
      ),
    ).toBeTruthy();
  });

  it("无并行区域的画布不渲染泳道", () => {
    const plainCanvas: WorkflowCanvasState = {
      nodes: [makeNode("solo", controlFlowConfig("loop"))],
      edges: [],
    };
    const { container } = render(
      <NodeExecutionStatusOverlay instanceId="i1" canvasState={plainCanvas} />,
    );
    act(() => {
      captured.config?.onEvent(branchEvent("PARALLEL_BRANCH_STARTED", "b1", "started"));
    });
    expect(container.querySelector(".wfp-overlay-parallel")).toBeNull();
  });

  it("收起态隐藏泳道，展开后恢复", () => {
    const { container } = render(
      <NodeExecutionStatusOverlay instanceId="i1" canvasState={parallelCanvas()} />,
    );
    act(() => {
      captured.config?.onEvent(branchEvent("PARALLEL_BRANCH_STARTED", "b1", "started"));
    });
    expect(container.querySelector(".wfp-overlay-parallel")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "收起执行状态与输出展示" }));
    expect(container.querySelector(".wfp-overlay-parallel")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "展开执行状态与输出展示" }));
    expect(container.querySelector(".wfp-overlay-parallel")).toBeTruthy();
  });
});
