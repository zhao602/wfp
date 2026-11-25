// =============================================================================
// 并行区域泳道状态推导（src/execution-visualization/parallelRegionStatus.ts）
//
// 职责（B13 U2 / design D3）：
//   1. deriveParallelRegions：扫描画布 parallel_join 节点（control_flow_kind
//      === "parallel_join" 且配置含非空 parallel_branches），推导区域信息与
//      分支路径节点集（沿 edges 从分支入口推进至 join 前）；
//   2. reduceBranchStatuses：消费实例事件流中的 PARALLEL_BRANCH_STARTED/
//      COMPLETED 事件，归约每分支五态（pending/running/succeeded/failed/
//      cancelled）与 join 等待原因（wait_all / wait_any / wait_n_of_m 三策略
//      文案；全部到达 → 已收敛）。
// 纯函数模块：无 React 依赖，供叠加层组件与单元测试直接使用。
// 乱序防护：分支一旦进入终态（succeeded/failed/cancelled），迟到的
// STARTED 事件不回退状态。
// =============================================================================

import type { WorkflowCanvasState, CanvasEdge } from "../canvas/canvasTypes";
import type { ExecutionEvent } from "../api/apiTypes";
import type {
  JoinWaitPolicy,
  ParallelFailureStrategy,
} from "../workflow-definition/definitionTypes";
import { isControlFlowConfig } from "../workflow-definition/definitionTypes";

/** 分支执行状态（五态；cancelled 为 fast_fail / wait_any 裁撤产物） */
export type BranchExecutionStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled";

/** 并行区域信息（由画布静态配置推导，与运行事件无关） */
export interface ParallelRegionInfo {
  joinNodeId: string;
  joinName: string;
  /** 声明序分支入口节点标识 */
  branchEntries: string[];
  waitPolicy: JoinWaitPolicy;
  waitCount?: number;
  failureStrategy: ParallelFailureStrategy;
  /** 分支路径节点集（沿 edges 从入口推进至 join 前的全部节点，去重，含入口，不含 join） */
  branchPathNodeIds: string[];
}

/** 并行区域运行视图（静态区域 + 事件归约结果） */
export interface ParallelRegionView {
  region: ParallelRegionInfo;
  /** 声明序分支状态列表（无事件分支为 pending） */
  branches: Array<{ branchId: string; status: BranchExecutionStatus }>;
  /** join 等待原因文案（策略前缀 + 进度；全部到达 → 已收敛） */
  waitReason: string;
  /** 是否已按策略收敛（wait_all 全部到达 / wait_any 任一成功 / wait_n_of_m 成功数达标） */
  converged: boolean;
  /** 是否已收到任一分支事件（决定泳道是否呈现） */
  hasEvents: boolean;
}

/** 分支终态集合：一旦进入不可回退（乱序迟到的 STARTED 不降级） */
const TERMINAL_BRANCH_STATUSES: readonly BranchExecutionStatus[] = [
  "succeeded",
  "failed",
  "cancelled",
];

/** 分支完成事件 payload.status → 分支终态（未知值不归约） */
function toTerminalStatus(raw: unknown): BranchExecutionStatus | null {
  if (raw === "succeeded" || raw === "failed" || raw === "cancelled") {
    return raw;
  }
  return null;
}

/** 沿 edges 从分支入口推进至 join 前的路径节点集（去重，含入口，不含 join） */
function deriveBranchPathNodeIds(
  edges: CanvasEdge[],
  branchEntries: string[],
  joinNodeId: string,
): string[] {
  const successors = new Map<string, string[]>();
  for (const edge of edges) {
    const list = successors.get(edge.source);
    if (list) {
      list.push(edge.target);
    } else {
      successors.set(edge.source, [edge.target]);
    }
  }
  const visited = new Set<string>();
  const visit = (nodeId: string): void => {
    if (nodeId === joinNodeId || visited.has(nodeId)) return;
    visited.add(nodeId);
    for (const next of successors.get(nodeId) ?? []) {
      visit(next);
    }
  };
  for (const entry of branchEntries) {
    visit(entry);
  }
  return [...visited];
}

