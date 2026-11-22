// =============================================================================
// 变量引用选择器引用行为测试（canvas/__tests__/VariableReferenceSelector.reference.test.tsx）
//
// 留待非沙箱环境执行：cd web && npx vitest run VariableReferenceSelector.reference
//
// 覆盖用例：
//   T7.7  targetNodeId 时仅展示上游候选 / 未传入时回落全集
//         两种空态引导 / 三类警告 / 非法值保留
//   T5.3  兼容性（未传 targetNodeId 时行为不变）
//   T5.2  形态 B 上游输入参数候选（CHG-F04）：分组并列可见 / 寻址键回填 /
//         形态 B 警告文案派生 / 无 alias 不出现 / 仅有输入候选不误报 /
//         形态 A 文案零回归
// =============================================================================

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent } from "@testing-library/react";
import type { ReactNode } from "react";
import VariableReferenceSelector from "../VariableReferenceSelector";
import type { CanvasNode, CanvasEdge, WorkflowCanvasState } from "../canvasTypes";
import type { CanvasNodeData } from "../canvasTypes";
import type {
  AgentInputEntry,
  NodeType,
  NodeConfiguration,
} from "../../workflow-definition/definitionTypes";

// 主题 mock
let mockTheme: "light" | "dark" = "light";
vi.mock("../../theme/themeMode", async () => {
  const actual = await vi.importActual<typeof import("../../theme/themeMode")>(
    "../../theme/themeMode",
  );
  return { ...actual, useThemeMode: () => mockTheme };
});

// ---------------------------------------------------------------------------
// 测试数据工厂
// ---------------------------------------------------------------------------

function makeNode(
  nodeId: string,
  name: string,
  nodeType: NodeType,
  outputName?: string,
): CanvasNode {
  const data: CanvasNodeData = {
    node_id: nodeId,
    name,
    node_type: nodeType,
    configuration: makeMinimalConfig(nodeType),
  };
  if (outputName !== undefined) {
    data.output_name = outputName;
  }
  return { id: nodeId, type: nodeType, position: { x: 0, y: 0 }, data };
}

function makeMinimalConfig(nodeType: NodeType): NodeConfiguration {
  switch (nodeType) {
    case "trigger":
      return { node_type: "trigger", trigger_type: "manual", input_template: {} };
    case "agent":
      return { node_type: "agent", model_identifier: "m", system_prompt: "", available_tools: [] };
    case "tool":
      return { node_type: "tool", tool_identifier: "t", tool_source: "local", argument_mapping: {}, static_arguments: {} };
    case "data_source":
      return { node_type: "data_source", connection_reference: "c", query: "" };
    case "control_flow":
      return { node_type: "control_flow", control_flow_kind: "conditional_branch", conditions: [], parallel_failure_strategy: "fast_fail", review_timeout_action: "reject" };
    case "subworkflow":
      return { node_type: "subworkflow", subworkflow_definition_id: "w", input_mapping: {}, output_mapping: {}, context_strategy: "inherit", max_nesting_depth: 3 };
    case "dynamic_injector":
      return { node_type: "dynamic_injector", injection_condition: "", node_templates: [], max_injection_count: 1 };
    case "data_transform":
      return { node_type: "data_transform", operation: "map", input_dataset_reference: "", params: {} };
    case "output_display":
      return { node_type: "output_display" };
    case "vector_knowledge_base":
      return { node_type: "vector_knowledge_base", knowledge_base_id: "", query_mode: "manual", top_k: 5 };
    case "llm":
      return { node_type: "llm", model_identifier: "m", system_prompt: "", user_prompt: "" };
    case "code":
      return { node_type: "code", language: "python", code: "pass", entry_function: "main" };
  }
}

function makeEdge(source: string, target: string): CanvasEdge {
  return { id: `e-${source}-${target}`, source, target };
}

function makeState(nodes: CanvasNode[], edges: CanvasEdge[]): WorkflowCanvasState {
  return { nodes, edges };
}

