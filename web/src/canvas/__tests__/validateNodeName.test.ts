// =============================================================================
// 节点名称校验纯函数单元测试（canvas/__tests__/validateNodeName.test.ts）
//
// 留待非沙箱环境执行：cd web && npx vitest run validateNodeName
//
// 覆盖用例（node_name_edit / DC-3，TC-VN-01~05）：
//   TC-VN-01 合法名称通过校验（含中文、边界 32 字符、中文标点空格）
//   TC-VN-02 非空校验（空串、全空白 → "节点名称不能为空"）
//   TC-VN-03 长度校验（33 字符 → "节点名称不能超过 32 个字符"）
//   TC-VN-04 字符集校验（控制字符、零宽空格、BOM → "节点名称包含不支持的字符"）
//   TC-VN-05 短路顺序（既空又超长 → 优先返回非空原因）
//
// 沙箱限制声明：沙箱内 vitest 无法运行，本文件作为交付物入库，
// 请在非沙箱环境执行。
// =============================================================================

import { describe, it, expect } from "vitest";
import { validateNodeName } from "../NodeConfigurationPanel";

describe("validateNodeName", () => {
  // TC-VN-01 合法名称通过校验
  describe("合法名称", () => {
    it("普通英文标识", () => {
      const result = validateNodeName("Agent");
      expect(result).toEqual({ ok: true });
    });

    it("中文名称", () => {
      const result = validateNodeName("推理器");
      expect(result).toEqual({ ok: true });
    });

    it("边界 32 字符（含中文）", () => {
      const name = "推理器".repeat(10) + "推推"; // 32 字符
      expect(name.length).toBe(32);
      const result = validateNodeName(name);
      expect(result).toEqual({ ok: true });
    });

    it("含中文标点与空格", () => {
      const result = validateNodeName("数据清洗 · 步骤一");
      expect(result).toEqual({ ok: true });
    });
  });

  // TC-VN-02 非空校验
  describe("非空校验", () => {
    it("空串", () => {
      const result = validateNodeName("");
      expect(result).toEqual({ ok: false, reason: "节点名称不能为空" });
    });

    it("全空白", () => {
      const result = validateNodeName("   ");
      expect(result).toEqual({ ok: false, reason: "节点名称不能为空" });
    });

    it("仅含制表符", () => {
      const result = validateNodeName("\t\t");
      expect(result).toEqual({ ok: false, reason: "节点名称不能为空" });
    });
  });

  // TC-VN-03 长度校验
  describe("长度校验", () => {
    it("33 字符超出上限", () => {
      const name = "a".repeat(33);
      const result = validateNodeName(name);
      expect(result).toEqual({ ok: false, reason: "节点名称不能超过 32 个字符" });
    });

    it("超长中文名称", () => {
      const name = "推".repeat(33);
      const result = validateNodeName(name);
      expect(result).toEqual({ ok: false, reason: "节点名称不能超过 32 个字符" });
    });
  });

  // TC-VN-04 字符集校验
  describe("字符集校验", () => {
    it("含控制字符 NUL (\\u0000)", () => {
      const result = validateNodeName("节点\u0000");
      expect(result).toEqual({ ok: false, reason: "节点名称包含不支持的字符" });
    });

    it("含零宽空格 (\\u200B)", () => {
      const result = validateNodeName("节点\u200B");
      expect(result).toEqual({ ok: false, reason: "节点名称包含不支持的字符" });
    });

    it("含 BOM (\\uFEFF)", () => {
      const result = validateNodeName("\uFEFF节点");
      expect(result).toEqual({ ok: false, reason: "节点名称包含不支持的字符" });
    });
  });

  // TC-VN-05 短路顺序
  describe("短路顺序", () => {
    it("既空又超长 → 优先返回非空原因", () => {
      const name = "   ".repeat(33);
      const result = validateNodeName(name);
      expect(result).toEqual({ ok: false, reason: "节点名称不能为空" });
    });

    it("超长且含非法字符 → 优先返回长度原因", () => {
      const name = "a".repeat(33) + "\u0000";
      const result = validateNodeName(name);
      expect(result).toEqual({ ok: false, reason: "节点名称不能超过 32 个字符" });
    });
  });
});