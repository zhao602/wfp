// =============================================================================
// 画布持久化防抖器（src/canvas/persistenceSaver.ts，B2-C3/DC-B2-03）
//
// 职责：createDebouncedSaver——trailing 去抖落盘（默认 300ms 静默期，latest-wins）；
// flush 显式落盘 pending；dispose 丢弃 pending 并停用；clock/sleep 可注入
// （V 类机械验证，测试免真实定时器）。
// 设计意图：
//   1. trailing 去抖：连续 schedule 仅最后一次载荷在静默期后落盘一次；
//   2. flush：pending 立即落盘（卸载/visibilitychange→hidden/beforeunload 三处触发，
//      见 App.tsx 接线）；
//   3. dispose：丢弃 pending 并停用（后续 schedule/flush 均为 no-op）；
//   4. 纯 TS 无 React 依赖（App 经 useRef 持有实例，会话级单例语义）；
//   5. write 回调不抛错由调用方保证（App 侧 try/catch 静默既有行为）。
// =============================================================================

/** 防抖器选项 */
export interface DebouncedSaverOptions {
  /** 静默期毫秒数（默认 300） */
  delayMs?: number;
  /** 时钟注入（默认 Date.now）——测试注入假时钟 */
  clock?: () => number;
  /** 睡眠注入（默认 setTimeout Promise）——测试注入假 sleep */
  sleep?: (ms: number) => Promise<void>;
}

/** 防抖落盘器 */
export interface DebouncedSaver<T> {
  /** 调度落盘（latest-wins：连续调用以最后一次为准，静默期后落盘） */
  schedule(data: T): void;
  /** 立即落盘 pending（无 pending 零动作） */
  flush(): void;
  /** 丢弃 pending 并停用（后续 schedule/flush 零动作） */
  dispose(): void;
}

/**
 * 创建防抖落盘器：trailing 去抖（静默期内连续调度仅最后一次落盘），
 * flush/dispose 显式控制；clock/sleep 可注入。
 */
export function createDebouncedSaver<T>(
  write: (data: T) => void,
  options: DebouncedSaverOptions = {},
): DebouncedSaver<T> {
  const delayMs = options.delayMs ?? 300;
  const clock = options.clock ?? ((): number => Date.now());
  const sleep =
    options.sleep ??
    ((ms: number): Promise<void> =>
      new Promise<void>((resolve) => {
        setTimeout(resolve, ms);
      }));

  /** pending 载荷（盒装以区分「无 pending」与值为 undefined 的载荷） */
  let pending: { value: T } | undefined;
  /** 当前静默期截止时刻（clock 时基） */
  let deadline = 0;
  /** 去抖循环进行中标记（防多循环并发） */
  let looping = false;
  /** 停用标记（dispose 后永久 no-op） */
  let disposed = false;

  /** 落盘 pending（无则零动作） */
  const writePending = (): void => {
    if (pending === undefined) {
      return;
    }
    const data = pending.value;
    pending = undefined;
    write(data);
  };

  /** 去抖循环：睡至静默期截止后落盘；期间新调度顺延截止时刻（trailing） */
  const loop = async (): Promise<void> => {
    looping = true;
    try {
      while (!disposed && pending !== undefined) {
        const remaining = deadline - clock();
        if (remaining > 0) {
          try {
            await sleep(remaining);
          } catch {
            // sleep 注入异常：退出循环（looping 复位），待下次 schedule 重启
            return;
          }
          continue;
        }
        writePending();
      }
    } finally {
      looping = false;
    }
  };

  return {
    schedule(data: T): void {
      if (disposed) {
        return;
      }
      pending = { value: data };
      deadline = clock() + delayMs;
      if (!looping) {
        void loop();
      }
    },
    flush(): void {
      if (disposed) {
        return;
      }
      writePending();
    },
    dispose(): void {
      disposed = true;
      pending = undefined;
    },
  };
}
