// =============================================================================
// 边右键菜单测试（canvas/__tests__/EdgeContextMenu.test.tsx）
//
// 留待非沙箱环境执行：cd web && npx vitest run
//
// 覆盖用例（add_edge_delete / CHG-E15，ECM-01~03，对应任务 T18.1~T18.3）：
//   ECM-01 渲染「删除连线」项 + createPortal 挂载 document.body + fixed 视口定位
//         （FR-004；AC-007；DC-03 决策 1/2；INV-D03）
//   ECM-02 点击菜单项 → onDelete 以正确 edgeId 被调一次 + onClose
//         （FR-004/FR-005；AC-008；INV-D05；DC-03 决策 1/4）
//   ECM-03 关闭时机：外部 pointerdown / wheel 滚动 / Escape → onClose；
//         菜单内 pointerdown 不误判为外部点击
//         （DC-03 决策 3；异常场景 3；INV-D03）
//
// 测试策略：真实渲染 EdgeContextMenu（createPortal 至 document.body，经 screen 查询）；
// 仅以 fireEvent 驱动 document 级监听，不改被测组件逻辑。
//
// 沙箱限制声明：本文件作为交付物入库，请在非沙箱环境执行。
// =============================================================================

import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import EdgeContextMenu from "../EdgeContextMenu";

/** 渲染受控菜单并返回回调间谍 */
function renderMenu() {
  const onClose = vi.fn();
  const onDelete = vi.fn();
  const utils = render(
    <EdgeContextMenu x={120} y={80} edgeId="e1" onClose={onClose} onDelete={onDelete} />,
  );
  return { onClose, onDelete, ...utils };
}

/** 取 portal 挂载的菜单根节点（.wfp-edge-menu） */
function getMenuRoot(): HTMLElement {
  const item = screen.getByText("删除连线");
  const menu = item.closest(".wfp-edge-menu");
  if (!(menu instanceof HTMLElement)) {
    throw new Error("未找到 .wfp-edge-menu 菜单根节点");
  }
  return menu;
}

describe("EdgeContextMenu 边右键菜单", () => {
  // ECM-01（T18.1）：菜单渲染、portal 挂载点、视口定位与层级。
  it("ECM-01 渲染「删除连线」项并经 createPortal 挂载至 document.body（AC-007）", () => {
    const { container } = renderMenu();
    expect(screen.getByText("删除连线")).toBeTruthy();
    const menu = getMenuRoot();
    // createPortal：挂载于 document.body，而非渲染容器（DC-03 决策 2，规避画布容器裁剪）
    expect(document.body.contains(menu)).toBe(true);
    expect(container.contains(menu)).toBe(false);
    // 视口定位与层级：position:fixed、left/top 来自 clientX/clientY、z-index > 1000
    expect(menu.style.position).toBe("fixed");
    expect(menu.style.left).toBe("120px");
    expect(menu.style.top).toBe("80px");
    expect(Number.parseInt(menu.style.zIndex, 10)).toBeGreaterThan(1000);
    expect(menu.dataset.edgeId).toBe("e1");
  });

  // ECM-02（T18.2）：点击「删除连线」→ onDelete(edgeId) 被调一次并关闭。
  it("ECM-02 点击「删除连线」→ onDelete 以正确 edgeId 被调一次并关闭（AC-008）", () => {
    const { onDelete, onClose } = renderMenu();
    fireEvent.click(screen.getByText("删除连线"));
    expect(onDelete).toHaveBeenCalledTimes(1);
    expect(onDelete).toHaveBeenCalledWith("e1");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  // ECM-03（T18.3）：外部 pointerdown / wheel / Escape 均触发关闭；菜单内点击不误判。
  it("ECM-03 外部 pointerdown/wheel/Escape → onClose；菜单内 pointerdown 不误判（DC-03 决策 3）", () => {
    const { onClose } = renderMenu();
    // 外部 pointerdown（点击菜单外）→ 关闭
    fireEvent.pointerDown(document.body);
    expect(onClose).toHaveBeenCalledTimes(1);
    // 画布滚动/缩放（wheel）→ 关闭
    fireEvent.wheel(document.body);
    expect(onClose).toHaveBeenCalledTimes(2);
    // Escape → 关闭
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(3);
    // 菜单内 pointerdown：不误判为外部点击 → 不触发关闭
    fireEvent.pointerDown(getMenuRoot());
    expect(onClose).toHaveBeenCalledTimes(3);
  });
});
