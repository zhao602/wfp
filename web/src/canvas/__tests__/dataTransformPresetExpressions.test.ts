// =============================================================================
// 数据转换预制表达式常量单测（canvas/__tests__/dataTransformPresetExpressions.test.ts）
//
// 执行：cd web && npx vitest run src/canvas/__tests__/dataTransformPresetExpressions.test.ts
//
// 覆盖用例（design §8.1-A，T-A1~T-A9）：
//   T-A1 锁值            PRESET_EXPRESSIONS.map/.filter === "True"（FR-02/AC-02）
//   T-A2 白名单等价      预制值为纯字面量形态，不含函数调用/下标/赋值/导入字符（INV-D08）
//   T-A3 缺项降级        getPresetExpression("aggregate"/"join") === undefined（FR-01/INV-D07）
//   T-A4 空值口径        isBlank 对 undefined/""/null 为 true，对空白串/非空为 false（DC-03）
//   T-A5 补空正例        空 params 切目标类型 → 补对应预制值（FR-04/AC-04）
//   T-A6 不覆盖反例      非空用户值 + 切换 → 原值保留、目标字段另行补空（FR-04/AC-04）
//   T-A7 不臆造          切 aggregate/join → params 逐字段不变、无新字段（INV-D07）
//   T-A8 幂等            连续两次 buildOperationChange 结果深等价（INV-D02）
//   T-A9 不可变          返回新对象、入参未被修改（INV-D02）
// =============================================================================

import { describe, it, expect } from "vitest";
import {
  PRESET_EXPRESSIONS,
  getPresetExpression,
  isPresetExpression,
  isBlank,
  buildOperationChange,
} from "../dataTransformPresetExpressions";
import type { DataTransformNodeConfiguration } from "../../workflow-definition/definitionTypes";

/** 构造合法单 operation + params 的 data_transform 配置（fixture 约束：禁用废弃 operations 数组） */
function makeConfig(
  overrides?: Partial<Pick<DataTransformNodeConfiguration, "operation" | "params">>,
): DataTransformNodeConfiguration {
  return {
    node_type: "data_transform",
    operation: "map",
    input_dataset_reference: "",
    params: {},
    ...overrides,
  };
}

