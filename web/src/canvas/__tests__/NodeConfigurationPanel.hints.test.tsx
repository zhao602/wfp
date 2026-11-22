// =============================================================================
// 节点配置面板提示信息回归测试（canvas/__tests__/NodeConfigurationPanel.hints.test.tsx）
//
// 留待非沙箱环境执行：
//   npx vitest run src/canvas/__tests__/NodeConfigurationPanel.hints.test.tsx
//
// 覆盖：
//   H-01 节点级说明：8 种节点类型各渲染一条非空说明且互异（AC-1 / FR-1）
//   H-02 字段级 helper：trigger 字段说明非空（trigger_type/schedule 以定时渲染；input_template 以手动渲染，AC-2 / FR-2）
//   H-03 覆盖完整性：字段键逐一断言 helper 存在（trigger 按 scheduled/manual 拆行，AC-3 / FR-5）
//   H-04 placeholder 规则：文本类有、其余无（AC-4 / FR-3）
//   H-05 aria 关联：aria-describedby 指向存在元素且 id 唯一（AC-5 / FR-8）
//   H-06 条件字段联动：提示随字段同步出现/消失（AC-6 / FR-6）
//   H-07 双主题取色：浅色/深色分别取预期值（AC-7 / FR-9）
//   H-08 样式约束：新类存在且无违规关键字（AC-9 / AC-10）
//   H-09 缺项降级：映射缺失不影响渲染（AC-13 / FR-4.6）
// =============================================================================

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, cleanup } from "@testing-library/react";
import NodeConfigurationPanel from "../NodeConfigurationPanel";
import type { CanvasNodeData, WorkflowCanvasState } from "../canvasTypes";
import type { NodeConfiguration } from "../../workflow-definition/definitionTypes";
import {
  resolveFieldHint,
  buildHintId,
} from "../nodeConfigHints";
import { darkTokens } from "../../theme/themeMode";

// 主题 mock：通过外部变量切换浅色/深色，保留真实 darkTokens 供 <style> 插值
let mockTheme: "light" | "dark" = "light";
vi.mock("../../theme/themeMode", async () => {
  const actual = await vi.importActual<typeof import("../../theme/themeMode")>(
    "../../theme/themeMode",
  );
  return { ...actual, useThemeMode: () => mockTheme };
});

// ---------------------------------------------------------------------------
// 节点数据工厂（8 种节点类型）
// ---------------------------------------------------------------------------

/** trigger 配置（默认手动；传 scheduled 以验证条件字段） */
function triggerConfig(
  triggerType: "manual" | "scheduled" = "manual",
): NodeConfiguration {
  return { node_type: "trigger", trigger_type: triggerType, input_template: {} };
}

/** agent 配置 */
function agentConfig(): NodeConfiguration {
  return {
    node_type: "agent",
    model_identifier: "model-1",
    system_prompt: "提示词",
    available_tools: [],
  };
}

/** tool 配置 */
function toolConfig(): NodeConfiguration {
  return {
    node_type: "tool",
    tool_identifier: "tool-1",
    tool_source: "local",
    argument_mapping: {},
    static_arguments: {},
  };
}

/** data_source 配置 */
function dataSourceConfig(): NodeConfiguration {
  return {
    node_type: "data_source",
    connection_reference: "conn-1",
    query: "select 1",
  };
}

/** control_flow 配置（默认 loop；传 conditional_branch 以验证条件字段差异） */
function controlFlowConfig(
  kind: "loop" | "conditional_branch" = "loop",
): NodeConfiguration {
  return {
    node_type: "control_flow",
    control_flow_kind: kind,
    conditions: [],
    parallel_failure_strategy: "fast_fail",
    review_timeout_action: "reject",
    ...(kind === "loop"
      ? {
          loop_body_entry: "entry-1",
          loop_termination_condition: "done",
          max_iterations: 3,
        }
      : {}),
  };
}

/** subworkflow 配置 */
function subWorkflowConfig(): NodeConfiguration {
  return {
    node_type: "subworkflow",
    subworkflow_definition_id: "sub-1",
    input_mapping: {},
    output_mapping: {},
    context_strategy: "inherit",
    max_nesting_depth: 2,
  };
}

/** dynamic_injector 配置 */
function dynamicInjectorConfig(): NodeConfiguration {
  return {
    node_type: "dynamic_injector",
    injection_condition: "items",
    node_templates: [
      {
        template_id: "tpl-1",
        node_type: "agent",
        name: "模板-1",
        configuration: { model_identifier: "model-1", system_prompt: "", available_tools: [] },
      },
    ],
    max_injection_count: 5,
  };
}

