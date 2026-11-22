// =============================================================================
// SSE 补偿刷新鉴权头单元测试（execution-visualization/__tests__/ExecutionEventStreamClient.auth.test.ts）
//
// 留待非沙箱环境执行：vitest run src/execution-visualization/__tests__/ExecutionEventStreamClient.auth.test.ts
//
// 覆盖：
//   compensateAndReconnect() 中 token 读取与 headers 按需构造
//   - 有非空 token → fetch headers 含 Authorization: Bearer <token>
//   - 无 token → fetch headers 为 {}，不含 Authorization 键
//   - openEventSource() 在补偿刷新后仍被调用（行为不变）
//
// 触发方式：mock EventSource 构造函数模拟 onerror → scheduleReconnect → compensateAndReconnect
// =============================================================================

import { describe, it, expect, vi, afterEach } from "vitest";
import { ExecutionEventStreamClient } from "../ExecutionEventStreamClient";
import type { ExecutionEvent } from "../../api/apiTypes";

/** 构造最小可用配置 */
function makeConfig(onEvent: (e: ExecutionEvent) => void) {
  return {
    instanceId: "test-instance",
    onEvent,
    maxRetries: 3,
  };
}

/** 模拟 EventSource：onerror 可在外部触发 */
class MockEventSource {
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  constructor(public readonly url: string) {}
  close() {}
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("compensateAndReconnect — SSE 补偿刷新鉴权头", () => {
  it("有非空令牌时 fetch headers 含 Authorization", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockReturnValue("sse-token-xyz");
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response("[]", { status: 200 }));
    vi.stubGlobal("EventSource", MockEventSource);
    vi.useFakeTimers();

    const client = new ExecutionEventStreamClient(makeConfig(() => {}));
    client.connect();

    // 触发 onerror → scheduleReconnect → setTimeout → compensateAndReconnect
    const es = (client as unknown as { eventSource: MockEventSource }).eventSource;
    es.onerror?.(new Event("error"));
    await vi.advanceTimersByTimeAsync(1000);

    const callArgs = fetchSpy.mock.calls[0];
    const headers = callArgs?.[1]?.headers as Record<string, string>;
    expect(headers["Authorization"]).toBe("Bearer sse-token-xyz");

    client.disconnect();
    vi.useRealTimers();
  });

  it("无令牌时 fetch headers 不含 Authorization 键", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockReturnValue(null);
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response("[]", { status: 200 }));
    vi.stubGlobal("EventSource", MockEventSource);
    vi.useFakeTimers();

    const client = new ExecutionEventStreamClient(makeConfig(() => {}));
    client.connect();

    const es = (client as unknown as { eventSource: MockEventSource }).eventSource;
    es.onerror?.(new Event("error"));
    await vi.advanceTimersByTimeAsync(1000);

    const callArgs = fetchSpy.mock.calls[0];
    const headers = callArgs?.[1]?.headers as Record<string, string>;
    expect(headers).not.toHaveProperty("Authorization");
    expect(JSON.stringify(headers)).not.toContain("Bearer ");

    client.disconnect();
    vi.useRealTimers();
  });

  it("补偿刷新后 openEventSource 仍被调用（行为不变）", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockReturnValue(null);
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("[]", { status: 200 }));
    const esInstances: MockEventSource[] = [];
    vi.stubGlobal("EventSource", class extends MockEventSource {
      constructor(url: string) {
        super(url);
        esInstances.push(this);
      }
    });
    vi.useFakeTimers();

    const client = new ExecutionEventStreamClient(makeConfig(() => {}));
    client.connect();
    expect(esInstances).toHaveLength(1); // connect → openEventSource

    const es = (client as unknown as { eventSource: MockEventSource }).eventSource;
    es.onerror?.(new Event("error"));
    await vi.advanceTimersByTimeAsync(1000);
    expect(esInstances).toHaveLength(2); // compensateAndReconnect → openEventSource 再次调用

    client.disconnect();
    vi.useRealTimers();
  });
});