// =============================================================================
// 用户自主开发 API 客户端测试（src/market/__tests__/devSubmissionApiClient.test.ts）
//
// 留待非沙箱环境执行：cd web && npx vitest run src/market/__tests__/devSubmissionApiClient.test.ts
//
// 覆盖用例（agent_market_user_development T23.3 / AC-DEV-22 / DC-06）：
//   TC-DC-01~08 各端点 URL / query 拼装 / multipart 构造 / 方法
//   TC-DC-09~13 非 2xx → 抛错；错误消息取自 detail.error_message（回退默认文案）
// =============================================================================

import { describe, it, expect, vi, afterEach } from "vitest";
import type { MockInstance } from "vitest";
import {
  listDevScaffolds,
  getDevScaffold,
  submitDevPackage,
  listDevSubmissions,
  getDevSubmission,
  transitionDevSubmission,
  rescanDevSubmission,
  getHistory,
} from "../devSubmissionApiClient";

/** 模拟 fetch 返回 200 JSON 响应 */
function mockFetchOk(payload: unknown): MockInstance<typeof fetch> {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify(payload), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

/** 模拟 fetch 返回非 2xx JSON 响应（可携带 detail.error_message） */
function mockFetchError(status: number, payload: unknown): void {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify(payload), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("devSubmissionApiClient — 请求构造", () => {
  it("TC-DC-01 listDevScaffolds → GET /api/v1/agent-market/dev-templates", async () => {
    const fetchSpy = mockFetchOk({ scaffolds: [], total: 0 });
    await listDevScaffolds();
    expect(fetchSpy.mock.calls[0]?.[0]).toBe("/api/v1/agent-market/dev-templates");
  });

  it("TC-DC-02 getDevScaffold → GET dev-templates/{scaffold_id}", async () => {
    const fetchSpy = mockFetchOk({ scaffold_id: "basic-node" });
    await getDevScaffold("basic-node");
    expect(fetchSpy.mock.calls[0]?.[0]).toBe(
      "/api/v1/agent-market/dev-templates/basic-node",
    );
  });

  it("TC-DC-03 submitDevPackage → POST dev-submissions + multipart 四字段", async () => {
    const fetchSpy = mockFetchOk({ submission_id: "sub-1", accepted: true });
    const file = new File(["zip-bytes"], "pkg.zip", { type: "application/zip" });
    await submitDevPackage(file, "alice", "demo-node", "1.0.0");
    const init = fetchSpy.mock.calls[0]?.[1] as RequestInit;
    expect(fetchSpy.mock.calls[0]?.[0]).toBe("/api/v1/agent-market/dev-submissions");
    expect(init.method).toBe("POST");
    expect(init.body).toBeInstanceOf(FormData);
    const body = init.body as FormData;
    expect(body.get("package")).toBe(file);
    expect(body.get("developer_id")).toBe("alice");
    expect(body.get("node_name")).toBe("demo-node");
    expect(body.get("version")).toBe("1.0.0");
  });

  it("TC-DC-04 listDevSubmissions 带筛选 → query 拼装 state/node_name", async () => {
    const fetchSpy = mockFetchOk({ submissions: [], total: 0 });
    await listDevSubmissions("reviewing", "demo-node");
    const url = fetchSpy.mock.calls[0]?.[0] as string;
    expect(url.startsWith("/api/v1/agent-market/dev-submissions?")).toBe(true);
    expect(url).toContain("state=reviewing");
    expect(url).toContain("node_name=demo-node");
  });

  it("TC-DC-04b listDevSubmissions 无筛选 → 无 query", async () => {
    const fetchSpy = mockFetchOk({ submissions: [], total: 0 });
    await listDevSubmissions();
    expect(fetchSpy.mock.calls[0]?.[0]).toBe("/api/v1/agent-market/dev-submissions");
  });

  it("TC-DC-05 getDevSubmission → GET dev-submissions/{submission_id}", async () => {
    const fetchSpy = mockFetchOk({ submission: { submission_id: "sub-1" } });
    await getDevSubmission("sub-1");
    expect(fetchSpy.mock.calls[0]?.[0]).toBe(
      "/api/v1/agent-market/dev-submissions/sub-1",
    );
  });

  it("TC-DC-06 transitionDevSubmission → POST transition + Form 三字段", async () => {
    const fetchSpy = mockFetchOk({ submission_id: "sub-1", success: true });
    await transitionDevSubmission("sub-1", "approved", "op-1", "审核通过");
    const init = fetchSpy.mock.calls[0]?.[1] as RequestInit;
    expect(fetchSpy.mock.calls[0]?.[0]).toBe(
      "/api/v1/agent-market/dev-submissions/sub-1/transition",
    );
    expect(init.method).toBe("POST");
    const body = init.body as FormData;
    expect(body).toBeInstanceOf(FormData);
    expect(body.get("to_state")).toBe("approved");
    expect(body.get("operator_id")).toBe("op-1");
    expect(body.get("reason")).toBe("审核通过");
  });

  it("TC-DC-07 rescanDevSubmission → POST rescan + operator_id", async () => {
    const fetchSpy = mockFetchOk({ report_id: "r2", valid: true });
    await rescanDevSubmission("sub-1", "op-1");
    const init = fetchSpy.mock.calls[0]?.[1] as RequestInit;
    expect(fetchSpy.mock.calls[0]?.[0]).toBe(
      "/api/v1/agent-market/dev-submissions/sub-1/rescan",
    );
    expect(init.method).toBe("POST");
    const body = init.body as FormData;
    expect(body).toBeInstanceOf(FormData);
    expect(body.get("operator_id")).toBe("op-1");
  });

  it("TC-DC-08 getHistory → GET dev-submissions/{submission_id}/history", async () => {
    const fetchSpy = mockFetchOk({ submission_id: "sub-1", history: [] });
    await getHistory("sub-1");
    expect(fetchSpy.mock.calls[0]?.[0]).toBe(
      "/api/v1/agent-market/dev-submissions/sub-1/history",
    );
  });
});

describe("devSubmissionApiClient — 非 2xx 抛错（AC-DEV-22）", () => {
  it("TC-DC-09 提交 409 → 错误消息取自 detail.error_message", async () => {
    mockFetchError(409, {
      detail: {
        error_code: "MARKET_DEV_SUBMISSION_DUPLICATE",
        error_message: "重复提交：alice/demo-node@1.0.0 已存在",
      },
    });
    const file = new File(["zip-bytes"], "pkg.zip");
    await expect(
      submitDevPackage(file, "alice", "demo-node", "1.0.0"),
    ).rejects.toThrow("重复提交：alice/demo-node@1.0.0 已存在");
  });

  it("TC-DC-10 流转 409 → 错误消息取自 detail.error_message", async () => {
    mockFetchError(409, {
      detail: {
        error_code: "MARKET_DEV_INVALID_TRANSITION",
        error_message: "非法状态流转：submitted → published",
      },
    });
    await expect(
      transitionDevSubmission("sub-1", "published", "op-1"),
    ).rejects.toThrow("非法状态流转：submitted → published");
  });

  it("TC-DC-11 流转 500 无 detail → 回退默认文案", async () => {
    mockFetchError(500, {});
    await expect(
      transitionDevSubmission("sub-1", "approved", "op-1"),
    ).rejects.toThrow("HTTP 500");
  });

  it("TC-DC-12 重新校验 500 → 错误消息取自 detail.error_message", async () => {
    mockFetchError(500, {
      detail: {
        error_code: "MARKET_STORAGE_UNAVAILABLE",
        error_message: "解压包目录缺失，无法重新校验",
      },
    });
    await expect(rescanDevSubmission("sub-1", "op-1")).rejects.toThrow(
      "解压包目录缺失，无法重新校验",
    );
  });

  it("TC-DC-13 脚手架清单 502 → 抛错；历史 404 → 抛错；提交清单 500 → 抛错", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}", { status: 502 }));
    await expect(listDevScaffolds()).rejects.toThrow("HTTP 502");

    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}", { status: 404 }));
    await expect(getHistory("no-such")).rejects.toThrow("HTTP 404");

    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}", { status: 500 }));
    await expect(listDevSubmissions()).rejects.toThrow("HTTP 500");
  });
});