/** 扫描画布并行区域：parallel_join 且配置含非空 parallel_branches */
export function deriveParallelRegions(
  canvasState: WorkflowCanvasState,
): ParallelRegionInfo[] {
  const regions: ParallelRegionInfo[] = [];
  for (const node of canvasState.nodes) {
    const config = node.data.configuration;
    if (!isControlFlowConfig(config)) continue;
    if (config.control_flow_kind !== "parallel_join") continue;
    if (!Array.isArray(config.parallel_branches)) continue;
    const branchEntries = config.parallel_branches.filter(
      (id): id is string => typeof id === "string",
    );
    if (branchEntries.length === 0) continue;
    regions.push({
      joinNodeId: node.id,
      joinName: node.data.name,
      branchEntries,
      waitPolicy: config.wait_policy ?? "wait_all",
      waitCount:
        typeof config.wait_count === "number" ? config.wait_count : undefined,
      failureStrategy: config.parallel_failure_strategy ?? "fast_fail",
      branchPathNodeIds: deriveBranchPathNodeIds(
        canvasState.edges,
        branchEntries,
        node.id,
      ),
    });
  }
  return regions;
}

/** 归约分支事件 → 每区域分支状态与等待原因（乱序防护：终态不回退） */
export function reduceBranchStatuses(
  regions: ParallelRegionInfo[],
  events: ExecutionEvent[],
): ParallelRegionView[] {
  const stateByKey = new Map<string, BranchExecutionStatus>();
  for (const event of events) {
    if (
      event.event_type !== "PARALLEL_BRANCH_STARTED" &&
      event.event_type !== "PARALLEL_BRANCH_COMPLETED"
    ) {
      continue;
    }
    const joinNodeId =
      typeof event.payload.join_node_id === "string"
        ? event.payload.join_node_id
        : event.node_id;
    const branchId = event.payload.branch_entry_id;
    if (typeof joinNodeId !== "string" || typeof branchId !== "string") {
      continue;
    }
    const key = `${joinNodeId}::${branchId}`;
    const prev = stateByKey.get(key);
    if (prev !== undefined && TERMINAL_BRANCH_STATUSES.includes(prev)) {
      continue;
    }
    if (event.event_type === "PARALLEL_BRANCH_STARTED") {
      stateByKey.set(key, "running");
      continue;
    }
    const terminal = toTerminalStatus(event.payload.status);
    if (terminal !== null) {
      stateByKey.set(key, terminal);
    }
  }

  return regions.map((region) => {
    const branches = region.branchEntries.map((branchId) => ({
      branchId,
      status:
        stateByKey.get(`${region.joinNodeId}::${branchId}`) ?? "pending",
    }));
    return buildRegionView(region, branches);
  });
}

/** 按等待策略计算分支视图的等待原因与收敛态 */
function buildRegionView(
  region: ParallelRegionInfo,
  branches: Array<{ branchId: string; status: BranchExecutionStatus }>,
): ParallelRegionView {
  const total = branches.length;
  const succeededCount = branches.filter(
    (branch) => branch.status === "succeeded",
  ).length;
  const settledCount = branches.filter(
    (branch) => branch.status !== "pending" && branch.status !== "running",
  ).length;
  const hasEvents = branches.some((branch) => branch.status !== "pending");

  let waitReason: string;
  let converged: boolean;
  switch (region.waitPolicy) {
    case "wait_any": {
      converged = succeededCount > 0;
      waitReason = converged
        ? "wait_any：已触发，裁撤其余分支"
        : "wait_any：等待任一分支完成";
      break;
    }
    case "wait_n_of_m": {
      const needed =
        typeof region.waitCount === "number" && region.waitCount > 0
          ? region.waitCount
          : total;
      converged = succeededCount >= needed;
      waitReason = converged
        ? "已收敛"
        : `wait_n_of_m：${succeededCount}/${total}`;
      break;
    }
    case "wait_all":
    default: {
      converged = settledCount >= total;
      if (converged) {
        waitReason = "已收敛";
      } else {
        const missing = branches
          .filter(
            (branch) => branch.status === "pending" || branch.status === "running",
          )
          .map((branch) => branch.branchId);
        waitReason = `wait_all：已到达 ${settledCount}/${total}，等待 ${missing.join("、")}`;
      }
      break;
    }
  }

  return { region, branches, waitReason, converged, hasEvents };
}
