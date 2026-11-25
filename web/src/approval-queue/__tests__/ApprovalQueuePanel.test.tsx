// =============================================================================
// 审批队列面板单元测试（web/src/approval-queue/__tests__/ApprovalQueuePanel.test.tsx）
//
// 覆盖（B16 F4）：
//   1. 行渲染：实例/定义·版本·租户副行/超时动作标签/复核说明/待审批计数
//   2. 空队列空态（工具栏隐藏，批量按钮不可达）
//   3. 超时红标：deadline 已过 → 「已超时」徽标；未超时行 → 倒计时文本
//   4. 批量通过：勾选 → batchReview 参数（instance_ids/decision/actor/备注缺省）
//      → 成功汇总消息展示
//   5. 未选择时三决策按钮禁用
//   6. 队列 5s 自动刷新（fake timers 推进 5000ms → listApprovals 再调一次）
//   7. 混合结果汇总（skipped/error 计数如实透出）
// =============================================================================

import { describe, it, expect, vi, afterEach } from "vitest";
import {
  render,
  cleanup,
  screen,
  fireEvent,
  waitFor,
  act,
} from "@testing-library/react";
import { ApprovalQueuePanel } from "../ApprovalQueuePanel";
import { listApprovals, batchReview } from "../approvalQueueClient";
import type { ApprovalRow, BatchReviewResponse } from "../approvalQueueTypes";

// mock approvalQueueClient：捕获调用并默认返回空队列
vi.mock("../approvalQueueClient", () => ({
  listApprovals: vi.fn().mockResolvedValue([]),
  batchReview: vi.fn(),
}));

function makeRow(overrides: Partial<ApprovalRow> & { instance_id: string }): ApprovalRow {
  return {
    definition_id: "review_gate_flow",
    definition_version: 2,
    tenant_id: "default-tenant",
    status: "waiting_review",
    checkpoint_id: "cp-review",
    suspended_at: new Date(Date.now() - 30_000).toISOString(),
    review_timeout_seconds: 3600,
    deadline: new Date(Date.now() + 3570_000).toISOString(),
    timeout_action: "reject",
    review_instructions: "请人工复核该实例",
    ...overrides,
  };
}

function mockRows(rows: ApprovalRow[]): void {
  vi.mocked(listApprovals).mockResolvedValue(rows);
}

function mockBatchResponse(resp: BatchReviewResponse): void {
  vi.mocked(batchReview).mockResolvedValueOnce(resp);
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.useRealTimers();
});

describe("ApprovalQueuePanel 行渲染与空态", () => {
  it("渲染实例行：定义·版本·租户副行、超时动作标签、复核说明、待审批计数", async () => {
    mockRows([
      makeRow({ instance_id: "inst-future" }),
      makeRow({
        instance_id: "inst-timeout",
        deadline: new Date(Date.now() - 60_000).toISOString(),
      }),
    ]);
    render(<ApprovalQueuePanel />);
    expect(await screen.findByText("inst-future")).toBeInTheDocument();
    expect(screen.getByText("inst-timeout")).toBeInTheDocument();
    expect(screen.getAllByText("review_gate_flow · v2 · default-tenant")).toHaveLength(2);
    // 「驳回」同时出现于超时动作徽标（2 行）与工具栏按钮，按 class 过滤徽标
    expect(
      screen.getAllByText("驳回").filter((el) => el.className === "wfp-apv-badge"),
    ).toHaveLength(2);
    expect(screen.getAllByText("请人工复核该实例")).toHaveLength(2);
    expect(screen.getByText("待审批 2 项")).toBeInTheDocument();
    expect(screen.getByTestId("approval-queue-table")).toBeInTheDocument();
  });

  it("空队列 → 空态文案，批量工具栏隐藏", async () => {
    mockRows([]);
    render(<ApprovalQueuePanel />);
    expect(await screen.findByText("暂无待审批实例")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "通过" })).toBeNull();
    expect(screen.getByText("待审批 0 项")).toBeInTheDocument();
  });
});

