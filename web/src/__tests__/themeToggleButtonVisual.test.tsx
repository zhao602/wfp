// =============================================================================
// 主题切换按钮视觉与语义回归测试（src/__tests__/themeToggleButtonVisual.test.tsx）
//
// 覆盖验收（对应 theme_toggle_button spec.md）：
//   ① 图形存在且为装饰（5.1.1.3 / 5.1.1.6 / 5.4.1.3）
//   ② 文本口径唯一（5.1.1.4 / 5.1.1.3）
//   ③ 两态图形切换（5.1.1.2 / 5.2.1.4）
//   ④ 注入块末尾规则（5.5.1.2 / 5.5.1.5）
//   ⑤ 浅色段零改动（5.5.1.2 / 5.5.1.3）
//   ⑥ 语义契约不破（5.1.1.1 / 5.3.1.6 / 5.4.1.1 / 5.4.1.2 / 5.4.1.6 / 5.5.1.10）
// 验证方式：V1 vitest 组件测试（jsdom）。
// 沙箱说明：本环境 `tsc` 全量编译超时、`vitest` 运行受 esbuild 子进程被杀限制
//（"The service was stopped"）无法执行；本测试作为交付物留待非沙箱环境执行
// `npx vitest run src/__tests__/themeToggleButtonVisual.test.tsx`，
// 并确保既有全部测试文件全绿。
// =============================================================================

import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import App from "../App";
import { orchestrationApiClient } from "../api/orchestrationApiClient";
import { THEME_ROOT_CLASS } from "../theme/themeMode";

// jsdom 无 ResizeObserver，reactflow 画布在测试环境渲染需要桩实现
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

/** 桩 matchMedia：控制 prefers-color-scheme 返回值 */
function stubMatchMedia(prefersDark: boolean): void {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: query.includes("dark") ? prefersDark : false,
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
}

beforeEach(() => {
  localStorage.clear();
  stubMatchMedia(false);
  vi.spyOn(orchestrationApiClient, "listExamples").mockResolvedValue({
    examples: [],
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("① 图形存在且为装饰", () => {
  it("按钮内存在 svg.wfp-topbar-theme-icon，具 aria-hidden/focusable 且无文本节点", () => {
    const { container } = render(<App />);
    const icon = container.querySelector("svg.wfp-topbar-theme-icon");

    // 图形存在（5.1.1.2）
    expect(icon).not.toBeNull();
    // 装饰图形对辅助技术隐藏（5.4.1.3）
    expect(icon?.getAttribute("aria-hidden")).toBe("true");
    expect(icon?.getAttribute("focusable")).toBe("false");
    // SVG 内零文本节点，不污染可访问名称（5.1.1.3 / 5.1.1.6）
    expect(icon?.textContent).toBe("");
  });
});

describe("② 文本口径唯一", () => {
  it("按钮 textContent 归一化后精确等于文案，getByText 唯一命中且携带 aria-pressed", () => {
    render(<App />);
    const toggle = screen.getByText("深色");

    // 按钮直接文本口径精确（5.1.1.3）；归一化后无图形字符污染
    expect(toggle.textContent?.trim()).toBe("深色");
    // 唯一命中且该元素即按钮本身，携带切换语义（5.1.1.4 / 5.4.1.1）
    expect(toggle.tagName).toBe("BUTTON");
    expect(toggle.getAttribute("aria-pressed")).toBe("false");
  });
});

describe("③ 两态图形切换", () => {
  it("浅色态月默认渲染、日 display=none；深色态根类出现且注入块含反转规则", () => {
    const { container } = render(<App />);
    const moon = container.querySelector(".wfp-topbar-theme-icon__moon");
    const sun = container.querySelector(".wfp-topbar-theme-icon__sun");

    // 浅色基准：月无 display 抑制、日由表现属性隐藏（5.1.1.2 / 5.2.1.4）
    expect(moon).not.toBeNull();
    expect(moon?.getAttribute("display")).not.toBe("none");
    expect(sun).not.toBeNull();
    expect(sun?.getAttribute("display")).toBe("none");

    // 切换深色：根容器主题标识出现，注入块含两条反转规则（5.2.1.4）
    fireEvent.click(screen.getByText("深色"));
    expect(container.querySelector(`.${THEME_ROOT_CLASS}`)).not.toBeNull();
    const css = container.querySelector("header style")?.textContent ?? "";
    expect(css).toContain(`.${THEME_ROOT_CLASS} .wfp-topbar-theme-icon__moon`);
    expect(css).toContain(`.${THEME_ROOT_CLASS} .wfp-topbar-theme-icon__sun`);
  });
});

describe("④ 注入块末尾规则", () => {
  it("<style> 含两条深色作用域显隐规则，且不含 button { 子串", () => {
    const { container } = render(<App />);
    const css = container.querySelector("header style")?.textContent ?? "";

    // 深色作用域规则就位（5.5.1.2）
    expect(css).toContain(
      `.${THEME_ROOT_CLASS} .wfp-topbar-theme-icon__moon { display: none; }`,
    );
    expect(css).toContain(
      `.${THEME_ROOT_CLASS} .wfp-topbar-theme-icon__sun { display: inline; }`,
    );
    // 选择器规范：无裸元素选择器子串（5.5.1.5）
    expect(css).not.toContain("button {");
  });
});

describe("⑤ 浅色段零改动", () => {
  it("注入块仍含既有浅色规则关键文本", () => {
    const { container } = render(<App />);
    const css = container.querySelector("header style")?.textContent ?? "";

    // 既有浅色规则文本逐字符保留（5.5.1.2 / 5.5.1.3）
    expect(css).toContain(".wfp-topbar-btn:active");
    expect(css).toContain(".wfp-topbar-btn:focus-visible");
    expect(css).toContain("120ms");
    expect(css).toContain("#1a1a1a");
  });
});

describe("⑥ 语义契约不破", () => {
  it("aria-pressed/aria-label 两态、点击切换往返", () => {
    render(<App />);

    // 浅色态：文案"深色"、aria-pressed=false（5.1.1.1 / 5.4.1.1 / 5.4.1.2）
    const lightBtn = screen.getByText("深色");
    expect(lightBtn.getAttribute("aria-pressed")).toBe("false");
    expect(lightBtn.getAttribute("aria-label")).toBe(
      "当前为浅色模式，点击切换至深色主题",
    );

    // 切换深色：文案"浅色"、aria-pressed=true（5.3.1.6）
    fireEvent.click(lightBtn);
    const darkBtn = screen.getByText("浅色");
    expect(darkBtn.getAttribute("aria-pressed")).toBe("true");
    expect(darkBtn.getAttribute("aria-label")).toBe(
      "当前为深色模式，点击切换至浅色主题",
    );

    // 往返恢复浅色（5.5.1.10 语义契约冻结）
    fireEvent.click(darkBtn);
    expect(screen.getByText("深色").getAttribute("aria-pressed")).toBe("false");
  });
});