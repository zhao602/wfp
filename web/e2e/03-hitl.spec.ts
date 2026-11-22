// 路径③：HITL 审批——混合口径（design D5，用户裁决 2026-11-22）。
// UI：画布构建 触发器→控制流(人工审核)→输出展示 → 运行挂起；
// API：轮询 waiting_review → POST /instances/{id}/review（approve）；
// UI：浮层断言恢复至终态。审批面板 UI 不存在（spec §5 登记遗留）。

import { test, expect } from "@playwright/test";
import { submitReview, waitForInstanceStatus } from "./helpers/backend";
import {
  canvasEdges,
  canvasNodes,
  connectNodes,
  dropNodeType,
  expectOverlayStatus,
  resetCanvas,
} from "./helpers/canvas";

test.beforeEach(async ({ page }) => {
  page.on("dialog", (dialog) => dialog.accept());
  await resetCanvas(page);
});

test("控制流(人工审核)链→运行挂起→API 审批→浮层终态", async ({ page, request }) => {
  // 1. DnD 三节点：触发器 → 控制流 → 输出展示
  await dropNodeType(page, "trigger", 300, 220);
  await dropNodeType(page, "control_flow", 520, 320);
  await dropNodeType(page, "output_display", 740, 420);
  await expect(canvasNodes(page)).toHaveCount(3);

  // 2. 选中控制流节点 → 配置面板「控制流子类型」切为「人工审核」
  //    （panels/control_flow/index.tsx：human_review_gate 选项）
  await canvasNodes(page).nth(1).click();
  const kindSelect = page.locator("select.wfp-ncp-select").first();
  await kindSelect.waitFor({ state: "visible" });
  await kindSelect.selectOption("human_review_gate");

  // 3. 连线 0→1→2
  await connectNodes(page, 0, 1);
  await connectNodes(page, 1, 2);
  await expect(canvasEdges(page)).toHaveCount(2);

  // 4. 运行：捕获 instance_id（POST /api/v1/instances 响应体）
  const [response] = await Promise.all([
    page.waitForResponse(
      (r) => r.url().includes("/api/v1/instances") && r.request().method() === "POST",
    ),
    page.getByRole("button", { name: "运行", exact: true }).click(),
  ]);
  expect(response.ok()).toBeTruthy();
  const instance = (await response.json()) as { instance_id: string };
  expect(instance.instance_id).toBeTruthy();

  // 5. 混合步：轮询至 waiting_review 挂起态（domain/models/enums.py 口径）
  await waitForInstanceStatus(request, instance.instance_id, ["waiting_review"]);

  // 6. 混合步：API 提交审批决策 approve（ReviewBody：decision/actor）
  await submitReview(request, instance.instance_id, "approve");

  // 7. 终态断言：实例 succeeded + 浮层三节点成功
  await waitForInstanceStatus(request, instance.instance_id, ["succeeded"]);
  await expectOverlayStatus(page, "成功", 3);
});
