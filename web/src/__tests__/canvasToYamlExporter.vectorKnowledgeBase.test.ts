// =============================================================================
// 向量知识库节点导出测试（__tests__/canvasToYamlExporter.vectorKnowledgeBase.test.ts）
//
// 留待非沙箱环境执行：cd web && npx vitest run canvasToYamlExporter.vectorKnowledgeBase
//
// 覆盖用例（design.md §4.1.2 批次 4-b 导出组，对应 tasks.md T5.4/T5.5 验收）：
//   VKB-E01 manual 全字段导出，无 api_key 键（AC-07/INV-D04）
//   VKB-E02 reference 模式导出 query_reference，非激活 query_value 不导出（AC-07/FR-07）
//   VKB-E03 空可选字段省略（AC-07）
//   VKB-E04 rerank_model 空模型标识省略，非空完整导出（FR-25）
//   VKB-E05 allow_degraded_fallback=false 省略（后端缺省，导出简洁）
//   VKB-E06 契约表 + 后端类型映射登记（AC-38）
//   VKB-E07 导出→导入回环还原节点与配置（AC-08）
// =============================================================================

import { describe, it, expect } from "vitest";
import {
  canvasToDefinition,
  canvasToYaml,
} from "../workflow-definition/canvasToYamlExporter";
import { yamlToCanvas } from "../workflow-definition/yamlToCanvasImporter";
import {
  NODE_TYPE_BACKEND_MAP,
  NODE_OUTPUT_CONTRACTS,
  resolveOutputContract,
} from "../canvas/nodeOutputContracts";
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

// ---------------------------------------------------------------------------
// 测试数据工厂
// ---------------------------------------------------------------------------

function makeNode(
  nodeId: string,
  name: string,
  config: NodeConfiguration,
  outputName?: string,
): CanvasNode {
  const data: CanvasNodeData = {
    node_id: nodeId,
    name,
    node_type: config.node_type,
    configuration: config,
  };
  if (outputName !== undefined) data.output_name = outputName;
  return { id: nodeId, type: config.node_type, position: { x: 0, y: 0 }, data };
}

function makeState(nodes: CanvasNode[], edges: CanvasEdge[] = []): WorkflowCanvasState {
  return { nodes, edges };
}

function getConfig(state: WorkflowCanvasState, index: number): NodeConfiguration {
  return canvasToDefinition(state).nodes[index].configuration;
}

/** 向量知识库配置工厂（manual 模式最小合法配置 + 可覆盖项） */
function makeVectorConfig(
  overrides: Partial<VectorKnowledgeBaseNodeConfiguration> = {},
): VectorKnowledgeBaseNodeConfiguration {
  return {
    node_type: "vector_knowledge_base",
    knowledge_base_id: "kb_main",
    query_mode: "manual",
    query_value: "量子计算的基本原理",
    top_k: 5,
    embedding_model: { mode: "api", model_identifier: "emb-model" },
    ...overrides,
  };
}

/** manual 全字段配置（含 rerank，全部非空） */
function manualFullConfig(): VectorKnowledgeBaseNodeConfiguration {
  return {
    node_type: "vector_knowledge_base",
    knowledge_base_id: "kb_main",
    query_mode: "manual",
    query_value: "量子计算的基本原理",
    top_k: 5,
    embedding_model: {
      mode: "api",
      model_identifier: "emb-model",
      base_url: "https://emb.example.com",
      timeout_seconds: 30,
    },
    rerank_model: {
      mode: "api",
      model_identifier: "rerank-model",
      base_url: "https://rerank.example.com",
      timeout_seconds: 10,
    },
  };
}

// ---------------------------------------------------------------------------
// 导出用例
// ---------------------------------------------------------------------------

