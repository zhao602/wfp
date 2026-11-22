// =============================================================================
// Agent 模板选择列表组件测试（web/src/canvas/__tests__/AgentTemplateSelectionList.test.tsx）
//
// 执行：cd web && npx vitest run src/canvas/__tests__/AgentTemplateSelectionList.test.tsx
//
// 覆盖 per_node_agent_template T1.6 用例组（DC-01/DC-04/DC-05/DC-06）：
//   TC-NTASL-01 清单渲染（仅 3 个 single_node 卡片 + recipe 只读引导行）
//   TC-NTASL-02 首屏非阻塞（挂载即渲染 loading 态）
//   TC-NTASL-03 单击应用参数（E-6 详情 → onApplyTemplate 收到完整模板）
//   TC-NTASL-04 应用成功反馈（applied=true + reason 越界键说明）
//   TC-NTASL-05 清单失败降级（reject → 只读提示块、无未捕获异常）
//   TC-NTASL-06 开关关闭 404 降级（客户端 !ok 抛错 → 同 05 只读态）
//   TC-NTASL-07 空清单（total=0 → 空态提示）
//   TC-NTASL-08 详情失败不改配置（E-6 reject → 失败反馈、onApplyTemplate 未调用）
//   TC-NTASL-09 卸载丢弃迟到响应
//   TC-NTASL-10 recipe 不可应用（引导行不触发 E-6 / 应用）
//   TC-NTASL-11 来源标注（B5 批次补齐，T5.3）
// =============================================================================

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import type { AgentTemplate, AgentTemplateKind } from "../../market/marketTypes";
import type { listAgentTemplates } from "../../market/marketApiClient";
import type { ApplyTemplateResult } from "../applyAgentTemplate";
import type { WorkflowCanvasState } from "../canvasTypes";

// marketApiClient mock：vi.hoisted 保证工厂引用在模块求值前完成初始化
// （对齐 ModelSelectionList.test.tsx:17-25 范式）
const { listAgentTemplatesMock, getAgentTemplateMock } = vi.hoisted(() => ({
  listAgentTemplatesMock: vi.fn(),
  getAgentTemplateMock: vi.fn(),
}));
vi.mock("../../market/marketApiClient", () => ({
  listAgentTemplates: () => listAgentTemplatesMock(),
  getAgentTemplate: (templateId: string) => getAgentTemplateMock(templateId),
}));

import AgentTemplateSelectionList from "../AgentTemplateSelectionList";

/** E-5 响应类型（自真实客户端推导，类型安全） */
type ListTemplatesResponse = Awaited<ReturnType<typeof listAgentTemplates>>;

/** 样例模板分布（对齐后端内置 5 模板：3 single_node + 2 recipe） */
const SINGLE_NODE_ITEMS: ReadonlyArray<{
  template_id: string;
  name: string;
  kind: AgentTemplateKind;
}> = [
  { template_id: "plan_and_execute", name: "Plan-and-Execute", kind: "single_node" },
  { template_id: "basic_react", name: "Basic ReAct", kind: "single_node" },
  { template_id: "reflexive_agent", name: "Reflexive Agent", kind: "single_node" },
];

const RECIPE_ITEMS: ReadonlyArray<{
  template_id: string;
  name: string;
  kind: AgentTemplateKind;
}> = [
  { template_id: "multi_agent", name: "Multi-Agent", kind: "recipe" },
  { template_id: "autonomous_loop", name: "Autonomous Loop", kind: "recipe" },
];

/** 组装 E-5 清单响应体 */
function listResponse(
  items: ReadonlyArray<{ template_id: string; name: string; kind: AgentTemplateKind }>,
): ListTemplatesResponse {
  return {
    templates: items.map((item) => ({
      template_id: item.template_id,
      name: item.name,
      kind: item.kind,
      max_tool_rounds: null,
      allowed_tools: [],
    })),
    total: items.length,
  };
}

/** 组装 E-6 完整模板详情（含 preset） */
function templateDetail(
  templateId: string,
  name: string,
  preset: Record<string, unknown> = {},
): AgentTemplate {
  return {
    template_id: templateId,
    name,
    kind: "single_node",
    preset,
    system_prompt: "",
    allowed_tools: [],
    max_tool_rounds: null,
    output_max_retries: null,
    doc: {
      core_principle_flow: "",
      application_features: "",
      applicable_scenarios: "",
      advantages_limitations: "",
      comparison: "",
    },
    tool_config: {
      tool_types: [],
      invocation_mode: "react_loop",
      parameter_constraints: "",
      retry_and_exception_policy: "",
    },
    implementation_key_points: [],
    dependency_conditions: [],
    performance_boundaries: {
      max_tool_rounds: null,
      tool_timeout_seconds: 30,
      output_max_retries: null,
    },
    security_boundaries: [],
    evaluation_metrics: [],
    recipe: null,
  };
}

