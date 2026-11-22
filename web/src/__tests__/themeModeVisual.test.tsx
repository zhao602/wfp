// =============================================================================
// 主题模式（深色/浅色双模式）视觉与行为回归测试
//（src/__tests__/themeModeVisual.test.tsx）
//
// 覆盖验收（对应 spec.md 5.1~5.6）：
//   ① 默认渲染（5.2.1.1a / 5.1.1.3a）
//   ② 切换动作状态机（5.1.1.4a / 5.2.1.1a / 5.3.1.4a）
//   ③ 持久化写入（5.2.1.3a）
//   ④ 持久化恢复（5.2.1.2a / 5.4.3.2）
//   ⑤ 非法值与缺失回落（5.2.1.5a / 5.2.3.2）
//   ⑥ localStorage 不可用回落（5.2.3.1）
//   ⑦ 注入块双套规则（5.3.1.5a / 5.6.1.2a）
//   ⑧ 浅色冻结断言不破（5.3.3.3 / 1.2.4）
//   ⑨ header 既有断言顺序保护（5.1.1.8a）
//   ⑩ 管理后台主题一致（5.2.1.6a / 5.4.1.2a）
//   ⑪ ReactFlow 深色规则（5.5.1.9a / 6.3.6）
//   ⑫ 主/文字对比度核算常量（5.6.1.1a / 6.3）
// 验证方式：V1 vitest 组件测试（jsdom）；留待非沙箱环境执行
//（`npx vitest run src/__tests__/themeModeVisual.test.tsx`），
// 沙箱内不运行（esbuild 子进程被杀）
// =============================================================================

import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import App from "../App";
import { orchestrationApiClient } from "../api/orchestrationApiClient";
import {
  darkTokens,
  THEME_STORAGE_KEY,
  THEME_ROOT_CLASS,
  resolveInitialThemeMode,
  persistTheme,
  syncRootThemeClass,
} from "../theme/themeMode";

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

/** 从容器内所有 <style> 节点中提取含指定子串的 CSS 文本 */
function findCssContaining(
  container: HTMLElement,
  needle: string,
): string {
  const nodes = container.querySelectorAll("style");
  for (const node of nodes) {
    if (node.textContent?.includes(needle)) return node.textContent;
  }
  return "";
}

/** WCAG 相对亮度 */
function luminance(hex: string): number {
  const h = hex.replace("#", "");
  const toLin = (c: number) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
  };
  return (
    0.2126 * toLin(parseInt(h.slice(0, 2), 16)) +
    0.7152 * toLin(parseInt(h.slice(2, 4), 16)) +
    0.0722 * toLin(parseInt(h.slice(4, 6), 16))
  );
}

/** WCAG 对比度比 */
function contrastRatio(fg: string, bg: string): number {
  const l1 = luminance(fg);
  const l2 = luminance(bg);
  return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
}

