// =============================================================================
// 节点配置面板输出契约展示区测试
// （canvas/__tests__/NodeConfigurationPanel.outputContract.test.tsx）
//
// 留待非沙箱环境执行：cd web && npx vitest run NodeConfigurationPanel.outputContract
//
// 覆盖用例（T9.4）：
//   TC-OC-01 8 类前端类型逐一展示契约 category
//   TC-OC-02 control_flow 展示"不可引用"徽标
//   TC-OC-03 可引用类型不展示"不可引用"徽标
//   TC-OC-04 agent 展示 schemaEditable 提示
//   TC-OC-05 非 agent 类型不展示 schemaEditable 提示
//   TC-OC-06 <style> 注入块含 wfp-ncp-contract 系列样式
//   TC-OC-07 浅色/深色模式样式分支
// =============================================================================

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import NodeConfigurationPanel from "../NodeConfigurationPanel";
import type { CanvasNodeData, WorkflowCanvasState } from "../canvasTypes";
import type { NodeType, NodeConfiguration } from "../../workflow-definition/definitionTypes";

// 主题 mock
let mockTheme: "light" | "dark" = "light";
vi.mock("../../theme/themeMode", async () => {
  const actual = await vi.importActual<typeof import("../../theme/themeMode")>(
    "../../theme/themeMode",
  );
  return { ...actual, useThemeMode: () => mockTheme };
});

// ---------------------------------------------------------------------------
// 测试数据工厂
// ---------------------------------------------------------------------------

function makeMinimalConfig(nodeType: NodeType): NodeConfiguration {
  switch (nodeType) {
    case "trigger":
      return { node_type: "trigger", trigger_type: "manual", input_template: {} };
    case "agent":
      return { node_type: "agent", model_identifier: "m", system_prompt: "", available_tools: [] };
    case "tool":
      return { node_type: "tool", tool_identifier: "t", tool_source: "local", argument_mapping: {}, static_arguments: {} };
    case "data_source":
      return { node_type: "data_source", connection_reference: "c", query: "" };
    case "control_flow":
      return { node_type: "control_flow", control_flow_kind: "conditional_branch", conditions: [], parallel_failure_strategy: "fast_fail", review_timeout_action: "reject" };
    case "subworkflow":
      return { node_type: "subworkflow", subworkflow_definition_id: "w", input_mapping: {}, output_mapping: {}, context_strategy: "inherit", max_nesting_depth: 3 };
    case "dynamic_injector":
      return { node_type: "dynamic_injector", injection_condition: "", node_templates: [], max_injection_count: 1 };
    case "data_transform":
      return { node_type: "data_transform", operation: "map", input_dataset_reference: "", params: {} };
    case "output_display":
      return { node_type: "output_display" };
    case "vector_knowledge_base":
      return { node_type: "vector_knowledge_base", knowledge_base_id: "", query_mode: "manual", top_k: 5 };
    case "llm":
      return { node_type: "llm", model_identifier: "m", system_prompt: "", user_prompt: "" };
    case "code":
      return { node_type: "code", language: "python", code: "pass", entry_function: "main" };
  }
}

function makeNodeData(nodeType: NodeType): CanvasNodeData {
  return {
    node_id: "test-node",
    name: "测试节点",
    node_type: nodeType,
    configuration: makeMinimalConfig(nodeType),
  };
}

function makeCanvasState(node: CanvasNodeData): WorkflowCanvasState {
  return {
    nodes: [{ id: "test-node", type: node.node_type, position: { x: 0, y: 0 }, data: node }],
    edges: [],
  };
}

function renderPanel(nodeType: NodeType) {
  const node = makeNodeData(nodeType);
  const canvasState = makeCanvasState(node);
  const { container } = render(
    <NodeConfigurationPanel
      selectedNode={node}
      canvasState={canvasState}
      onCanvasStateChange={vi.fn()}
    /> as ReactNode,
  );
  return { container, node };
}