/** 组装应用结果（默认 applied=true、合并目标 agent-1） */
function applyResult(options: { applied?: boolean; reason?: string } = {}): ApplyTemplateResult {
  return {
    canvasState: { nodes: [], edges: [] } satisfies WorkflowCanvasState,
    applied: options.applied ?? true,
    createdNodeIds: [],
    replacedNodeId: "agent-1",
    ...(options.reason === undefined ? {} : { reason: options.reason }),
  };
}

beforeEach(() => {
  listAgentTemplatesMock.mockReset();
  getAgentTemplateMock.mockReset();
});

describe("TC-NTASL-01 清单渲染（AC-NT-06/07/17）", () => {
  it("E-5 返回 3 single_node + 2 recipe → 仅渲染 3 张卡片 + recipe 只读引导行", async () => {
    listAgentTemplatesMock.mockResolvedValue(
      listResponse([...SINGLE_NODE_ITEMS, ...RECIPE_ITEMS]),
    );
    render(
      <AgentTemplateSelectionList targetNodeId="agent-1" onApplyTemplate={vi.fn()} />,
    );
    await waitFor(() => {
      expect(screen.getByTitle("Plan-and-Execute")).toBeTruthy();
    });
    expect(screen.getByTitle("Basic ReAct")).toBeTruthy();
    expect(screen.getByTitle("Reflexive Agent")).toBeTruthy();
    expect(
      screen.getByText(
        "另有 2 个多节点配方模板，会产生多个节点与连线，请在 Agent 市场面板应用",
      ),
    ).toBeTruthy();
    expect(screen.queryByTitle("Multi-Agent")).toBeNull();
    expect(screen.queryByTitle("Autonomous Loop")).toBeNull();
  });
});

describe("TC-NTASL-02 首屏非阻塞（DFX §4.1.2）", () => {
  it("请求未返回时同步渲染加载提示，不阻塞首屏", () => {
    listAgentTemplatesMock.mockReturnValue(new Promise(() => {}));
    render(
      <AgentTemplateSelectionList targetNodeId="agent-1" onApplyTemplate={vi.fn()} />,
    );
    expect(screen.getByText("模板清单加载中…")).toBeTruthy();
  });
});

describe("TC-NTASL-03 单击应用参数（AC-NT-01/02/28）", () => {
  it("单击卡片 → E-6 取详情 → onApplyTemplate 收到该完整模板", async () => {
    listAgentTemplatesMock.mockResolvedValue(
      listResponse([...SINGLE_NODE_ITEMS, ...RECIPE_ITEMS]),
    );
    const detail = templateDetail("basic_react", "Basic ReAct", {
      system_prompt: "You are a helpful assistant.",
    });
    getAgentTemplateMock.mockResolvedValue(detail);
    const onApplyTemplate = vi.fn(() => applyResult());
    render(
      <AgentTemplateSelectionList
        targetNodeId="agent-1"
        onApplyTemplate={onApplyTemplate}
      />,
    );
    await waitFor(() => {
      expect(screen.getByTitle("Basic ReAct")).toBeTruthy();
    });
    fireEvent.click(screen.getByTitle("Basic ReAct"));
    await waitFor(() => {
      expect(onApplyTemplate).toHaveBeenCalledTimes(1);
    });
    expect(getAgentTemplateMock).toHaveBeenCalledWith("basic_react");
    expect(onApplyTemplate).toHaveBeenCalledWith(detail);
  });

  it("targetNodeId 为空串 → 拒绝应用（不触发 E-6 / onApplyTemplate，spec §5.9.3）", async () => {
    listAgentTemplatesMock.mockResolvedValue(listResponse(SINGLE_NODE_ITEMS));
    const onApplyTemplate = vi.fn();
    render(
      <AgentTemplateSelectionList targetNodeId="" onApplyTemplate={onApplyTemplate} />,
    );
    await waitFor(() => {
      expect(screen.getByTitle("Basic ReAct")).toBeTruthy();
    });
    fireEvent.click(screen.getByTitle("Basic ReAct"));
    await waitFor(() => {
      expect(screen.getByText("应用失败：目标节点不可用")).toBeTruthy();
    });
    expect(getAgentTemplateMock).not.toHaveBeenCalled();
    expect(onApplyTemplate).not.toHaveBeenCalled();
  });
});

