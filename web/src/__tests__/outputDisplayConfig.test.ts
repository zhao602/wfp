// =============================================================================
// 输出展示节点配置归一化纯函数对照测试（src/__tests__/outputDisplayConfig.test.ts）
//
// 执行：cd web && npx vitest run src/__tests__/outputDisplayConfig.test.ts
//
// 覆盖（fix_raw_not_iterable CHG-06，DC-06/DC-07）：
//   T7.1/T7.2 normalizeOutputReferences / normalizeOutputMode 全形态矩阵
//     （null ≡ undefined；对任意入参零抛出；与后端
//      src/orchestration_platform/domain/models/node_configurations.py:379
//      `if raw is None: return []` 逐条对照 —— 前端结果 ≡ 后端结果）
//   T7.3 修复前行为对照：修复前 normalizeOutputReferences(null) 抛
//     "TypeError: raw is not iterable"（旧 :18 仅拦 undefined，:25 for...of 迭代 null）；
//     本文件 null 用例守护该缺陷不再复发（AC-06 反向确认）
//   T8.1/T8.2 installGlobalErrorListeners 事件载荷与幂等（CHG-05，G-05）
// =============================================================================

import { describe, it, expect, vi } from "vitest";
import {
  normalizeOutputReferences,
  normalizeOutputMode,
} from "../workflow-definition/outputDisplayConfig";
import { installGlobalErrorListeners } from "../errorObservability";

// ---------------------------------------------------------------------------
// T7.1 normalizeOutputReferences 全形态矩阵（对照后端 normalize_output_references）
// 后端逐条对照（node_configurations.py:379 起）：前端结果 ≡ 后端结果
//   None → [] ｜ "" → [] ｜ 标量 → [标量] ｜ 数组保序剔空白不去重 ｜ 非法形态 → []
// ---------------------------------------------------------------------------

describe("normalizeOutputReferences 全形态矩阵（T7.1）", () => {
  it("未配置形态：undefined / null / \"\" → []，且 null ≡ undefined（修复前 null 抛 raw is not iterable）", () => {
    expect(normalizeOutputReferences(undefined)).toStrictEqual([]);
    expect(normalizeOutputReferences(null)).toStrictEqual([]);
    expect(normalizeOutputReferences("")).toStrictEqual([]);
    expect(normalizeOutputReferences(null)).toStrictEqual(normalizeOutputReferences(undefined));
  });

  it("标量与数组：\"a\"→[\"a\"]；[]→[]；[\"a\"]→[\"a\"]；[\"a\",\"b\"]→[\"a\",\"b\"]", () => {
    expect(normalizeOutputReferences("a")).toStrictEqual(["a"]);
    expect(normalizeOutputReferences([])).toStrictEqual([]);
    expect(normalizeOutputReferences(["a"])).toStrictEqual(["a"]);
    expect(normalizeOutputReferences(["a", "b"])).toStrictEqual(["a", "b"]);
  });

  it("数组空白元素剔除保序：[\"a\",\"\",\"  \",\"b\"] → [\"a\",\"b\"]", () => {
    expect(normalizeOutputReferences(["a", "", "  ", "b"])).toStrictEqual(["a", "b"]);
  });

  it("类型外形态（运行时防御，CHG-02）：1 / true / {} → [] 且零抛出", () => {
    const unexpected: unknown[] = [1, true, {}];
    for (const raw of unexpected) {
      // 显式断言仅用于绕过编译期类型，验证「对任意入参零抛出」运行时契约
      expect(() => normalizeOutputReferences(raw as string)).not.toThrow();
      expect(normalizeOutputReferences(raw as string)).toStrictEqual([]);
    }
  });
});

// ---------------------------------------------------------------------------
// T7.2 normalizeOutputMode 全形态矩阵（对照后端 normalize_output_mode）
// 后端逐条对照：缺省/null → ["screen"] ｜ 仅保留 {"screen","file"} ｜ 过滤后空回落 ["screen"]
// ---------------------------------------------------------------------------

