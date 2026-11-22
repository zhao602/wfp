// =============================================================================
// RouteLink 组件（src/router/RouteLink.tsx，B2-C1/DC-07）
//
// 职责：语义化路由链接——<a href="#…">，浏览器原生 hash 跳转，无拦截层。
// =============================================================================

import type { CSSProperties, JSX, ReactNode } from "react";
import { routeToHash, type AppRoute } from "./hashRouter";

export interface RouteLinkProps {
  /** 目标路由（not-found 不可导航，类型上排除） */
  to: AppRoute;
  children: ReactNode;
  className?: string;
  style?: CSSProperties;
  title?: string;
  "data-testid"?: string;
}

export function RouteLink({ to, children, ...rest }: RouteLinkProps): JSX.Element {
  return (
    <a {...rest} href={routeToHash(to)}>
      {children}
    </a>
  );
}