describe("dataTransformPresetExpressions 常量与纯函数", () => {
  it("T-A1 锁值：map/filter 预制值均为 True（FR-02/AC-02）", () => {
    expect(PRESET_EXPRESSIONS.map).toBe("True");
    expect(PRESET_EXPRESSIONS.filter).toBe("True");
  });

  it("T-A2 白名单等价：预制值为纯字面量形态，不含白名单外语法字符（INV-D08）", () => {
    // 前端无 Python AST 解析器，按 DC-06 退化为确定性断言：
    // 预制值必须保持"纯字面量"形态——不含函数调用 "("、下标 "["、赋值 "="、
    // 导入 "import"、属性访问 "." 等白名单外/易越界语法字符。
    // ⚠ 后端 RestrictedExpressionEvaluator._ALLOWED_NODES 白名单变更时人工复核本断言。
    const forbiddenFragments = ["(", "[", "=", "import", "."] as const;
    for (const value of [PRESET_EXPRESSIONS.map, PRESET_EXPRESSIONS.filter]) {
      expect(value.length).toBeGreaterThan(0);
      for (const fragment of forbiddenFragments) {
        expect(value.includes(fragment)).toBe(false);
      }
    }
  });

  it("T-A3 缺项降级：aggregate/join 无预制值（FR-01/INV-D07）", () => {
    expect(getPresetExpression("map")).toBe("True");
    expect(getPresetExpression("filter")).toBe("True");
    expect(getPresetExpression("aggregate")).toBeUndefined();
    expect(getPresetExpression("join")).toBeUndefined();
    expect(isPresetExpression("aggregate", "True")).toBe(false);
    expect(isPresetExpression("join", "True")).toBe(false);
  });

  it("T-A4 空值口径：undefined/\"\"/null 判空，空白串与非空判非空（DC-03）", () => {
    expect(isBlank(undefined)).toBe(true);
    expect(isBlank("")).toBe(true);
    expect(isBlank(null)).toBe(true);
    expect(isBlank("   ")).toBe(false);
    expect(isBlank("x")).toBe(false);
  });

  it("T-A5 补空正例：空 params 切 filter 补 True；空 map 字段切回 map 补 True（FR-04/AC-04）", () => {
    const config = makeConfig({ operation: "map", params: {} });
    const switched = buildOperationChange(config, "filter");
    expect(switched.operation).toBe("filter");
    expect(switched.params.filter_condition).toBe("True");

    const back = buildOperationChange(switched, "map");
    expect(back.operation).toBe("map");
    expect(back.params.map_expression).toBe("True");
    // 补空不删除其它类型已补字段
    expect(back.params.filter_condition).toBe("True");
  });

  it("T-A6 不覆盖反例：非空用户值切换后保留，仅目标空字段补空（FR-04/AC-04）", () => {
    const config = makeConfig({
      operation: "map",
      params: { map_expression: "amount > 100" },
    });
    const switched = buildOperationChange(config, "filter");
    // map 用户值保留
    expect(switched.params.map_expression).toBe("amount > 100");
    // filter 目标字段为空 → 补预制值
    expect(switched.params.filter_condition).toBe("True");

    // 目标字段非空时切换不覆盖
    const custom = makeConfig({
      operation: "filter",
      params: { map_expression: "amount > 100", filter_condition: "row_value >= 1" },
    });
    const kept = buildOperationChange(custom, "map");
    expect(kept.params.filter_condition).toBe("row_value >= 1");
    expect(kept.params.map_expression).toBe("amount > 100");
  });

  it("T-A7 不臆造：切 aggregate/join 时 params 逐字段等价、无新增字段（INV-D07）", () => {
    const params = { map_expression: "amount > 100", join_key: "id" };
    const config = makeConfig({ operation: "map", params });
    const toAggregate = buildOperationChange(config, "aggregate");
    expect(toAggregate.operation).toBe("aggregate");
    expect(toAggregate.params).toEqual(params);
    expect(Object.keys(toAggregate.params).sort()).toEqual(["join_key", "map_expression"]);

    const toJoin = buildOperationChange(config, "join");
    expect(toJoin.operation).toBe("join");
    expect(toJoin.params).toEqual(params);
    expect(Object.keys(toJoin.params).sort()).toEqual(["join_key", "map_expression"]);
  });

  it("T-A8 幂等：连续两次调用结果深等价（INV-D02）", () => {
    const config = makeConfig({ operation: "map", params: {} });
    const first = buildOperationChange(config, "filter");
    const second = buildOperationChange(config, "filter");
    expect(first).toEqual(second);
    expect(first.params).toEqual(second.params);

    const again = buildOperationChange(first, "filter");
    expect(again).toEqual(first);
  });

  it("T-A9 不可变：返回新对象，入参 config 未被修改（INV-D02）", () => {
    const config = makeConfig({ operation: "map", params: {} });
    const snapshot = JSON.stringify(config);
    const result = buildOperationChange(config, "filter");
    expect(result).not.toBe(config);
    expect(result.params).not.toBe(config.params);
    expect(JSON.stringify(config)).toBe(snapshot);
    expect(config.params.filter_condition).toBeUndefined();
    expect(config.operation).toBe("map");
  });

  it("isPresetExpression 可用性判定：等于预制值 true，否则 false（DC-05/FR-07）", () => {
    expect(isPresetExpression("map", "True")).toBe(true);
    expect(isPresetExpression("map", "x == 1")).toBe(false);
    expect(isPresetExpression("map", undefined)).toBe(false);
    expect(isPresetExpression("map", "")).toBe(false);
    expect(isPresetExpression("filter", "True")).toBe(true);
  });
});
