// =============================================================================
// vkb_write_mode 导出往返测试（__tests__/canvasToYamlExporter.vkbWriteMode.test.ts）
//
// 覆盖 design §2.6.1(15 导出)：写入字段保留与旧 YAML 往返（AC-07/INV-05）。
// 留待非沙箱环境执行：cd web && npx vitest run canvasToYamlExporter.vkbWriteMode
// =============================================================================

import { describe, it, expect } from "vitest";
import {
  canvasToDefinition,
  canvasToYaml,
} from "../workflow-definition/canvasToYamlExporter";
import type {
  WorkflowCanvasState,
  CanvasNode,
  CanvasNodeData,
  CanvasEdge,
} from "../canvas/canvasTypes";
import type {
  NodeConfiguration,
  VectorKnowledgeBaseNodeConfiguration,
} from "../workflow-definition/definitionTypes";

function makeNode(
  nodeId: string,
  name: string,
  config: NodeConfiguration,
): CanvasNode {
  const data: CanvasNodeData = {
    node_id: nodeId,
    name,
    node_type: config.node_type,
    configuration: config,
  };
  return { id: nodeId, type: config.node_type, position: { x: 0, y: 0 }, data };
}

function makeState(
  nodes: CanvasNode[],
  edges: CanvasEdge[] = [],
): WorkflowCanvasState {
  return { nodes, edges };
}

function writeConfig(): VectorKnowledgeBaseNodeConfiguration {
  return {
    node_type: "vector_knowledge_base",
    knowledge_base_id: "kb_main",
    query_mode: "manual",
    top_k: 5,
    mode: "write",
    write_source_mode: "manual",
    write_text: "待写入内容",
    write_chunk_strategy: "recursive",
    write_chunk_params: { max_chunk_length: 800, overlap: 100 },
    write_document_id: "kb_main:n1",
  };
}

describe("vkb_write_mode 导出保留写入字段", () => {
  it("写入模式全字段导出并在往返后保留", () => {
    const config = writeConfig();
    const state = makeState([makeNode("n1", "向量知识库", config)]);
    const yamlText = canvasToYaml(state);
    expect(yamlText).toContain("mode: write");
    expect(yamlText).toContain("write_text");
    expect(yamlText).toContain("write_chunk_params");
    expect(yamlText).toContain("write_document_id");

    const parsed = canvasToDefinition(state).nodes[0]
      .configuration as VectorKnowledgeBaseNodeConfiguration;
    expect(parsed.mode).toBe("write");
    expect(parsed.write_text).toBe("待写入内容");
    expect(parsed.write_chunk_params?.max_chunk_length).toBe(800);
    expect(parsed.write_document_id).toBe("kb_main:n1");
  });

  it("旧 YAML（retrieve，无写入字段）往返一致：不产生写入字段", () => {
    const legacy: VectorKnowledgeBaseNodeConfiguration = {
      node_type: "vector_knowledge_base",
      knowledge_base_id: "kb_old",
      query_mode: "manual",
      query_value: "查询文本",
      top_k: 5,
    };
    const state = makeState([makeNode("n1", "向量知识库", legacy)]);
    const yamlText = canvasToYaml(state);
    expect(yamlText).not.toContain("write_text");
    expect(yamlText).not.toContain("mode: write");

    const parsed = canvasToDefinition(state).nodes[0]
      .configuration as VectorKnowledgeBaseNodeConfiguration;
    expect(parsed.mode).toBeUndefined();
    expect(parsed.write_text).toBeUndefined();
  });
});