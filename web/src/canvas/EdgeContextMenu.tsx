// =============================================================================
// 边右键菜单组件（src/canvas/EdgeContextMenu.tsx）
//
// 职责：画布连线右键菜单（本期仅「删除连线」一项，FR-004/AC-007/AC-008）。
// 设计意图（对应设计 2.2.2 接口 I3、决策 DC-03、任务 T10）：
//   1. createPortal 渲染至 document.body，避免被画布容器 overflow 裁剪（风险 R3）；
//   2. 关闭时机：外部 pointerdown / Escape / wheel（画布滚动缩放）/ 执行删除后（DC-03 决策 3/4）；
//   3. 点击菜单项自身不误判为外部点击；document 级监听随卸载移除（防泄漏）；
//   4. 内联 style + 组件内 <style> 注入块、wfp- 前缀、禁 CSS 变量/@keyframes/外部 CSS（NFR-013）；
//   5. 深色经 useThemeMode() 上下文判定：portal 挂载于 document.body，
//      脱离 .wfp-theme--dark 根作用域，禁依赖祖先类兜底（设计 I3「深色经 useThemeMode() 判定」）。
// =============================================================================

import { useCallback, useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { useThemeMode, darkTokens } from "../theme/themeMode";

/** 边右键菜单 props（设计 2.2.2 接口 I3） */
export interface EdgeContextMenuProps {
  /** 视口坐标（客户端坐标，来自 onEdgeContextMenu 事件的 clientX/clientY） */
  x: number;
  y: number;
  /** 目标连线标识（右键命中的该条边） */
  edgeId: string;
  /** 关闭菜单（外部点击 / Escape / 滚动 / 执行后） */
  onClose: () => void;
  /** 执行删除 */
  onDelete: (edgeId: string) => void;
}

/**
 * 边右键菜单（受控组件，无内部开关状态：开/关由父层 contextMenu 驱动）。
 * 删除经 onDelete 上抛、由父层编排（与键盘删除共用 onDeleteEdge；可撤销，不弹 confirm，AC-023）。
 */
export default function EdgeContextMenu({ x, y, edgeId, onClose, onDelete }: EdgeContextMenuProps) {
  const themeMode = useThemeMode();
  const isDark = themeMode === "dark";
  const menuRef = useRef<HTMLDivElement | null>(null);

  /** 点击「删除连线」：上抛删除意图后立即关闭（DC-03 决策 4） */
  const handleDeleteClick = useCallback(() => {
    onDelete(edgeId);
    onClose();
  }, [edgeId, onDelete, onClose]);

  // 深浅两套配色：模板内嵌 darkTokens 取值，无 CSS 变量（NFR-013；深色由主题上下文判定）
  const menuCss = `
.wfp-edge-menu {
  min-width: 120px;
  padding: 4px 0;
  border-radius: 6px;
  background: ${isDark ? darkTokens.surface.bg : "#ffffff"};
  border: 1px solid ${isDark ? darkTokens.surface.border : "#d9d9d9"};
  box-shadow: 0 4px 12px ${isDark ? "rgba(0, 0, 0, 0.5)" : "rgba(0, 0, 0, 0.15)"};
  color: ${isDark ? darkTokens.text.primary : "#1f1f1f"};
  font-size: 13px;
  line-height: 20px;
}
.wfp-edge-menu-item {
  display: block;
  width: 100%;
  padding: 6px 12px;
  background: none;
  border: none;
  text-align: left;
  cursor: pointer;
  font: inherit;
  color: inherit;
}
.wfp-edge-menu-item:hover,
.wfp-edge-menu-item:focus-visible {
  background: ${isDark ? darkTokens.surface.bgHover : "#f0f0f0"};
  outline: none;
}
`;

  useEffect(() => {
    /** 外部 pointerdown：点在菜单外才关闭；菜单项点击走自身 onClick，不误判（DC-03 决策 3） */
    const handlePointerDown = (event: PointerEvent) => {
      const menu = menuRef.current;
      if (menu !== null && event.target instanceof Node && menu.contains(event.target)) {
        return;
      }
      onClose();
    };
    /** Escape 关闭（键盘可达性，DC-03 决策 3） */
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    /** 画布滚动/缩放（wheel）即关闭：菜单为视口定位，画布变换后原位置失真（DC-03 决策 3） */
    const handleWheel = () => {
      onClose();
    };
    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    document.addEventListener("wheel", handleWheel, { passive: true });
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
      document.removeEventListener("wheel", handleWheel);
    };
  }, [onClose]);

  return createPortal(
    <div
      ref={menuRef}
      className="wfp-edge-menu"
      data-edge-id={edgeId}
      style={{
        position: "fixed",
        left: `${x}px`,
        top: `${y}px`,
        zIndex: 1001,
      }}
    >
      <style>{menuCss}</style>
      <button type="button" className="wfp-edge-menu-item" onClick={handleDeleteClick}>
        删除连线
      </button>
    </div>,
    document.body,
  );
}
