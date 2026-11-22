// 路径⑤：OEM 产物差异化（design D7）——build_oem.py 双品牌产物 + 静态服断言：
// default 标题/主色/菜单全显 vs acmeflow 标题/紫色主色/三产品菜单剔除。

import { test, expect, type Page } from "@playwright/test";
import type { ChildProcess } from "node:child_process";
import { ensureOemBundles, serveOemBundle } from "./helpers/oem";

const DEFAULT_URL = "http://127.0.0.1:8931";
const ACME_URL = "http://127.0.0.1:8932";

let defaultServer: ChildProcess | null = null;
let acmeServer: ChildProcess | null = null;

test.setTimeout(300_000); // 首轮可能触发 build_oem.py 全量构建

test.beforeAll(async () => {
  ensureOemBundles();
  defaultServer = await serveOemBundle("default", 8931);
  acmeServer = await serveOemBundle("acmeflow", 8932);
});

test.afterAll(() => {
  defaultServer?.kill();
  acmeServer?.kill();
});

/** 进入管理后台（顶栏「管理后台 →」→ h1「管理后台」）。 */
async function openAdmin(page: Page): Promise<void> {
  await page.getByRole("button", { name: "管理后台 →" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "管理后台" })).toBeVisible();
}

test("default 品牌：标题默认 + 主按钮默认蓝 + 三产品菜单在列", async ({ page }) => {
  await page.goto(DEFAULT_URL);
  await expect(page).toHaveTitle("工作流编排平台");
  // 运行按钮（wfp-footer-btn--primary）浅色态背景 = PRIMARY_COLOR 默认 #1976d2
  const runBtn = page.locator("button.wfp-footer-btn--primary");
  await expect(runBtn).toBeVisible();
  await expect(runBtn).toHaveCSS("background-color", "rgb(25, 118, 210)");
  // 管理后台菜单全显（无 VITE_OEM_DISABLED_PANELS 剔除）
  await openAdmin(page);
  await expect(page.getByText("Agent 市场")).toBeVisible();
  await expect(page.getByText("Tool 市场")).toBeVisible();
  await expect(page.getByText("向量知识库管理")).toBeVisible();
});

test("acmeflow 品牌：标题改写 + 主按钮紫色 + 三产品菜单剔除", async ({ page }) => {
  await page.goto(ACME_URL);
  await expect(page).toHaveTitle("AcmeFlow");
  // 运行按钮浅色态背景 = acmeflow primary #7c3aed
  const runBtn = page.locator("button.wfp-footer-btn--primary");
  await expect(runBtn).toBeVisible();
  await expect(runBtn).toHaveCSS("background-color", "rgb(124, 58, 237)");
  // 管理后台菜单：三产品模块全关（VITE_OEM_DISABLED_PANELS 构建期剔除）
  await openAdmin(page);
  await expect(page.getByText("Agent 市场")).toHaveCount(0);
  await expect(page.getByText("Tool 市场")).toHaveCount(0);
  await expect(page.getByText("向量知识库管理")).toHaveCount(0);
  // 侧边导航未剔空（回落保护 + 剩余菜单可见：治理总览为默认面板）
  await expect(page.getByText("治理总览")).toBeVisible();
});
