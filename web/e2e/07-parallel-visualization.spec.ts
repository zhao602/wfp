// 路径⑦：并行分支泳道可视化（B13 U2/U3 e2e）。
// 链路 = 「导入 YAML」原生文件选择导入并行夹具（fixtures/parallel-visualization.yaml：
//   trigger 双 display 分支 + parallel_join，probe 预验证 valid=true——仅 trigger 双出边
//   WARNING 不阻断；并行区域编译期激活，PARALLEL_BRANCH_STARTED/COMPLETED×2 事件序见 T2）
//   → 运行 → SSE 泳道事件驱动：泳道标题 / 分支行（display_a、display_b 成功）/ 已收敛
//   / 节点计数成功: 4。

import path from "node:path";
import { fileURLToPath } from "node:url";
import { test, expect } from "@playwright/test";
import {
  canvasEdges,
  canvasNodes,
  expectOverlayStatus,
  resetCanvas,
} from "./helpers/canvas";

// ESM 无 __dirname：import.meta.url 推导夹具绝对路径
const FIXTURE = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  "fixtures",
  "parallel-visualization.yaml",
);

test.beforeEach(async ({ page }) => {
  page.on("dialog", (dialog) => dialog.accept());
  await resetCanvas(page);
});

test("导入并行 YAML→运行→泳道分支终态与节点计数", async ({ page }) => {
  // 1. 文件选择导入（YamlFileImportButton 隐藏 input → 原生 filechooser）
  const chooserPromise = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "导入 YAML" }).click();
  const chooser = await chooserPromise;
  await chooser.setFiles(FIXTURE);

  // 2. 画布还原：4 节点 / 4 边（importer 保留 node_id 与连线）
  await expect(canvasNodes(page)).toHaveCount(4);
  await expect(canvasEdges(page)).toHaveCount(4);

  // 3. 运行：前置校验放行（valid=true，仅 WARNING）→ 实例启动成功
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
  expect(report.valid).toBe(true);

  const instanceResponse = await page.waitForResponse(
    (r) => r.url().includes("/api/v1/instances") && r.request().method() === "POST",
  );
  expect(instanceResponse.ok()).toBeTruthy();

  // 4. 泳道终态：标题（joinName 源自导入节点名「并行汇聚」）+ 双分支行成功 + 已收敛
  await expect(page.locator(".wfp-overlay-parallel")).toBeVisible({
    timeout: 20_000,
  });
  await expect(page.locator(".wfp-overlay-parallel__title")).toHaveText(
    "并行汇聚 · 并行分支",
  );
  await expect(page.locator(".wfp-overlay-parallel__branch")).toHaveCount(2);
  await expect(page.locator(".wfp-overlay-parallel__branch")).toContainText([
    "display_a",
    "display_b",
  ]);
  await expect(page.locator(".wfp-overlay-parallel__status")).toContainText([
    "成功",
    "成功",
  ]);
  await expect(page.locator(".wfp-overlay-parallel__reason")).toHaveText(
    "已收敛",
  );

  // 5. 节点计数：trigger + 双分支 display + join 全部成功
  await expectOverlayStatus(page, "成功", 4);
});
