import { describe, it, expect } from "vitest";
import { classifyDiagnosis } from "../ConnectionTestPanel";

describe("classifyDiagnosis", () => {
  it("参数错误前缀 → param", () => {
    expect(classifyDiagnosis("参数错误：Spark Master URL 未配置")).toBe("param");
    expect(classifyDiagnosis("参数错误：")).toBe("param");
  });

  it("连接失败前缀 → connection", () => {
    expect(classifyDiagnosis("连接失败：连接被拒绝")).toBe("connection");
    expect(classifyDiagnosis("连接失败：")).toBe("connection");
  });

  it("无法探测前缀 → unavailable", () => {
    expect(classifyDiagnosis("无法探测：驱动/网络不可用（asyncpg）")).toBe("unavailable");
    expect(classifyDiagnosis("无法探测：")).toBe("unavailable");
  });

  it("空诊断 → none", () => {
    expect(classifyDiagnosis(undefined)).toBe("none");
    expect(classifyDiagnosis(null)).toBe("none");
    expect(classifyDiagnosis("")).toBe("none");
  });

  it("无匹配前缀 → none", () => {
    expect(classifyDiagnosis("连接失败，请检查连接参数")).toBe("none");
    expect(classifyDiagnosis("some random text")).toBe("none");
  });
});