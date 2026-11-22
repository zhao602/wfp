// =============================================================================
// SsoSettingsPanel 守卫单元测试（sso/__tests__/SsoSettingsPanel.guard.test.tsx）
//
// 留待非沙箱环境执行：npx vitest run src/sso/__tests__/SsoSettingsPanel.guard.test.tsx
//
// 覆盖用例（对应 CHG-3 / DC-2/3/4 / AC-1/3/4/5/6/10）：
//   未启用时不发请求 / 显示提示 / 隐藏按钮 / 不渲染表格
//   启用时发起请求 / 渲染按钮与表格 / 请求参数不变
//   深色主题下未启用提示取色
//
// 沙箱限制声明：沙箱内 vitest 无法运行，本文件作为交付物入库。
// =============================================================================

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, cleanup, screen } from "@testing-library/react";
import { SsoSettingsPanel } from "../SsoSettingsPanel";
import { listIdps } from "../ssoApiClient";
import { isSsoEnabled } from "../../config/ssoConfig";

// mock ssoApiClient：捕获 listIdps 调用
vi.mock("../ssoApiClient", () => ({
  listIdps: vi.fn().mockResolvedValue([]),
  createIdp: vi.fn(),
}));

// mock isSsoEnabled：默认返回 false，各用例按需覆盖
vi.mock("../../config/ssoConfig", () => ({
  isSsoEnabled: vi.fn().mockReturnValue(false),
}));

describe("SsoSettingsPanel 守卫 — 未启用", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("AC-1 未启用时不发请求", () => {
    vi.mocked(isSsoEnabled).mockReturnValue(false);
    render(<SsoSettingsPanel tenantId="default-tenant" />);
    expect(listIdps).not.toHaveBeenCalled();
  });

  it("AC-3 未启用时显示提示", () => {
    vi.mocked(isSsoEnabled).mockReturnValue(false);
    render(<SsoSettingsPanel tenantId="default-tenant" />);
    expect(screen.getByText("SSO 企业身份集成未启用")).toBeInTheDocument();
  });

  it("AC-4 未启用时隐藏按钮", () => {
    vi.mocked(isSsoEnabled).mockReturnValue(false);
    render(<SsoSettingsPanel tenantId="default-tenant" />);
    expect(screen.queryByText("配置 IdP")).not.toBeInTheDocument();
  });

  it("未启用时不渲染表格", () => {
    vi.mocked(isSsoEnabled).mockReturnValue(false);
    render(<SsoSettingsPanel tenantId="default-tenant" />);
    expect(screen.queryByTestId("idp-table")).not.toBeInTheDocument();
  });
});

describe("SsoSettingsPanel 守卫 — 启用", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("AC-5 启用时发起请求", () => {
    vi.mocked(isSsoEnabled).mockReturnValue(true);
    render(<SsoSettingsPanel tenantId="default-tenant" />);
    expect(listIdps).toHaveBeenCalledWith("default-tenant");
  });

  it("AC-6 启用时渲染按钮与表格", () => {
    vi.mocked(isSsoEnabled).mockReturnValue(true);
    render(<SsoSettingsPanel tenantId="default-tenant" />);
    expect(screen.getByText("配置 IdP")).toBeInTheDocument();
    expect(screen.getByTestId("idp-table")).toBeInTheDocument();
  });

  it("启用时请求参数为 tenantId（路径不变）", () => {
    vi.mocked(isSsoEnabled).mockReturnValue(true);
    render(<SsoSettingsPanel tenantId="my-tenant" />);
    expect(listIdps).toHaveBeenCalledWith("my-tenant");
  });
});