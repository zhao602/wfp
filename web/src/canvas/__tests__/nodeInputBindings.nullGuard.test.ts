// =============================================================================
// 节点输入绑定登记表 null 守卫归因实验与双态固化
// （canvas/__tests__/nodeInputBindings.nullGuard.test.ts）
//
// 执行：cd web && npx vitest run src/canvas/__tests__/nodeInputBindings.nullGuard.test.ts
//
// 覆盖（fix_agent_null_length tasks v3 §2.1 归因实验硬门禁 + §3.1/§3.2/§4.2 双态固化）：
//   G1 归因实验（修复前失败 / 修复后通过成对留痕，报错文案与用户控制台堆栈
//   逐字一致：Cannot read properties of null (reading 'length')）：
//     NG-1 崩溃点直调：上游 agent inputs=[{alias: null}] → collectNodeInputBindings
//          修复前抛 TypeError（nodeInputBindings.ts:100:54，与堆栈逐字一致）；
//          修复后安全跳过（alias 非空字符串才入登记，T2.1 语义不变）
//     NG-2 完整链复现：同毒态画布 → collectUpstreamInputCandidates
//          修复前同族抛错（堆栈链 nodeIoTopology.ts:212 → :100）；修复后空候选
//     NG-3 undefined 变体（alias 键缺失）：条目不入登记（修复前后一致，契约固化）
//     NG-4 inputs=null 变体：修复前 "is not iterable" 同族；修复后安全降级 []
//     NG-5 合法对照：alias 非空字符串正常入登记（INV-02 零回归基线）
//   G2-② 登记表其余分支 null 双态（§3.1 ②③④⑤）：
//     NG-6/7/8 trigger.input_template / tool.argument_mapping / subworkflow.input_mapping
//          = null → 修复前 Object.entries(null) 抛；修复后 ?? {} 等效降级 []
//     NG-9 data_transform.params=null → 修复前 null 解引用抛；修复后仅基础绑定
//     NG-10 join 且 params.right_dataset_reference=null → 修复前 :76-77 null.length 抛；
//          修复后 typeof 守卫跳过
//   G2 nodeIoTopology null 守卫（§3.2）：
//     NG-11 上游绑定 reference=null → collectUpstreamInputCandidates
//          修复前 isFormAReferenceExpression null.trim 抛（:156）；修复后前置短路空候选
//     NG-12 validateReference(null) → 修复前 :333 null.trim 抛；修复后 { kind: "unbound" }
//     NG-13 形态 B 分支上游绑定 reference=null → 修复前 :306-309 null.trim 抛；
//          修复后 { kind: "notExist" } 等效降级
// =============================================================================

import { describe, it, expect } from "vitest";
import { collectNodeInputBindings } from "../nodeInputBindings";
import {
  collectUpstreamInputCandidates,
  validateReference,
} from "../nodeIoTopology";
import { NODE_OUTPUT_CONTRACTS } from "../nodeOutputContracts";
import type {
  CanvasNode,
  CanvasEdge,
  CanvasNodeData,
  WorkflowCanvasState,
} from "../canvasTypes";
import type {
  AgentInputEntry,
  NodeType,
  NodeConfiguration,
} from "../../workflow-definition/definitionTypes";

// ---------------------------------------------------------------------------
// 测试数据工厂（最小集，仅本文件所需节点类型；毒态配置经双断言注入，沿
// AgentInputListEditor.guard.test.tsx 既有惯例）
// ---------------------------------------------------------------------------

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

function makeEdge(source: string, target: string): CanvasEdge {
  return { id: `e-${source}-${target}`, source, target };
}

function makeState(
  nodes: CanvasNode[],
  edges: CanvasEdge[],
): WorkflowCanvasState {
  return { nodes, edges };
}

/** 带 inputs 的 agent 节点（inputs 由调用方注入：合法 / null / 毒态条目） */
function makeAgentWithInputs(
  nodeId: string,
  name: string,
  inputs: AgentInputEntry[],
): CanvasNode {
  return makeNodeWithConfig(nodeId, name, "agent", {
    node_type: "agent",
    model_identifier: "m",
    system_prompt: "",
    available_tools: [],
    inputs,
  });
}

// ---------------------------------------------------------------------------
// G1 归因实验：崩溃链复现（tasks v3 §2.1 硬门禁）
// ---------------------------------------------------------------------------

describe("G1 归因实验：null-alias 上游 agent 崩溃链（nodeInputBindings.ts:100）", () => {
  it("NG-1 崩溃点直调：alias=null 条目安全跳过，登记为空（与用户堆栈逐字对照）", () => {
    const upstream = makeAgentWithInputs("u1", "上游Agent", [
      { alias: null, reference: "n1.out" },
    ] as unknown as AgentInputEntry[]);
    // 修复前：TypeError: Cannot read properties of null (reading 'length')
    //（nodeInputBindings.ts:100:54，entry.alias.length）；修复后：安全跳过
    expect(collectNodeInputBindings(upstream)).toEqual([]);
  });

  it("NG-2 完整链复现：collectUpstreamInputCandidates 经 :212 → :100 同族抛错链安全返回", () => {
    const state = makeState(
      [
        makeAgentWithInputs("u1", "上游Agent", [
          { alias: null, reference: "n1.out" },
        ] as unknown as AgentInputEntry[]),
        makeAgentWithInputs("t1", "下游Agent", []),
      ],
      [makeEdge("u1", "t1")],
    );
    // 修复前：堆栈完整链（collectUpstreamInputCandidates :212 →
    // collectNodeInputBindings :100）抛同族 TypeError；修复后：空候选安全返回
    expect(
      collectUpstreamInputCandidates(state, "t1", NODE_OUTPUT_CONTRACTS),
    ).toEqual([]);
  });

  it("NG-3 undefined 变体：alias 键缺失条目不入登记（修复前后一致，契约固化）", () => {
    const upstream = makeAgentWithInputs("u1", "上游Agent", [
      { reference: "n1.out" },
    ]);
    expect(collectNodeInputBindings(upstream)).toEqual([]);
  });

  it("NG-4 inputs=null 变体：安全降级 []（修复前 for...of null 同族抛错）", () => {
    const upstream = makeAgentWithInputs("u1", "上游Agent", null as unknown as AgentInputEntry[]);
    expect(collectNodeInputBindings(upstream)).toEqual([]);
  });

  it("NG-5 合法对照：alias 非空字符串正常入登记（INV-02 零回归基线）", () => {
    const upstream = makeAgentWithInputs("u1", "上游Agent", [
      { alias: "q", reference: "n1.out" },
    ]);
    expect(collectNodeInputBindings(upstream)).toEqual([
      { positionName: "q", reference: "n1.out" },
    ]);
  });
});

