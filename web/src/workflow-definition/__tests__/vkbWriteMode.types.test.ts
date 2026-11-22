// =============================================================================
// vkb_write_mode 前端类型契约测试（workflow-definition/__tests__/vkbWriteMode.types.test.ts）
//
// 覆盖 design §2.6.1(12)：类型守卫与旧 YAML（AC-12）。
// 留待非沙箱环境执行：cd web && npx vitest run vkbWriteMode.types
// =============================================================================

import { describe, it, expect } from "vitest";
import {
  isVectorKnowledgeBaseConfig,
} from "../definitionTypes";
import type {
  NodeConfiguration,
  VectorKnowledgeBaseNodeConfiguration,
} from "../definitionTypes";

describe("vkb_write_mode 前端类型契约", () => {
  it("write 模式全字段可构造且取值正确", () => {
    const cfg: VectorKnowledgeBaseNodeConfiguration = {
      node_type: "vector_knowledge_base",
      knowledge_base_id: "kb1",
      query_mode: "manual",
      top_k: 5,
      mode: "write",
      write_source_mode: "reference",
      write_reference: "up_out",
      write_chunk_strategy: "recursive",
      write_chunk_params: {
        max_chunk_length: 800,
        overlap: 100,
        similarity_threshold: 0.8,
        hierarchy: ["^#"],
      },
      write_document_id: "kb1:n1",
    };
    expect(cfg.mode).toBe("write");
    expect(cfg.write_source_mode).toBe("reference");
    expect(cfg.write_chunk_params?.max_chunk_length).toBe(800);
    expect(cfg.write_document_id).toBe("kb1:n1");
  });

  it("旧配置（无 mode/写入字段）类型守卫仍通过", () => {
    const cfg = {
      node_type: "vector_knowledge_base",
      knowledge_base_id: "kb_old",
      query_mode: "manual",
      query_value: "q",
      top_k: 5,
    } as VectorKnowledgeBaseNodeConfiguration;
    expect(isVectorKnowledgeBaseConfig(cfg as NodeConfiguration)).toBe(true);
    expect(cfg.mode).toBeUndefined();
  });
});