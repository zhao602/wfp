// =============================================================================
// OEM 品牌配置单元测试（src/brand/__tests__/brandConfig.test.ts，roadmap B5）
//
// 覆盖：
//   1. 默认值=自建平台现状（无 env 注入时 13 常量逐字节一致）
//   2. 构建期 env 覆盖（vi.stubEnv + vi.resetModules + 动态 import——模块
//      加载期读 env，须重载模块求值）
//   3. env 空白回退默认值（orDefault 空白回落）
// =============================================================================

import { describe, it, expect, vi, afterEach } from "vitest";
import {
  PRODUCT_NAME,
  PRIMARY_COLOR,
  PRIMARY_COLOR_HOVER,
  PRIMARY_COLOR_ACTIVE,
  PRIMARY_COLOR_LIGHT,
  PRIMARY_COLOR_LIGHT_HOVER,
  PRIMARY_COLOR_SOFT,
  PRIMARY_COLOR_TINT,
  PRIMARY_COLOR_TINT_HOVER,
  PRIMARY_COLOR_DARK_TINT,
  PRIMARY_COLOR_DARK_TINT_HOVER,
  PRIMARY_COLOR_RGB,
  PRIMARY_COLOR_LIGHT_RGB,
} from "../brandConfig";

describe("OEM 品牌配置（brandConfig）", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("默认值=自建平台现状：无 env 注入时 13 常量逐字节一致", () => {
    expect(PRODUCT_NAME).toBe("工作流编排平台");
    expect(PRIMARY_COLOR).toBe("#1976d2");
    expect(PRIMARY_COLOR_HOVER).toBe("#1565c0");
    expect(PRIMARY_COLOR_ACTIVE).toBe("#0d47a1");
    expect(PRIMARY_COLOR_LIGHT).toBe("#42a5f5");
    expect(PRIMARY_COLOR_LIGHT_HOVER).toBe("#2196f3");
    expect(PRIMARY_COLOR_SOFT).toBe("#8ec5fc");
    expect(PRIMARY_COLOR_TINT).toBe("#e8f1fb");
    expect(PRIMARY_COLOR_TINT_HOVER).toBe("#dceaf8");
    expect(PRIMARY_COLOR_DARK_TINT).toBe("#1a2a3a");
    expect(PRIMARY_COLOR_DARK_TINT_HOVER).toBe("#213045");
    expect(PRIMARY_COLOR_RGB).toBe("25, 118, 210");
    expect(PRIMARY_COLOR_LIGHT_RGB).toBe("66, 165, 245");
  });

  it("构建期 env 覆盖：stubEnv 后重载模块读到品牌值，RGB 随 hex 推导", async () => {
    vi.stubEnv("VITE_OEM_PRODUCT_NAME", "AcmeFlow");
    vi.stubEnv("VITE_OEM_PRIMARY_COLOR", "#7c3aed");
    vi.stubEnv("VITE_OEM_PRIMARY_COLOR_LIGHT", "#a78bfa");
    vi.resetModules();
    const mod = await import("../brandConfig");
    expect(mod.PRODUCT_NAME).toBe("AcmeFlow");
    expect(mod.PRIMARY_COLOR).toBe("#7c3aed");
    expect(mod.PRIMARY_COLOR_RGB).toBe("124, 58, 237");
    expect(mod.PRIMARY_COLOR_LIGHT).toBe("#a78bfa");
    expect(mod.PRIMARY_COLOR_LIGHT_RGB).toBe("167, 139, 250");
    // 未 stub 的常量回落默认值（部分覆盖语义）
    expect(mod.PRIMARY_COLOR_HOVER).toBe("#1565c0");
  });

  it("env 空白值回退默认（orDefault 空白回落，非法注入不产生空产品名）", async () => {
    vi.stubEnv("VITE_OEM_PRODUCT_NAME", "   ");
    vi.resetModules();
    const mod = await import("../brandConfig");
    expect(mod.PRODUCT_NAME).toBe("工作流编排平台");
  });
});
