// =============================================================================
// 画布导出守卫测试（workflow-definition/__tests__/canvasToYamlExporter.guard.test.ts）
//
// 执行：cd web && npx vitest run src/workflow-definition/__tests__/canvasToYamlExporter.guard.test.ts
//
// 覆盖（fix_agent_null_length 隐患 L-13 / L-14 / DC-05，五要素映射）：
//   L-13-a dynamic_injector node_templates=null → 导出不崩溃，产物回落空清单 []
//   L-13-b node_templates 键缺失（undefined）→ 同口径
//   L-13-c 合法对照：非空模板清单导出保真（template_id 保留）
//   L-14-a collectReferenceIssues：tool argument_mapping=null → 不崩溃、零 issue
//   L-14-b collectReferenceIssues：agent inputs 条目 reference=null → 不崩溃、零 issue
//   L-14-c 合法对照：悬空引用照常产出 issue（既有校验功能零回归）
// =============================================================================

import { describe, it, expect } from "vitest";
import yaml from "yaml";
import { canvasToYaml, collectReferenceIssues } from "../canvasToYamlExporter";
import type { WorkflowCanvasState, CanvasNode } from "../../canvas/canvasTypes";
import type {
  NodeConfiguration,
  DynamicInjectorNodeConfiguration,
  ToolNodeConfiguration,
  AgentNodeConfiguration,
} from "../definitionTypes";

/** 构造画布节点壳 */
function makeNode(
  nodeId: string,
  nodeType: string,
  configuration: NodeConfiguration,
): CanvasNode {
  return {
    id: nodeId,
    type: nodeType,
    position: { x: 0, y: 0 },
    data: {
      node_id: nodeId,
      name: `节点-${nodeId}`,
      node_type: nodeType as CanvasNode["data"]["node_type"],
      configuration,
    },
  };
}

/** 单节点画布 */
function makeState(node: CanvasNode): WorkflowCanvasState {
  return { nodes: [node], edges: [] };
}

// ---------------------------------------------------------------------------
// L-13 sanitizeDynamicInjectorConfig node_templates 空态导出
// ---------------------------------------------------------------------------

describe("L-13 dynamic_injector node_templates 导出守卫（fix_agent_null_length）", () => {
  const base = {
    node_type: "dynamic_injector" as const,
    injection_condition: "items",
    max_injection_count: 5,
  };

  it("L-13-a node_templates=null：导出不崩溃，YAML 产物回落 node_templates: []", () => {
    const config = {
      ...base,
      node_templates: null,
    } as unknown as DynamicInjectorNodeConfiguration;
    const text = canvasToYaml(makeState(makeNode("di_1", "dynamic_injector", config)));
    const parsed = yaml.parse(text) as {
      nodes: Array<{ configuration: { node_templates: unknown } }>;
    };
    expect(parsed.nodes[0].configuration.node_templates).toEqual([]);
  });

  it("L-13-b node_templates 键缺失（undefined）：同口径回落 []", () => {
    const config = base as unknown as DynamicInjectorNodeConfiguration;
    const text = canvasToYaml(makeState(makeNode("di_1", "dynamic_injector", config)));
    const parsed = yaml.parse(text) as {
      nodes: Array<{ configuration: { node_templates: unknown } }>;
    };
    expect(parsed.nodes[0].configuration.node_templates).toEqual([]);
  });

  it("L-13-c 合法对照：非空模板清单导出保真（template_id 保留）", () => {
    const config: DynamicInjectorNodeConfiguration = {
      ...base,
      node_templates: [
        {
          template_id: "tpl-1",
          node_type: "agent",
          name: "模板-1",
          configuration: {},
        },
      ],
    };
    const text = canvasToYaml(makeState(makeNode("di_1", "dynamic_injector", config)));
    const parsed = yaml.parse(text) as {
      nodes: Array<{
        configuration: {
          node_templates: Array<{ template_id: string }>;
        };
      }>;
    };
    expect(parsed.nodes[0].configuration.node_templates.length).toBe(1);
    expect(parsed.nodes[0].configuration.node_templates[0].template_id).toBe("tpl-1");
  });
});

// ---------------------------------------------------------------------------
// L-14 collectReferenceIssues mapping / 条目空态守卫
// ---------------------------------------------------------------------------

describe("L-14 collectReferenceIssues 空态守卫（fix_agent_null_length）", () => {
  it("L-14-a tool argument_mapping=null：不崩溃、零 issue", () => {
    const config = {
      node_type: "tool" as const,
      tool_identifier: "legacy_tool",
      tool_source: "local" as const,
      argument_mapping: null,
      static_arguments: {},
    } as unknown as ToolNodeConfiguration;
    const issues = collectReferenceIssues(
      makeState(makeNode("tool_1", "tool", config)),
    );
    expect(issues).toEqual([]);
  });

  it("L-14-b agent inputs 条目 reference=null：不崩溃、零 issue", () => {
    const config = {
      node_type: "agent" as const,
      model_identifier: "m-1",
      system_prompt: "p",
      available_tools: [],
      inputs: [{ reference: null as unknown as string }],
    } as unknown as AgentNodeConfiguration;
    const issues = collectReferenceIssues(
      makeState(makeNode("agent_1", "agent", config)),
    );
    expect(issues).toEqual([]);
  });

  it("L-14-c 合法对照：悬空引用照常产出 issue（tool 与 agent 双通道）", () => {
    const toolConfig: ToolNodeConfiguration = {
      node_type: "tool",
      tool_identifier: "t",
      tool_source: "local",
      argument_mapping: { q: "ghost_node.out" },
      static_arguments: {},
    };
    const toolIssues = collectReferenceIssues(
      makeState(makeNode("tool_1", "tool", toolConfig)),
    );
    expect(toolIssues.length).toBe(1);
    expect(toolIssues[0].field).toBe("argument_mapping");
    expect(toolIssues[0].reference).toBe("ghost_node.out");

    const agentConfig: AgentNodeConfiguration = {
      node_type: "agent",
      model_identifier: "m-1",
      system_prompt: "p",
      available_tools: [],
      inputs: [{ reference: "ghost_node.out" }],
    };
    const agentIssues = collectReferenceIssues(
      makeState(makeNode("agent_1", "agent", agentConfig)),
    );
    expect(agentIssues.length).toBe(1);
    expect(agentIssues[0].field).toBe("inputs");
  });
});