describe("向量知识库节点导出（T5.5 sanitize）", () => {
  it("VKB-E01 manual 全字段导出且无 api_key 键（AC-07/INV-D04）", () => {
    const config = manualFullConfig();
    const yamlText = canvasToYaml(
      makeState([makeNode("n1", "向量知识库", config)]),
    );
    expect(yamlText).toContain("vector_knowledge_base");
    expect(yamlText).not.toContain("api_key");

    const result = getConfig(
      makeState([makeNode("n1", "向量知识库", config)]),
      0,
    ) as VectorKnowledgeBaseNodeConfiguration;
    // 全字段非空：sanitize 保留全部，结构逐字段一致
    expect(result).toEqual(config);
    expect(result.node_type).toBe("vector_knowledge_base");
    expect(result.knowledge_base_id).toBe("kb_main");
    expect(result.query_mode).toBe("manual");
    expect(result.query_value).toBe("量子计算的基本原理");
    expect(result.top_k).toBe(5);
    expect(result.embedding_model).toEqual({
      mode: "api",
      model_identifier: "emb-model",
      base_url: "https://emb.example.com",
      timeout_seconds: 30,
    });
    expect(result.rerank_model).toEqual({
      mode: "api",
      model_identifier: "rerank-model",
      base_url: "https://rerank.example.com",
      timeout_seconds: 10,
    });
  });

  it("VKB-E02 reference 模式导出 query_reference，非激活 query_value 不导出（AC-07/FR-07）", () => {
    const config = makeVectorConfig({
      query_mode: "reference",
      query_value: "不应导出的手动查询文本",
      query_reference: "upstream_output",
    });
    const result = getConfig(
      makeState([makeNode("n1", "向量知识库", config)]),
      0,
    ) as VectorKnowledgeBaseNodeConfiguration;
    expect(result.query_mode).toBe("reference");
    expect(result.query_reference).toBe("upstream_output");
    expect((result as unknown as Record<string, unknown>).query_value).toBeUndefined();
  });

  it("VKB-E03 空可选字段省略，必填字段保留（AC-07）", () => {
    const config = makeVectorConfig({
      query_value: "",
      embedding_model: { mode: "api", model_identifier: "emb-model" },
    });
    const result = getConfig(
      makeState([makeNode("n1", "向量知识库", config)]),
      0,
    ) as VectorKnowledgeBaseNodeConfiguration;
    expect((result as unknown as Record<string, unknown>).query_value).toBeUndefined();
    expect((result as unknown as Record<string, unknown>).query_reference).toBeUndefined();
    expect((result as unknown as Record<string, unknown>).rerank_model).toBeUndefined();
    expect(result.embedding_model).toEqual({
      mode: "api",
      model_identifier: "emb-model",
    });
    expect(result.knowledge_base_id).toBe("kb_main");
    expect(result.query_mode).toBe("manual");
    expect(result.top_k).toBe(5);
  });

  it("VKB-E04 rerank_model 空模型标识省略，非空 local 完整导出（FR-25）", () => {
    // 模型标识为空 → 省略（等同未启用重排）
    const emptyRerank = makeVectorConfig({
      rerank_model: { mode: "local", model_identifier: "", model_path: "/models/rerank" },
    });
    const omitted = getConfig(
      makeState([makeNode("n1", "向量知识库", emptyRerank)]),
      0,
    ) as VectorKnowledgeBaseNodeConfiguration;
    expect((omitted as unknown as Record<string, unknown>).rerank_model).toBeUndefined();

    // 非空 local → 完整导出
    const localRerank = makeVectorConfig({
      rerank_model: {
        mode: "local",
        model_identifier: "rerank-local",
        model_path: "/models/rerank",
        device: "gpu",
      },
    });
    const exported = getConfig(
      makeState([makeNode("n1", "向量知识库", localRerank)]),
      0,
    ) as VectorKnowledgeBaseNodeConfiguration;
    expect(exported.rerank_model).toEqual({
      mode: "local",
      model_identifier: "rerank-local",
      model_path: "/models/rerank",
      device: "gpu",
    });
  });

  it("VKB-E05 allow_degraded_fallback=false 省略、true 保留（导出简洁）", () => {
    // false 为后端缺省值：省略
    const fallbackFalse = makeVectorConfig({
      embedding_model: {
        mode: "local",
        model_identifier: "emb-local",
        model_path: "/models/emb",
        device: "cpu",
        allow_degraded_fallback: false,
      },
    });
    const omitted = getConfig(
      makeState([makeNode("n1", "向量知识库", fallbackFalse)]),
      0,
    ) as VectorKnowledgeBaseNodeConfiguration;
    expect(omitted.embedding_model).toEqual({
      mode: "local",
      model_identifier: "emb-local",
      model_path: "/models/emb",
      device: "cpu",
    });

    // true 显式导出
    const fallbackTrue = makeVectorConfig({
      embedding_model: {
        mode: "local",
        model_identifier: "emb-local",
        model_path: "/models/emb",
        device: "cpu",
        allow_degraded_fallback: true,
      },
    });
    const kept = getConfig(
      makeState([makeNode("n1", "向量知识库", fallbackTrue)]),
      0,
    ) as VectorKnowledgeBaseNodeConfiguration;
    expect(kept.embedding_model).toEqual({
      mode: "local",
      model_identifier: "emb-local",
      model_path: "/models/emb",
      device: "cpu",
      allow_degraded_fallback: true,
    });
  });

  it("VKB-E06 契约表与后端类型映射登记（AC-38）", () => {
    expect(NODE_TYPE_BACKEND_MAP.vector_knowledge_base).toBe("VECTOR_KNOWLEDGE_BASE");

    const contract = NODE_OUTPUT_CONTRACTS.vector_knowledge_base;
    expect(contract.category).toBe("片段集合（文本片段列表）");
    expect(contract.detail).toContain("top_k");
    expect(contract.referable).toBe(true);
    expect(contract.schemaEditable).toBe(false);
    expect(resolveOutputContract("vector_knowledge_base")).toBe(contract);
  });

  it("VKB-E07 导出→导入回环还原节点与配置（AC-08）", () => {
    const config = manualFullConfig();
    const state = makeState(
      [
        makeNode("n1", "触发器", {
          node_type: "trigger",
          trigger_type: "manual",
          input_template: {},
        }),
        makeNode("n2", "向量知识库", config, "vkb_output"),
      ],
      [{ id: "e1", source: "n1", target: "n2" }],
    );

    const importResult = yamlToCanvas(canvasToYaml(state));
    expect(importResult.ok).toBe(true);
    if (!importResult.ok) {
      throw new Error(importResult.error);
    }
    const restored = importResult.canvasState;

    // 节点/连线数量还原
    expect(restored.nodes).toHaveLength(2);
    expect(restored.edges).toHaveLength(1);

    // 向量知识库节点配置逐字段还原（全字段非空，sanitize 无损）
    const restoredVkb = restored.nodes.find((n) => n.data.node_id === "n2");
    expect(restoredVkb).toBeDefined();
    expect(restoredVkb?.data.node_type).toBe("vector_knowledge_base");
    expect(restoredVkb?.data.configuration).toEqual(config);
    expect(restoredVkb?.data.output_name).toBe("vkb_output");

    // 连线端点还原
    expect(restored.edges[0].source).toBe("n1");
    expect(restored.edges[0].target).toBe("n2");
  });
});
