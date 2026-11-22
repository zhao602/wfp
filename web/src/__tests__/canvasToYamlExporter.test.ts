// =============================================================================
// 画布导出 YAML 测试（src/__tests__/canvasToYamlExporter.test.ts）
//
// 覆盖验收：A11（导出结构满足五要素且导出后再导入结构与配置一致）
// =============================================================================

import { describe, it, expect } from "vitest";
import { canvasToYaml, canvasToDefinition } from "../workflow-definition/canvasToYamlExporter";
import { yamlToCanvas } from "../workflow-definition/yamlToCanvasImporter";
import type { WorkflowCanvasState } from "../canvas/canvasTypes";

/** 构造测试用画布状态：两节点一连线 */
function makeTestCanvasState(): WorkflowCanvasState {
  return {
    nodes: [
      {
        id: "trigger_1",
        type: "trigger",
        position: { x: 0, y: 0 },
        data: {
          node_id: "trigger_1",
          name: "触发器",
          node_type: "trigger",
          configuration: {
            node_type: "trigger",
            trigger_type: "manual",
            input_template: { topic: "topic" },
          },
        },
      },
      {
        id: "agent_1",
        type: "agent",
        position: { x: 250, y: 0 },
        data: {
          node_id: "agent_1",
          name: "分析",
          node_type: "agent",
          output_name: "analysis",
          configuration: {
            node_type: "agent",
            model_identifier: "primary-model",
            system_prompt: "分析数据",
            available_tools: [],
          },
        },
      },
    ],
    edges: [
      {
        id: "e1",
        source: "trigger_1",
        target: "agent_1",
        data: { edge_id: "e1" },
      },
    ],
  };
}

describe("canvasToYamlExporter", () => {
  it("导出 YAML 应包含五要素", () => {
    const canvasState = makeTestCanvasState();
    const yamlText = canvasToYaml(canvasState);

    expect(yamlText).toContain("spec_version");
    expect(yamlText).toContain("definition_id");
    expect(yamlText).toContain("name");
    expect(yamlText).toContain("definition_version");
    expect(yamlText).toContain("nodes");
    expect(yamlText).toContain("edges");
  });

  it("导出定义应包含正确数量的节点和连线", () => {
    const canvasState = makeTestCanvasState();
    const definition = canvasToDefinition(canvasState);

    expect(definition.nodes).toHaveLength(2);
    expect(definition.edges).toHaveLength(1);
    expect(definition.spec_version).toBe("1.0");
  });

  it("导出后再导入应得到等价的节点结构", () => {
    const canvasState = makeTestCanvasState();
    const yamlText = canvasToYaml(canvasState);
    const importResult = yamlToCanvas(yamlText);

    expect(importResult.ok).toBe(true);
    if (importResult.ok) {
      expect(importResult.canvasState.nodes).toHaveLength(2);
      expect(importResult.canvasState.edges).toHaveLength(1);

      const nodeIds = importResult.canvasState.nodes.map((n) => n.id);
      expect(nodeIds).toContain("trigger_1");
      expect(nodeIds).toContain("agent_1");
    }
  });

  it("节点类型应正确保留", () => {
    const canvasState = makeTestCanvasState();
    const definition = canvasToDefinition(canvasState);

    const nodeTypes = definition.nodes.map((n) => n.node_type);
    expect(nodeTypes).toContain("trigger");
    expect(nodeTypes).toContain("agent");
  });
});
