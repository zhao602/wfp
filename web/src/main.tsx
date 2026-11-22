// =============================================================================
// 应用入口（src/main.tsx）
//
// 职责：挂载 React 根组件至 DOM #root 节点。
// =============================================================================

import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { installGlobalErrorListeners } from "./errorObservability";
import { RootErrorBoundary } from "./RootErrorBoundary";

installGlobalErrorListeners();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <RootErrorBoundary>
      <App />
    </RootErrorBoundary>
  </React.StrictMode>,
);
