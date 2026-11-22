// =============================================================================
// 全局错误可观测性（src/errorObservability.ts，fix_raw_not_iterable CHG-05）
//
// 职责：注册 window 级 error / unhandledrejection 监听，将异步与事件回调错误
//       以统一前缀输出至控制台，补齐 React Error Boundary 覆盖不到的全局链路。
// 设计意图（DC-05）：
//   1. 幂等注册：模块级 installed 标记，重复调用不重复绑定（StrictMode 双执行安全）；
//   2. 日志输出失败静默（对齐 RootErrorBoundary.componentDidCatch 范式）；
//   3. 零第三方依赖、零 any；不新增可回显敏感数据的字段。
// =============================================================================

let installed = false;

/** 安装全局错误监听（幂等：重复调用不重复绑定） */
export function installGlobalErrorListeners(): void {
  if (installed) {
    return;
  }
  installed = true;
  window.addEventListener("error", (event: ErrorEvent) => {
    try {
      console.error(
        "[global-error]",
        event.error ?? event.message,
        event.filename,
        event.lineno,
        event.colno,
      );
    } catch {
      // 日志输出失败静默（对齐 RootErrorBoundary.componentDidCatch 范式）
    }
  });
  window.addEventListener("unhandledrejection", (event: PromiseRejectionEvent) => {
    try {
      console.error("[global-error]", event.reason);
    } catch {
      // 日志输出失败静默（对齐 RootErrorBoundary.componentDidCatch 范式）
    }
  });
}
