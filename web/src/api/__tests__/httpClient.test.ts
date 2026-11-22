// =============================================================================
// httpClient 基元单元测试（api/__tests__/httpClient.test.ts）
//
// 覆盖（design §4.3 / T3.1-④）：
//   1. token 注入：默认 localStorage 读取五路径 + 自定义 provider；
//   2. 租户头：tenantHeaderName 设置/未设置/自定义 getTenantId；
//   3. 错误映射：2xx JSON / voidResponse / 结构化错误体 / FastAPI {detail}（字符串与
//      {error_code,error_message} 结构化对象）/ 不可解析体 / 网络失败 transport 兜底 / 超时 / 自定义 errorFactory；
//   4. 调用方取消：裸 AbortError 透传（不包装为 ApiError）；
//   5. 重试开关三态：默认关闭 / 开启+幂等方法重试 / 开启+非幂等方法被限定；
//   6. baseUrl 拼接与 ApiError 错误类引用。
// =============================================================================

import { describe, it, expect, vi, afterEach } from "vitest";
import type { MockInstance } from "vitest";
import { createHttpClient } from "../httpClient";
import { ApiError as ApiErrorFromHttpClient } from "../httpClient";
import { ApiError as ApiErrorFromApiError, ApiErrorCodeLayer } from "../apiError";

/** 构造 2xx JSON 响应 mock */
function okResponse<T>(body: T): Response {
  return { ok: true, status: 200, json: () => Promise.resolve(body) } as unknown as Response;
}

/** 构造非 2xx 响应 mock */
function errorResponse(status: number, statusText: string, body: unknown): Response {
  const json = () =>
    typeof body === "string"
      ? Promise.reject(new SyntaxError("Unexpected token"))
      : Promise.resolve(body);
  return { ok: false, status, statusText, json } as unknown as Response;
}

/** 构造可被 abort 的 fetch mock（预中止立即拒绝，运行中中止经监听器拒绝） */
function abortableFetchMock(): MockInstance<typeof fetch> {
  return vi.spyOn(globalThis, "fetch").mockImplementation(
    (_url: RequestInfo | URL, init?: RequestInit) =>
      new Promise<Response>((_resolve, reject) => {
        const rejectWithAbort = () =>
          reject(new DOMException("The operation was aborted.", "AbortError"));
        if (init?.signal?.aborted) {
          rejectWithAbort();
          return;
        }
        init?.signal?.addEventListener("abort", rejectWithAbort);
      }),
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

// =============================================================================
// token 注入（默认 provider）
// =============================================================================

describe("token 注入 — 默认 localStorage 读取与空 Bearer 守卫", () => {
  it("有非空令牌时注入 trim 后 Bearer", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(okResponse({}));
    vi.spyOn(Storage.prototype, "getItem").mockReturnValue("  valid-token  ");
    const client = createHttpClient();
    await client.request({ path: "/ping" });
    const headers = fetchSpy.mock.calls[0]?.[1]?.headers as Record<string, string>;
    expect(headers["Authorization"]).toBe("Bearer valid-token");
  });

  it("localStorage 返回 null 时不携带 Authorization 键", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(okResponse({}));
    vi.spyOn(Storage.prototype, "getItem").mockReturnValue(null);
    const client = createHttpClient();
    await client.request({ path: "/ping" });
    const headers = fetchSpy.mock.calls[0]?.[1]?.headers as Record<string, string>;
    expect(headers).not.toHaveProperty("Authorization");
    expect(JSON.stringify(headers)).not.toContain("Bearer ");
  });

  it("localStorage 返回纯空白时经 trim 归一为无令牌", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(okResponse({}));
    vi.spyOn(Storage.prototype, "getItem").mockReturnValue("   ");
    const client = createHttpClient();
    await client.request({ path: "/ping" });
    const headers = fetchSpy.mock.calls[0]?.[1]?.headers as Record<string, string>;
    expect(headers).not.toHaveProperty("Authorization");
  });

  it("localStorage.getItem 抛异常时视为无令牌", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(okResponse({}));
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("SecurityError: localStorage 不可用");
    });
    const client = createHttpClient();
    await client.request({ path: "/ping" });
    const headers = fetchSpy.mock.calls[0]?.[1]?.headers as Record<string, string>;
    expect(headers).not.toHaveProperty("Authorization");
  });

  it("自定义 getAccessToken provider 时使用 provider 值", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(okResponse({}));
    const client = createHttpClient({ getAccessToken: () => "provider-token" });
    await client.request({ path: "/ping" });
    const headers = fetchSpy.mock.calls[0]?.[1]?.headers as Record<string, string>;
    expect(headers["Authorization"]).toBe("Bearer provider-token");
  });
});