function renderSelector(props: {
  canvasState: WorkflowCanvasState;
  value?: string;
  onChange?: (v: string) => void;
  targetNodeId?: string;
}) {
  const onChange = props.onChange ?? vi.fn();
  const { container, unmount } = render(
    <VariableReferenceSelector
      canvasState={props.canvasState}
      value={props.value ?? ""}
      onChange={onChange}
      targetNodeId={props.targetNodeId}
    /> as ReactNode,
  );
  return { container, onChange, unmount };
}

// ---------------------------------------------------------------------------
// T7.7 targetNodeId 时仅展示上游候选
// ---------------------------------------------------------------------------

describe("VariableReferenceSelector targetNodeId 上游限定（T7.7）", () => {
  beforeEach(() => {
    mockTheme = "light";
  });

  it("传入 targetNodeId 时下拉仅展示上游可引用候选", () => {
    const state = makeState(
      [
        makeNode("n1", "上游Agent", "agent", "up_out"),
        makeNode("n2", "下游Agent", "agent", "down_out"),
        makeNode("n3", "目标Tool", "tool"),
      ],
      [makeEdge("n1", "n3"), makeEdge("n3", "n2")],
    );
    const { container } = renderSelector({ canvasState: state, targetNodeId: "n3" });
    const input = container.querySelector(".wfp-vrs-input") as HTMLInputElement;
    fireEvent.focus(input);
    const options = container.querySelectorAll(".wfp-vrs-option");
    expect(options).toHaveLength(1);
    expect(options[0].textContent).toContain("up_out");
    expect(options[0].textContent).not.toContain("down_out");
  });

  it("控制流上游节点被过滤（referable=false）", () => {
    const state = makeState(
      [
        makeNode("n1", "控制流", "control_flow", "cf_out"),
        makeNode("n2", "上游Agent", "agent", "agent_out"),
        makeNode("n3", "目标", "tool"),
      ],
      [makeEdge("n1", "n3"), makeEdge("n2", "n3")],
    );
    const { container } = renderSelector({ canvasState: state, targetNodeId: "n3" });
    const input = container.querySelector(".wfp-vrs-input") as HTMLInputElement;
    fireEvent.focus(input);
    const options = container.querySelectorAll(".wfp-vrs-option");
    expect(options).toHaveLength(1);
    expect(options[0].textContent).toContain("agent_out");
  });

  it("候选展示类别徽标（category）", () => {
    const state = makeState(
      [
        makeNode("n1", "上游Agent", "agent", "up_out"),
        makeNode("n2", "目标", "tool"),
      ],
      [makeEdge("n1", "n2")],
    );
    const { container } = renderSelector({ canvasState: state, targetNodeId: "n2" });
    const input = container.querySelector(".wfp-vrs-input") as HTMLInputElement;
    fireEvent.focus(input);
    const category = container.querySelector(".wfp-vrs-option-category");
    expect(category).not.toBeNull();
    expect(category?.textContent).toBe("结构化对象");
  });
});

// ---------------------------------------------------------------------------
// T5.3 未传入 targetNodeId 时回落全集（兼容性）
// ---------------------------------------------------------------------------

describe("VariableReferenceSelector 未传 targetNodeId 回落全集（T5.3）", () => {
  beforeEach(() => {
    mockTheme = "light";
  });

  it("未传 targetNodeId 时展示全部命名输出候选", () => {
    const state = makeState(
      [
        makeNode("n1", "节点A", "agent", "a_out"),
        makeNode("n2", "节点B", "tool", "b_out"),
      ],
      [],
    );
    const { container } = renderSelector({ canvasState: state });
    const input = container.querySelector(".wfp-vrs-input") as HTMLInputElement;
    fireEvent.focus(input);
    const options = container.querySelectorAll(".wfp-vrs-option");
    expect(options).toHaveLength(2);
  });
});

// ---------------------------------------------------------------------------
// T7.7 两种空态引导
// ---------------------------------------------------------------------------

