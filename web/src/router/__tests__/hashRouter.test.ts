// =============================================================================
// hash 路由纯函数单测（src/router/__tests__/hashRouter.test.ts，B2-C1）
//
// 覆盖：默认路由（存量 URL 零破坏）/ 管理面板深链 / 404 兜底 /
// validPanelIds 注入（测试口径）/ routeToHash 生成与往返。
// =============================================================================

import { describe, it, expect } from "vitest";
import {
  parseHash,
  routeToHash,
  DEFAULT_ADMIN_PANEL,
} from "../hashRouter";
import { MENU_ITEMS } from "../../admin/menuConfig";

describe("hashRouter 纯函数", () => {
  describe("parseHash 默认路由（存量 URL 零破坏，INV-B2-01）", () => {
    it.each(["", "#", "#/"])("空/hash 根 → canvas（%s）", (hash) => {
      expect(parseHash(hash)).toEqual({ view: "canvas" });
    });
  });

  describe("parseHash 管理面板深链", () => {
    it("合法 panel id → admin 深链", () => {
      expect(parseHash("#/admin/agent_market")).toEqual({
        view: "admin",
        panelId: "agent_market",
      });
    });

    it("裸 #/admin 与 #/admin/ → 默认面板（MENU_ITEMS 首项）", () => {
      expect(DEFAULT_ADMIN_PANEL).toBe("governance");
      expect(parseHash("#/admin")).toEqual({
        view: "admin",
        panelId: DEFAULT_ADMIN_PANEL,
      });
      expect(parseHash("#/admin/")).toEqual({
        view: "admin",
        panelId: DEFAULT_ADMIN_PANEL,
      });
    });

    it("MENU_ITEMS 全部 20 项 id 均可深链解析（防漂移）", () => {
      expect(MENU_ITEMS).toHaveLength(20);
      for (const item of MENU_ITEMS) {
        expect(parseHash(`#/admin/${item.key}`)).toEqual({
          view: "admin",
          panelId: item.key,
        });
      }
    });
  });

  describe("parseHash 404 兜底", () => {
    it("未知 admin panel id → not-found（segment 保留原始段）", () => {
      expect(parseHash("#/admin/nonexistent")).toEqual({
        view: "not-found",
        segment: "nonexistent",
      });
    });

    it("未知顶层段 → not-found", () => {
      expect(parseHash("#/foo")).toEqual({
        view: "not-found",
        segment: "foo",
      });
    });

    it("多段路径 → not-found（segment 保留原始段）", () => {
      expect(parseHash("#/admin/a/b")).toEqual({
        view: "not-found",
        segment: "admin/a/b",
      });
    });
  });

  describe("parseHash validPanelIds 注入（测试口径）", () => {
    it("注入集合内 id 命中 admin；集合外 id 落入 not-found", () => {
      const custom = new Set(["custom_panel"]);
      expect(parseHash("#/admin/custom_panel", custom)).toEqual({
        view: "admin",
        panelId: "custom_panel",
      });
      expect(parseHash("#/admin/agent_market", custom)).toEqual({
        view: "not-found",
        segment: "agent_market",
      });
    });
  });

  describe("routeToHash 生成与往返", () => {
    it("canvas → #/", () => {
      expect(routeToHash({ view: "canvas" })).toBe("#/");
    });

    it("admin → #/admin/<panelId>，与 parseHash 往返一致", () => {
      const hash = routeToHash({ view: "admin", panelId: "tool_market" });
      expect(hash).toBe("#/admin/tool_market");
      expect(parseHash(hash)).toEqual({
        view: "admin",
        panelId: "tool_market",
      });
    });
  });
});
