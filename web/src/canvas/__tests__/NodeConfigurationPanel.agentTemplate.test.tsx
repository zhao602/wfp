// =============================================================================
// 节点配置面板 Agent 模板集成测试（web/src/canvas/__tests__/NodeConfigurationPanel.agentTemplate.test.tsx）
//
// 执行：cd web && npx vitest run src/canvas/__tests__/NodeConfigurationPanel.agentTemplate.test.tsx
//
// 覆盖 per_node_agent_template T3.4 用例组（DC-02/DC-03/DC-08，design §2.7 P3 调用链接入）：
//   TC-NTINT-01 插入点（「Agent 模板」在模型标识之前，既有 5 字段仍在且行为不变）
//   TC-NTINT-02 应用后节点配置变化（预设合并、未声明字段保持、available_tools 去重保序、越界键过滤）
//   TC-NTINT-03 多节点隔离（仅目标节点变化，非目标节点引用不变）
//   TC-NTINT-04 重复应用（同名键为 T2、T1 独有键保留）
//   TC-NTINT-05 模型优先级（模板覆盖模型标识 → ModelSelectionList 改选后等于新选值）
//   TC-NTINT-06 无 onCanvasStateChange（向后兼容：控件不渲染、面板正常）
//   TC-NTINT-07 入参不可变（canvasState/template 深冻结下应用无原地修改）
//   TC-NTINT-08 无写请求（交互全过程仅 E-5/E-6 只读命中，无任何真实 fetch/写方法）
//
// 测试策略：面板级集成——真实 NodeConfigurationPanel + AgentConfigForm + Field +
// AgentTemplateSelectionList + applyAgentTemplateToCanvas 纯函数链路；仅以 vi.mock
// 隔离网络边界（E-5/E-6 marketApiClient、listAvailableModels/listTools）与主题；
// 以受控驱动器承接 onCanvasStateChange（对齐 atomic 测试 createDriver 范式）。
// =============================================================================

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import type { ReactNode, SetStateAction } from "react";

// 主题 mock：通过外部变量切换浅色/深色，保留真实 darkTokens 供 <style> 插值
let mockTheme: "light" | "dark" = "light";
vi.mock("../../theme/themeMode", async () => {
  const actual = await vi.importActual<typeof import("../../theme/themeMode")>(
    "../../theme/themeMode",
  );
  return { ...actual, useThemeMode: () => mockTheme };
});

// marketApiClient mock：E-5/E-6 只读端点（vi.hoisted 保证工厂引用在模块求值前初始化）
const { listAgentTemplatesMock, getAgentTemplateMock } = vi.hoisted(() => ({
  listAgentTemplatesMock: vi.fn(),
  getAgentTemplateMock: vi.fn(),
}));
vi.mock("../../market/marketApiClient", () => ({
  listAgentTemplates: () => listAgentTemplatesMock(),
  getAgentTemplate: (templateId: string) => getAgentTemplateMock(templateId),
}));

// orchestrationApiClient mock：importActual 保留 getTenantId/ApiException 等其余导出，
// 仅替换 agent 渲染路径会用到的两个只读方法（ModelSelectionList / ToolSelectionList）
const { listAvailableModelsMock, listToolsMock } = vi.hoisted(() => ({
  listAvailableModelsMock: vi.fn(),
  listToolsMock: vi.fn(),
}));
vi.mock("../../api/orchestrationApiClient", async () => {
  const actual = await vi.importActual<typeof import("../../api/orchestrationApiClient")>(
    "../../api/orchestrationApiClient",
  );
  return {
    ...actual,
    orchestrationApiClient: {
      ...actual.orchestrationApiClient,
      listAvailableModels: () => listAvailableModelsMock(),
      listTools: () => listToolsMock(),
    },
  };
});

