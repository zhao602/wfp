// =============================================================================
// Tool 市场面板测试（src/market/__tests__/ToolMarketPanel.test.tsx）
//
// 留待非沙箱环境执行：cd web && npx vitest run src/market/__tests__/ToolMarketPanel.test.tsx
//
// 覆盖用例（agent_tool_marketplace T18.4 / AC-21）：
//   TC-TMP-01 列表含来源（platform/mcp/local）与权限徽章
//   TC-TMP-02 权限筛选触发新请求
//   TC-TMP-03 点击工具 → 详情渲染（含输入参数 schema 摘要）
//   TC-TMP-04 拉取失败 → 错误条且页面可渲染
// =============================================================================

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
import { ToolMarketPanel } from "../ToolMarketPanel";
import { listToolMarketTools, listToolMarketCategories } from "../marketApiClient";
import type { ToolMarketEntry } from "../marketTypes";

vi.mock("../marketApiClient", () => ({
  listToolMarketTools: vi.fn(),
  listToolMarketCategories: vi.fn(),
}));

/** 工具条目夹具 */
function makeTool(overrides: Partial<ToolMarketEntry> = {}): ToolMarketEntry {
  return {
    tool_identifier: "http_request",
    name: "HTTP 请求",
    description: "发起 HTTP 请求",
    source: "platform",
    permission: "readwrite",
    version: "1.0.0",
    tags: ["网络"],
    input_schema_summary: [{ param: "url", type: "string" }],
    output_schema_summary: [{ param: "status_code", type: "integer" }],
    ...overrides,
  };
}

beforeEach(() => {
  vi.mocked(listToolMarketTools).mockResolvedValue({
    tools: [
      makeTool(),
      makeTool({
        tool_identifier: "mcp_search",
        name: "MCP 搜索",
        source: "mcp",
        permission: "readonly",
        input_schema_summary: [],
      }),
      makeTool({
        tool_identifier: "local_calc",
        name: "本地计算",
        source: "local",
        permission: "readonly",
        input_schema_summary: [],
      }),
    ],
    total: 3,
  });
  vi.mocked(listToolMarketCategories).mockResolvedValue({
    categories: [{ category: "网络", count: 2 }],
    total: 1,
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ToolMarketPanel", () => {
  it("TC-TMP-01 列表含来源与权限徽章", async () => {
    render(<ToolMarketPanel />);

    await screen.findByTestId("tool-market-table");
    // 三源徽章
    expect(screen.getByText("platform")).toBeInTheDocument();
    expect(screen.getByText("mcp")).toBeInTheDocument();
    expect(screen.getByText("local")).toBeInTheDocument();
    // 权限徽章（select 的 option 与表格徽章均含权限值，用 getAllByText）
    expect(screen.getAllByText("readwrite").length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText("readonly").length).toBeGreaterThanOrEqual(2);
  });

  it("TC-TMP-02 权限筛选触发新请求", async () => {
    render(<ToolMarketPanel />);
    await screen.findByTestId("tool-market-table");

    fireEvent.change(screen.getByTestId("tool-market-permission-select"), {
      target: { value: "readonly" },
    });

    await waitFor(() => {
      expect(listToolMarketTools).toHaveBeenCalledTimes(2);
    });
    expect(listToolMarketTools).toHaveBeenNthCalledWith(2, {
      q: undefined,
      category: undefined,
      permission: "readonly",
    });
  });

  it("TC-TMP-03 点击工具 → 详情渲染（含 schema 摘要）", async () => {
    render(<ToolMarketPanel />);
    await screen.findByTestId("tool-market-table");

    fireEvent.click(screen.getByTestId("tool-market-tool-http_request"));

    const detail = await screen.findByTestId("tool-market-detail");
    expect(detail).toHaveTextContent("HTTP 请求");
    expect(detail).toHaveTextContent("http_request");
    // 输入参数 schema 摘要（param：type）
    expect(screen.getByTestId("tool-market-input-schema")).toHaveTextContent("url：string");
    expect(screen.getByTestId("tool-market-output-schema")).toHaveTextContent(
      "status_code：integer",
    );
  });

  it("TC-TMP-04 拉取失败 → 错误条且页面可渲染", async () => {
    vi.mocked(listToolMarketTools).mockRejectedValue(new Error("查询 Tool 市场工具失败"));

    render(<ToolMarketPanel />);

    const errorBar = await screen.findByTestId("tool-market-error");
    expect(errorBar).toHaveTextContent("查询 Tool 市场工具失败");
    expect(screen.getByTestId("tool-market-keyword-input")).toBeInTheDocument();
    expect(screen.getByTestId("tool-market-permission-select")).toBeInTheDocument();
  });
});
