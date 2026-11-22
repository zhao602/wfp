// =============================================================================
// 画布历史快照栈 hook（src/canvas/useCanvasHistory.ts）
//
// 职责：自建撤销/重做快照栈（对应设计 2.2.2 接口 I2、决策 DC-04、任务 T13）。
// 设计意图：
//   1. 零外部撤销库（NFR-018，零新增依赖 INV-D02）；泛型化 <T> 预留节点删除共用（OP-004）；
//   2. past/future 双栈用 useRef 存储（非 React state，避免高频重渲染，NFR-004/DC-04 决策 6）；
//   3. canUndo/canRedo 用布尔 state 驱动入口可用性（NFR-010）；
//   4. 容量默认 HISTORY_LIMIT = 50，溢出丢弃最旧快照（OP-007/NFR-004）；
//   5. push/undo/redo 为纯栈操作，可从组件同步段调用；禁止在 setCanvasState updater 内调用
//      （INV-D09，StrictMode 幂等）；push 清空 redo 分支（AC-019）；
//   6. 空栈 undo/redo 返回 undefined（调用侧安全返回，AC-017/异常场景 6）；
//      快照为不可变引用，原样入栈/出栈（INV-D08 逐字段可逆的前提）。
// =============================================================================

import { useCallback, useMemo, useRef, useState } from "react";

/** 历史栈容量默认值（OP-007）：超出丢弃最旧快照，防内存膨胀（NFR-004） */
export const HISTORY_LIMIT = 50;

/** 画布历史接口（设计 2.2.2 接口 I2） */
export interface CanvasHistory<T> {
  /** 以「变更前快照」入栈；同时清空 redo 分支（AC-019） */
  push: (snapshot: T) => void;
  /** 回退：返回上一快照（无可用则返回 undefined，AC-017 幂等安全） */
  undo: (current: T) => T | undefined;
  /** 前进：返回 redo 快照（无可用则 undefined） */
  redo: (current: T) => T | undefined;
  /** 可回退标识（驱动入口可用性，NFR-010） */
  canUndo: boolean;
  /** 可前进标识 */
  canRedo: boolean;
}

/**
 * 自建快照栈 hook：past/future 双 useRef 栈 + canUndo/canRedo 布尔 state。
 * 栈操作仅写 useRef（入栈不依赖 React 状态提交，INV-D09/DC-04 决策 6）；
 * 返回对象经 useMemo 稳定化（canUndo/canRedo 变化外保持同一引用，减小调用方依赖抖动）。
 */
export function useCanvasHistory<T>(limit: number = HISTORY_LIMIT): CanvasHistory<T> {
  const pastRef = useRef<T[]>([]);
  const futureRef = useRef<T[]>([]);
  const [canUndo, setCanUndo] = useState(false);
  const [canRedo, setCanRedo] = useState(false);

  /** 入栈：变更前快照压入 past；溢出丢最旧；清空 future（AC-019） */
  const push = useCallback(
    (snapshot: T) => {
      const past = pastRef.current;
      past.push(snapshot);
      if (past.length > limit) {
        // 容量溢出：丢弃最旧快照（OP-007）
        past.splice(0, past.length - limit);
      }
      futureRef.current = [];
      setCanUndo(past.length > 0);
      setCanRedo(false);
    },
    [limit],
  );

  /** 回退：弹出 past 栈顶、current 压入 future；空栈返回 undefined（AC-017） */
  const undo = useCallback((current: T): T | undefined => {
    const past = pastRef.current;
    const snapshot = past.pop();
    if (snapshot === undefined) {
      return undefined; // 空栈安全返回，不抛错（异常场景 6）
    }
    futureRef.current.push(current);
    setCanUndo(past.length > 0);
    setCanRedo(true);
    return snapshot;
  }, []);

  /** 前进：弹出 future 栈顶、current 压入 past；空栈返回 undefined */
  const redo = useCallback((current: T): T | undefined => {
    const future = futureRef.current;
    const snapshot = future.pop();
    if (snapshot === undefined) {
      return undefined;
    }
    pastRef.current.push(current);
    setCanUndo(true);
    setCanRedo(future.length > 0);
    return snapshot;
  }, []);

  return useMemo<CanvasHistory<T>>(
    () => ({ push, undo, redo, canUndo, canRedo }),
    [push, undo, redo, canUndo, canRedo],
  );
}
