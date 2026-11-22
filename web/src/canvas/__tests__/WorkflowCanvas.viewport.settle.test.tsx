// =============================================================================
// 画布视口就绪门控与尺寸校正测试（canvas/__tests__/WorkflowCanvas.viewport.settle.test.tsx）
//
// 背景：刷新后首帧布局未稳定 —— 容器尺寸可能仍是 0×0 或偏小，此时 fitView 会按错误
// 容器尺寸计算平移，工作流被画在视口偏上位置。本组用例锁定修复行为：
//   S-01 容器尺寸未就绪（0×0）：不消费适配；尺寸就绪后恰适配 1 次
//   S-02 适配后容器尺寸才稳定（置信窗口内变化）：重新适配，结果归正
//   S-03 用户已接管视口（pointerdown）后容器尺寸变化：不再适配
//   S-04 fitView 返回 false（库未就绪）：不消费，待下次就绪信号重试成功
//
// 手段：桩替换 ReactFlow + 伪 ResizeObserver + 受控 getBoundingClientRect。
// =============================================================================

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, fireEvent } from "@testing-library/react";
import type { NodeChange, ReactFlowInstance } from "reactflow";
import type { SetStateAction } from "react";
import WorkflowCanvas from "../WorkflowCanvas";
import type { WorkflowCanvasState, CanvasNode } from "../canvasTypes";
import type { NodeConfiguration } from "../../workflow-definition/definitionTypes";

interface CapturedReactFlowProps {
  onNodesChange: (changes: NodeChange[]) => void;
  onInit: (instance: ReactFlowInstance) => void;
}

const rfCapture = vi.hoisted(() => ({
  props: null as CapturedReactFlowProps | null,
}));

const fitViewSpy = vi.fn();

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
  trigger_type: "manual",
  input_template: {},
};

function makeNode(id: string): CanvasNode {
  return {
    id,
    type: "trigger",
    position: { x: 0, y: 0 },
    data: { node_id: id, name: id, node_type: "trigger", configuration: configA },
  };
}

function dimensionsChange(id: string): NodeChange {
  return { type: "dimensions", id, dimensions: { width: 100, height: 40 } };
}

/** 当前容器尺寸（由用例控制） */
let paneSize = { width: 0, height: 0 };
/** 捕获的 ResizeObserver 回调 */
let resizeCallbacks: ResizeObserverCallback[] = [];

class FakeResizeObserver {
  constructor(callback: ResizeObserverCallback) {
    resizeCallbacks.push(callback);
  }
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

/** 触发一次尺寸观察回调（模拟容器尺寸变化） */
function triggerResize(): void {
  for (const callback of resizeCallbacks) {
    callback([], {} as ResizeObserver);
  }
}

function renderCanvas(state: WorkflowCanvasState) {
  const onStateChange = vi.fn<(update: SetStateAction<WorkflowCanvasState>) => void>();
  const result = render(
    <WorkflowCanvas
      canvasState={state}
      onCanvasStateChange={onStateChange}
      selectedNodeId={null}
      onSelectNode={vi.fn()}
    />,
  );
  const instance = { screenToFlowPosition: () => ({ x: 0, y: 0 }), fitView: fitViewSpy };
  getProps().onInit(instance as unknown as ReactFlowInstance);
  return result;
}

const twoNodes: WorkflowCanvasState = {
  nodes: [makeNode("n-a"), makeNode("n-b")],
  edges: [],
};

beforeEach(() => {
  rfCapture.props = null;
  resizeCallbacks = [];
  paneSize = { width: 0, height: 0 };
  fitViewSpy.mockClear();
  localStorage.clear();
  vi.stubGlobal("ResizeObserver", FakeResizeObserver);
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(
    () =>
      ({
        width: paneSize.width,
        height: paneSize.height,
        top: 0,
        left: 0,
        right: paneSize.width,
        bottom: paneSize.height,
        x: 0,
        y: 0,
        toJSON: () => ({}),
      }) as DOMRect,
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("WorkflowCanvas 视口就绪门控", () => {
  it("S-01 容器 0×0 时不适配；尺寸就绪后恰适配 1 次", () => {
    renderCanvas(twoNodes);

    // 节点尺寸已全部上报，但容器尚无尺寸：不得用 0×0 计算视口
    getProps().onNodesChange([dimensionsChange("n-a"), dimensionsChange("n-b")]);
    expect(fitViewSpy).not.toHaveBeenCalled();

    // 首帧布局定型：容器拿到真实尺寸
    paneSize = { width: 1200, height: 800 };
    triggerResize();

    expect(fitViewSpy).toHaveBeenCalledTimes(1);
  });

  it("S-02 适配后容器尺寸才定型：置信窗口内重新适配，纠正偏移", () => {
    paneSize = { width: 1200, height: 260 }; // 偏小的"未定型"高度
    renderCanvas(twoNodes);
    getProps().onNodesChange([dimensionsChange("n-a"), dimensionsChange("n-b")]);
    expect(fitViewSpy).toHaveBeenCalledTimes(1);

    // 布局定型后高度变大：按新尺寸重算，工作流回到视觉中心
    paneSize = { width: 1200, height: 800 };
    triggerResize();
    expect(fitViewSpy).toHaveBeenCalledTimes(2);

    // 尺寸再次微调（同帧重排）：仍可校正
    paneSize = { width: 1200, height: 820 };
    triggerResize();
    expect(fitViewSpy).toHaveBeenCalledTimes(3);
  });

  it("S-03 用户接管视口后容器尺寸变化：不再覆盖其视野", () => {
    paneSize = { width: 1200, height: 800 };
    const { container } = renderCanvas(twoNodes);
    getProps().onNodesChange([dimensionsChange("n-a"), dimensionsChange("n-b")]);
    expect(fitViewSpy).toHaveBeenCalledTimes(1);

    // 用户按下鼠标（开始平移/拖拽）：关闭置信窗口
    fireEvent.pointerDown(container.firstChild as Element);

    paneSize = { width: 900, height: 600 };
    triggerResize();
    expect(fitViewSpy).toHaveBeenCalledTimes(1);
  });

  it("S-04 fitView 返回 false（库未就绪）：不消费，就绪后重试成功", () => {
    paneSize = { width: 1200, height: 800 };
    fitViewSpy.mockReturnValueOnce(false);
    renderCanvas(twoNodes);

    getProps().onNodesChange([dimensionsChange("n-a"), dimensionsChange("n-b")]);
    expect(fitViewSpy).toHaveBeenCalledTimes(1); // 首次被库拒绝

    // 下一次就绪信号（容器尺寸回调）驱动重试：成功即消费
    triggerResize();
    expect(fitViewSpy).toHaveBeenCalledTimes(2);
  });
});
