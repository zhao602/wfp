// =============================================================================
// 节点输出契约层单元测试（canvas/__tests__/nodeOutputContracts.test.ts）
//
// 留待非沙箱环境执行：cd web && npx vitest run nodeOutputContracts
//
// 覆盖用例：
//   T2.4  12 类前端类型逐一命中 + 可引用性矩阵 + schemaEditable 仅 agent
//         + resolveOutputContract 未定义返回 undefined
//   T11.3 前后端类型对齐与降级（NODE_TYPE_BACKEND_MAP / BACKEND_ONLY_NODE_TYPES）
//   llm_code_nodes_v2：契约断言 llm.referable === true、code.referable === true
//   （DC-13-4/D-06，design 附录 A.4 表 B B1——Record 补键须被遍历断言真执行）
// =============================================================================

import { describe, it, expect } from "vitest";
import {
  NODE_OUTPUT_CONTRACTS,
  resolveOutputContract,
  NODE_TYPE_BACKEND_MAP,
  BACKEND_ONLY_NODE_TYPES,
} from "../nodeOutputContracts";
import type { NodeType } from "../../workflow-definition/definitionTypes";

const ALL_NODE_TYPES: NodeType[] = [
  "trigger",
  "agent",
  "tool",
  "data_source",
  "control_flow",
  "subworkflow",
  "dynamic_injector",
  "data_transform",
  "output_display",
  "vector_knowledge_base",
  "llm",
  "code",
];

// ---------------------------------------------------------------------------
// T2.4 契约表穷尽性与可引用性矩阵
// ---------------------------------------------------------------------------

describe("NODE_OUTPUT_CONTRACTS 契约表（T2.4）", () => {
  it("12 类前端类型逐一命中（无 undefined）", () => {
    for (const nt of ALL_NODE_TYPES) {
      const contract = NODE_OUTPUT_CONTRACTS[nt];
      expect(contract).toBeDefined();
      expect(typeof contract.category).toBe("string");
      expect(typeof contract.referable).toBe("boolean");
      expect(typeof contract.detail).toBe("string");
      expect(typeof contract.schemaEditable).toBe("boolean");
    }
  });

  it("可引用性矩阵：仅 control_flow 为 false，其余 11 类均为 true（llm/code 可被引用，DC-13-4）", () => {
    const expected: Record<NodeType, boolean> = {
      trigger: true,
      agent: true,
      tool: true,
      data_source: true,
      control_flow: false,
      subworkflow: true,
      dynamic_injector: true,
      data_transform: true,
      output_display: true,
      vector_knowledge_base: true,
      llm: true,
      code: true,
    };
    for (const nt of ALL_NODE_TYPES) {
      expect(NODE_OUTPUT_CONTRACTS[nt].referable).toBe(expected[nt]);
    }
  });

  it("schemaEditable 仅 agent 为 true", () => {
    for (const nt of ALL_NODE_TYPES) {
      const expected = nt === "agent";
      expect(NODE_OUTPUT_CONTRACTS[nt].schemaEditable).toBe(expected);
    }
  });

  it("category 文案非空且各类型有意义区分", () => {
    const categories = ALL_NODE_TYPES.map((nt) => NODE_OUTPUT_CONTRACTS[nt].category);
    const unique = new Set(categories);
    // 两对有意重复：data_source 与 data_transform 同为「数据集（记录集合）」（expand_data_transform_ops
    // 契约矩阵：data_transform 默认 dataset 输出）；agent 与 llm 同为「结构化对象」
    // （llm_code_nodes_v2 D-06：结构化输出与 agent 输出同构），其余互异
    expect(unique.size).toBe(categories.length - 2);
    expect(NODE_OUTPUT_CONTRACTS.data_source.category).toBe(
      NODE_OUTPUT_CONTRACTS.data_transform.category,
    );
    expect(NODE_OUTPUT_CONTRACTS.llm.category).toBe(
      NODE_OUTPUT_CONTRACTS.agent.category,
    );
    for (const cat of categories) {
      expect(cat.length).toBeGreaterThan(0);
    }
  });

  it("detail 文案非空", () => {
    for (const nt of ALL_NODE_TYPES) {
      expect(NODE_OUTPUT_CONTRACTS[nt].detail.length).toBeGreaterThan(0);
    }
  });
});

