// 路径⑨：订阅管理面（B16 F5，design D6-D8）。
// 与 04-subscriptions 严格区分：04 锁定 /api/v1/subscriptions（实时触发族，
//   B8d 已知缺陷 D1 → 503 契约锁定）；本用例面向 /api/v1/event-export/
//   subscriptions（事件导出 Webhook 订阅族，B16 新增正向链路）。
// 链路：API 探针建订阅（不可达回调 127.0.0.1:9 → 快速失败）→ UI 深链
//   #/admin/subscription_management 行断言 → UI 表单建订阅 → 轮换密钥
//   （一次性明文弹窗）→ 画布跑两节点流触发 instance.succeeded（领域事件
//   点分口径，execution_runner 发布点 P2；租户 tenant_default = 事件
//   tenant 缺省富化）→ 投递失败 → 死信入队（重试
//   2 次 × 0.5s 退避，playwright.config env 调速）→ UI 投递日志/死信
//   重投（redelivered=false 仍留队）→ UI 删除 + API 删除收口。

import { test, expect } from "@playwright/test";
import { waitForInstanceStatus } from "./helpers/backend";
import {
  canvasEdges,
  canvasNodes,
  connectNodes,
  dropNodeType,
  resetCanvas,
} from "./helpers/canvas";

const EVENT_API = "/api/v1/event-export";

test.beforeEach(async ({ page }) => {
  page.on("dialog", (dialog) => dialog.accept());
  await resetCanvas(page);
});

/** 轮询死信队列直至指定实例条目达标（投递重试异步收口的稳定兜底；
 *  以实例为锚防陈旧订阅/条目串扰——复用后端可能载入历史运行遗留订阅）。 */
async function waitForInstanceDeadLetters(
  request: import("@playwright/test").APIRequestContext,
  instanceId: string,
  minCount: number,
  timeoutMs = 20_000,
): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const res = await request.get(`${EVENT_API}/dead-letters`);
    if (res.ok()) {
      const body = (await res.json()) as { dead_letters: { instance_id: string }[] };
      const matched = body.dead_letters.filter(
        (entry) => entry.instance_id === instanceId,
      ).length;
      if (matched >= minCount) {
        return;
      }
    }
    if (Date.now() > deadline) {
      throw new Error(`实例 ${instanceId} 死信 ${minCount} 条等待超时`);
    }
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
}