beforeEach(() => {
  localStorage.clear();
  stubMatchMedia(false);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("① 默认渲染（无存储/无系统偏好）", () => {
  it("根 div 无深色类、切换按钮文案「深色」、aria-pressed=false", () => {
    vi.spyOn(orchestrationApiClient, "listExamples").mockResolvedValue({
      examples: [],
    });
    const { container } = render(<App />);
    // 5.2.1.1a / 5.1.1.3a：浅色态根容器无深色标识类
    expect(container.querySelector(`.${THEME_ROOT_CLASS}`)).toBeNull();
    // 切换按钮文案为"深色"（提示点击将切至深色）
    const toggleBtn = screen.getByText("深色");
    expect(toggleBtn).toBeInTheDocument();
    expect(toggleBtn.getAttribute("aria-pressed")).toBe("false");
  });
});

describe("② 切换动作状态机", () => {
  it("点击切换→深色态→再点恢复浅色", () => {
    vi.spyOn(orchestrationApiClient, "listExamples").mockResolvedValue({
      examples: [],
    });
    const { container } = render(<App />);

    // 5.1.1.4a / 5.3.1.4a：首次点击进入深色
    fireEvent.click(screen.getByText("深色"));
    expect(container.querySelector(`.${THEME_ROOT_CLASS}`)).not.toBeNull();
    const lightBtn = screen.getByText("浅色");
    expect(lightBtn).toBeInTheDocument();
    expect(lightBtn.getAttribute("aria-pressed")).toBe("true");

    // 5.2.1.1a：再次点击恢复浅色
    fireEvent.click(lightBtn);
    expect(container.querySelector(`.${THEME_ROOT_CLASS}`)).toBeNull();
    expect(screen.getByText("深色")).toBeInTheDocument();
  });
});

describe("③ 持久化写入", () => {
  it("切换后 localStorage 写入 dark，再切回写入 light", () => {
    vi.spyOn(orchestrationApiClient, "listExamples").mockResolvedValue({
      examples: [],
    });
    render(<App />);

    // 5.2.1.3a：仅显式切换才写入
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBeNull();

    fireEvent.click(screen.getByText("深色"));
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");

    fireEvent.click(screen.getByText("浅色"));
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");
  });
});

describe("④ 持久化恢复", () => {
  it("预置 dark → resolveInitialThemeMode 返回 dark（等价 App 首渲染深色）", () => {
    // 5.2.1.2a / 5.4.3.2：localStorage 有效值优先于系统偏好
    localStorage.setItem(THEME_STORAGE_KEY, "dark");
    expect(resolveInitialThemeMode()).toBe("dark");

    localStorage.setItem(THEME_STORAGE_KEY, "light");
    expect(resolveInitialThemeMode()).toBe("light");
  });
});

describe("⑤ 非法值与缺失回落", () => {
  it("预置非法值或缺失 → 回落浅色、不报错", () => {
    // 5.2.1.5a / 5.2.3.2：非法值不抛异常
    localStorage.setItem(THEME_STORAGE_KEY, "blue");
    expect(resolveInitialThemeMode()).toBe("light");

    localStorage.removeItem(THEME_STORAGE_KEY);
    expect(resolveInitialThemeMode()).toBe("light");
  });
});

describe("⑥ localStorage 不可用回落", () => {
  it("getItem 抛异常 → 回落浅色；persistTheme 写抛异常静默", () => {
    // 5.2.3.1：读取异常静默回落
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("quota");
    });
    expect(resolveInitialThemeMode()).toBe("light");

    // 写入异常静默不抛
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("quota");
    });
    expect(() => persistTheme("dark")).not.toThrow();
  });
});

describe("⑦ 注入块双套规则", () => {
  it("topbarCss/footerCss 含深色作用域规则", () => {
    vi.spyOn(orchestrationApiClient, "listExamples").mockResolvedValue({
      examples: [],
    });
    const { container } = render(<App />);

    // 5.3.1.5a：topbarCss 深色规则
    const topbarCss = findCssContaining(container, "wfp-topbar-btn");
    expect(topbarCss).toContain(`.${THEME_ROOT_CLASS} .wfp-topbar-btn`);
    expect(topbarCss).toContain(darkTokens.surface.bg);
    expect(topbarCss).toContain(`outline-color: ${darkTokens.text.focus}`);

    // 5.6.1.2a：footerCss 深色规则
    const footerCss = findCssContaining(container, "wfp-footer-btn");
    expect(footerCss).toContain(`.${THEME_ROOT_CLASS} .wfp-footer-btn`);
  });
});

describe("⑧ 浅色冻结断言不破", () => {
  it("四注入块浅色规则文本保留关键断言", () => {
    vi.spyOn(orchestrationApiClient, "listExamples").mockResolvedValue({
      examples: [],
    });
    const { container } = render(<App />);

    // 5.3.3.3：topbarCss 浅色段关键文本仍在
    const topbarCss = findCssContaining(container, "wfp-topbar-btn");
    expect(topbarCss).toContain(".wfp-topbar-btn:active");
    expect(topbarCss).toContain("120ms");
    expect(topbarCss).toContain("#1a1a1a");

    // footerCss 浅色段
    const footerCss = findCssContaining(container, "wfp-footer-btn");
    expect(footerCss).toContain("#d0d0d0");
    expect(footerCss).toContain("font-size: 13px");

    // paletteCss 浅色段（1.2.4 全清单）
    const paletteCss = findCssContaining(container, "wfp-palette-card");
    expect(paletteCss).toContain(
      ".wfp-palette-card:hover:not(.wfp-palette-card--dragging)",
    );

    // 全部规则选择器以 . 开头（无裸元素选择器）
    expect(topbarCss).not.toContain("button {");
    expect(footerCss).not.toContain("button {");
  });
});