// ---------------------------------------------------------------------------
// T2.4 resolveOutputContract
// ---------------------------------------------------------------------------

describe("resolveOutputContract（T2.4）", () => {
  it("12 类前端类型均命中返回契约对象", () => {
    for (const nt of ALL_NODE_TYPES) {
      const contract = resolveOutputContract(nt);
      expect(contract).toBeDefined();
      expect(contract?.category).toBe(NODE_OUTPUT_CONTRACTS[nt].category);
    }
  });

  it("未知类型返回 undefined（安全降级）", () => {
    expect(resolveOutputContract("unknown_type")).toBeUndefined();
    expect(resolveOutputContract("")).toBeUndefined();
    expect(resolveOutputContract("PLUGIN")).toBeUndefined();
  });

  it("后端专有类型 plugin / supervisor 返回 undefined", () => {
    expect(resolveOutputContract("plugin")).toBeUndefined();
    expect(resolveOutputContract("supervisor")).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// T11.3 前后端类型对齐与降级
// ---------------------------------------------------------------------------

describe("前后端类型对齐（T11.3）", () => {
  it("NODE_TYPE_BACKEND_MAP 覆盖 12 类前端类型", () => {
    for (const nt of ALL_NODE_TYPES) {
      const backendName = NODE_TYPE_BACKEND_MAP[nt];
      expect(backendName).toBeDefined();
      expect(typeof backendName).toBe("string");
      expect(backendName.length).toBeGreaterThan(0);
    }
  });

  it("NODE_TYPE_BACKEND_MAP 映射值与后端枚举名一致", () => {
    expect(NODE_TYPE_BACKEND_MAP.trigger).toBe("TRIGGER");
    expect(NODE_TYPE_BACKEND_MAP.agent).toBe("AGENT");
    expect(NODE_TYPE_BACKEND_MAP.tool).toBe("TOOL");
    expect(NODE_TYPE_BACKEND_MAP.data_source).toBe("DATA_SOURCE");
    expect(NODE_TYPE_BACKEND_MAP.control_flow).toBe("CONTROL_FLOW");
    expect(NODE_TYPE_BACKEND_MAP.subworkflow).toBe("SUB_WORKFLOW");
    expect(NODE_TYPE_BACKEND_MAP.dynamic_injector).toBe("DYNAMIC_INJECTOR");
    expect(NODE_TYPE_BACKEND_MAP.data_transform).toBe("DATA_TRANSFORM");
    expect(NODE_TYPE_BACKEND_MAP.output_display).toBe("OUTPUT_DISPLAY");
    expect(NODE_TYPE_BACKEND_MAP.vector_knowledge_base).toBe("VECTOR_KNOWLEDGE_BASE");
    expect(NODE_TYPE_BACKEND_MAP.llm).toBe("LLM");
    expect(NODE_TYPE_BACKEND_MAP.code).toBe("CODE");
  });

  it("BACKEND_ONLY_NODE_TYPES 含 plugin 和 supervisor", () => {
    expect(BACKEND_ONLY_NODE_TYPES).toContain("plugin");
    expect(BACKEND_ONLY_NODE_TYPES).toContain("supervisor");
    expect(BACKEND_ONLY_NODE_TYPES).toHaveLength(2);
  });

  it("BACKEND_ONLY_NODE_TYPES 中的类型在契约表中均未定义（降级）", () => {
    for (const bt of BACKEND_ONLY_NODE_TYPES) {
      expect(resolveOutputContract(bt)).toBeUndefined();
    }
  });

  it("前端 12 类与后端专有类型无交集", () => {
    const frontendNames = ALL_NODE_TYPES;
    for (const bt of BACKEND_ONLY_NODE_TYPES) {
      expect(frontendNames).not.toContain(bt);
    }
  });
});