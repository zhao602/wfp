// =============================================================================
// 订阅管理面板单元测试（web/src/subscription-management/__tests__/SubscriptionManagementPanel.test.tsx）
//
// 覆盖（B16 F5）：
//   1. 列表渲染：订阅行（钉住/状态/密钥版本）+ 死信行 + 计数
//   2. 空态：暂无订阅 / 暂无死信条目
//   3. 新建表单提交：event_types 拆分、版本钉住数字化、enabled 随行
//   4. 版本钉住前置校验：未钉定义 → 不发请求，表单错误提示
//   5. 轮换一次性明文弹窗：secret 明文展示（v+1）→ 关闭即不可见
//   6. 启停 / 删除：disableSubscription / deleteSubscription 调用与消息
//   7. 死信重投：redriveDeadLetter 调用 + redelivered 成功消息
//   8. 投递日志展开：listDeliveries 调用 + 记录渲染（已投递徽标）
// =============================================================================

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, cleanup, screen, fireEvent, waitFor } from "@testing-library/react";
import { SubscriptionManagementPanel } from "../SubscriptionManagementPanel";
import {
  listSubscriptions,
  upsertSubscription,
  deleteSubscription,
  disableSubscription,
  rotateSecret,
  listDeliveries,
  listDeadLetters,
  redriveDeadLetter,
} from "../subscriptionClient";
import type { Subscription, DeadLetterEntry, DeliveryRecord } from "../subscriptionTypes";

vi.mock("../subscriptionClient", () => ({
  listSubscriptions: vi.fn().mockResolvedValue([]),
  upsertSubscription: vi.fn(),
  deleteSubscription: vi.fn(),
  enableSubscription: vi.fn(),
  disableSubscription: vi.fn(),
  rotateSecret: vi.fn(),
  listDeliveries: vi.fn().mockResolvedValue([]),
  listDeadLetters: vi.fn().mockResolvedValue([]),
  redriveDeadLetter: vi.fn(),
}));

const sub: Subscription = {
  subscription_id: "sub-1",
  tenant_id: "default-tenant",
  url: "https://example.com/hook",
  event_types: ["instance.completed"],
  definition_id: "review_gate_flow",
  definition_version: 2,
  enabled: true,
  description: "主订阅",
  secret_version: 1,
  created_at: "2026-11-25T10:00:00Z",
  rotated_at: null,
  secret_set: true,
};

const dlq: DeadLetterEntry = {
  entry_id: "dlq_abc",
  subscription_id: "sub-1",
  event_id: "evt-1",
  event_type: "instance.completed",
  instance_id: "inst-9",
  attempts: 3,
  failed_at: "2026-11-25T11:00:00Z",
  last_error: "HTTP 500",
  payload_snapshot: {},
};

const record: DeliveryRecord = {
  subscription_id: "sub-1",
  event_id: "evt-1",
  event_type: "instance.completed",
  instance_id: "inst-9",
  attempts: 2,
  last_status: "delivered",
  delivered_at: "2026-11-25T11:30:00Z",
  last_error: "",
};

function fillForm(values: Record<string, string>): void {
  for (const [placeholder, value] of Object.entries(values)) {
    fireEvent.change(screen.getByPlaceholderText(placeholder), { target: { value } });
  }
}