// =============================================================================
// 租户头
// =============================================================================

describe("租户头 — tenantHeaderName 注入", () => {
  it("设置 tenantHeaderName 时注入默认租户读取值", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(okResponse({}));
    vi.spyOn(Storage.prototype, "getItem").mockImplementation((key: string) =>
      key === "wfp_tenant_id" ? "my-tenant" : null,
    );
    const client = createHttpClient({ tenantHeaderName: "X-Tenant-Id" });
    await client.request({ path: "/ping" });
    const headers = fetchSpy.mock.calls[0]?.[1]?.headers as Record<string, string>;
    expect(headers["X-Tenant-Id"]).toBe("my-tenant");
  });

  it("租户键缺失时回退 default", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(okResponse({}));
    vi.spyOn(Storage.prototype, "getItem").mockReturnValue(null);
    const client = createHttpClient({ tenantHeaderName: "X-Tenant-Id" });
    await client.request({ path: "/ping" });
    const headers = fetchSpy.mock.calls[0]?.[1]?.headers as Record<string, string>;
    expect(headers["X-Tenant-Id"]).toBe("default");
  });

  it("自定义 getTenantId 时注入自定义值", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(okResponse({}));
    const client = createHttpClient({
      tenantHeaderName: "X-Tenant-Id",
      getTenantId: () => "custom-tenant",
    });
    await client.request({ path: "/ping" });
    const headers = fetchSpy.mock.calls[0]?.[1]?.headers as Record<string, string>;
    expect(headers["X-Tenant-Id"]).toBe("custom-tenant");
  });

  it("未设置 tenantHeaderName 时请求头仅含 Content-Type（无令牌场景）", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(okResponse({}));
    vi.spyOn(Storage.prototype, "getItem").mockReturnValue(null);
    const client = createHttpClient();
    await client.request({ path: "/ping" });
    const headers = fetchSpy.mock.calls[0]?.[1]?.headers as Record<string, string>;
    expect(headers).toEqual({ "Content-Type": "application/json" });
  });
});

// =============================================================================
// 错误映射
// =============================================================================