describe("VariableReferenceSelector 空态引导（T7.7）", () => {
  beforeEach(() => {
    mockTheme = "light";
  });

  it("有 targetNodeId 但无上游节点时展示'暂无上游节点'提示", () => {
    const state = makeState(
      [makeNode("n1", "目标", "tool")],
      [],
    );
    const { container } = renderSelector({ canvasState: state, targetNodeId: "n1" });
    const input = container.querySelector(".wfp-vrs-input") as HTMLInputElement;
    fireEvent.focus(input);
    const empty = container.querySelector(".wfp-vrs-empty");
    expect(empty).not.toBeNull();
    expect(empty?.textContent).toContain("暂无上游节点");
  });

  it("有上游节点但均未声明命名输出时展示'尚未声明命名输出'提示", () => {
    const state = makeState(
      [
        makeNode("n1", "无输出上游", "agent"),
        makeNode("n2", "目标", "tool"),
      ],
      [makeEdge("n1", "n2")],
    );
    const { container } = renderSelector({ canvasState: state, targetNodeId: "n2" });
    const input = container.querySelector(".wfp-vrs-input") as HTMLInputElement;
    fireEvent.focus(input);
    const empty = container.querySelector(".wfp-vrs-empty");
    expect(empty).not.toBeNull();
    expect(empty?.textContent).toContain("尚未声明命名输出");
  });
});

// ---------------------------------------------------------------------------
// T7.7 三类警告
// ---------------------------------------------------------------------------

