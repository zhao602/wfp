// =============================================================================
// 路由 DOM 侧行为单测（src/router/__tests__/router.dom.test.tsx，B2-C1）
//
// 覆盖：navigate hash 写入 / useHashRoute 初始解析与 hashchange 重订阅与
// 卸载清理 / RouteLink 语义化锚点渲染与原生 hash 跳转。
// =============================================================================

import { describe, it, expect, afterEach } from "vitest";
import {
  render,
  renderHook,
  fireEvent,
  act,
  waitFor,
  cleanup,
} from "@testing-library/react";
import { navigate } from "../hashRouter";
import { useHashRoute } from "../useHashRoute";
import { RouteLink } from "../RouteLink";

afterEach(() => {
  cleanup();
  window.location.hash = "";
});

describe("navigate", () => {
  it("同步写入 location.hash", () => {
    window.location.hash = "";
    navigate({ view: "admin", panelId: "knowledge_base" });
    expect(window.location.hash).toBe("#/admin/knowledge_base");
  });

  it("canvas 路由写入 hash 根", () => {
    window.location.hash = "#/admin/sso";
    navigate({ view: "canvas" });
    expect(window.location.hash).toBe("#/");
  });
});

describe("useHashRoute hook", () => {
  it("初始 hash → 初始路由", () => {
    window.location.hash = "#/admin/agent_market";
    const { result } = renderHook(() => useHashRoute());
    expect(result.current).toEqual({
      view: "admin",
      panelId: "agent_market",
    });
  });

  it("hashchange → 路由重解析", async () => {
    window.location.hash = "";
    const { result } = renderHook(() => useHashRoute());
    expect(result.current).toEqual({ view: "canvas" });
    navigate({ view: "admin", panelId: "sso" });
    await waitFor(() =>
      expect(result.current).toEqual({ view: "admin", panelId: "sso" }),
    );
  });

  it("卸载后移除监听（hashchange 不再触发状态更新）", async () => {
    window.location.hash = "#/admin/sso";
    const { result, unmount } = renderHook(() => useHashRoute());
    expect(result.current).toEqual({ view: "admin", panelId: "sso" });
    unmount();
    navigate({ view: "admin", panelId: "governance" });
    await waitFor(() =>
      expect(window.location.hash).toBe("#/admin/governance"),
    );
    // 卸载后路由冻结为卸载前值（无泄漏）
    expect(result.current).toEqual({ view: "admin", panelId: "sso" });
  });

  it("程序化导航同步生效，同 hash 重复派发 hashchange 去重（无二次渲染）", () => {
    window.location.hash = "";
    let renders = 0;
    const { result } = renderHook(() => {
      renders += 1;
      return useHashRoute();
    });
    expect(result.current).toEqual({ view: "canvas" });
    const before = renders;
    act(() => {
      navigate({ view: "admin", panelId: "sso" });
    });
    // 程序化导航同步派发 → 立即重解析（旧 setView 同步语义保持）
    expect(result.current).toEqual({ view: "admin", panelId: "sso" });
    expect(renders).toBeGreaterThan(before);
    const afterNavigate = renders;
    // 模拟浏览器/jsdom 原生异步 hashchange（同 hash）→ 去重，无额外渲染
    act(() => {
      window.dispatchEvent(new Event("hashchange"));
    });
    expect(result.current).toEqual({ view: "admin", panelId: "sso" });
    expect(renders).toBe(afterNavigate);
  });
});

describe("RouteLink", () => {
  it("渲染语义化锚点（href 由路由生成）", () => {
    const { getByRole } = render(
      <RouteLink to={{ view: "admin", panelId: "agent_market" }}>
        Agent 市场
      </RouteLink>,
    );
    const anchor = getByRole("link", { name: "Agent 市场" });
    expect(anchor).toHaveAttribute("href", "#/admin/agent_market");
  });

  it("透传 className/title/data-testid", () => {
    const { getByTestId } = render(
      <RouteLink
        to={{ view: "canvas" }}
        className="back-link"
        title="返回画布"
        data-testid="back-link"
      >
        返回
      </RouteLink>,
    );
    const anchor = getByTestId("back-link");
    expect(anchor).toHaveClass("back-link");
    expect(anchor).toHaveAttribute("title", "返回画布");
    expect(anchor).toHaveAttribute("href", "#/");
  });

  it("点击触发原生 hash 跳转", async () => {
    window.location.hash = "";
    const { getByRole } = render(
      <RouteLink to={{ view: "admin", panelId: "tool_market" }}>
        Tool 市场
      </RouteLink>,
    );
    fireEvent.click(getByRole("link", { name: "Tool 市场" }));
    await waitFor(() =>
      expect(window.location.hash).toBe("#/admin/tool_market"),
    );
  });
});
