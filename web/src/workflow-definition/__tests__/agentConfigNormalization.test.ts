// =============================================================================
// 归一函数全形态矩阵单测（src/workflow-definition/__tests__/agentConfigNormalization.test.ts）
// fix_agent_null_length CHG-18（DC-04；FR-05.1）：
// normalizeStringArrayField / normalizeAgentNodeConfiguration 全形态矩阵断言。
// =============================================================================

import { describe, it, expect } from "vitest";
import {
  normalizeStringArrayField,
  normalizeAgentNodeConfiguration,
} from "../agentConfigNormalization";
import type { AgentNodeConfiguration } from "../definitionTypes";

describe("normalizeStringArrayField 全形态矩阵（fix_agent_null_length CHG-18）", () => {
  it.each([
    ["null → []", null],
    ["undefined → []", undefined],
    ["空数组 → []", []],
    ["字符串（非数组 truthy）→ []", "report_builder"],
    ["对象（非数组 truthy）→ []", { 0: "report_builder", length: 1 }],
    ["数字 → []", 42],
  ])("%s", (_label, input) => {
    expect(normalizeStringArrayField(input)).toEqual([]);
  });

  it("合法数组保序返回（非空）", () => {
    expect(normalizeStringArrayField(["b", "a", "c"])).toEqual(["b", "a", "c"]);
  });

  it("含空串条目的合法数组保序返回（空串为合法 string 项）", () => {
    expect(normalizeStringArrayField(["", "a"])).toEqual(["", "a"]);
  });

  it("含非字符串项的数组整体视为非法 → []（DC-04 逐项判定）", () => {
    expect(normalizeStringArrayField(["a", 1])).toEqual([]);
    expect(normalizeStringArrayField([null])).toEqual([]);
    expect(normalizeStringArrayField([["nested"]])).toEqual([]);
  });

  it("合法数组返回原引用（零拷贝，INV-02 最小行为面）", () => {
    const legal = ["a", "b"];
    expect(normalizeStringArrayField(legal)).toBe(legal);
  });

  it("不抛异常契约：任意输入均返回 string[]（DC-04 不抛异常）", () => {
    expect(() => normalizeStringArrayField(Symbol("x"))).not.toThrow();
    expect(() => normalizeStringArrayField(() => undefined)).not.toThrow();
  });
});

