// =============================================================================
// 工具勾选列表组件测试（src/canvas/__tests__/ToolSelectionList.test.tsx）
//
// 执行：cd web && npx vitest run src/canvas/__tests__/ToolSelectionList.test.tsx
//
// 覆盖 merge_tool_into_agent_node T4.3 用例组（FR-2 / DC-B / DC-F）：
//   ① 清单加载成功 → 按来源分组渲染、勾选态与传入 value 一致、勾选/取消全量回写（AC-2）
//   ② 清单拉取失败 → 「工具清单加载失败」提示、value 不被清空、
//     既有绑定以「（已绑定）」灰底行保留且可取消勾选（AC-3）
//   ③ value 含清单外标识 → 「已失效」标记行、可移除（AC-4）
//   ④ 收起/展开：默认展开常驻「收起」按钮；点击隐藏列表主体、保留「已选 N 项」
//     摘要、不触发 onChange；再点「展开」内容恢复且勾选回写正常
// =============================================================================

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import type { ToolCatalogItem, ToolCatalogResponse } from "../../api/orchestrationApiClient";

// listTools mock：vi.hoisted 保证工厂引用在模块求值前完成初始化
const { listToolsMock } = vi.hoisted(() => ({ listToolsMock: vi.fn() }));
vi.mock("../../api/orchestrationApiClient", () => ({
  orchestrationApiClient: {
    listTools: () => listToolsMock(),
  },
}));

import ToolSelectionList from "../ToolSelectionList";

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

/** 组装清单响应体 */
function catalogResponse(tools: ToolCatalogItem[]): ToolCatalogResponse {
  return { tools, total: tools.length };
}

/** 按行 title（工具全量描述）定位行内勾选框 */
function checkboxOf(rowTitle: string): HTMLInputElement {
  const row = screen.getByTitle(rowTitle);
  return within(row).getByRole("checkbox");
}

beforeEach(() => {
  listToolsMock.mockReset();
});

describe("① 清单加载成功：分组渲染与勾选还原（AC-2）", () => {
  it("渲染平台 / MCP / 本地三组标题与工具项", async () => {
    listToolsMock.mockResolvedValue(catalogResponse(CATALOG));
    render(<ToolSelectionList value={[]} onChange={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByText("平台工具")).toBeTruthy();
    });
    expect(screen.getByText("MCP 工具")).toBeTruthy();
    expect(screen.getByText("本地工具")).toBeTruthy();
    expect(screen.getByText("校验工作流")).toBeTruthy();
    expect(screen.getByText("天气查询")).toBeTruthy();
    expect(screen.getByText("报告生成")).toBeTruthy();
  });

  it("勾选态与传入 value 一致，勾选/取消全量回写 onChange", async () => {
    listToolsMock.mockResolvedValue(catalogResponse(CATALOG));
    const onChange = vi.fn();
    render(<ToolSelectionList value={["report_builder"]} onChange={onChange} />);
    await waitFor(() => {
      expect(screen.getByText("本地工具")).toBeTruthy();
    });
    const reportBox = checkboxOf("报告生成（report_builder）：生成分析报告");
    const weatherBox = checkboxOf("天气查询（weather_query）：查询指定城市天气");
    // 勾选还原：value 内的 report_builder 已勾选，其余未勾选
    expect(reportBox).toBeChecked();
    expect(weatherBox).not.toBeChecked();
    // 勾选未选工具 → 全量数组追加回写
    fireEvent.click(weatherBox);
    expect(onChange).toHaveBeenCalledWith(["report_builder", "weather_query"]);
    // 取消已选工具 → 全量数组过滤回写
    fireEvent.click(reportBox);
    expect(onChange).toHaveBeenLastCalledWith([]);
  });
});

