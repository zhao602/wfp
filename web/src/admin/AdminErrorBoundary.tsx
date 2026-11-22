// =============================================================================
// 管理后台面板渲染兜底（src/admin/AdminErrorBoundary.tsx）
//
// 职责：捕获管理后台面板子树的渲染期错误，展示兜底提示并提供重试入口，
// 避免渲染崩溃导致整页空白、无任何提示。侧边栏与返回按钮保持可用。
// 设计意图（对应 fix_admin_console_blank design CHG-2/DC-3~DC-7）：
//   1. 仅包裹 renderPanel 输出，不包裹 header/nav（DC-4 最小侵入）；
//   2. 正常态直接透传 children（无包装 DOM，布局零影响，INV-4）；
//   3. 兜底文案为管理后台语境（DC-7），与画布兜底 CanvasErrorBoundary 隔离（INV-6）；
//   4. CSS 前缀 wfp-aeb-（与 wfp-eb- 隔离，INV-7）。
// =============================================================================

import React from "react";
import { darkTokens } from "../theme/themeMode";

/** 管理后台面板兜底属性 */
interface AdminErrorBoundaryProps {
  /** 面板子树 */
  children: React.ReactNode;
}

/** 兜底内部状态 */
interface AdminErrorBoundaryState {
  /** 是否处于兜底态（子树渲染期发生错误） */
  hasError: boolean;
  /** 最近一次捕获的错误信息（兜底提示展示用） */
  errorMessage: string | null;
}

/**
 * 兜底 UI `<style>` 注入块文本：wfp-aeb- 前缀类选择器限定作用域（与
 * wfp-eb-/wfp-ncp- 隔离共存）；深色规则由 `.wfp-theme--dark`
 * 祖先驱动（App 根容器持有主题类）。禁 CSS 变量、禁 @keyframes（风格硬约束）。
 */
const adminErrorBoundaryCss = `
.wfp-aeb {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  background: #f5f5f5;
}
.wfp-aeb-card {
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
.wfp-aeb-title {
  font-size: 14px;
  font-weight: 600;
  color: #1f1f1f;
}
.wfp-aeb-desc {
  line-height: 1.5;
  word-break: break-all;
}
.wfp-aeb-btn {
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
.wfp-aeb-btn:hover {
  background: #f5f5f5;
  border-color: #b8b8b8;
}
.wfp-aeb-hint {
  font-size: 12px;
  color: #999999;
  line-height: 1.5;
}
/* —— 深色作用域规则：追加于末尾，浅色规则文本不动（对齐既有注入块写法） —— */
.wfp-theme--dark .wfp-aeb {
  background: ${darkTokens.surface.bg};
}
.wfp-theme--dark .wfp-aeb-card {
  background: ${darkTokens.surface.bgHover};
  border-color: ${darkTokens.surface.divider};
}
.wfp-theme--dark .wfp-aeb-title {
  color: ${darkTokens.text.primary};
}
.wfp-theme--dark .wfp-aeb-desc {
  color: ${darkTokens.text.secondary};
}
.wfp-theme--dark .wfp-aeb-btn {
  background: ${darkTokens.surface.bg};
  border-color: ${darkTokens.surface.border};
  color: ${darkTokens.text.primary};
}
.wfp-theme--dark .wfp-aeb-btn:hover {
  background: ${darkTokens.surface.bgHover};
  border-color: ${darkTokens.surface.borderStrong};
}
.wfp-theme--dark .wfp-aeb-hint {
  color: ${darkTokens.text.hint};
}
`;

/**
 * 管理后台面板渲染兜底组件：子树渲染崩溃时展示兜底 UI，保留侧边栏与返回按钮的恢复路径。
 * 正常态直接透传 children（无包装 DOM，布局零影响，INV-4）。
 */
class AdminErrorBoundary extends React.Component<
  AdminErrorBoundaryProps,
  AdminErrorBoundaryState
> {
  /** 兜底内部状态初值 */
  state: AdminErrorBoundaryState = { hasError: false, errorMessage: null };

  /** 渲染期错误入口：切换兜底内部状态（纯计算，无副作用，StrictMode 安全） */
  static getDerivedStateFromError(error: unknown): AdminErrorBoundaryState {
    return {
      hasError: true,
      errorMessage: error instanceof Error ? error.message : String(error),
    };
  }

  /** 记录错误详情供调试；内部防御不连锁崩溃（日志失败不影响兜底 UI 呈现） */
  componentDidCatch(error: unknown, errorInfo: React.ErrorInfo): void {
    try {
      console.error("AdminErrorBoundary 捕获面板渲染错误:", error, errorInfo);
    } catch {
      // 静默：日志失败不阻断兜底 UI
    }
  }

  /** 重试：退出兜底内部状态并重挂载子树（DC-5 内部恢复，无外部回调） */
  private handleRetry = (): void => {
    this.setState({ hasError: false, errorMessage: null });
  };

  render(): React.ReactNode {
    if (!this.state.hasError) {
      return this.props.children;
    }
    return (
      <div className="wfp-aeb">
        <style>{adminErrorBoundaryCss}</style>
        <div className="wfp-aeb-card">
          <div className="wfp-aeb-title">面板加载异常</div>
          <div className="wfp-aeb-desc">
            {this.state.errorMessage
              ? `面板渲染时发生错误：${this.state.errorMessage}`
              : "面板渲染时发生未知错误。"}
          </div>
          <button className="wfp-aeb-btn" onClick={this.handleRetry}>
            重试当前面板
          </button>
          <div className="wfp-aeb-hint">
            如需返回画布编辑器，请点击顶部"← 返回画布编辑器"按钮。
          </div>
        </div>
      </div>
    );
  }
}

export { AdminErrorBoundary };