// =============================================================================
// ssoConfig 单元测试（src/__tests__/ssoConfig.test.ts）
//
// 留待非沙箱环境执行：npx vitest run src/__tests__/ssoConfig.test.ts
//
// 覆盖用例（对应 CHG-2 / DC-1 / INV-9 / AC-12）：
//   VITE_SSO_ENABLED 各取值下 isSsoEnabled() 的返回值
//
// 沙箱限制声明：沙箱内 vitest 无法运行，本文件作为交付物入库。
// =============================================================================

import { describe, it, expect, vi, afterEach } from "vitest";
import { isSsoEnabled } from "../config/ssoConfig";

describe("ssoConfig", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it('VITE_SSO_ENABLED === "true" → true', () => {
    vi.stubEnv("VITE_SSO_ENABLED", "true");
    expect(isSsoEnabled()).toBe(true);
  });

  it('VITE_SSO_ENABLED === "false" → false', () => {
    vi.stubEnv("VITE_SSO_ENABLED", "false");
    expect(isSsoEnabled()).toBe(false);
  });

  it("VITE_SSO_ENABLED === undefined → false（缺失安全降级，AC-12）", () => {
    vi.stubEnv("VITE_SSO_ENABLED", undefined);
    expect(isSsoEnabled()).toBe(false);
  });

  it('VITE_SSO_ENABLED === "" → false', () => {
    vi.stubEnv("VITE_SSO_ENABLED", "");
    expect(isSsoEnabled()).toBe(false);
  });

  it('VITE_SSO_ENABLED === "True" → false（严格比较，大小写敏感）', () => {
    vi.stubEnv("VITE_SSO_ENABLED", "True");
    expect(isSsoEnabled()).toBe(false);
  });

  it('VITE_SSO_ENABLED === "1" → false（非 "true" 字符串）', () => {
    vi.stubEnv("VITE_SSO_ENABLED", "1");
    expect(isSsoEnabled()).toBe(false);
  });
});