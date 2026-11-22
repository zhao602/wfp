// =============================================================================
// 画布历史快照栈测试（canvas/__tests__/useCanvasHistory.test.ts）
//
// 留待非沙箱环境执行：cd web && npx vitest run
//
// 覆盖用例（add_edge_delete / CHG-E16，HIST-01~04，对应任务 T19.1~T19.4）：
//   HIST-01 push/undo/redo 正确性：快照逐字段等同（FR-010/FR-011；AC-016/AC-018；INV-008/INV-D08）
//   HIST-02 容量溢出丢最旧：past 恰为 HISTORY_LIMIT(50)、最旧丢弃最新保留（OP-007；NFR-004）
//   HIST-03 新变更清空 redo（AC-019）；空栈 undo/redo 返回 undefined 不抛错（异常场景 6）
//   HIST-04 StrictMode 包裹下 push 一次恰入栈一份（幂等）（spec §13.1-10；INV-D09）
//
// 测试策略：renderHook 驱动真实 useCanvasHistory（泛型以极简 { tag } 快照驱动）；
// 每次断言前经 result.current 重取（canUndo/canRedo 为 state，避免陈旧引用）。
//
// 沙箱限制声明：本文件作为交付物入库，请在非沙箱环境执行。
// =============================================================================

import { describe, it, expect } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { StrictMode, createElement } from "react";
import { useCanvasHistory, HISTORY_LIMIT } from "../useCanvasHistory";

/** 最小快照形状：hook 泛型化（T13.1），以极简对象驱动 */
interface Snap {
  tag: string;
}

describe("useCanvasHistory 快照栈", () => {
  // HIST-01（T19.1）：push 后可 undo 取回、undo 后可 redo 前进，快照逐字段等同。
  it("HIST-01 push 后可 undo 取回、undo 后可 redo 前进，快照逐字段等同（AC-016/AC-018）", () => {
    const { result } = renderHook(() => useCanvasHistory<Snap>(50));
    const s0: Snap = { tag: "v0" };
    const s1: Snap = { tag: "v1" };
    const s2: Snap = { tag: "v2" };

    act(() => {
      result.current.push(s0);
    });
    expect(result.current.canUndo).toBe(true);
    expect(result.current.canRedo).toBe(false);

    let restored: Snap | undefined = undefined;
    act(() => {
      restored = result.current.undo(s1);
    });
    expect(restored).toEqual(s0); // 逐字段等同（AC-016）
    expect(result.current.canUndo).toBe(false);
    expect(result.current.canRedo).toBe(true);

    act(() => {
      restored = result.current.redo(s2);
    });
    expect(restored).toEqual(s1); // 前进到撤销时的状态（AC-018）
    expect(result.current.canRedo).toBe(false);
    expect(result.current.canUndo).toBe(true);
  });

  // HIST-02（T19.2）：连续 push 超过 HISTORY_LIMIT(50) → past 不超过 50、最旧被丢弃、最新保留。
  it("HIST-02 连续 push 超过 HISTORY_LIMIT(50) → 丢弃最旧、最新保留（OP-007）", () => {
    const { result } = renderHook(() => useCanvasHistory<Snap>(HISTORY_LIMIT));
    for (let i = 0; i < HISTORY_LIMIT + 2; i += 1) {
      const snap: Snap = { tag: `v${i}` };
      act(() => {
        result.current.push(snap);
      });
    }
    // 依序回退：最先弹出最新 v51，最后弹出最旧保留的 v2（v0/v1 已被丢弃），随后 undefined
    let popped = 0;
    let first: Snap | undefined = undefined;
    let last: Snap | undefined = undefined;
    for (let i = 0; i < HISTORY_LIMIT + 5; i += 1) {
      let restored: Snap | undefined = undefined;
      act(() => {
        restored = result.current.undo({ tag: "current" });
      });
      if (restored === undefined) {
        break;
      }
      if (popped === 0) {
        first = restored;
      }
      popped += 1;
      last = restored;
    }
    expect(popped).toBe(HISTORY_LIMIT); // past 长度恰为 50，不超过上限
    expect(first).toEqual({ tag: "v51" }); // 最新保留、最先弹出
    expect(last).toEqual({ tag: "v2" }); // 最旧被丢弃（v0/v1）、v2 成为最旧保留
  });

  // HIST-03（T19.3）：undo 后新变更 push → redo 不可用（future 已清空）；空栈安全返回。
  it("HIST-03 新变更清空 redo；空栈 undo/redo 返回 undefined 不抛错（AC-019）", () => {
    const { result } = renderHook(() => useCanvasHistory<Snap>(50));
    let restored: Snap | undefined = undefined;

    // 空栈安全：不抛错、返回 undefined
    expect(() => {
      act(() => {
        restored = result.current.undo({ tag: "cur" });
      });
    }).not.toThrow();
    expect(restored).toBeUndefined();
    expect(() => {
      act(() => {
        restored = result.current.redo({ tag: "cur" });
      });
    }).not.toThrow();
    expect(restored).toBeUndefined();

    // undo 后新变更 push → future 清空（AC-019）
    act(() => {
      result.current.push({ tag: "a" });
    });
    act(() => {
      restored = result.current.undo({ tag: "cur" });
    });
    expect(restored).toEqual({ tag: "a" });
    act(() => {
      result.current.push({ tag: "b" }); // 新变更
    });
    expect(result.current.canRedo).toBe(false);
    act(() => {
      restored = result.current.redo({ tag: "cur2" });
    });
    expect(restored).toBeUndefined(); // redo 分支已被清空
  });

  // HIST-04（T19.4）：StrictMode 包裹渲染，调用一次 push → 恰入栈一份（幂等）。
  it("HIST-04 StrictMode 包裹下调用一次 push → 恰入栈一份（INV-D09）", () => {
    const { result } = renderHook(() => useCanvasHistory<Snap>(50), {
      // 文件名约定为 .ts（T19/T20.1）：以 createElement 表达 StrictMode 包裹（.ts 不支持 JSX）
      wrapper: ({ children }) => createElement(StrictMode, null, children),
    });
    const snap: Snap = { tag: "only" };
    act(() => {
      result.current.push(snap);
    });
    let restored: Snap | undefined = undefined;
    act(() => {
      restored = result.current.undo({ tag: "cur" });
    });
    expect(restored).toEqual(snap); // 第一份即该快照
    act(() => {
      restored = result.current.undo({ tag: "cur2" });
    });
    expect(restored).toBeUndefined(); // 恰 1 份：StrictMode 下无重复入栈
  });
});
