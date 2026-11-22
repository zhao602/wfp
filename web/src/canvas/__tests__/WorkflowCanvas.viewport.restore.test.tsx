// =============================================================================
// 画布视口恢复与持久化单元测试（canvas/__tests__/WorkflowCanvas.viewport.restore.test.tsx）
//
// 覆盖用例：
//   R-01 刷新（挂载即非空）且记录签名匹配：setViewport 恰 1 次、fitView 不调用
//   R-02 记录签名不匹配（另一份工作流）：fitView 恰 1 次、setViewport 不调用
//   R-03 整批外部载入（代际切换）：丢弃旧记录并强制 fitView
//   R-04 手势结束（onMoveEnd）：把当前视口按最新签名写回 localStorage
//   R-05 恢复后再拖入新节点：不重复接管视口
//
// 测试策略同 WorkflowCanvas.viewport.test.tsx：桩替换 ReactFlow 默认导出，
// 捕获回调 props，注入含 fitView/setViewport/getViewport 的实例桩。
// =============================================================================

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render } from "@testing-library/react";
import type { NodeChange, ReactFlowInstance, Viewport } from "reactflow";
import type { DragEvent as ReactDragEvent, SetStateAction } from "react";
import WorkflowCanvas from "../WorkflowCanvas";
import type { WorkflowCanvasState, CanvasNode } from "../canvasTypes";
import type { NodeConfiguration } from "../../workflow-definition/definitionTypes";
import {
  computeWorkflowSignature,
  readStoredViewport,
  writeStoredViewport,
} from "../canvasViewport";

interface CapturedReactFlowProps {
  onNodesChange: (changes: NodeChange[]) => void;
  onDrop: (event: ReactDragEvent) => void;
  onInit: (instance: ReactFlowInstance) => void;
  onMoveEnd: (event: MouseEvent, viewport: Viewport) => void;
}

interface InstanceStub {
  screenToFlowPosition: (position: { x: number; y: number }) => { x: number; y: number };
  fitView: ReturnType<typeof vi.fn>;
  setViewport: ReturnType<typeof vi.fn>;
  getViewport: ReturnType<typeof vi.fn>;
}

const rfCapture = vi.hoisted(() => ({
  props: null as CapturedReactFlowProps | null,
}));

const fitViewSpy = vi.fn();
const setViewportSpy = vi.fn();
const currentViewport: Viewport = { x: 0, y: 0, zoom: 1 };
const getViewportSpy = vi.fn(() => currentViewport);

vi.mock("reactflow", async (importOriginal) => {
  const actual = await importOriginal<typeof import("reactflow")>();
  return {
    ...actual,
    default: (props: CapturedReactFlowProps) => {
      rfCapture.props = props;
      return null;
    },
  };
});

function getProps(): CapturedReactFlowProps {
  if (rfCapture.props === null) {
    throw new Error("尚未捕获 ReactFlow props：WorkflowCanvas 未渲染");
  }
  return rfCapture.props;
}

const configA: NodeConfiguration = {
  node_type: "trigger",
  trigger_type: "scheduled",
  schedule: "0 9 * * *",
  input_template: {},
};

function makeNode(id: string, name: string): CanvasNode {
  return {
    id,
    type: "trigger",
    position: { x: 0, y: 0 },
    data: { node_id: id, name, node_type: "trigger", configuration: configA },
  };
}

function dimensionsChange(id: string): NodeChange {
  return { type: "dimensions", id, dimensions: { width: 100, height: 40 } };
}

function applyCapturedUpdate(
  calls: unknown[][],
  base: WorkflowCanvasState,
): WorkflowCanvasState {
  let state = base;
  for (const call of calls) {
    const update: unknown = call[0];
    state =
      typeof update === "function"
        ? (update as (prev: WorkflowCanvasState) => WorkflowCanvasState)(state)
        : (update as WorkflowCanvasState);
  }
  return state;
}

function makeDropEvent(type: string): ReactDragEvent {
  return {
    preventDefault: vi.fn(),
    dataTransfer: { getData: (key: string) => (key === "application/node-type" ? type : "") },
    clientX: 100,
    clientY: 200,
  } as unknown as ReactDragEvent;
}

function renderCanvas(initial: WorkflowCanvasState, generation = 0) {
  const onStateChange = vi.fn<(update: SetStateAction<WorkflowCanvasState>) => void>();
  const onSelectNode = vi.fn<(nodeId: string | null) => void>();
  let currentGen = generation;
  const buildElement = (state: WorkflowCanvasState, gen: number) => (
    <WorkflowCanvas
      canvasState={state}
      onCanvasStateChange={onStateChange}
      selectedNodeId={null}
      onSelectNode={onSelectNode}
      canvasGeneration={gen}
    />
  );
  const result = render(buildElement(initial, generation));
  return {
    onStateChange,
    rerenderWith: (state: WorkflowCanvasState, gen?: number) => {
      if (gen !== undefined) currentGen = gen;
      result.rerender(buildElement(state, currentGen));
    },
  };
}