describe("TC-NTASL-04 应用成功反馈（AC-NT-10/12）", () => {
  it("applied=true 且 reason 非空 → 成功提示 + 越界键说明", async () => {
    listAgentTemplatesMock.mockResolvedValue(listResponse(SINGLE_NODE_ITEMS));
    getAgentTemplateMock.mockResolvedValue(templateDetail("basic_react", "Basic ReAct"));
    const onApplyTemplate = vi.fn(() =>
      applyResult({ reason: "预设越界键已过滤：sandbox_policy_id" }),
    );
    render(
      <AgentTemplateSelectionList
        targetNodeId="agent-1"
        onApplyTemplate={onApplyTemplate}
      />,
    );
    await waitFor(() => {
      expect(screen.getByTitle("Basic ReAct")).toBeTruthy();
    });
    fireEvent.click(screen.getByTitle("Basic ReAct"));
    await waitFor(() => {
      expect(
        screen.getByText(
          "已应用「Basic ReAct」到当前节点；预设越界键已过滤：sandbox_policy_id",
        ),
      ).toBeTruthy();
    });
  });

  it("applied=true 且无 reason → 仅成功提示", async () => {
    listAgentTemplatesMock.mockResolvedValue(listResponse(SINGLE_NODE_ITEMS));
    getAgentTemplateMock.mockResolvedValue(templateDetail("basic_react", "Basic ReAct"));
    const onApplyTemplate = vi.fn(() => applyResult());
    render(
      <AgentTemplateSelectionList
        targetNodeId="agent-1"
        onApplyTemplate={onApplyTemplate}
      />,
    );
    await waitFor(() => {
      expect(screen.getByTitle("Basic ReAct")).toBeTruthy();
    });
    fireEvent.click(screen.getByTitle("Basic ReAct"));
    await waitFor(() => {
      expect(screen.getByText("已应用「Basic ReAct」到当前节点")).toBeTruthy();
    });
  });
});

describe("TC-NTASL-05 清单失败降级（AC-NT-31、§5.10.3-1）", () => {
  it("E-5 reject → 只读提示块、不渲染卡片、无未捕获异常", async () => {
    listAgentTemplatesMock.mockRejectedValue(new Error("network down"));
    const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <AgentTemplateSelectionList targetNodeId="agent-1" onApplyTemplate={vi.fn()} />,
    );
    await waitFor(() => {
      expect(
        screen.getByText("未能获取 Agent 模板清单（模板服务可能未启用），可稍后重试"),
      ).toBeTruthy();
    });
    expect(screen.queryByTitle("Plan-and-Execute")).toBeNull();
    expect(consoleSpy).not.toHaveBeenCalled();
    consoleSpy.mockRestore();
  });
});

describe("TC-NTASL-06 开关关闭（404）降级（§5.10.3-1/AC-NT-33）", () => {
  it("E-5 非 2xx（agent_market_enabled=False 不注册路由 → 客户端 !ok 抛错）→ 同 05 只读态", async () => {
    listAgentTemplatesMock.mockRejectedValue(new Error("查询模板清单失败"));
    const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <AgentTemplateSelectionList targetNodeId="agent-1" onApplyTemplate={vi.fn()} />,
    );
    await waitFor(() => {
      expect(
        screen.getByText("未能获取 Agent 模板清单（模板服务可能未启用），可稍后重试"),
      ).toBeTruthy();
    });
    expect(consoleSpy).not.toHaveBeenCalled();
    consoleSpy.mockRestore();
  });
});

describe("TC-NTASL-07 空清单（AC-NT-06 异常场景 4）", () => {
  it("total=0 → 当前无可用 Agent 模板，不渲染卡片", async () => {
    listAgentTemplatesMock.mockResolvedValue(listResponse([]));
    const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <AgentTemplateSelectionList targetNodeId="agent-1" onApplyTemplate={vi.fn()} />,
    );
    await waitFor(() => {
      expect(screen.getByText("当前无可用 Agent 模板")).toBeTruthy();
    });
    expect(screen.queryByTitle("Plan-and-Execute")).toBeNull();
    expect(consoleSpy).not.toHaveBeenCalled();
    consoleSpy.mockRestore();
  });
});

