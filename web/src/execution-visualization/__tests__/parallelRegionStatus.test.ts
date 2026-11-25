// =============================================================================
// 并行区域泳道状态推导单元测试（execution-visualization/__tests__/parallelRegionStatus.test.ts）
//
// 覆盖（B13 U2 / design D3）：
//   - deriveParallelRegions：parallel_join 区域提取（字段/分支路径推导）；
//     非 parallel_join、缺 parallel_branches 的排除；wait_n_of_m 配置透传
//   - reduceBranchStatuses：wait_all / wait_any / wait_n_of_m 三策略等待文案与
//     收敛判定；fast_fail 下 failed/cancelled 终态；乱序防护（终态不回退）；
//     非分支事件过滤
// =============================================================================

import { describe, it, expect } from "vitest";
import {
  deriveParallelRegions,
  reduceBranchStatuses,
  type ParallelRegionInfo,
} from "../parallelRegionStatus";
import type { ExecutionEvent } from "../../api/apiTypes";
import type { CanvasNode, WorkflowCanvasState } from "../../canvas/canvasTypes";
import type {
  ControlFlowKind,
  ControlFlowNodeConfiguration,
  NodeConfiguration,
  ParallelFailureStrategy,
} from "../../workflow-definition/definitionTypes";

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
  parallelFailureStrategy: ParallelFailureStrategy = "fast_fail",
): ControlFlowNodeConfiguration {
  return {
    node_type: "control_flow",
    control_flow_kind: controlFlowKind,
    conditions: [],
    parallel_failure_strategy: parallelFailureStrategy,
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

/** 菱形扇出画布：fork → b1/b2 → join（parallel_join wait_all fast_fail） */
function makeFanoutCanvas(
  joinOverrides: Partial<
    Pick<
      ControlFlowNodeConfiguration,
      "parallel_branches" | "wait_policy" | "wait_count"
    >
  > = {},
  joinFailureStrategy: ParallelFailureStrategy = "fast_fail",
): WorkflowCanvasState {
  return {
    nodes: [
      makeNode("fork", controlFlowConfig("loop")),
      makeNode("b1", controlFlowConfig("loop")),
      makeNode("b2", controlFlowConfig("loop")),
      makeNode(
        "join",
        controlFlowConfig(
          "parallel_join",
          {
            parallel_branches: ["b1", "b2"],
            wait_policy: "wait_all",
            ...joinOverrides,
          },
          joinFailureStrategy,
        ),
      ),
    ],
    edges: [
      { id: "e0", source: "fork", target: "b1" },
      { id: "e1", source: "fork", target: "b2" },
      { id: "e2", source: "b1", target: "join" },
      { id: "e3", source: "b2", target: "join" },
    ],
  };
}

/** 手工区域（reduce 测试直用，默认 join/b1/b2 wait_all fast_fail） */
function makeRegion(overrides: Partial<ParallelRegionInfo> = {}): ParallelRegionInfo {
  return {
    joinNodeId: "join",
    joinName: "join",
    branchEntries: ["b1", "b2"],
    waitPolicy: "wait_all",
    failureStrategy: "fast_fail",
    branchPathNodeIds: ["b1", "b2"],
    ...overrides,
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

// ---------------------------------------------------------------------------
// deriveParallelRegions — 区域提取
// ---------------------------------------------------------------------------

describe("deriveParallelRegions — 区域提取", () => {
  it("parallel_join + parallel_branches → 单区域，分支路径沿 edges 推导（不含 fork 上游）", () => {
    const regions = deriveParallelRegions(makeFanoutCanvas());
    expect(regions).toHaveLength(1);
    const region = regions[0];
    expect(region.joinNodeId).toBe("join");
    expect(region.joinName).toBe("join");
    expect(region.branchEntries).toEqual(["b1", "b2"]);
    expect(region.waitPolicy).toBe("wait_all");
    expect(region.failureStrategy).toBe("fast_fail");
    expect(region.branchPathNodeIds).toEqual(["b1", "b2"]);
  });

  it("非 parallel_join（loop）与缺 parallel_branches 的 join 不产出区域", () => {
    const loopOnly: WorkflowCanvasState = {
      nodes: [makeNode("fork", controlFlowConfig("loop"))],
      edges: [],
    };
    expect(deriveParallelRegions(loopOnly)).toHaveLength(0);

    const joinNoBranches: WorkflowCanvasState = {
      nodes: [
        makeNode("join", controlFlowConfig("parallel_join", { wait_policy: "wait_all" })),
      ],
      edges: [],
    };
    expect(deriveParallelRegions(joinNoBranches)).toHaveLength(0);
  });

  it("wait_n_of_m + wait_count 与 wait_all 失败策略透传", () => {
    const canvas = makeFanoutCanvas(
      {
        parallel_branches: ["b1", "b2", "b3"],
        wait_policy: "wait_n_of_m",
        wait_count: 2,
      },
      "wait_all",
    );
    const regions = deriveParallelRegions(canvas);
    expect(regions).toHaveLength(1);
    expect(regions[0].waitPolicy).toBe("wait_n_of_m");
    expect(regions[0].waitCount).toBe(2);
    expect(regions[0].failureStrategy).toBe("wait_all");
    expect(regions[0].branchEntries).toEqual(["b1", "b2", "b3"]);
  });

  it("分支路径推进至 join 前：多跳路径含中间节点且不含 join", () => {
    const canvas: WorkflowCanvasState = {
      nodes: [
        makeNode("b1", controlFlowConfig("loop")),
        makeNode("mid", controlFlowConfig("loop")),
        makeNode(
          "join",
          controlFlowConfig("parallel_join", {
            parallel_branches: ["b1"],
            wait_policy: "wait_all",
          }),
        ),
      ],
      edges: [
        { id: "e0", source: "b1", target: "mid" },
        { id: "e1", source: "mid", target: "join" },
      ],
    };
    const regions = deriveParallelRegions(canvas);
    expect(regions).toHaveLength(1);
    expect(regions[0].branchPathNodeIds).toEqual(["b1", "mid"]);
  });
});

// ---------------------------------------------------------------------------
// reduceBranchStatuses — 三 wait 策略归约
// ---------------------------------------------------------------------------

describe("reduceBranchStatuses — wait_all 归约", () => {
  it("部分到达：进度 + 未到分支进入等待原因", () => {
    const views = reduceBranchStatuses([makeRegion()], [
      branchEvent("PARALLEL_BRANCH_STARTED", "b1", "started"),
      branchEvent("PARALLEL_BRANCH_STARTED", "b2", "started"),
      branchEvent("PARALLEL_BRANCH_COMPLETED", "b1", "succeeded"),
    ]);
    expect(views).toHaveLength(1);
    const view = views[0];
    expect(view.hasEvents).toBe(true);
    expect(view.branches).toEqual([
      { branchId: "b1", status: "succeeded" },
      { branchId: "b2", status: "running" },
    ]);
    expect(view.converged).toBe(false);
    expect(view.waitReason).toBe("wait_all：已到达 1/2，等待 b2");
  });

  it("全部到达 → 已收敛", () => {
    const views = reduceBranchStatuses([makeRegion()], [
      branchEvent("PARALLEL_BRANCH_STARTED", "b1", "started"),
      branchEvent("PARALLEL_BRANCH_STARTED", "b2", "started"),
      branchEvent("PARALLEL_BRANCH_COMPLETED", "b1", "succeeded"),
      branchEvent("PARALLEL_BRANCH_COMPLETED", "b2", "succeeded"),
    ]);
    expect(views[0].converged).toBe(true);
    expect(views[0].waitReason).toBe("已收敛");
    expect(views[0].branches).toEqual([
      { branchId: "b1", status: "succeeded" },
      { branchId: "b2", status: "succeeded" },
    ]);
  });

  it("无事件：全 pending、hasEvents=false、等待原因含 0 进度", () => {
    const views = reduceBranchStatuses([makeRegion()], []);
    const view = views[0];
    expect(view.hasEvents).toBe(false);
    expect(view.branches).toEqual([
      { branchId: "b1", status: "pending" },
      { branchId: "b2", status: "pending" },
    ]);
    expect(view.converged).toBe(false);
    expect(view.waitReason).toBe("wait_all：已到达 0/2，等待 b1、b2");
  });
});

describe("reduceBranchStatuses — wait_any 归约", () => {
  const region = makeRegion({ waitPolicy: "wait_any" });

  it("未触发前显示等待文案", () => {
    const views = reduceBranchStatuses([region], [
      branchEvent("PARALLEL_BRANCH_STARTED", "b1", "started"),
    ]);
    expect(views[0].converged).toBe(false);
    expect(views[0].waitReason).toBe("wait_any：等待任一分支完成");
  });

  it("任一成功 → 已触发，裁撤其余分支", () => {
    const views = reduceBranchStatuses([region], [
      branchEvent("PARALLEL_BRANCH_STARTED", "b1", "started"),
      branchEvent("PARALLEL_BRANCH_STARTED", "b2", "started"),
      branchEvent("PARALLEL_BRANCH_COMPLETED", "b1", "succeeded"),
      branchEvent("PARALLEL_BRANCH_COMPLETED", "b2", "cancelled"),
    ]);
    expect(views[0].converged).toBe(true);
    expect(views[0].waitReason).toBe("wait_any：已触发，裁撤其余分支");
    expect(views[0].branches).toEqual([
      { branchId: "b1", status: "succeeded" },
      { branchId: "b2", status: "cancelled" },
    ]);
  });
});

describe("reduceBranchStatuses — wait_n_of_m 归约", () => {
  const region = makeRegion({
    branchEntries: ["b1", "b2", "b3"],
    waitPolicy: "wait_n_of_m",
    waitCount: 2,
  });

  it("按成功数进度显示 N/M，达标 → 已收敛", () => {
    const partial = reduceBranchStatuses([region], [
      branchEvent("PARALLEL_BRANCH_COMPLETED", "b1", "succeeded"),
    ]);
    expect(partial[0].converged).toBe(false);
    expect(partial[0].waitReason).toBe("wait_n_of_m：1/3");

    const reached = reduceBranchStatuses([region], [
      branchEvent("PARALLEL_BRANCH_COMPLETED", "b1", "succeeded"),
      branchEvent("PARALLEL_BRANCH_COMPLETED", "b2", "succeeded"),
    ]);
    expect(reached[0].converged).toBe(true);
    expect(reached[0].waitReason).toBe("已收敛");
  });
});

describe("reduceBranchStatuses — 终态与过滤", () => {
  it("fast_fail：分支失败 + 取消事件 → failed/cancelled 终态", () => {
    const views = reduceBranchStatuses([makeRegion()], [
      branchEvent("PARALLEL_BRANCH_STARTED", "b1", "started"),
      branchEvent("PARALLEL_BRANCH_STARTED", "b2", "started"),
      branchEvent("PARALLEL_BRANCH_COMPLETED", "b1", "failed"),
      branchEvent("PARALLEL_BRANCH_COMPLETED", "b2", "cancelled"),
    ]);
    expect(views[0].branches).toEqual([
      { branchId: "b1", status: "failed" },
      { branchId: "b2", status: "cancelled" },
    ]);
    expect(views[0].converged).toBe(true);
    expect(views[0].waitReason).toBe("已收敛");
  });

  it("乱序防护：终态不因迟到 STARTED 回退", () => {
    const views = reduceBranchStatuses([makeRegion()], [
      branchEvent("PARALLEL_BRANCH_COMPLETED", "b1", "succeeded"),
      branchEvent("PARALLEL_BRANCH_STARTED", "b1", "started"),
    ]);
    expect(views[0].branches[0]).toEqual({
      branchId: "b1",
      status: "succeeded",
    });
  });

  it("非分支事件不参与归约", () => {
    const nodeEvent: ExecutionEvent = {
      event_id: "evt-node",
      instance_id: "i1",
      sequence: 1,
      event_type: "NODE_STARTED",
      node_id: "join",
      timestamp: "2026-01-01T00:00:00Z",
      payload: { join_node_id: "join", branch_entry_id: "b1", status: "started" },
    };
    const views = reduceBranchStatuses([makeRegion()], [nodeEvent]);
    expect(views[0].hasEvents).toBe(false);
    expect(views[0].branches[0].status).toBe("pending");
  });
});