describe("normalizeOutputMode 全形态矩阵（T7.2）", () => {
  it("未配置与空：undefined / null / [] → [\"screen\"]；类型外 \"\" 同回落", () => {
    expect(normalizeOutputMode(undefined)).toStrictEqual(["screen"]);
    expect(normalizeOutputMode(null)).toStrictEqual(["screen"]);
    expect(normalizeOutputMode([])).toStrictEqual(["screen"]);
    // "" 为类型外形态，经显式断言验证运行时回落（CHG-03）
    expect(normalizeOutputMode("" as unknown as string[])).toStrictEqual(["screen"]);
  });

  it("合法输入：[\"screen\"]→[\"screen\"]；[\"screen\",\"file\"]→[\"screen\",\"file\"]；[\"screen\",\"bogus\"]→[\"screen\"]", () => {
    expect(normalizeOutputMode(["screen"])).toStrictEqual(["screen"]);
    expect(normalizeOutputMode(["screen", "file"])).toStrictEqual(["screen", "file"]);
    expect(normalizeOutputMode(["screen", "bogus"])).toStrictEqual(["screen"]);
  });

  it("类型外形态（运行时防御，CHG-03）：1 / true / {} → [\"screen\"] 且零抛出", () => {
    const unexpected: unknown[] = [1, true, {}];
    for (const raw of unexpected) {
      // 显式断言仅用于绕过编译期类型，验证运行时兜底契约
      expect(() => normalizeOutputMode(raw as string[])).not.toThrow();
      expect(normalizeOutputMode(raw as string[])).toStrictEqual(["screen"]);
    }
  });

  it("后置条件：返回值恒非空且 ⊆ {\"screen\",\"file\"}", () => {
    const variants: Array<string[] | null | undefined> = [
      undefined, null, [], ["screen"], ["file"], ["screen", "file"], ["screen", "bogus"], ["bogus"],
    ];
    for (const raw of variants) {
      const result = normalizeOutputMode(raw);
      expect(result.length).toBeGreaterThan(0);
      for (const mode of result) {
        expect(mode === "screen" || mode === "file").toBe(true);
      }
    }
  });
});

// ---------------------------------------------------------------------------
// T8 installGlobalErrorListeners 全局监听（CHG-05 / G-05）
// 注：jsdom 25 无 PromiseRejectionEvent 构造器 → unhandledrejection 以普通
// Event + reason 属性等价派发（handler 仅读取 .reason，行为一致）
// ---------------------------------------------------------------------------

describe("installGlobalErrorListeners 全局监听（T8.1/T8.2）", () => {
  it("T8.1a window error 事件 → [global-error] 前缀 console.error（error 优先，message 兜底）", () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    installGlobalErrorListeners();
    const err = new Error("boom");
    window.dispatchEvent(
      new ErrorEvent("error", { error: err, message: "m", filename: "a.ts", lineno: 3, colno: 7 }),
    );
    window.dispatchEvent(
      new ErrorEvent("error", { message: "fallback", filename: "b.ts", lineno: 1, colno: 2 }),
    );
    expect(errorSpy).toHaveBeenCalledTimes(2);
    expect(errorSpy.mock.calls[0][0]).toBe("[global-error]");
    expect(errorSpy.mock.calls[0][1]).toBe(err);
    expect(errorSpy.mock.calls[0][2]).toBe("a.ts");
    expect(errorSpy.mock.calls[1][1]).toBe("fallback");
    errorSpy.mockRestore();
  });

  it("T8.1b unhandledrejection 事件 → [global-error] 前缀 console.error（reason 载荷）", () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    installGlobalErrorListeners();
    const event = new Event("unhandledrejection");
    Reflect.set(event, "reason", "rejected");
    window.dispatchEvent(event);
    expect(errorSpy).toHaveBeenCalledTimes(1);
    expect(errorSpy.mock.calls[0][0]).toBe("[global-error]");
    expect(errorSpy.mock.calls[0][1]).toBe("rejected");
    errorSpy.mockRestore();
  });

  it("T8.2 幂等：重复调用不重复注册（G-05）", async () => {
    vi.resetModules();
    const fresh = await import("../errorObservability");
    const addSpy = vi.spyOn(window, "addEventListener");
    fresh.installGlobalErrorListeners();
    expect(addSpy).toHaveBeenCalledTimes(2);
    fresh.installGlobalErrorListeners();
    expect(addSpy).toHaveBeenCalledTimes(2);
    addSpy.mockRestore();
  });
});