// 每测试重置默认实现（clearAllMocks 不清 mockResolvedValue，防跨测试残留）
beforeEach(() => {
  vi.mocked(listSubscriptions).mockResolvedValue([]);
  vi.mocked(listDeadLetters).mockResolvedValue([]);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("SubscriptionManagementPanel 列表渲染与空态", () => {
  it("渲染订阅行（钉住/状态/密钥版本）与死信行、计数", async () => {
    vi.mocked(listSubscriptions).mockResolvedValue([sub]);
    vi.mocked(listDeadLetters).mockResolvedValue([dlq]);
    render(<SubscriptionManagementPanel />);
    // sub-1 同时出现于订阅行与死信行（subscription_id / dlq.subscription_id）
    expect((await screen.findAllByText("sub-1")).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("review_gate_flow · v2")).toBeInTheDocument();
    expect(screen.getByText("启用")).toBeInTheDocument();
    expect(screen.getByText("https://example.com/hook")).toBeInTheDocument();
    expect(screen.getByText("订阅 1 项")).toBeInTheDocument();
    expect(screen.getByText("死信 1 条")).toBeInTheDocument();
    expect(screen.getByText("dlq_abc")).toBeInTheDocument();
    expect(screen.getByTestId("subscription-table")).toBeInTheDocument();
    expect(screen.getByTestId("deadletter-table")).toBeInTheDocument();
  });

  it("空态：暂无订阅 / 暂无死信条目", async () => {
    render(<SubscriptionManagementPanel />);
    expect(await screen.findByText("暂无订阅")).toBeInTheDocument();
    expect(screen.getByText("暂无死信条目")).toBeInTheDocument();
    expect(screen.getByText("订阅 0 项")).toBeInTheDocument();
  });
});

describe("SubscriptionManagementPanel 表单", () => {
  it("新建提交：event_types 拆分、版本钉住数字化、请求体完整", async () => {
    render(<SubscriptionManagementPanel />);
    await screen.findByText("暂无订阅");
    fireEvent.click(screen.getByRole("button", { name: "新建订阅" }));
    fillForm({
      订阅标识: "sub-new",
      租户标识: "t-1",
      "https://example.com/webhook": "https://hooks.example.com/abc",
      "HMAC-SHA256 密钥": "sec-1",
      "instance.completed, node.": "instance.completed, node.",
      定义标识: "review_gate_flow",
      "如 2": "2",
      订阅描述: "测试订阅",
    });
    fireEvent.click(screen.getByRole("button", { name: "保存订阅" }));
    await waitFor(() => expect(upsertSubscription).toHaveBeenCalledTimes(1));
    expect(upsertSubscription).toHaveBeenCalledWith({
      subscription_id: "sub-new",
      tenant_id: "t-1",
      url: "https://hooks.example.com/abc",
      secret: "sec-1",
      event_types: ["instance.completed", "node."],
      definition_id: "review_gate_flow",
      definition_version: 2,
      enabled: true,
      description: "测试订阅",
    });
  });

  it("版本钉住未钉定义 → 不发请求，表单错误提示", async () => {
    render(<SubscriptionManagementPanel />);
    await screen.findByText("暂无订阅");
    fireEvent.click(screen.getByRole("button", { name: "新建订阅" }));
    fillForm({
      订阅标识: "sub-new",
      租户标识: "t-1",
      "https://example.com/webhook": "https://hooks.example.com/abc",
      "HMAC-SHA256 密钥": "sec-1",
      "如 2": "2",
    });
    fireEvent.click(screen.getByRole("button", { name: "保存订阅" }));
    expect(upsertSubscription).not.toHaveBeenCalled();
    expect(await screen.findByText(/表单校验失败/)).toBeInTheDocument();
  });
});

describe("SubscriptionManagementPanel 行操作", () => {
  it("轮换密钥：一次性明文弹窗展示（v+1），关闭后不可见", async () => {
    vi.mocked(listSubscriptions).mockResolvedValue([sub]);
    vi.mocked(rotateSecret).mockResolvedValueOnce({
      ...sub,
      secret_version: 2,
      rotated_at: "2026-11-25T12:00:00Z",
      secret: "new-plaintext-secret",
    });
    render(<SubscriptionManagementPanel />);
    fireEvent.click(await screen.findByRole("button", { name: "轮换密钥" }));
    const modal = await screen.findByTestId("rotate-modal");
    expect(modal).toBeInTheDocument();
    expect(screen.getByTestId("rotated-secret").textContent).toBe("new-plaintext-secret");
    expect(modal.textContent).toContain("v2");
    fireEvent.click(screen.getByRole("button", { name: "关闭" }));
    await waitFor(() => expect(screen.queryByTestId("rotate-modal")).toBeNull());
  });

  it("停用与删除：端点调用与消息", async () => {
    vi.mocked(listSubscriptions).mockResolvedValue([sub]);
    render(<SubscriptionManagementPanel />);
    await screen.findByText("sub-1");
    fireEvent.click(screen.getByRole("button", { name: "停用" }));
    await waitFor(() => expect(disableSubscription).toHaveBeenCalledWith("sub-1"));
    expect(await screen.findByText("订阅 sub-1 已停用")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    await waitFor(() => expect(deleteSubscription).toHaveBeenCalledWith("sub-1"));
    expect(await screen.findByText("订阅 sub-1 已删除")).toBeInTheDocument();
  });

  it("投递日志展开：listDeliveries 调用 + 记录渲染", async () => {
    vi.mocked(listSubscriptions).mockResolvedValue([sub]);
    vi.mocked(listDeliveries).mockResolvedValueOnce([record]);
    render(<SubscriptionManagementPanel />);
    fireEvent.click(await screen.findByRole("button", { name: "投递日志" }));
    await waitFor(() => expect(listDeliveries).toHaveBeenCalledWith("sub-1"));
    expect(await screen.findByTestId("deliveries-table")).toBeInTheDocument();
    expect(screen.getByText("inst-9")).toBeInTheDocument();
    expect(screen.getByText("已投递")).toBeInTheDocument();
  });

  it("死信重投：redriveDeadLetter 调用 + 成功消息", async () => {
    vi.mocked(listDeadLetters).mockResolvedValue([dlq]);
    vi.mocked(redriveDeadLetter).mockResolvedValueOnce({ ...dlq, redelivered: true });
    render(<SubscriptionManagementPanel />);
    fireEvent.click(await screen.findByRole("button", { name: "重投" }));
    await waitFor(() => expect(redriveDeadLetter).toHaveBeenCalledWith("dlq_abc"));
    expect(
      await screen.findByText("死信 dlq_abc 重投成功，已移出死信队列"),
    ).toBeInTheDocument();
  });
});
