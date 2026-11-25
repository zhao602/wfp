// 路径⑥：校验未通过面板渲染与画布定位（B13 U3 e2e）。
// 链路 = 拖放缺省 control_flow（conditional_branch 空 conditions → 运行前置校验唯一
//   ERROR，probe 预验证：恰 1 错误 / 0 警告，node_id 挂靠条件分支节点）
//   → 运行 → 校验面板渲染（标题计数 / 问题行 / 定位行「节点 …」）
//   → 定位到画布（目标节点选中态）→ 关闭面板；
//   负向断言：校验未通过不启动实例（无 POST /api/v1/instances）。

import { test, expect } from "@playwright/test";
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

test("缺省条件分支空条件→运行→校验面板渲染并定位到画布", async ({ page }) => {
  // 1. 拖放链：trigger → control_flow（缺省 conditional_branch，conditions 为空）→ output_display
  await dropNodeType(page, "trigger", 300, 220);
  await dropNodeType(page, "control_flow", 520, 320);
  await dropNodeType(page, "output_display", 740, 420);
  await expect(canvasNodes(page)).toHaveCount(3);

  // 2. 连线 0→1、1→2（线性链：拓扑无问题，配置 ERROR 唯一）
  await connectNodes(page, 0, 1);
  await connectNodes(page, 1, 2);
  await expect(canvasEdges(page)).toHaveCount(2);

  // 3. 负向哨兵：任何 instances POST 即置位（校验失败语义下必须保持 false）
  let instancePosted = false;
  page.on("request", (request) => {
    if (request.url().includes("/api/v1/instances") && request.method() === "POST") {
      instancePosted = true;
    }
  });

  // 4. 运行：前置校验请求到达，报告 valid=false（App.tsx:602-608 口径）
  const [validateResponse] = await Promise.all([
    page.waitForResponse(
      (r) =>
        r.url().includes("/api/v1/workflows/validate") &&
        r.request().method() === "POST",
    ),
    page.getByRole("button", { name: "运行", exact: true }).click(),
  ]);
  expect(validateResponse.ok()).toBeTruthy();
  const report = (await validateResponse.json()) as { valid: boolean };
  expect(report.valid).toBe(false);

  // 5. 校验面板：role=alert + 标题计数 + 空分支条件问题行 + 定位行
  const panel = page.getByRole("alert");
  await expect(panel).toBeVisible();
  await expect(panel.getByText("校验未通过（1 错误 / 0 警告）")).toBeVisible();
  await expect(panel.getByText(/未配置任何分支条件/)).toBeVisible();
  await expect(
    panel.locator(".wfp-validation-panel__location"),
  ).toHaveText(/^节点 /);

  // 6. 负向断言：面板已渲染（校验分支已返回）且未发起实例启动
  await page.waitForTimeout(300);
  expect(instancePosted).toBe(false);

  // 7. 定位到画布：control_flow 节点（画布第 2 个）进入选中态
  await panel.getByRole("button", { name: "定位到画布" }).click();
  await expect(canvasNodes(page).nth(1)).toHaveClass(/selected/);

  // 8. 关闭校验面板
  await panel.getByRole("button", { name: "关闭校验面板" }).click();
  await expect(panel).toBeHidden();
});
