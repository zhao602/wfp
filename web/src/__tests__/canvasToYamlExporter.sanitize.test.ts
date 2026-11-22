// =============================================================================
// sanitizeConfig 八类节点导出 + 回归测试
// （__tests__/canvasToYamlExporter.sanitize.test.ts）
//
// 留待非沙箱环境执行：cd web && npx vitest run canvasToYamlExporter.sanitize
//
// 覆盖用例：
//   T6.1 八类节点 sanitizeConfig 导出穷尽性
//   T6.3 回归测试：既有五类节点导出行为不变
//   CHG-18（optimize_output_display_node）：output_display 集合序列化三态
//   （空省略/单元素标量/多元素数组）+ output_mode 省略/导出 + 旧 YAML 往返
//   （UT-14 单值 / UT-15 缺省 / UT-17 多元素，INV-05/INV-D05/INV-D15/R-03）
//   expand_data_transform_ops（CHG-F07 / DC-01，T9.2/T9.4）：链长 1 等价不写
//   operations、链长 > 1 写出、非默认输出格式才写、参数白名单、导出→导入→
//   再导出幂等
// =============================================================================

import { describe, it, expect } from "vitest";
import { canvasToDefinition, canvasToYaml } from "../workflow-definition/canvasToYamlExporter";
import { yamlToCanvas } from "../workflow-definition/yamlToCanvasImporter";
import type { WorkflowCanvasState, CanvasNode, CanvasNodeData } from "../canvas/canvasTypes";
import type {
  NodeConfiguration,
  TriggerNodeConfiguration,
  AgentNodeConfiguration,
  ToolNodeConfiguration,
  DataSourceNodeConfiguration,
  ControlFlowNodeConfiguration,
  SubWorkflowNodeConfiguration,
  DynamicInjectorNodeConfiguration,
  DataTransformNodeConfiguration,
  DataTransformOperationParams,
  OutputDisplayNodeConfiguration,
} from "../workflow-definition/definitionTypes";

// ---------------------------------------------------------------------------
// 测试数据工厂
// ---------------------------------------------------------------------------

function makeNode(nodeId: string, name: string, config: NodeConfiguration, outputName?: string): CanvasNode {
  const data: CanvasNodeData = {
    node_id: nodeId,
    name,
    node_type: config.node_type,
    configuration: config,
  };
  if (outputName !== undefined) data.output_name = outputName;
  return { id: nodeId, type: config.node_type, position: { x: 0, y: 0 }, data };
}

function makeState(nodes: CanvasNode[]): WorkflowCanvasState {
  return { nodes, edges: [] };
}

function getConfig(state: WorkflowCanvasState, index: number): NodeConfiguration {
  return canvasToDefinition(state).nodes[index].configuration;
}

// ---------------------------------------------------------------------------
// T6.1 八类节点 sanitizeConfig 导出穷尽性
// ---------------------------------------------------------------------------