// ---------------------------------------------------------------------------
// G2-② 登记表其余分支 null 双态（tasks v3 §3.1 ②③④⑤ / §4.2）
// ---------------------------------------------------------------------------

describe("G2-② 登记表其余分支 null 安全降级（Object.entries / params 解引用）", () => {
  it("NG-6 trigger.input_template=null → 等效降级 []（修复前 Object.entries(null) 抛）", () => {
    const node = makeNodeWithConfig("trig", "触发器", "trigger", {
      node_type: "trigger",
      trigger_type: "manual",
      input_template: null,
    } as unknown as NodeConfiguration);
    expect(collectNodeInputBindings(node)).toEqual([]);
  });

  it("NG-7 tool.argument_mapping=null → 等效降级 []（修复前 Object.entries(null) 抛）", () => {
    const node = makeNodeWithConfig("tool_1", "工具", "tool", {
      node_type: "tool",
      tool_identifier: "t",
      tool_source: "local",
      argument_mapping: null,
      static_arguments: {},
    } as unknown as NodeConfiguration);
    expect(collectNodeInputBindings(node)).toEqual([]);
  });

  it("NG-8 subworkflow.input_mapping=null → 等效降级 []（修复前 Object.entries(null) 抛）", () => {
    const node = makeNodeWithConfig("sub_u", "子工作流", "subworkflow", {
      node_type: "subworkflow",
      subworkflow_definition_id: "w",
      input_mapping: null,
      output_mapping: {},
      context_strategy: "inherit",
      max_nesting_depth: 3,
    } as unknown as NodeConfiguration);
    expect(collectNodeInputBindings(node)).toEqual([]);
  });

  it("NG-9 data_transform.params=null → 仅基础绑定（修复前 params null 解引用抛）", () => {
    const node = makeNodeWithConfig("dt_1", "变换", "data_transform", {
      node_type: "data_transform",
      operation: "join",
      input_dataset_reference: "left_out",
      params: null,
    } as unknown as NodeConfiguration);
    expect(collectNodeInputBindings(node)).toEqual([
      { positionName: "input_dataset_reference", reference: "left_out" },
    ]);
  });

  it("NG-10 join 且 params.right_dataset_reference=null → 跳过右引用（修复前 :76-77 null.length 抛）", () => {
    const node = makeNodeWithConfig("dt_2", "连接", "data_transform", {
      node_type: "data_transform",
      operation: "join",
      input_dataset_reference: "left_out",
      params: { right_dataset_reference: null },
    } as unknown as NodeConfiguration);
    expect(collectNodeInputBindings(node)).toEqual([
      { positionName: "input_dataset_reference", reference: "left_out" },
    ]);
  });
});

// ---------------------------------------------------------------------------
// G2 nodeIoTopology null 守卫（tasks v3 §3.2 / §4.2）
// ---------------------------------------------------------------------------

describe("G2 nodeIoTopology null 守卫（isFormA / validateReference / 形态 B 分支）", () => {
  it("NG-11 上游绑定 reference=null → collectUpstreamInputCandidates 空候选（修复前 :156 null.trim 抛）", () => {
    const state = makeState(
      [
        makeAgentWithInputs("u1", "上游Agent", [
          { alias: "q", reference: null },
        ] as unknown as AgentInputEntry[]),
        makeAgentWithInputs("t1", "下游Agent", []),
      ],
      [makeEdge("u1", "t1")],
    );
    expect(
      collectUpstreamInputCandidates(state, "t1", NODE_OUTPUT_CONTRACTS),
    ).toEqual([]);
  });

  it("NG-12 validateReference(null) → { kind: \"unbound\" }（修复前 :333 null.trim 抛；与空串同态）", () => {
    const state = makeState(
      [makeAgentWithInputs("t1", "目标Agent", [])],
      [],
    );
    expect(
      validateReference(
        null as unknown as string,
        "t1",
        state,
        NODE_OUTPUT_CONTRACTS,
      ),
    ).toEqual({ kind: "unbound" });
  });

  it("NG-13 形态 B 分支上游绑定 reference=null → { kind: \"notExist\" } 等效降级（修复前 :306-309 null.trim 抛）", () => {
    const state = makeState(
      [
        makeAgentWithInputs("u1", "上游Agent", [
          { alias: "q", reference: null },
        ] as unknown as AgentInputEntry[]),
        makeAgentWithInputs("t1", "下游Agent", []),
      ],
      [makeEdge("u1", "t1")],
    );
    expect(
      validateReference("u1::q", "t1", state, NODE_OUTPUT_CONTRACTS),
    ).toEqual({ kind: "notExist", outputName: "u1" });
  });
});
