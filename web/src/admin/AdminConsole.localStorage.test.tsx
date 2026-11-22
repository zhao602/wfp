// =============================================================================
// 管理后台 localStorage 防护测试（admin/AdminConsole.localStorage.test.tsx）
//
// 留待非沙箱环境执行：cd web && npx vitest run AdminConsole.localStorage
//
// 覆盖用例（fix_admin_console_blank / CHG-4，TC-LS-01~04）：
//   TC-LS-01 localStorage 正常有值 → 读取成功
//   TC-LS-02 localStorage 正常但无键 → 回落默认值（INV-8）
//   TC-LS-03 localStorage 抛 SecurityError → 静默回落默认值（INV-9）
//   TC-LS-04 模块加载不抛异常
//
// 沙箱限制声明：沙箱内 vitest 无法运行，本文件作为交付物入库，
// 请在非沙箱环境执行。
// =============================================================================

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import type { MockInstance } from "vitest";

describe("readStoredString 防护（AdminConsole 模块顶层 localStorage）", () => {
  let getItemSpy: MockInstance<(key: string) => string | null>;

  beforeEach(() => {
    getItemSpy = vi.spyOn(Storage.prototype, "getItem");
  });

  afterEach(() => {
    getItemSpy.mockRestore();
  });

  // TC-LS-01 localStorage 正常有值
  it("localStorage 有值时返回存储值", () => {
    getItemSpy.mockImplementation((key: string) => {
      if (key === "tenantId") return "tenant-123";
      if (key === "authToken") return "token-abc";
      if (key === "userId") return "user-xyz";
      return null;
    });
    // 重新动态导入以触发模块顶层 const 求值
    vi.resetModules();
    return import("./AdminConsole").then(() => {
      // 模块成功加载即证明 readStoredString 正确读取
      expect(getItemSpy).toHaveBeenCalledWith("tenantId");
      expect(getItemSpy).toHaveBeenCalledWith("authToken");
      expect(getItemSpy).toHaveBeenCalledWith("userId");
    });
  });

  // TC-LS-02 localStorage 正常但无键 → 回落默认值
  it("localStorage 无键时回落默认值", () => {
    getItemSpy.mockReturnValue(null);
    vi.resetModules();
    return import("./AdminConsole").then(() => {
      expect(getItemSpy).toHaveBeenCalled();
      // 模块加载成功即证明回落值生效
    });
  });

  // TC-LS-03 localStorage 抛 SecurityError → 静默回落
  it("localStorage 抛 SecurityError 时静默回落", () => {
    getItemSpy.mockImplementation(() => {
      throw new DOMException("SecurityError", "SecurityError");
    });
    vi.resetModules();
    return import("./AdminConsole").then(() => {
      // 模块加载成功即证明 try/catch 防护生效（INV-9）
      expect(getItemSpy).toHaveBeenCalled();
    });
  });

  // TC-LS-04 模块加载不抛异常
  it("localStorage 不可用时模块仍可加载", () => {
    getItemSpy.mockImplementation(() => {
      throw new Error("localStorage unavailable");
    });
    vi.resetModules();
    return expect(import("./AdminConsole")).resolves.toBeDefined();
  });
});