// =============================================================================
// 数据转换操作元数据回归测试（canvas/__tests__/dataTransformOperations.test.ts）
//
// 执行：cd web && npx vitest run src/canvas/__tests__/dataTransformOperations.test.ts
//
// 覆盖（expand_data_transform_ops T8.7 / DC-09.1，design §8.1-B）：
//   DTM-01 四分类齐全：OPERATION_CATEGORIES 顺序与标签完整，各分类非空
//   DTM-02 操作全量：21 项与后端 enums.py DataTransformOperation 逐项对齐、无重复
//   DTM-03 既有四项归类：map/filter/aggregate/join 均归入 basic（FR-21）
//   DTM-04 未知操作降级：getOperationMeta 未知/空串安全返回 undefined
//   DTM-05 元数据完整：label/degradableNote 非空、paramFields 字段名步骤内唯一
//   DTM-06 常量口径：步数上限 20；输出格式 4 项且 dataset 居首（缺省形态）
//   DTM-07 needsCondition 仅 filter：条件区挂载判定唯一（DC-09.3）
// =============================================================================

import { describe, it, expect } from "vitest";
import {
  OPERATION_CATEGORIES,
  OPERATION_CATEGORY_LABELS,
  MAX_OPERATION_CHAIN_STEPS,
  OUTPUT_FORMAT_OPTIONS,
  getOperationMeta,
  getOperationsByCategory,
} from "../dataTransformOperations";
import type { OperationCategory, OperationMeta } from "../dataTransformOperations";

/** 后端 enums.py DataTransformOperation 全量（21 项，对齐口径） */
const BACKEND_OPERATION_IDS: readonly string[] = [
  "map",
  "filter",
  "aggregate",
  "join",
  "sort",
  "distinct",
  "rename",
  "select_columns",
  "drop_columns",
  "cast",
  "row_number",
  "flatten",
  "explode",
  "handle_nulls",
  "regex_extract",
  "regex_replace",
  "validate",
  "masking",
  "json_path_extract",
  "sampling",
  "custom_code",
];

/** 全量元数据（按分类展平，保持定义顺序） */
function allOperationMetas(): OperationMeta[] {
  const grouped = getOperationsByCategory();
  const metas: OperationMeta[] = [];
  for (const category of OPERATION_CATEGORIES) {
    metas.push(...grouped[category]);
  }
  return metas;
}

describe("dataTransformOperations 元数据（DC-09.1）", () => {
  it("DTM-01 四分类齐全且各分类非空", () => {
    expect(OPERATION_CATEGORIES).toEqual(["basic", "cleaning", "ai", "advanced"]);
    for (const category of OPERATION_CATEGORIES) {
      expect(OPERATION_CATEGORY_LABELS[category].length).toBeGreaterThan(0);
      expect(getOperationsByCategory()[category].length).toBeGreaterThan(0);
    }
  });

  it("DTM-02 操作全量 21 项与后端枚举逐项对齐且无重复", () => {
    const metas = allOperationMetas();
    expect(metas.map((meta) => meta.id)).toEqual(BACKEND_OPERATION_IDS);
    expect(new Set(metas.map((meta) => meta.id)).size).toBe(BACKEND_OPERATION_IDS.length);
  });

  it("DTM-03 既有四项均归入 basic 分类（FR-21）", () => {
    const basicIds = getOperationsByCategory().basic.map((meta) => meta.id);
    for (const id of ["map", "filter", "aggregate", "join"]) {
      expect(basicIds).toContain(id);
      const meta = getOperationMeta(id);
      expect(meta?.category).toBe("basic");
    }
    const labels = getOperationsByCategory().basic
      .filter((meta) => ["map", "filter", "aggregate", "join"].includes(meta.id))
      .map((meta) => meta.label);
    expect(labels).toEqual(["映射", "过滤", "聚合", "连接"]);
  });

  it("DTM-04 未知操作安全降级 undefined", () => {
    expect(getOperationMeta("no_such_operation")).toBeUndefined();
    expect(getOperationMeta("")).toBeUndefined();
  });

  it("DTM-05 元数据完整：label/degradableNote 非空、paramFields 字段名步骤内唯一", () => {
    for (const meta of allOperationMetas()) {
      expect(meta.label.length).toBeGreaterThan(0);
      expect(meta.degradableNote.length).toBeGreaterThan(0);
      const fields = meta.paramFields.map((spec) => spec.field);
      expect(new Set(fields).size).toBe(fields.length);
    }
  });

  it("DTM-06 步数上限 20；输出格式 4 项且 dataset 居首", () => {
    expect(MAX_OPERATION_CHAIN_STEPS).toBe(20);
    expect(OUTPUT_FORMAT_OPTIONS.map((option) => option.id)).toEqual([
      "dataset",
      "single_object",
      "text",
      "kv",
    ]);
    for (const option of OUTPUT_FORMAT_OPTIONS) {
      expect(option.label.length).toBeGreaterThan(0);
    }
  });

  it("DTM-07 needsCondition 仅 filter 为 true（DC-09.3）", () => {
    for (const meta of allOperationMetas()) {
      expect(meta.needsCondition).toBe(meta.id === "filter");
    }
  });

  it("DTM-08 分类归属计数：basic 13 / cleaning 5 / ai 1 / advanced 2", () => {
    const counts: Record<OperationCategory, number> = {
      basic: 0,
      cleaning: 0,
      ai: 0,
      advanced: 0,
    };
    for (const meta of allOperationMetas()) {
      counts[meta.category] += 1;
    }
    expect(counts).toEqual({ basic: 13, cleaning: 5, ai: 1, advanced: 2 });
  });
});
