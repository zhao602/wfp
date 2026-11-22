// =============================================================================
// 兼容映射 migrateCanvasState 单元测试
// （canvas/__tests__/canvasStateMigrator.test.ts）
//
// 留待非沙箱环境执行：cd web && npx vitest run canvasStateMigrator
//
// 覆盖用例（T6.2 / T9.1 expand_data_transform_ops）：
//   - 旧 control_flow_kind "human_review" → "human_review_gate"
//   - 旧 dynamic_injector 字段名迁移
//   - 旧 data_transform operations 数组「整体提升为链」（保留全部元素、顶层镜像首步、
//     每步 params 白名单迁移、缺 params 兜底、legacy input_dataset_reference 保留）
//   - 已是新格式（operation 单数）早返回不重复迁移（含 operation+operations 并存）
//   - 迁移后节点数量不变、节点 ID 不变
//   - 无需迁移的节点原样返回；纯函数不修改输入
// =============================================================================

import { describe, it, expect } from "vitest";
import { migrateCanvasState } from "../canvasStateMigrator";
import type { WorkflowCanvasState, CanvasNode, CanvasNodeData } from "../canvasTypes";
import type {
  NodeConfiguration,
  ControlFlowNodeConfiguration,
  DynamicInjectorNodeConfiguration,
  DataTransformNodeConfiguration,
  TriggerNodeConfiguration,
  AgentNodeConfiguration,
} from "../../workflow-definition/definitionTypes";

// ---------------------------------------------------------------------------
// 测试数据工厂
// ---------------------------------------------------------------------------

function makeNode(nodeId: string, name: string, config: NodeConfiguration): CanvasNode {
  const data: CanvasNodeData = {
    node_id: nodeId,
    name,
    node_type: config.node_type,
    configuration: config,
  };
  return { id: nodeId, type: config.node_type, position: { x: 0, y: 0 }, data };
}

function makeState(nodes: CanvasNode[]): WorkflowCanvasState {
  return { nodes, edges: [] };
}

// ---------------------------------------------------------------------------
// T6.2 control_flow 枚举值迁移
// ---------------------------------------------------------------------------

describe("migrateCanvasState control_flow 枚举值迁移（T6.2）", () => {
  it('旧 "human_review" 迁移为 "human_review_gate"', () => {
    const config = {
      node_type: "control_flow" as const,
      control_flow_kind: "human_review" as string,
      conditions: [],
      parallel_failure_strategy: "fast_fail" as const,
      review_timeout_action: "reject" as const,
    };
    const state = makeState([makeNode("n1", "审核门", config as unknown as NodeConfiguration)]);
    const migrated = migrateCanvasState(state);
    const result = migrated.nodes[0].data.configuration as ControlFlowNodeConfiguration;
    expect(result.control_flow_kind).toBe("human_review_gate");
  });

  it('"conditional_branch" 不变', () => {
    const config: ControlFlowNodeConfiguration = {
      node_type: "control_flow",
      control_flow_kind: "conditional_branch",
      conditions: [],
      parallel_failure_strategy: "fast_fail",
      review_timeout_action: "reject",
    };
    const state = makeState([makeNode("n1", "条件分支", config)]);
    const migrated = migrateCanvasState(state);
    const result = migrated.nodes[0].data.configuration as ControlFlowNodeConfiguration;
    expect(result.control_flow_kind).toBe("conditional_branch");
  });

  it('"human_review_gate" 不变（已是新格式）', () => {
    const config: ControlFlowNodeConfiguration = {
      node_type: "control_flow",
      control_flow_kind: "human_review_gate",
      conditions: [],
      parallel_failure_strategy: "fast_fail",
      review_timeout_action: "reject",
    };
    const state = makeState([makeNode("n1", "审核门", config)]);
    const migrated = migrateCanvasState(state);
    const result = migrated.nodes[0].data.configuration as ControlFlowNodeConfiguration;
    expect(result.control_flow_kind).toBe("human_review_gate");
  });
});

// ---------------------------------------------------------------------------
// T6.2 dynamic_injector 字段名迁移
// ---------------------------------------------------------------------------

