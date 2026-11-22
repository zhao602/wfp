// =============================================================================
// vkb_write_mode 提示文案测试（canvas/__tests__/nodeConfigHints.vkb.test.ts）
//
// 覆盖 design §2.6.1(14)：提示文案条目（AC-11）。
// 留待非沙箱环境执行：cd web && npx vitest run nodeConfigHints.vkb
// =============================================================================

import { describe, it, expect } from "vitest";
import { resolveFieldHint } from "../nodeConfigHints";

const FIELDS = [
  "mode",
  "write_source_mode",
  "write_text",
  "write_reference",
  "write_chunk_strategy",
  "write_chunk_params",
  "write_document_id",
] as const;

describe("vkb_write_mode 提示文案", () => {
  it.each(FIELDS)("字段 %s 具备提示文案", (field) => {
    const hint = resolveFieldHint("vector_knowledge_base", field);
    expect(hint).toBeDefined();
    expect(hint?.helper).toBeTruthy();
  });

  it("mode 提示说明检索/写入两种模式", () => {
    const hint = resolveFieldHint("vector_knowledge_base", "mode");
    expect(hint?.helper).toContain("检索");
    expect(hint?.helper).toContain("写入");
  });
});