import { defineConfig } from "@playwright/test";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";

// B8d e2e 编排（design D2/D8）：
//   webServer 数组 = uvicorn 后端（8330，local_file + 临时数据/日志目录）+
//   vite dev server（5173，避开 tsc --noEmit 门控的 build 路径）。
//   workers=1 / retries=0：10 连绿出口必须裸真（本地零重试；CI 建议另行加 1，
//   见 docs/testing.md e2e 章节）。
// 注意：本文件在 web/ 根，tsconfig include 仅 src——构造性隔离 typecheck（13 基线零新增）。

const PYTHON = "D:\\anaconda\\envs\\bd_agent\\python.exe";
const BACKEND_PORT = 8330;

const e2eTmp = path.join(os.tmpdir(), "wfp_e2e_backend");
fs.mkdirSync(path.join(e2eTmp, "data"), { recursive: true });
fs.mkdirSync(path.join(e2eTmp, "logs"), { recursive: true });

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["line"]],
  use: {
    baseURL: "http://localhost:5173",
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command:
        `"${PYTHON}" -m uvicorn ` +
        "orchestration_platform.api.application_factory:create_app --factory " +
        `--port ${BACKEND_PORT}`,
      cwd: "..",
      port: BACKEND_PORT,
      timeout: 60_000,
      reuseExistingServer: !process.env.CI,
      env: {
        ...process.env,
        PYTHONPATH: "src",
        STORAGE_BACKEND: "local_file",
        LOCAL_STORAGE_ROOT: path.join(e2eTmp, "data"),
        LOG_DIR: path.join(e2eTmp, "logs"),
        // B16 F5 e2e（design D8）：事件导出全链路开启（默认关）+ 订阅持久化
        // 落 e2e 临时目录（防污染工作区 ./.data）；投递重试 2 次 × 0.5s 退避
        // ——不可达回调快速进死信，缩短 09 用例等待。发布器装配依赖
        // event_bus_publish_enabled（application_factory G1 双开关，默认关）。
        EVENT_BUS_PUBLISH_ENABLED: "true",
        EVENT_EXPORT_ENABLED: "true",
        EVENT_EXPORT_API_ENABLED: "true",
        EVENT_EXPORT_SUBSCRIPTIONS_PERSIST_PATH: path.join(e2eTmp, "event_export_subscriptions.json"),
        EVENT_EXPORT_WEBHOOK_MAX_ATTEMPTS: "2",
        EVENT_EXPORT_WEBHOOK_BACKOFF_BASE_SECONDS: "0.5",
      },
    },
    {
      command: "npm run dev",
      url: "http://localhost:5173",
      timeout: 60_000,
      reuseExistingServer: !process.env.CI,
      env: {
        ...process.env,
        WFP_E2E_API_TARGET: `http://localhost:${BACKEND_PORT}`,
      },
    },
  ],
});
