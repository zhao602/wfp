import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Vite 构建配置：React 插件 + 开发代理转发至后端 API
export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0', // 关键配置：监听所有网络接口，允许局域网访问
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: "./src/test-setup.ts",
  },
});
