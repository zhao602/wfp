// =============================================================================
// App 导入代际自增单元测试（src/__tests__/App.import.generation.test.tsx）
//
// 留待非沙箱环境执行：npx vitest run src/__tests__/App.import.generation.test.tsx
//
// 覆盖用例（对应 CHG-6 / FR-2.1 / DC-10）：
//   TC-IMPORT-01 导入成功 → canvasGeneration 自增；导入失败 → 代际不变
//
// 测试策略：桩 WorkflowCanvas 捕获 canvasGeneration prop，桩 ExampleLibraryPanel 捕获
// onLoadExample 回调，桩 yamlToCanvas 控制返回值，通过 act 驱动同步状态更新。
//
// 沙箱限制声明：沙箱内 tsc 全量编译超时、vitest 无法运行，本文件作为交付物入库（NFR-009）。
// =============================================================================

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, cleanup, act } from "@testing-library/react";
import App from "../App";
import type { ImportResult } from "../workflow-definition/yamlToCanvasImporter";
import { orchestrationApiClient } from "../api/orchestrationApiClient";

// —— 桩捕获容器 ——
const wcCapture = vi.hoisted(() => ({ canvasGeneration: 0 }));
const elpCapture = vi.hoisted(() => ({
  onLoadExample: null as ((yamlText: string) => ImportResult) | null,
}));

// 桩 WorkflowCanvas：仅捕获 canvasGeneration prop
vi.mock("../canvas/WorkflowCanvas", () => ({
  default: (props: { canvasGeneration?: number }) => {
    wcCapture.canvasGeneration = props.canvasGeneration ?? 0;
    return null;
  },
}));

// 桩 ExampleLibraryPanel：仅捕获 onLoadExample 回调
vi.mock("../example-library/ExampleLibraryPanel", () => ({
  default: (props: { onLoadExample: (yamlText: string) => ImportResult }) => {
    elpCapture.onLoadExample = props.onLoadExample;
    return null;
  },
}));

// 桩 yamlToCanvas：根据输入返回成功/失败
vi.mock("../workflow-definition/yamlToCanvasImporter", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("../workflow-definition/yamlToCanvasImporter")>();
  return {
    ...actual,
    yamlToCanvas: vi.fn((yamlText: string): ImportResult => {
      if (yamlText === "valid") {
        return {
          ok: true,
          canvasState: {
            nodes: [
              {
                id: "n1",
                type: "trigger",
                position: { x: 0, y: 0 },
                data: {
                  node_id: "n1",
                  name: "触发器",
                  node_type: "trigger",
                  configuration: {
                    node_type: "trigger",
                    trigger_type: "manual",
                    input_template: {},
                  },
                },
              },
            ],
            edges: [],
          },
        };
      }
      return { ok: false, error: "测试模拟导入失败" };
    }),
  };
});

// jsdom 无 ResizeObserver
vi.stubGlobal(
  "ResizeObserver",
  class {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  },
);

// 后端返回 200 空对象
vi.stubGlobal(
  "fetch",
  vi.fn().mockResolvedValue(new Response(JSON.stringify({}), { status: 200 })),
);

describe("App 导入代际自增", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("TC-IMPORT-01 导入成功 → canvasGeneration 自增；导入失败 → 代际不变", () => {
    vi.spyOn(orchestrationApiClient, "listExamples").mockResolvedValue({
      examples: [],
    });

    render(<App />);

    // 初始代际为 0
    expect(wcCapture.canvasGeneration).toBe(0);

    // 导入成功 → 代际自增
    act(() => {
      elpCapture.onLoadExample!("valid");
    });
    expect(wcCapture.canvasGeneration).toBe(1);

    // 导入失败 → 代际不变
    act(() => {
      elpCapture.onLoadExample!("invalid");
    });
    expect(wcCapture.canvasGeneration).toBe(1);

    // 再次导入成功 → 代际再自增
    act(() => {
      elpCapture.onLoadExample!("valid");
    });
    expect(wcCapture.canvasGeneration).toBe(2);
  });
});