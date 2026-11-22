// =============================================================================
// 前端 YAML 往返一致性测试（workflow-definition/__tests__/yamlRoundTrip.test.ts）
//
// 执行：cd web && npx vitest run src/workflow-definition/__tests__/yamlRoundTrip.test.ts
//
// 覆盖用例（design §2.8.5 / DC-08 / tasks.md T9.7，CHG-35/CHG-36 验收）：
//   YRT-01 含 embedding_model 的旧 YAML：导入不报错，导出→再导入往返稳定
//   YRT-02 不含 embedding_model 的 YAML：导入不报错；导出产物无该字段（无空 {} 脏字段）
//   YRT-03 缺省 embedding_model 画布：导出 YAML 无该键；导出→导入→导出文本一致
//   YRT-04 llm 采样未配置：导出 YAML 无六采样键；再加载逐字段一致（llm_code_nodes_v2 DC-14）
//   YRT-05 llm 显式采样：0 值与空数组为合法显式配置，导出保留且往返逐字段一致
//   YRT-06 code 多行脚本与依赖清单：往返逐字段一致（含 dependencies: [] 与 sandbox_policy_id 保真）
//   YRT-07 形态 B 引用（node_id::position，tool/agent 双载体）：引用字符串往返
//         逐字节一致 + 导入态 validateReference 判 valid（upstream_input_ref
//         T6.3 / DC-01 / INV-05）
// =============================================================================

import { describe, it, expect } from "vitest";
import { canvasToYaml } from "../canvasToYamlExporter";
import { yamlToCanvas } from "../yamlToCanvasImporter";
import { validateReference } from "../../canvas/nodeIoTopology";
import { NODE_OUTPUT_CONTRACTS } from "../../canvas/nodeOutputContracts";
import type {
  WorkflowCanvasState,
  CanvasNode,
} from "../../canvas/canvasTypes";
import type {
  VectorKnowledgeBaseNodeConfiguration,
  LlmNodeConfiguration,
  CodeNodeConfiguration,
  ToolNodeConfiguration,
  AgentNodeConfiguration,
} from "../definitionTypes";
import {
  isLlmConfig,
  isCodeConfig,
  isToolConfig,
  isAgentConfig,
} from "../definitionTypes";

// ---------------------------------------------------------------------------
// 测试数据工厂
// ---------------------------------------------------------------------------

/** 构造画布节点壳（最小两节点：触发器 → 向量知识库，含连线） */
function makeState(
  vkbConfig: VectorKnowledgeBaseNodeConfiguration,
): WorkflowCanvasState {
  const trigger: CanvasNode = {
    id: "n1",
    type: "trigger",
    position: { x: 0, y: 0 },
    data: {
      node_id: "n1",
      name: "手动触发",
      node_type: "trigger",
      configuration: {
        node_type: "trigger",
        trigger_type: "manual",
        input_template: {},
      },
    },
  };
  const vkb: CanvasNode = {
    id: "n2",
    type: "vector_knowledge_base",
    position: { x: 250, y: 0 },
    data: {
      node_id: "n2",
      name: "向量知识库",
      node_type: "vector_knowledge_base",
      configuration: vkbConfig,
    },
  };
  return { nodes: [trigger, vkb], edges: [{ id: "e1", source: "n1", target: "n2" }] };
}

/** 含 embedding_model 的旧配置（CHG-35 之前的旧 YAML 形态） */
function legacyConfig(): VectorKnowledgeBaseNodeConfiguration {
  return {
    node_type: "vector_knowledge_base",
    knowledge_base_id: "kb_main",
    query_mode: "manual",
    query_value: "量子计算的基本原理",
    top_k: 5,
    embedding_model: {
      mode: "api",
      model_identifier: "emb-model",
      base_url: "https://emb.example.com",
      timeout_seconds: 30,
    },
  };
}

