// =============================================================================
// 市场客户端单元测试（src/market/__tests__/marketApiClient.test.ts）
//
// 留待非沙箱环境执行：cd web && npx vitest run src/market/__tests__/marketApiClient.test.ts
//
// 覆盖用例（agent_tool_marketplace T18.2 / AC-24）：
//   TC-MC-01~12 各端点 URL / query 拼装 / 方法 / 请求体
//   TC-MC-13~15 非 2xx → 抛错（Agent 条目 / 模板详情 / Tool 清单）
// =============================================================================

import { describe, it, expect, vi, afterEach } from "vitest";
import type { MockInstance } from "vitest";
import {
  listAgentMarketEntries,
  getAgentMarketEntry,
  listAgentMarketCategories,
  registerAgentMarketEntry,
  listAgentTemplates,
  getAgentTemplate,
  listToolMarketTools,
  getToolMarketTool,
  listToolMarketCategories,
  registerToolMarketTool,
} from "../marketApiClient";

/** 模拟 fetch 返回 200 JSON 响应 */
function mockFetchOk(payload: unknown): MockInstance<typeof fetch> {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify(payload), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("marketApiClient — Agent 市场端点", () => {
  it("TC-MC-01 listAgentMarketEntries 无筛选 → GET /api/v1/agent-market/entries（无 query）", async () => {
    const fetchSpy = mockFetchOk({ entries: [], total: 0 });
    await listAgentMarketEntries();
    expect(fetchSpy.mock.calls[0]?.[0]).toBe("/api/v1/agent-market/entries");
  });

  it("TC-MC-02 listAgentMarketEntries 带筛选 → query 拼装 q/category/status", async () => {
    const fetchSpy = mockFetchOk({ entries: [], total: 0 });
    await listAgentMarketEntries({ q: "关键词", category: "分类A", status: "available" });
    const url = fetchSpy.mock.calls[0]?.[0] as string;
    expect(url.startsWith("/api/v1/agent-market/entries?")).toBe(true);
    expect(url).toContain("q=");
    expect(url).toContain("category=");
    expect(url).toContain("status=available");
  });

  it("TC-MC-03 getAgentMarketEntry → GET /api/v1/agent-market/entries/{id}", async () => {
    const fetchSpy = mockFetchOk({ entry_id: "e1" });
    await getAgentMarketEntry("e1");
    expect(fetchSpy.mock.calls[0]?.[0]).toBe("/api/v1/agent-market/entries/e1");
  });

  it("TC-MC-04 listAgentMarketCategories → GET /api/v1/agent-market/categories", async () => {
    const fetchSpy = mockFetchOk({ categories: [], total: 0 });
    await listAgentMarketCategories();
    expect(fetchSpy.mock.calls[0]?.[0]).toBe("/api/v1/agent-market/categories");
  });

  it("TC-MC-05 registerAgentMarketEntry → POST entries + JSON 请求体", async () => {
    const fetchSpy = mockFetchOk({ entry_id: "e1" });
    const payload = { name: "新条目" };
    await registerAgentMarketEntry(payload);
    const init = fetchSpy.mock.calls[0]?.[1] as RequestInit;
    expect(fetchSpy.mock.calls[0]?.[0]).toBe("/api/v1/agent-market/entries");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual(payload);
  });

  it("TC-MC-06 listAgentTemplates → GET /api/v1/agent-market/templates", async () => {
    const fetchSpy = mockFetchOk({ templates: [], total: 5 });
    await listAgentTemplates();
    expect(fetchSpy.mock.calls[0]?.[0]).toBe("/api/v1/agent-market/templates");
  });

  it("TC-MC-07 getAgentTemplate → GET /api/v1/agent-market/templates/{template_id}", async () => {
    const fetchSpy = mockFetchOk({ template_id: "basic_react" });
    await getAgentTemplate("basic_react");
    expect(fetchSpy.mock.calls[0]?.[0]).toBe("/api/v1/agent-market/templates/basic_react");
  });
});

describe("marketApiClient — Tool 市场端点", () => {
  it("TC-MC-08 listToolMarketTools 无筛选 → GET /api/v1/tool-market/tools", async () => {
    const fetchSpy = mockFetchOk({ tools: [], total: 0 });
    await listToolMarketTools();
    expect(fetchSpy.mock.calls[0]?.[0]).toBe("/api/v1/tool-market/tools");
  });

  it("TC-MC-09 listToolMarketTools 带筛选 → query 拼装 q/category/permission", async () => {
    const fetchSpy = mockFetchOk({ tools: [], total: 0 });
    await listToolMarketTools({ q: "http", category: "网络", permission: "readonly" });
    const url = fetchSpy.mock.calls[0]?.[0] as string;
    expect(url.startsWith("/api/v1/tool-market/tools?")).toBe(true);
    expect(url).toContain("q=http");
    expect(url).toContain("category=");
    expect(url).toContain("permission=readonly");
  });

  it("TC-MC-10 getToolMarketTool → GET /api/v1/tool-market/tools/{id}", async () => {
    const fetchSpy = mockFetchOk({ tool_identifier: "http_request" });
    await getToolMarketTool("http_request");
    expect(fetchSpy.mock.calls[0]?.[0]).toBe("/api/v1/tool-market/tools/http_request");
  });

  it("TC-MC-11 listToolMarketCategories → GET /api/v1/tool-market/categories", async () => {
    const fetchSpy = mockFetchOk({ categories: [], total: 0 });
    await listToolMarketCategories();
    expect(fetchSpy.mock.calls[0]?.[0]).toBe("/api/v1/tool-market/categories");
  });

  it("TC-MC-12 registerToolMarketTool → POST tools + JSON 请求体", async () => {
    const fetchSpy = mockFetchOk({
      success: true,
      tool_identifier: "t1",
      version: "1.0.0",
      conflict_reason: null,
    });
    const payload = { tool_identifier: "t1" };
    await registerToolMarketTool(payload);
    const init = fetchSpy.mock.calls[0]?.[1] as RequestInit;
    expect(fetchSpy.mock.calls[0]?.[0]).toBe("/api/v1/tool-market/tools");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual(payload);
  });
});

describe("marketApiClient — 非 2xx 抛错（AC-24）", () => {
  it("TC-MC-13 Agent 条目查询 500 → 抛错", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}", { status: 500 }));
    await expect(listAgentMarketEntries()).rejects.toThrow("HTTP 500");
  });

  it("TC-MC-14 模板详情查询 404 → 抛错", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}", { status: 404 }));
    await expect(getAgentTemplate("missing")).rejects.toThrow("HTTP 404");
  });

  it("TC-MC-15 Tool 清单查询 502 → 抛错", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}", { status: 502 }));
    await expect(listToolMarketTools()).rejects.toThrow("HTTP 502");
  });
});
