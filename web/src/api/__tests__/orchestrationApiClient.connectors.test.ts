import { describe, it, expect, vi, beforeEach } from "vitest";
import { orchestrationApiClient, getTenantId, ApiException } from "../orchestrationApiClient";

describe("orchestrationApiClient connectors", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("listEnterpriseConnectors 发起 GET 请求并携带 tenant_id", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve([{ connector_id: "c1", connector_type: "csv", target_endpoint: "/api", enabled: true }]),
    });
    globalThis.fetch = mockFetch as unknown as typeof fetch;
    vi.spyOn(Storage.prototype, "getItem").mockReturnValue("");

    const result = await orchestrationApiClient.listEnterpriseConnectors("tenant-1");

    expect(mockFetch).toHaveBeenCalledWith(
      "/api/v1/connectors/enterprise?tenant_id=tenant-1",
      expect.objectContaining({ headers: expect.any(Object) }),
    );
    expect(result).toHaveLength(1);
    expect(result[0].connector_id).toBe("c1");
  });

  it("testEnterpriseConnection 发起 POST 请求且 signal 透传（AC-2.2）", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ reachable: true, response_latency_ms: 50, diagnosis: "ok", tested_at: "2026-01-01T00:00:00Z" }),
    });
    globalThis.fetch = mockFetch as unknown as typeof fetch;
    vi.spyOn(Storage.prototype, "getItem").mockReturnValue("");

    const controller = new AbortController();
    await orchestrationApiClient.testEnterpriseConnection("c1", controller.signal);

    expect(mockFetch).toHaveBeenCalledWith(
      "/api/v1/connectors/enterprise/c1/test",
      expect.objectContaining({ method: "POST", signal: controller.signal }),
    );
  });

  it("testDataSourceConnection 发起 POST 请求、携带完整连接配置且 signal 透传（AC-8）", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ reachable: true, response_latency_ms: 12, diagnosis: "", tested_at: "2026-01-01T00:00:00Z" }),
    });
    globalThis.fetch = mockFetch as unknown as typeof fetch;
    vi.spyOn(Storage.prototype, "getItem").mockReturnValue("");

    const controller = new AbortController();
    const payload = {
      source_type: "json" as const,
      connection_reference: "local-json-1",
      file_path: "D:/data/demo.json",
    };
    const result = await orchestrationApiClient.testDataSourceConnection(payload, controller.signal);

    expect(mockFetch).toHaveBeenCalledWith(
      "/api/v1/data-source/test",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify(payload),
        signal: controller.signal,
      }),
    );
    expect(result.reachable).toBe(true);
  });

  it("isLocalDataSourceType 分流判定：本地 8 类为 true，企业 3 类为 false（INV-2）", async () => {
    const { isLocalDataSourceType } = await import("../../canvas/dataSourceConfig/dataSourceTypeMeta");
    const local = ["csv", "json", "rest_api", "database", "hologres", "kafka", "flink", "spark"] as const;
    const enterprise = ["crm", "erp", "oa"] as const;
    for (const t of local) expect(isLocalDataSourceType(t)).toBe(true);
    for (const t of enterprise) expect(isLocalDataSourceType(t)).toBe(false);
    expect(isLocalDataSourceType(undefined)).toBe(false);
  });

  it("404 错误体为 FastAPI 默认 {detail} 时保留 detail 作为 message（路由未注册场景）", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
      statusText: "Not Found",
      json: () => Promise.resolve({ detail: "Not Found" }),
    });
    globalThis.fetch = mockFetch as unknown as typeof fetch;
    vi.spyOn(Storage.prototype, "getItem").mockReturnValue("");

    await expect(
      orchestrationApiClient.testDataSourceConnection({
        source_type: "json",
        connection_reference: "r1",
        file_path: "D:/demo.json",
      }),
    ).rejects.toMatchObject({ httpStatus: 404, message: "Not Found" });
  });

  it("404 错误体为 {detail} 且含业务原因时透出后端原因（连接器未注册）", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
      statusText: "Not Found",
      json: () => Promise.resolve({ detail: "数据源类型 json 的连接器未注册" }),
    });
    globalThis.fetch = mockFetch as unknown as typeof fetch;
    vi.spyOn(Storage.prototype, "getItem").mockReturnValue("");

    await expect(
      orchestrationApiClient.testDataSourceConnection({
        source_type: "json",
        connection_reference: "r1",
        file_path: "D:/demo.json",
      }),
    ).rejects.toMatchObject({ httpStatus: 404, message: "数据源类型 json 的连接器未注册" });
  });

  it("inferEnterpriseConnectorSchema 发起 POST 请求", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ fields: [{ name: "id", type: "integer", nullable: false }], inferred_at: "2026-01-01T00:00:00Z" }),
    });
    globalThis.fetch = mockFetch as unknown as typeof fetch;
    vi.spyOn(Storage.prototype, "getItem").mockReturnValue("");

    await orchestrationApiClient.inferEnterpriseConnectorSchema("c1");

    expect(mockFetch).toHaveBeenCalledWith(
      "/api/v1/connectors/enterprise/c1/schema",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("getTenantId 从 localStorage 读取 wfp_tenant_id（Q-1）", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation((key: string) => {
      if (key === "wfp_tenant_id") return "my-tenant";
      return null;
    });
    expect(getTenantId()).toBe("my-tenant");
  });

  it("getTenantId 缺失时回退 default（Q-1）", () => {
    vi.spyOn(Storage.prototype, "getItem").mockReturnValue(null);
    expect(getTenantId()).toBe("default");
  });

  it("非 2xx 响应抛出 ApiException 含 httpStatus", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
      json: () => Promise.resolve({ code: "NOT_FOUND", message: "连接器不存在", details: {} }),
    });
    globalThis.fetch = mockFetch as unknown as typeof fetch;
    vi.spyOn(Storage.prototype, "getItem").mockReturnValue("");

    try {
      await orchestrationApiClient.testEnterpriseConnection("nonexistent");
      expect.fail("应抛出 ApiException");
    } catch (e) {
      expect(e).toBeInstanceOf(ApiException);
      expect((e as ApiException).httpStatus).toBe(404);
    }
  });
});