/** 不含 embedding_model 的配置（CHG-35 之后：绑定由知识库提供，字段缺省） */
function bindingOnlyConfig(): VectorKnowledgeBaseNodeConfiguration {
  return {
    node_type: "vector_knowledge_base",
    knowledge_base_id: "kb_main",
    query_mode: "manual",
    query_value: "量子计算的基本原理",
    top_k: 5,
  };
}

// ---------------------------------------------------------------------------
// llm/code 往返工厂（YRT-04~06，llm_code_nodes_v2 CHG-F05/DC-14）
// ---------------------------------------------------------------------------

/** 共用触发器节点（YRT-04~06 工厂复用） */
function makeTriggerNode(): CanvasNode {
  return {
    id: "n1",
    type: "trigger",
    position: { x: 0, y: 0 },
    data: {
      node_id: "n1",
      name: "手动触发",
      node_type: "trigger",
      configuration: {
        node_type: "trigger",
        trigger_type: "manual",
        input_template: {},
      },
    },
  };
}

/** 构造画布状态：触发器 → 指定节点（YRT-04~06 复用） */
function makeStateWithNode(node: CanvasNode): WorkflowCanvasState {
  return {
    nodes: [makeTriggerNode(), node],
    edges: [{ id: "e1", source: "n1", target: node.id }],
  };
}

/** 大模型节点壳 */
function makeLlmNode(config: LlmNodeConfiguration): CanvasNode {
  return {
    id: "n2",
    type: "llm",
    position: { x: 250, y: 0 },
    data: {
      node_id: "n2",
      name: "大模型",
      node_type: "llm",
      configuration: config,
    },
  };
}

/** 代码节点壳 */
function makeCodeNode(config: CodeNodeConfiguration): CanvasNode {
  return {
    id: "n2",
    type: "code",
    position: { x: 250, y: 0 },
    data: {
      node_id: "n2",
      name: "代码",
      node_type: "code",
      configuration: config,
    },
  };
}

// ---------------------------------------------------------------------------
// 往返用例
// ---------------------------------------------------------------------------

describe("前端 YAML 往返一致性（CHG-35/CHG-36，§2.8.5）", () => {
  it("YRT-01 含 embedding_model 的旧 YAML：导入不报错，往返稳定（字段逐一还原）", () => {
    const yamlText1 = canvasToYaml(makeState(legacyConfig()));

    // 旧 YAML 导入不报错（INV-D05：必填→可选是唯一不破坏旧 YAML 的兼容路径）
    const imported = yamlToCanvas(yamlText1);
    expect(imported.ok).toBe(true);
    if (!imported.ok) {
      throw new Error(imported.error);
    }

    // 导入还原：embedding_model 逐字段保留
    const restoredVkb = imported.canvasState.nodes.find(
      (n) => n.data.node_id === "n2",
    );
    expect(restoredVkb).toBeDefined();
    const restoredConfig = restoredVkb?.data
      .configuration as VectorKnowledgeBaseNodeConfiguration;
    expect(restoredConfig.embedding_model).toEqual({
      mode: "api",
      model_identifier: "emb-model",
      base_url: "https://emb.example.com",
      timeout_seconds: 30,
    });

    // 导出→导入→再导出：文本一致（往返稳定）；产物含 embedding_model 且非空对象
    const yamlText2 = canvasToYaml(imported.canvasState);
    expect(yamlText2).toBe(yamlText1);
    expect(yamlText2).toContain("embedding_model");
    expect(yamlText2).not.toContain("embedding_model: {}");
  });

  it("YRT-02 不含 embedding_model 的 YAML：导入不报错；导出产物无该字段", () => {
    const yamlText = canvasToYaml(makeState(bindingOnlyConfig()));

    // 缺省 embedding_model 的 YAML 导入不报错（AC-46：缺省由知识库绑定提供）
    const imported = yamlToCanvas(yamlText);
    expect(imported.ok).toBe(true);
    if (!imported.ok) {
      throw new Error(imported.error);
    }
    const restoredConfig = imported.canvasState.nodes.find(
      (n) => n.data.node_id === "n2",
    )?.data.configuration as VectorKnowledgeBaseNodeConfiguration;
    expect(restoredConfig.embedding_model).toBeUndefined();

    // 导出产物无 embedding_model 字段（CHG-36：不产生空 embedding_model: {} 脏字段）
    const exportedText = canvasToYaml(imported.canvasState);
    expect(exportedText).not.toContain("embedding_model");
    expect(exportedText).not.toContain("embedding_model: {}");
  });

  it("YRT-03 缺省 embedding_model：导出→导入→导出文本一致（二次往返稳定）", () => {
    const yamlText1 = canvasToYaml(makeState(bindingOnlyConfig()));
    expect(yamlText1).not.toContain("embedding_model");

    const first = yamlToCanvas(yamlText1);
    expect(first.ok).toBe(true);
    if (!first.ok) {
      throw new Error(first.error);
    }
    const yamlText2 = canvasToYaml(first.canvasState);
    expect(yamlText2).toBe(yamlText1);

    // 二次导入依旧稳定，无脏字段累积
    const second = yamlToCanvas(yamlText2);
    expect(second.ok).toBe(true);
    if (!second.ok) {
      throw new Error(second.error);
    }
    expect(canvasToYaml(second.canvasState)).toBe(yamlText1);
  });
});

