// 路径④：事件订阅——D1 缺陷锁定（用户裁决 2026-11-22）。
//
// 缺陷 D1：application_factory 从未调用 set_subscription_manager（全仓零调用），
// /api/v1/subscriptions 六端点在任何运行时（dev/e2e/生产）恒 503
// 「订阅管理器未初始化」。修复需 trigger 回调桥接 + 事件监听器注册
// （无监听器时创建即转 ERROR）+ shutdown 生命周期——产品批量级，已登记
// roadmap 后续批；本测试锁定现状契约（503 ≠ 404：路由已注册、仅管理器未装配）。
//
// 翻转条件：产品批装配管理器后，本文件改回 CRUD 正向断言
// （helpers/backend.ts 的 createSubscription 等 7 个函数即翻转工具箱）。

import { test, expect } from "@playwright/test";

/** 能通过 Pydantic 校验的合法创建请求体（确保 503 来自 _get_manager 而非 422）。 */
const VALID_CREATE_BODY = {
  workflow_definition_id: "e2e-subscription-target",
  workflow_definition_version: 1,
  source_type: "kafka",
  connection_reference: "e2e-conn",
  event_source: "e2e-topic",
};

test("D1 缺陷锁定：订阅管理器未装配 → 全端点 503（修复后翻转本断言）", async ({ request }) => {
  // 创建端点：路由已注册（非 404）+ 管理器未装配（503 + 契约 detail）
  const created = await request.post("/api/v1/subscriptions", { data: VALID_CREATE_BODY });
  expect(created.status()).toBe(503);
  const body = (await created.json()) as { detail: string };
  expect(body.detail).toBe("订阅管理器未初始化");

  // 列表端点：同一 503 契约（覆盖 GET 面）
  const list = await request.get("/api/v1/subscriptions");
  expect(list.status()).toBe(503);
  const listBody = (await list.json()) as { detail: string };
  expect(listBody.detail).toBe("订阅管理器未初始化");
});
