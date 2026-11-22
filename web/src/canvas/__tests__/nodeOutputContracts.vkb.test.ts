// =============================================================================
// vkb_write_mode 输出契约测试（canvas/__tests__/nodeOutputContracts.vkb.test.ts）
//
// 覆盖 design §2.6.1(15)：输出契约按模式变体（AC-51/AC-52）。
// 留待非沙箱环境执行：cd web && npx vitest run nodeOutputContracts.vkb
// =============================================================================

import { describe, it, expect } from "vitest";
import {
  NODE_OUTPUT_CONTRACTS,
  resolveOutputContract,
} from "../nodeOutputContracts";

describe("vkb_write_mode 输出契约", () => {
  it("顶层取值不变（既有消费方零影响，R6）", () => {
    const contract = NODE_OUTPUT_CONTRACTS.vector_knowledge_base;
    expect(contract.category).toBe("片段集合（文本片段列表）");
    expect(contract.referable).toBe(true);
    expect(contract.schemaEditable).toBe(false);
  });

  it("按模式差异化：write=写入报告 / retrieve=片段集合", () => {
    const contract = NODE_OUTPUT_CONTRACTS.vector_knowledge_base;
    expect(contract.variantByMode?.write?.category).toBe("写入报告");
    expect(contract.variantByMode?.retrieve?.category).toBe(
      "片段集合（文本片段列表）",
    );
    expect(contract.variantByMode?.write?.detail).toContain("写入报告");
  });

  it("resolveOutputContract 仍恒命中向量知识库并透出顶层契约", () => {
    expect(resolveOutputContract("vector_knowledge_base")).toBe(
      NODE_OUTPUT_CONTRACTS.vector_knowledge_base,
    );
  });
});