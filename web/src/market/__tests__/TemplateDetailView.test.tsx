// =============================================================================
// 模板详情视图测试（src/market/__tests__/TemplateDetailView.test.tsx）
//
// 留待非沙箱环境执行：cd web && npx vitest run src/market/__tests__/TemplateDetailView.test.tsx
//
// 覆盖用例（agent_tool_marketplace T18.5 / AC-15 ~ AC-18）：
//   TC-TDV-01 五维度 5 段标题与内容均渲染（固定次序，DC-07）
//   TC-TDV-02 工具调用配置 4 项
//   TC-TDV-03 性能边界与安全边界
//   TC-TDV-04 评价指标含可采集性标注
//   TC-TDV-05 recipe 渲染节点/连线摘要；single_node 无该段
//   TC-TDV-06 应用模板回调（DC-06 通道 1）
// =============================================================================

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { TemplateDetailView } from "../TemplateDetailView";
import type { AgentTemplate } from "../marketTypes";

/** 模板详情夹具（single_node 全量字段） */
function makeTemplate(overrides: Partial<AgentTemplate> = {}): AgentTemplate {
  return {
    template_id: "basic_react",
    name: "React 基础模板",
    kind: "single_node",
    preset: { model_identifier: "qwen-max", system_prompt: "sp", available_tools: [] },
    system_prompt: "sp",
    allowed_tools: [],
    max_tool_rounds: null,
    output_max_retries: null,
    doc: {
      core_principle_flow: "核心原理内容",
      application_features: "应用特性内容",
      applicable_scenarios: "适用场景内容",
      advantages_limitations: "优势与局限内容",
      comparison: "对比内容",
    },
    tool_config: {
      tool_types: ["builtin", "mcp"],
      invocation_mode: "react_loop",
      parameter_constraints: "参数约束内容",
      retry_and_exception_policy: "重试策略内容",
    },
    implementation_key_points: ["关键点1", "关键点2"],
    dependency_conditions: ["依赖条件1"],
    performance_boundaries: {
      max_tool_rounds: null,
      tool_timeout_seconds: 30,
      output_max_retries: null,
    },
    security_boundaries: ["安全边界1", "安全边界2"],
    evaluation_metrics: [
      { metric: "任务完成率", collectable: "runtime" },
      { metric: "静态检查通过数", collectable: "static" },
      { metric: "用户满意度", collectable: "not_quantifiable" },
    ],
    recipe: null,
    ...overrides,
  };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("TemplateDetailView", () => {
  it("TC-TDV-01 五维度 5 段标题与内容均渲染", () => {
    render(<TemplateDetailView template={makeTemplate()} />);

    const expectedTitles = [
      "核心原理与执行流程",
      "应用特性",
      "适用场景",
      "优势与局限",
      "与相关方案对比",
    ];
    const expectedContents = [
      "核心原理内容",
      "应用特性内容",
      "适用场景内容",
      "优势与局限内容",
      "对比内容",
    ];
    for (let i = 0; i < 5; i += 1) {
      const section = screen.getByTestId(`template-doc-section-${i}`);
      expect(section).toHaveTextContent(expectedTitles[i]!);
      expect(section).toHaveTextContent(expectedContents[i]!);
    }
  });

  it("TC-TDV-02 工具调用配置 4 项", () => {
    render(<TemplateDetailView template={makeTemplate()} />);

    const toolConfig = screen.getByTestId("template-tool-config");
    expect(toolConfig).toHaveTextContent("工具类型：builtin、mcp");
    expect(toolConfig).toHaveTextContent("调用方式：react_loop");
    expect(toolConfig).toHaveTextContent("参数约束：参数约束内容");
    expect(toolConfig).toHaveTextContent("失败重试与异常策略：重试策略内容");
  });

  it("TC-TDV-03 性能边界与安全边界", () => {
    render(<TemplateDetailView template={makeTemplate()} />);

    const performance = screen.getByTestId("template-performance-boundaries");
    // max_tool_rounds 缺省 → 回落全局 5（AC-13 规则 3）
    expect(performance).toHaveTextContent("最大工具轮次：缺省（回落全局 5）");
    expect(performance).toHaveTextContent("工具调用超时：30 秒");
    expect(performance).toHaveTextContent("输出重试上限：缺省");

    const security = screen.getByTestId("template-security-boundaries");
    expect(security).toHaveTextContent("安全边界1");
    expect(security).toHaveTextContent("安全边界2");
  });

  it("TC-TDV-04 评价指标含可采集性标注", () => {
    render(<TemplateDetailView template={makeTemplate()} />);

    const metrics = screen.getByTestId("template-evaluation-metrics");
    expect(metrics).toHaveTextContent("任务完成率");
    expect(metrics).toHaveTextContent("运行时可采集");
    expect(metrics).toHaveTextContent("静态检查通过数");
    expect(metrics).toHaveTextContent("静态可采集");
    expect(metrics).toHaveTextContent("用户满意度");
    expect(metrics).toHaveTextContent("不可量化");
  });

  it("TC-TDV-05 recipe 渲染节点/连线摘要；single_node 无该段", () => {
    // single_node：无配方摘要段
    const { unmount } = render(<TemplateDetailView template={makeTemplate()} />);
    expect(screen.queryByTestId("template-recipe-summary")).toBeNull();
    unmount();

    // recipe：节点表 + 连线摘要
    const recipeTemplate = makeTemplate({
      kind: "recipe",
      recipe: {
        nodes: [
          {
            local_ref: "agent_main",
            node_type: "agent",
            name: "主 Agent",
            configuration: { node_type: "agent" },
          },
          {
            local_ref: "cond_check",
            node_type: "condition",
            name: "条件判断",
            configuration: { node_type: "condition" },
          },
        ],
        edges: [
          { source_ref: "agent_main", target_ref: "cond_check", condition_label: "默认分支" },
        ],
      },
    });
    render(<TemplateDetailView template={recipeTemplate} />);

    const summary = screen.getByTestId("template-recipe-summary");
    expect(summary).toHaveTextContent("agent_main");
    expect(summary).toHaveTextContent("主 Agent");
    expect(summary).toHaveTextContent("cond_check");
    expect(summary).toHaveTextContent("agent_main → cond_check（默认分支）");
  });

  it("TC-TDV-06 应用模板回调（DC-06 通道 1）", () => {
    const onApplyTemplate = vi.fn();
    const template = makeTemplate();
    render(<TemplateDetailView template={template} onApplyTemplate={onApplyTemplate} />);

    fireEvent.click(screen.getByTestId("template-apply-button"));

    expect(onApplyTemplate).toHaveBeenCalledTimes(1);
    expect(onApplyTemplate).toHaveBeenCalledWith(template);
    expect(screen.getByTestId("template-apply-hint")).toHaveTextContent("模板已提交应用到画布");
  });
});
