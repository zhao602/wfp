// =============================================================================
// SSE 事件流客户端（src/execution-visualization/ExecutionEventStreamClient.ts）
//
// 职责：订阅后端 SSE 事件流，断线自动重连并拉取实例最新状态进行补偿刷新。
// 设计意图（对应需求规格 5.5.1.6、任务 T20.1）：
//   1. 使用 EventSource API 订阅 /api/v1/instances/{id}/events/stream；
//   2. 断线时自动重连（指数退避），重连后拉取全量事件补偿刷新；
//   3. 对外暴露 onEvent 回调，由上层组件驱动状态更新。
// =============================================================================

import type { ExecutionEvent } from "../api/apiTypes";
import { getDefaultAccessToken } from "../api/httpClient";

/** SSE 客户端配置 */
interface EventStreamClientConfig {
  instanceId: string;
  onEvent: (event: ExecutionEvent) => void;
  onDisconnect?: () => void;
  onReconnect?: () => void;
  /**
   * 重连耗尽信号：仅在 `retryCount >= maxRetries`（重连次数耗尽）时触发一次。
   * 新增 additive 可选回调（默认 no-op），不改变既有 onDisconnect / onReconnect 语义。
   */
  onRetryExhausted?: () => void;
  maxRetries?: number;
}

/** SSE 事件流客户端：管理订阅、断线重连与补偿刷新 */
export class ExecutionEventStreamClient {
  private eventSource: EventSource | null = null;
  private retryCount = 0;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private closed = false;
  /** 是否已上报"重连耗尽"信号（保证仅触发一次，DC-8） */
  private exhaustedNotified = false;
  private readonly config: Required<EventStreamClientConfig>;

  constructor(config: EventStreamClientConfig) {
    this.config = {
      instanceId: config.instanceId,
      onEvent: config.onEvent,
      onDisconnect: config.onDisconnect ?? (() => {}),
      onReconnect: config.onReconnect ?? (() => {}),
      onRetryExhausted: config.onRetryExhausted ?? (() => {}),
      maxRetries: config.maxRetries ?? 10,
    };
  }

  /** 开始订阅 SSE 事件流 */
  connect(): void {
    this.closed = false;
    this.openEventSource();
  }

  /** 关闭订阅，停止重连 */
  disconnect(): void {
    this.closed = true;
    if (this.retryTimer) {
      clearTimeout(this.retryTimer);
      this.retryTimer = null;
    }
    if (this.eventSource) {
      this.eventSource.close();
      this.eventSource = null;
    }
  }

  /** 打开 EventSource 连接 */
  private openEventSource(): void {
    const url = `/api/v1/instances/${this.config.instanceId}/events/stream`;
    this.eventSource = new EventSource(url);

    this.eventSource.onmessage = (messageEvent) => {
      try {
        const event = JSON.parse(messageEvent.data) as ExecutionEvent;
        this.config.onEvent(event);
      } catch {
        // 忽略解析失败的事件
      }
    };

    this.eventSource.onerror = () => {
      if (this.eventSource) {
        this.eventSource.close();
        this.eventSource = null;
      }
      this.config.onDisconnect();
      this.scheduleReconnect();
    };
  }

  /** 安排重连（指数退避：1s, 2s, 4s, ...，上限 30s） */
  private scheduleReconnect(): void {
    if (this.closed) {
      return;
    }
    if (this.retryCount >= this.config.maxRetries) {
      // 重连次数耗尽：上报一次连接异常信号（DC-8/AC-16）；既有重连/补偿算法不变
      if (!this.exhaustedNotified) {
        this.exhaustedNotified = true;
        this.config.onRetryExhausted();
      }
      return;
    }
    this.retryCount++;
    const delay = Math.min(1000 * 2 ** (this.retryCount - 1), 30000);
    this.retryTimer = setTimeout(() => {
      this.config.onReconnect();
      this.compensateAndReconnect();
    }, delay);
  }

  /** 拉取全量事件补偿刷新后重新订阅 */
  private async compensateAndReconnect(): Promise<void> {
    try {
      // token 注入复用（★P-04=A）：SSE 特例不复用 httpClient 请求管线，
      // 仅共享基元导出的默认令牌读取（localStorage + trim 归一 + 容错）
      const token = getDefaultAccessToken();
      // 按需构造 headers：仅当存在非空令牌时才注入 Authorization，避免空 Bearer
      const headers: Record<string, string> = {};
      if (token !== "") {
        headers["Authorization"] = `Bearer ${token}`;
      }
      const res = await fetch(
        `/api/v1/instances/${this.config.instanceId}/events`,
        { headers },
      );
      if (res.ok) {
        const events = (await res.json()) as ExecutionEvent[];
        for (const event of events) {
          this.config.onEvent(event);
        }
      }
    } catch {
      // 补偿刷新失败，继续重连
    }
    this.openEventSource();
  }
}
