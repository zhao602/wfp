// =============================================================================
// 面板惰性加载机制（src/admin/panelLazy.tsx，B2-C2/DC-B2-02）
//
// 职责：治理/管理面板按需动态 import（每面板一 chunk）+ 加载态/失败态 UI。
// 设计意图：
//   1. 每面板一个模块级 LazySlot：loader 惰性 kick，解析结果模块级缓存
//      （会话内二次进入零重复请求，同步直渲零 fallback 闪现）；
//   2. 未解析槽渲染轻量加载态；已解析槽同步渲染（预热/复访零等待）；
//   3. 加载失败可辨识（失败卡片 + 错误信息 + 重试重新 kick），禁静默白屏；
//   4. 面板渲染期错误不在此拦截——继续上抛至 AdminErrorBoundary
//      （既有语义契约：AdminConsole.market.test TC-ACM-03 兜底文案）；
//   5. 相比 React.lazy 的实现偏差：React 18 lazy 首渲必异步挂起重试，
//      无法满足既有测试同步断言（零修改金标准）；本机制 chunk 语义等价
//      （每面板独立动态 import），且已解析槽支持同步渲染。
// =============================================================================

import { useCallback, useEffect, useState, type JSX } from "react";
import { useThemeMode, darkTokens } from "../theme/themeMode";

/** 惰性槽状态机：idle（未 kick）→ pending（加载中）→ resolved/rejected */
export type LazySlotStatus = "idle" | "pending" | "resolved" | "rejected";

/** 面板惰性槽：模块级单例，承载加载状态机与解析结果 */
export interface LazySlot<T> {
  /** 面板模块动态 import 函数（每面板一 chunk） */
  readonly loader: () => Promise<T>;
  /** 当前状态 */
  status: LazySlotStatus;
  /** 解析结果（status=resolved 时有效） */
  value?: T;
  /** 拒绝原因（status=rejected 时有效） */
  error?: unknown;
  /** 解析/拒绝通知订阅者（组件重渲染触发器） */
  readonly listeners: Set<() => void>;
}

/** 创建惰性槽（不 kick；loader 首次 loadLazySlot 时调用） */
export function createLazySlot<T>(loader: () => Promise<T>): LazySlot<T> {
  return { loader, status: "idle", listeners: new Set<() => void>() };
}

/** kick 惰性槽（幂等：非 idle 状态零动作） */
export function loadLazySlot<T>(slot: LazySlot<T>): void {
  if (slot.status !== "idle") {
    return;
  }
  slot.status = "pending";
  slot
    .loader()
    .then((value: T) => {
      slot.value = value;
      slot.status = "resolved";
      notifyLazySlot(slot);
    })
    .catch((error: unknown) => {
      slot.error = error;
      slot.status = "rejected";
      notifyLazySlot(slot);
    });
}

/** 重置惰性槽至 idle（重试路径：重置后须再次 loadLazySlot 重新 kick） */
export function resetLazySlot<T>(slot: LazySlot<T>): void {
  slot.status = "idle";
  slot.value = undefined;
  slot.error = undefined;
  notifyLazySlot(slot);
}

/** 通知全部订阅者（解析/拒绝/重置时触发组件重渲染） */
function notifyLazySlot<T>(slot: LazySlot<T>): void {
  slot.listeners.forEach((listener: () => void) => {
    listener();
  });
}

/** 订阅槽变更；返回退订函数（组件卸载防泄漏） */
export function subscribeLazySlot<T>(
  slot: LazySlot<T>,
  listener: () => void,
): () => void {
  slot.listeners.add(listener);
  return () => {
    slot.listeners.delete(listener);
  };
}

// -----------------------------------------------------------------------------
// 加载态 / 失败态 UI
// -----------------------------------------------------------------------------

/** 加载态：轻量占位（浅深双轨内联样式，与 AdminErrorBoundary 卡片风格对齐） */
function PanelFallback({ isDark }: { isDark: boolean }): JSX.Element {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        minHeight: "180px",
        fontSize: "13px",
        color: isDark ? darkTokens.text.hint : "#9e9e9e",
        userSelect: "none",
      }}
    >
      面板加载中…
    </div>
  );
}