describe("sanitizeConfig 八类节点导出（T6.1）", () => {
  it("trigger 导出非 undefined 且字段对齐", () => {
    const config: TriggerNodeConfiguration = {
      node_type: "trigger",
      trigger_type: "manual",
      input_template: { key: "value" },
    };
    const result = getConfig(makeState([makeNode("n1", "触发器", config)]), 0) as TriggerNodeConfiguration;
    expect(result).toBeDefined();
    expect(result.node_type).toBe("trigger");
    expect(result.trigger_type).toBe("manual");
    expect(result.input_template).toEqual({ key: "value" });
  });

  it("agent 导出非 undefined 且字段对齐", () => {
    const config: AgentNodeConfiguration = {
      node_type: "agent",
      model_identifier: "gpt-4",
      system_prompt: "test",
      available_tools: ["tool1"],
    };
    const result = getConfig(makeState([makeNode("n1", "Agent", config)]), 0) as AgentNodeConfiguration;
    expect(result).toBeDefined();
    expect(result.model_identifier).toBe("gpt-4");
    expect(result.available_tools).toEqual(["tool1"]);
  });

  it("tool 导出非 undefined 且字段对齐", () => {
    const config: ToolNodeConfiguration = {
      node_type: "tool",
      tool_identifier: "search",
      tool_source: "local",
      argument_mapping: { q: "upstream.output" },
      static_arguments: { limit: 10 },
    };
    const result = getConfig(makeState([makeNode("n1", "Tool", config)]), 0) as ToolNodeConfiguration;
    expect(result).toBeDefined();
    expect(result.tool_identifier).toBe("search");
    expect(result.tool_source).toBe("local");
  });

  it("data_source 导出非 undefined 且字段对齐", () => {
    const config: DataSourceNodeConfiguration = {
      node_type: "data_source",
      connection_reference: "conn1",
      query: "SELECT * FROM t",
    };
    const result = getConfig(makeState([makeNode("n1", "数据源", config)]), 0) as DataSourceNodeConfiguration;
    expect(result).toBeDefined();
    expect(result.connection_reference).toBe("conn1");
  });

  it("control_flow 导出非 undefined 且 control_flow_kind 不含 human_review", () => {
    const config: ControlFlowNodeConfiguration = {
      node_type: "control_flow",
      control_flow_kind: "human_review_gate",
      conditions: [],
      parallel_failure_strategy: "fast_fail",
      review_timeout_action: "reject",
    };
    const result = getConfig(makeState([makeNode("n1", "审核门", config)]), 0) as ControlFlowNodeConfiguration;
    expect(result).toBeDefined();
    expect(result.control_flow_kind).toBe("human_review_gate");
    expect(result.control_flow_kind).not.toBe("human_review");
  });

  it("subworkflow 导出非 undefined 且字段对齐", () => {
    const config: SubWorkflowNodeConfiguration = {
      node_type: "subworkflow",
      subworkflow_definition_id: "sub_wf_1",
      input_mapping: { a: "upstream.output" },
      output_mapping: { b: "result" },
      context_strategy: "inherit",
      max_nesting_depth: 3,
    };
    const result = getConfig(makeState([makeNode("n1", "子工作流", config)]), 0) as SubWorkflowNodeConfiguration;
    expect(result).toBeDefined();
    expect(result.subworkflow_definition_id).toBe("sub_wf_1");
    expect(result.input_mapping).toEqual({ a: "upstream.output" });
    expect(result.context_strategy).toBe("inherit");
    expect(result.max_nesting_depth).toBe(3);
  });

  it("dynamic_injector 导出非 undefined 且不含旧字段名", () => {
    const config: DynamicInjectorNodeConfiguration = {
      node_type: "dynamic_injector",
      injection_condition: "items.length > 0",
      node_templates: [
        { template_id: "tpl1", node_type: "agent", name: "处理", configuration: {} },
      ],
      max_injection_count: 50,
    };
    const result = getConfig(makeState([makeNode("n1", "动态注入", config)]), 0) as DynamicInjectorNodeConfiguration;
    expect(result).toBeDefined();
    expect(result.injection_condition).toBe("items.length > 0");
    expect(result.node_templates).toHaveLength(1);
    expect(result.max_injection_count).toBe(50);
    expect((result as unknown as Record<string, unknown>).source_expression).toBeUndefined();
    expect((result as unknown as Record<string, unknown>).template_node_id).toBeUndefined();
    expect((result as unknown as Record<string, unknown>).injection_key_field).toBeUndefined();
  });

  it("data_transform 导出非 undefined 且不含 operations 数组", () => {
    const config: DataTransformNodeConfiguration = {
      node_type: "data_transform",
      operation: "map",
      input_dataset_reference: "upstream.output",
      params: { map_expression: "x * 2" },
    };
    const result = getConfig(makeState([makeNode("n1", "数据转换", config)]), 0) as DataTransformNodeConfiguration;
    expect(result).toBeDefined();
    expect(result.operation).toBe("map");
    expect(result.input_dataset_reference).toBe("upstream.output");
    expect(result.params.map_expression).toBe("x * 2");
    expect(result.operations).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// T6.3 回归测试：既有五类节点导出行为不变
// ---------------------------------------------------------------------------

describe("回归测试：既有五类节点导出不变（T6.3）", () => {
  it("trigger / agent / tool / data_source / control_flow(非 human_review) 导出字段一致", () => {
    const nodes: CanvasNode[] = [
      makeNode("n1", "触发器", {
        node_type: "trigger", trigger_type: "manual", input_template: {},
      }),
      makeNode("n2", "Agent", {
        node_type: "agent", model_identifier: "m", system_prompt: "", available_tools: [],
      }),
      makeNode("n3", "Tool", {
        node_type: "tool", tool_identifier: "t", tool_source: "local",
        argument_mapping: {}, static_arguments: {},
      }),
      makeNode("n4", "数据源", {
        node_type: "data_source", connection_reference: "c", query: "",
      }),
      makeNode("n5", "条件分支", {
        node_type: "control_flow", control_flow_kind: "conditional_branch",
        conditions: [], parallel_failure_strategy: "fast_fail", review_timeout_action: "reject",
      }),
    ];
    const state = makeState(nodes);
    const definition = canvasToDefinition(state);

    const trigger = definition.nodes[0].configuration as TriggerNodeConfiguration;
    expect(trigger.node_type).toBe("trigger");
    expect(trigger.trigger_type).toBe("manual");

    const agent = definition.nodes[1].configuration as AgentNodeConfiguration;
    expect(agent.node_type).toBe("agent");
    expect(agent.model_identifier).toBe("m");

    const tool = definition.nodes[2].configuration as ToolNodeConfiguration;
    expect(tool.node_type).toBe("tool");
    expect(tool.tool_identifier).toBe("t");

    const ds = definition.nodes[3].configuration as DataSourceNodeConfiguration;
    expect(ds.node_type).toBe("data_source");
    expect(ds.connection_reference).toBe("c");

    const cf = definition.nodes[4].configuration as ControlFlowNodeConfiguration;
    expect(cf.node_type).toBe("control_flow");
    expect(cf.control_flow_kind).toBe("conditional_branch");
  });

  it("control_flow 四种 kind 值均正确导出", () => {
    const kinds: ControlFlowNodeConfiguration["control_flow_kind"][] = [
      "conditional_branch", "loop", "parallel_join", "human_review_gate",
    ];
    for (const kind of kinds) {
      const config: ControlFlowNodeConfiguration = {
        node_type: "control_flow",
        control_flow_kind: kind,
        conditions: [],
        parallel_failure_strategy: "fast_fail",
        review_timeout_action: "reject",
      };
      const result = getConfig(makeState([makeNode("n1", "CF", config)]), 0) as ControlFlowNodeConfiguration;
      expect(result.control_flow_kind).toBe(kind);
    }
  });
});

// ---------------------------------------------------------------------------
// CHG-18（optimize_output_display_node）：output_display 集合序列化三态
// + output_mode 省略/导出（DC-10 / INV-D15 / R-03）
// ---------------------------------------------------------------------------

describe("output_display 集合序列化三态与 output_mode 导出（CHG-18）", () => {
  function odConfig(
    config: OutputDisplayNodeConfiguration,
  ): OutputDisplayNodeConfiguration {
    return getConfig(
      makeState([makeNode("n_od", "输出展示", config)]),
      0,
    ) as OutputDisplayNodeConfiguration;
  }

  it("空引用集合（undefined / 空串 / 空数组）→ 省略 input_reference 字段", () => {
    const emptyVariants: Array<string | string[] | null> = ["", [], null];
    for (const variant of emptyVariants) {
      const result = odConfig({
        node_type: "output_display",
        input_reference: variant,
      });
      expect(result.input_reference).toBeUndefined();
      expect((result as unknown as Record<string, unknown>).input_references).toBeUndefined();
    }
    const absent = odConfig({ node_type: "output_display" });
    expect(absent.input_reference).toBeUndefined();
  });

  it("单元素（标量或单元素数组）→ 导出标量（最大兼容，INV-D15）", () => {
    const scalar = odConfig({
      node_type: "output_display",
      input_reference: "ref1",
    });
    expect(scalar.input_reference).toBe("ref1");

    const singleArray = odConfig({
      node_type: "output_display",
      input_reference: ["ref1"],
    });
    expect(singleArray.input_reference).toBe("ref1");
  });

  it("多元素数组 → 保序导出数组，且无 input_references 复数字段（R-03）", () => {
    const multi = odConfig({
      node_type: "output_display",
      input_reference: ["b_ref", "a_ref", "c_ref"],
      output_mode: ["screen", "file"],
      display_title: "结果",
    });
    expect(multi.input_reference).toEqual(["b_ref", "a_ref", "c_ref"]);
    expect((multi as unknown as Record<string, unknown>).input_references).toBeUndefined();
  });

  it("数组空白元素剔除后序列化", () => {
    const cleaned = odConfig({
      node_type: "output_display",
      input_reference: ["ref1", "", "  ", "ref2"],
    });
    expect(cleaned.input_reference).toEqual(["ref1", "ref2"]);
  });

  it("output_mode 等价默认（缺省 / [screen] / 净化后等价）→ 省略；非默认 → 数组导出", () => {
    const absent = odConfig({ node_type: "output_display" });
    expect(absent.output_mode).toBeUndefined();

    const screenOnly = odConfig({
      node_type: "output_display",
      output_mode: ["screen"],
    });
    expect(screenOnly.output_mode).toBeUndefined();

    const purified = odConfig({
      node_type: "output_display",
      output_mode: ["screen", "bogus"],
    });
    expect(purified.output_mode).toBeUndefined();

    const fileOnly = odConfig({
      node_type: "output_display",
      output_mode: ["file"],
    });
    expect(fileOnly.output_mode).toEqual(["file"]);

    const both = odConfig({
      node_type: "output_display",
      output_mode: ["screen", "file"],
    });
    expect(both.output_mode).toEqual(["screen", "file"]);
  });

  it("display_title 非空保留、空值省略", () => {
    const withTitle = odConfig({
      node_type: "output_display",
      input_reference: "ref1",
      display_title: "结果",
    });
    expect(withTitle.display_title).toBe("结果");

    const noTitle = odConfig({ node_type: "output_display" });
    expect(noTitle.display_title).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// CHG-18（optimize_output_display_node）：旧 YAML 往返
// UT-14 单值 / UT-15 缺省 / UT-17 多元素 / 兼选模式（INV-05 / INV-D05）
// ---------------------------------------------------------------------------

describe("旧 YAML 往返（UT-14/15/17，CHG-18）", () => {
  /** 构造旧格式 YAML：trigger + output_display 两节点一连线（导入器拒绝空 edges） */
  function makeLegacyYaml(outputDisplayFields: string): string {
    return `spec_version: "1.0"
definition_id: legacy_wf
name: 旧工作流
definition_version: 1
inputs: []
outputs: []
nodes:
  - node_id: n_trigger
    name: 触发
    node_type: trigger
    configuration:
      node_type: trigger
      trigger_type: manual
      input_template: {}
  - node_id: n_display
    name: 输出展示
    node_type: output_display
    configuration:
      node_type: output_display
${outputDisplayFields}
edges:
  - edge_id: e1
    source_node_id: n_trigger
    target_node_id: n_display
`;
  }

  /** 导入 YAML 为画布状态（导入失败即断言失败） */
  function importCanvas(text: string): WorkflowCanvasState {
    const imported = yamlToCanvas(text);
    expect(imported.ok).toBe(true);
    if (!imported.ok) {
      throw new Error(imported.error);
    }
    return imported.canvasState;
  }

  /** 提取 n_display 节点的 output_display 配置 */
  function displayConfigOf(
    state: WorkflowCanvasState,
  ): OutputDisplayNodeConfiguration {
    const node = state.nodes.find((n) => n.data.node_id === "n_display");
    expect(node).toBeDefined();
    return node!.data.configuration as OutputDisplayNodeConfiguration;
  }

  it("UT-14 旧单值 YAML：标量加载保持，导出往返语义等价", () => {
    const first = importCanvas(
      makeLegacyYaml("      input_reference: up.x\n      display_title: 结果"),
    );
    const firstConfig = displayConfigOf(first);
    expect(firstConfig.input_reference).toBe("up.x");
    expect(firstConfig.display_title).toBe("结果");

    const exportedText = canvasToYaml(first);
    expect(exportedText).toContain("input_reference: up.x");

    // 二次往返：导入后标量保持，再导出文本一致（语义等价稳定，INV-05）
    const second = importCanvas(exportedText);
    expect(displayConfigOf(second).input_reference).toBe("up.x");
    expect(canvasToYaml(second)).toBe(exportedText);
  });

  it("UT-15 旧缺省字段 YAML：无 input_reference/output_mode → 再导出省略（展示全部+屏幕，语义不变）", () => {
    const first = importCanvas(makeLegacyYaml(""));
    const firstConfig = displayConfigOf(first);
    expect(firstConfig.input_reference).toBeUndefined();
    expect(firstConfig.output_mode).toBeUndefined();

    const exportedText = canvasToYaml(first);
    expect(exportedText).not.toContain("input_reference");
    expect(exportedText).not.toContain("output_mode");
    expect(exportedText).not.toContain("display_title");
  });

  it("UT-17 多元素：数组保序导出、导入还原往返一致", () => {
    const fields =
      "      input_reference:\n        - a\n        - b\n        - c";
    const first = importCanvas(makeLegacyYaml(fields));
    expect(displayConfigOf(first).input_reference).toEqual(["a", "b", "c"]);

    const exportedText = canvasToYaml(first);
    expect(exportedText).toContain("- b");

    // 二次往返：数组保序还原，再导出文本一致
    const second = importCanvas(exportedText);
    expect(displayConfigOf(second).input_reference).toEqual(["a", "b", "c"]);
    expect(canvasToYaml(second)).toBe(exportedText);
  });

  it("兼选模式 output_mode [screen, file]：导入导出保序往返", () => {
    const fields =
      "      input_reference: up.x\n      output_mode:\n        - screen\n        - file";
    const first = importCanvas(makeLegacyYaml(fields));
    expect(displayConfigOf(first).output_mode).toEqual(["screen", "file"]);

    const exportedText = canvasToYaml(first);
    expect(exportedText).toContain("output_mode");
    expect(exportedText).toContain("- file");

    const second = importCanvas(exportedText);
    expect(displayConfigOf(second).output_mode).toEqual(["screen", "file"]);
    expect(canvasToYaml(second)).toBe(exportedText);
  });

  it("UT-N 显式 null 引用 YAML：导入透传 null、导出省略、二次往返语义等价（CHG-08，修 DC-06 漏测）", () => {
    // 修复目标场景：YAML 显式 input_reference: null（后端 Optional 序列化形态）
    const first = importCanvas(makeLegacyYaml("      input_reference: null"));
    expect(displayConfigOf(first).input_reference).toBeNull();

    // null ≡ 空集合：导出省略该字段（语义 = 展示上游全部命名输出）
    const exportedText = canvasToYaml(first);
    expect(exportedText).not.toContain("input_reference");

    // 二次往返：省略形态稳定（null 与缺省均为「展示上游全部命名输出」，语义等价）
    const second = importCanvas(exportedText);
    expect(displayConfigOf(second).input_reference).toBeUndefined();
    expect(canvasToYaml(second)).toBe(exportedText);
  });
});

// ---------------------------------------------------------------------------
// expand_data_transform_ops：链与输出格式导出规则（CHG-F07 / DC-01，T9.2/T9.4）
// ---------------------------------------------------------------------------

describe("data_transform 链与输出格式导出（CHG-F07 / DC-01）", () => {
  /** 导出单个 data_transform 节点配置 */
  function dtConfig(
    config: DataTransformNodeConfiguration,
  ): DataTransformNodeConfiguration {
    return getConfig(
      makeState([makeNode("n_dt", "数据转换", config)]),
      0,
    ) as DataTransformNodeConfiguration;
  }

  /** 构造链测试基型配置 */
  function chainConfig(
    overrides: Partial<DataTransformNodeConfiguration>,
  ): DataTransformNodeConfiguration {
    return {
      node_type: "data_transform",
      operation: "map",
      input_dataset_reference: "",
      params: { map_expression: "True" },
      ...overrides,
    };
  }

  it("链长 > 1 → 写出 operations（每步 params 白名单净化）", () => {
    const sortParams: Record<string, unknown> = {
      sort_keys: ["a", "b"],
      sort_orders: ["desc"],
      unknown_junk: "x",
    };
    const result = dtConfig(
      chainConfig({
        input_dataset_reference: "up.x",
        operations: [
          { operation: "map", params: { map_expression: "True" } },
          {
            operation: "sort",
            params: sortParams as unknown as DataTransformOperationParams,
          },
        ],
      }),
    );
    expect(result.operations).toEqual([
      { operation: "map", params: { map_expression: "True" } },
      { operation: "sort", params: { sort_keys: ["a", "b"], sort_orders: ["desc"] } },
    ]);
    expect(result.operation).toBe("map");
    expect(result.input_dataset_reference).toBe("up.x");
  });

  it("链长 1 且与顶层等价 → 不写 operations（旧导出形态保真）", () => {
    const result = dtConfig(
      chainConfig({
        operations: [{ operation: "map", params: { map_expression: "True" } }],
      }),
    );
    expect(result.operation).toBe("map");
    expect(result.params.map_expression).toBe("True");
    expect(result.operations).toBeUndefined();
  });

  it("链长 1 与顶层不等价 → 写出 operations", () => {
    const result = dtConfig(
      chainConfig({
        operations: [
          { operation: "filter", params: { filter_condition: "x > 1" } },
        ],
      }),
    );
    expect(result.operations).toEqual([
      { operation: "filter", params: { filter_condition: "x > 1" } },
    ]);
    expect(result.operation).toBe("map");
  });

  it("等价判定键序无关：嵌套映射键序不同视为等价不写 operations", () => {
    const result = dtConfig(
      chainConfig({
        operation: "rename",
        params: { rename_map: { b: "c", a: "d" } },
        operations: [
          { operation: "rename", params: { rename_map: { a: "d", b: "c" } } },
        ],
      }),
    );
    expect(result.operations).toBeUndefined();
    expect(result.params.rename_map).toEqual({ a: "d", b: "c" });
  });

  it("空 operations 数组 → 不写 operations", () => {
    const result = dtConfig(chainConfig({ operations: [] }));
    expect(result.operations).toBeUndefined();
  });

  it("默认输出格式（缺省 / 显式 dataset）→ 不写 output_format", () => {
    const absent = dtConfig(chainConfig({}));
    expect(absent.output_format).toBeUndefined();
    expect(absent.output_format_params).toBeUndefined();

    const explicit = dtConfig(chainConfig({ output_format: "dataset" }));
    expect(explicit.output_format).toBeUndefined();
    expect(explicit.output_format_params).toBeUndefined();
  });

  it("非默认输出格式 text + 非空参数 → 写 output_format 与 output_format_params", () => {
    const result = dtConfig(
      chainConfig({
        output_format: "text",
        output_format_params: { text_field: "content", text_separator: ";" },
      }),
    );
    expect(result.output_format).toBe("text");
    expect(result.output_format_params).toEqual({
      text_field: "content",
      text_separator: ";",
    });
  });

  it("非默认输出格式空参数 → 只写 output_format；record_index=0 保留", () => {
    const emptyParams = dtConfig(
      chainConfig({ output_format: "text", output_format_params: {} }),
    );
    expect(emptyParams.output_format).toBe("text");
    expect(emptyParams.output_format_params).toBeUndefined();

    const zeroIndex = dtConfig(
      chainConfig({
        output_format: "single_object",
        output_format_params: { record_index: 0 },
      }),
    );
    expect(zeroIndex.output_format_params?.record_index).toBe(0);
  });

  it("新参数字段白名单：数值 0 保留、falsy 填充值保留、空串与未知字段剔除", () => {
    const params: Record<string, unknown> = {
      group: 0,
      fill_value: "",
      sample_mode: "",
      unknown_junk: "x",
    };
    const result = dtConfig({
      node_type: "data_transform",
      operation: "regex_extract",
      input_dataset_reference: "",
      params: params as unknown as DataTransformOperationParams,
    });
    expect(result.params.group).toBe(0);
    expect(result.params.fill_value).toBe("");
    expect(result.params).toEqual({ group: 0, fill_value: "" });
  });

  it("导出 → 导入 → 再导出幂等（双步链 + 非默认输出格式，含一条边满足导入器约束）", () => {
    const config: DataTransformNodeConfiguration = {
      node_type: "data_transform",
      operation: "map",
      input_dataset_reference: "up.x",
      params: { map_expression: "True" },
      operations: [
        { operation: "map", params: { map_expression: "True" } },
        { operation: "sort", params: { sort_keys: ["a"], sort_orders: ["desc"] } },
      ],
      output_format: "text",
      output_format_params: { text_field: "content", text_separator: ";" },
    };
    const state: WorkflowCanvasState = {
      nodes: [
        makeNode("n_trig", "触发", {
          node_type: "trigger",
          trigger_type: "manual",
          input_template: {},
        } as TriggerNodeConfiguration),
        makeNode("n_dt", "数据转换", config),
      ],
      edges: [
        { id: "e1", source: "n_trig", target: "n_dt" },
      ],
    };
    const exportedText = canvasToYaml(state);
    expect(exportedText).toContain("operations:");
    expect(exportedText).toContain("output_format: text");

    const imported = yamlToCanvas(exportedText);
    expect(imported.ok).toBe(true);
    if (!imported.ok) {
      throw new Error(imported.error);
    }
    const importedNode = imported.canvasState.nodes.find(
      (n) => n.data.node_id === "n_dt",
    );
    expect(importedNode).toBeDefined();
    const importedConfig = importedNode!.data.configuration as DataTransformNodeConfiguration;
    expect(importedConfig.operations).toEqual(config.operations);
    expect(importedConfig.output_format).toBe("text");
    expect(importedConfig.output_format_params).toEqual(config.output_format_params);

    const secondExported = canvasToYaml(imported.canvasState);
    expect(secondExported).toBe(exportedText);
  });
});