// ---------------------------------------------------------------------------
// llm/code 往返用例（llm_code_nodes_v2 DC-14：None 与显式缺省区分）
// ---------------------------------------------------------------------------

describe("llm/code YAML 往返一致性（llm_code_nodes_v2，DC-14）", () => {
  it("YRT-04 llm 采样未配置：导出 YAML 无六采样键；再加载逐字段一致", () => {
    const config: LlmNodeConfiguration = {
      node_type: "llm",
      model_identifier: "qwen-max",
      system_prompt: "你是一个严谨的助手",
      user_prompt: "请回答用户问题",
    };
    const yamlText1 = canvasToYaml(makeStateWithNode(makeLlmNode(config)));

    // 采样未配置即省略键：YAML 不含六采样键（不产生空值脏字段）
    expect(yamlText1).not.toContain("temperature");
    expect(yamlText1).not.toContain("top_p");
    expect(yamlText1).not.toContain("top_k");
    expect(yamlText1).not.toContain("max_tokens");
    expect(yamlText1).not.toContain("stop");
    expect(yamlText1).not.toContain("seed");
    // 导出产物含 llm 配置本体（非 undefined）
    expect(yamlText1).toContain("model_identifier: qwen-max");

    const imported = yamlToCanvas(yamlText1);
    expect(imported.ok).toBe(true);
    if (!imported.ok) {
      throw new Error(imported.error);
    }
    const restored = imported.canvasState.nodes.find(
      (n) => n.data.node_id === "n2",
    );
    expect(restored).toBeDefined();
    if (!restored) {
      throw new Error("expected restored node n2");
    }
    const restoredConfig = restored.data.configuration;
    expect(isLlmConfig(restoredConfig)).toBe(true);
    if (!isLlmConfig(restoredConfig)) {
      throw new Error("expected llm configuration");
    }
    // 再加载逐字段一致：采样未配置为 undefined（与显式 0/空区分，DC-14）
    expect(restoredConfig).toEqual(config);
    expect(restoredConfig.temperature).toBeUndefined();
    expect(restoredConfig.seed).toBeUndefined();

    // 导出→导入→再导出文本一致（往返稳定）
    const yamlText2 = canvasToYaml(imported.canvasState);
    expect(yamlText2).toBe(yamlText1);
  });

  it("YRT-05 llm 显式采样：0 值与空数组为合法显式配置，导出保留且往返逐字段一致", () => {
    const config: LlmNodeConfiguration = {
      node_type: "llm",
      model_identifier: "qwen-max",
      system_prompt: "你是一个严谨的助手",
      user_prompt: "请回答用户问题",
      temperature: 0,
      top_p: 0.5,
      top_k: 0,
      max_tokens: 1024,
      stop: [],
      seed: 0,
    };
    const yamlText1 = canvasToYaml(makeStateWithNode(makeLlmNode(config)));

    // 0 为合法显式值：导出保留（greedy 采样 temperature: 0 常见形态，不得被剔除）
    expect(yamlText1).toContain("temperature: 0");
    expect(yamlText1).toContain("top_k: 0");
    expect(yamlText1).toContain("seed: 0");
    expect(yamlText1).toContain("max_tokens: 1024");
    expect(yamlText1).toContain("stop: []");

    const imported = yamlToCanvas(yamlText1);
    expect(imported.ok).toBe(true);
    if (!imported.ok) {
      throw new Error(imported.error);
    }
    const restoredNode = imported.canvasState.nodes.find(
      (n) => n.data.node_id === "n2",
    );
    expect(restoredNode).toBeDefined();
    if (!restoredNode) {
      throw new Error("expected restored node n2");
    }
    const restoredConfig = restoredNode.data.configuration;
    expect(isLlmConfig(restoredConfig)).toBe(true);
    if (!isLlmConfig(restoredConfig)) {
      throw new Error("expected llm configuration");
    }
    // 再加载逐字段一致：0 值仍为 0、空数组保真（不被误判为未配置剔除）
    expect(restoredConfig).toEqual(config);
    expect(restoredConfig.temperature).toBe(0);
    expect(restoredConfig.top_k).toBe(0);
    expect(restoredConfig.seed).toBe(0);
    expect(restoredConfig.stop).toEqual([]);

    // 导出→导入→再导出文本一致（显式采样往返稳定）
    const yamlText2 = canvasToYaml(imported.canvasState);
    expect(yamlText2).toBe(yamlText1);
  });

  it("YRT-06 code 多行脚本与依赖清单：往返逐字段一致（含 dependencies/sandbox_policy_id 保真）", () => {
    const config: CodeNodeConfiguration = {
      node_type: "code",
      language: "python",
      code: "def main(payload):\n    value = 21 * 2\n    return {'answer': value}\n",
      entry_function: "main",
      dependencies: [],
      sandbox_policy_id: "sp_default",
    };
    const yamlText1 = canvasToYaml(makeStateWithNode(makeCodeNode(config)));

    // 依赖清单与沙箱策略标识导出保留（dependencies: [] 为显式空依赖，非未配置）
    expect(yamlText1).toContain("sandbox_policy_id: sp_default");
    expect(yamlText1).toContain("dependencies: []");

    const imported = yamlToCanvas(yamlText1);
    expect(imported.ok).toBe(true);
    if (!imported.ok) {
      throw new Error(imported.error);
    }
    const restoredNode = imported.canvasState.nodes.find(
      (n) => n.data.node_id === "n2",
    );
    expect(restoredNode).toBeDefined();
    if (!restoredNode) {
      throw new Error("expected restored node n2");
    }
    const restoredConfig = restoredNode.data.configuration;
    expect(isCodeConfig(restoredConfig)).toBe(true);
    if (!isCodeConfig(restoredConfig)) {
      throw new Error("expected code configuration");
    }
    // 再加载逐字段一致：多行脚本逐字还原（含缩进与换行）
    expect(restoredConfig).toEqual(config);
    expect(restoredConfig.code).toBe(config.code);
    expect(restoredConfig.dependencies).toEqual([]);

    // 导出→导入→再导出文本一致（多行块往返稳定）
    const yamlText2 = canvasToYaml(imported.canvasState);
    expect(yamlText2).toBe(yamlText1);
  });
});