describe("migrateCanvasState dynamic_injector 字段名迁移（T6.2）", () => {
  it("旧字段 source_expression 迁移到 injection_condition", () => {
    const legacyConfig = {
      node_type: "dynamic_injector" as const,
      source_expression: "items.length > 0",
      template_node_id: "tpl_1",
      max_injection_count: 10,
      injection_key_field: "id",
    };
    const state = makeState([makeNode("n1", "动态注入", legacyConfig as unknown as NodeConfiguration)]);
    const migrated = migrateCanvasState(state);
    const result = migrated.nodes[0].data.configuration as DynamicInjectorNodeConfiguration;
    expect(result.injection_condition).toBe("items.length > 0");
    expect(result.max_injection_count).toBe(10);
    expect("source_expression" in result).toBe(false);
    expect("injection_key_field" in result).toBe(false);
  });

  it("旧 template_node_id 非空时构造单元素 node_templates", () => {
    const legacyConfig = {
      node_type: "dynamic_injector" as const,
      source_expression: "",
      template_node_id: "tpl_1",
      max_injection_count: 5,
      injection_key_field: "",
    };
    const state = makeState([makeNode("n1", "动态注入", legacyConfig as unknown as NodeConfiguration)]);
    const migrated = migrateCanvasState(state);
    const result = migrated.nodes[0].data.configuration as DynamicInjectorNodeConfiguration;
    expect(result.node_templates).toHaveLength(1);
    expect(result.node_templates[0].template_id).toBe("tpl_1");
  });

  it("新格式（已有 injection_condition）不变", () => {
    const config: DynamicInjectorNodeConfiguration = {
      node_type: "dynamic_injector",
      injection_condition: "x > 0",
      node_templates: [],
      max_injection_count: 100,
    };
    const state = makeState([makeNode("n1", "动态注入", config)]);
    const migrated = migrateCanvasState(state);
    const result = migrated.nodes[0].data.configuration as DynamicInjectorNodeConfiguration;
    expect(result.injection_condition).toBe("x > 0");
    expect(result.max_injection_count).toBe(100);
  });
});

// ---------------------------------------------------------------------------
// T6.2 data_transform 结构迁移
// ---------------------------------------------------------------------------

describe("migrateCanvasState data_transform 结构迁移（T6.2 / T9.1）", () => {
  it("旧 operations 数组整体提升为链：保留全部元素，顶层镜像首步", () => {
    const legacyConfig = {
      node_type: "data_transform" as const,
      operations: [
        { operation: "filter", params: { filter_condition: "x > 10" } },
        { operation: "map", params: { map_expression: "x * 2" } },
      ],
    };
    const state = makeState([makeNode("n1", "数据转换", legacyConfig as unknown as NodeConfiguration)]);
    const migrated = migrateCanvasState(state);
    const result = migrated.nodes[0].data.configuration as DataTransformNodeConfiguration;
    expect(result.operation).toBe("filter");
    expect(result.params).toEqual({ filter_condition: "x > 10" });
    expect(result.operations).toEqual([
      { operation: "filter", params: { filter_condition: "x > 10" } },
      { operation: "map", params: { map_expression: "x * 2" } },
    ]);
  });

  it("旧 operations 每步 params 经白名单迁移并过滤未知字段", () => {
    const legacyConfig = {
      node_type: "data_transform" as const,
      operations: [
        {
          operation: "map",
          params: { map_expression: "a", unknown_junk: "junk" },
        },
      ],
    };
    const state = makeState([makeNode("n1", "数据转换", legacyConfig as unknown as NodeConfiguration)]);
    const migrated = migrateCanvasState(state);
    const result = migrated.nodes[0].data.configuration as DataTransformNodeConfiguration;
    expect(result.operations).toEqual([
      { operation: "map", params: { map_expression: "a" } },
    ]);
  });

  it("旧 operations 步骤缺 params 时兜底空对象", () => {
    const legacyConfig = {
      node_type: "data_transform" as const,
      operations: [{ operation: "sort" }],
    };
    const state = makeState([makeNode("n1", "数据转换", legacyConfig as unknown as NodeConfiguration)]);
    const migrated = migrateCanvasState(state);
    const result = migrated.nodes[0].data.configuration as DataTransformNodeConfiguration;
    expect(result.operations).toEqual([{ operation: "sort", params: {} }]);
    expect(result.params).toEqual({});
  });

  it("旧 operations 迁移保留 legacy input_dataset_reference", () => {
    const legacyConfig = {
      node_type: "data_transform" as const,
      input_dataset_reference: "trigger.output",
      operations: [{ operation: "map", params: { map_expression: "x * 2" } }],
    };
    const state = makeState([makeNode("n1", "数据转换", legacyConfig as unknown as NodeConfiguration)]);
    const migrated = migrateCanvasState(state);
    const result = migrated.nodes[0].data.configuration as DataTransformNodeConfiguration;
    expect(result.input_dataset_reference).toBe("trigger.output");
  });

  it("旧 operations 空数组时默认 operation=map", () => {
    const legacyConfig = {
      node_type: "data_transform" as const,
      operations: [],
    };
    const state = makeState([makeNode("n1", "数据转换", legacyConfig as unknown as NodeConfiguration)]);
    const migrated = migrateCanvasState(state);
    const result = migrated.nodes[0].data.configuration as DataTransformNodeConfiguration;
    expect(result.operation).toBe("map");
    expect(result.input_dataset_reference).toBe("");
    expect(result.params).toEqual({});
  });

  it("新格式（已有 operation 单数）不变", () => {
    const config: DataTransformNodeConfiguration = {
      node_type: "data_transform",
      operation: "aggregate",
      input_dataset_reference: "ds1",
      params: { group_by: ["category"], aggregate_function: "sum", aggregate_field: "amount" },
    };
    const state = makeState([makeNode("n1", "数据转换", config)]);
    const migrated = migrateCanvasState(state);
    const result = migrated.nodes[0].data.configuration as DataTransformNodeConfiguration;
    expect(result.operation).toBe("aggregate");
    expect(result.input_dataset_reference).toBe("ds1");
    expect(result.params.aggregate_function).toBe("sum");
  });

  it("新格式（operation 与 operations 并存）早返回不重复迁移", () => {
    const config = {
      node_type: "data_transform" as const,
      operation: "map" as const,
      input_dataset_reference: "up.x",
      params: { map_expression: "keep" },
      operations: [{ operation: "map", params: { map_expression: "keep" } }],
    } as unknown as DataTransformNodeConfiguration;
    const state = makeState([makeNode("n1", "数据转换", config)]);
    const migrated = migrateCanvasState(state);
    const result = migrated.nodes[0].data.configuration as DataTransformNodeConfiguration;
    expect(result.operation).toBe("map");
    expect(result.params).toEqual({ map_expression: "keep" });
    expect(result.operations).toEqual([
      { operation: "map", params: { map_expression: "keep" } },
    ]);
  });

  it("纯函数：data_transform 迁移不修改输入", () => {
    const legacyConfig = {
      node_type: "data_transform" as const,
      operations: [
        { operation: "filter", params: { filter_condition: "x > 10" } },
        { operation: "map", params: { map_expression: "x * 2" } },
      ],
    };
    const state = makeState([makeNode("n1", "数据转换", legacyConfig as unknown as NodeConfiguration)]);
    const stateCopy = JSON.parse(JSON.stringify(state));
    migrateCanvasState(state);
    expect(state).toEqual(stateCopy);
  });
});

