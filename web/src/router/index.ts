// =============================================================================
// router 模块出口（src/router/index.ts，B2-C1/DC-07）
// =============================================================================

export {
  parseHash,
  routeToHash,
  navigate,
  DEFAULT_ADMIN_PANEL,
  type AppRoute,
  type ParsedRoute,
} from "./hashRouter";
export { useHashRoute } from "./useHashRoute";
export { RouteLink, type RouteLinkProps } from "./RouteLink";