describe("错误映射 — 2xx / 错误体兼容 / transport 兜底", () => {
  it("2xx 响应解析 JSON 返回", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(okResponse({ value: 42 }));
    const client = createHttpClient();
    const result = await client.request<{ value: number }>({ path: "/ping" });
    expect(result.value).toBe(42);
  });

  it("voidResponse 为 true 时 2xx 不解析响应体且 resolve undefined", async () => {
    const json = vi.fn(() => Promise.resolve({}));
    vi.spyOn(globalThis, "fetch").mockResolvedValue({ ok: true, status: 200, json } as unknown as Response);
    const client = createHttpClient();
    const result = await client.request<void>({ path: "/ping", method: "DELETE", voidResponse: true });
    expect(result).toBeUndefined();
    expect(json).not.toHaveBeenCalled();
  });

  it("非 2xx 结构化错误体 {code,message,details} 经默认工厂抛 ApiError（404→business 层）", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      errorResponse(404, "Not Found", { code: "NOT_FOUND", message: "连接器不存在", details: { id: "c1" } }),
    );
    const client = createHttpClient();
    const error = await client.request({ path: "/ping" }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiErrorFromApiError);
    expect(error).toBeInstanceOf(ApiErrorFromHttpClient);
    const apiError = error as ApiErrorFromApiError;
    expect(apiError.code).toBe("NOT_FOUND");
    expect(apiError.message).toBe("连接器不存在");
    expect(apiError.details).toEqual({ id: "c1" });
    expect(apiError.httpStatus).toBe(404);
    expect(apiError.layer).toBe(ApiErrorCodeLayer.Business);
  });

  it("非 2xx FastAPI {detail} 错误体保留 detail 作为 message", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      errorResponse(404, "Not Found", { detail: "Not Found" }),
    );
    const client = createHttpClient();
    await expect(client.request({ path: "/ping" })).rejects.toMatchObject({
      code: "HTTP_ERROR",
      message: "Not Found",
      httpStatus: 404,
    });
  });

  it("非 2xx 市场/治理族 {detail:{error_code,error_message}} 提取结构化字段（409→business 层）", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      errorResponse(409, "Conflict", {
        detail: {
          error_code: "MARKET_DEV_SUBMISSION_DUPLICATE",
          error_message: "重复提交：alice/demo-node@1.0.0 已存在",
        },
      }),
    );
    const client = createHttpClient();
    await expect(client.request({ path: "/ping" })).rejects.toMatchObject({
      code: "MARKET_DEV_SUBMISSION_DUPLICATE",
      message: "重复提交：alice/demo-node@1.0.0 已存在",
      httpStatus: 409,
      layer: ApiErrorCodeLayer.Business,
    });
  });

  it("非 2xx detail 为不含 error_message 的对象时回退 HTTP 兜底", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      errorResponse(400, "Bad Request", { detail: { foo: "bar" } }),
    );
    const client = createHttpClient();
    await expect(client.request({ path: "/ping" })).rejects.toMatchObject({
      code: "HTTP_ERROR",
      message: "HTTP 400 Bad Request",
      httpStatus: 400,
    });
  });

  it("非 2xx 响应体不可解析时兜底 UNKNOWN_ERROR", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      errorResponse(503, "Service Unavailable", "not-json"),
    );
    const client = createHttpClient();
    await expect(client.request({ path: "/ping" })).rejects.toMatchObject({
      code: "UNKNOWN_ERROR",
      message: "HTTP 503: Service Unavailable",
      httpStatus: 503,
      layer: ApiErrorCodeLayer.Transport,
    });
  });

  it("网络失败（fetch 拒绝）兜底 NETWORK_ERROR transport 层", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("Network down"));
    const client = createHttpClient();
    await expect(client.request({ path: "/ping" })).rejects.toMatchObject({
      code: "NETWORK_ERROR",
      message: "Network down",
      httpStatus: undefined,
      layer: ApiErrorCodeLayer.Transport,
    });
  });

  it("超时（timeoutMs）兜底 TIMEOUT transport 层", async () => {
    abortableFetchMock();
    const client = createHttpClient({ timeoutMs: 10 });
    await expect(client.request({ path: "/ping" })).rejects.toMatchObject({
      code: "TIMEOUT",
      httpStatus: undefined,
      layer: ApiErrorCodeLayer.Transport,
    });
  });

  it("自定义 errorFactory 时抛工厂产物（子类 instanceof ApiError 保留）", async () => {
    class CustomApiError extends ApiErrorFromApiError {
      constructor(code: string, message: string, details: Record<string, unknown>, httpStatus?: number) {
        super(code, message, details, httpStatus);
        this.name = "CustomApiError";
      }
    }
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      errorResponse(409, "Conflict", { code: "STATE_CONFLICT", message: "冲突", details: {} }),
    );
    const client = createHttpClient({
      errorFactory: (code, message, details, httpStatus) =>
        new CustomApiError(code, message, details, httpStatus),
    });
    const error = await client.request({ path: "/ping" }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(CustomApiError);
    expect(error).toBeInstanceOf(ApiErrorFromApiError);
    expect((error as CustomApiError).httpStatus).toBe(409);
  });
});

// =============================================================================
// 调用方取消
// =============================================================================

describe("调用方取消 — 裸 AbortError 透传", () => {
  it("调用方 signal 预中止时裸 AbortError 透传（不包装为 ApiError）", async () => {
    abortableFetchMock();
    const client = createHttpClient();
    const controller = new AbortController();
    controller.abort();
    const error = await client
      .request({ path: "/ping", signal: controller.signal })
      .catch((e: unknown) => e);
    expect(error).toBeInstanceOf(DOMException);
    expect((error as DOMException).name).toBe("AbortError");
    expect(error).not.toBeInstanceOf(ApiErrorFromApiError);
  });
});

// =============================================================================
// 重试开关三态
// =============================================================================

