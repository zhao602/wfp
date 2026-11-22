// =============================================================================
// 管理台市场导航测试（src/admin/__tests__/AdminConsole.market.test.tsx）
//
// 留待非沙箱环境执行：cd web && npx vitest run src/admin/__tests__/AdminConsole.market.test.tsx
//
// 覆盖用例（agent_tool_marketplace T18.6 / AC-22 / AC-23）：
//   TC-ACM-01 侧边栏出现「Agent 市场」「Tool 市场」两入口
//   TC-ACM-02 点击入口可切换对应面板
//   TC-ACM-03 面板渲染异常 → AdminErrorBoundary 兜底且其他面板仍可切换
//   TC-ACM-04 兜底重试恢复面板渲染
//
// 测试策略：桩 AgentMarketPanel / ToolMarketPanel（可控抛错），
// 聚焦导航追加（MenuKey/MENU_ITEMS/renderPanel 三处同步）与错误兜底（Z-14）。
// =============================================================================

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { AdminConsole } from "../AdminConsole";

// React 18 dev 会经事件派发重放渲染期错误，jsdom 将其上报为未捕获错误并导致测试误判失败；
// 本文件刻意触发渲染异常以验证 AdminErrorBoundary 兜底（Z-14），抑制该上报（仅本文件作用域）。
window.addEventListener("error", (event: ErrorEvent): void => {
  event.preventDefault();
});

/** Agent 市场面板桩状态（vi.hoisted：vi.mock 工厂提升后仍可引用） */
const agentPanelState = vi.hoisted(() => ({ shouldThrow: false }));

vi.mock("../../market/AgentMarketPanel", () => ({
  AgentMarketPanel: (): JSX.Element | never => {
    if (agentPanelState.shouldThrow) {
      throw new Error("Agent 市场面板崩溃");
    }
    return <div data-testid="amp-stub" />;
  },
}));

vi.mock("../../market/ToolMarketPanel", () => ({
  ToolMarketPanel: (): JSX.Element => <div data-testid="tmp-stub" />,
}));

let consoleErrorSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  agentPanelState.shouldThrow = false;
  // 抑制 React 在刻意的渲染异常用例中输出错误日志
  consoleErrorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  cleanup();
  consoleErrorSpy.mockRestore();
  vi.clearAllMocks();
});

describe("AdminConsole 市场导航（agent_market / tool_market）", () => {
  it("TC-ACM-01 侧边栏出现「Agent 市场」「Tool 市场」两入口", () => {
    render(<AdminConsole onBackToCanvas={() => {}} />);

    expect(screen.getByText("Agent 市场")).toBeInTheDocument();
    expect(screen.getByText("Tool 市场")).toBeInTheDocument();
  });

  it("TC-ACM-02 点击入口可切换对应面板", () => {
    render(<AdminConsole onBackToCanvas={() => {}} />);

    fireEvent.click(screen.getByText("Agent 市场"));
    expect(screen.getByTestId("amp-stub")).toBeInTheDocument();

    fireEvent.click(screen.getByText("Tool 市场"));
    expect(screen.getByTestId("tmp-stub")).toBeInTheDocument();
  });

  it("TC-ACM-03 面板渲染异常 → AdminErrorBoundary 兜底且其他面板仍可切换", () => {
    agentPanelState.shouldThrow = true;
    render(<AdminConsole onBackToCanvas={() => {}} />);

    fireEvent.click(screen.getByText("Agent 市场"));
    // AdminErrorBoundary 兜底 UI（Z-14：零改动复用）
    expect(screen.getByText("面板加载异常")).toBeInTheDocument();
    expect(screen.getByText(/Agent 市场面板崩溃/)).toBeInTheDocument();

    // 其他面板仍可切换：侧边栏切换后经兜底「重试当前面板」（DC-5 内部恢复）退出兜底态，
    // 新面板正常渲染（AdminErrorBoundary 不因 children 变化自动复位，属既有设计语义）。
    fireEvent.click(screen.getByText("Tool 市场"));
    expect(screen.getByText("重试当前面板")).toBeInTheDocument();
    fireEvent.click(screen.getByText("重试当前面板"));
    expect(screen.getByTestId("tmp-stub")).toBeInTheDocument();
    expect(screen.queryByText("面板加载异常")).toBeNull();
  });

  it("TC-ACM-04 兜底重试恢复面板渲染", () => {
    agentPanelState.shouldThrow = true;
    render(<AdminConsole onBackToCanvas={() => {}} />);

    fireEvent.click(screen.getByText("Agent 市场"));
    expect(screen.getByText("面板加载异常")).toBeInTheDocument();

    // 恢复桩状态后点击「重试当前面板」→ 面板恢复渲染
    agentPanelState.shouldThrow = false;
    fireEvent.click(screen.getByText("重试当前面板"));
    expect(screen.getByTestId("amp-stub")).toBeInTheDocument();
  });
});
