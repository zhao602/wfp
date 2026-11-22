// =============================================================================
// 开发包审核台测试（src/market/__tests__/DevReviewBoard.test.tsx）
//
// 留待非沙箱环境执行：cd web && npx vitest run src/market/__tests__/DevReviewBoard.test.tsx
//
// 覆盖用例（agent_market_user_development T23.2 / AC-DEV-21/23 / DC-06）：
//   TC-DRB-01 清单/详情渲染 + 状态徽章（同一色板语义）
//   TC-DRB-02 报告（按 layer 分组）与流转历史展示
//   TC-DRB-03 批准/驳回按钮在 reviewing 可用（驳回须填原因）
//   TC-DRB-04 重新校验按钮在 security_scan 可用
//   TC-DRB-05 触发上架在 approved 可用
//   TC-DRB-06 流转失败 → 错误条（AC-DEV-22）
//   TC-DRB-07 清单加载失败 → 错误条且页面可渲染（AC-DEV-22）
// =============================================================================

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { DevReviewBoard } from "../DevReviewBoard";
import {
  listDevSubmissions,
  getDevSubmission,
  getHistory,
  transitionDevSubmission,
  rescanDevSubmission,
} from "../devSubmissionApiClient";
import type {
  DevContributionState,
  DevHistoryResponse,
  DevSubmission,
  DevSubmissionDetailResponse,
  DevValidationReport,
} from "../devSubmissionTypes";

vi.mock("../devSubmissionApiClient", () => ({
  listDevSubmissions: vi.fn(),
  getDevSubmission: vi.fn(),
  getHistory: vi.fn(),
  transitionDevSubmission: vi.fn(),
  rescanDevSubmission: vi.fn(),
}));

/** 三层校验报告夹具 */
function makeReport(): DevValidationReport {
  return {
    report_id: "r1",
    valid: true,
    layers: ["L1", "L2", "L3"],
    findings: [
      {
        layer: "L1",
        rule_id: "input_schema_undeclared",
        severity: "warning",
        message: "input_schema 未声明",
        location: null,
      },
      {
        layer: "L3",
        rule_id: "dangerous_call",
        severity: "error",
        message: "检测到 eval 调用",
        location: "node.py:1",
      },
    ],
    scanner_version: "dev-validation/1.0",
    checked_at: "2026-11-11T00:00:00Z",
    error: null,
  };
}

/** 开发提交主体夹具 */
function makeSubmission(state: DevContributionState): DevSubmission {
  return {
    submission_id: "sub-1",
    developer_id: "alice",
    node_name: "demo-node",
    version: "1.0.0",
    package_digest: "d".repeat(64),
    package_size: 100,
    file_count: 2,
    state,
    validation_reports: [makeReport()],
    rejection_reason: "",
    state_history: [],
    created_at: "2026-11-11T00:00:00Z",
    updated_at: "2026-11-11T00:00:00Z",
  };
}

/** 提交详情夹具（DV-5） */
function makeDetail(state: DevContributionState): DevSubmissionDetailResponse {
  return {
    submission: makeSubmission(state),
    latest_report: makeReport(),
    source_kind: "user_dev",
  };
}

/** 流转历史夹具（DV-8） */
function makeHistory(): DevHistoryResponse {
  return {
    submission_id: "sub-1",
    history: [
      {
        from_state: "submitted",
        to_state: "security_scan",
        operator_id: "scanner-1",
        reason: "",
        occurred_at: "2026-11-11T01:00:00Z",
      },
      {
        from_state: "security_scan",
        to_state: "reviewing",
        operator_id: "reviewer-1",
        reason: "扫描通过",
        occurred_at: "2026-11-11T02:00:00Z",
      },
    ],
  };
}

/** 按指定状态装配 mock（清单 + 详情 + 历史 + 流转 + 重扫） */
function mockState(state: DevContributionState): void {
  vi.mocked(listDevSubmissions).mockResolvedValue({
    submissions: [makeSubmission(state)],
    total: 1,
  });
  vi.mocked(getDevSubmission).mockResolvedValue(makeDetail(state));
  vi.mocked(getHistory).mockResolvedValue(makeHistory());
  vi.mocked(transitionDevSubmission).mockResolvedValue({
    submission_id: "sub-1",
    from_state: state,
    to_state: "approved",
    success: true,
    message: "流转成功",
  });
  vi.mocked(rescanDevSubmission).mockResolvedValue(makeReport());
}

