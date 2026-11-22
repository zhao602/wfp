// =============================================================================
// 请求头鉴权构造单元测试（api/__tests__/orchestrationApiClient.auth.test.ts）
//
// 留待非沙箱环境执行：vitest run src/api/__tests__/orchestrationApiClient.auth.test.ts
//
// 覆盖：
//   1. getAccessToken() 五条路径（正常/空/null/纯空白/异常）
//   2. buildHeaders() 两条路径（有令牌注入 Authorization / 无令牌仅含 Content-Type）
//
// 因 getAccessToken() 与 buildHeaders() 为模块私有函数，测试通过 mock fetch + localStorage
// 调用公开方法 listExamples() 间接验证请求头构造行为。
// =============================================================================

import { describe, it, expect, vi, afterEach } from "vitest";
import type { MockInstance } from "vitest";
import { orchestrationApiClient } from "../orchestrationApiClient";

/** 模拟 fetch 返回范例列表 200 响应 */
function mockFetchOk(): MockInstance<typeof fetch> {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify({ examples: [] }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

// =============================================================================
// getAccessToken() — 令牌读取容错与空白归一（间接验证）
// =============================================================================

describe("getAccessToken — 令牌读取容错与空白归一", () => {
  it("有非空令牌时返回 trim 后值", async () => {
    const fetchSpy = mockFetchOk();
    vi.spyOn(Storage.prototype, "getItem").mockReturnValue("  valid-token  ");
    await orchestrationApiClient.listExamples();
    const headers = fetchSpy.mock.calls[0]?.[1]?.headers as Record<string, string>;
    expect(headers["Authorization"]).toBe("Bearer valid-token");
  });

  it("localStorage 返回 null 时视为无令牌", async () => {
    const fetchSpy = mockFetchOk();
    vi.spyOn(Storage.prototype, "getItem").mockReturnValue(null);
    await orchestrationApiClient.listExamples();
    const headers = fetchSpy.mock.calls[0]?.[1]?.headers as Record<string, string>;
    expect(headers).not.toHaveProperty("Authorization");
  });

  it("localStorage 返回空字符串时视为无令牌", async () => {
    const fetchSpy = mockFetchOk();
    vi.spyOn(Storage.prototype, "getItem").mockReturnValue("");
    await orchestrationApiClient.listExamples();
    const headers = fetchSpy.mock.calls[0]?.[1]?.headers as Record<string, string>;
    expect(headers).not.toHaveProperty("Authorization");
  });

  it("localStorage 返回纯空白时经 trim 归一为无令牌", async () => {
    const fetchSpy = mockFetchOk();
    vi.spyOn(Storage.prototype, "getItem").mockReturnValue("   ");
    await orchestrationApiClient.listExamples();
    const headers = fetchSpy.mock.calls[0]?.[1]?.headers as Record<string, string>;
    expect(headers).not.toHaveProperty("Authorization");
  });

  it("localStorage.getItem 抛异常时视为无令牌", async () => {
    const fetchSpy = mockFetchOk();
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("SecurityError: localStorage 不可用");
    });
    await orchestrationApiClient.listExamples();
    const headers = fetchSpy.mock.calls[0]?.[1]?.headers as Record<string, string>;
    expect(headers).not.toHaveProperty("Authorization");
  });
});

// =============================================================================
// buildHeaders() — 按需注入 Authorization 头
// =============================================================================

describe("buildHeaders — 按需注入 Authorization", () => {
  it("有非空令牌时返回含 Content-Type 与 Authorization 的头", async () => {
    const fetchSpy = mockFetchOk();
    vi.spyOn(Storage.prototype, "getItem").mockReturnValue("my-token-abc");
    await orchestrationApiClient.listExamples();
    const headers = fetchSpy.mock.calls[0]?.[1]?.headers as Record<string, string>;
    expect(headers["Content-Type"]).toBe("application/json");
    expect(headers["Authorization"]).toBe("Bearer my-token-abc");
  });

  it("无令牌时返回仅含 Content-Type 的头，不含 Authorization 键", async () => {
    const fetchSpy = mockFetchOk();
    vi.spyOn(Storage.prototype, "getItem").mockReturnValue(null);
    await orchestrationApiClient.listExamples();
    const headers = fetchSpy.mock.calls[0]?.[1]?.headers as Record<string, string>;
    expect(headers["Content-Type"]).toBe("application/json");
    expect(headers).not.toHaveProperty("Authorization");
    // 确保不存在空 Bearer 子串
    expect(JSON.stringify(headers)).not.toContain("Bearer ");
  });
});