describe("TC-NTASL-08 详情失败不改配置（DFX §4.2.3）", () => {
  it("E-6 reject → 应用失败提示，onApplyTemplate 未被调用（父级写回未触发）", async () => {
    listAgentTemplatesMock.mockResolvedValue(listResponse(SINGLE_NODE_ITEMS));
    getAgentTemplateMock.mockRejectedValue(new Error("detail down"));
    const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const onApplyTemplate = vi.fn();
    render(
      <AgentTemplateSelectionList
        targetNodeId="agent-1"
        onApplyTemplate={onApplyTemplate}
      />,
    );
    await waitFor(() => {
      expect(screen.getByTitle("Basic ReAct")).toBeTruthy();
    });
    fireEvent.click(screen.getByTitle("Basic ReAct"));
    await waitFor(() => {
      expect(screen.getByText("应用失败：模板详情加载失败")).toBeTruthy();
    });
    expect(onApplyTemplate).not.toHaveBeenCalled();
    expect(consoleSpy).not.toHaveBeenCalled();
    consoleSpy.mockRestore();
  });
});

describe("TC-NTASL-09 卸载丢弃迟到响应（DFX §4.3.1，承 TC-F9）", () => {
  it("卸载后迟到 resolve 不产生状态更新或控制台错误", async () => {
    let resolveLate: ((res: ListTemplatesResponse) => void) | undefined;
    listAgentTemplatesMock.mockImplementation(
      () =>
        new Promise<ListTemplatesResponse>((resolve) => {
          resolveLate = resolve;
        }),
    );
    const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const { unmount } = render(
      <AgentTemplateSelectionList targetNodeId="agent-1" onApplyTemplate={vi.fn()} />,
    );
    unmount();
    resolveLate?.(listResponse(SINGLE_NODE_ITEMS));
    await Promise.resolve();
    await Promise.resolve();
    expect(consoleSpy).not.toHaveBeenCalled();
    consoleSpy.mockRestore();
  });
});

describe("TC-NTASL-10 recipe 不可应用（AC-NT-17/18）", () => {
  it("点击 recipe 引导行 → getAgentTemplate 与 onApplyTemplate 调用数均为 0", async () => {
    listAgentTemplatesMock.mockResolvedValue(
      listResponse([...SINGLE_NODE_ITEMS, ...RECIPE_ITEMS]),
    );
    const onApplyTemplate = vi.fn();
    const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <AgentTemplateSelectionList
        targetNodeId="agent-1"
        onApplyTemplate={onApplyTemplate}
      />,
    );
    const guidanceText =
      "另有 2 个多节点配方模板，会产生多个节点与连线，请在 Agent 市场面板应用";
    await waitFor(() => {
      expect(screen.getByText(guidanceText)).toBeTruthy();
    });
    fireEvent.click(screen.getByText(guidanceText));
    expect(getAgentTemplateMock).not.toHaveBeenCalled();
    expect(onApplyTemplate).not.toHaveBeenCalled();
    expect(consoleSpy).not.toHaveBeenCalled();
    consoleSpy.mockRestore();
  });
});

describe("TC-NTASL-11 来源标注（AC-NT-22/24，T5.2/T5.3）", () => {
  it("传入 appliedTemplateName → 渲染只读来源行；未传 / 空串 → 不渲染", async () => {
    listAgentTemplatesMock.mockResolvedValue(listResponse(SINGLE_NODE_ITEMS));
    const { rerender } = render(
      <AgentTemplateSelectionList targetNodeId="agent-1" onApplyTemplate={vi.fn()} />,
    );
    await waitFor(() => {
      expect(screen.getByTitle("Basic ReAct")).toBeTruthy();
    });
    expect(screen.queryByText(/当前节点最近应用的模板：/)).toBeNull();
    rerender(
      <AgentTemplateSelectionList
        targetNodeId="agent-1"
        onApplyTemplate={vi.fn()}
        appliedTemplateName="Basic ReAct"
      />,
    );
    expect(screen.getByText("当前节点最近应用的模板：Basic ReAct")).toBeTruthy();
    rerender(
      <AgentTemplateSelectionList
        targetNodeId="agent-1"
        onApplyTemplate={vi.fn()}
        appliedTemplateName=""
      />,
    );
    expect(screen.queryByText(/当前节点最近应用的模板：/)).toBeNull();
  });
});
