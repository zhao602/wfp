// =============================================================================
// 画布渲染兜底（src/canvas/CanvasErrorBoundary.tsx）
//
// 职责：捕获画布子树的渲染期错误，展示兜底提示并提供显式恢复入口，
// 避免渲染崩溃导致整棵组件树卸载、被用户感知为"画布被清空"。
// 设计意图（对应 design CHG-8/DC-9、INV-12）：
//   1. 能力边界：仅捕获子树渲染期错误（render / 生命周期 / 构造函数），
//      不捕获事件回调错误（已由提交预判防护）与异步错误（已有 try/catch 兜底）；
//   2. 恢复顺序：先经 onResetCanvas 清空画布状态（剔除异常数据），再退出兜底
//      内部状态并重挂载子树——先清空再重试，避免异常数据保留导致崩溃死循环；
//   3. 不持有业务状态：恢复动作经 onResetCanvas 回调收敛回 App 唯一事实源。
// =============================================================================

import React from "react";
import { darkTokens } from "../theme/themeMode";

/** 画布渲染兜底属性 */
interface CanvasErrorBoundaryProps {
  /** 画布子树 */
  children: React.ReactNode;
  /** 恢复画布回调（清空画布状态 + 清空选中态）；未注入时兜底 UI 仅提示不提供恢复按钮 */
  onResetCanvas?: () => void;
}

/** 兜底内部状态 */
interface CanvasErrorBoundaryState {
  /** 是否处于兜底态（子树渲染期发生错误） */
  hasError: boolean;
  /** 最近一次捕获的错误信息（兜底提示展示用） */
  errorMessage: string | null;
}

/**
 * 兜底 UI `<style>` 注入块文本：wfp-eb- 前缀类选择器限定作用域（与
 * wfp-topbar-/wfp-footer-/wfp-ncp- 隔离共存）；深色规则由 `.wfp-theme--dark`
 * 祖先驱动（App 根容器持有主题类）。禁 CSS 变量、禁 @keyframes（风格硬约束）。
 */
const errorBoundaryCss = `
.wfp-eb {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  background: #f5f5f5;
}
.wfp-eb-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  max-width: 420px;
  padding: 24px 28px;
  background: #ffffff;
  border: 1px solid #d0d0d0;
  border-radius: 8px;
  font-size: 13px;
  color: #666666;
}
.wfp-eb-title {
  font-size: 14px;
  font-weight: 600;
  color: #1f1f1f;
}
.wfp-eb-desc {
  line-height: 1.5;
  word-break: break-all;
}
.wfp-eb-btn {
  font-family: inherit;
  font-size: 13px;
  font-weight: 500;
  padding: 6px 18px;
  border: 1px solid #d0d0d0;
  border-radius: 6px;
  background: #ffffff;
  color: #1a1a1a;
  cursor: pointer;
  transition: background 120ms ease, border-color 120ms ease;
}
.wfp-eb-btn:hover {
  background: #f5f5f5;
  border-color: #b8b8b8;
}
.wfp-eb-hint {
  font-size: 12px;
  color: #999999;
  line-height: 1.5;
}
/* —— 深色作用域规则：追加于末尾，浅色规则文本不动（对齐既有注入块写法） —— */
.wfp-theme--dark .wfp-eb {
  background: ${darkTokens.surface.bg};
}
.wfp-theme--dark .wfp-eb-card {
  background: ${darkTokens.surface.bgHover};
  border-color: ${darkTokens.surface.divider};
}
.wfp-theme--dark .wfp-eb-title {
  color: ${darkTokens.text.primary};
}
.wfp-theme--dark .wfp-eb-desc {
  color: ${darkTokens.text.secondary};
}
.wfp-theme--dark .wfp-eb-btn {
  background: ${darkTokens.surface.bg};
  border-color: ${darkTokens.surface.border};
  color: ${darkTokens.text.primary};
}
.wfp-theme--dark .wfp-eb-btn:hover {
  background: ${darkTokens.surface.bgHover};
  border-color: ${darkTokens.surface.borderStrong};
}
.wfp-theme--dark .wfp-eb-hint {
  color: ${darkTokens.text.hint};
}
`;

/**
 * 画布渲染兜底组件：子树渲染崩溃时展示兜底 UI，保留顶栏与范例库的恢复路径（INV-12）。
 * 正常态直接透传 children（无包装 DOM，布局零影响）。
 */
class CanvasErrorBoundary extends React.Component<
  CanvasErrorBoundaryProps,
  CanvasErrorBoundaryState
> {
  /** 兜底内部状态初值 */
  state: CanvasErrorBoundaryState = { hasError: false, errorMessage: null };

  /** 渲染期错误入口：切换兜底内部状态（纯计算，无副作用，StrictMode 安全） */
  static getDerivedStateFromError(error: unknown): CanvasErrorBoundaryState {
    return {
      hasError: true,
      errorMessage: error instanceof Error ? error.message : String(error),
    };
  }

  /** 记录错误详情供调试；内部防御不连锁崩溃（日志失败不影响兜底 UI 呈现） */
  componentDidCatch(error: unknown, errorInfo: React.ErrorInfo): void {
    try {
      console.error("CanvasErrorBoundary 捕获画布渲染错误:", error, errorInfo);
    } catch {
      // 静默：日志失败不阻断兜底 UI
    }
  }

  /**
   * 显式恢复（DC-9 恢复顺序）：先经回调清空画布状态，再退出兜底内部状态并
   * 重挂载子树——先清空再重试，避免异常数据保留导致崩溃死循环。
   */
  private handleReset = (): void => {
    this.props.onResetCanvas?.();
    this.setState({ hasError: false, errorMessage: null });
  };

  render(): React.ReactNode {
    if (!this.state.hasError) {
      return this.props.children;
    }
    return (
      <div className="wfp-eb">
        <style>{errorBoundaryCss}</style>
        <div className="wfp-eb-card">
          <div className="wfp-eb-title">画布渲染异常</div>
          <div className="wfp-eb-desc">
            {this.state.errorMessage
              ? `画布渲染时发生错误：${this.state.errorMessage}`
              : "画布渲染时发生未知错误。"}
          </div>
          {this.props.onResetCanvas && (
            <button className="wfp-eb-btn" onClick={this.handleReset}>
              恢复画布
            </button>
          )}
          <div className="wfp-eb-hint">
            如需恢复原编排内容，可通过顶部范例库重新加载。
          </div>
        </div>
      </div>
    );
  }
}

export default CanvasErrorBoundary;