// =============================================================================
// 输入引用合法性校验单元测试（canvas/__tests__/validateReference.test.ts）
//
// 留待非沙箱环境执行：cd web && npx vitest run validateReference
//
// 覆盖用例（T6.3 五态判别）：
//   TC-VR-01 unbound（空串 / 纯空白）
//   TC-VR-02 notExist（引用的 output_name 无节点声明）
//   TC-VR-03 selfReference（引用自身输出）
//   TC-VR-04 notUpstream（引用存在但非上游）
//   TC-VR-05 notUpstream（引用存在且为上游但契约不可引用，如 control_flow）
//   TC-VR-06 valid（合法引用）
//   TC-VR-07 点号字段路径（取点号前标识为 output_name）
//   TC-VR-08 目标节点被删除后引用变为 notExist
//   TC-VR-09 形态 B（upstream_input_ref：`node_id::输入位置名[.字段路径]`；
//         规则 2′~5′ 前端映射 + 消歧只看首段）
// =============================================================================

import { describe, it, expect } from "vitest";
import { validateReference } from "../nodeIoTopology";
import { NODE_OUTPUT_CONTRACTS } from "../nodeOutputContracts";
import type { CanvasNode, CanvasEdge, WorkflowCanvasState } from "../canvasTypes";
import type { CanvasNodeData } from "../canvasTypes";
import type { NodeType, NodeConfiguration } from "../../workflow-definition/definitionTypes";

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

// ---------------------------------------------------------------------------
// TC-VR-01 unbound
// ---------------------------------------------------------------------------

describe("validateReference unbound（TC-VR-01）", () => {
  const state = makeState(
    [makeNode("n1", "A", "agent", "a_out"), makeNode("n2", "目标", "tool")],
    [makeEdge("n1", "n2")],
  );

  it("空串返回 unbound", () => {
    expect(validateReference("", "n2", state, NODE_OUTPUT_CONTRACTS)).toEqual({ kind: "unbound" });
  });

  it("纯空白返回 unbound", () => {
    expect(validateReference("   ", "n2", state, NODE_OUTPUT_CONTRACTS)).toEqual({ kind: "unbound" });
  });

  it("制表符+空格混合返回 unbound", () => {
    expect(validateReference("\t  \t", "n2", state, NODE_OUTPUT_CONTRACTS)).toEqual({ kind: "unbound" });
  });
});

// ---------------------------------------------------------------------------
// TC-VR-02 notExist
// ---------------------------------------------------------------------------

describe("validateReference notExist（TC-VR-02）", () => {
  const state = makeState(
    [makeNode("n1", "A", "agent", "a_out"), makeNode("n2", "目标", "tool")],
    [makeEdge("n1", "n2")],
  );

  it("引用不存在的 output_name 返回 notExist", () => {
    const result = validateReference("nonexistent", "n2", state, NODE_OUTPUT_CONTRACTS);
    expect(result).toEqual({ kind: "notExist", outputName: "nonexistent" });
  });

  it("引用存在节点但 output_name 未声明返回 notExist", () => {
    const state2 = makeState(
      [makeNode("n1", "A", "agent"), makeNode("n2", "目标", "tool")],
      [makeEdge("n1", "n2")],
    );
    const result = validateReference("a_out", "n2", state2, NODE_OUTPUT_CONTRACTS);
    expect(result).toEqual({ kind: "notExist", outputName: "a_out" });
  });
});

// ---------------------------------------------------------------------------
// TC-VR-03 selfReference
// ---------------------------------------------------------------------------

describe("validateReference selfReference（TC-VR-03）", () => {
  const state = makeState(
    [makeNode("n1", "目标", "tool", "self_out"), makeNode("n2", "上游", "agent", "up_out")],
    [makeEdge("n2", "n1")],
  );

  it("引用自身 output_name 返回 selfReference", () => {
    const result = validateReference("self_out", "n1", state, NODE_OUTPUT_CONTRACTS);
    expect(result).toEqual({ kind: "selfReference", outputName: "self_out" });
  });
});

// ---------------------------------------------------------------------------
// TC-VR-04 notUpstream（非上游）
// ---------------------------------------------------------------------------