import NodeConfigurationPanel from "../NodeConfigurationPanel";
import type { CanvasNodeData, CanvasNode, WorkflowCanvasState } from "../canvasTypes";
import type { AgentNodeConfiguration } from "../../workflow-definition/definitionTypes";
import type { AgentTemplate, AgentTemplateKind } from "../../market/marketTypes";
import type { listAgentTemplates } from "../../market/marketApiClient";
import type { AvailableModelItem } from "../../api/apiTypes";
import type { ToolCatalogItem } from "../../api/orchestrationApiClient";

/** E-5 响应类型（自真实客户端推导，类型安全） */
type ListTemplatesResponse = Awaited<ReturnType<typeof listAgentTemplates>>;

/** 受控更新形态 */
type StateUpdate = SetStateAction<WorkflowCanvasState>;

/** 样例 single_node 模板清单条目（3 个） */
const SINGLE_NODE_ITEMS: ReadonlyArray<{
  template_id: string;
  name: string;
  kind: AgentTemplateKind;
}> = [
  { template_id: "plan_and_execute", name: "Plan-and-Execute", kind: "single_node" },
  { template_id: "basic_react", name: "Basic ReAct", kind: "single_node" },
  { template_id: "reflexive_agent", name: "Reflexive Agent", kind: "single_node" },
];

/** 样例模型候选（主 / 备 各一项） */
const MODELS: AvailableModelItem[] = [
  { model_identifier: "gpt-primary", source: "primary" },
  { model_identifier: "gpt-fallback", source: "fallback" },
];

