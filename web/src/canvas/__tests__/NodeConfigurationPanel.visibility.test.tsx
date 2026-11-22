// =============================================================================
// 节点配置面板字段可见性测试（canvas/__tests__/NodeConfigurationPanel.visibility.test.tsx）
//
// 覆盖（fix_trigger_display）：
//   V-06 谓词真值表：schedule / input_template × manual / scheduled / data_event
//   V-07 谓词缺项降级：未声明字段默认可见、不抛错
//   V-01 ~ V-05、V-08（批次 2 补齐）：DOM 存在性、切换即时性、旧数据零副作用、schedule 等价性
//
// 执行：npx vitest run src/canvas/__tests__/NodeConfigurationPanel.visibility.test.tsx
// =============================================================================

import { useState } from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, cleanup, fireEvent } from "@testing-library/react";
import NodeConfigurationPanel from "../NodeConfigurationPanel";
import type { CanvasNodeData, WorkflowCanvasState } from "../canvasTypes";
import type {
  NodeConfiguration,
  TriggerNodeConfiguration,
} from "../../workflow-definition/definitionTypes";
import { isTriggerFieldVisible } from "../nodeConfigFieldVisibility";
import { buildHintId } from "../nodeConfigHints";

// 主题 mock：与 hints.test.tsx 惯例一致，保留真实 darkTokens 供 <style> 插值
let mockTheme: "light" | "dark" = "light";
vi.mock("../../theme/themeMode", async () => {
  const actual = await vi.importActual<typeof import("../../theme/themeMode")>(
    "../../theme/themeMode",
  );
  return { ...actual, useThemeMode: () => mockTheme };
});

// ---------------------------------------------------------------------------
// 本地工厂（本文件内复制，不走跨测试文件 import，DC-04 裁决）
// ---------------------------------------------------------------------------

/** trigger 配置（默认手动；可传 scheduled / data_event 与旧模板数据） */
function triggerConfig(
  triggerType: TriggerNodeConfiguration["trigger_type"],
  inputTemplate: Record<string, string> = {},
): TriggerNodeConfiguration {
  return { node_type: "trigger", trigger_type: triggerType, input_template: inputTemplate };
}

/** 构造面板节点数据 */
function makeNode(configuration: NodeConfiguration): CanvasNodeData {
  return {
    node_id: `node-${configuration.node_type}`,
    name: `节点-${configuration.node_type}`,
    node_type: configuration.node_type,
    configuration,
  };
}

/** 渲染面板；返回 container 与画布状态变更 mock（V-05 零副作用断言用） */
function renderPanel(node: CanvasNodeData, onCanvasStateChange = vi.fn()) {
  const canvasState: WorkflowCanvasState = {
    nodes: [
      { id: node.node_id, type: node.node_type, position: { x: 0, y: 0 }, data: node },
    ],
    edges: [],
  };
  const result = render(
    <NodeConfigurationPanel
      selectedNode={node}
      canvasState={canvasState}
      onCanvasStateChange={onCanvasStateChange}
    />,
  );
  return { ...result, onCanvasStateChange };
}

describe("触发器字段可见性谓词", () => {
  beforeEach(() => {
    mockTheme = "light";
  });

  afterEach(() => {
    cleanup();
  });

  it("V-06 谓词真值表：schedule 仅 scheduled 可见，input_template 仅非 scheduled 可见", () => {
    const scheduled = triggerConfig("scheduled");
    const manual = triggerConfig("manual");
    const dataEvent = triggerConfig("data_event");

    // schedule：仅定时触发可见
    expect(isTriggerFieldVisible("schedule", scheduled)).toBe(true);
    expect(isTriggerFieldVisible("schedule", manual)).toBe(false);
    expect(isTriggerFieldVisible("schedule", dataEvent)).toBe(false);

    // input_template：定时触发隐藏，手动与事件触发可见
    expect(isTriggerFieldVisible("input_template", scheduled)).toBe(false);
    expect(isTriggerFieldVisible("input_template", manual)).toBe(true);
    expect(isTriggerFieldVisible("input_template", dataEvent)).toBe(true);
  });

  it("V-07 谓词缺项降级：未声明字段默认可见、不抛错", () => {
    const config = triggerConfig("scheduled");
    expect(isTriggerFieldVisible("output_name", config)).toBe(true);
    expect(isTriggerFieldVisible("未声明字段", config)).toBe(true);
    expect(() => isTriggerFieldVisible("whatever", config)).not.toThrow();
  });
});

