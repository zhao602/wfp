// =============================================================================
// 模板应用纯函数单元测试（src/canvas/__tests__/applyAgentTemplate.test.ts）
//
// 留待非沙箱环境执行：cd web && npx vitest run src/canvas/__tests__/applyAgentTemplate.test.ts
//
// 覆盖用例（agent_tool_marketplace T18.1 / DC-06 / 必做守护 ⑥）：
//   TC-AT-01 single_node + 目标 Agent 节点 → 合并（同名键模板优先、其余不变、available_tools 去重保序）
//   TC-AT-02 未指定目标且画布恰一个 Agent 节点 → 合并（T16.2 ②）
//   TC-AT-03 未指定目标且画布无 Agent 节点 → 新建 Agent 节点（T16.2 ③）
//   TC-AT-04 targetNodeId 指向非 Agent 节点 → 回落新建（T16.2 ③ 防御分支）
//   TC-AT-05 max_tool_rounds 缺省 → 不写入字段（T16.3 / AC-13 规则 3）
//   TC-AT-06 recipe → nodes/edges 全量实例化且映射正确（T16.4）
//   TC-AT-07 预设键越界（sandbox_policy_id 等）→ 过滤 + reason（T16.5）
//   TC-AT-08 纯函数入参不可变（T16.1 / 必做守护 ⑥ / INV-D13）
// =============================================================================

import { describe, it, expect } from "vitest";
import { applyAgentTemplateToCanvas } from "../applyAgentTemplate";
import type { CanvasNode, WorkflowCanvasState } from "../canvasTypes";
import type { AgentTemplate } from "../../market/marketTypes";
import type {
  AgentNodeConfiguration,
  NodeConfiguration,
} from "../../workflow-definition/definitionTypes";

// —— 测试夹具 ——

/** 构造 Agent 节点配置夹具 */
function makeAgentConfig(overrides: Partial<AgentNodeConfiguration> = {}): AgentNodeConfiguration {
  return {
    node_type: "agent",
    model_identifier: "base-model",
    system_prompt: "base prompt",
    available_tools: ["tool_a"],
    max_tool_rounds: 3,
    ...overrides,
  };
}

/** 构造 Agent 画布节点夹具 */
function makeAgentNode(id: string, config?: AgentNodeConfiguration): CanvasNode {
  return {
    id,
    type: "agent",
    position: { x: 100, y: 100 },
    data: {
      node_id: id,
      name: `节点 ${id}`,
      node_type: "agent",
      configuration: config ?? makeAgentConfig(),
    },
  };
}

/** 构造触发器画布节点夹具（非 Agent 目标防御用例载体） */
function makeTriggerNode(id: string): CanvasNode {
  const configuration: NodeConfiguration = {
    node_type: "trigger",
    trigger_type: "manual",
    input_template: {},
  } as NodeConfiguration;
  return {
    id,
    type: "trigger",
    position: { x: 0, y: 0 },
    data: {
      node_id: id,
      name: `节点 ${id}`,
      node_type: "trigger",
      configuration,
    },
  };
}

/** 构造模板夹具（single_node / recipe 可覆写） */
function makeTemplate(
  preset: Record<string, unknown>,
  overrides: Partial<AgentTemplate> = {},
): AgentTemplate {
  return {
    template_id: "tpl_test",
    name: "测试模板",
    kind: "single_node",
    preset,
    system_prompt: "模板提示词",
    allowed_tools: [],
    max_tool_rounds: null,
    output_max_retries: null,
    doc: {
      core_principle_flow: "d1",
      application_features: "d2",
      applicable_scenarios: "d3",
      advantages_limitations: "d4",
      comparison: "d5",
    },
    tool_config: {
      tool_types: ["builtin"],
      invocation_mode: "react_loop",
      parameter_constraints: "无",
      retry_and_exception_policy: "无",
    },
    implementation_key_points: [],
    dependency_conditions: [],
    performance_boundaries: {
      max_tool_rounds: null,
      tool_timeout_seconds: 30,
      output_max_retries: null,
    },
    security_boundaries: [],
    evaluation_metrics: [],
    recipe: null,
    ...overrides,
  };
}