describe("validateReference notUpstream 非上游（TC-VR-04）", () => {
  const state = makeState(
    [
      makeNode("n1", "上游", "agent", "up_out"),
      makeNode("n2", "目标", "tool"),
      makeNode("n3", "下游", "agent", "down_out"),
    ],
    [makeEdge("n1", "n2"), makeEdge("n2", "n3")],
  );

  it("引用下游节点输出返回 notUpstream", () => {
    const result = validateReference("down_out", "n2", state, NODE_OUTPUT_CONTRACTS);
    expect(result).toEqual({ kind: "notUpstream", outputName: "down_out" });
  });

  it("引用无连接的节点输出返回 notUpstream", () => {
    const state2 = makeState(
      [
        makeNode("n1", "孤立", "agent", "iso_out"),
        makeNode("n2", "目标", "tool"),
      ],
      [],
    );
    const result = validateReference("iso_out", "n2", state2, NODE_OUTPUT_CONTRACTS);
    expect(result).toEqual({ kind: "notUpstream", outputName: "iso_out" });
  });
});

// ---------------------------------------------------------------------------
// TC-VR-05 notUpstream（契约不可引用）
// ---------------------------------------------------------------------------

describe("validateReference notUpstream 契约不可引用（TC-VR-05）", () => {
  const state = makeState(
    [
      makeNode("n1", "控制流", "control_flow", "cf_out"),
      makeNode("n2", "目标", "tool"),
    ],
    [makeEdge("n1", "n2")],
  );

  it("引用上游控制流节点输出返回 notUpstream（referable=false）", () => {
    const result = validateReference("cf_out", "n2", state, NODE_OUTPUT_CONTRACTS);
    expect(result).toEqual({ kind: "notUpstream", outputName: "cf_out" });
  });
});

// ---------------------------------------------------------------------------
// TC-VR-06 valid
// ---------------------------------------------------------------------------

describe("validateReference valid（TC-VR-06）", () => {
  const state = makeState(
    [
      makeNode("n1", "上游Agent", "agent", "up_out"),
      makeNode("n2", "目标", "tool"),
    ],
    [makeEdge("n1", "n2")],
  );

  it("合法引用返回 valid", () => {
    const result = validateReference("up_out", "n2", state, NODE_OUTPUT_CONTRACTS);
    expect(result).toEqual({ kind: "valid" });
  });

  it("多跳上游合法引用返回 valid", () => {
    const state2 = makeState(
      [
        makeNode("n1", "远上游", "trigger", "trig_out"),
        makeNode("n2", "中间", "agent", "mid_out"),
        makeNode("n3", "目标", "tool"),
      ],
      [makeEdge("n1", "n2"), makeEdge("n2", "n3")],
    );
    const result = validateReference("trig_out", "n3", state2, NODE_OUTPUT_CONTRACTS);
    expect(result).toEqual({ kind: "valid" });
  });
});

// ---------------------------------------------------------------------------
// TC-VR-07 点号字段路径
// ---------------------------------------------------------------------------

describe("validateReference 点号字段路径（TC-VR-07）", () => {
  const state = makeState(
    [
      makeNode("n1", "上游Agent", "agent", "up_out"),
      makeNode("n2", "目标", "tool"),
    ],
    [makeEdge("n1", "n2")],
  );

  it("点号路径取首段为 output_name，合法时返回 valid", () => {
    const result = validateReference("up_out.field1", "n2", state, NODE_OUTPUT_CONTRACTS);
    expect(result).toEqual({ kind: "valid" });
  });

  it("点号路径首段不存在时返回 notExist", () => {
    const result = validateReference("nonexistent.field", "n2", state, NODE_OUTPUT_CONTRACTS);
    expect(result).toEqual({ kind: "notExist", outputName: "nonexistent" });
  });

  it("多段点号路径取首段", () => {
    const result = validateReference("up_out.a.b.c", "n2", state, NODE_OUTPUT_CONTRACTS);
    expect(result).toEqual({ kind: "valid" });
  });
});

// ---------------------------------------------------------------------------
// TC-VR-08 目标节点被删除
// ---------------------------------------------------------------------------

describe("validateReference 目标节点被删除（TC-VR-08）", () => {
  it("引用的节点被删除后返回 notExist", () => {
    const stateBefore = makeState(
      [
        makeNode("n1", "上游", "agent", "up_out"),
        makeNode("n2", "目标", "tool"),
      ],
      [makeEdge("n1", "n2")],
    );
    expect(validateReference("up_out", "n2", stateBefore, NODE_OUTPUT_CONTRACTS)).toEqual({ kind: "valid" });

    const stateAfter = makeState(
      [makeNode("n2", "目标", "tool")],
      [],
    );
    expect(validateReference("up_out", "n2", stateAfter, NODE_OUTPUT_CONTRACTS)).toEqual({
      kind: "notExist",
      outputName: "up_out",
    });
  });
});

// ---------------------------------------------------------------------------
// TC-VR-09 形态 B（upstream_input_ref：node_id::输入位置名[.字段路径]）
// ---------------------------------------------------------------------------