describe("触发器输入模板面板 DOM 可见性", () => {
  beforeEach(() => {
    mockTheme = "light";
  });

  afterEach(() => {
    cleanup();
  });

  it("V-01 定时触发下输入模板面板不渲染", () => {
    const { container } = renderPanel(makeNode(triggerConfig("scheduled")));
    // 主锚点：提示元素不存在
    expect(container.querySelector(`#${buildHintId("trigger", "input_template")}`)).toBeNull();
    // 辅锚点：KeyValueMapEditor 标题"输入模板"不存在（防"仅提示消失"假阳性）
    const titles = Array.from(container.querySelectorAll(".wfp-kvme-title")).map(
      (el) => el.textContent,
    );
    expect(titles).not.toContain("输入模板");
  });

  it("V-02 手动触发下输入模板面板正常渲染", () => {
    const { container } = renderPanel(makeNode(triggerConfig("manual")));
    const hint = container.querySelector(`#${buildHintId("trigger", "input_template")}`);
    expect(hint).not.toBeNull();
    expect(hint?.textContent ?? "").toContain("触发上下文字段引用");
    const titles = Array.from(container.querySelectorAll(".wfp-kvme-title")).map(
      (el) => el.textContent,
    );
    expect(titles).toContain("输入模板");
  });

  it("V-03 事件触发下输入模板面板正常渲染", () => {
    const { container } = renderPanel(makeNode(triggerConfig("data_event")));
    const hint = container.querySelector(`#${buildHintId("trigger", "input_template")}`);
    expect(hint).not.toBeNull();
    expect(hint?.textContent ?? "").toContain("触发上下文字段引用");
    const titles = Array.from(container.querySelectorAll(".wfp-kvme-title")).map(
      (el) => el.textContent,
    );
    expect(titles).toContain("输入模板");
  });

  it("V-04 触发方式切换的即时隐藏（manual → scheduled）", () => {
    const node = makeNode(triggerConfig("manual"));
    // 有状态包装器：以函数式回写驱动受控重渲染，模拟真实父组件数据流
    function StatefulPanel() {
      const [canvasState, setCanvasState] = useState<WorkflowCanvasState>({
        nodes: [
          { id: node.node_id, type: node.node_type, position: { x: 0, y: 0 }, data: node },
        ],
        edges: [],
      });
      return (
        <NodeConfigurationPanel
          selectedNode={canvasState.nodes[0].data}
          canvasState={canvasState}
          onCanvasStateChange={setCanvasState}
        />
      );
    }
    const { container } = render(<StatefulPanel />);
    expect(container.querySelector(`#${buildHintId("trigger", "input_template")}`)).not.toBeNull();

    const select = container.querySelector(
      `select[aria-describedby="${buildHintId("trigger", "trigger_type")}"]`,
    ) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "scheduled" } });

    // 无需重新选择节点，切换后即时隐藏
    expect(container.querySelector(`#${buildHintId("trigger", "input_template")}`)).toBeNull();
    expect(container.querySelector(`#${buildHintId("trigger", "schedule")}`)).not.toBeNull();
  });

  it("V-05 旧数据场景（scheduled + 非空模板）隐藏、零副作用、数据保留", () => {
    const node = makeNode(triggerConfig("scheduled", { topic: "topic" }));
    const { container, onCanvasStateChange } = renderPanel(node);
    // 面板隐藏
    expect(container.querySelector(`#${buildHintId("trigger", "input_template")}`)).toBeNull();
    // 零副作用：隐藏期间未回写画布状态
    expect(onCanvasStateChange).not.toHaveBeenCalled();
    // 数据保留：节点配置中的模板原样驻留
    expect((node.configuration as TriggerNodeConfiguration).input_template).toEqual({
      topic: "topic",
    });
  });

  it("V-08 schedule 收敛等价性独立守护", () => {
    const scheduled = renderPanel(makeNode(triggerConfig("scheduled")));
    expect(
      scheduled.container.querySelector(`#${buildHintId("trigger", "schedule")}`),
    ).not.toBeNull();
    scheduled.unmount();

    const manual = renderPanel(makeNode(triggerConfig("manual")));
    expect(manual.container.querySelector(`#${buildHintId("trigger", "schedule")}`)).toBeNull();
    manual.unmount();
  });
});