describe("重试 — 开关三态（默认关闭 / 幂等限定）", () => {
  it("默认关闭：5xx 仅请求一次后抛 ApiError", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(errorResponse(500, "Internal Server Error", { code: "BOOM", message: "内部错误", details: {} }));
    const client = createHttpClient();
    await expect(client.request({ path: "/ping" })).rejects.toMatchObject({
      code: "BOOM",
      httpStatus: 500,
    });
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });

  it("开启 + GET（幂等）：5xx×2 后 200 成功（3 次尝试）", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(errorResponse(500, "Internal Server Error", { code: "BOOM", message: "m", details: {} }))
      .mockResolvedValueOnce(errorResponse(502, "Bad Gateway", { code: "BOOM2", message: "m", details: {} }))
      .mockResolvedValueOnce(okResponse({ ok: true }));
    const client = createHttpClient({
      retry: { enabled: true, maxAttempts: 3, idempotentMethodsOnly: true },
    });
    const result = await client.request<{ ok: boolean }>({ path: "/ping" });
    expect(result.ok).toBe(true);
    expect(fetchSpy).toHaveBeenCalledTimes(3);
  });

  it("开启 + 幂等限定 + POST：非幂等方法不重试（1 次尝试）", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      errorResponse(500, "Internal Server Error", { code: "BOOM", message: "m", details: {} }),
    );
    const client = createHttpClient({
      retry: { enabled: true, maxAttempts: 3, idempotentMethodsOnly: true },
    });
    await expect(client.request({ path: "/ping", method: "POST", body: {} })).rejects.toMatchObject({
      httpStatus: 500,
    });
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });

  it("开启 + idempotentMethodsOnly=false + POST：非幂等方法也重试", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(errorResponse(500, "Internal Server Error", { code: "BOOM", message: "m", details: {} }))
      .mockResolvedValueOnce(okResponse({ saved: true }));
    const client = createHttpClient({
      retry: { enabled: true, maxAttempts: 2, idempotentMethodsOnly: false },
    });
    const result = await client.request<{ saved: boolean }>({ path: "/ping", method: "POST", body: {} });
    expect(result.saved).toBe(true);
    expect(fetchSpy).toHaveBeenCalledTimes(2);
  });

  it("开启 + GET：网络失败后重试成功", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockRejectedValueOnce(new TypeError("Network down"))
      .mockResolvedValueOnce(okResponse({ ok: true }));
    const client = createHttpClient({
      retry: { enabled: true, maxAttempts: 2, idempotentMethodsOnly: true },
    });
    const result = await client.request<{ ok: boolean }>({ path: "/ping" });
    expect(result.ok).toBe(true);
    expect(fetchSpy).toHaveBeenCalledTimes(2);
  });
});

// =============================================================================
// multipart 与逐请求头
// =============================================================================

describe("multipart 与逐请求头", () => {
  it("multipart 为 true 时不注入 Content-Type，FormData 体原样传递", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(okResponse({}));
    const form = new FormData();
    form.append("file", new File(["x"], "a.txt"), "a.txt");
    const client = createHttpClient();
    await client.request({ path: "/upload", method: "POST", multipart: true, body: form });
    const init = fetchSpy.mock.calls[0]?.[1] as RequestInit;
    const headers = init.headers as Record<string, string>;
    expect(headers).not.toHaveProperty("Content-Type");
    expect(init.body).toBe(form);
  });

  it("multipart 请求仍注入 Bearer 令牌", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(okResponse({}));
    vi.spyOn(Storage.prototype, "getItem").mockReturnValue("mtoken");
    const client = createHttpClient();
    await client.request({
      path: "/upload",
      method: "POST",
      multipart: true,
      body: new FormData(),
    });
    const headers = fetchSpy.mock.calls[0]?.[1]?.headers as Record<string, string>;
    expect(headers["Authorization"]).toBe("Bearer mtoken");
  });

  it("逐请求 headers 合并覆写默认头", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(okResponse({}));
    const client = createHttpClient();
    await client.request({
      path: "/ping",
      headers: { "X-Custom": "v1", "Content-Type": "application/merge-patch+json" },
    });
    const headers = fetchSpy.mock.calls[0]?.[1]?.headers as Record<string, string>;
    expect(headers["Content-Type"]).toBe("application/merge-patch+json");
    expect(headers["X-Custom"]).toBe("v1");
  });
});

// =============================================================================
// baseUrl 拼接与错误类引用
// =============================================================================

describe("baseUrl 与错误类引用", () => {
  it("baseUrl 与 path（含查询串）直拼", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(okResponse([]));
    const client = createHttpClient({ baseUrl: "/api/v1" });
    await client.request({ path: "/examples?tag=x" });
    expect(fetchSpy.mock.calls[0]?.[0]).toBe("/api/v1/examples?tag=x");
  });

  it("httpClient 导出的 ApiError 与 apiError 模块为同一类", () => {
    expect(ApiErrorFromHttpClient).toBe(ApiErrorFromApiError);
  });
});
