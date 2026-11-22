// =============================================================================
// 节点配置面板输出展示配置测试（src/canvas/__tests__/NodeConfigurationPanel.outputDisplayConfig.test.tsx）
//
// 执行：cd web && npx vitest run src/canvas/__tests__/NodeConfigurationPanel.outputDisplayConfig.test.tsx
//
// 覆盖 optimize_output_display_node T8.2 用例组（CHG-19 / FR-1 / FR-2 / FR-3）：
//   ① 表单分派：output_display 节点渲染输出方式/输出内容（引用）/展示标题，
//     output_mode 缺省 ≡ 仅「屏幕显示」勾选（AC-1 / DC-01）
//   ② 输出方式复选框回写：勾选/取消全量回写；取消至空回落 ["screen"]（AC-2）
//   ③ 输出内容候选渲染：候选=上游有效输出（直接/间接上游且可引用），
//     控制流（referable=false）/非上游/未声明 output_name 者排除；
//     依赖占位「上游输入参数」灰显不可勾选（AC-3 / DC-05）
//   ④ 输出内容复选框回写：勾选 → input_reference 数组回写；旧 YAML 标量回显勾选；
//     候选外存量引用「已失效」标记行可移除（AC-3 / R-04）
//   ⑤ 展示标题回写保持（既有行为回归）
// =============================================================================

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, within, cleanup } from "@testing-library/react";
import type { SetStateAction } from "react";
import type { CanvasNode, CanvasNodeData, CanvasEdge, WorkflowCanvasState } from "../canvasTypes";
import type {
  OutputDisplayNodeConfiguration,
} from "../../workflow-definition/definitionTypes";

// 主题 mock：外部变量切换浅色/深色，保留真实 darkTokens 供 <style> 插值
let mockTheme: "light" | "dark" = "light";
vi.mock("../../theme/themeMode", async () => {
  const actual = await vi.importActual<typeof import("../../theme/themeMode")>(
    "../../theme/themeMode",
  );
  return { ...actual, useThemeMode: () => mockTheme };
});

import NodeConfigurationPanel from "../NodeConfigurationPanel";

// ---------------------------------------------------------------------------
// 画布样例：目标 od-1 + 上游拓扑
//
//   agent-1 ──────────────────┐
//   tool-1 → cf-1（控制流）───┤→ od-1（output_display，目标节点）
//   agent-2（无命名输出）─────┘
//   iso-1（命名输出但非上游，孤立）
//
// 预期候选：analysis_result（直接上游）、weather_result（经 cf-1 间接上游）；
// 排除：branch_signal（控制流 referable=false）、agent-2（未声明 output_name）、
//       isolated_dataset（非上游）。
// ---------------------------------------------------------------------------

/** 输出展示节点配置工厂（全部可选字段缺省，模拟旧 YAML 最小形态） */
function outputDisplayConfig(
  overrides?: Partial<OutputDisplayNodeConfiguration>,
): OutputDisplayNodeConfiguration {
  return { node_type: "output_display", ...overrides };
}

/** 构造画布：目标输出展示节点 + 上游拓扑样例 */
function buildCanvas(odConfig: OutputDisplayNodeConfiguration): WorkflowCanvasState {
  const odData: CanvasNodeData = {
    node_id: "od-1",
    name: "结果展示",
    node_type: "output_display",
    configuration: odConfig,
  };
  const nodes: CanvasNode[] = [
    {
      id: "agent-1",
      type: "agent",
      position: { x: 0, y: 0 },
      data: {
        node_id: "agent-1",
        name: "情报分析",
        node_type: "agent",
        output_name: "analysis_result",
        configuration: {
          node_type: "agent",
          model_identifier: "model-1",
          system_prompt: "分析",
          available_tools: [],
        },
      },
    },
    {
      id: "tool-1",
      type: "tool",
      position: { x: 0, y: 80 },
      data: {
        node_id: "tool-1",
        name: "天气查询",
        node_type: "tool",
        output_name: "weather_result",
        configuration: {
          node_type: "tool",
          tool_identifier: "tool-1",
          tool_source: "local",
          argument_mapping: {},
          static_arguments: {},
        },
      },
    },
    {
      id: "cf-1",
      type: "control_flow",
      position: { x: 0, y: 160 },
      data: {
        node_id: "cf-1",
        name: "分支控制",
        node_type: "control_flow",
        output_name: "branch_signal",
        configuration: {
          node_type: "control_flow",
          control_flow_kind: "conditional_branch",
          conditions: [],
          parallel_failure_strategy: "fast_fail",
          review_timeout_action: "reject",
        },
      },
    },
    {
      id: "agent-2",
      type: "agent",
      position: { x: 0, y: 240 },
      data: {
        node_id: "agent-2",
        name: "未命名输出智能体",
        node_type: "agent",
        configuration: {
          node_type: "agent",
          model_identifier: "model-1",
          system_prompt: "辅助",
          available_tools: [],
        },
      },
    },
    {
      id: "iso-1",
      type: "data_source",
      position: { x: 0, y: 320 },
      data: {
        node_id: "iso-1",
        name: "孤立数据源",
        node_type: "data_source",
        output_name: "isolated_dataset",
        configuration: {
          node_type: "data_source",
          connection_reference: "conn-1",
          query: "select 1",
        },
      },
    },
    { id: "od-1", type: "output_display", position: { x: 200, y: 0 }, data: odData },
  ];
  const edges: CanvasEdge[] = [
    { id: "e-1", source: "agent-1", target: "od-1" },
    { id: "e-2", source: "cf-1", target: "od-1" },
    { id: "e-3", source: "tool-1", target: "cf-1" },
    { id: "e-4", source: "agent-2", target: "od-1" },
  ];
  return { nodes, edges };
}

