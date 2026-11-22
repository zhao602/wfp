// =============================================================================
// 执行失败信息格式化纯函数单元测试（execution-visualization/__tests__/failureFeedback.test.ts）
//
// 留待非沙箱环境执行：vitest run src/execution-visualization/__tests__/failureFeedback.test.ts
//
// 覆盖（CHG-5 / INV-1 / INV-2 / AC-4 / AC-8 / DC-11 / DC-12）：
//   - extractError 类型守卫：string 原样返回，number/null/undefined/缺失键归空串
//   - formatNodeFailure 正常路径 / 空原因兜底（返回非空、始终含节点标识）
//   - formatInstanceFailure 正常路径 / 空原因兜底（返回非空）
//   - mergeNodeFailure 去重：同 nodeId 幂等保留最早、多节点全保留且顺序稳定
// =============================================================================

import { describe, it, expect } from "vitest";
import {
  extractError,
  formatNodeFailure,
  formatInstanceFailure,
  mergeNodeFailure,
} from "../failureFeedback";
import type { NodeFailureEntry } from "../failureFeedback";

describe("extractError — payload.error 类型守卫", () => {
  it("error 为字符串时原样返回", () => {
    expect(extractError({ error: "超时" })).toBe("超时");
  });

  it("error 为 number 时归为空串", () => {
    expect(extractError({ error: 500 })).toBe("");
  });

  it("error 为 null 时归为空串", () => {
    expect(extractError({ error: null })).toBe("");
  });

  it("error 为 undefined 时归为空串", () => {
    expect(extractError({ error: undefined })).toBe("");
  });

  it("缺失 error 键时归为空串", () => {
    expect(extractError({})).toBe("");
  });
});

describe("formatNodeFailure — 节点级失败文本", () => {
  it("正常原因：包含节点标识与原因", () => {
    expect(formatNodeFailure("node_1", "超时")).toBe("节点 node_1 执行失败：超时");
  });

  it("优先展示节点名称", () => {
    expect(formatNodeFailure("node_1", "超时", "数据抓取")).toBe(
      "节点 数据抓取 执行失败：超时",
    );
  });

  it("空原因：兜底占位且非空、含节点标识", () => {
    const text = formatNodeFailure("node_1", "");
    expect(text).toBe("节点 node_1 执行失败（未提供详细原因）");
    expect(text.length).toBeGreaterThan(0);
    expect(text).toContain("node_1");
  });

  it("纯空白原因：同样走兜底占位", () => {
    expect(formatNodeFailure("node_1", "   ")).toBe(
      "节点 node_1 执行失败（未提供详细原因）",
    );
  });

  it("空白展示名回落 nodeId", () => {
    expect(formatNodeFailure("node_1", "超时", "   ")).toBe("节点 node_1 执行失败：超时");
  });
});

describe("formatInstanceFailure — 实例级失败文本", () => {
  it("正常原因：包含前缀与原因", () => {
    expect(formatInstanceFailure("编译失败")).toBe("工作流执行失败：编译失败");
  });

  it("空原因：兜底占位且非空", () => {
    const text = formatInstanceFailure("");
    expect(text).toBe("工作流执行失败（未提供详细原因）");
    expect(text.length).toBeGreaterThan(0);
  });

  it("纯空白原因：同样走兜底占位", () => {
    expect(formatInstanceFailure("  ")).toBe("工作流执行失败（未提供详细原因）");
  });
});

describe("mergeNodeFailure — 按 nodeId 去重、保留最早", () => {
  const first: NodeFailureEntry = { nodeId: "n1", reason: "节点 n1 执行失败：A" };

  it("首个条目正常追加", () => {
    const result = mergeNodeFailure([], first);
    expect(result).toHaveLength(1);
    expect(result[0]).toBe(first);
  });

  it("同 nodeId 重复：保留最早、幂等", () => {
    const later: NodeFailureEntry = { nodeId: "n1", reason: "节点 n1 执行失败：B" };
    const result = mergeNodeFailure([first], later);
    expect(result).toHaveLength(1);
    expect(result[0]).toBe(first);
  });

  it("多节点失败：全部保留且顺序稳定", () => {
    const second: NodeFailureEntry = { nodeId: "n2", reason: "节点 n2 执行失败：B" };
    const result = mergeNodeFailure(mergeNodeFailure([], first), second);
    expect(result.map((entry) => entry.nodeId)).toEqual(["n1", "n2"]);
  });
});