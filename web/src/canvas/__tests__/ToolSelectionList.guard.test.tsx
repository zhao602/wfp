// =============================================================================
// 工具勾选列表空值守卫归因测试（canvas/__tests__/ToolSelectionList.guard.test.tsx）
//
// 执行：cd web && npx vitest run src/canvas/__tests__/ToolSelectionList.guard.test.tsx
//
// fix_agent_null_length CHG-17 / DC-01 / DC-09（P1 归因 → P2 转绿成对留痕）：
//   E1-a available_tools = null（自备 YAML `available_tools:` 空值）→ 修复前渲染抛
//       TypeError: Cannot read properties of null (reading 'length')（与用户报告
//       逐字一致，经 NodeConfigurationPanel 既有渲染入口复现）；修复后渲染成功，
//       摘要显示「已选 0 项」（AC-01）
//   E1-b available_tools 键整体缺失（undefined 变体，范例 YAML 缺键形态）→
//       同族报错主体为 undefined；修复后同样渲染「已选 0 项」（AC-02）
//   E1-c 合法数组（含清单外失效标识）→ 渲染与修复前完全一致（INV-02 零行为回归，
//       AC-03）：摘要计数 / 分组标题 / 勾选态 / 失效标记基线形态锁定
// =============================================================================

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import type { ToolCatalogItem } from "../../api/orchestrationApiClient";

// listTools mock：vi.hoisted 保证工厂引用在模块求值前完成初始化；
// 其余方法（listAvailableModels 等）保留真实实现——jsdom 下 fetch 失败由组件优雅降级
// （与 hints.test.tsx 不 mock API 的既有范式一致）
const { listToolsMock } = vi.hoisted(() => ({ listToolsMock: vi.fn() }));
vi.mock("../../api/orchestrationApiClient", async () => {
  const actual = await vi.importActual<typeof import("../../api/orchestrationApiClient")>(
    "../../api/orchestrationApiClient",
  );
  return {
    ...actual,
    orchestrationApiClient: {
      ...actual.orchestrationApiClient,
      listTools: () => listToolsMock(),
    },
  };
});

import NodeConfigurationPanel from "../NodeConfigurationPanel";
import type { CanvasNodeData, WorkflowCanvasState } from "../canvasTypes";
import type { NodeConfiguration } from "../../workflow-definition/definitionTypes";

// ---------------------------------------------------------------------------
// 测试夹具
// ---------------------------------------------------------------------------

/** 三源样例清单：平台 / MCP / 本地 各一项 */
const CATALOG: ToolCatalogItem[] = [
  {
    tool_identifier: "validate_workflow",
    name: "校验工作流",
    description: "校验工作流定义合法性",
    source: "platform",
    permission: "readonly",
    version: "1.0.0",
  },
  {
    tool_identifier: "weather_query",
    name: "天气查询",
    description: "查询指定城市天气",
    source: "mcp",
    permission: "readwrite",
    version: "1.2.0",
  },
  {
    tool_identifier: "report_builder",
    name: "报告生成",
    description: "生成分析报告",
    source: "local",
    permission: "readonly",
    version: "1.0.0",
  },
];

/** 构造 agent 配置（available_tools 形态由调用方指定，容纳 null/合法数组实验形态） */
function agentConfig(availableTools: unknown): NodeConfiguration {
  return {
    node_type: "agent",
    model_identifier: "model-1",
    system_prompt: "提示词",
    available_tools: availableTools,
  } as unknown as NodeConfiguration;
}

/** 构造 agent 配置（键整体缺失变体——available_tools 键不写入） */
function agentConfigMissingKey(): NodeConfiguration {
  return {
    node_type: "agent",
    model_identifier: "model-1",
    system_prompt: "提示词",
  } as unknown as NodeConfiguration;
}

/** 构造面板节点数据 */
function makeNode(configuration: NodeConfiguration): CanvasNodeData {
  return {
    node_id: `node-${configuration.node_type}`,
    name: `节点-${configuration.node_type}`,
    node_type: configuration.node_type,
    configuration,
  };
}

/** 经既有渲染入口挂载 NodeConfigurationPanel（复用 hints.test.tsx 工厂形态） */
function renderPanel(node: CanvasNodeData) {
  const canvasState: WorkflowCanvasState = {
    nodes: [
      { id: node.node_id, type: node.node_type, position: { x: 0, y: 0 }, data: node },
    ],
    edges: [],
  };
  return render(
    <NodeConfigurationPanel
      selectedNode={node}
      canvasState={canvasState}
      onCanvasStateChange={vi.fn()}
    />,
  );
}

beforeEach(() => {
  listToolsMock.mockReset();
  listToolsMock.mockResolvedValue({ tools: CATALOG, total: CATALOG.length });
});

// ---------------------------------------------------------------------------
// E1-a / E1-b：null 与 undefined 双态崩溃消除（AC-01 / AC-02）
// ---------------------------------------------------------------------------

describe("ToolSelectionList 空值守卫归因（fix_agent_null_length E1）", () => {
  it("E1-a available_tools=null：渲染不崩溃，摘要显示「已选 0 项」（AC-01）", async () => {
    renderPanel(makeNode(agentConfig(null)));
    // 修复前：首帧渲染即抛 TypeError: Cannot read properties of null (reading 'length')
    // （ToolSelectionList.tsx 摘要 value.length，与用户报告逐字一致——P1 归因留痕）
    expect(screen.getByText("已选 0 项")).toBeTruthy();
    // ready 态访问点（invalidIds value.filter / value.includes）在 null 态下同样不崩
    await waitFor(() => {
      expect(screen.getByText("平台工具")).toBeTruthy();
    });
    expect(screen.getByText("MCP 工具")).toBeTruthy();
    expect(screen.getByText("本地工具")).toBeTruthy();
  });

  it("E1-b available_tools 键缺失（undefined）：同口径渲染「已选 0 项」（AC-02）", async () => {
    renderPanel(makeNode(agentConfigMissingKey()));
    expect(screen.getByText("已选 0 项")).toBeTruthy();
    await waitFor(() => {
      expect(screen.getByText("平台工具")).toBeTruthy();
    });
  });

  it("E1-c 合法数组：渲染与修复前一致——摘要计数/分组/勾选态/失效标记（AC-03 零回归）", async () => {
    renderPanel(makeNode(agentConfig(["report_builder", "weather_query", "ghost_tool"])));
    // 摘要计数基形
    expect(screen.getByText("已选 3 项")).toBeTruthy();
    await waitFor(() => {
      expect(screen.getByText("报告生成")).toBeTruthy();
    });
    // 分组勾选态：value 内两项已勾选，清单内未选项未勾选
    const reportBox = within(screen.getByTitle("报告生成（report_builder）：生成分析报告")).getByRole("checkbox");
    const weatherBox = within(screen.getByTitle("天气查询（weather_query）：查询指定城市天气")).getByRole("checkbox");
    const validateBox = within(screen.getByTitle("校验工作流（validate_workflow）：校验工作流定义合法性")).getByRole("checkbox");
    expect(reportBox).toBeChecked();
    expect(weatherBox).toBeChecked();
    expect(validateBox).not.toBeChecked();
    // 失效标记（AC-4 基形）：清单外标识 ghost_tool 渲染「已失效」行
    expect(screen.getByText("已失效")).toBeTruthy();
    // 交互回调（toggle/onChange）合法路径行为由既有 ToolSelectionList.test.tsx ①~④
    // 全量覆盖（受控组件契约零改动），本用例锁定渲染基形即可（INV-02 零回归口径）
  });
});