describe("ApprovalQueuePanel 超时红标与倒计时", () => {
  it("deadline 已过 → 「已超时」红徽标；未超时行 → 倒计时文本", async () => {
    mockRows([
      makeRow({ instance_id: "inst-future" }),
      makeRow({
        instance_id: "inst-timeout",
        deadline: new Date(Date.now() - 60_000).toISOString(),
      }),
    ]);
    render(<ApprovalQueuePanel />);
    expect(await screen.findByText("inst-future")).toBeInTheDocument();
    const badges = screen.getAllByText("已超时");
    expect(badges).toHaveLength(1);
    expect(badges[0].className).toBe("wfp-apv-timeout");
    // 未超时行渲染 m:ss 倒计时（deadline = now + ~90s 内偏移）
    expect(screen.getByText(/^\d+:\d{2}$/)).toBeInTheDocument();
  });
});

describe("ApprovalQueuePanel 批量复核", () => {
  it("勾选一行 → 通过：batchReview 参数含 instance_ids/decision/actor，成功汇总展示", async () => {
    mockRows([makeRow({ instance_id: "inst-1" })]);
    mockBatchResponse({
      results: [{ instance_id: "inst-1", status: "ok" }],
      summary: { total: 1, ok: 1, skipped: 0, error: 0 },
    });
    render(<ApprovalQueuePanel />);
    fireEvent.click(await screen.findByRole("checkbox", { name: "选择实例 inst-1" }));
    expect(screen.getByText("已选 1 项")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "通过" }));
    await waitFor(() => expect(batchReview).toHaveBeenCalledTimes(1));
    expect(batchReview).toHaveBeenCalledWith({
      instance_ids: ["inst-1"],
      decision: "approve",
      actor: "admin",
    });
    expect(
      await screen.findByText(/批量通过完成（共 1 项）：成功 1，跳过 0，失败 0/),
    ).toBeInTheDocument();
  });

  it("混合结果汇总：skipped/error 计数如实透出", async () => {
    mockRows([makeRow({ instance_id: "inst-1" }), makeRow({ instance_id: "inst-2" })]);
    mockBatchResponse({
      results: [
        { instance_id: "inst-1", status: "skipped", http_status: 409, error: "state_conflict" },
        { instance_id: "inst-2", status: "error", http_status: 500, error: "internal" },
      ],
      summary: { total: 2, ok: 0, skipped: 1, error: 1 },
    });
    render(<ApprovalQueuePanel />);
    fireEvent.click(await screen.findByRole("checkbox", { name: "选择实例 inst-1" }));
    fireEvent.click(screen.getByRole("button", { name: "驳回" }));
    await waitFor(() => expect(batchReview).toHaveBeenCalledTimes(1));
    expect(batchReview).toHaveBeenCalledWith({
      instance_ids: ["inst-1"],
      decision: "reject",
      actor: "admin",
    });
    expect(
      await screen.findByText(/批量驳回完成（共 2 项）：成功 0，跳过 1，失败 1/),
    ).toBeInTheDocument();
  });

  it("未选择时三决策按钮禁用", async () => {
    mockRows([makeRow({ instance_id: "inst-1" })]);
    render(<ApprovalQueuePanel />);
    await screen.findByRole("checkbox", { name: "选择实例 inst-1" });
    expect(screen.getByRole("button", { name: "通过" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "驳回" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "按超时动作" })).toBeDisabled();
  });
});

describe("ApprovalQueuePanel 自动刷新", () => {
  it("5s 自动刷新：fake timers 推进 5000ms → listApprovals 再调一次", async () => {
    vi.useFakeTimers();
    render(<ApprovalQueuePanel />);
    expect(listApprovals).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(listApprovals).toHaveBeenCalledTimes(2);
  });
});