// ---------------------------------------------------------------------------
// 形态 B 引用往返用例（upstream_input_ref T6.3 / DC-01 / INV-05）
// ---------------------------------------------------------------------------

describe("形态 B 引用 YAML 往返一致性（upstream_input_ref，DC-01/INV-05）", () => {
  /** 触发器（input_template 含绑定 q —— 形态 B 引用的作用域来源） */
  function makeFormBTrigger(): CanvasNode {
    return {
      id: "n1",
      type: "trigger",
      position: { x: 0, y: 0 },
      data: {
        node_id: "n1",
        name: "手动触发",
        node_type: "trigger",
        configuration: {
          node_type: "trigger",
          trigger_type: "manual",
          input_template: { q: "启动参数" },
        },
      },
    };
  }

  it("YRT-07 形态 B 引用（tool/agent 双载体）：往返逐字节一致，导入态校验 valid", () => {
    const toolConfig: ToolNodeConfiguration = {
      node_type: "tool",
      tool_identifier: "t",
      tool_source: "local",
      argument_mapping: { query: "n1::q" },
      static_arguments: {},
    };
    const agentConfig: AgentNodeConfiguration = {
      node_type: "agent",
      model_identifier: "m",
      system_prompt: "",
      available_tools: [],
      inputs: [{ reference: "n1::q", alias: "p" }],
    };
    const state: WorkflowCanvasState = {
      nodes: [
        makeFormBTrigger(),
        {
          id: "n2",
          type: "tool",
          position: { x: 250, y: 0 },
          data: {
            node_id: "n2",
            name: "工具",
            node_type: "tool",
            configuration: toolConfig,
          },
        },
        {
          id: "n3",
          type: "agent",
          position: { x: 500, y: 0 },
          data: {
            node_id: "n3",
            name: "智能体",
            node_type: "agent",
            configuration: agentConfig,
          },
        },
      ],
      edges: [
        { id: "e1", source: "n1", target: "n2" },
        { id: "e2", source: "n2", target: "n3" },
      ],
    };

    // 导出：引用字符串以字面量保真（n1::q）
    const yamlText1 = canvasToYaml(state);
    expect(yamlText1).toContain("n1::q");

    const imported = yamlToCanvas(yamlText1);
    expect(imported.ok).toBe(true);
    if (!imported.ok) {
      throw new Error(imported.error);
    }

    // 导入还原：双载体引用字符串逐字节一致
    const tool = imported.canvasState.nodes.find(
      (n) => n.data.node_id === "n2",
    );
    const agent = imported.canvasState.nodes.find(
      (n) => n.data.node_id === "n3",
    );
    expect(tool).toBeDefined();
    expect(agent).toBeDefined();
    if (!tool || !agent) {
      throw new Error("expected restored nodes n2/n3");
    }
    expect(isToolConfig(tool.data.configuration)).toBe(true);
    if (!isToolConfig(tool.data.configuration)) {
      throw new Error("expected tool configuration");
    }
    expect(tool.data.configuration.argument_mapping.query).toBe("n1::q");
    expect(isAgentConfig(agent.data.configuration)).toBe(true);
    if (!isAgentConfig(agent.data.configuration)) {
      throw new Error("expected agent configuration");
    }
    expect(agent.data.configuration.inputs?.[0]?.reference).toBe("n1::q");
    expect(agent.data.configuration.inputs?.[0]?.alias).toBe("p");

    // 导入态校验关口：形态 B 引用对目标节点判 valid（A3 接口，非空非形态 B 绑定）
    expect(
      validateReference(
        "n1::q",
        "n2",
        imported.canvasState,
        NODE_OUTPUT_CONTRACTS,
      ),
    ).toEqual({ kind: "valid" });

    // 导出→导入→再导出：文本逐字节一致（往返稳定）
    const yamlText2 = canvasToYaml(imported.canvasState);
    expect(yamlText2).toBe(yamlText1);
    const second = yamlToCanvas(yamlText2);
    expect(second.ok).toBe(true);
    if (!second.ok) {
      throw new Error(second.error);
    }
    expect(canvasToYaml(second.canvasState)).toBe(yamlText1);
  });
});