/** 失败态属性 */
interface PanelLoadErrorProps {
  /** 拒绝原因（错误信息展示） */
  error: unknown;
  /** 深色模式标记 */
  isDark: boolean;
  /** 重试回调（重置并重新 kick 槽） */
  onRetry: () => void;
}

/** 失败态：可辨识加载失败卡片（错误信息 + 重试入口 + 返回画布提示） */
function PanelLoadError({ error, isDark, onRetry }: PanelLoadErrorProps): JSX.Element {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        width: "100%",
        minHeight: "240px",
      }}
    >
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: "10px",
          maxWidth: "420px",
          padding: "24px 28px",
          border: `1px solid ${isDark ? darkTokens.surface.divider : "#d0d0d0"}`,
          borderRadius: "8px",
          backgroundColor: isDark ? darkTokens.surface.bgHover : "#ffffff",
          fontSize: "13px",
          color: isDark ? darkTokens.text.secondary : "#666666",
        }}
      >
        <div
          style={{
            fontSize: "14px",
            fontWeight: 600,
            color: isDark ? darkTokens.text.primary : "#1f1f1f",
          }}
        >
          面板加载失败
        </div>
        <div style={{ lineHeight: 1.5, wordBreak: "break-all" }}>
          {error instanceof Error
            ? `面板模块加载时发生错误：${error.message}`
            : "面板模块加载时发生未知错误。"}
        </div>
        <button
          onClick={onRetry}
          style={{
            fontFamily: "inherit",
            fontSize: "13px",
            fontWeight: 500,
            padding: "6px 18px",
            border: `1px solid ${isDark ? darkTokens.surface.border : "#d0d0d0"}`,
            borderRadius: "6px",
            backgroundColor: isDark ? darkTokens.surface.bg : "#ffffff",
            color: isDark ? darkTokens.text.primary : "#1a1a1a",
            cursor: "pointer",
          }}
        >
          重试加载
        </button>
        <div
          style={{
            fontSize: "12px",
            color: isDark ? darkTokens.text.hint : "#999999",
            lineHeight: 1.5,
          }}
        >
          如需返回画布编辑器，请点击顶部「← 返回画布编辑器」按钮。
        </div>
      </div>
    </div>
  );
}

// -----------------------------------------------------------------------------
// LazyPanel 组件
// -----------------------------------------------------------------------------

/** LazyPanel 属性 */
export interface LazyPanelProps<T> {
  /** 面板惰性槽 */
  slot: LazySlot<T>;
  /** 解析结果渲染回调（面板 props 契约在调用点逐一类型核对，GK-14） */
  render: (value: T) => JSX.Element;
}

/**
 * 面板惰性渲染器：已解析槽同步直渲（零 fallback 闪现）；未解析槽挂载时 kick
 * 并渲染加载态；失败态展示失败卡片（重试重置并重新 kick）。面板渲染期错误
 * 不拦截（上抛 AdminErrorBoundary，既有兜底语义）。
 */
export function LazyPanel<T>({ slot, render }: LazyPanelProps<T>): JSX.Element {
  const [, setTick] = useState(0);
  const rerender = useCallback((): void => {
    setTick((tick: number) => tick + 1);
  }, []);

  useEffect(() => {
    const unsubscribe = subscribeLazySlot(slot, rerender);
    loadLazySlot(slot);
    return unsubscribe;
  }, [slot, rerender]);

  const isDark = useThemeMode() === "dark";

  if (slot.status === "resolved" && slot.value !== undefined) {
    return render(slot.value);
  }
  if (slot.status === "rejected") {
    return (
      <PanelLoadError
        error={slot.error}
        isDark={isDark}
        onRetry={() => {
          resetLazySlot(slot);
          loadLazySlot(slot);
        }}
      />
    );
  }
  return <PanelFallback isDark={isDark} />;
}
