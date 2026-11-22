// =============================================================================
// 页脚"导出 YAML"按钮下载副作用回归测试（src/__tests__/exportYamlDownload.test.tsx）
//
// 覆盖目标（fix_yaml_export_button，封堵 spec 证据 5 盲区）：
//   既有 footerExportButtonVisual.test.tsx 仅断言"canvasToYaml 被调用"，
//   不断言"下载副作用被触发"——本文件补齐下载链路断言（design DC-06）：
//   1. 点击导出按钮 → URL.createObjectURL 调用 1 次（Blob 对象 URL 生成）
//   2. HTMLAnchorElement.prototype.click 调用 1 次（下载触发的可观察末端）
//   3. URL.revokeObjectURL 调用 1 次（对象 URL 同步释放，DC-04）
//   4. 捕获的 anchor：download === "workflow.yaml"、href === "blob:mock-url"（DC-02/DC-03）
//   5. canvasToYaml 被调用（"点击 → 序列化 → 下载"链路完整，证据 5 盲区封堵）
// 验证方式：V1 vitest 组件测试（jsdom）；单文件逐跑
// （`npx vitest run src/__tests__/exportYamlDownload.test.tsx`），
// 崩溃 `The service was stopped` 重试即过；勿用批量循环脚本。
// =============================================================================

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import App from "../App";
import { orchestrationApiClient } from "../api/orchestrationApiClient";
import * as canvasToYamlExporter from "../workflow-definition/canvasToYamlExporter";

// jsdom 无 ResizeObserver，reactflow 画布在测试环境渲染需要桩实现（仅测试环境补丁）
vi.stubGlobal(
  "ResizeObserver",
  class {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  },
);

// 后端返回 200 空对象，避免渲染全树时子面板的真实请求外溢
vi.stubGlobal(
  "fetch",
  vi.fn().mockResolvedValue(new Response(JSON.stringify({}), { status: 200 })),
);

// jsdom 无 URL.createObjectURL/revokeObjectURL 原生实现，显式桩化（守护 C，design 2.11）
URL.createObjectURL = vi.fn(() => "blob:mock-url");
URL.revokeObjectURL = vi.fn();

describe("页脚导出 YAML 下载副作用（fix_yaml_export_button）", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("点击导出按钮触发下载六步：createObjectURL/anchor.click/revokeObjectURL 各 1 次，文件名 workflow.yaml，canvasToYaml 被调用（链路完整）", () => {
    vi.spyOn(orchestrationApiClient, "listExamples").mockResolvedValue({
      examples: [],
    });
    const canvasToYamlSpy = vi.spyOn(canvasToYamlExporter, "canvasToYaml");
    const anchorClickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click");
    const createElementSpy = vi.spyOn(document, "createElement");

    render(<App />);
    fireEvent.click(screen.getByText("导出 YAML"));

    // "点击 → 序列化"链路完整（封堵 spec 证据 5"仅断言序列化"盲区）
    expect(canvasToYamlSpy).toHaveBeenCalledTimes(1);

    // 下载副作用三项核心断言（DC-06 红线）
    expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
    expect(anchorClickSpy).toHaveBeenCalledTimes(1);
    expect(URL.revokeObjectURL).toHaveBeenCalledTimes(1);

    // 捕获的 anchor：文件名与对象 URL 口径（DC-02/DC-03）
    const anchors = createElementSpy.mock.results
      .map((r) => r.value)
      .filter((v): v is HTMLAnchorElement => v instanceof HTMLAnchorElement);
    const downloadAnchor = anchors.find((a) => a.download === "workflow.yaml");
    expect(downloadAnchor).toBeDefined();
    expect(downloadAnchor?.href).toBe("blob:mock-url");
  });
});
