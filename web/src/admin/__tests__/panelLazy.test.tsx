// =============================================================================
// 面板惰性加载机制单测（src/admin/__tests__/panelLazy.test.tsx，B2-C2/DC-B2-02）
//
// 留待非沙箱环境执行：cd web && npx vitest run src/admin/__tests__/panelLazy.test.tsx
//
// 覆盖用例：
//   TC-PL-01 未解析槽渲染加载态；解析后渲染面板（订阅通知驱动）
//   TC-PL-02 已解析槽首渲同步直渲（预热/复访语义，零 fallback 闪现）
//   TC-PL-03 加载失败态可辨识（失败卡片 + 错误信息 + 重试入口）
//   TC-PL-04 重试恢复（重置槽 → loader 二次 kick → 面板渲染）
//   TC-PL-05 props 契约透传（GK-14：render 回调调用点类型核对 + 值透传）
//   TC-PL-06 kick 幂等（挂载/手动重复 kick 仅一次 loader 调用）
//   TC-PL-07 卸载退订（解析通知不再触发已卸载组件更新，无泄漏）
// =============================================================================

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, act } from "@testing-library/react";
import {
  createLazySlot,
  loadLazySlot,
  resetLazySlot,
  LazyPanel,
} from "../panelLazy";

/** 受控延迟对象（手动 resolve/reject 驱动槽状态机迁移） */
function deferred<T>(): {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (error: unknown) => void;
} {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

/** 占位面板组件工厂 */
function makePanel(testId: string): () => JSX.Element {
  return (): JSX.Element => <div data-testid={testId} />;
}

describe("panelLazy 惰性加载机制", () => {
  beforeEach(() => {
    vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  // TC-PL-01 未解析槽渲染加载态；解析后渲染面板
  it("加载态占位 → 解析后经订阅通知渲染面板", async () => {
    const pending = deferred<() => JSX.Element>();
    const loader = vi.fn(() => pending.promise);
    const slot = createLazySlot(loader);

    render(<LazyPanel slot={slot} render={(Panel) => <Panel />} />);
    expect(screen.getByText("面板加载中…")).toBeInTheDocument();
    expect(loader).toHaveBeenCalledTimes(1);

    await act(async () => {
      pending.resolve(makePanel("pl-01-body"));
    });
    expect(screen.getByTestId("pl-01-body")).toBeInTheDocument();
    expect(screen.queryByText("面板加载中…")).toBeNull();
  });

  // TC-PL-02 已解析槽首渲同步直渲（预热/复访语义）
  it("已解析槽首渲同步直渲，零 fallback 闪现", async () => {
    const loader = vi.fn(() => Promise.resolve(makePanel("pl-02-warm")));
    const slot = createLazySlot(loader);
    loadLazySlot(slot);
    await act(async () => {});

    render(<LazyPanel slot={slot} render={(Panel) => <Panel />} />);
    expect(screen.getByTestId("pl-02-warm")).toBeInTheDocument();
    expect(screen.queryByText("面板加载中…")).toBeNull();
  });

  // TC-PL-03 加载失败态可辨识
  it("loader 拒绝 → 失败卡片含错误信息与重试入口", async () => {
    const loader = vi.fn((): Promise<() => JSX.Element> =>
      Promise.reject(new Error("面板 chunk 拉取失败")),
    );
    const slot = createLazySlot(loader);

    render(<LazyPanel slot={slot} render={(Panel) => <Panel />} />);
    await act(async () => {});

    expect(screen.getByText("面板加载失败")).toBeInTheDocument();
    expect(screen.getByText(/面板 chunk 拉取失败/)).toBeInTheDocument();
    expect(screen.getByText("重试加载")).toBeInTheDocument();
  });

  // TC-PL-04 重试恢复
  it("失败后重试 → 槽重置并二次 kick → 面板渲染", async () => {
    const loader = vi
      .fn<() => Promise<() => JSX.Element>>()
      .mockRejectedValueOnce(new Error("首次失败"))
      .mockResolvedValue(makePanel("pl-04-recovered"));
    const slot = createLazySlot(loader);

    render(<LazyPanel slot={slot} render={(Panel) => <Panel />} />);
    await act(async () => {});
    expect(screen.getByText("面板加载失败")).toBeInTheDocument();

    await act(async () => {
      screen.getByText("重试加载").click();
    });
    expect(loader).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId("pl-04-recovered")).toBeInTheDocument();
    expect(screen.queryByText("面板加载失败")).toBeNull();
  });

  // TC-PL-05 props 契约透传
  it("render 回调透传面板 props（GK-14 契约零改动）", async () => {
    const PanelWithProps = (props: {
      token: string;
      tenantId: string;
    }): JSX.Element => (
      <div data-testid="pl-05-props">
        {props.token}|{props.tenantId}
      </div>
    );
    const loader = vi.fn(() => Promise.resolve(PanelWithProps));
    const slot = createLazySlot(loader);
    loadLazySlot(slot);
    await act(async () => {});

    render(
      <LazyPanel
        slot={slot}
        render={(Panel) => <Panel token="tk-1" tenantId="tn-1" />}
      />,
    );
    expect(screen.getByTestId("pl-05-props")).toHaveTextContent("tk-1|tn-1");
  });

  // TC-PL-06 kick 幂等
  it("重复 loadLazySlot 幂等（仅一次 loader 调用）", () => {
    const loader = vi.fn(() => new Promise<() => JSX.Element>(() => {}));
    const slot = createLazySlot(loader);

    loadLazySlot(slot);
    loadLazySlot(slot);
    render(<LazyPanel slot={slot} render={(Panel) => <Panel />} />);

    expect(loader).toHaveBeenCalledTimes(1);
  });

  // TC-PL-07 卸载退订
  it("卸载后退订：解析通知不触发已卸载组件更新（无泄漏）", async () => {
    const pending = deferred<() => JSX.Element>();
    const loader = vi.fn(() => pending.promise);
    const slot = createLazySlot(loader);

    const { unmount } = render(
      <LazyPanel slot={slot} render={(Panel) => <Panel />} />,
    );
    unmount();

    await act(async () => {
      pending.resolve(makePanel("pl-07-after-unmount"));
    });
    // 卸载后解析：无异常、无残留监听（listeners 已清）
    expect(slot.listeners.size).toBe(0);
    expect(slot.status).toBe("resolved");
  });

  // resetLazySlot 状态机回归
  it("resetLazySlot 重置至 idle，可重新 kick", async () => {
    const loader = vi
      .fn<() => Promise<() => JSX.Element>>()
      .mockResolvedValueOnce(makePanel("pl-08-first"))
      .mockResolvedValueOnce(makePanel("pl-08-second"));
    const slot = createLazySlot(loader);

    loadLazySlot(slot);
    await act(async () => {});
    expect(slot.status).toBe("resolved");

    resetLazySlot(slot);
    expect(slot.status).toBe("idle");
    expect(slot.value).toBeUndefined();

    loadLazySlot(slot);
    await act(async () => {});
    expect(slot.status).toBe("resolved");
    expect(loader).toHaveBeenCalledTimes(2);
  });
});
