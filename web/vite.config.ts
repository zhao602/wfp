import { defineConfig } from "vite";
import { configDefaults } from "vitest/config";
import react from "@vitejs/plugin-react";

// Vite 构建配置：React 插件 + 开发代理转发至后端 API
export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0', // 关键配置：监听所有网络接口，允许局域网访问
    port: 5173,
    strictPort: true, // 端口占用即失败（e2e baseURL 固定口径；防 vite 自增 5174 失配）
    proxy: {
      "/api": {
        // e2e（web/playwright.config.ts）经此 env 指向独立测试后端；缺省不动
        target: process.env.WFP_E2E_API_TARGET ?? "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: "./src/test-setup.ts",
    // Playwright e2e 目录不入 vitest（B8d）：spec 文件经 playwright test 运行，
    // vitest 收集会因 @playwright/test 语义失败；显式叠加默认排除清单防漂移。
    exclude: [...configDefaults.exclude, "e2e/**"],
  },
});