describe("② 清单拉取失败：降级不清空既有绑定（AC-3）", () => {
  it("出现失败提示、既有绑定以（已绑定）灰底行保留、可取消勾选", async () => {
    listToolsMock.mockRejectedValue(new Error("network down"));
    const onChange = vi.fn();
    render(
      <ToolSelectionList value={["legacy_tool", "another_tool"]} onChange={onChange} />,
    );
    await waitFor(() => {
      expect(screen.getByText("工具清单加载失败")).toBeTruthy();
    });
    // 既有绑定保留展示（灰底行 + （已绑定）标记），value 不被清空
    expect(screen.getByText("legacy_tool")).toBeTruthy();
    expect(screen.getByText("another_tool")).toBeTruthy();
    expect(screen.getAllByText("（已绑定）")).toHaveLength(2);
    expect(onChange).not.toHaveBeenCalled();
    // 可取消勾选：仅移除目标标识
    fireEvent.click(within(screen.getByTitle("legacy_tool")).getByRole("checkbox"));
    expect(onChange).toHaveBeenCalledWith(["another_tool"]);
  });
});

describe("③ 清单外标识：已失效标记与移除（AC-4）", () => {
  it("失效标识渲染「已失效」标记行，可移除且不影响正常勾选项", async () => {
    listToolsMock.mockResolvedValue(catalogResponse(CATALOG));
    const onChange = vi.fn();
    render(
      <ToolSelectionList value={["report_builder", "ghost_tool"]} onChange={onChange} />,
    );
    await waitFor(() => {
      expect(screen.getByText("本地工具")).toBeTruthy();
    });
    // 失效标记行：清单外标识 + 「已失效」
    expect(screen.getByText("ghost_tool")).toBeTruthy();
    expect(screen.getByText("已失效")).toBeTruthy();
    // 正常勾选项不受影响：report_builder 仍勾选
    expect(
      checkboxOf("报告生成（report_builder）：生成分析报告"),
    ).toBeChecked();
    // 移除失效标识 → 全量数组过滤回写
    fireEvent.click(screen.getByRole("button", { name: "✕" }));
    expect(onChange).toHaveBeenCalledWith(["report_builder"]);
  });
});

describe("④ 收起/展开：可用工具区块折叠", () => {
  it("默认展开：「收起」按钮可见且 aria-expanded=true，列表内容渲染", async () => {
    listToolsMock.mockResolvedValue(catalogResponse(CATALOG));
    render(<ToolSelectionList value={[]} onChange={vi.fn()} />);
    const toggle = screen.getByRole("button", { name: "收起" });
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    await waitFor(() => {
      expect(screen.getByText("平台工具")).toBeTruthy();
    });
    expect(screen.getByText("已选 0 项")).toBeTruthy();
  });

  it("点击收起：列表主体隐藏、摘要计数保留、按钮变「展开」，不触发 onChange", async () => {
    listToolsMock.mockResolvedValue(catalogResponse(CATALOG));
    const onChange = vi.fn();
    render(
      <ToolSelectionList value={["report_builder", "weather_query"]} onChange={onChange} />,
    );
    await waitFor(() => {
      expect(screen.getByText("平台工具")).toBeTruthy();
    });
    fireEvent.click(screen.getByRole("button", { name: "收起" }));
    // 列表主体隐藏：组标题与工具行全部卸载
    expect(screen.queryByText("平台工具")).toBeNull();
    expect(screen.queryByText("MCP 工具")).toBeNull();
    expect(screen.queryByText("本地工具")).toBeNull();
    expect(screen.queryByText("校验工作流")).toBeNull();
    // 摘要计数保留，按钮切换为「展开」
    expect(screen.getByText("已选 2 项")).toBeTruthy();
    const expand = screen.getByRole("button", { name: "展开" });
    expect(expand.getAttribute("aria-expanded")).toBe("false");
    // 折叠为纯 UI 状态，不改动数据
    expect(onChange).not.toHaveBeenCalled();
  });

  it("收起后再点「展开」：列表内容恢复，勾选回写正常", async () => {
    listToolsMock.mockResolvedValue(catalogResponse(CATALOG));
    const onChange = vi.fn();
    render(<ToolSelectionList value={["report_builder"]} onChange={onChange} />);
    await waitFor(() => {
      expect(screen.getByText("平台工具")).toBeTruthy();
    });
    fireEvent.click(screen.getByRole("button", { name: "收起" }));
    expect(screen.queryByText("平台工具")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "展开" }));
    expect(screen.getByText("平台工具")).toBeTruthy();
    // 展开后勾选功能正常：全量数组追加回写
    fireEvent.click(checkboxOf("天气查询（weather_query）：查询指定城市天气"));
    expect(onChange).toHaveBeenCalledWith(["report_builder", "weather_query"]);
  });
});
