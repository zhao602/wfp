// =============================================================================
// 根级错误边界（src/RootErrorBoundary.tsx）
//
// 职责：捕获整个应用渲染树的未处理错误，展示兜底提示与重载入口，
// 避免渲染崩溃导致整页空白、无任何提示。
// 设计意图：
//   1. 仅在 main.tsx 根级使用一次，包裹 <App />；
//   2. 正常态直接透传 children（无包装 DOM，布局零影响）；
//   3. 兜底 UI 极简：错误信息 + 页面重载按钮，帮助快速定位问题；
//   4. CSS 前缀 wfp-reb-（与 wfp-aeb-/wfp-eb- 隔离）。
// =============================================================================

import React from "react";
import { darkTokens } from "./theme/themeMode";

/** 根级兜底属性 */
interface RootErrorBoundaryProps {
  /** 应用子树 */
  children: React.ReactNode;
}

/** 兜底内部状态 */
interface RootErrorBoundaryState {
  /** 是否处于兜底态（子树渲染期发生错误） */
  hasError: boolean;
  /** 最近一次捕获的错误信息（兜底提示展示用） */
  errorMessage: string | null;
}

/** 兜底 UI `<style>` 注入块文本：wfp-reb- 前缀类选择器限定作用域 */
const rootErrorBoundaryCss = `
.wfp-reb {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100vw;
  height: 100vh;
  background: #f5f5f5;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}
.wfp-reb-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  max-width: 520px;
  padding: 28px 32px;
  background: #ffffff;
  border: 1px solid #d0d0d0;
  border-radius: 8px;
  font-size: 13px;
  color: #666666;
}
.wfp-reb-title {
  font-size: 16px;
  font-weight: 600;
  color: #d32f2f;
}
.wfp-reb-desc {
  line-height: 1.6;
  word-break: break-all;
  max-height: 200px;
  overflow-y: auto;
}
.wfp-reb-btn {
  font-family: inherit;
  font-size: 13px;
  font-weight: 500;
  padding: 8px 24px;
  border: 1px solid #1a1a1a;
  border-radius: 6px;
  background: #1a1a1a;
  color: #ffffff;
  cursor: pointer;
  transition: background 120ms ease;
}
.wfp-reb-btn:hover {
  background: #333333;
}
.wfp-reb-hint {
  font-size: 12px;
  color: #999999;
  line-height: 1.5;
}
.wfp-theme--dark .wfp-reb { background: ${darkTokens.surface.bg}; }
.wfp-theme--dark .wfp-reb-card { background: ${darkTokens.surface.bg}; border-color: ${darkTokens.surface.border}; }
.wfp-theme--dark .wfp-reb-title { color: ${darkTokens.text.primary}; }
.wfp-theme--dark .wfp-reb-desc { color: ${darkTokens.text.secondary}; }
.wfp-theme--dark .wfp-reb-hint { color: ${darkTokens.text.hint}; }
.wfp-theme--dark .wfp-reb-btn { background: ${darkTokens.surface.bgHover}; color: ${darkTokens.text.primary}; border-color: ${darkTokens.surface.border}; }
.wfp-theme--dark .wfp-reb-btn:hover { background: ${darkTokens.surface.bgActive}; border-color: ${darkTokens.surface.borderStrong}; }
`;

/**
 * 根级错误边界组件：应用渲染崩溃时展示兜底 UI 与重载入口。
 * 正常态直接透传 children（无包装 DOM，布局零影响）。
 */
class RootErrorBoundary extends React.Component<
  RootErrorBoundaryProps,
  RootErrorBoundaryState
> {
  /** 兜底内部状态初值 */
  state: RootErrorBoundaryState = { hasError: false, errorMessage: null };

  /** 渲染期错误入口：切换兜底内部状态 */
  static getDerivedStateFromError(error: unknown): RootErrorBoundaryState {
    return {
      hasError: true,
      errorMessage: error instanceof Error ? error.message : String(error),
    };
  }

  /** 记录错误详情供调试 */
  componentDidCatch(error: unknown, errorInfo: React.ErrorInfo): void {
    try {
      console.error("RootErrorBoundary 捕获应用渲染错误:", error, errorInfo);
    } catch {
      // 静默：日志失败不阻断兜底 UI
    }
  }

  /** 重载：刷新整个页面（根级兜底无法局部恢复，刷新是最可靠的恢复路径） */
  private handleReload = (): void => {
    try {
      window.location.reload();
    } catch {
      // 刷新失败：忽略
    }
  };

  render(): React.ReactNode {
    if (!this.state.hasError) {
      return this.props.children;
    }
    return (
      <div className="wfp-reb">
        <style>{rootErrorBoundaryCss}</style>
        <div className="wfp-reb-card">
          <div className="wfp-reb-title">应用加载异常</div>
          <div className="wfp-reb-desc">
            {this.state.errorMessage
              ? `渲染时发生错误：${this.state.errorMessage}`
              : "渲染时发生未知错误。"}
          </div>
          <button className="wfp-reb-btn" onClick={this.handleReload}>
            重新加载页面
          </button>
          <div className="wfp-reb-hint">
            如问题持续，请检查浏览器控制台（F12）获取详细错误信息。
          </div>
        </div>
      </div>
    );
  }
}

export { RootErrorBoundary };