describe("normalizeAgentNodeConfiguration 整体归一逐字段断言（fix_agent_null_length CHG-18）", () => {
  it("整体非法（null / 标量）→ 最小安全配置（不抛异常契约）", () => {
    const minimal: AgentNodeConfiguration = {
      node_type: "agent",
      model_identifier: "",
      system_prompt: "",
      available_tools: [],
    };
    expect(normalizeAgentNodeConfiguration(null)).toEqual(minimal);
    expect(normalizeAgentNodeConfiguration(undefined)).toEqual(minimal);
    expect(normalizeAgentNodeConfiguration("garbage")).toEqual(minimal);
    expect(normalizeAgentNodeConfiguration(42)).toEqual(minimal);
  });

  it("available_tools 三态：null → []；缺键 → []；合法数组原样（§3.1 主目标字段）", () => {
    const withNull = normalizeAgentNodeConfiguration({
      node_type: "agent",
      model_identifier: "m-1",
      system_prompt: "p",
      available_tools: null,
    });
    expect(withNull.available_tools).toEqual([]);
    expect(Array.isArray(withNull.available_tools)).toBe(true);

    const missingKey = normalizeAgentNodeConfiguration({
      node_type: "agent",
      model_identifier: "m-1",
      system_prompt: "p",
    });
    expect(missingKey.available_tools).toEqual([]);

    const tools = ["report_builder", "weather_query"];
    const withLegal = normalizeAgentNodeConfiguration({
      node_type: "agent",
      model_identifier: "m-1",
      system_prompt: "p",
      available_tools: tools,
    });
    expect(withLegal.available_tools).toBe(tools);
  });

  it("model_identifier / system_prompt 缺键或非 string → 容错回落 \"\"（对齐后端必备语义）", () => {
    const missing = normalizeAgentNodeConfiguration({ node_type: "agent" });
    expect(missing.model_identifier).toBe("");
    expect(missing.system_prompt).toBe("");

    const illegal = normalizeAgentNodeConfiguration({
      node_type: "agent",
      model_identifier: null,
      system_prompt: 42,
    });
    expect(illegal.model_identifier).toBe("");
    expect(illegal.system_prompt).toBe("");

    const legal = normalizeAgentNodeConfiguration({
      node_type: "agent",
      model_identifier: "qwen-max",
      system_prompt: "你是助手",
    });
    expect(legal.model_identifier).toBe("qwen-max");
    expect(legal.system_prompt).toBe("你是助手");
  });

  it("inputs 三态语义保持：null → undefined；[] 原样；非空原样（对齐后端 Optional=None=隐式）", () => {
    const withNull = normalizeAgentNodeConfiguration({
      node_type: "agent",
      inputs: null,
    });
    expect(withNull.inputs).toBeUndefined();

    const empty: AgentNodeConfiguration = {
      node_type: "agent",
      model_identifier: "m",
      system_prompt: "p",
      available_tools: [],
      inputs: [],
    };
    expect(normalizeAgentNodeConfiguration(empty).inputs).toEqual([]);

    const entries = [{ reference: "trigger.payload", alias: "ctx" }];
    const withEntries = normalizeAgentNodeConfiguration({
      node_type: "agent",
      inputs: entries,
    });
    expect(withEntries.inputs).toBe(entries);

    // 非法形态（字符串）→ undefined（DC-05 规则 3：非数组 truthy 回落空态）
    const illegal = normalizeAgentNodeConfiguration({
      node_type: "agent",
      inputs: "garbage",
    });
    expect(illegal.inputs).toBeUndefined();
  });

  it("output_schema：null / 非对象 → undefined；合法 Record 原样", () => {
    const withNull = normalizeAgentNodeConfiguration({
      node_type: "agent",
      output_schema: null,
    });
    expect(withNull.output_schema).toBeUndefined();

    const illegal = normalizeAgentNodeConfiguration({
      node_type: "agent",
      output_schema: "garbage",
    });
    expect(illegal.output_schema).toBeUndefined();

    const schema = { summary: "string" };
    const legal = normalizeAgentNodeConfiguration({
      node_type: "agent",
      output_schema: schema,
    });
    expect(legal.output_schema).toBe(schema);
  });

  it("max_tool_rounds / output_max_retries：null → undefined；合法数值原样（§3.1 低优先一并归一）", () => {
    const withNull = normalizeAgentNodeConfiguration({
      node_type: "agent",
      max_tool_rounds: null,
      output_max_retries: null,
    });
    expect(withNull.max_tool_rounds).toBeUndefined();
    expect(withNull.output_max_retries).toBeUndefined();

    const legal = normalizeAgentNodeConfiguration({
      node_type: "agent",
      max_tool_rounds: 8,
      output_max_retries: 2,
    });
    expect(legal.max_tool_rounds).toBe(8);
    expect(legal.output_max_retries).toBe(2);
  });

  it("其余字段原样保留（未知扩展键 spread 透传）", () => {
    const normalized = normalizeAgentNodeConfiguration({
      node_type: "agent",
      custom_extension: { any: "value" },
    });
    expect(
      (normalized as unknown as Record<string, unknown>).custom_extension,
    ).toEqual({ any: "value" });
  });

  it("纯函数契约：不修改入参（DC-04 承 migrator 纯函数约定）", () => {
    const input = {
      node_type: "agent" as const,
      model_identifier: "m-1",
      system_prompt: "p",
      available_tools: null as unknown as string[],
    };
    normalizeAgentNodeConfiguration(input);
    expect(input.available_tools).toBeNull();
    expect(input.model_identifier).toBe("m-1");
    expect(Object.keys(input)).toEqual([
      "node_type",
      "model_identifier",
      "system_prompt",
      "available_tools",
    ]);
  });

  it("node_type 恒归一为 \"agent\"（判别字段兜底）", () => {
    const normalized = normalizeAgentNodeConfiguration({ node_type: "agent" });
    expect(normalized.node_type).toBe("agent");
  });
});