// ---------------------------------------------------------------------------
// T6.2 节点数量与 ID 不变
// ---------------------------------------------------------------------------

describe("migrateCanvasState 节点不变性（T6.2）", () => {
  it("迁移后节点数量不变", () => {
    const nodes: CanvasNode[] = [
      makeNode("n1", "触发器", {
        node_type: "trigger", trigger_type: "manual", input_template: {},
      } as TriggerNodeConfiguration),
      makeNode("n2", "审核门", {
        node_type: "control_flow",
        control_flow_kind: "human_review" as unknown as ControlFlowNodeConfiguration["control_flow_kind"],
        conditions: [],
        parallel_failure_strategy: "fast_fail",
        review_timeout_action: "reject",
      } as unknown as NodeConfiguration),
      makeNode("n3", "动态注入", {
        node_type: "dynamic_injector",
        injection_condition: "",
        node_templates: [],
        max_injection_count: 100,
      } as DynamicInjectorNodeConfiguration),
    ];
    const state = makeState(nodes);
    const migrated = migrateCanvasState(state);
    expect(migrated.nodes).toHaveLength(3);
  });

  it("迁移后节点 ID 不变", () => {
    const nodes: CanvasNode[] = [
      makeNode("node-a", "A", {
        node_type: "trigger", trigger_type: "manual", input_template: {},
      } as TriggerNodeConfiguration),
      makeNode("node-b", "B", {
        node_type: "agent", model_identifier: "m", system_prompt: "", available_tools: [],
      } as NodeConfiguration),
    ];
    const state = makeState(nodes);
    const migrated = migrateCanvasState(state);
    expect(migrated.nodes[0].data.node_id).toBe("node-a");
    expect(migrated.nodes[1].data.node_id).toBe("node-b");
  });

  it("无需迁移的节点原样返回", () => {
    const config: TriggerNodeConfiguration = {
      node_type: "trigger",
      trigger_type: "scheduled",
      input_template: { key: "val" },
      schedule: "0 0 * * *",
    };
    const state = makeState([makeNode("n1", "触发器", config)]);
    const migrated = migrateCanvasState(state);
    const result = migrated.nodes[0].data.configuration as TriggerNodeConfiguration;
    expect(result.trigger_type).toBe("scheduled");
    expect(result.schedule).toBe("0 0 * * *");
    expect(result.input_template).toEqual({ key: "val" });
  });

  it("纯函数：不修改输入", () => {
    const config: ControlFlowNodeConfiguration = {
      node_type: "control_flow",
      control_flow_kind: "human_review_gate",
      conditions: [],
      parallel_failure_strategy: "fast_fail",
      review_timeout_action: "reject",
    };
    const state = makeState([makeNode("n1", "审核门", config)]);
    const stateCopy = JSON.parse(JSON.stringify(state));
    migrateCanvasState(state);
    expect(state).toEqual(stateCopy);
  });
});