describe("VariableReferenceSelector 警告（T7.7）", () => {
  beforeEach(() => {
    mockTheme = "light";
  });

  it("引用不存在的输出时展示'引用的输出不存在'警告", () => {
    const state = makeState(
      [
        makeNode("n1", "上游", "agent", "up_out"),
        makeNode("n2", "目标", "tool"),
      ],
      [makeEdge("n1", "n2")],
    );
    const { container } = renderSelector({
      canvasState: state,
      targetNodeId: "n2",
      value: "nonexistent",
    });
    const warn = container.querySelector(".wfp-vrs-warn");
    expect(warn).not.toBeNull();
    expect(warn?.textContent).toBe("引用的输出不存在");
  });

  it("引用自身输出时展示'不能引用节点自身的输出'警告", () => {
    const state = makeState(
      [
        makeNode("n1", "目标", "tool", "self_out"),
        makeNode("n2", "上游", "agent", "up_out"),
      ],
      [makeEdge("n2", "n1")],
    );
    const { container } = renderSelector({
      canvasState: state,
      targetNodeId: "n1",
      value: "self_out",
    });
    const warn = container.querySelector(".wfp-vrs-warn");
    expect(warn).not.toBeNull();
    expect(warn?.textContent).toBe("不能引用节点自身的输出");
  });

  it("引用非上游输出时展示'引用的输出不在上游范围内'警告", () => {
    const state = makeState(
      [
        makeNode("n1", "上游", "agent", "up_out"),
        makeNode("n2", "目标", "tool"),
        makeNode("n3", "下游", "agent", "down_out"),
      ],
      [makeEdge("n1", "n2"), makeEdge("n2", "n3")],
    );
    const { container } = renderSelector({
      canvasState: state,
      targetNodeId: "n2",
      value: "down_out",
    });
    const warn = container.querySelector(".wfp-vrs-warn");
    expect(warn).not.toBeNull();
    expect(warn?.textContent).toBe("引用的输出不在上游范围内");
  });

  it("合法引用时不展示警告", () => {
    const state = makeState(
      [
        makeNode("n1", "上游", "agent", "up_out"),
        makeNode("n2", "目标", "tool"),
      ],
      [makeEdge("n1", "n2")],
    );
    const { container } = renderSelector({
      canvasState: state,
      targetNodeId: "n2",
      value: "up_out",
    });
    const warn = container.querySelector(".wfp-vrs-warn");
    expect(warn).toBeNull();
  });

  it("空值时不展示警告", () => {
    const state = makeState(
      [
        makeNode("n1", "上游", "agent", "up_out"),
        makeNode("n2", "目标", "tool"),
      ],
      [makeEdge("n1", "n2")],
    );
    const { container } = renderSelector({
      canvasState: state,
      targetNodeId: "n2",
      value: "",
    });
    const warn = container.querySelector(".wfp-vrs-warn");
    expect(warn).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// T7.7 非法值保留
// ---------------------------------------------------------------------------

describe("VariableReferenceSelector 非法值保留（T7.7）", () => {
  beforeEach(() => {
    mockTheme = "light";
  });

  it("非法引用值在 input 中保留（不被清除或改写）", () => {
    const state = makeState(
      [
        makeNode("n1", "上游", "agent", "up_out"),
        makeNode("n2", "目标", "tool"),
      ],
      [makeEdge("n1", "n2")],
    );
    const { container } = renderSelector({
      canvasState: state,
      targetNodeId: "n2",
      value: "nonexistent",
    });
    const input = container.querySelector(".wfp-vrs-input") as HTMLInputElement;
    expect(input.value).toBe("nonexistent");
  });

  it("选择候选后 onChange 收到对应 outputName", () => {
    const state = makeState(
      [
        makeNode("n1", "上游", "agent", "up_out"),
        makeNode("n2", "目标", "tool"),
      ],
      [makeEdge("n1", "n2")],
    );
    const onChange = vi.fn();
    const { container } = renderSelector({
      canvasState: state,
      targetNodeId: "n2",
      onChange,
    });
    const input = container.querySelector(".wfp-vrs-input") as HTMLInputElement;
    fireEvent.focus(input);
    const option = container.querySelector(".wfp-vrs-option") as HTMLButtonElement;
    fireEvent.mouseDown(option);
    expect(onChange).toHaveBeenCalledWith("up_out");
  });
});

// ---------------------------------------------------------------------------
// T5.2 / CHG-F04 形态 B 上游输入参数候选
// ---------------------------------------------------------------------------

/** 带输入绑定（argument_mapping）的工具节点（形态 B 候选来源） */
function makeBoundToolNode(
  nodeId: string,
  name: string,
  argumentMapping: Record<string, string>,
): CanvasNode {
  const base = makeNode(nodeId, name, "tool");
  return {
    ...base,
    data: {
      ...base.data,
      configuration: {
        node_type: "tool",
        tool_identifier: "t",
        tool_source: "local",
        argument_mapping: argumentMapping,
        static_arguments: {},
      },
    },
  };
}

/** 带 inputs 的 Agent 节点（agent 输入须声明 alias 才产出绑定） */
function makeAgentWithInputs(
  nodeId: string,
  name: string,
  inputs: AgentInputEntry[],
): CanvasNode {
  const base = makeNode(nodeId, name, "agent");
  return {
    ...base,
    data: {
      ...base.data,
      configuration: {
        node_type: "agent",
        model_identifier: "m",
        system_prompt: "",
        available_tools: [],
        inputs,
      },
    },
  };
}

describe("VariableReferenceSelector 形态 B 上游输入参数（T5.2 / CHG-F04）", () => {
  beforeEach(() => {
    mockTheme = "light";
  });

  it("上游命名输出与上游输入参数分组并列可见", () => {
    const state = makeState(
      [
        makeNode("n1", "上游Agent", "agent", "a_out"),
        makeBoundToolNode("n2", "中游工具", { query: "a_out" }),
        makeNode("n3", "目标Tool", "tool"),
      ],
      [makeEdge("n1", "n2"), makeEdge("n2", "n3")],
    );
    const { container } = renderSelector({ canvasState: state, targetNodeId: "n3" });
    const input = container.querySelector(".wfp-vrs-input") as HTMLInputElement;
    fireEvent.focus(input);
    const labels = container.querySelectorAll(".wfp-vrs-group-label");
    expect(labels).toHaveLength(2);
    expect(labels[0].textContent).toBe("上游命名输出");
    expect(labels[1].textContent).toBe("上游输入参数");
    const options = container.querySelectorAll(".wfp-vrs-option");
    expect(options).toHaveLength(2);
    expect(options[0].textContent).toContain("a_out");
    expect(options[1].textContent).toContain("n2::query");
    expect(options[1].textContent).toContain("中游工具");
    const category = options[1].querySelector(".wfp-vrs-option-category");
    expect(category?.textContent).toBe("输入参数");
  });

  it("选择输入参数候选后 onChange 收到寻址键 node_id::position", () => {
    const state = makeState(
      [
        makeNode("n1", "上游Agent", "agent", "a_out"),
        makeBoundToolNode("n2", "中游工具", { query: "a_out" }),
        makeNode("n3", "目标Tool", "tool"),
      ],
      [makeEdge("n1", "n2"), makeEdge("n2", "n3")],
    );
    const onChange = vi.fn();
    const { container } = renderSelector({
      canvasState: state,
      targetNodeId: "n3",
      onChange,
    });
    const input = container.querySelector(".wfp-vrs-input") as HTMLInputElement;
    fireEvent.focus(input);
    const options = container.querySelectorAll(".wfp-vrs-option");
    expect(options).toHaveLength(2);
    fireEvent.mouseDown(options[1] as HTMLButtonElement);
    expect(onChange).toHaveBeenCalledWith("n2::query");
  });

  it("形态 B 值按上游输入项语义派生三类警告文案", () => {
    const state = makeState(
      [
        makeNode("n1", "上游Agent", "agent", "a_out"),
        makeNode("n4", "范围外Agent", "agent", "far_out"),
        makeNode("n3", "目标Tool", "tool"),
      ],
      [makeEdge("n1", "n3")],
    );
    const first = renderSelector({
      canvasState: state,
      targetNodeId: "n3",
      value: "n1::missing",
    });
    expect(first.container.querySelector(".wfp-vrs-warn")?.textContent).toBe(
      "引用的上游输入项不存在",
    );
    first.unmount();

    const second = renderSelector({
      canvasState: state,
      targetNodeId: "n3",
      value: "n4::x",
    });
    expect(second.container.querySelector(".wfp-vrs-warn")?.textContent).toBe(
      "引用的上游输入项不在上游范围内",
    );
    second.unmount();

    const third = renderSelector({
      canvasState: state,
      targetNodeId: "n3",
      value: "n3::q",
    });
    expect(third.container.querySelector(".wfp-vrs-warn")?.textContent).toBe(
      "不能引用节点自身的输入项",
    );
  });

  it("未声明 alias 的 Agent 输入项不出现在候选中", () => {
    const state = makeState(
      [
        makeNode("n1", "上游Agent", "agent", "up_out"),
        makeAgentWithInputs("n2", "中游Agent", [
          { reference: "up_out", alias: "q" },
          { reference: "up_out" },
        ]),
        makeNode("n3", "目标Tool", "tool"),
      ],
      [makeEdge("n1", "n2"), makeEdge("n2", "n3")],
    );
    const { container } = renderSelector({ canvasState: state, targetNodeId: "n3" });
    const input = container.querySelector(".wfp-vrs-input") as HTMLInputElement;
    fireEvent.focus(input);
    const options = container.querySelectorAll(".wfp-vrs-option");
    const boundOptions = Array.from(options).filter((option) =>
      option.textContent.includes("n2::"),
    );
    expect(boundOptions).toHaveLength(1);
    expect(boundOptions[0].textContent).toContain("n2::q");
  });

  it("仅有输入参数候选时不误报「尚未声明命名输出」", () => {
    // 环结构（目标自身为绑定 owner）：目标自身的命名输出不入其上游候选，
    // 但其上游输入绑定产出输入参数候选 → 输出候选空 + 输入候选非空（真实数据）
    const state = makeState(
      [
        makeNode("n1", "目标Agent", "agent", "x"),
        makeBoundToolNode("n2", "环上工具", { q: "x" }),
      ],
      [makeEdge("n1", "n2"), makeEdge("n2", "n1")],
    );
    const { container } = renderSelector({ canvasState: state, targetNodeId: "n1" });
    const input = container.querySelector(".wfp-vrs-input") as HTMLInputElement;
    fireEvent.focus(input);
    expect(container.querySelector(".wfp-vrs-empty")).toBeNull();
    expect(container.textContent).not.toContain("尚未声明命名输出");
    const options = container.querySelectorAll(".wfp-vrs-option");
    expect(options).toHaveLength(1);
    expect(options[0].textContent).toContain("n2::q");
    const labels = container.querySelectorAll(".wfp-vrs-group-label");
    expect(labels).toHaveLength(1);
    expect(labels[0].textContent).toBe("上游输入参数");
  });

  it("形态 A 值仍使用既有输出类警告文案（零回归）", () => {
    const state = makeState(
      [
        makeNode("n1", "上游", "agent", "up_out"),
        makeNode("n2", "目标", "tool"),
      ],
      [makeEdge("n1", "n2")],
    );
    const first = renderSelector({
      canvasState: state,
      targetNodeId: "n2",
      value: "nonexistent",
    });
    expect(first.container.querySelector(".wfp-vrs-warn")?.textContent).toBe(
      "引用的输出不存在",
    );
    first.unmount();

    // 消歧只看首段（DC-01）：`::` 出现在字段路径段 → 仍按形态 A 判定
    const second = renderSelector({
      canvasState: state,
      targetNodeId: "n2",
      value: "up_out.a::b",
    });
    expect(second.container.querySelector(".wfp-vrs-warn")).toBeNull();
    second.unmount();

    const third = renderSelector({
      canvasState: state,
      targetNodeId: "n2",
      value: "zz.a::b",
    });
    expect(third.container.querySelector(".wfp-vrs-warn")?.textContent).toBe(
      "引用的输出不存在",
    );
  });
});

// ---------------------------------------------------------------------------
// fix_agent_null_length tasks v3 §2.2 组件级归因实验：渲染链复现
// （用户堆栈：VariableReferenceSelector ← AgentInputListEditor ←
//   NodeConfigurationPanel，崩溃链 useMemo :108 → collectUpstreamInputCandidates
//   :212 → collectNodeInputBindings :100；修复前失败 / 修复后通过成对留痕）
// ---------------------------------------------------------------------------

describe("VariableReferenceSelector null-alias 上游渲染链归因（fix_agent_null_length G1 §2.2）", () => {
  beforeEach(() => {
    mockTheme = "light";
  });

  it("上游 agent inputs 条目 alias=null：渲染不崩溃，输入框正常呈现", () => {
    const state = makeState(
      [
        makeAgentWithInputs("u1", "上游Agent", [
          { alias: null, reference: "n1.out" },
        ] as unknown as AgentInputEntry[]),
        makeAgentWithInputs("t1", "目标Agent", []),
      ],
      [makeEdge("u1", "t1")],
    );
    // 修复前：渲染抛 TypeError: Cannot read properties of null (reading 'length')
    //（useMemo :108 → :212 → :100，与用户控制台组件树链一致）；修复后：安全渲染
    const { container } = renderSelector({
      canvasState: state,
      targetNodeId: "t1",
    });
    expect(container.querySelector(".wfp-vrs-input")).toBeTruthy();
  });

  it("合法对照：上游 agent alias 非空字符串时渲染正常（INV-02 基线）", () => {
    const state = makeState(
      [
        makeAgentWithInputs("u1", "上游Agent", [
          { alias: "q", reference: "n1.out" },
        ]),
        makeAgentWithInputs("t1", "目标Agent", []),
      ],
      [makeEdge("u1", "t1")],
    );
    const { container } = renderSelector({
      canvasState: state,
      targetNodeId: "t1",
    });
    expect(container.querySelector(".wfp-vrs-input")).toBeTruthy();
  });
});