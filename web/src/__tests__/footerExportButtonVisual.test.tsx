// =============================================================================
// 页脚"导出 YAML"按钮视觉优化回归测试（src/__tests__/footerExportButtonVisual.test.tsx）
//
// 覆盖验收（对应 spec.md 5.1/5.2/5.3，EARS 验收条件中的 jsdom 可断言子集）：
//   1. 页脚容器结构保持（5.1.1.1a/2a/4a、5.2.1.2a）
//   2. 按钮皮肤挂载（5.2.1.1a）
//   3. 令牌同源取值（5.3.1.1a/2a/3a、5.2.1.10a）
//   4. 动效克制与禁尺寸过渡（5.2.1.7a/8a）
//   5. 点击行为不变（5.2.1.9a）
//   6. 焦点规则可见性（5.2.1.5a）
//   7. 样式共存隔离（5.3.1.4a/5a、5.3.3.1）
// 验证方式：V1 vitest 组件测试（jsdom）；留待非沙箱环境执行
// （`npx vitest run src/__tests__/footerExportButtonVisual.test.tsx`），
// 沙箱内不运行（esbuild 子进程被杀）。
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

describe("页脚导出按钮视觉优化", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("页脚容器结构保持：footer 存在、容器内联样式与文本原样（5.1.1.1a/2a/4a、5.2.1.2a）", () => {
    vi.spyOn(orchestrationApiClient, "listExamples").mockResolvedValue({
      examples: [],
    });

    const { container } = render(<App />);
    const footer = container.querySelector("footer");

    // 页脚存在，容器内联样式与优化前逐字节一致（spec 5.1.1.1a/2a、6.2.1）
    expect(footer).not.toBeNull();
    expect(footer).toHaveStyle({
      padding: "4px 16px",
      borderTop: "1px solid #e0e0e0",
    });

    // 按钮文本原样，页脚内无新增可见内容元素（仅注入块零尺寸无可见内容，spec 5.1.1.4a）
    expect(screen.getByText("导出 YAML")).toBeTruthy();
  });

  it("按钮皮肤挂载：按钮含皮肤类，footer 注入块携带三伪类规则（5.2.1.1a）", () => {
    vi.spyOn(orchestrationApiClient, "listExamples").mockResolvedValue({
      examples: [],
    });

    const { container } = render(<App />);
    const footer = container.querySelector("footer");

    // 导出按钮挂载皮肤类（spec 5.2.1.1a 消除原生残留的皮肤挂载前提）
    const exportButton = footer?.querySelector("button");
    expect(exportButton?.className).toBe("wfp-footer-btn");

    // <style> 注入块随 footer 挂载，包含三类伪态规则（spec 5.2.1.3a/4a/5a）
    const styleNode = footer?.querySelector("style");
    expect(styleNode).not.toBeNull();
    const css = styleNode?.textContent ?? "";
    expect(css).toContain(".wfp-footer-btn:hover");
    expect(css).toContain(".wfp-footer-btn:active");
    expect(css).toContain(".wfp-footer-btn:focus-visible");
  });

  it("令牌同源取值：注入块含与顶栏按钮同源的色值/字号/圆角/动效（5.3.1.1a/2a/3a、5.2.1.10a）", () => {
    vi.spyOn(orchestrationApiClient, "listExamples").mockResolvedValue({
      examples: [],
    });

    const { container } = render(<App />);
    const footer = container.querySelector("footer");
    const css = footer?.querySelector("style")?.textContent ?? "";

    // 字号 13px / 圆角 6px / 描边 #d0d0d0 / 近黑文字 #1a1a1a / 可点击光标 / 120ms 短过渡
    // （与顶栏 wfp-topbar-btn 同语言，spec 5.3.1.1a/2a/3a、5.2.1.10a）
    expect(css).toContain("font-size: 13px");
    expect(css).toContain("border-radius: 6px");
    expect(css).toContain("#d0d0d0");
    expect(css).toContain("#1a1a1a");
    expect(css).toContain("cursor: pointer");
    expect(css).toContain("120ms");
  });

  it("动效克制与禁尺寸过渡：transition 仅三属性且无尺寸/布局过渡（5.2.1.7a/8a）", () => {
    vi.spyOn(orchestrationApiClient, "listExamples").mockResolvedValue({
      examples: [],
    });

    const { container } = render(<App />);
    const footer = container.querySelector("footer");
    const css = footer?.querySelector("style")?.textContent ?? "";

    // 过渡仅允许 background-color/border-color/box-shadow（spec 5.2.1.7a）
    const transitionDecl = css.match(/transition:[^;]+/)?.[0] ?? "";
    expect(transitionDecl).toContain("background-color");
    expect(transitionDecl).toContain("border-color");
    expect(transitionDecl).toContain("box-shadow");

    // 禁尺寸/布局类过渡：width/height/margin/padding/transform 不在过渡属性集内（spec 5.2.1.8a）
    expect(transitionDecl).not.toContain("width");
    expect(transitionDecl).not.toContain("height");
    expect(transitionDecl).not.toContain("margin");
    expect(transitionDecl).not.toContain("padding");
    expect(transitionDecl).not.toContain("transform");

    // 无动画定义
    expect(css).not.toContain("@keyframes");
    expect(css).not.toContain("animation:");
  });

  it("点击行为不变：点击导出按钮触发 canvasToYaml 调用路径（5.2.1.9a）", () => {
    vi.spyOn(orchestrationApiClient, "listExamples").mockResolvedValue({
      examples: [],
    });
    const canvasToYamlSpy = vi.spyOn(canvasToYamlExporter, "canvasToYaml");

    render(<App />);
    fireEvent.click(screen.getByText("导出 YAML"));

    // handleExportYaml 触发 canvasToYaml（行为与优化前完全一致，spec 5.2.1.9a）
    expect(canvasToYamlSpy).toHaveBeenCalled();
  });

  it("焦点规则可见性：focus-visible 仅键盘可见轮廓（5.2.1.5a）", () => {
    vi.spyOn(orchestrationApiClient, "listExamples").mockResolvedValue({
      examples: [],
    });

    const { container } = render(<App />);
    const footer = container.querySelector("footer");
    const css = footer?.querySelector("style")?.textContent ?? "";

    // focus-visible 语义：outline 深灰 2px + 2px 离隙，与顶栏 wfp-topbar-btn 同风格（spec 5.2.1.5a）
    expect(css).toContain("outline: 2px solid #333333");
    expect(css).toContain("outline-offset: 2px");
  });

  it("样式共存隔离：footer 注入块与其余类族互不渗透且纯类选择器（5.3.1.4a/5a、5.3.3.1）", () => {
    vi.spyOn(orchestrationApiClient, "listExamples").mockResolvedValue({
      examples: [],
    });

    const { container } = render(<App />);
    const footer = container.querySelector("footer");
    const footerCss = footer?.querySelector("style")?.textContent ?? "";

    // 页脚注入块不含其余区域类名（spec 5.3.1.5a 类族隔离）
    expect(footerCss).not.toContain("wfp-topbar-");
    expect(footerCss).not.toContain("wfp-palette-");
    expect(footerCss).not.toContain("wfp-splitter-");

    // 顶栏注入块不含 wfp-footer-（spec 5.3.3.1 双向隔离）
    const topbarCss = container.querySelector("header style")?.textContent ?? "";
    expect(topbarCss).not.toContain("wfp-footer-");

    // 每条规则选择器以 . 开头（纯类选择器，无元素选择器外溢，spec 4.4.2）；
    // 先剥内联 /* */ 注释，避免注释段被朴素 split 误判为选择器
    const selectors = footerCss
      .replace(/\/\*[\s\S]*?\*\//g, " ")
      .split("}")
      .map((rule) => rule.trim().split("{")[0]?.trim())
      .filter(Boolean);
    for (const selector of selectors) {
      expect(selector.startsWith(".")).toBe(true);
    }
  });
});