// =============================================================================
// 节点配置面板删除按钮回归测试（canvas/__tests__/NodeConfigurationPanel.delete.test.tsx）
//
// 留待非沙箱环境执行：npx vitest run src/canvas/__tests__/NodeConfigurationPanel.delete.test.tsx
//
// 覆盖：
//   N-01 有选中节点且注入 onDeleteNode 时渲染删除按钮（FR-001/FR-012/AC-001/AC-018）
//   N-02 点击按钮以 selectedNode.node_id 调用一次（FR-012/DC-9）
//   N-03 未注入 onDeleteNode 时不渲染按钮（NFR-012/DC-9/INV-8）
//   N-04 节点数 ≤ 1 时按钮 disabled 且带 title 说明（FR-006/AC-011/AC-012/DC-5）
//   N-05 节点数 ≥ 2 时按钮可点击（AC-012/AC-022）
//   N-06 <style> 注入块含 wfp-ncp-btn 基态与 --danger 危险态浅色规则（NFR-011/INV-9）
//   N-07 <style> 注入块含 .wfp-theme--dark 深色作用域覆盖（NFR-011/INV-9）
//   N-08 无选中节点时不渲染删除按钮（仅提示）
// =============================================================================

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import NodeConfigurationPanel from "../NodeConfigurationPanel";
import type { CanvasNodeData, WorkflowCanvasState, CanvasNode } from "../canvasTypes";
import type { TriggerNodeConfiguration } from "../../workflow-definition/definitionTypes";

let mockTheme: "light" | "dark" = "light";
vi.mock("../../theme/themeMode", async () => {
  const actual = await vi.importActual<typeof import("../../theme/themeMode")>(
    "../../theme/themeMode",
  );
  return { ...actual, useThemeMode: () => mockTheme };
});

/** 构造 trigger 节点数据 */
function makeTriggerData(id: string, name: string): CanvasNodeData {
  const configuration: TriggerNodeConfiguration = {
    node_type: "trigger",
    trigger_type: "manual",
    input_template: {},
  };
  return { node_id: id, name, node_type: "trigger", configuration };
}

/** 构造画布节点 */
function makeNode(id: string, name: string): CanvasNode {
  return {
    id,
    type: "trigger",
    position: { x: 0, y: 0 },
    data: makeTriggerData(id, name),
  };
}

/** 渲染面板 */
function renderPanel(nodeCount: number, withCallback: boolean) {
  const nodes: CanvasNode[] = [];
  for (let i = 0; i < nodeCount; i += 1) {
    nodes.push(makeNode(`node-${i}`, `节点${i}`));
  }
  const canvasState: WorkflowCanvasState = { nodes, edges: [] };
  const onDeleteNode = vi.fn();
  const { container } = render(
    <NodeConfigurationPanel
      selectedNode={nodes[0]?.data ?? null}
      canvasState={canvasState}
      onCanvasStateChange={vi.fn()}
      onDeleteNode={withCallback ? onDeleteNode : undefined}
    />,
  );
  return { onDeleteNode, container };
}

describe("NodeConfigurationPanel 删除按钮", () => {
  beforeEach(() => {
    mockTheme = "light";
  });

  it("N-01 有选中节点且注入回调时渲染删除按钮", () => {
    renderPanel(2, true);
    const btn = screen.getByRole("button", { name: "删除节点" });
    expect(btn).toBeTruthy();
  });

  it("N-02 点击按钮以 node_id 调用一次", () => {
    const { onDeleteNode } = renderPanel(2, true);
    fireEvent.click(screen.getByRole("button", { name: "删除节点" }));
    expect(onDeleteNode).toHaveBeenCalledTimes(1);
    expect(onDeleteNode).toHaveBeenCalledWith("node-0");
  });

  it("N-03 未注入回调时不渲染按钮", () => {
    renderPanel(2, false);
    expect(screen.queryByRole("button", { name: "删除节点" })).toBeNull();
  });

  it("N-04 节点数 ≤ 1 时按钮禁用且带 title", () => {
    renderPanel(1, true);
    const btn = screen.getByRole("button", { name: "删除节点" }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(btn.getAttribute("title")).toBe("至少保留一个节点");
  });

  it("N-05 节点数 ≥ 2 时按钮可点击", () => {
    renderPanel(2, true);
    const btn = screen.getByRole("button", { name: "删除节点" }) as HTMLButtonElement;
    expect(btn.disabled).toBe(false);
  });

  it("N-06 <style> 含 wfp-ncp-btn 基态与危险态浅色规则", () => {
    const { container } = renderPanel(2, true);
    const css = container.querySelector("style")?.textContent ?? "";
    expect(css).toContain(".wfp-ncp-btn {");
    expect(css).toContain(".wfp-ncp-btn--danger");
    expect(css).toContain("#d32f2f");
    expect(css).not.toContain("@keyframes");
    expect(css).not.toContain("var(--");
  });

  it("N-07 <style> 含 .wfp-theme--dark 深色作用域覆盖", () => {
    const { container } = renderPanel(2, true);
    const css = container.querySelector("style")?.textContent ?? "";
    expect(css).toContain(".wfp-theme--dark .wfp-ncp-btn--danger");
    expect(css).toContain(".wfp-theme--dark .wfp-ncp-btn:disabled");
  });

  it("N-08 无选中节点时不渲染删除按钮", () => {
    const { onDeleteNode } = renderPanel(0, true);
    expect(onDeleteNode).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "删除节点" })).toBeNull();
    expect(screen.getByText("请选择一个节点以编辑配置")).toBeTruthy();
  });
});