// ---------------------------------------------------------------------------
// fix_agent_null_length E2-e：持久化恢复链 agent 归一守卫（P1 通道实证 → P2 定稿）
// ---------------------------------------------------------------------------

describe("migrateCanvasState agent 节点归一守卫（fix_agent_null_length E2-e / G-02 / AC-05）", () => {
  it("available_tools: null 的旧持久化 agent 状态经恢复链归一为 []（P1 实证 null 透传通道已断根）", () => {
    const legacyConfig = {
      node_type: "agent" as const,
      model_identifier: "m-1",
      system_prompt: "p",
      available_tools: null,
    };
    const state = makeState([makeNode("agent_1", "智能体", legacyConfig as unknown as NodeConfiguration)]);
    const migrated = migrateCanvasState(state);
    const result = migrated.nodes[0].data.configuration as AgentNodeConfiguration;
    // L2（G-02）定稿口径：migrateNodeConfiguration 新增 agent case，null 清洗为安全形态
    expect(result.available_tools).toEqual([]);
    expect(Array.isArray(result.available_tools)).toBe(true);
  });

  it("available_tools 键缺失（undefined）的旧持久化 agent 状态经恢复链归一为 []（双态口径）", () => {
    const legacyConfig = {
      node_type: "agent" as const,
      model_identifier: "m-1",
      system_prompt: "p",
    };
    const state = makeState([makeNode("agent_1", "智能体", legacyConfig as unknown as NodeConfiguration)]);
    const migrated = migrateCanvasState(state);
    const result = migrated.nodes[0].data.configuration as AgentNodeConfiguration;
    expect(result.available_tools).toEqual([]);
  });

  it("对照：tool 节点旧状态照常透传不被误清洗（agent 归一不外溢，tasks 3.8 对照断言）", () => {
    const legacyToolConfig = {
      node_type: "tool" as const,
      tool_identifier: "legacy_tool",
      tool_source: "local" as const,
      argument_mapping: { q: "trigger.payload" },
      static_arguments: { k: "v" },
    };
    const state = makeState([makeNode("tool_1", "旧工具", legacyToolConfig as unknown as NodeConfiguration)]);
    const migrated = migrateCanvasState(state);
    expect(migrated.nodes[0].data.configuration).toEqual(legacyToolConfig);
  });
});

// ---------------------------------------------------------------------------
// fix_agent_null_length L-12：data_transform 旧链字段非法形态穿透守卫
// （原 :105 === undefined 判空对 null/非数组穿透 → :111 null.length 崩溃）
// ---------------------------------------------------------------------------

describe("migrateCanvasState data_transform operations 非法形态守卫（fix_agent_null_length L-12 / INV-01）", () => {
  it("L-12-a operations=null（无 operation 键）：不崩溃，配置原样透传（渲染层真值守卫兜底）", () => {
    const legacyConfig = {
      node_type: "data_transform" as const,
      input_dataset_reference: "ds-1",
      operations: null,
    };
    const state = makeState([
      makeNode("dt_1", "数据转换", legacyConfig as unknown as NodeConfiguration),
    ]);
    const migrated = migrateCanvasState(state);
    expect(migrated.nodes[0].data.configuration).toEqual(legacyConfig);
  });

  it("L-12-b operations=非数组 truthy（字符串）：不崩溃，配置原样透传", () => {
    const legacyConfig = {
      node_type: "data_transform" as const,
      input_dataset_reference: "ds-1",
      operations: "not-an-array",
    };
    const state = makeState([
      makeNode("dt_1", "数据转换", legacyConfig as unknown as NodeConfiguration),
    ]);
    const migrated = migrateCanvasState(state);
    expect(migrated.nodes[0].data.configuration).toEqual(legacyConfig);
  });

  it("L-12-c 合法对照：新格式（有 operation 键）即使 operations=null 也原样返回（格式判定分支零回归）", () => {
    const newFormatConfig = {
      node_type: "data_transform" as const,
      operation: "map" as const,
      input_dataset_reference: "ds-1",
      params: {},
      operations: null,
    };
    const state = makeState([
      makeNode("dt_2", "数据转换", newFormatConfig as unknown as NodeConfiguration),
    ]);
    const migrated = migrateCanvasState(state);
    expect(migrated.nodes[0].data.configuration).toEqual(newFormatConfig);
  });
});