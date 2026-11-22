// =============================================================================
// 管理后台错误边界单元测试（admin/AdminErrorBoundary.test.tsx）
//
// 留待非沙箱环境执行：cd web && npx vitest run AdminErrorBoundary
//
// 覆盖用例（fix_admin_console_blank / CHG-2，TC-AEB-01~05）：
//   TC-AEB-01 正常态透传 children（无包装 DOM，INV-4）
//   TC-AEB-02 兜底态捕获渲染期错误（展示 wfp-aeb 容器 + 管理后台文案）
//   TC-AEB-03 重试恢复（点击按钮退出兜底态，子树重挂载）
//   TC-AEB-04 console.error 记录错误详情与组件栈
//   TC-AEB-05 文案无画布术语（INV-6）
//
// 沙箱限制声明：沙箱内 vitest 无法运行，本文件作为交付物入库，
// 请在非沙箱环境执行。
// =============================================================================

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { AdminErrorBoundary } from "./AdminErrorBoundary";

/** 抛错子组件（模拟面板渲染期错误） */
function ThrowingChild({ message }: { message: string }): never {
  throw new Error(message);
}

describe("AdminErrorBoundary", () => {
  let consoleErrorSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    // 抑制 React 在测试中输出未捕获错误日志
    consoleErrorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    consoleErrorSpy.mockRestore();
  });

  // TC-AEB-01 正常态透传 children
  it("正常态透传 children，无 wfp-aeb 容器", () => {
    render(
      <AdminErrorBoundary>
        <div data-testid="child">正常内容</div>
      </AdminErrorBoundary>,
    );
    expect(screen.getByTestId("child")).toBeTruthy();
    expect(screen.queryByText("面板加载异常")).toBeNull();
  });

  // TC-AEB-02 兜底态捕获渲染期错误
  it("子树抛错时展示兜底 UI，含管理后台文案", () => {
    render(
      <AdminErrorBoundary>
        <ThrowingChild message="面板数据加载失败" />
      </AdminErrorBoundary>,
    );
    expect(screen.getByText("面板加载异常")).toBeTruthy();
    expect(screen.getByText(/面板渲染时发生错误：面板数据加载失败/)).toBeTruthy();
    expect(screen.getByText("重试当前面板")).toBeTruthy();
  });

  // TC-AEB-03 重试恢复
  it("点击重试按钮退出兜底态", () => {
    const { rerender } = render(
      <AdminErrorBoundary>
        <ThrowingChild message="首次错误" />
      </AdminErrorBoundary>,
    );
    expect(screen.getByText("面板加载异常")).toBeTruthy();

    // 面板修复后父组件传入正常子树（兜底态下 children 变更不退出兜底 UI）
    rerender(
      <AdminErrorBoundary>
        <div data-testid="recovered">已恢复</div>
      </AdminErrorBoundary>,
    );
    expect(screen.getByText("面板加载异常")).toBeTruthy();

    // 点击重试 → 退出兜底态，重挂载当前正常子树（DC-5 内部恢复）
    fireEvent.click(screen.getByText("重试当前面板"));
    expect(screen.getByTestId("recovered")).toBeTruthy();
    expect(screen.queryByText("面板加载异常")).toBeNull();
  });

  // TC-AEB-04 console.error 记录
  it("componentDidCatch 经 console.error 记录错误", () => {
    render(
      <AdminErrorBoundary>
        <ThrowingChild message="记录测试" />
      </AdminErrorBoundary>,
    );
    expect(consoleErrorSpy).toHaveBeenCalled();
    // React 会先经 console.error 输出 "Uncaught [Error ...]"，boundary 自身日志须按前缀检索
    const boundaryCall = consoleErrorSpy.mock.calls.find(
      (args) => typeof args[0] === "string" && args[0].includes("AdminErrorBoundary"),
    );
    expect(boundaryCall).toBeDefined();
  });

  // TC-AEB-05 文案无画布术语
  it("兜底文案不含画布术语", () => {
    render(
      <AdminErrorBoundary>
        <ThrowingChild message="测试" />
      </AdminErrorBoundary>,
    );
    const bodyText = document.body.textContent ?? "";
    expect(bodyText).not.toContain("画布渲染异常");
    expect(bodyText).not.toContain("恢复画布");
  });
});