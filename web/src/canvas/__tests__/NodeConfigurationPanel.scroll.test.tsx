// =============================================================================
// 节点配置面板独立垂直滚动契约测试（ncp_independent_scroll CHG-06）
// （canvas/__tests__/NodeConfigurationPanel.scroll.test.tsx）
//
// 验证边界声明（强制，design DC-06 / OP-09 三层口径第①层）：
//   jsdom 不计算真实布局、不产生真实滚动——本文件 A1~A7 仅证明滚动容器
//   「类名存在、内联 overflow/overscroll 声明存在、<style> 文本含滚动规则」，
//   不证明滚动条真实出现、滚动真实被隔离。真实滚动行为须由用户在真实
//   浏览器按 M-01~M-10 目检清单核验（沙箱无浏览器，不得声称真机复现）。
//
// 覆盖用例（T7.1，断言 A1~A7）：
//   A1 未选中态根容器 className 含 wfp-ncp-scroll（FR-1.5 两态一致）
//   A2 未选中态根容器内联滚动声明（overflowY=auto / overscrollBehavior=contain / minHeight=0）
//   A3 未选中态 <style> 文本含跨内核滚动规则（::-webkit-scrollbar / scrollbar-width / overscroll-behavior）
//   A4 选中态根容器 className 含 wfp-ncp-scroll
//   A5 选中态根容器内联滚动声明（overflowY=auto / overscrollBehavior=contain / minHeight=0）
//   A6 选中态 <style> 文本含滚动规则且仍含既有 .wfp-ncp-* 片段（追加不替换回归）
//   A7 深色模式下 <style> 含 darkTokens 双轨取色（#4a4a4a / #666666）
// =============================================================================

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import NodeConfigurationPanel from "../NodeConfigurationPanel";
import type { CanvasNodeData, WorkflowCanvasState } from "../canvasTypes";
import type { NodeConfiguration } from "../../workflow-definition/definitionTypes";

// 主题 mock（循 outputContract.test.tsx 既有范式）
let mockTheme: "light" | "dark" = "light";
vi.mock("../../theme/themeMode", async () => {
  const actual = await vi.importActual<typeof import("../../theme/themeMode")>(
    "../../theme/themeMode",
  );
  return { ...actual, useThemeMode: () => mockTheme };
});

// ---------------------------------------------------------------------------
// 测试数据工厂（最小 trigger 配置，字段最少便于聚焦滚动容器契约）
// ---------------------------------------------------------------------------

function makeTriggerNodeData(): CanvasNodeData {
  const configuration: NodeConfiguration = {
    node_type: "trigger",
    trigger_type: "manual",
    input_template: {},
  };
  return {
    node_id: "scroll-test-node",
    name: "滚动测试节点",
    node_type: "trigger",
    configuration,
  };
}

function makeCanvasState(node: CanvasNodeData): WorkflowCanvasState {
  return {
    nodes: [{ id: node.node_id, type: node.node_type, position: { x: 0, y: 0 }, data: node }],
    edges: [],
  };
}

/** 未选中态渲染：返回根容器元素 */
function renderUnselected() {
  const { container } = render(
    <NodeConfigurationPanel
      selectedNode={null}
      canvasState={makeCanvasState(makeTriggerNodeData())}
      onCanvasStateChange={vi.fn()}
    /> as ReactNode,
  );
  // 根容器为 container 的第一个子元素（未选中态直接返回单根 div）
  const root = container.firstElementChild as HTMLElement;
  return { container, root };
}

/** 选中态渲染：返回根容器元素 */
function renderSelected() {
  const node = makeTriggerNodeData();
  const { container } = render(
    <NodeConfigurationPanel
      selectedNode={node}
      canvasState={makeCanvasState(node)}
      onCanvasStateChange={vi.fn()}
    /> as ReactNode,
  );
  const root = container.firstElementChild as HTMLElement;
  return { container, root };
}

// ---------------------------------------------------------------------------
// A1~A7 断言
// ---------------------------------------------------------------------------

describe("NodeConfigurationPanel 独立垂直滚动契约（CHG-06，jsdom 仅断声明不证滚动）", () => {
  beforeEach(() => {
    mockTheme = "light";
  });

  it("A1 未选中态根容器 className 含 wfp-ncp-scroll（两态滚动契约一致化 FR-1.5）", () => {
    const { root } = renderUnselected();
    expect(root.className).toContain("wfp-ncp-scroll");
  });

  it("A2 未选中态根容器内联滚动声明：overflowY=auto / overscrollBehavior=contain / minHeight=0（FR-1.2/2.1/3.2）", () => {
    const { root } = renderUnselected();
    expect(root.style.overflowY).toBe("auto");
    expect(root.style.overscrollBehavior).toBe("contain");
    expect(root.style.minHeight).toBe("0");
  });

  it("A3 未选中态 <style> 文本含跨内核滚动规则与滚动链隔离（FR-2.1/4.1/4.2）", () => {
    const { container } = renderUnselected();
    const css = container.querySelector("style")?.textContent ?? "";
    expect(css).toContain("::-webkit-scrollbar");
    expect(css).toContain("scrollbar-width");
    expect(css).toContain("overscroll-behavior");
  });

  it("A4 选中态根容器 className 含 wfp-ncp-scroll（既有 wfp-ncp-panel 类保留）", () => {
    const { root } = renderSelected();
    expect(root.className).toContain("wfp-ncp-scroll");
    expect(root.className).toContain("wfp-ncp-panel");
  });

  it("A5 选中态根容器内联滚动声明：overflowY=auto / overscrollBehavior=contain / minHeight=0（FR-2.1/3.2）", () => {
    const { root } = renderSelected();
    expect(root.style.overflowY).toBe("auto");
    expect(root.style.overscrollBehavior).toBe("contain");
    expect(root.style.minHeight).toBe("0");
  });

  it("A6 选中态 <style> 含滚动规则且仍含既有 .wfp-ncp-* 规则文本（追加不替换回归，INV-2）", () => {
    const { container } = renderSelected();
    const css = container.querySelector("style")?.textContent ?? "";
    // 新增滚动规则
    expect(css).toContain(".wfp-ncp-scroll::-webkit-scrollbar");
    expect(css).toContain("scrollbar-width");
    expect(css).toContain("overscroll-behavior");
    // 既有 .wfp-ncp-* 规则文本逐字符保留（回归）
    expect(css).toContain(".wfp-ncp-title");
    expect(css).toContain(".wfp-ncp-field-label");
    expect(css).toContain(".wfp-ncp-select");
    expect(css).toContain(".wfp-ncp-input");
  });

  it("A7 深色模式下 <style> 含 darkTokens 双轨滚动条取色（FR-4.5）", () => {
    mockTheme = "dark";
    const { container } = renderSelected();
    const css = container.querySelector("style")?.textContent ?? "";
    // 深色滚动条滑块走 darkTokens.surface.border / borderStrong（themeMode.ts 单一来源）
    expect(css).toContain("#4a4a4a");
    expect(css).toContain("#666666");
  });
});
