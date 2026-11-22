// =============================================================================
// useHashRoute hook（src/router/useHashRoute.ts，B2-C1/DC-07）
//
// 职责：订阅 hashchange 返回当前解析路由；按 hash 去重（程序化导航的同步
// 派发与浏览器原生异步派发同 hash 不产生双渲染）；卸载时清理监听（无泄漏）。
// =============================================================================

import { useCallback, useEffect, useRef, useState } from "react";
import { parseHash, type ParsedRoute } from "./hashRouter";

export function useHashRoute(): ParsedRoute {
  const [route, setRoute] = useState<ParsedRoute>(() =>
    parseHash(window.location.hash),
  );
  const lastHashRef = useRef<string>(window.location.hash);

  const syncFromLocation = useCallback((): void => {
    const hash = window.location.hash;
    if (hash !== lastHashRef.current) {
      lastHashRef.current = hash;
      setRoute(parseHash(hash));
    }
  }, []);

  useEffect(() => {
    window.addEventListener("hashchange", syncFromLocation);
    return () => window.removeEventListener("hashchange", syncFromLocation);
  }, [syncFromLocation]);

  return route;
}