// =============================================================================
// TC-AT-01 single_node + 目标 Agent 节点 → 合并
// =============================================================================

describe("applyAgentTemplateToCanvas — single_node 目标合并", () => {
  it("TC-AT-01 同名键模板优先、其余不变、available_tools 去重保序", () => {
    const target = makeAgentNode(
      "ag1",
      makeAgentConfig({
        output_schema: { result: "string" },
        output_max_retries: 2,
      }),
    );
    const canvas: WorkflowCanvasState = { nodes: [makeTriggerNode("tr1"), target], edges: [] };
    const template = makeTemplate({
      model_identifier: "tpl-model",
      system_prompt: "tpl prompt",
      available_tools: ["tool_b", "tool_a"],
      max_tool_rounds: 8,
    });

    const result = applyAgentTemplateToCanvas(canvas, template, "ag1");

    expect(result.applied).toBe(true);
    expect(result.replacedNodeId).toBe("ag1");
    expect(result.createdNodeIds).toEqual([]);
    expect(result.reason).toBeUndefined();

    const merged = result.canvasState.nodes.find((node) => node.id === "ag1");
    expect(merged).toBeDefined();
    const config = merged!.data.configuration as AgentNodeConfiguration;
    // 同名键模板优先
    expect(config.model_identifier).toBe("tpl-model");
    expect(config.system_prompt).toBe("tpl prompt");
    expect(config.max_tool_rounds).toBe(8);
    // available_tools 去重保序（既有在前）
    expect(config.available_tools).toEqual(["tool_a", "tool_b"]);
    // 其余字段不变
    expect(config.output_schema).toEqual({ result: "string" });
    expect(config.output_max_retries).toBe(2);

    // 目标之外的节点零改动（引用保持）
    const trigger = result.canvasState.nodes.find((node) => node.id === "tr1");
    expect(trigger).toBe(canvas.nodes[0]);
    expect(result.canvasState.nodes).toHaveLength(2);
  });

  it("TC-AT-02 未指定目标且画布恰一个 Agent 节点 → 合并该节点", () => {
    const agent = makeAgentNode("ag1");
    const canvas: WorkflowCanvasState = { nodes: [makeTriggerNode("tr1"), agent], edges: [] };
    const template = makeTemplate({ model_identifier: "tpl-model" });

    const result = applyAgentTemplateToCanvas(canvas, template);

    expect(result.applied).toBe(true);
    expect(result.replacedNodeId).toBe("ag1");
    expect(result.createdNodeIds).toEqual([]);
    expect(result.canvasState.nodes).toHaveLength(2);
    const merged = result.canvasState.nodes.find((node) => node.id === "ag1");
    expect((merged!.data.configuration as AgentNodeConfiguration).model_identifier).toBe(
      "tpl-model",
    );
  });

  it("TC-AT-03 未指定目标且画布无 Agent 节点 → 新建 Agent 节点", () => {
    const canvas: WorkflowCanvasState = { nodes: [makeTriggerNode("tr1")], edges: [] };
    const template = makeTemplate({
      model_identifier: "tpl-model",
      system_prompt: "tpl prompt",
    });

    const result = applyAgentTemplateToCanvas(canvas, template);

    expect(result.applied).toBe(true);
    expect(result.replacedNodeId).toBeNull();
    expect(result.createdNodeIds).toEqual(["tpl_test_agent_1"]);
    expect(result.canvasState.nodes).toHaveLength(2);

    const created = result.canvasState.nodes.find((node) => node.id === "tpl_test_agent_1");
    expect(created).toBeDefined();
    expect(created!.type).toBe("agent");
    expect(created!.data.node_type).toBe("agent");
    const config = created!.data.configuration as AgentNodeConfiguration;
    // 默认配置与预设保序合一
    expect(config.model_identifier).toBe("tpl-model");
    expect(config.system_prompt).toBe("tpl prompt");
    expect(config.available_tools).toEqual([]);
    // 既有节点零改动
    expect(result.canvasState.nodes[0]).toBe(canvas.nodes[0]);
  });

  it("TC-AT-04 targetNodeId 指向非 Agent 节点 → 回落新建", () => {
    const canvas: WorkflowCanvasState = { nodes: [makeTriggerNode("tr1")], edges: [] };
    const template = makeTemplate({ model_identifier: "tpl-model" });

    const result = applyAgentTemplateToCanvas(canvas, template, "tr1");

    expect(result.applied).toBe(true);
    expect(result.replacedNodeId).toBeNull();
    expect(result.createdNodeIds).toEqual(["tpl_test_agent_1"]);
  });
});

