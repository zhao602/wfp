// =============================================================================
// 画布持久化防抖器单测（src/canvas/__tests__/persistenceSaver.test.ts，B2-C3/DC-B2-03）
//
// 留待非沙箱环境执行：cd web && npx vitest run src/canvas/__tests__/persistenceSaver.test.ts
//
// 覆盖用例：
//   TC-PS-01 trailing 去抖：静默期连续调度仅最后一次落盘（latest-wins 单次写）
//   TC-PS-02 静默期内再调度顺延截止（滑动窗口，非固定节流）
//   TC-PS-03 flush 立即落盘且清 pending（到期后不重复写）
//   TC-PS-04 flush 无 pending 零动作
//   TC-PS-05 dispose 丢弃 pending 并停用（后续 schedule/flush 均为 no-op）
//   TC-PS-06 自定义 delayMs 生效
//   TC-PS-07 默认参数路径（真实 setTimeout + Date.now，vi 假定时器机械验证）
// =============================================================================

import { describe, it, expect, vi } from "vitest";
import { createDebouncedSaver } from "../persistenceSaver";

/** 假时钟/假 sleep：手动唤醒（advance 推进时间并唤醒挂起循环，确定性无忙等） */
function createFakeTime(): {
  clock: () => number;
  sleep: (ms: number) => Promise<void>;
  advance: (ms: number) => Promise<void>;
} {
  let now = 0;
  let wake: (() => void) | undefined;
  const clock = (): number => now;
  const sleep = (_ms: number): Promise<void> =>
    new Promise<void>((resolve) => {
      wake = resolve;
    });
  const advance = async (ms: number): Promise<void> => {
    now += ms;
    const w = wake;
    wake = undefined;
    if (w) {
      w();
    }
    // 两轮微任务冲刷：循环醒后完成「检查→写/再睡」一轮
    await Promise.resolve();
    await Promise.resolve();
  };
  return { clock, sleep, advance };
}

describe("createDebouncedSaver 防抖落盘器", () => {
  // TC-PS-01 trailing 去抖
  it("静默期连续调度仅最后一次落盘（latest-wins 单次写）", async () => {
    const writes: string[] = [];
    const time = createFakeTime();
    const saver = createDebouncedSaver<string>((d) => writes.push(d), {
      clock: time.clock,
      sleep: time.sleep,
    });

    saver.schedule("a");
    saver.schedule("b");
    expect(writes).toEqual([]);

    await time.advance(300);
    expect(writes).toEqual(["b"]);
  });

  // TC-PS-02 滑动窗口（再调度顺延截止，非固定节流）
  it("静默期内再调度顺延截止时刻", async () => {
    const writes: string[] = [];
    const time = createFakeTime();
    const saver = createDebouncedSaver<string>((d) => writes.push(d), {
      clock: time.clock,
      sleep: time.sleep,
    });

    saver.schedule("a"); // t=0，截止 t=300
    await time.advance(200); // t=200，未到期，循环续睡
    saver.schedule("b"); // t=200，截止顺延至 t=500
    await time.advance(100); // t=300：旧截止已到但新截止未到，不落盘
    expect(writes).toEqual([]);

    await time.advance(200); // t=500：到达新截止，落盘 b
    expect(writes).toEqual(["b"]);
  });

  // TC-PS-03 flush 立即落盘且清 pending
  it("flush 同步落盘 pending，到期后不重复写", async () => {
    const writes: string[] = [];
    const time = createFakeTime();
    const saver = createDebouncedSaver<string>((d) => writes.push(d), {
      clock: time.clock,
      sleep: time.sleep,
    });

    saver.schedule("a");
    saver.flush();
    expect(writes).toEqual(["a"]);

    await time.advance(300); // 挂起循环醒来：pending 已清，退出不写
    expect(writes).toEqual(["a"]);
  });

  // TC-PS-04 flush 无 pending 零动作
  it("flush 无 pending 时零动作", () => {
    const writes: string[] = [];
    const time = createFakeTime();
    const saver = createDebouncedSaver<string>((d) => writes.push(d), {
      clock: time.clock,
      sleep: time.sleep,
    });

    saver.flush();
    expect(writes).toEqual([]);
  });

  // TC-PS-05 dispose 停用
  it("dispose 丢弃 pending 并停用，后续 schedule/flush 均 no-op", async () => {
    const writes: string[] = [];
    const time = createFakeTime();
    const saver = createDebouncedSaver<string>((d) => writes.push(d), {
      clock: time.clock,
      sleep: time.sleep,
    });

    saver.schedule("a");
    saver.dispose();
    await time.advance(300);
    expect(writes).toEqual([]);

    saver.schedule("b");
    saver.flush();
    expect(writes).toEqual([]);
  });

  // TC-PS-06 自定义 delayMs
  it("自定义 delayMs 生效（50ms 静默期边界）", async () => {
    const writes: string[] = [];
    const time = createFakeTime();
    const saver = createDebouncedSaver<string>((d) => writes.push(d), {
      delayMs: 50,
      clock: time.clock,
      sleep: time.sleep,
    });

    saver.schedule("a");
    await time.advance(49);
    expect(writes).toEqual([]);

    await time.advance(1);
    expect(writes).toEqual(["a"]);
  });

  // TC-PS-07 默认参数路径（vi 假定时器机械验证）
  it("默认参数（300ms + setTimeout + Date.now）路径可用", async () => {
    vi.useFakeTimers();
    try {
      const writes: string[] = [];
      const saver = createDebouncedSaver<string>((d) => writes.push(d));

      saver.schedule("a");
      saver.schedule("b");
      expect(writes).toEqual([]);

      await vi.advanceTimersByTimeAsync(300);
      expect(writes).toEqual(["b"]);
    } finally {
      vi.useRealTimers();
    }
  });
});
