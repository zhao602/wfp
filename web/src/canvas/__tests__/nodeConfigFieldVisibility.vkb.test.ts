// =============================================================================
// vkb_write_mode 字段可见性测试（canvas/__tests__/nodeConfigFieldVisibility.vkb.test.ts）
//
// 覆盖 design §2.6.1(13)：可见性纯函数模式互斥（AC-09/AC-10）。
// 留待非沙箱环境执行：cd web && npx vitest run nodeConfigFieldVisibility.vkb
// =============================================================================

import { describe, it, expect } from "vitest";
import {
  isVectorKbModeFieldVisible,
} from "../nodeConfigFieldVisibility";
import type { VectorKnowledgeBaseNodeConfiguration } from "../../workflow-definition/definitionTypes";

function base(
  patch: Partial<VectorKnowledgeBaseNodeConfiguration> = {},
): VectorKnowledgeBaseNodeConfiguration {
  return {
    node_type: "vector_knowledge_base",
    knowledge_base_id: "kb1",
    query_mode: "manual",
    top_k: 5,
    ...patch,
  };
}

describe("vkb_write_mode 字段可见性", () => {
  it("默认 retrieve：检索字段可见、写入字段隐藏", () => {
    const cfg = base();
    expect(isVectorKbModeFieldVisible("query_mode", cfg)).toBe(true);
    expect(isVectorKbModeFieldVisible("top_k", cfg)).toBe(true);
    expect(isVectorKbModeFieldVisible("rerank_model", cfg)).toBe(true);
    expect(isVectorKbModeFieldVisible("write_source_mode", cfg)).toBe(false);
    expect(isVectorKbModeFieldVisible("write_text", cfg)).toBe(false);
    expect(isVectorKbModeFieldVisible("write_chunk_params", cfg)).toBe(false);
  });

  it("write 模式：写入字段可见、检索字段隐藏", () => {
    const cfg = base({ mode: "write" });
    expect(isVectorKbModeFieldVisible("query_mode", cfg)).toBe(false);
    expect(isVectorKbModeFieldVisible("top_k", cfg)).toBe(false);
    expect(isVectorKbModeFieldVisible("rerank_model", cfg)).toBe(false);
    expect(isVectorKbModeFieldVisible("write_source_mode", cfg)).toBe(true);
    expect(isVectorKbModeFieldVisible("write_chunk_strategy", cfg)).toBe(true);
    expect(isVectorKbModeFieldVisible("write_chunk_params", cfg)).toBe(true);
    expect(isVectorKbModeFieldVisible("write_document_id", cfg)).toBe(true);
  });

  it("write_text/write_reference 按 write_source_mode 互斥", () => {
    const manual = base({ mode: "write", write_source_mode: "manual" });
    expect(isVectorKbModeFieldVisible("write_text", manual)).toBe(true);
    expect(isVectorKbModeFieldVisible("write_reference", manual)).toBe(false);

    const reference = base({ mode: "write", write_source_mode: "reference" });
    expect(isVectorKbModeFieldVisible("write_text", reference)).toBe(false);
    expect(isVectorKbModeFieldVisible("write_reference", reference)).toBe(true);
  });

  it("检索模式下 query_value/query_reference 按 query_mode 互斥", () => {
    const manual = base({ query_mode: "manual" });
    expect(isVectorKbModeFieldVisible("query_value", manual)).toBe(true);
    expect(isVectorKbModeFieldVisible("query_reference", manual)).toBe(false);

    const reference = base({ query_mode: "reference" });
    expect(isVectorKbModeFieldVisible("query_value", reference)).toBe(false);
    expect(isVectorKbModeFieldVisible("query_reference", reference)).toBe(true);
  });

  it("未声明字段默认可见（安全降级）", () => {
    const cfg = base();
    expect(isVectorKbModeFieldVisible("embedding_model", cfg)).toBe(true);
    expect(isVectorKbModeFieldVisible("unknown_field", cfg)).toBe(true);
  });
});