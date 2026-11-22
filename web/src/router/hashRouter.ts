// =============================================================================
// 零依赖 hash 路由纯函数模块（src/router/hashRouter.ts，B2-C1/DC-07）
//
// 职责：hash 解析/生成纯函数——无 React 依赖、无第三方路由库（GK-06，
// 否决 react-router）。路由表 = 画布默认路由（"" | "#" | "#/"）+
// #/admin/<panel_id>（合法 id 运行时读 menuConfig.MENU_ITEMS，不复制清单——
// 防漂移）+ 404 兜底（not-found）。
// =============================================================================

import { MENU_ITEMS, type MenuKey } from "../admin/menuConfig";

/** 可导航路由（not-found 为派生态，不可导航——类型上排除） */
export type AppRoute =
  | { view: "canvas" }
  | { view: "admin"; panelId: MenuKey };

/** 解析结果（含 404 兜底派生态） */
export type ParsedRoute =
  | AppRoute
  | { view: "not-found"; segment: string };

/** 默认管理面板 = MENU_ITEMS 首项（与 AdminConsole 既有内部状态默认值同源） */
export const DEFAULT_ADMIN_PANEL: MenuKey = MENU_ITEMS[0].key;

/** 合法面板 id 集合（运行时读 MENU_ITEMS，防漂移——DC-B2-01 ②） */
const VALID_PANEL_IDS: ReadonlySet<string> = new Set(
  MENU_ITEMS.map((item) => item.key),
);

/**
 * 解析 location.hash 为路由对象。
 * 规则：空/hash 根 → canvas；裸 #/admin（含尾斜杠）→ 默认面板；
 * #/admin/<合法 id> → admin 深链；#/admin/<未知 id> 与未知顶层段 → not-found。
 * validPanelIds 可注入（测试口径）。
 */
export function parseHash(
  hash: string,
  validPanelIds: ReadonlySet<string> = VALID_PANEL_IDS,
): ParsedRoute {
  const raw = hash.replace(/^#/, "");
  if (raw === "" || raw === "/") {
    return { view: "canvas" };
  }
  const segments = raw.split("/").filter((segment) => segment !== "");
  if (segments.length === 1 && segments[0] === "admin") {
    return { view: "admin", panelId: DEFAULT_ADMIN_PANEL };
  }
  if (segments.length === 2 && segments[0] === "admin") {
    const panelId = segments[1];
    if (validPanelIds.has(panelId)) {
      return { view: "admin", panelId: panelId as MenuKey };
    }
    return { view: "not-found", segment: panelId };
  }
  return { view: "not-found", segment: raw.replace(/^\//, "") };
}

/** 生成路由 hash（AppRoute → "#/..."） */
export function routeToHash(route: AppRoute): string {
  if (route.view === "canvas") {
    return "#/";
  }
  return `#/admin/${route.panelId}`;
}

/** hash 导航（浏览器环境守卫；jsdom 测试环境可用） */
export function navigate(route: AppRoute): void {
  if (typeof window === "undefined") {
    return;
  }
  window.location.hash = routeToHash(route);
  // 程序化导航同步派发 hashchange：浏览器/jsdom 对 location.hash 赋值的原生
  // hashchange 为异步事件兜底（回退/前进/手改 URL/锚点点击）；
  // useHashRoute 内按 hash 去重，同 hash 双派发不产生双渲染。
  window.dispatchEvent(new Event("hashchange"));
}