/** 样例工具清单（覆盖节点与模板会用到的 t0/t1/t2） */
const TOOLS: ToolCatalogItem[] = [
  { tool_identifier: "t0", name: "工具0", description: "", source: "platform", permission: "default", version: "1.0.0" },
  { tool_identifier: "t1", name: "工具1", description: "", source: "platform", permission: "default", version: "1.0.0" },
  { tool_identifier: "t2", name: "工具2", description: "", source: "platform", permission: "default", version: "1.0.0" },
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

/** 构造 agent 节点数据（默认配置可局部覆写） */
function makeAgentNode(
  nodeId: string,
  overrides: Partial<AgentNodeConfiguration> = {},
): CanvasNodeData {
  const configuration: AgentNodeConfiguration = {
    node_type: "agent",
    model_identifier: "old-model",
    system_prompt: "原始提示词",
    available_tools: ["t0"],
    ...overrides,
  };
  return { node_id: nodeId, name: nodeId, node_type: "agent", configuration };
}

/** 构造画布节点 */
function makeCanvasNode(data: CanvasNodeData): CanvasNode {
  return { id: data.node_id, type: data.node_type, position: { x: 0, y: 0 }, data };
}

/** 构造画布状态 */
function makeCanvasState(nodes: readonly CanvasNodeData[]): WorkflowCanvasState {
  return { nodes: nodes.map(makeCanvasNode), edges: [] };
}

/** 受控驱动器：承接 onCanvasStateChange 的函数式/直赋值更新（atomic 范式） */
function createDriver(initial: WorkflowCanvasState) {
  let state = initial;
  const onStateChange = vi.fn((update: StateUpdate) => {
    state =
      typeof update === "function"
        ? (update as (prev: WorkflowCanvasState) => WorkflowCanvasState)(state)
        : update;
  });
  return { onStateChange, getState: (): WorkflowCanvasState => state };
}

/** 渲染面板（受控驱动器），并提供以驱动器最新状态受控重渲染的助手 */
function renderPanel(selected: CanvasNodeData, canvasState: WorkflowCanvasState) {
  const driver = createDriver(canvasState);
  const view = render(
    <NodeConfigurationPanel
      selectedNode={selected}
      canvasState={canvasState}
      onCanvasStateChange={driver.onStateChange}
    /> as ReactNode,
  );
  /** 以驱动器最新状态受控重渲染（选中节点取最新画布中的对应数据，模拟父层更新） */
  const rerenderSelected = (nodeId: string): void => {
    const latest = driver.getState();
    const node = latest.nodes.find((n) => n.id === nodeId);
    if (node === undefined) {
      throw new Error(`rerenderSelected：画布中未找到节点 ${nodeId}`);
    }
    view.rerender(
      <NodeConfigurationPanel
        selectedNode={node.data}
        canvasState={latest}
        onCanvasStateChange={driver.onStateChange}
      /> as ReactNode,
    );
  };
  return { driver, rerenderSelected };
}

/** 深冻结（TC-NTINT-07）：严格模式下原地修改冻结对象会抛 TypeError */
function deepFreeze(value: unknown): void {
  if (value !== null && typeof value === "object") {
    Object.freeze(value);
    for (const key of Object.keys(value)) {
      deepFreeze((value as Record<string, unknown>)[key]);
    }
  }
}

beforeEach(() => {
  mockTheme = "light";
  listAgentTemplatesMock.mockReset();
  getAgentTemplateMock.mockReset();
  listAvailableModelsMock.mockReset();
  listToolsMock.mockReset();
  listAgentTemplatesMock.mockResolvedValue(listResponse(SINGLE_NODE_ITEMS));
  listAvailableModelsMock.mockResolvedValue({ models: MODELS, total: MODELS.length });
  listToolsMock.mockResolvedValue({ tools: TOOLS, total: TOOLS.length });
});

describe("NodeConfigurationPanel Agent 模板集成（P3 调用链，T3.4）", () => {
  it("TC-NTINT-01 插入点：「Agent 模板」在模型标识之前，既有 5 字段仍在且行为不变（AC-NT-28/29）", async () => {
    const node = makeAgentNode("agent-1");
    const { driver } = renderPanel(node, makeCanvasState([node]));

    // 模板清单就绪（控件完整挂载）
    await waitFor(() => {
      expect(screen.getByTitle("Plan-and-Execute")).toBeTruthy();
    });

    // 既有 5 字段标签仍在，顺序：Agent 模板 → 模型标识 → 系统提示词 → 可用工具 → 输入引用 → 输出约束
    expect(screen.getByText("Agent 模板")).toBeTruthy();
    expect(screen.getByText("模型标识")).toBeTruthy();
    expect(screen.getByText("系统提示词")).toBeTruthy();
    expect(screen.getByText("可用工具")).toBeTruthy();
    expect(screen.getByText("输入引用")).toBeTruthy();
    expect(screen.getByText("输出约束")).toBeTruthy();
    expect(
      screen.getByText("Agent 模板").compareDocumentPosition(screen.getByText("模型标识")) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).not.toBe(0);

    // 既有字段行为不变：system_prompt textarea 仍受控逐字符回写
    fireEvent.change(screen.getByDisplayValue("原始提示词"), {
      target: { value: "手改提示词" },
    });
    const cfg = driver.getState().nodes[0].data.configuration as AgentNodeConfiguration;
    expect(cfg.system_prompt).toBe("手改提示词");
  });

  it("TC-NTINT-02 应用后节点配置变化：预设合并、未声明字段保持、available_tools 去重保序（AC-NT-10/11）", async () => {
    const node = makeAgentNode("agent-1");
    const { driver } = renderPanel(node, makeCanvasState([node]));

    getAgentTemplateMock.mockResolvedValueOnce(
      templateDetail("basic_react", "Basic ReAct", {
        system_prompt: "模板提示词",
        available_tools: ["t1", "t0", "t2"],
        sandbox_policy_id: "policy-x",
      }),
    );
    await waitFor(() => {
      expect(screen.getByTitle("Basic ReAct")).toBeTruthy();
    });
    fireEvent.click(screen.getByTitle("Basic ReAct"));
    // 越界键被纯函数过滤 → reason 附加到成功反馈（T3.3 文案）
    await waitFor(() => {
      expect(
        screen.getByText("已应用「Basic ReAct」到当前节点；预设越界键已过滤：sandbox_policy_id"),
      ).toBeTruthy();
    });

    const next = driver.getState();
    expect(next.nodes).toHaveLength(1);
    const cfg = next.nodes[0].data.configuration as AgentNodeConfiguration;
    // 声明字段等于预设
    expect(cfg.system_prompt).toBe("模板提示词");
    // available_tools 去重保序（既有在前，重复 t0 跳过）
    expect(cfg.available_tools).toEqual(["t0", "t1", "t2"]);
    // 未声明字段保持原值
    expect(cfg.model_identifier).toBe("old-model");
    expect(cfg.node_type).toBe("agent");
    // 越界键不写入画布
    expect(Object.keys(cfg)).not.toContain("sandbox_policy_id");
  });

  it("TC-NTINT-03 多节点隔离：仅目标节点变化，非目标节点引用不变（AC-NT-01/05/14/16）", async () => {
    const n1 = makeAgentNode("N1");
    const n2 = makeAgentNode("N2");
    const n3 = makeAgentNode("N3");
    const initial = makeCanvasState([n1, n2, n3]);
    const n2Before = initial.nodes[1];
    const n3Before = initial.nodes[2];
    const { driver, rerenderSelected } = renderPanel(n1, initial);

    // 对 N1 应用 A → 仅 N1 变化，N2/N3 引用逐字段不变
    getAgentTemplateMock.mockResolvedValueOnce(
      templateDetail("plan_and_execute", "Plan-and-Execute", { system_prompt: "A-提示词" }),
    );
    await waitFor(() => {
      expect(screen.getByTitle("Plan-and-Execute")).toBeTruthy();
    });
    fireEvent.click(screen.getByTitle("Plan-and-Execute"));
    await waitFor(() => {
      expect(screen.getByText("已应用「Plan-and-Execute」到当前节点")).toBeTruthy();
    });

    let next = driver.getState();
    expect((next.nodes[0].data.configuration as AgentNodeConfiguration).system_prompt).toBe(
      "A-提示词",
    );
    expect(next.nodes[1]).toBe(n2Before);
    expect(next.nodes[2]).toBe(n3Before);
    expect(next.edges).toBe(initial.edges);

    // 切到 N2（受控重渲染）再应用 B：N1 不被波及、三节点互不相同
    rerenderSelected("N2");
    getAgentTemplateMock.mockResolvedValueOnce(
      templateDetail("basic_react", "Basic ReAct", { system_prompt: "B-提示词" }),
    );
    fireEvent.click(screen.getByTitle("Basic ReAct"));
    await waitFor(() => {
      expect(screen.getByText("已应用「Basic ReAct」到当前节点")).toBeTruthy();
    });

    next = driver.getState();
    expect((next.nodes[0].data.configuration as AgentNodeConfiguration).system_prompt).toBe(
      "A-提示词",
    );
    expect((next.nodes[1].data.configuration as AgentNodeConfiguration).system_prompt).toBe(
      "B-提示词",
    );
    expect((next.nodes[2].data.configuration as AgentNodeConfiguration).system_prompt).toBe(
      "原始提示词",
    );
    expect(next.nodes[2]).toBe(n3Before);
  });

  it("TC-NTINT-04 重复应用：同一节点先后应用 T1、T2 → 同名键为 T2、T1 独有键保留（AC-NT-15）", async () => {
    const node = makeAgentNode("agent-1");
    const { driver } = renderPanel(node, makeCanvasState([node]));

    getAgentTemplateMock.mockResolvedValueOnce(
      templateDetail("plan_and_execute", "Plan-and-Execute", {
        system_prompt: "T1-提示词",
        max_tool_rounds: 3,
      }),
    );
    await waitFor(() => {
      expect(screen.getByTitle("Plan-and-Execute")).toBeTruthy();
    });
    fireEvent.click(screen.getByTitle("Plan-and-Execute"));
    await waitFor(() => {
      expect(screen.getByText("已应用「Plan-and-Execute」到当前节点")).toBeTruthy();
    });

    getAgentTemplateMock.mockResolvedValueOnce(
      templateDetail("basic_react", "Basic ReAct", {
        system_prompt: "T2-提示词",
        model_identifier: "m2",
      }),
    );
    fireEvent.click(screen.getByTitle("Basic ReAct"));
    await waitFor(() => {
      expect(screen.getByText("已应用「Basic ReAct」到当前节点")).toBeTruthy();
    });

    const cfg = driver.getState().nodes[0].data.configuration as AgentNodeConfiguration;
    expect(cfg.system_prompt).toBe("T2-提示词");
    expect(cfg.max_tool_rounds).toBe(3);
    expect(cfg.model_identifier).toBe("m2");
  });

  it("TC-NTINT-05 模型优先级：模板覆盖模型标识，ModelSelectionList 改选后等于新选值（AC-NT-19/20）", async () => {
    const node = makeAgentNode("agent-1");
    const { driver, rerenderSelected } = renderPanel(node, makeCanvasState([node]));

    await waitFor(() => {
      expect(screen.getByTitle("gpt-primary")).toBeTruthy();
    });

    // 应用声明 model_identifier 的模板 → 节点等于模板值（同名键模板优先，DC-08）
    getAgentTemplateMock.mockResolvedValueOnce(
      templateDetail("basic_react", "Basic ReAct", { model_identifier: "tpl-model" }),
    );
    fireEvent.click(screen.getByTitle("Basic ReAct"));
    await waitFor(() => {
      expect(screen.getByText("已应用「Basic ReAct」到当前节点")).toBeTruthy();
    });
    let cfg = driver.getState().nodes[0].data.configuration as AgentNodeConfiguration;
    expect(cfg.model_identifier).toBe("tpl-model");

    // 父层受控更新后经 ModelSelectionList 改选 → 节点等于新选值
    rerenderSelected("agent-1");
    fireEvent.click(screen.getByTitle("gpt-fallback"));
    cfg = driver.getState().nodes[0].data.configuration as AgentNodeConfiguration;
    expect(cfg.model_identifier).toBe("gpt-fallback");
    expect(cfg.system_prompt).toBe("原始提示词");
  });

  it("TC-NTINT-06 无 onCanvasStateChange（向后兼容）：模板控件不渲染、面板正常（AC-NT-29）", () => {
    const node = makeAgentNode("agent-1");
    const canvasState = makeCanvasState([node]);
    render(
      // @ts-expect-error TC-NTINT-06：刻意省略必填 onCanvasStateChange（运行时 undefined），验证守卫降级
      <NodeConfigurationPanel selectedNode={node} canvasState={canvasState} /> as ReactNode,
    );

    // 模板控件未挂载：清单请求未发出、卡片与字段标签不存在
    expect(listAgentTemplatesMock).not.toHaveBeenCalled();
    expect(screen.queryByTitle("Plan-and-Execute")).toBeNull();
    expect(screen.queryByText("Agent 模板")).toBeNull();
    // 面板正常：既有 5 字段仍在
    expect(screen.getByText("模型标识")).toBeTruthy();
    expect(screen.getByText("系统提示词")).toBeTruthy();
    expect(screen.getByText("可用工具")).toBeTruthy();
    expect(screen.getByText("输入引用")).toBeTruthy();
    expect(screen.getByText("输出约束")).toBeTruthy();
  });

  it("TC-NTINT-07 入参不可变：应用前后 canvasState/template 对象不变（AC-NT-13）", async () => {
    const node = makeAgentNode("agent-1");
    const initial = makeCanvasState([node]);
    deepFreeze(initial);
    const detail = templateDetail("basic_react", "Basic ReAct", { system_prompt: "模板提示词" });
    deepFreeze(detail);

    const { driver } = renderPanel(node, initial);
    getAgentTemplateMock.mockResolvedValueOnce(detail);
    await waitFor(() => {
      expect(screen.getByTitle("Basic ReAct")).toBeTruthy();
    });
    fireEvent.click(screen.getByTitle("Basic ReAct"));
    await waitFor(() => {
      expect(screen.getByText("已应用「Basic ReAct」到当前节点")).toBeTruthy();
    });

    // 应用在副本上生效；冻结的入参对象不被原地修改（严格模式下原地修改会抛 TypeError）
    const cfg = driver.getState().nodes[0].data.configuration as AgentNodeConfiguration;
    expect(cfg.system_prompt).toBe("模板提示词");
    const original = initial.nodes[0].data.configuration as AgentNodeConfiguration;
    expect(original.system_prompt).toBe("原始提示词");
    expect(Object.isFrozen(initial.nodes[0].data.configuration)).toBe(true);
  });

  it("TC-NTINT-08 无写请求：交互全过程仅 E-5/E-6 只读命中，无任何写方法（AC-NT-08/31）", async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    try {
      const node = makeAgentNode("agent-1");
      const { driver } = renderPanel(node, makeCanvasState([node]));
      getAgentTemplateMock.mockResolvedValueOnce(
        templateDetail("basic_react", "Basic ReAct", { system_prompt: "模板提示词" }),
      );
      await waitFor(() => {
        expect(screen.getByTitle("Basic ReAct")).toBeTruthy();
      });
      fireEvent.click(screen.getByTitle("Basic ReAct"));
      await waitFor(() => {
        expect(screen.getByText("已应用「Basic ReAct」到当前节点")).toBeTruthy();
      });
      expect(
        (driver.getState().nodes[0].data.configuration as AgentNodeConfiguration).system_prompt,
      ).toBe("模板提示词");

      // 只读命中：E-5 清单一次、E-6 详情一次（GET）
      expect(listAgentTemplatesMock).toHaveBeenCalledTimes(1);
      expect(getAgentTemplateMock).toHaveBeenCalledTimes(1);
      expect(getAgentTemplateMock).toHaveBeenCalledWith("basic_react");
      // 无任何真实 fetch 发出（无任何写方法命中）
      expect(fetchSpy).not.toHaveBeenCalled();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("面板级来源标注（DC-07/T5.1/T5.2）：应用成功渲染来源行、不落盘、节点切换重置（AC-NT-22/24）", async () => {
    const n1 = makeAgentNode("N1");
    const n2 = makeAgentNode("N2");
    const { driver, rerenderSelected } = renderPanel(n1, makeCanvasState([n1, n2]));

    await waitFor(() => {
      expect(screen.getByTitle("Basic ReAct")).toBeTruthy();
    });
    // 未应用 → 无来源行
    expect(screen.queryByText(/当前节点最近应用的模板：/)).toBeNull();

    // 应用成功 → 来源行渲染（面板 useState 记忆）
    getAgentTemplateMock.mockResolvedValueOnce(
      templateDetail("basic_react", "Basic ReAct", { system_prompt: "B-提示词" }),
    );
    fireEvent.click(screen.getByTitle("Basic ReAct"));
    await waitFor(() => {
      expect(screen.getByText("已应用「Basic ReAct」到当前节点")).toBeTruthy();
    });
    expect(screen.getByText("当前节点最近应用的模板：Basic ReAct")).toBeTruthy();

    // 不落盘：节点字段无来源类新增键（INV-NTD-03，T5.1 验收）
    const cfg = driver.getState().nodes[0].data.configuration as AgentNodeConfiguration;
    expect(Object.keys(cfg).some((key) => key.includes("applied"))).toBe(false);

    // 切到 N2 → 来源行重置；切回 N1 → 记忆不复活
    rerenderSelected("N2");
    expect(screen.queryByText(/当前节点最近应用的模板：/)).toBeNull();
    rerenderSelected("N1");
    expect(screen.queryByText(/当前节点最近应用的模板：/)).toBeNull();
  });
});
