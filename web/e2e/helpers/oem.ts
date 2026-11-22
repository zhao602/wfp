import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

// OEM 产物帮助函数（design D7）：build_oem.py 产物按需构建 + 静态服子进程。

const PYTHON = "D:\\anaconda\\envs\\bd_agent\\python.exe";

/** 仓库根定位：兼容 web/ 或仓库根两种运行 cwd。 */
export function repoRoot(): string {
  const cwd = process.cwd();
  if (fs.existsSync(path.join(cwd, "web", "playwright.config.ts"))) {
    return cwd;
  }
  if (fs.existsSync(path.join(cwd, "playwright.config.ts"))) {
    return path.resolve(cwd, "..");
  }
  throw new Error("无法定位仓库根：请在 web/ 或仓库根目录运行 playwright");
}

function bundleIndex(brand: string): string {
  return path.join(repoRoot(), "oem", "dist", brand, "web", "index.html");
}

/** 确保 OEM 双品牌产物存在；缺失则执行 build_oem.py（幂等：manifest 可复现）。 */
export function ensureOemBundles(): void {
  const missing = ["default", "acmeflow"].filter((b) => !fs.existsSync(bundleIndex(b)));
  if (missing.length === 0) {
    return;
  }
  const result = spawnSync(PYTHON, ["scripts/build_oem.py"], {
    cwd: repoRoot(),
    encoding: "utf8",
  });
  if (result.status !== 0) {
    throw new Error(
      `build_oem.py 失败（exit ${result.status}）：${(result.stderr ?? "").slice(-500)}`,
    );
  }
  const stillMissing = ["default", "acmeflow"].filter((b) => !fs.existsSync(bundleIndex(b)));
  if (stillMissing.length > 0) {
    throw new Error(`构建后仍缺产物：${stillMissing.join(", ")}`);
  }
}

/** 起静态服（python http.server）托管品牌产物；返回子进程（用毕 kill）。 */
export async function serveOemBundle(brand: string, port: number): Promise<ChildProcess> {
  const dir = path.join(repoRoot(), "oem", "dist", brand, "web");
  const child = spawn(PYTHON, ["-m", "http.server", String(port), "--bind", "127.0.0.1", "--directory", dir], {
    stdio: "ignore",
  });
  const deadline = Date.now() + 15_000;
  for (;;) {
    try {
      const res = await fetch(`http://127.0.0.1:${port}/`);
      if (res.ok) {
        return child;
      }
    } catch {
      // 端口未就绪，继续轮询
    }
    if (Date.now() > deadline) {
      child.kill();
      throw new Error(`静态服 ${brand}（端口 ${port}）启动超时`);
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
}