/** 渲染面板（选中 od-1）并返回画布变更回调 */
function renderPanel(odConfig: OutputDisplayNodeConfiguration) {
  const canvasState = buildCanvas(odConfig);
  const odNode = canvasState.nodes.find((node) => node.id === "od-1");
  const onCanvasStateChange = vi.fn<(update: SetStateAction<WorkflowCanvasState>) => void>();
  render(
    <NodeConfigurationPanel
      selectedNode={odNode!.data}
      canvasState={canvasState}
      onCanvasStateChange={onCanvasStateChange}
    />,
  );
  return { canvasState, onCanvasStateChange };
}

/** 取最近一次画布变更中 od-1 的最新配置 */
function latestConfig(
  onCanvasStateChange: ReturnType<typeof renderPanel>["onCanvasStateChange"],
  base: WorkflowCanvasState,
): OutputDisplayNodeConfiguration {
  expect(onCanvasStateChange.mock.calls.length).toBeGreaterThan(0);
  const updater = onCanvasStateChange.mock.calls[
    onCanvasStateChange.mock.calls.length - 1
  ][0] as (prev: WorkflowCanvasState) => WorkflowCanvasState;
  const next = updater(base);
  const odNode = next.nodes.find((node) => node.data.node_id === "od-1");
  return odNode!.data.configuration as OutputDisplayNodeConfiguration;
}

/** 按文案定位输出方式复选框（label 包裹 input） */
function modeCheckbox(labelText: string): HTMLInputElement {
  const label = screen.getByText(labelText).closest("label");
  expect(label, `应存在「${labelText}」输出方式行`).not.toBeNull();
  return label!.querySelector("input") as HTMLInputElement;
}

/** 按行 title（nodeName（outputName））定位候选行内勾选框 */
function candidateCheckbox(rowTitle: string): HTMLInputElement {
  const row = screen.getByTitle(rowTitle);
  return within(row).getByRole("checkbox") as HTMLInputElement;
}

// ---------------------------------------------------------------------------
// ① 表单分派与缺省输出方式
// ---------------------------------------------------------------------------

