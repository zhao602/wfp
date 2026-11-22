// =============================================================================
// 节点输入输出拓扑纯函数单元测试（canvas/__tests__/nodeIoTopology.test.ts）
//
// 留待非沙箱环境执行：cd web && npx vitest run nodeIoTopology
//
// 覆盖用例：
//   T1.3  collectUpstreamNodeIds / isUpstreamOf 拓扑判定
//   T3.2  collectUpstreamOutputCandidates 上游限定候选收集
//   T4.3  触发器豁免（无上游 → 空集）
//   T12.1 端到端集成（多类型节点 + 多分支拓扑）
//   T12.2 拓扑变化可靠性（增删边后重新计算）
//   T2.1  collectNodeInputBindings 输入项登记表（upstream_input_ref CHG-F01）
//   T2.3  collectUpstreamInputCandidates 上游输入参数候选（CHG-F03；
//         入候选三条件：可解析 / 形态 A / owner 上游且可引用，design §2.5）
//   T2.4  既有输出候选零回归（候选并列新增，互不影响）
// =============================================================================

import { describe, it, expect } from "vitest";
import {
  collectUpstreamNodeIds,
  isUpstreamOf,
  collectUpstreamOutputCandidates,
  collectUpstreamInputCandidates,
} from "../nodeIoTopology";
import { collectNodeInputBindings } from "../nodeInputBindings";
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
  const configuration = makeMinimalConfig(nodeType);
  const data: CanvasNodeData = {
    node_id: nodeId,
    name,
    node_type: nodeType,
    configuration,
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
// T1.3 collectUpstreamNodeIds / isUpstreamOf
// ---------------------------------------------------------------------------

describe("collectUpstreamNodeIds", () => {
  it("无上游节点返回空集", () => {
    const edges: CanvasEdge[] = [];
    const result = collectUpstreamNodeIds(edges, "n1");
    expect(result.size).toBe(0);
  });

  it("单直连上游", () => {
    const edges = [makeEdge("n1", "n2")];
    const result = collectUpstreamNodeIds(edges, "n2");
    expect(result.has("n1")).toBe(true);
    expect(result.size).toBe(1);
  });

  it("多跳传递上游（n1→n2→n3，求 n3 上游得 n1+n2）", () => {
    const edges = [makeEdge("n1", "n2"), makeEdge("n2", "n3")];
    const result = collectUpstreamNodeIds(edges, "n3");
    expect(result.has("n1")).toBe(true);
    expect(result.has("n2")).toBe(true);
    expect(result.size).toBe(2);
  });

  it("多分支汇聚（菱形拓扑 n1→n2, n1→n3, n2→n4, n3→n4）", () => {
    const edges = [
      makeEdge("n1", "n2"),
      makeEdge("n1", "n3"),
      makeEdge("n2", "n4"),
      makeEdge("n3", "n4"),
    ];
    const result = collectUpstreamNodeIds(edges, "n4");
    expect(result.has("n1")).toBe(true);
    expect(result.has("n2")).toBe(true);
    expect(result.has("n3")).toBe(true);
    expect(result.size).toBe(3);
  });

  it("环结构终止（n1→n2→n1，求 n2 上游得 n1，不无限循环）", () => {
    const edges = [makeEdge("n1", "n2"), makeEdge("n2", "n1")];
    const result = collectUpstreamNodeIds(edges, "n2");
    expect(result.has("n1")).toBe(true);
    expect(result.size).toBe(1);
  });

  it("目标不在边集合中返回空集", () => {
    const edges = [makeEdge("n1", "n2")];
    const result = collectUpstreamNodeIds(edges, "n999");
    expect(result.size).toBe(0);
  });

  it("自身出现在上游中时被排除（自环 n1→n1）", () => {
    const edges = [makeEdge("n1", "n1")];
    const result = collectUpstreamNodeIds(edges, "n1");
    expect(result.size).toBe(0);
  });
});

describe("isUpstreamOf", () => {
  it("直连上游返回 true", () => {
    const state = makeState(
      [makeNode("n1", "A", "agent", "a"), makeNode("n2", "B", "tool")],
      [makeEdge("n1", "n2")],
    );
    expect(isUpstreamOf(state, "n1", "n2")).toBe(true);
  });

  it("非上游返回 false", () => {
    const state = makeState(
      [makeNode("n1", "A", "agent", "a"), makeNode("n2", "B", "tool")],
      [makeEdge("n1", "n2")],
    );
    expect(isUpstreamOf(state, "n2", "n1")).toBe(false);
  });

  it("传递上游返回 true", () => {
    const state = makeState(
      [
        makeNode("n1", "A", "agent", "a"),
        makeNode("n2", "B", "tool"),
        makeNode("n3", "C", "tool"),
      ],
      [makeEdge("n1", "n2"), makeEdge("n2", "n3")],
    );
    expect(isUpstreamOf(state, "n1", "n3")).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// T3.2 collectUpstreamOutputCandidates
// ---------------------------------------------------------------------------

describe("collectUpstreamOutputCandidates", () => {
  it("仅返回上游节点中已声明命名输出且可引用的候选", () => {
    const state = makeState(
      [
        makeNode("n1", "上游Agent", "agent", "agent_out"),
        makeNode("n2", "下游Tool", "tool", "tool_out"),
      ],
      [makeEdge("n1", "n2")],
    );
    const candidates = collectUpstreamOutputCandidates(state, "n2", NODE_OUTPUT_CONTRACTS);
    expect(candidates).toHaveLength(1);
    expect(candidates[0].outputName).toBe("agent_out");
    expect(candidates[0].nodeId).toBe("n1");
    expect(candidates[0].category).toBe("结构化对象");
  });

  it("排除自身（目标节点即使声明了 output_name 也不出现在候选中）", () => {
    const state = makeState(
      [
        makeNode("n1", "上游", "agent", "up_out"),
        makeNode("n2", "目标", "tool", "self_out"),
      ],
      [makeEdge("n1", "n2")],
    );
    const candidates = collectUpstreamOutputCandidates(state, "n2", NODE_OUTPUT_CONTRACTS);
    expect(candidates.every((c) => c.nodeId !== "n2")).toBe(true);
  });

  it("排除下游节点（非上游不出现在候选中）", () => {
    const state = makeState(
      [
        makeNode("n1", "上游", "agent", "up_out"),
        makeNode("n2", "目标", "tool"),
        makeNode("n3", "下游", "tool", "down_out"),
      ],
      [makeEdge("n1", "n2"), makeEdge("n2", "n3")],
    );
    const candidates = collectUpstreamOutputCandidates(state, "n2", NODE_OUTPUT_CONTRACTS);
    expect(candidates.every((c) => c.nodeId !== "n3")).toBe(true);
  });

  it("控制流节点被过滤（referable=false 不出现在候选中）", () => {
    const state = makeState(
      [
        makeNode("n1", "控制流", "control_flow", "cf_out"),
        makeNode("n2", "目标", "tool"),
      ],
      [makeEdge("n1", "n2")],
    );
    const candidates = collectUpstreamOutputCandidates(state, "n2", NODE_OUTPUT_CONTRACTS);
    expect(candidates).toHaveLength(0);
  });

  it("无上游时返回空数组", () => {
    const state = makeState(
      [
        makeNode("n1", "孤立", "agent", "iso_out"),
        makeNode("n2", "目标", "tool"),
      ],
      [],
    );
    const candidates = collectUpstreamOutputCandidates(state, "n2", NODE_OUTPUT_CONTRACTS);
    expect(candidates).toHaveLength(0);
  });

  it("includeAll=true 时退化为全节点候选（向后兼容）", () => {
    const state = makeState(
      [
        makeNode("n1", "节点A", "agent", "a_out"),
        makeNode("n2", "节点B", "tool", "b_out"),
      ],
      [],
    );
    const candidates = collectUpstreamOutputCandidates(state, "n2", NODE_OUTPUT_CONTRACTS, true);
    expect(candidates).toHaveLength(2);
    expect(candidates.some((c) => c.outputName === "a_out")).toBe(true);
    expect(candidates.some((c) => c.outputName === "b_out")).toBe(true);
  });

  it("未声明 output_name 的上游节点不出现在候选中", () => {
    const state = makeState(
      [
        makeNode("n1", "无输出上游", "agent"),
        makeNode("n2", "目标", "tool"),
      ],
      [makeEdge("n1", "n2")],
    );
    const candidates = collectUpstreamOutputCandidates(state, "n2", NODE_OUTPUT_CONTRACTS);
    expect(candidates).toHaveLength(0);
  });

  it("候选 category 由契约表填充", () => {
    const state = makeState(
      [
        makeNode("n1", "触发器", "trigger", "trig_out"),
        makeNode("n2", "数据源", "data_source", "ds_out"),
        makeNode("n3", "目标", "tool"),
      ],
      [makeEdge("n1", "n3"), makeEdge("n2", "n3")],
    );
    const candidates = collectUpstreamOutputCandidates(state, "n3", NODE_OUTPUT_CONTRACTS);
    expect(candidates).toHaveLength(2);
    const trig = candidates.find((c) => c.outputName === "trig_out");
    const ds = candidates.find((c) => c.outputName === "ds_out");
    expect(trig?.category).toBe("输入参数集合（键值对）");
    expect(ds?.category).toBe("数据集（记录集合）");
  });
});

// ---------------------------------------------------------------------------
// T4.3 触发器豁免
// ---------------------------------------------------------------------------

describe("触发器豁免（T4.3）", () => {
  it("触发器作为目标节点时无上游（拓扑函数自然满足）", () => {
    const state = makeState(
      [
        makeNode("n1", "触发器", "trigger", "trig_out"),
        makeNode("n2", "Agent", "agent", "agent_out"),
      ],
      [],
    );
    const candidates = collectUpstreamOutputCandidates(state, "n1", NODE_OUTPUT_CONTRACTS);
    expect(candidates).toHaveLength(0);
  });

  it("触发器作为上游时其输出可被引用（referable=true）", () => {
    const state = makeState(
      [
        makeNode("n1", "触发器", "trigger", "trig_out"),
        makeNode("n2", "Tool", "tool"),
      ],
      [makeEdge("n1", "n2")],
    );
    const candidates = collectUpstreamOutputCandidates(state, "n2", NODE_OUTPUT_CONTRACTS);
    expect(candidates).toHaveLength(1);
    expect(candidates[0].outputName).toBe("trig_out");
  });
});

// ---------------------------------------------------------------------------
// T12.1 端到端集成
// ---------------------------------------------------------------------------

describe("端到端集成（T12.1）", () => {
  it("多类型节点 + 多分支拓扑：仅返回可达上游中可引用的命名输出", () => {
    const state = makeState(
      [
        makeNode("trig", "触发器", "trigger", "trig_out"),
        makeNode("agent", "Agent", "agent", "agent_out"),
        makeNode("cf", "控制流", "control_flow", "cf_out"),
        makeNode("ds", "数据源", "data_source", "ds_out"),
        makeNode("tool", "目标Tool", "tool"),
      ],
      [
        makeEdge("trig", "agent"),
        makeEdge("agent", "cf"),
        makeEdge("cf", "tool"),
        makeEdge("ds", "tool"),
      ],
    );
    const candidates = collectUpstreamOutputCandidates(state, "tool", NODE_OUTPUT_CONTRACTS);
    const outputNames = candidates.map((c) => c.outputName).sort();
    expect(outputNames).toEqual(["agent_out", "ds_out", "trig_out"]);
    const cfCandidate = candidates.find((c) => c.outputName === "cf_out");
    expect(cfCandidate).toBeUndefined();
    const validCandidates = candidates.filter((c) => c.outputName !== "cf_out");
    expect(validCandidates).toHaveLength(3);
  });
});

// ---------------------------------------------------------------------------
// T12.2 拓扑变化可靠性
// ---------------------------------------------------------------------------

describe("拓扑变化可靠性（T12.2）", () => {
  it("新增边后上游集合扩大", () => {
    const nodes = [
      makeNode("n1", "A", "agent", "a_out"),
      makeNode("n2", "B", "agent", "b_out"),
      makeNode("n3", "目标", "tool"),
    ];
    const stateBefore = makeState(nodes, [makeEdge("n1", "n3")]);
    const before = collectUpstreamOutputCandidates(stateBefore, "n3", NODE_OUTPUT_CONTRACTS);
    expect(before).toHaveLength(1);

    const stateAfter = makeState(nodes, [makeEdge("n1", "n3"), makeEdge("n2", "n3")]);
    const after = collectUpstreamOutputCandidates(stateAfter, "n3", NODE_OUTPUT_CONTRACTS);
    expect(after).toHaveLength(2);
  });

  it("删除边后上游集合缩小", () => {
    const nodes = [
      makeNode("n1", "A", "agent", "a_out"),
      makeNode("n2", "B", "agent", "b_out"),
      makeNode("n3", "目标", "tool"),
    ];
    const stateBefore = makeState(nodes, [makeEdge("n1", "n3"), makeEdge("n2", "n3")]);
    const before = collectUpstreamOutputCandidates(stateBefore, "n3", NODE_OUTPUT_CONTRACTS);
    expect(before).toHaveLength(2);

    const stateAfter = makeState(nodes, [makeEdge("n1", "n3")]);
    const after = collectUpstreamOutputCandidates(stateAfter, "n3", NODE_OUTPUT_CONTRACTS);
    expect(after).toHaveLength(1);
    expect(after[0].outputName).toBe("a_out");
  });
});

// ---------------------------------------------------------------------------
// T2.1 collectNodeInputBindings 输入项登记表（upstream_input_ref CHG-F01）
// ---------------------------------------------------------------------------

/** 构造带显式配置的画布节点（T2 系列夹具；不侵入既有工厂） */
function makeNodeWithConfig(
  nodeId: string,
  name: string,
  nodeType: NodeType,
  configuration: NodeConfiguration,
  outputName?: string,
): CanvasNode {
  const data: CanvasNodeData = {
    node_id: nodeId,
    name,
    node_type: nodeType,
    configuration,
  };
  if (outputName !== undefined) {
    data.output_name = outputName;
  }
  return { id: nodeId, type: nodeType, position: { x: 0, y: 0 }, data };
}

describe("collectNodeInputBindings 登记表（T2.1）", () => {
  it("trigger：input_template 各键入登记（含空绑定，保持声明顺序）", () => {
    const node = makeNodeWithConfig("trig", "触发器", "trigger", {
      node_type: "trigger",
      trigger_type: "manual",
      input_template: { user_query: "some_input", empty_slot: "" },
    });
    expect(collectNodeInputBindings(node)).toEqual([
      { positionName: "user_query", reference: "some_input" },
      { positionName: "empty_slot", reference: "" },
    ]);
  });

  it("tool：argument_mapping 各键入登记，static_arguments 常量项不入", () => {
    const node = makeNodeWithConfig("tool_1", "工具", "tool", {
      node_type: "tool",
      tool_identifier: "t",
      tool_source: "local",
      argument_mapping: { table: "ds_out", filter: "" },
      static_arguments: { limit: "10" },
    });
    expect(collectNodeInputBindings(node)).toEqual([
      { positionName: "table", reference: "ds_out" },
      { positionName: "filter", reference: "" },
    ]);
  });

  it("subworkflow：input_mapping 各键入登记", () => {
    const node = makeNodeWithConfig("sub_u", "子工作流", "subworkflow", {
      node_type: "subworkflow",
      subworkflow_definition_id: "w",
      input_mapping: { user_input: "agent_out" },
      output_mapping: {},
      context_strategy: "inherit",
      max_nesting_depth: 3,
    });
    expect(collectNodeInputBindings(node)).toEqual([
      { positionName: "user_input", reference: "agent_out" },
    ]);
  });

  it("data_transform：input_dataset_reference 入登记；join 且右引用非空时 params.right_dataset_reference 入登记", () => {
    const joinNode = makeNodeWithConfig("dt_join", "连接", "data_transform", {
      node_type: "data_transform",
      operation: "join",
      input_dataset_reference: "left_out",
      params: { right_dataset_reference: "right_out" },
    });
    expect(collectNodeInputBindings(joinNode)).toEqual([
      { positionName: "input_dataset_reference", reference: "left_out" },
      { positionName: "params.right_dataset_reference", reference: "right_out" },
    ]);

    const mapNode = makeNodeWithConfig("dt_map", "映射", "data_transform", {
      node_type: "data_transform",
      operation: "map",
      input_dataset_reference: "left_out",
      params: {},
    });
    expect(collectNodeInputBindings(mapNode)).toEqual([
      { positionName: "input_dataset_reference", reference: "left_out" },
    ]);
  });

  it("output_display：input_reference 非空字符串入登记，空值不入", () => {
    const bound = makeNodeWithConfig("od_1", "展示", "output_display", {
      node_type: "output_display",
      input_reference: "agent_out",
    });
    expect(collectNodeInputBindings(bound)).toEqual([
      { positionName: "input_reference", reference: "agent_out" },
    ]);

    const unbound = makeNodeWithConfig("od_2", "展示空", "output_display", {
      node_type: "output_display",
      input_reference: "",
    });
    expect(collectNodeInputBindings(unbound)).toEqual([]);
  });

  it("agent：inputs 条目须 alias 非空，无 alias 条目不入", () => {
    const node = makeNodeWithConfig("agent_1", "智能体", "agent", {
      node_type: "agent",
      model_identifier: "m",
      system_prompt: "",
      available_tools: [],
      inputs: [
        { alias: "ctx", reference: "agent_out" },
        { reference: "raw_out" },
      ],
    });
    expect(collectNodeInputBindings(node)).toEqual([
      { positionName: "ctx", reference: "agent_out" },
    ]);
  });

  it("安全降级：control_flow / dynamic_injector / data_source / vector_knowledge_base / llm 返回 []", () => {
    const cf = makeNode("cf_1", "控制流", "control_flow");
    const di = makeNode("di_1", "注入", "dynamic_injector");
    const ds = makeNode("ds_1", "数据源", "data_source");
    const vkb = makeNode("vkb_1", "知识库", "vector_knowledge_base");
    const llm = makeNodeWithConfig("llm_1", "大模型", "llm", {
      node_type: "llm",
      model_identifier: "m",
      system_prompt: "",
      user_prompt: "",
    });
    expect(collectNodeInputBindings(cf)).toEqual([]);
    expect(collectNodeInputBindings(di)).toEqual([]);
    expect(collectNodeInputBindings(ds)).toEqual([]);
    expect(collectNodeInputBindings(vkb)).toEqual([]);
    expect(collectNodeInputBindings(llm)).toEqual([]);
  });

  it("纯函数：同输入两次调用结果深比较相等", () => {
    const node = makeNodeWithConfig("sub_p", "纯度", "subworkflow", {
      node_type: "subworkflow",
      subworkflow_definition_id: "w",
      input_mapping: { user_input: "agent_out" },
      output_mapping: {},
      context_strategy: "inherit",
      max_nesting_depth: 3,
    });
    const first = collectNodeInputBindings(node);
    const second = collectNodeInputBindings(node);
    expect(first).toEqual(second);
  });
});

// ---------------------------------------------------------------------------
// T2.3 collectUpstreamInputCandidates 上游输入参数候选（CHG-F03）
// ---------------------------------------------------------------------------

/** T2.3 标准场景：agent_a(agent_out) → sub_u(input_mapping) → tool_t（目标） */
function makeInputCandidateScenario(): WorkflowCanvasState {
  return makeState(
    [
      makeNode("agent_a", "上游Agent", "agent", "agent_out"),
      makeNodeWithConfig("sub_u", "子工作流", "subworkflow", {
        node_type: "subworkflow",
        subworkflow_definition_id: "w",
        input_mapping: { user_input: "agent_out", empty_slot: "" },
        output_mapping: {},
        context_strategy: "inherit",
        max_nesting_depth: 3,
      }),
      makeNode("tool_t", "目标Tool", "tool"),
    ],
    [makeEdge("agent_a", "sub_u"), makeEdge("sub_u", "tool_t")],
  );
}

describe("collectUpstreamInputCandidates（T2.3）", () => {
  it("合法候选精确断言：上游来源节点 + 形态 A 绑定 + category 固定「输入参数」", () => {
    const candidates = collectUpstreamInputCandidates(
      makeInputCandidateScenario(),
      "tool_t",
      NODE_OUTPUT_CONTRACTS,
    );
    expect(candidates).toHaveLength(1);
    expect(candidates[0]).toEqual({
      nodeId: "sub_u",
      nodeName: "子工作流",
      positionName: "user_input",
      reference: "agent_out",
      category: "输入参数",
    });
  });

  it("条件①：绑定为空的位置不入候选", () => {
    const candidates = collectUpstreamInputCandidates(
      makeInputCandidateScenario(),
      "tool_t",
      NODE_OUTPUT_CONTRACTS,
    );
    expect(candidates.some((c) => c.positionName === "empty_slot")).toBe(false);
  });

  it("条件②：绑定为形态 B 的位置不入候选（DC-03 不递归）", () => {
    const state = makeState(
      [
        makeNode("agent_a", "上游Agent", "agent", "agent_out"),
        makeNodeWithConfig("sub_b", "形态B绑定", "subworkflow", {
          node_type: "subworkflow",
          subworkflow_definition_id: "w",
          input_mapping: { chained: "x::y" },
          output_mapping: {},
          context_strategy: "inherit",
          max_nesting_depth: 3,
        }),
        makeNode("tool_t", "目标Tool", "tool"),
      ],
      [makeEdge("agent_a", "sub_b"), makeEdge("sub_b", "tool_t")],
    );
    expect(
      collectUpstreamInputCandidates(state, "tool_t", NODE_OUTPUT_CONTRACTS),
    ).toHaveLength(0);
  });

  it("条件①：绑定引用无节点 owner（工作流级入参名）不入——trigger input_template 前端无候选", () => {
    const state = makeState(
      [
        makeNodeWithConfig("trig", "触发器", "trigger", {
          node_type: "trigger",
          trigger_type: "manual",
          input_template: { user_query: "workflow_input_name" },
        }),
        makeNode("tool_t", "目标Tool", "tool"),
      ],
      [makeEdge("trig", "tool_t")],
    );
    expect(
      collectUpstreamInputCandidates(state, "tool_t", NODE_OUTPUT_CONTRACTS),
    ).toHaveLength(0);
  });

  it("条件③：owner 不可引用（control_flow，referable=false）不入候选", () => {
    const state = makeState(
      [
        makeNode("cf_u", "控制流", "control_flow", "cf_out"),
        makeNodeWithConfig("sub_cf", "绑定控制流", "subworkflow", {
          node_type: "subworkflow",
          subworkflow_definition_id: "w",
          input_mapping: { p: "cf_out" },
          output_mapping: {},
          context_strategy: "inherit",
          max_nesting_depth: 3,
        }),
        makeNode("tool_t", "目标Tool", "tool"),
      ],
      [makeEdge("cf_u", "sub_cf"), makeEdge("sub_cf", "tool_t")],
    );
    expect(
      collectUpstreamInputCandidates(state, "tool_t", NODE_OUTPUT_CONTRACTS),
    ).toHaveLength(0);
  });

  it("条件③：owner 不在来源节点上游可达集内不入候选", () => {
    // agent_far 直连目标（是目标的上游）但不在 sub_s 的上游：owner 非来源节点可达
    const state = makeState(
      [
        makeNode("agent_a", "上游Agent", "agent", "agent_out"),
        makeNodeWithConfig("sub_s", "越权绑定", "subworkflow", {
          node_type: "subworkflow",
          subworkflow_definition_id: "w",
          input_mapping: { q: "far_out" },
          output_mapping: {},
          context_strategy: "inherit",
          max_nesting_depth: 3,
        }),
        makeNode("agent_far", "远Agent", "agent", "far_out"),
        makeNode("tool_t", "目标Tool", "tool"),
      ],
      [
        makeEdge("agent_a", "sub_s"),
        makeEdge("sub_s", "tool_t"),
        makeEdge("agent_far", "tool_t"),
      ],
    );
    expect(
      collectUpstreamInputCandidates(state, "tool_t", NODE_OUTPUT_CONTRACTS),
    ).toHaveLength(0);
  });

  it("上游限定：非上游节点的输入位置不入候选（下游 / 孤立 / 自身）", () => {
    const state = makeState(
      [
        makeNode("agent_a", "上游Agent", "agent", "agent_out"),
        makeNodeWithConfig("sub_u", "上游子工作流", "subworkflow", {
          node_type: "subworkflow",
          subworkflow_definition_id: "w",
          input_mapping: { user_input: "agent_out" },
          output_mapping: {},
          context_strategy: "inherit",
          max_nesting_depth: 3,
        }),
        makeNodeWithConfig("sub_d", "下游子工作流", "subworkflow", {
          node_type: "subworkflow",
          subworkflow_definition_id: "w",
          input_mapping: { later: "agent_out" },
          output_mapping: {},
          context_strategy: "inherit",
          max_nesting_depth: 3,
        }),
        makeNodeWithConfig("sub_iso", "孤立子工作流", "subworkflow", {
          node_type: "subworkflow",
          subworkflow_definition_id: "w",
          input_mapping: { isolated: "agent_out" },
          output_mapping: {},
          context_strategy: "inherit",
          max_nesting_depth: 3,
        }),
        makeNodeWithConfig("tool_t", "目标Tool", "tool", {
          node_type: "tool",
          tool_identifier: "t",
          tool_source: "local",
          argument_mapping: { self_slot: "agent_out" },
          static_arguments: {},
        }),
      ],
      [
        makeEdge("agent_a", "sub_u"),
        makeEdge("sub_u", "tool_t"),
        makeEdge("tool_t", "sub_d"),
      ],
    );
    const candidates = collectUpstreamInputCandidates(
      state,
      "tool_t",
      NODE_OUTPUT_CONTRACTS,
    );
    expect(candidates).toHaveLength(1);
    expect(candidates[0].nodeId).toBe("sub_u");
  });

  it("无上游时返回空数组", () => {
    const state = makeState(
      [
        makeNodeWithConfig("sub_iso", "孤立子工作流", "subworkflow", {
          node_type: "subworkflow",
          subworkflow_definition_id: "w",
          input_mapping: { isolated: "agent_out" },
          output_mapping: {},
          context_strategy: "inherit",
          max_nesting_depth: 3,
        }),
        makeNode("tool_t", "目标Tool", "tool"),
      ],
      [],
    );
    expect(
      collectUpstreamInputCandidates(state, "tool_t", NODE_OUTPUT_CONTRACTS),
    ).toEqual([]);
  });

  it("确定性：节点声明顺序 + 字段声明顺序，重复调用深比较相等（INV-E11）", () => {
    const state = makeState(
      [
        makeNode("agent_a", "上游Agent", "agent", "agent_out"),
        makeNodeWithConfig("sub_first", "第一子工作流", "subworkflow", {
          node_type: "subworkflow",
          subworkflow_definition_id: "w",
          input_mapping: { b_slot: "agent_out", a_slot: "agent_out" },
          output_mapping: {},
          context_strategy: "inherit",
          max_nesting_depth: 3,
        }),
        makeNodeWithConfig("sub_second", "第二子工作流", "subworkflow", {
          node_type: "subworkflow",
          subworkflow_definition_id: "w",
          input_mapping: { c_slot: "agent_out" },
          output_mapping: {},
          context_strategy: "inherit",
          max_nesting_depth: 3,
        }),
        makeNode("tool_t", "目标Tool", "tool"),
      ],
      [
        makeEdge("agent_a", "sub_first"),
        makeEdge("sub_first", "sub_second"),
        makeEdge("sub_second", "tool_t"),
      ],
    );
    const first = collectUpstreamInputCandidates(state, "tool_t", NODE_OUTPUT_CONTRACTS);
    const second = collectUpstreamInputCandidates(state, "tool_t", NODE_OUTPUT_CONTRACTS);
    expect(first).toEqual(second);
    expect(first.map((c) => `${c.nodeId}.${c.positionName}`)).toEqual([
      "sub_first.b_slot",
      "sub_first.a_slot",
      "sub_second.c_slot",
    ]);
  });

  it("候选不含运行期数据值字段（INV-E12）", () => {
    const candidates = collectUpstreamInputCandidates(
      makeInputCandidateScenario(),
      "tool_t",
      NODE_OUTPUT_CONTRACTS,
    );
    for (const candidate of candidates) {
      expect(
        Object.keys(candidate).every((key) =>
          ["nodeId", "nodeName", "positionName", "reference", "category"].includes(key),
        ),
      ).toBe(true);
    }
  });

  it("既有输出候选零回归：与输入候选并列共存、互不影响（FR-04/AC-04）", () => {
    const state = makeInputCandidateScenario();
    const outputCandidates = collectUpstreamOutputCandidates(
      state,
      "tool_t",
      NODE_OUTPUT_CONTRACTS,
    );
    expect(outputCandidates).toHaveLength(1);
    expect(outputCandidates[0].outputName).toBe("agent_out");
    const inputCandidates = collectUpstreamInputCandidates(
      state,
      "tool_t",
      NODE_OUTPUT_CONTRACTS,
    );
    expect(inputCandidates).toHaveLength(1);
    expect(inputCandidates[0].positionName).toBe("user_input");
  });
});