// =============================================================================
// TC-AT-05 max_tool_rounds 缺省语义（T16.3 / AC-13 规则 3）
// =============================================================================

describe("applyAgentTemplateToCanvas — max_tool_rounds 缺省语义", () => {
  it("TC-AT-05 模板未声明 max_tool_rounds → 不写入字段（保持缺省回落全局 5）", () => {
    const baseConfig = makeAgentConfig();
    delete baseConfig.max_tool_rounds; // 模拟未声明该键的缺省配置
    const target = makeAgentNode("ag1", baseConfig);
    const canvas: WorkflowCanvasState = { nodes: [target], edges: [] };
    const template = makeTemplate({ model_identifier: "tpl-model" });

    const result = applyAgentTemplateToCanvas(canvas, template, "ag1");

    const config = result.canvasState.nodes[0]!.data.configuration as AgentNodeConfiguration;
    expect(config.model_identifier).toBe("tpl-model");
    expect("max_tool_rounds" in config).toBe(false);
    expect(config.max_tool_rounds).toBeUndefined();
  });

  it("模板声明 max_tool_rounds → 写入模板值", () => {
    const baseConfig = makeAgentConfig();
    delete baseConfig.max_tool_rounds;
    const target = makeAgentNode("ag1", baseConfig);
    const canvas: WorkflowCanvasState = { nodes: [target], edges: [] };
    const template = makeTemplate({ model_identifier: "tpl-model", max_tool_rounds: 7 });

    const result = applyAgentTemplateToCanvas(canvas, template, "ag1");

    const config = result.canvasState.nodes[0]!.data.configuration as AgentNodeConfiguration;
    expect(config.max_tool_rounds).toBe(7);
  });
});

// =============================================================================
// TC-AT-06 recipe 分支：批量实例化（T16.4）
// =============================================================================

describe("applyAgentTemplateToCanvas — recipe 全量实例化", () => {
  it("TC-AT-06 nodes/edges 全量实例化、local_ref 映射正确、位置不重叠", () => {
    const canvas: WorkflowCanvasState = { nodes: [], edges: [] };
    const agentRecipeConfig = {
      node_type: "agent",
      model_identifier: "recipe-model",
      system_prompt: "recipe prompt",
      available_tools: [],
    };
    const template = makeTemplate(
      {},
      {
        kind: "recipe",
        recipe: {
          nodes: [
            {
              local_ref: "agent_main",
              node_type: "agent",
              name: "主 Agent",
              configuration: agentRecipeConfig,
            },
            {
              local_ref: "cond_check",
              node_type: "condition",
              name: "条件判断",
              configuration: { node_type: "condition" },
            },
          ],
          edges: [{ source_ref: "agent_main", target_ref: "cond_check" }],
        },
      },
    );

    const result = applyAgentTemplateToCanvas(canvas, template);

    expect(result.applied).toBe(true);
    expect(result.replacedNodeId).toBeNull();
    expect(result.createdNodeIds).toEqual([
      "tpl_test_agent_main_1",
      "tpl_test_cond_check_1",
    ]);
    expect(result.canvasState.nodes).toHaveLength(2);
    expect(result.canvasState.edges).toHaveLength(1);

    // local_ref → node_id 映射：边 source/target 指向新建节点 id
    const edge = result.canvasState.edges[0]!;
    expect(edge.id).toBe("tpl_test_1_edge_1");
    expect(edge.source).toBe("tpl_test_agent_main_1");
    expect(edge.target).toBe("tpl_test_cond_check_1");
    expect(edge.data?.edge_id).toBe("tpl_test_1_edge_1");

    // recipe 节点配置原样落位
    const mainNode = result.canvasState.nodes.find(
      (node) => node.id === "tpl_test_agent_main_1",
    );
    expect(mainNode!.data.configuration).toBe(agentRecipeConfig);
    expect(mainNode!.data.name).toBe("主 Agent");

    // 网格布局：两节点位置不重叠
    const posA = result.canvasState.nodes[0]!.position;
    const posB = result.canvasState.nodes[1]!.position;
    expect(posA.x === posB.x && posA.y === posB.y).toBe(false);
  });
});

