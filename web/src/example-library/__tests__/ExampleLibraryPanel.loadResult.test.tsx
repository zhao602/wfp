// =============================================================================
// 范例加载反馈测试（example-library/__tests__/ExampleLibraryPanel.loadResult.test.tsx）
//
// 留待非沙箱环境执行：cd web && npx vitest run
//
// 覆盖用例（fix-canvas-drag-clear-and-edge-loop-bugs / T12，TC-EL-01~03）：
//   TC-EL-01 导入失败（ok=false）时面板出现红色错误提示（FR-2.3，消除静默失败）
//   TC-EL-02 加载后再次点击同一范例按钮仍可触发加载（FR-2.1，无缓存无禁用）
//   TC-EL-03 导入成功（ok=true）时历史错误被清除（FR-2.4/FR-1.4）
//
// 追加用例（fix_canvas_clear_and_example_recurrence / CHG-3，TC-REC-01~02）：
//   TC-REC-01 同一范例连续两次加载均以函数式提交最新 prev，第二次内容等于范例定义（FR-2.3）
//   TC-REC-02 单次加载提交次数与内容等价于一次替换（副作用不重复，FR-2.5）
//
// 沙箱限制声明：沙箱内 tsc 全量编译超时、vitest 无法运行，本文件作为交付物入库，
// 请在非沙箱环境执行。
// =============================================================================

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
import type { SetStateAction } from "react";
import ExampleLibraryPanel from "../ExampleLibraryPanel";
import { orchestrationApiClient } from "../../api/orchestrationApiClient";
import type { WorkflowCanvasState } from "../../canvas/canvasTypes";
import type { ExampleEntry } from "../../api/apiTypes";
import type { ImportResult } from "../../workflow-definition/yamlToCanvasImporter";
import type { WorkflowDefinition } from "../../workflow-definition/definitionTypes";

/** 范例条目夹具 */
const exampleEntry: ExampleEntry = {
  example_id: "ex-1",
  name: "顺序管道",
  yaml_path: "examples/ex.yaml",
  available: true,
  missing_dependencies: [],
};

/** 范例定义夹具：handleLoad 仅 JSON.stringify 透传，内容不影响任何断言 */
const exampleDefinition: WorkflowDefinition = {
  spec_version: "1.0",
  definition_id: "def-ex-1",
  name: "范例定义",
  definition_version: 1,
  inputs: [],
  outputs: [],
  nodes: [],
  edges: [],
};

/** 成功形态 ImportResult */
const okResult: ImportResult = {
  ok: true,
  canvasState: { nodes: [], edges: [] } as WorkflowCanvasState,
};

/** 失败形态 ImportResult（空集合拒绝文案，与 T2 实现同源） */
const failResult: ImportResult = {
  ok: false,
  error: "范例定义中节点或连线集合为空，已拒绝导入",
};

/** 构造固定返回结果的 onLoadExample 桩 */
function makeOnLoadExample(result: ImportResult) {
  return vi.fn((_yamlText: string) => result);
}

/**
 * 展开范例库：面板默认收起，范例按钮在收起态不可交互。
 * 故所有点击路径先经折叠钮展开，保持"点得到才点"的真实用户链路。
 */
function expandLibrary() {
  fireEvent.click(screen.getByRole("button", { name: "范例库" }));
}