describe("① 表单分派与缺省输出方式（AC-1 / DC-01）", () => {
  beforeEach(() => {
    mockTheme = "light";
  });
  afterEach(() => {
    cleanup();
  });

  it("output_display 节点渲染输出方式 / 输出内容（引用）/ 展示标题三区块", () => {
    renderPanel(outputDisplayConfig());
    expect(screen.getByText("输出方式")).toBeTruthy();
    expect(screen.getByText("输出内容（引用）")).toBeTruthy();
    expect(screen.getByText("展示标题")).toBeTruthy();
  });

  it("output_mode 缺省 ≡ 仅「屏幕显示」勾选，「输出为文件」不勾选", () => {
    renderPanel(outputDisplayConfig());
    expect(modeCheckbox("屏幕显示").checked).toBe(true);
    expect(modeCheckbox("输出为文件").checked).toBe(false);
  });

  it("output_mode=['file'] 回显：仅「输出为文件」勾选", () => {
    renderPanel(outputDisplayConfig({ output_mode: ["file"] }));
    expect(modeCheckbox("屏幕显示").checked).toBe(false);
    expect(modeCheckbox("输出为文件").checked).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// ② 输出方式复选框回写
// ---------------------------------------------------------------------------

describe("② 输出方式复选框回写（AC-2）", () => {
  beforeEach(() => {
    mockTheme = "light";
  });
  afterEach(() => {
    cleanup();
  });

  it("勾选「输出为文件」→ output_mode=['screen','file']", () => {
    const { canvasState, onCanvasStateChange } = renderPanel(outputDisplayConfig());
    fireEvent.click(modeCheckbox("输出为文件"));
    expect(latestConfig(onCanvasStateChange, canvasState).output_mode).toEqual([
      "screen",
      "file",
    ]);
  });

  it("取消「屏幕显示」→ output_mode=['file']", () => {
    const { canvasState, onCanvasStateChange } = renderPanel(
      outputDisplayConfig({ output_mode: ["screen", "file"] }),
    );
    fireEvent.click(modeCheckbox("屏幕显示"));
    expect(latestConfig(onCanvasStateChange, canvasState).output_mode).toEqual(["file"]);
  });

  it("取消至空回落 ['screen']（禁止「无任何输出方式」）", () => {
    const { canvasState, onCanvasStateChange } = renderPanel(
      outputDisplayConfig({ output_mode: ["file"] }),
    );
    fireEvent.click(modeCheckbox("输出为文件"));
    expect(onCanvasStateChange).toHaveBeenCalledTimes(1);
    expect(latestConfig(onCanvasStateChange, canvasState).output_mode).toEqual(["screen"]);
  });

  it("回写仅替换目标节点配置，非目标节点与连线原样透传", () => {
    const { canvasState, onCanvasStateChange } = renderPanel(outputDisplayConfig());
    const before = JSON.stringify({
      others: canvasState.nodes.filter((node) => node.data.node_id !== "od-1"),
      edges: canvasState.edges,
    });
    fireEvent.click(modeCheckbox("输出为文件"));
    const updater = onCanvasStateChange.mock.calls[0][0] as (
      prev: WorkflowCanvasState,
    ) => WorkflowCanvasState;
    const next = updater(canvasState);
    expect(
      JSON.stringify({
        others: next.nodes.filter((node) => node.data.node_id !== "od-1"),
        edges: next.edges,
      }),
    ).toBe(before);
  });
});

// ---------------------------------------------------------------------------
// ③ 输出内容候选渲染（上游限定 + 依赖占位）
// ---------------------------------------------------------------------------

describe("③ 输出内容候选渲染（AC-3 / DC-05）", () => {
  beforeEach(() => {
    mockTheme = "light";
  });
  afterEach(() => {
    cleanup();
  });

  it("候选=上游有效输出：直接/间接上游且可引用者入选并带类别徽标", () => {
    renderPanel(outputDisplayConfig());
    // 直接入选：直接上游 agent-1、经 cf-1 间接上游 tool-1
    expect(screen.getByTitle("情报分析（analysis_result）")).toBeTruthy();
    expect(screen.getByTitle("天气查询（weather_result）")).toBeTruthy();
    // 类别徽标（契约填充）
    expect(screen.getByText("结构化对象")).toBeTruthy();
    expect(screen.getByText("工具调用结果")).toBeTruthy();
  });

  it("排除：控制流（referable=false）、非上游、未声明 output_name 者不入选", () => {
    renderPanel(outputDisplayConfig());
    expect(screen.queryByText("branch_signal")).toBeNull();
    expect(screen.queryByText("isolated_dataset")).toBeNull();
    expect(screen.queryByText("未命名输出智能体")).toBeNull();
  });

  it("依赖占位：「上游输入参数」分组灰显、复选框禁用、说明就位", () => {
    renderPanel(outputDisplayConfig());
    expect(screen.getByText("上游输入参数")).toBeTruthy();
    expect(screen.getByText("待 upstream_input_ref 落地后启用")).toBeTruthy();
    const deferredCheckbox = screen
      .getByText("待 upstream_input_ref 落地后启用")
      .closest("div")
      ?.querySelector('input[type="checkbox"]') as HTMLInputElement;
    expect(deferredCheckbox).not.toBeNull();
    expect(deferredCheckbox.disabled).toBe(true);
  });

  it("无上游候选时渲染「暂无上游可引用输出」", () => {
    const canvasState: WorkflowCanvasState = {
      nodes: [
        {
          id: "od-1",
          type: "output_display",
          position: { x: 0, y: 0 },
          data: {
            node_id: "od-1",
            name: "结果展示",
            node_type: "output_display",
            configuration: outputDisplayConfig(),
          },
        },
      ],
      edges: [],
    };
    render(
      <NodeConfigurationPanel
        selectedNode={canvasState.nodes[0].data}
        canvasState={canvasState}
        onCanvasStateChange={vi.fn<(update: SetStateAction<WorkflowCanvasState>) => void>()}
      />,
    );
    expect(screen.getByText("暂无上游可引用输出")).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// ④ 输出内容复选框回写
// ---------------------------------------------------------------------------

describe("④ 输出内容复选框回写（AC-3 / R-04）", () => {
  beforeEach(() => {
    mockTheme = "light";
  });
  afterEach(() => {
    cleanup();
  });

  it("勾选候选 → input_reference=['analysis_result']（数组回写）", () => {
    const { canvasState, onCanvasStateChange } = renderPanel(outputDisplayConfig());
    fireEvent.click(candidateCheckbox("情报分析（analysis_result）"));
    expect(latestConfig(onCanvasStateChange, canvasState).input_reference).toEqual([
      "analysis_result",
    ]);
  });

  it("旧 YAML 标量回显：input_reference='weather_result' → 对应候选勾选", () => {
    renderPanel(outputDisplayConfig({ input_reference: "weather_result" }));
    expect(candidateCheckbox("天气查询（weather_result）").checked).toBe(true);
    expect(candidateCheckbox("情报分析（analysis_result）").checked).toBe(false);
    expect(screen.getByText("已选 1 项")).toBeTruthy();
  });

  it("续勾追加保序、取消按引用过滤", () => {
    const { canvasState, onCanvasStateChange } = renderPanel(
      outputDisplayConfig({ input_reference: ["analysis_result"] }),
    );
    fireEvent.click(candidateCheckbox("天气查询（weather_result）"));
    expect(latestConfig(onCanvasStateChange, canvasState).input_reference).toEqual([
      "analysis_result",
      "weather_result",
    ]);
  });

  it("候选外存量引用渲染「已失效」标记行且可移除", () => {
    const { canvasState, onCanvasStateChange } = renderPanel(
      outputDisplayConfig({ input_reference: ["legacy_ref"] }),
    );
    const row = screen.getByTitle("legacy_ref");
    expect(within(row).getByText("已失效")).toBeTruthy();
    fireEvent.click(within(row).getByText("✕"));
    expect(latestConfig(onCanvasStateChange, canvasState).input_reference).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// ⑤ 展示标题回写保持（既有行为回归）
// ---------------------------------------------------------------------------

describe("⑤ 展示标题回写保持（回归）", () => {
  beforeEach(() => {
    mockTheme = "light";
  });
  afterEach(() => {
    cleanup();
  });

  it("编辑展示标题 → display_title 回写", () => {
    const { canvasState, onCanvasStateChange } = renderPanel(
      outputDisplayConfig({ display_title: "旧标题" }),
    );
    const titleInput = screen.getByDisplayValue("旧标题") as HTMLInputElement;
    expect(titleInput.className).toContain("wfp-ncp-input");
    fireEvent.change(titleInput, { target: { value: "新标题" } });
    expect(latestConfig(onCanvasStateChange, canvasState).display_title).toBe("新标题");
  });
});

// ---------------------------------------------------------------------------
// ⑥ null 容错渲染（fix_raw_not_iterable CHG-07 / T9.1）
// ---------------------------------------------------------------------------

describe("⑥ null 容错渲染（CHG-07）", () => {
  beforeEach(() => {
    mockTheme = "light";
  });
  afterEach(() => {
    cleanup();
  });

  it("input_reference: null 渲染不崩溃且三区块与候选完整就位（修复前 :1669 抛 raw is not iterable）", () => {
    renderPanel(outputDisplayConfig({ input_reference: null }));
    expect(screen.getByText("输出方式")).toBeTruthy();
    expect(screen.getByText("输出内容（引用）")).toBeTruthy();
    expect(screen.getByText("展示标题")).toBeTruthy();
    expect(screen.getByTitle("情报分析（analysis_result）")).toBeTruthy();
  });

  it("output_mode: null 回落缺省：仅「屏幕显示」勾选（返回值恒非空）", () => {
    renderPanel(outputDisplayConfig({ output_mode: null }));
    expect(modeCheckbox("屏幕显示").checked).toBe(true);
    expect(modeCheckbox("输出为文件").checked).toBe(false);
  });
});