/** 构造带显式配置的画布节点（TC-VR-09 夹具；不侵入既有工厂） */
function makeNodeWithConfig(
  nodeId: string,
  name: string,
  nodeType: NodeType,
  configuration: NodeConfiguration,
): CanvasNode {
  const data: CanvasNodeData = {
    node_id: nodeId,
    name,
    node_type: nodeType,
    configuration,
  };
  return { id: nodeId, type: nodeType, position: { x: 0, y: 0 }, data };
}

/** TC-VR-09 标准场景：agent_a(agent_out) → sub_u(input_mapping) → tool_t（目标） */
function makeFormBState(): WorkflowCanvasState {
  return makeState(
    [
      makeNode("agent_a", "上游Agent", "agent", "agent_out"),
      makeNodeWithConfig("sub_u", "子工作流", "subworkflow", {
        node_type: "subworkflow",
        subworkflow_definition_id: "w",
        input_mapping: {
          user_input: "agent_out",
          empty_slot: "",
          form_b_slot: "x::y",
        },
        output_mapping: {},
        context_strategy: "inherit",
        max_nesting_depth: 3,
      }),
      makeNode("tool_t", "目标Tool", "tool"),
    ],
    [makeEdge("agent_a", "sub_u"), makeEdge("sub_u", "tool_t")],
  );
}

describe("validateReference 形态 B（TC-VR-09，upstream_input_ref）", () => {
  it("合法输入位置引用返回 valid", () => {
    const result = validateReference(
      "sub_u::user_input",
      "tool_t",
      makeFormBState(),
      NODE_OUTPUT_CONTRACTS,
    );
    expect(result).toEqual({ kind: "valid" });
  });

  it("带字段路径的合法引用返回 valid（路径段不参与判定）", () => {
    const result = validateReference(
      "sub_u::user_input.text",
      "tool_t",
      makeFormBState(),
      NODE_OUTPUT_CONTRACTS,
    );
    expect(result).toEqual({ kind: "valid" });
  });

  it("来源节点不存在返回 notExist", () => {
    const result = validateReference(
      "ghost_node::x",
      "tool_t",
      makeFormBState(),
      NODE_OUTPUT_CONTRACTS,
    );
    expect(result).toEqual({ kind: "notExist", outputName: "ghost_node" });
  });

  it("来源节点为自身返回 selfReference", () => {
    const result = validateReference(
      "sub_u::user_input",
      "sub_u",
      makeFormBState(),
      NODE_OUTPUT_CONTRACTS,
    );
    expect(result).toEqual({ kind: "selfReference", outputName: "sub_u" });
  });

  it("来源节点非上游返回 notUpstream", () => {
    const state = makeState(
      [
        makeNodeWithConfig("sub_d", "下游子工作流", "subworkflow", {
          node_type: "subworkflow",
          subworkflow_definition_id: "w",
          input_mapping: { later: "agent_out" },
          output_mapping: {},
          context_strategy: "inherit",
          max_nesting_depth: 3,
        }),
        makeNode("tool_t", "目标Tool", "tool"),
      ],
      [makeEdge("tool_t", "sub_d")],
    );
    const result = validateReference("sub_d::later", "tool_t", state, NODE_OUTPUT_CONTRACTS);
    expect(result).toEqual({ kind: "notUpstream", outputName: "sub_d" });
  });

  it("来源节点存在但无该位置名返回 notExist", () => {
    const result = validateReference(
      "sub_u::ghost_position",
      "tool_t",
      makeFormBState(),
      NODE_OUTPUT_CONTRACTS,
    );
    expect(result).toEqual({ kind: "notExist", outputName: "sub_u" });
  });

  it("位置绑定为空返回 notExist（规则 5′ 前端映射）", () => {
    const result = validateReference(
      "sub_u::empty_slot",
      "tool_t",
      makeFormBState(),
      NODE_OUTPUT_CONTRACTS,
    );
    expect(result).toEqual({ kind: "notExist", outputName: "sub_u" });
  });

  it("位置绑定为形态 B 返回 notExist（规则 5′ 不递归）", () => {
    const result = validateReference(
      "sub_u::form_b_slot",
      "tool_t",
      makeFormBState(),
      NODE_OUTPUT_CONTRACTS,
    );
    expect(result).toEqual({ kind: "notExist", outputName: "sub_u" });
  });

  it("消歧只看首段：形态 A 引用带含 `::` 的字段路径仍走形态 A", () => {
    const result = validateReference(
      "agent_out.a::b",
      "tool_t",
      makeFormBState(),
      NODE_OUTPUT_CONTRACTS,
    );
    expect(result).toEqual({ kind: "valid" });
  });
});