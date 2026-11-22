// =============================================================================
// 导出期引用校验单元测试
// （__tests__/canvasToYamlExporter.referenceIssues.test.ts）
//
// 留待非沙箱环境执行：cd web && npx vitest run canvasToYamlExporter.referenceIssues
//
// 覆盖用例：
//   T10.3 collectReferenceIssues 汇总非法引用
//         - tool.argument_mapping 中的非法引用
//         - subworkflow.input_mapping 中的非法引用
//         - 合法引用不产生 issue
//         - 空引用跳过（不产生 issue）
//         - 原值不被改写
//   T12.4 YAML 兼容性（引用原值在 issue 中保留）
// =============================================================================

import { describe, it, expect } from "vitest";
import { collectReferenceIssues } from "../workflow-definition/canvasToYamlExporter";
import type { CanvasNode, CanvasEdge, WorkflowCanvasState } from "../canvas/canvasTypes";
import type { CanvasNodeData } from "../canvas/canvasTypes";
import type {
  NodeType,
  NodeConfiguration,
  ToolNodeConfiguration,
  SubWorkflowNodeConfiguration,
} from "../workflow-definition/definitionTypes";

// ---------------------------------------------------------------------------
// 测试数据工厂
// ---------------------------------------------------------------------------

function makeNode(
  nodeId: string,
  name: string,
  nodeType: NodeType,
  outputName?: string,
  configuration?: NodeConfiguration,
): CanvasNode {
  const data: CanvasNodeData = {
    node_id: nodeId,
    name,
    node_type: nodeType,
    configuration: configuration ?? makeMinimalConfig(nodeType),
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

function makeToolNode(
  nodeId: string,
  name: string,
  argumentMapping: Record<string, string>,
): CanvasNode {
  const config: ToolNodeConfiguration = {
    node_type: "tool",
    tool_identifier: "t",
    tool_source: "local",
    argument_mapping: argumentMapping,
    static_arguments: {},
  };
  return makeNode(nodeId, name, "tool", undefined, config);
}

function makeSubWorkflowNode(
  nodeId: string,
  name: string,
  inputMapping: Record<string, string>,
): CanvasNode {
  const config: SubWorkflowNodeConfiguration = {
    node_type: "subworkflow",
    subworkflow_definition_id: "w",
    input_mapping: inputMapping,
    output_mapping: {},
    context_strategy: "inherit",
    max_nesting_depth: 3,
  };
  return makeNode(nodeId, name, "subworkflow", undefined, config);
}

// ---------------------------------------------------------------------------
// T10.3 tool.argument_mapping 非法引用
// ---------------------------------------------------------------------------

describe("collectReferenceIssues tool.argument_mapping（T10.3）", () => {
  it("引用不存在的输出 → notExist issue", () => {
    const state = makeState(
      [
        makeNode("n1", "上游", "agent", "up_out"),
        makeToolNode("n2", "目标", { arg1: "nonexistent" }),
      ],
      [makeEdge("n1", "n2")],
    );
    const issues = collectReferenceIssues(state);
    expect(issues).toHaveLength(1);
    expect(issues[0].nodeId).toBe("n2");
    expect(issues[0].field).toBe("argument_mapping");
    expect(issues[0].key).toBe("arg1");
    expect(issues[0].reference).toBe("nonexistent");
    expect(issues[0].message).toBe("引用的输出不存在");
  });

  it("引用非上游输出 → notUpstream issue", () => {
    const state = makeState(
      [
        makeNode("n1", "上游", "agent", "up_out"),
        makeToolNode("n2", "目标", { arg1: "up_out" }),
        makeNode("n3", "下游", "agent", "down_out"),
      ],
      [makeEdge("n1", "n2"), makeEdge("n2", "n3")],
    );
    const issues = collectReferenceIssues(state);
    expect(issues).toHaveLength(0);
  });

  it("引用下游输出 → notUpstream issue", () => {
    const state = makeState(
      [
        makeNode("n1", "上游", "agent", "up_out"),
        makeToolNode("n2", "目标", { arg1: "down_out" }),
        makeNode("n3", "下游", "agent", "down_out"),
      ],
      [makeEdge("n1", "n2"), makeEdge("n2", "n3")],
    );
    const issues = collectReferenceIssues(state);
    expect(issues).toHaveLength(1);
    expect(issues[0].message).toBe("引用的输出不在上游范围内");
  });

  it("引用自身输出 → selfReference issue", () => {
    const state = makeState(
      [
        makeNode("n1", "上游", "agent", "up_out"),
        makeToolNode("n2", "目标", { arg1: "self_out" }),
      ],
      [makeEdge("n1", "n2")],
    );
    state.nodes[1].data.output_name = "self_out";
    const issues = collectReferenceIssues(state);
    expect(issues).toHaveLength(1);
    expect(issues[0].message).toBe("不能引用节点自身的输出");
  });

  it("合法引用不产生 issue", () => {
    const state = makeState(
      [
        makeNode("n1", "上游", "agent", "up_out"),
        makeToolNode("n2", "目标", { arg1: "up_out" }),
      ],
      [makeEdge("n1", "n2")],
    );
    const issues = collectReferenceIssues(state);
    expect(issues).toHaveLength(0);
  });

  it("空引用跳过（不产生 issue）", () => {
    const state = makeState(
      [
        makeNode("n1", "上游", "agent", "up_out"),
        makeToolNode("n2", "目标", { arg1: "", arg2: "   " }),
      ],
      [makeEdge("n1", "n2")],
    );
    const issues = collectReferenceIssues(state);
    expect(issues).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// T10.3 subworkflow.input_mapping 非法引用
// ---------------------------------------------------------------------------

describe("collectReferenceIssues subworkflow.input_mapping（T10.3）", () => {
  it("引用不存在的输出 → notExist issue", () => {
    const state = makeState(
      [
        makeNode("n1", "上游", "agent", "up_out"),
        makeSubWorkflowNode("n2", "子工作流", { in1: "nonexistent" }),
      ],
      [makeEdge("n1", "n2")],
    );
    const issues = collectReferenceIssues(state);
    expect(issues).toHaveLength(1);
    expect(issues[0].field).toBe("input_mapping");
    expect(issues[0].key).toBe("in1");
    expect(issues[0].message).toBe("引用的输出不存在");
  });

  it("合法引用不产生 issue", () => {
    const state = makeState(
      [
        makeNode("n1", "上游", "agent", "up_out"),
        makeSubWorkflowNode("n2", "子工作流", { in1: "up_out" }),
      ],
      [makeEdge("n1", "n2")],
    );
    const issues = collectReferenceIssues(state);
    expect(issues).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// T10.3 多节点汇总
// ---------------------------------------------------------------------------

describe("collectReferenceIssues 多节点汇总（T10.3）", () => {
  it("多个节点的多个非法引用全部汇总", () => {
    const state = makeState(
      [
        makeNode("n1", "上游", "agent", "up_out"),
        makeToolNode("n2", "Tool1", { a1: "nonexistent1", a2: "nonexistent2" }),
        makeToolNode("n3", "Tool2", { b1: "nonexistent3" }),
      ],
      [makeEdge("n1", "n2"), makeEdge("n1", "n3")],
    );
    const issues = collectReferenceIssues(state);
    expect(issues).toHaveLength(3);
    const keys = issues.map((i) => i.key).sort();
    expect(keys).toEqual(["a1", "a2", "b1"]);
  });

  it("无映射的节点不产生 issue", () => {
    const state = makeState(
      [
        makeNode("n1", "Agent", "agent", "up_out"),
        makeNode("n2", "触发器", "trigger", "trig_out"),
      ],
      [],
    );
    const issues = collectReferenceIssues(state);
    expect(issues).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// T12.4 原值不被改写 / YAML 兼容性
// ---------------------------------------------------------------------------

describe("原值不被改写（T12.4）", () => {
  it("issue.reference 与原始映射值完全一致（含点号路径）", () => {
    const state = makeState(
      [
        makeNode("n1", "上游", "agent", "up_out"),
        makeToolNode("n2", "目标", { arg1: "nonexistent.field1.field2" }),
      ],
      [makeEdge("n1", "n2")],
    );
    const issues = collectReferenceIssues(state);
    expect(issues).toHaveLength(1);
    expect(issues[0].reference).toBe("nonexistent.field1.field2");
  });

  it("原始映射值在收集 issue 后不被修改", () => {
    const state = makeState(
      [
        makeNode("n1", "上游", "agent", "up_out"),
        makeToolNode("n2", "目标", { arg1: "nonexistent" }),
      ],
      [makeEdge("n1", "n2")],
    );
    collectReferenceIssues(state);
    const toolConfig = state.nodes[1].data.configuration as ToolNodeConfiguration;
    expect(toolConfig.argument_mapping.arg1).toBe("nonexistent");
  });

  it("issue 包含完整的节点定位信息（nodeId / nodeName / field / key）", () => {
    const state = makeState(
      [
        makeNode("n1", "上游", "agent", "up_out"),
        makeToolNode("tool-node", "工具节点", { my_arg: "nonexistent" }),
      ],
      [makeEdge("n1", "tool-node")],
    );
    const issues = collectReferenceIssues(state);
    expect(issues).toHaveLength(1);
    expect(issues[0].nodeId).toBe("tool-node");
    expect(issues[0].nodeName).toBe("工具节点");
    expect(issues[0].field).toBe("argument_mapping");
    expect(issues[0].key).toBe("my_arg");
  });
});