test("事件导出订阅：探针+UI 建订→轮换→投递失败→死信重投→删除", async ({ page, request }) => {
  // 1. API 探针建订阅（租户对齐事件缺省富化 tenant_default；回调不可达）
  const probeRes = await request.post(`${EVENT_API}/subscriptions`, {
    data: {
      subscription_id: "e2e_probe_sub",
      tenant_id: "tenant_default",
      url: "http://127.0.0.1:9/probe-hook",
      secret: "e2e-probe-secret",
      event_types: ["instance.succeeded"],
      description: "e2e 探针订阅",
    },
  });
  expect(probeRes.ok()).toBeTruthy();

  // 2. UI 深链订阅管理面 → 探针行可见
  await page.goto("/#/admin/subscription_management");
  const subTable = page.getByTestId("subscription-table");
  const probeRow = subTable.locator("tr", { hasText: "e2e_probe_sub" });
  await expect(probeRow).toBeVisible({ timeout: 15_000 });

  // 3. UI 表单建订阅（占位符定位：表单 label 未挂 htmlFor）
  await page.getByRole("button", { name: "新建订阅" }).click();
  await page.getByPlaceholder("订阅标识").fill("e2e_ui_sub");
  await page.getByPlaceholder("租户标识").fill("tenant_default");
  await page.getByPlaceholder("https://example.com/webhook").fill("http://127.0.0.1:9/ui-hook");
  await page.getByPlaceholder("HMAC-SHA256 密钥").fill("e2e-ui-secret");
  await page.getByPlaceholder("instance.completed, node.").fill("instance.succeeded");
  await page.getByPlaceholder("订阅描述").fill("e2e UI 订阅");
  await page.getByRole("button", { name: "保存订阅" }).click();
  await expect(page.getByText("订阅 e2e_ui_sub 已保存")).toBeVisible();
  const uiRow = subTable.locator("tr", { hasText: "e2e_ui_sub" });
  await expect(uiRow).toBeVisible();

  // 4. 轮换密钥 → 一次性明文弹窗（新密钥非空且 ≠ 旧值）→ 关闭
  await uiRow.getByRole("button", { name: "轮换密钥" }).click();
  const modal = page.getByTestId("rotate-modal");
  await expect(modal).toBeVisible();
  const rotatedSecret = (await page.getByTestId("rotated-secret").innerText()).trim();
  expect(rotatedSecret.length).toBeGreaterThan(0);
  expect(rotatedSecret).not.toBe("e2e-ui-secret");
  await modal.getByRole("button", { name: "关闭" }).click();
  await expect(modal).toHaveCount(0);

  // 5. 画布两节点流（触发器→输出展示）→ 运行成功 → instance.succeeded 事件
  await page.goto("/");
  await dropNodeType(page, "trigger", 320, 240);
  await dropNodeType(page, "output_display", 560, 340);
  await expect(canvasNodes(page)).toHaveCount(2);
  await connectNodes(page, 0, 1);
  await expect(canvasEdges(page)).toHaveCount(1);
  const [response] = await Promise.all([
    page.waitForResponse(
      (r) => r.url().includes("/api/v1/instances") && r.request().method() === "POST",
    ),
    page.getByRole("button", { name: "运行", exact: true }).click(),
  ]);
  expect(response.ok()).toBeTruthy();
  const instance = (await response.json()) as { instance_id: string };
  await waitForInstanceStatus(page.request, instance.instance_id, ["succeeded"]);

  // 6. 双订阅投递均失败 → 各入一条死信（2 次 × 0.5s 退避后终态）
  await waitForInstanceDeadLetters(request, instance.instance_id, 2);

  // 7. 回订阅管理面（挂载即拉全量）→ 投递日志（失败徽标 + 实例）
  await page.goto("/#/admin/subscription_management");
  await expect(probeRow).toBeVisible({ timeout: 15_000 });
  await probeRow.getByRole("button", { name: "投递日志" }).click();
  const deliveries = page.getByTestId("deliveries-table");
  await expect(deliveries).toBeVisible();
  await expect(deliveries).toContainText(instance.instance_id);
  await expect(deliveries).toContainText("失败");

  // 8. 死信重投（回调仍不可达 → redelivered=false 仍留队）；行锚定本用例实例
  const deadletterRow = page
    .getByTestId("deadletter-table")
    .locator("tr", { hasText: instance.instance_id })
    .first();
  await expect(deadletterRow).toBeVisible();
  await deadletterRow.getByRole("button", { name: "重投" }).click();
  await expect(page.getByText("重投完成（仍失败，保留在死信队列）")).toBeVisible();

  // 9. UI 删除 e2e_ui_sub → 行消失；API 删除探针 → 列表收口
  await uiRow.getByRole("button", { name: "删除", exact: true }).click();
  await expect(subTable.locator("tr", { hasText: "e2e_ui_sub" })).toHaveCount(0);
  const deleteRes = await request.delete(`${EVENT_API}/subscriptions/e2e_probe_sub`);
  expect(deleteRes.ok()).toBeTruthy();
  const listRes = await request.get(`${EVENT_API}/subscriptions`);
  const listBody = (await listRes.json()) as { subscriptions: { subscription_id: string }[] };
  const remaining = listBody.subscriptions.map((s) => s.subscription_id);
  expect(remaining).not.toContain("e2e_probe_sub");
  expect(remaining).not.toContain("e2e_ui_sub");
});
