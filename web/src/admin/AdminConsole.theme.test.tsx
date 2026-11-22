// =============================================================================
// 管理后台深色模式背景色回归测试（admin/AdminConsole.theme.test.tsx）
//
// 留待非沙箱环境执行：cd web && npx vitest run AdminConsole.theme
//
// 覆盖用例（fix_admin_console_blank / CHG-1，TC-TH-01~03）：
//   TC-TH-01 深色模式 main 背景为 darkTokens.surface.bg（非 #ffffff）
//   TC-TH-02 浅色模式 main 背景为 undefined（零回归，INV-1）
//   TC-TH-03 深色模式无 #ffffff 字面量（INV-3）
//
// 沙箱限制声明：沙箱内 vitest 无法运行，本文件作为交付物入库，
// 请在非沙箱环境执行。
// =============================================================================

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render } from "@testing-library/react";
import { AdminConsole } from "./AdminConsole";
import { darkTokens } from "../theme/themeMode";

/** 主题 mock */
let mockTheme: "light" | "dark" = "light";
vi.mock("../theme/themeMode", async () => {
  const actual = await vi.importActual<typeof import("../theme/themeMode")>(
    "../theme/themeMode",
  );
  return { ...actual, useThemeMode: () => mockTheme };
});

/** 查找 main 元素 */
function getMainElement(): HTMLElement {
  return document.querySelector("main") as HTMLElement;
}

describe("AdminConsole 深色模式背景色", () => {
  beforeEach(() => {
    mockTheme = "light";
  });

  // TC-TH-01 深色模式 main 背景为 darkTokens.surface.bg
  it("深色模式 main 背景为 darkTokens.surface.bg", () => {
    mockTheme = "dark";
    render(<AdminConsole onBackToCanvas={() => {}} />);
    const main = getMainElement();
    expect(main).toBeTruthy();
    // jsdom 会将内联 hex 色序列化为 rgb()，须经 toHaveStyle 归一化比较
    expect(main).toHaveStyle({ backgroundColor: darkTokens.surface.bg });
  });

  // TC-TH-02 浅色模式 main 背景为 undefined（零回归）
  it("浅色模式 main 背景为 undefined", () => {
    mockTheme = "light";
    render(<AdminConsole onBackToCanvas={() => {}} />);
    const main = getMainElement();
    expect(main).toBeTruthy();
    expect(main.style.backgroundColor).toBe("");
  });

  // TC-TH-03 深色模式无 #ffffff 字面量
  it("深色模式 main 背景不等于 #ffffff", () => {
    mockTheme = "dark";
    render(<AdminConsole onBackToCanvas={() => {}} />);
    const main = getMainElement();
    expect(main.style.backgroundColor).not.toBe("#ffffff");
    expect(main.style.backgroundColor).not.toBe("rgb(255, 255, 255)");
  });
});