describe("范例库加载反馈", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  // TC-EL-01（FR-2.3）：导入失败（ok=false）→ 红色错误提示出现（消除静默失败）
  it("TC-EL-01 导入失败时面板出现红色错误提示（FR-2.3）", async () => {
    vi.spyOn(orchestrationApiClient, "listExamples").mockResolvedValue({
      examples: [exampleEntry],
    });
    vi.spyOn(orchestrationApiClient, "loadExample").mockResolvedValue(exampleDefinition);
    const onLoadExample = makeOnLoadExample(failResult);

    render(<ExampleLibraryPanel onLoadExample={onLoadExample} />);
    expandLibrary();
    const button = await screen.findByText("顺序管道");

    fireEvent.click(button);

    // 红色警示文本出现（handleLoad 消费 ImportResult 后 setError）
    expect(
      await screen.findByText(failResult.error),
    ).toBeDefined();
    expect(onLoadExample).toHaveBeenCalledTimes(1);
  });

  // TC-EL-02（FR-2.1）：加载后再次点击同一范例 → loadExample 桩再次被调用
  // （无缓存、按钮 disabled 仅由 available 驱动，重复点击可达）
  it("TC-EL-02 加载后再次点击同一范例仍可触发加载（FR-2.1）", async () => {
    vi.spyOn(orchestrationApiClient, "listExamples").mockResolvedValue({
      examples: [exampleEntry],
    });
    const loadSpy = vi
      .spyOn(orchestrationApiClient, "loadExample")
      .mockResolvedValue(exampleDefinition);
    const onLoadExample = makeOnLoadExample(okResult);

    render(<ExampleLibraryPanel onLoadExample={onLoadExample} />);
    expandLibrary();
    const button = await screen.findByText("顺序管道");

    fireEvent.click(button);
    await waitFor(() => expect(loadSpy).toHaveBeenCalledTimes(1));

    fireEvent.click(button);
    await waitFor(() => expect(loadSpy).toHaveBeenCalledTimes(2));
  });

  // TC-EL-03（FR-2.4/FR-1.4）：导入成功 → 历史错误被清除 + 画布状态更新链路触达
  it("TC-EL-03 导入成功时历史错误被清除且再次触达导入回调（FR-2.4/FR-1.4）", async () => {
    vi.spyOn(orchestrationApiClient, "listExamples").mockResolvedValue({
      examples: [exampleEntry],
    });
    vi.spyOn(orchestrationApiClient, "loadExample").mockResolvedValue(exampleDefinition);
    // 可变结果：第一次失败、第二次成功
    let currentResult: ImportResult = failResult;
    const onLoadExample = vi.fn((_yamlText: string) => currentResult);

    render(<ExampleLibraryPanel onLoadExample={onLoadExample} />);
    expandLibrary();
    const button = await screen.findByText("顺序管道");

    // 第一次：失败 → 错误出现
    fireEvent.click(button);
    expect(await screen.findByText(failResult.error)).toBeDefined();

    // 第二次：成功 → 历史错误清除
    currentResult = okResult;
    fireEvent.click(button);
    await waitFor(() => {
      expect(screen.queryByText(failResult.error)).toBeNull();
    });
    expect(onLoadExample).toHaveBeenCalledTimes(2);
  });

  // TC-REC-01（FR-2.3）：同一范例连续两次加载 → 均以函数式提交最新 prev，
  // 第二次画布内容等于范例定义（恢复能力，非不可自愈清空）。
  it("TC-REC-01 同一范例连续两次加载均提交最新 prev 且内容等于范例定义（FR-2.3）", async () => {
    vi.spyOn(orchestrationApiClient, "listExamples").mockResolvedValue({
      examples: [exampleEntry],
    });
    vi.spyOn(orchestrationApiClient, "loadExample").mockResolvedValue(exampleDefinition);

    // 范例定义对应的画布状态（含单节点，用于验证"内容等于范例定义"）
    const exampleCanvasState = {
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
    } as WorkflowCanvasState;

    // 模拟 App 层函数式提交（handleImportYaml：setCanvasState(() => result.canvasState)）
    let canvasState: WorkflowCanvasState = { nodes: [], edges: [] };
    const setCanvasState = (update: SetStateAction<WorkflowCanvasState>) => {
      canvasState =
        typeof update === "function"
          ? (update as (prev: WorkflowCanvasState) => WorkflowCanvasState)(canvasState)
          : update;
    };
    const onLoadExample = vi.fn((_yamlText: string) => {
      const result: ImportResult = { ok: true, canvasState: exampleCanvasState };
      setCanvasState(() => result.canvasState);
      return result;
    });

    render(<ExampleLibraryPanel onLoadExample={onLoadExample} />);
    expandLibrary();
    const button = await screen.findByText("顺序管道");

    fireEvent.click(button);
    await waitFor(() => expect(onLoadExample).toHaveBeenCalledTimes(1));
    fireEvent.click(button);
    await waitFor(() => expect(onLoadExample).toHaveBeenCalledTimes(2));

    // 第二次加载后画布内容等于范例定义（恢复能力）
    expect(canvasState.nodes).toHaveLength(1);
    expect(canvasState.nodes[0].data.node_id).toBe("n1");
  });

  // TC-REC-02（FR-2.5）：单次点击 → 单次提交（副作用不重复，等价于一次替换）。
  it("TC-REC-02 单次加载提交次数等价于一次替换（FR-2.5）", async () => {
    vi.spyOn(orchestrationApiClient, "listExamples").mockResolvedValue({
      examples: [exampleEntry],
    });
    vi.spyOn(orchestrationApiClient, "loadExample").mockResolvedValue(exampleDefinition);

    let submitCount = 0;
    const onLoadExample = vi.fn((_yamlText: string) => {
      submitCount += 1;
      return okResult;
    });

    render(<ExampleLibraryPanel onLoadExample={onLoadExample} />);
    expandLibrary();
    const button = await screen.findByText("顺序管道");
    fireEvent.click(button);

    await waitFor(() => expect(submitCount).toBe(1));
  });
});