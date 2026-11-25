// =============================================================================
// OEM 构建期菜单过滤单元测试（src/admin/__tests__/menuConfig.oem.test.ts，roadmap B5）
//
// 覆盖（验收②前端侧数据源：禁用面板菜单不渲染 + 深链 not-found）：
//   1. 默认无 env：MENU_ITEMS = 20 项（B16 后全量，hashRouter :44 断言兼容）
//   2. ALL_MENU_ITEMS 恒 20 项（全量基准不受 env 影响）
//   3. stubEnv 禁 3 面板 → MENU_ITEMS 17 项且三市场 key 排除
//   4. 非法 JSON → fail-soft：console.warn 且 MENU_ITEMS 20 项全显
//   5. 禁用全部 20 项 → 回落全量（DEFAULT_ADMIN_PANEL 越界保护）
//   6. parseDisabledPanels 单元：空白/非字符串项/非数组
// =============================================================================

import { describe, it, expect, vi, afterEach } from "vitest";
import { ALL_MENU_ITEMS, MENU_ITEMS, parseDisabledPanels } from "../menuConfig";

describe("OEM 构建期菜单过滤（menuConfig B5）", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
  });

  it("默认无 env：MENU_ITEMS = 20 项且内容等于 ALL_MENU_ITEMS（B16 后全量）", () => {
    expect(MENU_ITEMS).toHaveLength(20);
    expect(MENU_ITEMS).toEqual(ALL_MENU_ITEMS);
  });

  it("ALL_MENU_ITEMS 恒 20 项（全量基准不受禁用 env 影响）", async () => {
    vi.stubEnv("VITE_OEM_DISABLED_PANELS", '["agent_market","tool_market","knowledge_base"]');
    vi.resetModules();
    const mod = await import("../menuConfig");
    expect(mod.ALL_MENU_ITEMS).toHaveLength(20);
  });

  it("stubEnv 禁 3 面板 → MENU_ITEMS 17 项且三市场 key 排除（菜单不渲染数据源）", async () => {
    vi.stubEnv("VITE_OEM_DISABLED_PANELS", '["agent_market","tool_market","knowledge_base"]');
    vi.resetModules();
    const mod = await import("../menuConfig");
    expect(mod.MENU_ITEMS).toHaveLength(17);
    const keys = mod.MENU_ITEMS.map((item) => item.key);
    expect(keys).not.toContain("agent_market");
    expect(keys).not.toContain("tool_market");
    expect(keys).not.toContain("knowledge_base");
    // 未禁用面板保留
    expect(keys).toContain("governance");
    expect(keys).toContain("sso");
  });

  it("非法 JSON → fail-soft：console.warn 且 MENU_ITEMS 20 项全显", async () => {
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    vi.stubEnv("VITE_OEM_DISABLED_PANELS", "not-a-json[");
    vi.resetModules();
    const mod = await import("../menuConfig");
    expect(mod.MENU_ITEMS).toHaveLength(20);
    expect(warnSpy).toHaveBeenCalled();
  });

  it("禁用全部 20 项 → 回落全量（DEFAULT_ADMIN_PANEL 越界保护）", async () => {
    const allKeys = ALL_MENU_ITEMS.map((item) => item.key);
    vi.stubEnv("VITE_OEM_DISABLED_PANELS", JSON.stringify(allKeys));
    vi.resetModules();
    const mod = await import("../menuConfig");
    expect(mod.MENU_ITEMS).toHaveLength(20);
  });

  it("parseDisabledPanels 单元：空白 → 空集；非字符串项剔除；非数组 JSON → 空集", () => {
    expect(parseDisabledPanels(undefined).size).toBe(0);
    expect(parseDisabledPanels("   ").size).toBe(0);
    expect(parseDisabledPanels('["a", 42, null, "b"]').size).toBe(2);
    expect(parseDisabledPanels('{"not":"array"}').size).toBe(0);
  });
});
