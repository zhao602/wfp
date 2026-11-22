// 路径②：范例加载（design D4）——范例库→载入 sequential_pipeline→画布渲染。
// 范例名经 GET /api/v1/examples 动态解析（按钮文案=example.name，防漂移）。

import { test, expect } from "@playwright/test";
import { fetchExamples } from "./helpers/backend";
import { canvasNodes, resetCanvas } from "./helpers/canvas";

test.beforeEach(async ({ page }) => {
  await resetCanvas(page);
});

test("范例库展开→载入 sequential_pipeline→画布渲染多节点", async ({ page, request }) => {
  // 0. 动态解析范例显示名（按钮文案与 example_id 解耦）
  const examples = await fetchExamples(request);
  const entry = examples.find((e) => e.example_id === "sequential_pipeline");
  expect(entry, "sequential_pipeline 应在范例列表中").toBeDefined();
  expect(entry!.available, "sequential_pipeline 在 e2e 后端应可用（.env 依赖齐备）").toBeTruthy();

  // 1. 展开范例库（默认收起，ExampleLibraryPanel 折叠钮）
  await page.getByRole("button", { name: /范例库/ }).click();

  // 2. 载入 sequential_pipeline（handleLoad → GET /examples/{id} → 导入画布）
  const [loadResponse] = await Promise.all([
    page.waitForResponse(
      (r) => r.url().includes(`/api/v1/examples/sequential_pipeline`) && r.request().method() === "GET",
    ),
    page.getByRole("button", { name: entry!.name, exact: true }).click(),
  ]);
  expect(loadResponse.ok()).toBeTruthy();

  // 3. 画布渲染断言：多节点工作流（>1 节点即通过——节点数随范例演进免脆断）
  await expect(async () => {
    const count = await canvasNodes(page).count();
    expect(count).toBeGreaterThan(1);
  }).toPass();
});
