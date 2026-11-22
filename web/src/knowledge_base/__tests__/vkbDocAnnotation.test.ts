// =============================================================================
// vkb_doc_annotation 前端单测（web/src/knowledge_base/__tests__/vkbDocAnnotation.test.ts）
//
// 覆盖 CHG-31：类型守卫与窄化（禁 any）；标注展示格式化（published_at=null →
// "未知"）；节点标注字段可见性谓词。
// 留待非沙箱环境执行：cd web && npx vitest run vkbDocAnnotation
// =============================================================================

import { describe, it, expect } from "vitest";
import {
  formatPublishedAt,
  resolveChunkAnnotations,
} from "../kbManagementTypes";
import { isVectorKbModeFieldVisible } from "../../canvas/nodeConfigFieldVisibility";
import type { VectorKnowledgeBaseNodeConfiguration } from "../../workflow-definition/definitionTypes";

describe("resolveChunkAnnotations 类型守卫", () => {
  it("正常形态窄化成功", () => {
    const annotations = resolveChunkAnnotations({
      document_id: "d1",
      annotations: {
        origin: "官网",
        published_at: "2024-01-01",
        ingested_at: "2024-02-02",
        custom: { 版本: "v2" },
      },
    });
    expect(annotations?.origin).toBe("官网");
    expect(annotations?.published_at).toBe("2024-01-01");
    expect(annotations?.custom?.["版本"]).toBe("v2");
  });

  it("published_at 缺失 → null（未知）", () => {
    const annotations = resolveChunkAnnotations({
      annotations: { origin: "S" },
    });
    expect(annotations?.published_at).toBeNull();
  });

  it("缺项 / 形态不符 → undefined（安全降级）", () => {
    expect(resolveChunkAnnotations(undefined)).toBeUndefined();
    expect(resolveChunkAnnotations({})).toBeUndefined();
    expect(resolveChunkAnnotations({ annotations: null })).toBeUndefined();
    expect(
      resolveChunkAnnotations({ annotations: { no_origin: true } }),
    ).toBeUndefined();
  });

  it("formatPublishedAt：null → 未知，非空 → 原值", () => {
    expect(formatPublishedAt({ published_at: null, origin: "S" })).toBe("未知");
    expect(
      formatPublishedAt({ published_at: "2024-01-01", origin: "S" }),
    ).toBe("2024-01-01");
  });
});

describe("节点标注字段可见性", () => {
  const base = (
    patch: Partial<VectorKnowledgeBaseNodeConfiguration> = {},
  ): VectorKnowledgeBaseNodeConfiguration => ({
    node_type: "vector_knowledge_base",
    knowledge_base_id: "kb1",
    query_mode: "manual",
    top_k: 5,
    ...patch,
  });

  it("写入模式下标注字段可见", () => {
    const config = base({ mode: "write" });
    expect(isVectorKbModeFieldVisible("annotation_origin", config)).toBe(true);
    expect(isVectorKbModeFieldVisible("annotation_published_at", config)).toBe(
      true,
    );
    expect(isVectorKbModeFieldVisible("annotation_custom", config)).toBe(true);
    expect(isVectorKbModeFieldVisible("annotation_enabled", config)).toBe(true);
  });

  it("检索模式下标注字段隐藏", () => {
    const config = base();
    expect(isVectorKbModeFieldVisible("annotation_origin", config)).toBe(false);
    expect(isVectorKbModeFieldVisible("annotation_custom", config)).toBe(false);
  });
});