function primeInstance(): void {
  const stub: InstanceStub = {
    screenToFlowPosition: () => ({ x: 100, y: 200 }),
    fitView: fitViewSpy,
    setViewport: setViewportSpy,
    getViewport: getViewportSpy,
  };
  getProps().onInit(stub as unknown as ReactFlowInstance);
}

const restoredNodes = [makeNode("n-a", "触发器A"), makeNode("n-b", "触发器B")];
const restoredSignature = computeWorkflowSignature(restoredNodes);

beforeEach(() => {
  rfCapture.props = null;
  fitViewSpy.mockClear();
  setViewportSpy.mockClear();
  localStorage.clear();
});

describe("WorkflowCanvas 视口恢复", () => {
  it("R-01 刷新且记录签名匹配：恢复上次视口，不触发 fitView", () => {
    writeStoredViewport({ x: 120, y: 64, zoom: 0.75 }, restoredSignature, null);

    renderCanvas({ nodes: restoredNodes, edges: [] });
    primeInstance();
    getProps().onNodesChange([dimensionsChange("n-a"), dimensionsChange("n-b")]);

    expect(setViewportSpy).toHaveBeenCalledTimes(1);
    expect(setViewportSpy).toHaveBeenCalledWith({ x: 120, y: 64, zoom: 0.75 });
    expect(fitViewSpy).not.toHaveBeenCalled();
  });

  it("R-02 记录签名不匹配：按 fit view 处理", () => {
    writeStoredViewport({ x: 120, y: 64, zoom: 0.75 }, "wfp-v1:9:otherapp", null);

    renderCanvas({ nodes: restoredNodes, edges: [] });
    primeInstance();
    getProps().onNodesChange([dimensionsChange("n-a"), dimensionsChange("n-b")]);

    expect(fitViewSpy).toHaveBeenCalledTimes(1);
    expect(setViewportSpy).not.toHaveBeenCalled();
  });

  it("R-03 整批外部载入（代际切换）：丢弃旧记录并强制 fit", () => {
    writeStoredViewport({ x: 5, y: 5, zoom: 1 }, restoredSignature, null);

    const { rerenderWith } = renderCanvas({ nodes: [], edges: [] }, 1);
    primeInstance();

    const loaded: WorkflowCanvasState = { nodes: restoredNodes, edges: [] };
    rerenderWith(loaded, 2);
    primeInstance();
    getProps().onNodesChange([dimensionsChange("n-a"), dimensionsChange("n-b")]);

    expect(fitViewSpy).toHaveBeenCalledTimes(1);
    expect(setViewportSpy).not.toHaveBeenCalled();
    // 旧记录已被丢弃：落盘的签名应为新内容（或记录被清除后由适配写回新签名）
    const stored = readStoredViewport();
    expect(stored === null || stored.signature === computeWorkflowSignature(loaded.nodes)).toBe(true);
  });

  it("R-04 onMoveEnd：按最新签名写回当前视口", () => {
    renderCanvas({ nodes: restoredNodes, edges: [] });
    primeInstance();
    getProps().onNodesChange([dimensionsChange("n-a"), dimensionsChange("n-b")]);

    getProps().onMoveEnd(new MouseEvent("mouseup"), { x: -40, y: 88, zoom: 1.25 });

    const stored = readStoredViewport();
    expect(stored).not.toBeNull();
    expect(stored?.x).toBe(-40);
    expect(stored?.y).toBe(88);
    expect(stored?.zoom).toBe(1.25);
    expect(stored?.signature).toBe(restoredSignature);
  });

  it("R-05 恢复后再拖入新节点：不重复接管视口", () => {
    writeStoredViewport({ x: 120, y: 64, zoom: 0.75 }, restoredSignature, null);

    const initial: WorkflowCanvasState = { nodes: restoredNodes, edges: [] };
    const { onStateChange, rerenderWith } = renderCanvas(initial);
    primeInstance();
    getProps().onNodesChange([dimensionsChange("n-a"), dimensionsChange("n-b")]);
    expect(setViewportSpy).toHaveBeenCalledTimes(1);

    getProps().onDrop(makeDropEvent("agent"));
    const next = applyCapturedUpdate(onStateChange.mock.calls, initial);
    expect(next.nodes).toHaveLength(3);
    rerenderWith(next);
    getProps().onNodesChange([dimensionsChange("new-node")]);

    expect(setViewportSpy).toHaveBeenCalledTimes(1);
    expect(fitViewSpy).not.toHaveBeenCalled();
  });
});
