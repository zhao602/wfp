// =============================================================================
// 画布视口持久化与决策纯函数测试（canvas/__tests__/canvasViewport.test.ts）
//
// 覆盖点：
//   C-01 内容签名：与顺序无关、随节点集合变化、对分隔敏感
//   C-02 视口可用性判定：有限数 + zoom 区间
//   C-03 持久化往返：写入 → 读取一致（含容器尺寸）
//   C-04 异常降级：损坏 JSON / 缺签名 / zoom 越界 → 读取返回 null
//   C-05 决策表：空画布 / 整批载入 / 无记录 / 签名不符 / 窗口尺寸漂移 / 完全匹配
// =============================================================================

import { describe, it, expect, beforeEach } from "vitest";
import {
  VIEWPORT_STORAGE_KEY,
  computeWorkflowSignature,
  isUsableViewport,
  clearStoredViewport,
  readStoredViewport,
  resolveInitialViewportPlan,
  writeStoredViewport,
} from "../canvasViewport";

beforeEach(() => {
  localStorage.clear();
});

describe("computeWorkflowSignature", () => {
  it("C-01a 节点顺序不同但集合相同：签名一致", () => {
    const a = computeWorkflowSignature([{ id: "n-1" }, { id: "n-2" }, { id: "n-3" }]);
    const b = computeWorkflowSignature([{ id: "n-3" }, { id: "n-1" }, { id: "n-2" }]);
    expect(a).toBe(b);
  });

  it("C-01b 节点集合变化：签名变化", () => {
    const base = computeWorkflowSignature([{ id: "n-1" }, { id: "n-2" }]);
    const added = computeWorkflowSignature([{ id: "n-1" }, { id: "n-2" }, { id: "n-3" }]);
    expect(base).not.toBe(added);
  });

  it("C-01c 分隔敏感：['ab','c'] 与 ['a','bc'] 签名不同", () => {
    expect(computeWorkflowSignature([{ id: "ab" }, { id: "c" }])).not.toBe(
      computeWorkflowSignature([{ id: "a" }, { id: "bc" }]),
    );
  });

  it("C-01d 空集合：稳定且带 0 计数前缀", () => {
    expect(computeWorkflowSignature([])).toMatch(/^wfp-v1:0:/);
  });
});

describe("isUsableViewport", () => {
  it("C-02a 合法视口：通过", () => {
    expect(isUsableViewport({ x: 12, y: -8, zoom: 1 })).toBe(true);
  });

  it("C-02b 非有限数 / 缺字段：拒绝", () => {
    expect(isUsableViewport({ x: Number.NaN, y: 0, zoom: 1 })).toBe(false);
    expect(isUsableViewport({ x: 0, y: 0 })).toBe(false);
    expect(isUsableViewport(null)).toBe(false);
    expect(isUsableViewport("viewport")).toBe(false);
  });

  it("C-02c zoom 越界：拒绝", () => {
    expect(isUsableViewport({ x: 0, y: 0, zoom: 0 })).toBe(false);
    expect(isUsableViewport({ x: 0, y: 0, zoom: 9 })).toBe(false);
  });
});

describe("视口持久化读写", () => {
  it("C-03 写入后可原样读回（含容器尺寸与签名）", () => {
    writeStoredViewport({ x: 40, y: -20, zoom: 0.8 }, "sig-a", { width: 900, height: 600 });
    expect(readStoredViewport()).toEqual({
      x: 40,
      y: -20,
      zoom: 0.8,
      signature: "sig-a",
      size: { width: 900, height: 600 },
    });
  });

  it("C-03b 不可用视口不写入", () => {
    writeStoredViewport({ x: 0, y: 0, zoom: 99 }, "sig-a", null);
    expect(localStorage.getItem(VIEWPORT_STORAGE_KEY)).toBeNull();
  });

  it("C-04a 内容损坏：返回 null 且不抛", () => {
    localStorage.setItem(VIEWPORT_STORAGE_KEY, "{not-json");
    expect(readStoredViewport()).toBeNull();
  });

  it("C-04b 缺签名或 zoom 越界：返回 null", () => {
    localStorage.setItem(
      VIEWPORT_STORAGE_KEY,
      JSON.stringify({ x: 0, y: 0, zoom: 1, size: null }),
    );
    expect(readStoredViewport()).toBeNull();

    localStorage.setItem(
      VIEWPORT_STORAGE_KEY,
      JSON.stringify({ x: 0, y: 0, zoom: 0, signature: "sig-a", size: null }),
    );
    expect(readStoredViewport()).toBeNull();
  });

  it("C-04c clear 后无记录", () => {
    writeStoredViewport({ x: 1, y: 2, zoom: 1 }, "sig-a", null);
    clearStoredViewport();
    expect(readStoredViewport()).toBeNull();
  });
});

describe("resolveInitialViewportPlan 决策表", () => {
  const stored = {
    x: 10,
    y: 20,
    zoom: 1.1,
    signature: "sig-match",
    size: { width: 800, height: 600 },
  };

  it("C-05a 空画布：不接管视口", () => {
    expect(
      resolveInitialViewportPlan({
        nodeCount: 0,
        generationChanged: false,
        storedViewport: stored,
        signature: "sig-match",
        paneSize: { width: 800, height: 600 },
      }),
    ).toEqual({ kind: "none" });
  });

  it("C-05b 整批外部载入：强制 fit（不恢复旧视口）", () => {
    expect(
      resolveInitialViewportPlan({
        nodeCount: 3,
        generationChanged: true,
        storedViewport: stored,
        signature: "sig-match",
        paneSize: { width: 800, height: 600 },
      }),
    ).toEqual({ kind: "fit" });
  });

  it("C-05c 无记录 / 签名不符：fit", () => {
    expect(
      resolveInitialViewportPlan({
        nodeCount: 3,
        generationChanged: false,
        storedViewport: null,
        signature: "sig-x",
        paneSize: null,
      }),
    ).toEqual({ kind: "fit" });

    expect(
      resolveInitialViewportPlan({
        nodeCount: 3,
        generationChanged: false,
        storedViewport: stored,
        signature: "sig-other",
        paneSize: { width: 800, height: 600 },
      }),
    ).toEqual({ kind: "fit" });
  });

  it("C-05d 容器尺寸漂移超阈值（换窗口大小）：fit，避免偏心", () => {
    expect(
      resolveInitialViewportPlan({
        nodeCount: 3,
        generationChanged: false,
        storedViewport: stored,
        signature: "sig-match",
        paneSize: { width: 1400, height: 900 },
      }),
    ).toEqual({ kind: "fit" });
  });

  it("C-05e 完全匹配：恢复记录中的缩放与平移", () => {
    expect(
      resolveInitialViewportPlan({
        nodeCount: 3,
        generationChanged: false,
        storedViewport: stored,
        signature: "sig-match",
        paneSize: { width: 820, height: 610 },
      }),
    ).toEqual({ kind: "restore", viewport: { x: 10, y: 20, zoom: 1.1 } });
  });

  it("C-05f 容器尺寸未知（首帧测不到）：不因尺寸比较降级，仍恢复", () => {
    expect(
      resolveInitialViewportPlan({
        nodeCount: 3,
        generationChanged: false,
        storedViewport: stored,
        signature: "sig-match",
        paneSize: null,
      }),
    ).toEqual({ kind: "restore", viewport: { x: 10, y: 20, zoom: 1.1 } });
  });
});
