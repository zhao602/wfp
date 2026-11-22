// =============================================================================
// SsoSettingsPanel 深色主题测试（sso/__tests__/SsoSettingsPanel.dark.test.tsx）
//
// 留待非沙箱环境执行：npx vitest run src/sso/__tests__/SsoSettingsPanel.dark.test.tsx
//
// 覆盖用例（对应 CHG-3 / DC-3 / INV-7 / AC-10）：
//   深色模式下未启用提示含 darkTokens.text.hint 值
//   浅色模式下未启用提示含 #9e9e9e
//
// 沙箱限制声明：沙箱内 vitest 无法运行，本文件作为交付物入库。
// =============================================================================

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, cleanup, screen } from "@testing-library/react";
import { SsoSettingsPanel } from "../SsoSettingsPanel";

vi.mock("../ssoApiClient", () => ({
  listIdps: vi.fn().mockResolvedValue([]),
  createIdp: vi.fn(),
}));

vi.mock("../../config/ssoConfig", () => ({
  isSsoEnabled: vi.fn().mockReturnValue(false),
}));

vi.mock("../../theme/themeMode", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../theme/themeMode")>();
  return {
    ...actual,
    useThemeMode: vi.fn().mockReturnValue("light"),
  };
});

import { useThemeMode, darkTokens } from "../../theme/themeMode";

describe("SsoSettingsPanel 深色主题 — 未启用提示取色", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("AC-10 深色模式下未启用提示含 darkTokens.text.hint 值", () => {
    vi.mocked(useThemeMode).mockReturnValue("dark");
    const { container } = render(<SsoSettingsPanel tenantId="default-tenant" />);
    const styleContent = container.querySelector("style")?.textContent ?? "";
    expect(styleContent).toContain(darkTokens.text.hint);
    expect(screen.getByText("SSO 企业身份集成未启用")).toBeInTheDocument();
  });

  it("浅色模式下未启用提示含 #9e9e9e", () => {
    vi.mocked(useThemeMode).mockReturnValue("light");
    const { container } = render(<SsoSettingsPanel tenantId="default-tenant" />);
    const styleContent = container.querySelector("style")?.textContent ?? "";
    expect(styleContent).toContain("#9e9e9e");
  });
});