const ALL_NODE_TYPES: NodeType[] = [
  "trigger",
  "agent",
  "tool",
  "data_source",
  "control_flow",
  "subworkflow",
  "dynamic_injector",
  "data_transform",
];

// ---------------------------------------------------------------------------
// TC-OC-01 8 类前端类型逐一展示契约 category
// ---------------------------------------------------------------------------

describe("OutputContractSection 契约展示（T9.4）", () => {
  beforeEach(() => {
    mockTheme = "light";
  });

  it("TC-OC-01 8 类前端类型均展示契约标题与 category", () => {
    for (const nt of ALL_NODE_TYPES) {
      const { container } = renderPanel(nt);
      const title = container.querySelector(".wfp-ncp-contract-title");
      expect(title?.textContent).toBe("输出格式（固定契约）");
      const category = container.querySelector(".wfp-ncp-contract-category");
      expect(category).not.toBeNull();
      expect(category?.textContent?.length).toBeGreaterThan(0);
    }
  });

  it("TC-OC-02 control_flow 展示'不可引用'徽标", () => {
    const { container } = renderPanel("control_flow");
    const badge = container.querySelector(".wfp-ncp-contract-badge--noref");
    expect(badge).not.toBeNull();
    expect(badge?.textContent).toContain("不可引用");
  });

  it("TC-OC-03 可引用类型不展示'不可引用'徽标", () => {
    const referableTypes: NodeType[] = [
      "trigger",
      "agent",
      "tool",
      "data_source",
      "subworkflow",
      "dynamic_injector",
      "data_transform",
    ];
    for (const nt of referableTypes) {
      const { container } = renderPanel(nt);
      const badge = container.querySelector(".wfp-ncp-contract-badge--noref");
      expect(badge).toBeNull();
    }
  });

  it("TC-OC-04 agent 展示 schemaEditable 提示（output_schema 细化）", () => {
    const { container } = renderPanel("agent");
    const hint = container.querySelector(".wfp-ncp-contract-hint");
    expect(hint).not.toBeNull();
    expect(hint?.textContent).toContain("output_schema");
  });

  it("TC-OC-05 非 agent 类型不展示 schemaEditable 提示", () => {
    const nonAgentTypes: NodeType[] = [
      "trigger",
      "tool",
      "data_source",
      "control_flow",
      "subworkflow",
      "dynamic_injector",
      "data_transform",
    ];
    for (const nt of nonAgentTypes) {
      const { container } = renderPanel(nt);
      const hint = container.querySelector(".wfp-ncp-contract-hint");
      expect(hint).toBeNull();
    }
  });

  it("TC-OC-06 <style> 注入块含 wfp-ncp-contract 系列样式", () => {
    const { container } = renderPanel("agent");
    const css = Array.from(container.querySelectorAll("style"))
      .map((style) => style.textContent ?? "")
      .join("");
    expect(css).toContain(".wfp-ncp-contract");
    expect(css).toContain(".wfp-ncp-contract-title");
    expect(css).toContain(".wfp-ncp-contract-category");
    expect(css).toContain(".wfp-ncp-contract-badge");
    expect(css).toContain(".wfp-ncp-contract-detail");
  });

  it("TC-OC-07a 浅色模式样式含浅色色值", () => {
    mockTheme = "light";
    const { container } = renderPanel("agent");
    const css = container.querySelector("style")?.textContent ?? "";
    expect(css).toContain("#1a1a1a");
  });

  it("TC-OC-07b 深色模式样式含 darkTokens 色值", () => {
    mockTheme = "dark";
    const { container } = renderPanel("agent");
    const css = container.querySelector("style")?.textContent ?? "";
    expect(css).toContain("#f5f5f5");
  });

  it("各类型 category 文案正确", () => {
    const expectations: Record<NodeType, string> = {
      trigger: "输入参数集合（键值对）",
      agent: "结构化对象",
      tool: "工具调用结果",
      data_source: "数据集（记录集合）",
      control_flow: "控制信号",
      subworkflow: "键值对集合",
      dynamic_injector: "注入清单",
      data_transform: "数据集（记录集合）",
      output_display: "上游输出透传",
      vector_knowledge_base: "片段集合（文本片段列表）",
      llm: "结构化对象",
      code: "函数返回值（result）",
    };
    for (const nt of ALL_NODE_TYPES) {
      const { container } = renderPanel(nt);
      const category = container.querySelector(".wfp-ncp-contract-category");
      expect(category?.textContent).toBe(expectations[nt]);
    }
  });

  it("TC-OC-08 llm 面板：渲染大模型配置表单（不空白），身份条/徽章两键存在（llm_code_nodes_v2）", () => {
    const node: CanvasNodeData = {
      node_id: "test-node",
      name: "测试大模型",
      node_type: "llm",
      configuration: {
        node_type: "llm",
        model_identifier: "qwen-max",
        system_prompt: "你是测试助手",
        user_prompt: "{{n1.payload}}",
        temperature: 0.7,
      },
    };
    const canvasState = makeCanvasState(node);
    const { container } = render(
      <NodeConfigurationPanel
        selectedNode={node}
        canvasState={canvasState}
        onCanvasStateChange={vi.fn()}
      /> as ReactNode,
    );

    // 身份徽章（NCP_TYPE_IDENTITY.llm.badge）与身份条两键渲染存在
    expect(container.querySelector(".wfp-ncp-type-badge")?.textContent).toBe(
      "大模型节点",
    );
    expect(container.querySelector(".wfp-ncp-identity-bar")).not.toBeNull();

    // 配置表单不空白：双提示词 textarea 绑定配置值
    const textareaValues = Array.from(container.querySelectorAll("textarea")).map(
      (t) => t.value,
    );
    expect(textareaValues).toContain("你是测试助手");
    expect(textareaValues).toContain("{{n1.payload}}");
    // 五个采样 number 输入（temperature/top_p/top_k/max_tokens/seed）渲染存在
    const numberInputs = container.querySelectorAll('input[type="number"]');
    expect(numberInputs.length).toBeGreaterThanOrEqual(5);

    // 输出契约 category 同步展示（llm → 结构化对象）
    expect(
      container.querySelector(".wfp-ncp-contract-category")?.textContent,
    ).toBe("结构化对象");
  });

  it("TC-OC-09 code 面板：渲染代码配置表单（不空白），身份条/徽章两键存在（llm_code_nodes_v2）", () => {
    const node: CanvasNodeData = {
      node_id: "test-node",
      name: "测试代码",
      node_type: "code",
      configuration: {
        node_type: "code",
        language: "python",
        code: "def main(payload):\n    return {'ok': True}\n",
        entry_function: "main",
        dependencies: ["requests"],
      },
    };
    const canvasState = makeCanvasState(node);
    const { container } = render(
      <NodeConfigurationPanel
        selectedNode={node}
        canvasState={canvasState}
        onCanvasStateChange={vi.fn()}
      /> as ReactNode,
    );

    // 身份徽章（NCP_TYPE_IDENTITY.code.badge）与身份条两键渲染存在
    expect(container.querySelector(".wfp-ncp-type-badge")?.textContent).toBe(
      "代码节点",
    );
    expect(container.querySelector(".wfp-ncp-identity-bar")).not.toBeNull();

    // 配置表单不空白：脚本 textarea 绑定多行代码值、语言 select 渲染存在
    const textareaValues = Array.from(container.querySelectorAll("textarea")).map(
      (t) => t.value,
    );
    expect(textareaValues).toContain("def main(payload):\n    return {'ok': True}\n");
    expect(container.querySelector("select")).not.toBeNull();
    // 入口函数/依赖清单两字段标签存在（FR-18/FR-19 展示语义）
    expect(screen.getByText("入口函数")).toBeDefined();
    expect(screen.getByText("依赖清单")).toBeDefined();

    // 输出契约 category 同步展示（code → 函数返回值（result））
    expect(
      container.querySelector(".wfp-ncp-contract-category")?.textContent,
    ).toBe("函数返回值（result）");
  });
});