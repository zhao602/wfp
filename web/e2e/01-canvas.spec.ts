// 路径①：画布编辑→保存→执行→结果（design D3）。
// 保存=localStorage 持久化（重载还原断言）；结果=执行状态浮层计数（spec §2①）。

import { test, expect } from "@playwright/test";
import {
  canvasEdges,
  canvasNodes,
  connectNodes,
  dropNodeType,
  expectOverlayStatus,
  resetCanvas,
} from "./helpers/canvas";

test.beforeEach(async ({ page }) => {
  // 节点删除等 confirm/alert 自动接受（App.tsx:597-605 口径；本路径不触发，防御性挂载）
  page.on("dialog", (dialog) => dialog.accept());
  await resetCanvas(page);
});

test("画布拖入两节点→连线→运行→浮层终态→重载还原", async ({ page }) => {
  // 1. 空画布起点
  await expect(canvasNodes(page)).toHaveCount(0);

  // 2. DnD：触发器 → 输出展示（最小可跑链：manual trigger + 无引用展示节点）
  await dropNodeType(page, "trigger", 350, 250);
  await dropNodeType(page, "output_display", 600, 350);
  await expect(canvasNodes(page)).toHaveCount(2);

  // 3. 连线 0→1
  await connectNodes(page, 0, 1);
  await expect(canvasEdges(page)).toHaveCount(1);

  // 4. 运行：断言 POST /api/v1/instances 成功（App.tsx:557-584 口径）
  const [response] = await Promise.all([
    page.waitForResponse(
      (r) => r.url().includes("/api/v1/instances") && r.request().method() === "POST",
    ),
    page.getByRole("button", { name: "运行", exact: true }).click(),
  ]);
  expect(response.ok()).toBeTruthy();

  // 5. 结果：执行状态浮层至两节点成功（SSE 事件流驱动，非节点着色口径）
  await expectOverlayStatus(page, "成功", 2);

  // 6. 保存=持久化：localStorage debounce 300ms → 重载还原节点与连线
  await page.waitForTimeout(500);
  await page.reload();
  await expect(canvasNodes(page)).toHaveCount(2);
  await expect(canvasEdges(page)).toHaveCount(1);
});