// =============================================================================
// TC-AT-07 预设键白名单防御过滤（T16.5 / INV-D10）
// =============================================================================

describe("applyAgentTemplateToCanvas — 预设键白名单防御", () => {
  it("TC-AT-07 越界键（sandbox_policy_id 等）被过滤并返回 reason，不写入画布", () => {
    const target = makeAgentNode("ag1");
    const canvas: WorkflowCanvasState = { nodes: [target], edges: [] };
    const template = makeTemplate({
      model_identifier: "tpl-model",
      sandbox_policy_id: "sp-evil",
      custom_evil_key: "evil",
    });

    const result = applyAgentTemplateToCanvas(canvas, template, "ag1");

    expect(result.applied).toBe(true);
    expect(result.reason).toContain("sandbox_policy_id");
    expect(result.reason).toContain("custom_evil_key");

    const serialized = JSON.stringify(result.canvasState);
    expect(serialized).not.toContain("sandbox_policy_id");
    expect(serialized).not.toContain("custom_evil_key");

    const config = result.canvasState.nodes[0]!.data.configuration as AgentNodeConfiguration;
    expect(config.model_identifier).toBe("tpl-model");
    expect("sandbox_policy_id" in config).toBe(false);
  });

  it("TC-AT-09 context_strategy 四值合法 → 写入节点配置；越界值 → 过滤", () => {
    const target = makeAgentNode("ag1");
    const canvas: WorkflowCanvasState = { nodes: [target], edges: [] };

    // 合法值：写入节点配置
    const okTemplate = makeTemplate({
      model_identifier: "tpl-model",
      context_strategy: "summary_compression",
    });
    const okResult = applyAgentTemplateToCanvas(canvas, okTemplate, "ag1");
    const okConfig = okResult.canvasState.nodes[0]!.data.configuration as AgentNodeConfiguration;
    expect(okConfig.context_strategy).toBe("summary_compression");

    // 越界值：过滤并附 reason，不写入节点配置
    const badTemplate = makeTemplate({
      context_strategy: "not_a_strategy",
    });
    const badResult = applyAgentTemplateToCanvas(canvas, badTemplate, "ag1");
    const badConfig = badResult.canvasState.nodes[0]!.data.configuration as AgentNodeConfiguration;
    expect("context_strategy" in badConfig).toBe(false);
  });
});

// =============================================================================
// TC-AT-08 纯函数入参不可变（T16.1 / 必做守护 ⑥ / INV-D13）
// =============================================================================

