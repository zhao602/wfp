// =============================================================================
// SSE 重连耗尽回调单元测试（execution-visualization/__tests__/ExecutionEventStreamClient.retryExhausted.test.ts）
//
// 留待非沙箱环境执行：vitest run src/execution-visualization/__tests__/ExecutionEventStreamClient.retryExhausted.test.ts
//
// 覆盖（CHG-5 / CHG-2 / DC-8 / AC-23）：
//   - 重连次数耗尽（retryCount >= maxRetries）时 onRetryExhausted 触发且仅一次
//   - 未耗尽时不触发
//   - 既有 ExecutionEventStreamClient.auth.test.ts 不受影响（本文件独立，不修改既有测试）
//
// 触发方式：mock EventSource 构造函数模拟 onerror → scheduleReconnect → 耗尽分支
// =============================================================================

import { describe, it, expect, vi, afterEach } from "vitest";
import { ExecutionEventStreamClient } from "../ExecutionEventStreamClient";

/** 模拟 EventSource：onerror 可在外部触发 */
class MockEventSource {
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  constructor(public readonly url: string) {}
  close() {}
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("ExecutionEventStreamClient.onRetryExhausted — 重连耗尽信号", () => {
  it("重连次数耗尽时触发一次", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockReturnValue(null);
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("[]", { status: 200 }));
    vi.stubGlobal("EventSource", MockEventSource);
    vi.useFakeTimers();

    const onRetryExhausted = vi.fn();
    const client = new ExecutionEventStreamClient({
      instanceId: "i1",
      onEvent: () => {},
      maxRetries: 2,
      onRetryExhausted,
    });
    client.connect();

    // 连续触发 onerror：每次 onerror → scheduleReconnect（超出上限后进入耗尽分支）
    for (let i = 0; i < 5; i++) {
      const es = (client as unknown as { eventSource: MockEventSource | null }).eventSource;
      if (!es) {
        break; // DC-8：重连耗尽后组件关闭连接（eventSource 置空）且不再重连
      }
      es.onerror?.(new Event("error"));
      await vi.advanceTimersByTimeAsync(30000);
    }
    // 耗尽后连接已放弃：eventSource 为 null 且耗尽信号仅触发一次
    expect((client as unknown as { eventSource: MockEventSource | null }).eventSource).toBeNull();

    expect(onRetryExhausted).toHaveBeenCalledTimes(1);
    client.disconnect();
  });

  it("未耗尽时不触发", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockReturnValue(null);
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("[]", { status: 200 }));
    vi.stubGlobal("EventSource", MockEventSource);
    vi.useFakeTimers();

    const onRetryExhausted = vi.fn();
    const client = new ExecutionEventStreamClient({
      instanceId: "i1",
      onEvent: () => {},
      maxRetries: 10,
      onRetryExhausted,
    });
    client.connect();

    const es = (client as unknown as { eventSource: MockEventSource }).eventSource;
    es.onerror?.(new Event("error"));
    await vi.advanceTimersByTimeAsync(1000);

    expect(onRetryExhausted).not.toHaveBeenCalled();
    client.disconnect();
  });
});