describe("⑨ header 既有断言顺序保护", () => {
  it("header 按钮顺序：范例库折叠钮在前，「管理后台 →」仍为首个挂皮肤类的按钮", () => {
    vi.spyOn(orchestrationApiClient, "listExamples").mockResolvedValue({
      examples: [],
    });
    const { container } = render(<App />);
    const header = container.querySelector("header");

    // 顶栏首个按钮是范例库折叠钮（默认收起，节省顶栏空间）
    const firstBtn = header?.querySelector("button");
    expect(firstBtn?.className).toContain("wfp-example-toggle");
    expect(firstBtn?.getAttribute("aria-expanded")).toBe("false");
    expect(firstBtn?.textContent).toContain("范例库");

    // 5.1.1.8a：管理后台入口仍是首个挂 wfp-topbar-btn 皮肤类的按钮（切换控件在其后）
    const skinBtn = header?.querySelector("button.wfp-topbar-btn");
    expect(skinBtn?.textContent).toBe("管理后台 →");
  });
});

describe("⑩ 管理后台主题一致", () => {
  it("切换深色后进入管理后台 → admin 根 div 含深色类；返回画布仍深色", () => {
    vi.spyOn(orchestrationApiClient, "listExamples").mockResolvedValue({
      examples: [],
    });
    const { container } = render(<App />);

    // 先切换至深色
    fireEvent.click(screen.getByText("深色"));
    expect(container.querySelector(`.${THEME_ROOT_CLASS}`)).not.toBeNull();

    // 5.2.1.6a / 5.4.1.2a：进入管理后台，admin 根 div 也含深色类
    fireEvent.click(screen.getByText("管理后台 →"));
    expect(container.querySelector(`.${THEME_ROOT_CLASS}`)).not.toBeNull();
    // 管理后台默认面板（治理总览）自带 h1，须按可访问名过滤至后台标题
    expect(screen.getByRole("heading", { level: 1, name: "管理后台" })).toHaveTextContent(
      "管理后台",
    );

    // 返回画布仍深色
    fireEvent.click(screen.getByText("← 返回画布编辑器"));
    expect(container.querySelector(`.${THEME_ROOT_CLASS}`)).not.toBeNull();
    expect(screen.getByText("浅色")).toBeInTheDocument();
  });
});

describe("⑪ ReactFlow 深色规则", () => {
  it("WorkflowCanvas 注入块含 .wfp-theme--dark .react-flow__edge-path 等规则", () => {
    vi.spyOn(orchestrationApiClient, "listExamples").mockResolvedValue({
      examples: [],
    });
    const { container } = render(<App />);

    // 5.5.1.9a / 6.3.6：rfThemeCss 深色作用域规则
    const rfCss = findCssContaining(container, "react-flow__edge-path");
    expect(rfCss).toContain(`.${THEME_ROOT_CLASS} .react-flow__edge-path`);
    expect(rfCss).toContain(darkTokens.reactflow.edgeStroke);
  });
});

describe("⑫ 主/文字对比度核算常量", () => {
  it("darkTokens 取值与设计表一致；主字 ≥4.5:1、提示 ≥3:1", () => {
    // 5.6.1.1a / 6.3：深色令牌字面量核对
    expect(darkTokens.surface.bg).toBe("#1e1e1e");
    expect(darkTokens.text.primary).toBe("#f5f5f5");
    expect(darkTokens.text.hint).toBe("#8a8a8a");

    // 主文字对比度 ≥ 4.5（WCAG AA）
    const primaryContrast = contrastRatio(
      darkTokens.text.primary,
      darkTokens.surface.bg,
    );
    expect(primaryContrast).toBeGreaterThanOrEqual(4.5);

    // 提示文字对比度 ≥ 3（WCAG AA 大字/UI 组件）
    const hintContrast = contrastRatio(
      darkTokens.text.hint,
      darkTokens.surface.bg,
    );
    expect(hintContrast).toBeGreaterThanOrEqual(3);

    // syncRootThemeClass 幂等性（spec 6.4.1）
    // jsdom 测试环境无 #root 挂载点（RTL 自建容器），构造后验证
    const rootEl = document.createElement("div");
    rootEl.id = "root";
    document.body.appendChild(rootEl);
    expect(rootEl.classList.contains(THEME_ROOT_CLASS)).toBe(false);
    syncRootThemeClass("dark");
    expect(rootEl.classList.contains(THEME_ROOT_CLASS)).toBe(true);
    syncRootThemeClass("dark");
    expect(rootEl.classList.contains(THEME_ROOT_CLASS)).toBe(true);
    syncRootThemeClass("light");
    expect(rootEl.classList.contains(THEME_ROOT_CLASS)).toBe(false);
    rootEl.remove();
  });
});