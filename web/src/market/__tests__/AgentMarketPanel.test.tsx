// =============================================================================
// Agent 市场面板测试（src/market/__tests__/AgentMarketPanel.test.tsx）
//
// 留待非沙箱环境执行：cd web && npx vitest run src/market/__tests__/AgentMarketPanel.test.tsx
//
// 覆盖用例（agent_tool_marketplace T18.3 / AC-20 / AC-23 / AC-24 / 必做守护 ⑥）：
//   TC-AMP-01 挂载拉取条目清单
//   TC-AMP-02 拉取失败 → 错误条且页面可渲染
//   TC-AMP-03 关键字筛选触发新请求
//   TC-AMP-04 点击条目 → 详情区渲染
//   TC-AMP-05 「模板详情」→ 五维度分段存在
//   TC-AMP-06 回调未注入 → 「应用模板」降级提示（不写画布，T16.6）
//   TC-AMP-07 回调已注入 → 「应用模板」走 onApplyTemplate 通道（DC-06 通道 1）
// =============================================================================

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
import { AgentMarketPanel } from "../AgentMarketPanel";
import {
  listAgentMarketEntries,
  listAgentMarketCategories,
  getAgentTemplate,
} from "../marketApiClient";
import type { AgentMarketEntry, AgentTemplate } from "../marketTypes";

vi.mock("../marketApiClient", () => ({
  listAgentMarketEntries: vi.fn(),
  listAgentMarketCategories: vi.fn(),
  getAgentTemplate: vi.fn(),
}));

// —— 测试夹具 ——

/** 市场条目夹具 */
function makeEntry(overrides: Partial<AgentMarketEntry> = {}): AgentMarketEntry {
  return {
    entry_id: "e1",
    name: "React 基础模板",
    description: "单节点 React 循环模板",
    category: "分类A",
    tags: ["react"],
    status: "available",
    source_kind: "builtin",
    preset_ref: "basic_react",
    tool_refs: ["web_search"],
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-02T00:00:00Z",
    ...overrides,
  };
}

/** 模板详情夹具（五维度全量） */
function makeTemplateDetail(): AgentTemplate {
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
      tool_types: ["builtin"],
      invocation_mode: "react_loop",
      parameter_constraints: "参数约束内容",
      retry_and_exception_policy: "重试策略内容",
    },
    implementation_key_points: ["关键点1"],
    dependency_conditions: ["依赖条件1"],
    performance_boundaries: {
      max_tool_rounds: null,
      tool_timeout_seconds: 30,
      output_max_retries: null,
    },
    security_boundaries: ["安全边界1"],
    evaluation_metrics: [{ metric: "任务完成率", collectable: "runtime" }],
    recipe: null,
  };
}

beforeEach(() => {
  vi.mocked(listAgentMarketEntries).mockResolvedValue({ entries: [makeEntry()], total: 1 });
  vi.mocked(listAgentMarketCategories).mockResolvedValue({
    categories: [{ category: "分类A", count: 1 }],
    total: 1,
  });
  vi.mocked(getAgentTemplate).mockResolvedValue(makeTemplateDetail());
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("AgentMarketPanel", () => {
  it("TC-AMP-01 挂载拉取条目清单", async () => {
    render(<AgentMarketPanel />);

    await screen.findByTestId("agent-market-table");
    expect(listAgentMarketEntries).toHaveBeenCalledTimes(1);
    expect(await screen.findByTestId("agent-market-entry-e1")).toBeInTheDocument();
    expect(screen.getByText("React 基础模板")).toBeInTheDocument();
  });

  it("TC-AMP-02 拉取失败 → 错误条且页面可渲染", async () => {
    vi.mocked(listAgentMarketEntries).mockRejectedValue(new Error("查询 Agent 市场条目失败"));

    render(<AgentMarketPanel />);

    const errorBar = await screen.findByTestId("agent-market-error");
    expect(errorBar).toHaveTextContent("查询 Agent 市场条目失败");
    // 页面仍可渲染：标题与检索控件存在
    expect(screen.getByTestId("agent-market-keyword-input")).toBeInTheDocument();
    expect(screen.getByTestId("agent-market-search-button")).toBeInTheDocument();
  });

  it("TC-AMP-03 关键字筛选触发新请求", async () => {
    render(<AgentMarketPanel />);
    await screen.findByTestId("agent-market-table");

    fireEvent.change(screen.getByTestId("agent-market-keyword-input"), {
      target: { value: "react" },
    });
    fireEvent.click(screen.getByTestId("agent-market-search-button"));

    await waitFor(() => {
      expect(listAgentMarketEntries).toHaveBeenCalledTimes(2);
    });
    expect(listAgentMarketEntries).toHaveBeenNthCalledWith(2, {
      q: "react",
      category: undefined,
    });
  });

  it("TC-AMP-04 点击条目 → 详情区渲染", async () => {
    render(<AgentMarketPanel />);
    await screen.findByTestId("agent-market-table");

    fireEvent.click(screen.getByTestId("agent-market-entry-e1"));

    const detail = await screen.findByTestId("agent-market-detail");
    expect(detail).toHaveTextContent("React 基础模板");
    expect(detail).toHaveTextContent("e1");
  });

  it("TC-AMP-05 「模板详情」→ 五维度分段存在", async () => {
    render(<AgentMarketPanel />);
    await screen.findByTestId("agent-market-table");

    fireEvent.click(screen.getByTestId("agent-market-entry-e1"));
    fireEvent.click(await screen.findByTestId("agent-market-template-button"));

    await screen.findByTestId("agent-market-template-detail");
    expect(getAgentTemplate).toHaveBeenCalledWith("basic_react");
    // 五维度分段（固定次序，DC-07）
    for (let i = 0; i < 5; i += 1) {
      expect(screen.getByTestId(`template-doc-section-${i}`)).toBeInTheDocument();
    }
    expect(screen.getByText("核心原理与执行流程")).toBeInTheDocument();
    expect(screen.getByText("与相关方案对比")).toBeInTheDocument();
  });

  it("TC-AMP-06 回调未注入 → 「应用模板」降级提示（不写画布，T16.6）", async () => {
    render(<AgentMarketPanel />);
    await screen.findByTestId("agent-market-table");

    fireEvent.click(screen.getByTestId("agent-market-entry-e1"));
    fireEvent.click(await screen.findByTestId("agent-market-apply-button"));

    expect(getAgentTemplate).toHaveBeenCalledWith("basic_react");
    const hint = await screen.findByTestId("agent-market-apply-hint");
    expect(hint).toHaveTextContent("应用回调未注入");
  });

  it("TC-AMP-07 回调已注入 → 「应用模板」走 onApplyTemplate 通道", async () => {
    const onApplyTemplate = vi.fn();
    render(<AgentMarketPanel onApplyTemplate={onApplyTemplate} />);
    await screen.findByTestId("agent-market-table");

    fireEvent.click(screen.getByTestId("agent-market-entry-e1"));
    fireEvent.click(await screen.findByTestId("agent-market-apply-button"));

    await waitFor(() => {
      expect(onApplyTemplate).toHaveBeenCalledTimes(1);
    });
    const applied = onApplyTemplate.mock.calls[0]?.[0] as AgentTemplate;
    expect(applied.template_id).toBe("basic_react");
    const hint = await screen.findByTestId("agent-market-apply-hint");
    expect(hint).toHaveTextContent("模板已提交应用到画布");
  });
});
