// 路径⑧：审批运营面（B16 F4，design D1-D5）。
// UI：画布构建 触发器→控制流(人工审核，填审核说明)→输出展示 → 运行挂起；
// UI：#/admin/approval_queue 深链 → 队列行断言（实例/复核说明/剩余时限走秒/
//   超时动作徽标）→ 勾选批量复核；
// API：轮询终态。用例 1「通过」→ succeeded（行出队）；用例 2「全选当页 +
//   驳回」→ cancelled（reject 语义映射取消，T1 集成 test_reject_maps_to_
//   cancelled_and_queue_drains 口径）+ 行出队。

import { test, expect } from "@playwright/test";
import { waitForInstanceStatus } from "./helpers/backend";
import {
  canvasEdges,
  canvasNodes,
  connectNodes,
  dropNodeType,
  resetCanvas,
} from "./helpers/canvas";

test.beforeEach(async ({ page }) => {
  page.on("dialog", (dialog) => dialog.accept());
  await resetCanvas(page);
});

/** 构建人工审核三节点流并运行至挂起，返回 instance_id（两用例共用）。 */
async function buildSuspendedReviewInstance(page: import("@playwright/test").Page): Promise<string> {
  await dropNodeType(page, "trigger", 300, 220);
  await dropNodeType(page, "control_flow", 520, 320);
  await dropNodeType(page, "output_display", 740, 420);
  await expect(canvasNodes(page)).toHaveCount(3);

  // 控制流子类型 → 人工审核 + 审核说明（队列「复核说明」列数据源）
  await canvasNodes(page).nth(1).click();
  const kindSelect = page.locator("select.wfp-ncp-select").first();
  await kindSelect.waitFor({ state: "visible" });
  await kindSelect.selectOption("human_review_gate");
  await page.locator("textarea.wfp-ncp-input").first().fill("请人工复核该工单");

  await connectNodes(page, 0, 1);
  await connectNodes(page, 1, 2);
  await expect(canvasEdges(page)).toHaveCount(2);

  const [response] = await Promise.all([
    page.waitForResponse(
      (r) => r.url().includes("/api/v1/instances") && r.request().method() === "POST",
    ),
    page.getByRole("button", { name: "运行", exact: true }).click(),
  ]);
  expect(response.ok()).toBeTruthy();
  const instance = (await response.json()) as { instance_id: string };
  await waitForInstanceStatus(page.request, instance.instance_id, ["waiting_review"]);
  return instance.instance_id;
}

test("审批队列：挂起行断言→单条「通过」→succeeded 出队", async ({ page }) => {
  const instanceId = await buildSuspendedReviewInstance(page);

  // 深链审批运营面（hashRouter #/admin/approval_queue 合法面板）
  await page.goto("/#/admin/approval_queue");
  const row = page.locator("tr", { hasText: instanceId });
  await expect(row).toBeVisible({ timeout: 15_000 });
  await expect(row).toContainText("请人工复核该工单");
  await expect(row).toContainText("驳回"); // 超时动作徽标（review_timeout_action=reject 默认）
  await expect(row).toContainText(/已超时|[0-9]+:[0-9]{2}/); // 剩余时限走秒/超时红标

  // 勾选该实例 → 批量「通过」（备注留空）
  await page.getByRole("checkbox", { name: `选择实例 ${instanceId}` }).check();
  await page.getByRole("button", { name: "通过", exact: true }).click();
  await expect(page.getByText("批量通过完成")).toBeVisible();
  await waitForInstanceStatus(page.request, instanceId, ["succeeded"]);

  // 队列刷新后该行出队（runBatch 内即时 loadData）
  await expect(page.locator("tr", { hasText: instanceId })).toHaveCount(0);
});

test("审批队列：「全选当页」+「驳回」→cancelled 出队", async ({ page }) => {
  const instanceId = await buildSuspendedReviewInstance(page);

  await page.goto("/#/admin/approval_queue");
  const row = page.locator("tr", { hasText: instanceId });
  await expect(row).toBeVisible({ timeout: 15_000 });

  // 全选当页 → 批量「驳回」：reject 语义映射 cancelled（非 failed）
  await page.getByRole("checkbox", { name: "全选当页" }).check();
  await page.getByRole("button", { name: "驳回", exact: true }).click();
  await expect(page.getByText("批量驳回完成")).toBeVisible();
  await waitForInstanceStatus(page.request, instanceId, ["cancelled"]);

  await expect(page.locator("tr", { hasText: instanceId })).toHaveCount(0);
});