beforeEach(() => {
  mockState("reviewing");
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("DevReviewBoard", () => {
  it("TC-DRB-01 清单/详情渲染 + 状态徽章（同一色板语义）", async () => {
    render(<DevReviewBoard operatorId="op-1" />);

    // 清单行 + 状态徽章（reviewing=yellow，与 ContributionReviewBoard 同色板；
    // jsdom 将 yellow 归一化为 rgb(255, 255, 0)）
    const badge = await screen.findByText("reviewing");
    expect(badge).toHaveStyle({ color: "rgb(255, 255, 0)" });
    expect(screen.getByTestId("dev-review-table")).toHaveTextContent("demo-node");

    // 点击行 → 详情（提交标识 + 包摘要）
    fireEvent.click(screen.getByText("demo-node"));
    const detail = await screen.findByTestId("dev-review-detail");
    expect(detail).toHaveTextContent("sub-1");
    expect(detail).toHaveTextContent("100 字节 / 2 文件");
  });

  it("TC-DRB-02 报告（按 layer 分组）与流转历史展示", async () => {
    render(<DevReviewBoard operatorId="op-1" />);

    fireEvent.click(await screen.findByText("demo-node"));

    // 三层报告按 layer 分组
    const report = await screen.findByTestId("dev-review-report");
    expect(report).toHaveTextContent("dev-validation/1.0");
    expect(screen.getByTestId("dev-review-report-L1")).toHaveTextContent(
      "input_schema_undeclared",
    );
    expect(screen.getByTestId("dev-review-report-L3")).toHaveTextContent(
      "dangerous_call",
    );
    expect(screen.getByTestId("dev-review-report-L2")).toHaveTextContent("0 项");

    // 流转历史
    const history = await screen.findByTestId("dev-review-history");
    expect(history).toHaveTextContent("submitted → security_scan");
    expect(history).toHaveTextContent("security_scan → reviewing");
    expect(history).toHaveTextContent("扫描通过");
  });

  it("TC-DRB-03 批准/驳回按钮在 reviewing 可用（驳回须填原因）", async () => {
    render(<DevReviewBoard operatorId="op-1" />);

    fireEvent.click(await screen.findByText("demo-node"));
    const approveButton = await screen.findByTestId("dev-review-approve-button");
    expect(approveButton).toBeInTheDocument();

    // 驳回未填原因 → 禁用；填写后启用
    const rejectButton = screen.getByTestId("dev-review-reject-button");
    expect(rejectButton).toBeDisabled();
    fireEvent.change(screen.getByTestId("dev-review-reject-reason-input"), {
      target: { value: "扫描发现高危调用" },
    });
    expect(screen.getByTestId("dev-review-reject-button")).toBeEnabled();

    // 批准 → DV-6 流转调用（operator_id 必填）
    fireEvent.click(approveButton);
    await screen.findByTestId("dev-review-message");
    expect(transitionDevSubmission).toHaveBeenCalledWith(
      "sub-1",
      "approved",
      "op-1",
      "",
    );
  });

  it("TC-DRB-04 重新校验按钮在 security_scan 可用", async () => {
    mockState("security_scan");
    render(<DevReviewBoard operatorId="op-1" />);

    fireEvent.click(await screen.findByText("demo-node"));
    const rescanButton = await screen.findByTestId("dev-review-rescan-button");
    expect(rescanButton).toBeInTheDocument();
    // 送人工审核按钮同态可用
    expect(screen.getByTestId("dev-review-review-button")).toBeInTheDocument();
    // reviewing 态的批准按钮不存在
    expect(screen.queryByTestId("dev-review-approve-button")).not.toBeInTheDocument();

    fireEvent.click(rescanButton);
    await screen.findByTestId("dev-review-message");
    expect(rescanDevSubmission).toHaveBeenCalledWith("sub-1", "op-1");
  });

  it("TC-DRB-05 触发上架在 approved 可用", async () => {
    mockState("approved");
    vi.mocked(transitionDevSubmission).mockResolvedValue({
      submission_id: "sub-1",
      from_state: "approved",
      to_state: "published",
      success: true,
      message: "上架成功（created）",
    });
    render(<DevReviewBoard operatorId="op-1" />);

    fireEvent.click(await screen.findByText("demo-node"));
    fireEvent.click(await screen.findByTestId("dev-review-publish-button"));

    const message = await screen.findByTestId("dev-review-message");
    expect(message).toHaveTextContent("上架成功（created）");
    expect(transitionDevSubmission).toHaveBeenCalledWith(
      "sub-1",
      "published",
      "op-1",
      "",
    );
  });

  it("TC-DRB-06 流转失败 → 错误条（AC-DEV-22）", async () => {
    mockState("approved");
    vi.mocked(transitionDevSubmission).mockRejectedValue(
      new Error("上架失败，提交保持 APPROVED 可重试"),
    );
    render(<DevReviewBoard operatorId="op-1" />);

    fireEvent.click(await screen.findByText("demo-node"));
    fireEvent.click(await screen.findByTestId("dev-review-publish-button"));

    const errorBar = await screen.findByTestId("dev-review-error");
    expect(errorBar).toHaveTextContent("上架失败，提交保持 APPROVED 可重试");
  });

  it("TC-DRB-07 清单加载失败 → 错误条且页面可渲染（AC-DEV-22）", async () => {
    vi.mocked(listDevSubmissions).mockRejectedValue(new Error("查询开发提交清单失败"));
    render(<DevReviewBoard operatorId="op-1" />);

    const errorBar = await screen.findByTestId("dev-review-error");
    expect(errorBar).toHaveTextContent("查询开发提交清单失败");
    // 页面可渲染：表格空态仍在
    expect(screen.getByTestId("dev-review-table")).toHaveTextContent("暂无开发提交");
  });
});