/** data_transform 配置（含 2 个操作项，验证多实例 id 消歧） */
function dataTransformConfig(): NodeConfiguration {
  return {
    node_type: "data_transform",
    operation: "map",
    input_dataset_reference: "ds-1",
    params: {},
    operations: [
      { operation: "map", params: {} },
      { operation: "filter", params: {} },
    ],
  };
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

/** 渲染面板 */
function renderPanel(node: CanvasNodeData) {
  const canvasState: WorkflowCanvasState = {
    nodes: [
      { id: node.node_id, type: node.node_type, position: { x: 0, y: 0 }, data: node },
    ],
    edges: [],
  };
  return render(
    <NodeConfigurationPanel
      selectedNode={node}
      canvasState={canvasState}
      onCanvasStateChange={vi.fn()}
    />,
  );
}

const ALL_CONFIGS: NodeConfiguration[] = [
  triggerConfig("scheduled"),
  agentConfig(),
  toolConfig(),
  dataSourceConfig(),
  controlFlowConfig("loop"),
  subWorkflowConfig(),
  dynamicInjectorConfig(),
  dataTransformConfig(),
];

describe("NodeConfigurationPanel 提示信息", () => {
  beforeEach(() => {
    mockTheme = "light";
  });

  afterEach(() => {
    cleanup();
  });

  it("H-01 节点级说明：8 种节点类型各渲染一条非空说明且互异", () => {
    const hints: string[] = [];
    for (const configuration of ALL_CONFIGS) {
      const { container, unmount } = renderPanel(makeNode(configuration));
      const el = container.querySelector(".wfp-ncp-node-hint");
      expect(el, `${configuration.node_type} 应渲染节点级说明`).not.toBeNull();
      const text = el?.textContent ?? "";
      expect(text.length).toBeGreaterThan(0);
      hints.push(text);
      unmount();
    }
    // 8 段说明两两不等
    expect(new Set(hints).size).toBe(8);
  });

  it("H-02 字段级 helper：trigger 字段说明非空", () => {
    // trigger_type / schedule：定时触发下渲染
    const scheduled = renderPanel(makeNode(triggerConfig("scheduled")));
    const typeHint = scheduled.container.querySelector(
      `#${buildHintId("trigger", "trigger_type")}`,
    );
    const scheduleHint = scheduled.container.querySelector(
      `#${buildHintId("trigger", "schedule")}`,
    );
    expect(typeHint).not.toBeNull();
    expect((typeHint?.textContent ?? "").length).toBeGreaterThan(0);
    expect(scheduleHint).not.toBeNull();
    expect((scheduleHint?.textContent ?? "").length).toBeGreaterThan(0);
    scheduled.unmount();

    // input_template：仅非定时触发下渲染（fix_trigger_display：定时触发无运行时入口、模板不生效故隐藏）
    const manual = renderPanel(makeNode(triggerConfig("manual")));
    const inputTemplateHint = manual.container.querySelector(
      `#${buildHintId("trigger", "input_template")}`,
    );
    expect(inputTemplateHint).not.toBeNull();
    const inputTemplateText = inputTemplateHint?.textContent ?? "";
    expect(inputTemplateText.length).toBeGreaterThan(0);
    expect(inputTemplateText).toContain("触发上下文字段引用");
    expect(inputTemplateText).toContain("定时触发暂无运行时入口");
    manual.unmount();
  });

  it("H-03 覆盖完整性：23 个字段键逐一断言 helper 存在", () => {
    // 公共字段：节点名称在任一面板中常驻
    expect(resolveFieldHint("common", "name")).toBeDefined();

    const cases: Array<{ configuration: NodeConfiguration; fields: string[] }> = [
      { configuration: triggerConfig("scheduled"), fields: ["trigger_type", "schedule"] },
      { configuration: triggerConfig("manual"), fields: ["trigger_type", "input_template"] },
      { configuration: agentConfig(), fields: ["model_identifier", "system_prompt"] },
      { configuration: toolConfig(), fields: ["tool_identifier", "tool_source"] },
      { configuration: dataSourceConfig(), fields: ["connection_reference", "query"] },
      {
        configuration: controlFlowConfig("loop"),
        fields: [
          "control_flow_kind",
          "loop_body_entry",
          "loop_termination_condition",
          "max_iterations",
        ],
      },
      {
        configuration: subWorkflowConfig(),
        fields: ["subworkflow_definition_id", "context_strategy", "max_nesting_depth"],
      },
      {
        configuration: dynamicInjectorConfig(),
        fields: [
          "injection_condition",
          "node_templates",
          "max_injection_count",
          "target_position",
        ],
      },
      { configuration: dataTransformConfig(), fields: ["operations"] },
    ];

    for (const item of cases) {
      const { container, unmount } = renderPanel(makeNode(item.configuration));
      for (const field of item.fields) {
        expect(
          resolveFieldHint(item.configuration.node_type, field),
          `${item.configuration.node_type}.${field} 应有映射`,
        ).toBeDefined();
        expect(
          container.querySelector(
            `#${buildHintId(item.configuration.node_type, field)}`,
          ),
          `${item.configuration.node_type}.${field} 的 helper 应渲染`,
        ).not.toBeNull();
      }
      // 节点名称 helper 亦常驻
      expect(container.querySelector(`#${buildHintId("common", "name")}`)).not.toBeNull();
      unmount();
    }

    // data_transform 多实例 operation（键 1 个、实例 2 个）
    const dt = renderPanel(makeNode(dataTransformConfig()));
    expect(
      dt.container.querySelector(`#${buildHintId("data_transform", "operation", 0)}`),
    ).not.toBeNull();
    expect(
      dt.container.querySelector(`#${buildHintId("data_transform", "operation", 1)}`),
    ).not.toBeNull();
  });

  it("H-04 placeholder 规则：文本类有、其余无", () => {
    // agent：model_identifier 已替换为候选控件（原生 input 不复存在，
    // 其降级态占位由组件级测试 TC-F4/TC-F5 覆盖）；system_prompt textarea 有 placeholder
    const agent = renderPanel(makeNode(agentConfig()));
    expect(
      agent.container
        .querySelector(`textarea[aria-describedby="${buildHintId("agent", "system_prompt")}"]`)
        ?.getAttribute("placeholder"),
    ).toBeTruthy();
    // 节点名称 input（node_name_edit 后已可编辑）无 placeholder
    expect(
      agent.container
        .querySelector(`input[aria-describedby="${buildHintId("common", "name")}"]`)
        ?.getAttribute("placeholder"),
    ).toBeNull();
    agent.unmount();

    // control_flow：number 无、text 有
    const cf = renderPanel(makeNode(controlFlowConfig("loop")));
    expect(
      cf.container
        .querySelector(`input[aria-describedby="${buildHintId("control_flow", "max_iterations")}"]`)
        ?.getAttribute("placeholder"),
    ).toBeNull();
    expect(
      cf.container
        .querySelector(`input[aria-describedby="${buildHintId("control_flow", "loop_body_entry")}"]`)
        ?.getAttribute("placeholder"),
    ).toBeTruthy();
    cf.unmount();

    // tool：select 无 placeholder
    const tool = renderPanel(makeNode(toolConfig()));
    expect(
      tool.container
        .querySelector(`select[aria-describedby="${buildHintId("tool", "tool_source")}"]`)
        ?.getAttribute("placeholder"),
    ).toBeNull();
  });

  it("H-05 aria 关联：aria-describedby 指向存在元素且 id 唯一", () => {
    const { container } = renderPanel(makeNode(dataTransformConfig()));
    const described = Array.from(container.querySelectorAll("[aria-describedby]"));
    const ids: string[] = [];
    for (const el of described) {
      const id = el.getAttribute("aria-describedby");
      expect(id).toBeTruthy();
      if (id) {
        expect(container.querySelector(`#${id}`)).not.toBeNull();
        ids.push(id);
      }
    }
    // id 唯一（含 data_transform 多实例 -0 / -1 消歧）
    expect(new Set(ids).size).toBe(ids.length);
    expect(ids).toContain(buildHintId("data_transform", "operation", 0));
    expect(ids).toContain(buildHintId("data_transform", "operation", 1));
  });

  it("H-06 条件字段联动：提示随字段同步出现/消失", () => {
    // trigger：manual 无 schedule 提示
    const manual = renderPanel(makeNode(triggerConfig("manual")));
    expect(
      manual.container.querySelector(`#${buildHintId("trigger", "schedule")}`),
    ).toBeNull();
    manual.unmount();

    // trigger：scheduled 有 schedule 提示
    const scheduled = renderPanel(makeNode(triggerConfig("scheduled")));
    expect(
      scheduled.container.querySelector(`#${buildHintId("trigger", "schedule")}`),
    ).not.toBeNull();
    scheduled.unmount();

    // control_flow：conditional_branch 无 loop 提示
    const branch = renderPanel(makeNode(controlFlowConfig("conditional_branch")));
    expect(
      branch.container.querySelector(`#${buildHintId("control_flow", "loop_body_entry")}`),
    ).toBeNull();
    branch.unmount();

    // control_flow：loop 有 loop 提示
    const loop = renderPanel(makeNode(controlFlowConfig("loop")));
    expect(
      loop.container.querySelector(`#${buildHintId("control_flow", "loop_body_entry")}`),
    ).not.toBeNull();
  });

  it("H-07 双主题取色：浅色/深色分别取预期值且类名一致", () => {
    mockTheme = "light";
    const light = renderPanel(makeNode(triggerConfig("scheduled")));
    const lightCss = light.container.querySelector("style")?.textContent ?? "";
    expect(lightCss).toContain("#9a9a9a");
    expect(lightCss).toContain(".wfp-ncp-field-hint");
    expect(lightCss).toContain(".wfp-ncp-node-hint");
    expect(light.container.querySelector(".wfp-ncp-node-hint")).not.toBeNull();
    expect(light.container.querySelector(".wfp-ncp-field-hint")).not.toBeNull();
    light.unmount();

    mockTheme = "dark";
    const dark = renderPanel(makeNode(triggerConfig("scheduled")));
    const darkCss = dark.container.querySelector("style")?.textContent ?? "";
    expect(darkCss).toContain(darkTokens.text.hint);
    expect(darkCss).toContain(".wfp-ncp-field-hint");
    expect(darkCss).toContain(".wfp-ncp-node-hint");
    expect(dark.container.querySelector(".wfp-ncp-node-hint")).not.toBeNull();
    expect(dark.container.querySelector(".wfp-ncp-field-hint")).not.toBeNull();
  });

  it("H-08 样式约束：新类存在且无违规关键字", () => {
    const { container } = renderPanel(makeNode(triggerConfig()));
    const css = container.querySelector("style")?.textContent ?? "";
    expect(css).toContain(".wfp-ncp-node-hint");
    expect(css).toContain(".wfp-ncp-field-hint");
    expect(css).not.toContain("@keyframes");
    expect(css).not.toContain("var(--");
  });

  it("H-09 缺项降级：映射缺失不影响渲染", () => {
    // 单元级：未配置字段键返回 undefined（不抛异常）
    expect(resolveFieldHint("trigger", "未配置字段")).toBeUndefined();
    expect(resolveFieldHint("common", "未配置字段")).toBeUndefined();
    // 面板渲染无异常
    expect(() => {
      renderPanel(makeNode(triggerConfig()));
    }).not.toThrow();
  });

  it("H-10 可编辑输入框统一套用 wfp-ncp-input 类", () => {
    const { container } = renderPanel(makeNode(agentConfig()));
    const css = container.querySelector("style")?.textContent ?? "";
    // 样式类及其交互态存在
    expect(css).toContain(".wfp-ncp-input {");
    expect(css).toContain(".wfp-ncp-input:hover");
    expect(css).toContain(".wfp-ncp-input:focus");
    expect(css).toContain(".wfp-ncp-input::placeholder");
    // model_identifier 已替换为 ModelSelectionList（非原生组件，样式类 wfp-msl-*
    // 由组件级测试覆盖）；可编辑 textarea 套用该类
    expect(
      container
        .querySelector(`textarea[aria-describedby="${buildHintId("agent", "system_prompt")}"]`)
        ?.className,
    ).toContain("wfp-ncp-input");
    // 节点名称已可编辑（node_name_edit）：统一套用可编辑类 wfp-ncp-input
    const nameInput = container.querySelector(`input[aria-describedby="${buildHintId("common", "name")}"]`);
    expect(nameInput).not.toBeNull();
    expect(nameInput?.className).toContain("wfp-ncp-input");
  });

  it("H-11 全部下拉框统一套用 wfp-ncp-select 类", () => {
    // tool：工具来源 select
    const tool = renderPanel(makeNode(toolConfig()));
    expect(
      tool.container
        .querySelector(`select[aria-describedby="${buildHintId("tool", "tool_source")}"]`)
        ?.className,
    ).toContain("wfp-ncp-select");
    tool.unmount();

    // control_flow：控制流子类型 select
    const cf = renderPanel(makeNode(controlFlowConfig("loop")));
    expect(
      cf.container
        .querySelector(
          `select[aria-describedby="${buildHintId("control_flow", "control_flow_kind")}"]`,
        )
        ?.className,
    ).toContain("wfp-ncp-select");
    cf.unmount();

    // subworkflow：上下文策略 select
    const sub = renderPanel(makeNode(subWorkflowConfig()));
    expect(
      sub.container
        .querySelector(
          `select[aria-describedby="${buildHintId("subworkflow", "context_strategy")}"]`,
        )
        ?.className,
    ).toContain("wfp-ncp-select");
    sub.unmount();

    // data_transform：每项操作 select
    const dt = renderPanel(makeNode(dataTransformConfig()));
    const selects = Array.from(dt.container.querySelectorAll("select"));
    expect(selects.length).toBe(2);
    for (const el of selects) {
      expect(el.className).toContain("wfp-ncp-select");
    }
  });
});