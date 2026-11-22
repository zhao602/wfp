// =============================================================================
// 节点配置面板触发方式 select 视觉优化 回归测试
//
// 留待非沙箱环境执行：npx vitest run src/canvas/__tests__/NodeConfigurationPanel.select.test.tsx
//
// 覆盖：
//   S-01 触发器 select 套用 wfp-ncp-select 类（AC-3 / CP-2）
//   S-02 <style> 注入块含 wfp-ncp-select 基态/hover/focus 规则（AC-2 / CP-1）
//   S-03 option 三项不变：manual/scheduled/data_event（AC-9 / INV-1）
//   S-04 onChange 切换 trigger_type 后回写载荷正确（AC-9 / INV-2）
//   S-05 浅色模式 <style> 含浅色箭头色值 %23757575（AC-10 / DC-3）
//   S-06 深色模式 <style> 含深色箭头色值 %238a8a8a（AC-10 / DC-3）
//   S-07 既有 wfp-ncp-readonly-input 类未被改动（AC-14 / INV-5）
// =============================================================================

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import type { ReactNode } from "react";
import NodeConfigurationPanel from "../NodeConfigurationPanel";
import type { CanvasNodeData, WorkflowCanvasState } from "../canvasTypes";
import type { TriggerNodeConfiguration } from "../../workflow-definition/definitionTypes";

// 主题 mock：通过外部变量切换浅色/深色，保留真实 darkTokens 供 <style> 插值
let mockTheme: "light" | "dark" = "light";
vi.mock("../../theme/themeMode", async () => {
  const actual = await vi.importActual<typeof import("../../theme/themeMode")>(
    "../../theme/themeMode",
  );
  return { ...actual, useThemeMode: () => mockTheme };
});

/** 构造 trigger 节点数据 */
function makeTriggerNode(
  triggerType: TriggerNodeConfiguration["trigger_type"] = "manual",
): CanvasNodeData {
  return {
    node_id: "node-trigger-1",
    name: "触发器",
    node_type: "trigger",
    configuration: {
      node_type: "trigger",
      trigger_type: triggerType,
      input_template: {},
    },
  };
}

/** 构造含 trigger 节点的画布状态 */
function makeCanvasState(node: CanvasNodeData): WorkflowCanvasState {
  return {
    nodes: [
      {
        id: "node-trigger-1",
        type: "trigger",
        position: { x: 0, y: 0 },
        data: node,
      },
    ],
    edges: [],
  };
}

/** 渲染面板并返回 onCanvasStateChange spy 与容器 */
function renderPanel(node: CanvasNodeData) {
  const onCanvasStateChange = vi.fn();
  const canvasState = makeCanvasState(node);
  const { container } = render(
    <NodeConfigurationPanel
      selectedNode={node}
      canvasState={canvasState}
      onCanvasStateChange={onCanvasStateChange}
    /> as ReactNode,
  );
  return { onCanvasStateChange, container };
}

describe("NodeConfigurationPanel 触发方式 select 视觉优化", () => {
  beforeEach(() => {
    mockTheme = "light";
  });

  it("S-01 触发器 select 套用 wfp-ncp-select 类", () => {
    const node = makeTriggerNode();
    renderPanel(node);
    const select = screen.getByDisplayValue("手动") as HTMLSelectElement;
    expect(select.className).toContain("wfp-ncp-select");
  });

  it("S-02 <style> 注入块含 wfp-ncp-select 基态/hover/focus 规则", () => {
    const { container } = renderPanel(makeTriggerNode());
    const styleEl = container.querySelector("style");
    expect(styleEl).not.toBeNull();
    const css = styleEl?.textContent ?? "";
    expect(css).toContain(".wfp-ncp-select");
    expect(css).toContain(".wfp-ncp-select:hover");
    expect(css).toContain(".wfp-ncp-select:focus");
  });

  it("S-03 option 三项不变：manual/scheduled/data_event", () => {
    renderPanel(makeTriggerNode());
    const options = screen.getAllByRole("option");
    expect(options).toHaveLength(3);
    expect((options[0] as HTMLOptionElement).value).toBe("manual");
    expect(options[0].textContent).toBe("手动");
    expect((options[1] as HTMLOptionElement).value).toBe("scheduled");
    expect(options[1].textContent).toBe("定时");
    expect((options[2] as HTMLOptionElement).value).toBe("data_event");
    expect(options[2].textContent).toBe("事件");
  });

  it("S-04 onChange 切换 trigger_type 后回写载荷正确", () => {
    const node = makeTriggerNode("manual");
    const { onCanvasStateChange } = renderPanel(node);
    const select = screen.getByDisplayValue("手动") as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "scheduled" } });
    expect(onCanvasStateChange).toHaveBeenCalledTimes(1);
    // 函数式提交适配（CHG-2）：以基准状态应用捕获的 updater（或传值）
    const base = makeCanvasState(node);
    const update = onCanvasStateChange.mock.calls[0][0] as
      | WorkflowCanvasState
      | ((prev: WorkflowCanvasState) => WorkflowCanvasState);
    const next = typeof update === "function" ? update(base) : update;
    const updated = next.nodes[0].data as CanvasNodeData;
    const cfg = updated.configuration as TriggerNodeConfiguration;
    expect(cfg.trigger_type).toBe("scheduled");
    // 其余配置字段保留
    expect(cfg.node_type).toBe("trigger");
    expect(cfg.input_template).toEqual({});
  });

  it("S-05 浅色模式 <style> 含浅色箭头色值 %23757575", () => {
    mockTheme = "light";
    const { container } = renderPanel(makeTriggerNode());
    const css = container.querySelector("style")?.textContent ?? "";
    expect(css).toContain("%23757575");
  });

  it("S-06 深色模式 <style> 含深色箭头色值 %238a8a8a", () => {
    mockTheme = "dark";
    const { container } = renderPanel(makeTriggerNode());
    const css = container.querySelector("style")?.textContent ?? "";
    expect(css).toContain("%238a8a8a");
  });

  it("S-07 既有 wfp-ncp-readonly-input 类未被改动", () => {
    const { container } = renderPanel(makeTriggerNode());
    const css = container.querySelector("style")?.textContent ?? "";
    expect(css).toContain(".wfp-ncp-readonly-input");
    expect(css).toContain(".wfp-ncp-readonly-input:focus");
    // 名称输入框已可编辑（node_name_edit）：只读元素不复存在，验证现行名称输入框在渲染
    const nameInput = container.querySelector('input[aria-describedby="wfp-ncp-hint-common-name"]');
    expect(nameInput).not.toBeNull();
  });
});