describe("applyAgentTemplateToCanvas — 入参不可变", () => {
  it("TC-AT-08 合并/新建/recipe 三分支均不修改入参 canvasState 与 template", () => {
    const mergeCanvas: WorkflowCanvasState = {
      nodes: [makeAgentNode("ag1")],
      edges: [],
    };
    const newCanvas: WorkflowCanvasState = { nodes: [makeTriggerNode("tr1")], edges: [] };
    const recipeCanvas: WorkflowCanvasState = { nodes: [], edges: [] };
    const mergeTemplate = makeTemplate({ model_identifier: "m1" });
    const recipeTemplate = makeTemplate(
      {},
      {
        kind: "recipe",
        recipe: {
          nodes: [
            {
              local_ref: "n1",
              node_type: "agent",
              name: "N1",
              configuration: { node_type: "agent" },
            },
          ],
          edges: [],
        },
      },
    );

    const snapshots = [
      JSON.stringify(mergeCanvas),
      JSON.stringify(newCanvas),
      JSON.stringify(recipeCanvas),
      JSON.stringify(mergeTemplate),
      JSON.stringify(recipeTemplate),
    ];

    applyAgentTemplateToCanvas(mergeCanvas, mergeTemplate, "ag1");
    applyAgentTemplateToCanvas(newCanvas, mergeTemplate);
    applyAgentTemplateToCanvas(recipeCanvas, recipeTemplate);

    expect(JSON.stringify(mergeCanvas)).toBe(snapshots[0]);
    expect(JSON.stringify(newCanvas)).toBe(snapshots[1]);
    expect(JSON.stringify(recipeCanvas)).toBe(snapshots[2]);
    expect(JSON.stringify(mergeTemplate)).toBe(snapshots[3]);
    expect(JSON.stringify(recipeTemplate)).toBe(snapshots[4]);
  });
});

// =============================================================================
// fix_agent_null_length E2-f：模板合并链 base 侧归一守卫
//（P1 隐患实证 toThrow "existing is not iterable" → P2 L3/G-03 定稿转绿）
// =============================================================================

describe("applyAgentTemplateToCanvas base.available_tools 归一守卫（fix_agent_null_length E2-f / G-03 / L-01）", () => {
  it("base null + preset 合法数组 → 合并成功不再抛错（P1 实证 [...null] not iterable 已断根）", () => {
    const baseConfig = {
      node_type: "agent",
      model_identifier: "m-1",
      system_prompt: "p",
      available_tools: null,
    } as unknown as AgentNodeConfiguration;
    const canvas: WorkflowCanvasState = { nodes: [makeAgentNode("ag_null", baseConfig)], edges: [] };
    const template = makeTemplate({ available_tools: ["tool_x"] });
    // 未指定目标且画布恰一个 agent 节点 → 走合并分支；
    // L3（G-03）：base 侧先经 normalizeStringArrayField（null → []），再与 preset 去重保序合并
    const result = applyAgentTemplateToCanvas(canvas, template);
    expect(result.applied).toBe(true);
    expect(result.replacedNodeId).toBe("ag_null");
    const merged = result.canvasState.nodes[0].data.configuration as AgentNodeConfiguration;
    expect(merged.available_tools).toEqual(["tool_x"]);
  });

  it("base available_tools 键缺失（undefined）+ preset 合法数组 → 合并成功（双态口径）", () => {
    const baseConfig = {
      node_type: "agent",
      model_identifier: "m-1",
      system_prompt: "p",
    } as unknown as AgentNodeConfiguration;
    const canvas: WorkflowCanvasState = { nodes: [makeAgentNode("ag_undef", baseConfig)], edges: [] };
    const template = makeTemplate({ available_tools: ["tool_y"] });
    const result = applyAgentTemplateToCanvas(canvas, template);
    expect(result.applied).toBe(true);
    const merged = result.canvasState.nodes[0].data.configuration as AgentNodeConfiguration;
    expect(merged.available_tools).toEqual(["tool_y"]);
  });

  it("base 合法数组 + preset 合法数组 → 去重保序合并零回归（既有顺序在前，AC-14）", () => {
    const canvas: WorkflowCanvasState = {
      nodes: [makeAgentNode("ag_ok", makeAgentConfig({ available_tools: ["a", "b"] }))],
      edges: [],
    };
    const template = makeTemplate({ available_tools: ["b", "c"] });
    const result = applyAgentTemplateToCanvas(canvas, template);
    const merged = result.canvasState.nodes[0].data.configuration as AgentNodeConfiguration;
    expect(merged.available_tools).toEqual(["a", "b", "c"]);
  });
});
