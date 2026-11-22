// =============================================================================
// 连线环校验测试（src/__tests__/ConnectionValidator.test.ts）
//
// 覆盖验收：A10（构成环的连线被拒绝、合法连线被接受）
// 本次更新（fix-canvas-drag-clear-and-edge-loop-bugs / T10）：TC-CV-01~05 对齐
// "局部可达性检测"新语义——仅拒绝 target 沿既有边可达 source 的新环引入；
// 既有回边（循环工作流结构）不误拒无关新连线（FR-3.1~FR-3.4）。
// ⚠️ 测试为交付物：留待非沙箱环境执行（cd web && npx vitest run）。
// =============================================================================

import { describe, it, expect } from "vitest";
import { validateConnection } from "../canvas/ConnectionValidator";
import type { CanvasEdge } from "../canvas/canvasTypes";

describe("ConnectionValidator", () => {
  // TC-CV-01（FR-3.1）：两个无关联新节点间连线 → 允许
  it("无环图中的连线应被接受", () => {
    const edges: CanvasEdge[] = [
      { id: "e1", source: "a", target: "b" },
      { id: "e2", source: "b", target: "c" },
    ];

    const result = validateConnection(edges, "c", "d");
    expect(result.ok).toBe(true);
  });

  // TC-CV-02（FR-3.2）：A→B 存在时尝试 B→A → 拒绝
  it("构成简单环的连线应被拒绝", () => {
    const edges: CanvasEdge[] = [
      { id: "e1", source: "a", target: "b" },
      { id: "e2", source: "b", target: "c" },
    ];

    // c -> a 构成 a -> b -> c -> a 环
    const result = validateConnection(edges, "c", "a");
    expect(result.ok).toBe(false);
    expect(result.reason).toContain("环");
  });

  // TC-CV-03（FR-3.3，Bug-3 修复核心回归）：既有边集合含独立环（X→Y→X）时，
  // 与该环无关的新节点间连线 → 允许（既有回边不误拒无关新连线）
  it("既有独立环不应误拒无关新连线", () => {
    const edges: CanvasEdge[] = [
      { id: "e1", source: "x", target: "y" },
      { id: "e2", source: "y", target: "x" }, // 独立环 X→Y→X（如循环工作流回边）
    ];

    const result = validateConnection(edges, "a", "b");
    expect(result.ok).toBe(true);
  });

  // TC-CV-04（FR-3.4）：自环（source === target）→ 拒绝，文案回归
  it("自环应被拒绝", () => {
    const edges: CanvasEdge[] = [];
    const result = validateConnection(edges, "a", "a");
    expect(result.ok).toBe(false);
    expect(result.reason).toContain("自环");
  });

  // 补充回归：空图首条连线允许
  it("空图中的首条连线应被接受", () => {
    const edges: CanvasEdge[] = [];
    const result = validateConnection(edges, "a", "b");
    expect(result.ok).toBe(true);
  });

  // 补充回归：DAG 多条合法连线均允许
  it("DAG 中多条合法连线应均被接受", () => {
    const edges: CanvasEdge[] = [
      { id: "e1", source: "trigger", target: "agent" },
    ];

    expect(validateConnection(edges, "agent", "tool").ok).toBe(true);
    expect(validateConnection(edges, "agent", "data_source").ok).toBe(true);
  });

  // TC-CV-05（FR-3.2 补充）：候选边接入既有路径形成新环
  // （target 沿既有边可达 source）→ 拒绝
  it("复杂环应被检测", () => {
    const edges: CanvasEdge[] = [
      { id: "e1", source: "a", target: "b" },
      { id: "e2", source: "b", target: "c" },
      { id: "e3", source: "c", target: "d" },
      { id: "e4", source: "d", target: "b" }, // b -> c -> d -> b 构成环
    ];

    // 添加 b -> a 会构成更大的环（a 沿既有边 a→b 可达 b）
    const result = validateConnection(edges, "b", "a");
    expect(result.ok).toBe(false);
  });
});
