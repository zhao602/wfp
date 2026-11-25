// =============================================================================
// 画布受控聚焦单元测试（canvas/__tests__/WorkflowCanvas.focus.test.tsx）
//
// 运行：npx vitest run src/canvas/__tests__/WorkflowCanvas.focus.test.tsx
//
// 覆盖用例（B13 U3 / design D5.4）：
//   TC-FOCUS-01 nonce 初次到达 → setCenter(回退尺寸中心, {zoom:1, duration:300})
//   TC-FOCUS-02 同 nonce 重放不重复触发（dimensions 更新驱动 effect 重跑）
//   TC-FOCUS-03 nonce 递增新请求再次触发（目标切换）
//   TC-FOCUS-04 已测量尺寸优先：setCenter 用实测中心
//   TC-FOCUS-05 目标节点不存在 → 不触发且不消费（节点后补后兑现）
//   TC-FOCUS-06 实例未就绪时请求保留，实例就绪后下一次渲染兑现
//
// 测试策略：复用 dimensions 系列的 vi.mock("reactflow") 桩手法（default 导出替换为
// 捕获桩），经 onInit 注入带 setCenter spy 的实例桩，经 rerender 变化 focusNodeRequest。
// =============================================================================

import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, render } from "@testing-library/react";
import type { NodeChange, ReactFlowInstance } from "reactflow";
import WorkflowCanvas, { type FocusNodeRequest } from "../WorkflowCanvas";
import type { WorkflowCanvasState, CanvasNode } from "../canvasTypes";
import type { NodeConfiguration } from "../../workflow-definition/definitionTypes";

/** 捕获的回调 props 形状（聚焦驱动所需子集） */
interface CapturedReactFlowProps {
  onNodesChange: (changes: NodeChange[]) => void;
  onInit: (instance: ReactFlowInstance) => void;
}

/** 被捕获的 ReactFlow props */
const rfCapture = vi.hoisted(() => ({
  props: null as CapturedReactFlowProps | null,
}));

/** setCenter spy（聚焦断言核心） */
const setCenterSpy = vi.fn();
/** fitView spy（实例桩需要） */
const fitViewSpy = vi.fn();

// 以桩组件替换 ReactFlow 默认导出：捕获回调 props
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

/** 读取被捕获的 ReactFlow props */
function getProps(): CapturedReactFlowProps {
  if (rfCapture.props === null) {
    throw new Error("尚未捕获 ReactFlow props：WorkflowCanvas 未渲染");
  }
  return rfCapture.props;
}

// —— 夹具 ——
const configA: NodeConfiguration = {
  node_type: "trigger",
  trigger_type: "manual",
  input_template: {},
};

function makeNode(id: string, x: number, y: number): CanvasNode {
  return {
    id,
    type: configA.node_type,
    position: { x, y },
    data: {
      node_id: id,
      name: id,
      node_type: configA.node_type,
      configuration: configA,
    },
  };
}

const state: WorkflowCanvasState = {
  nodes: [makeNode("n1", 100, 50), makeNode("n2", 300, 200)],
  edges: [],
};

/** 渲染受控画布，支持 focusNodeRequest 注入与 rerender */
function renderCanvas() {
  const onCanvasStateChange = vi.fn();
  const onSelectNode = vi.fn();
  const buildElement = (focus: FocusNodeRequest | null) => (
    <WorkflowCanvas
      canvasState={state}
      onCanvasStateChange={onCanvasStateChange}
      selectedNodeId={null}
      onSelectNode={onSelectNode}
      canvasGeneration={0}
      focusNodeRequest={focus}
    />
  );
  const result = render(buildElement(null));
  const rerenderWith = (focus: FocusNodeRequest | null) => {
    result.rerender(buildElement(focus));
  };
  return { rerenderWith };
}

/** 注入带 setCenter spy 的实例桩 */
function primeInstance(): void {
  const stub = {
    screenToFlowPosition: () => ({ x: 0, y: 0 }),
    fitView: fitViewSpy,
    setCenter: setCenterSpy,
  };
  getProps().onInit(stub as unknown as ReactFlowInstance);
}

/** dimensions 变更（nodeDimensions 实测注入） */
function dimensionsChange(id: string, width: number, height: number): NodeChange {
  return { type: "dimensions", id, dimensions: { width, height } };
}

beforeEach(() => {
  rfCapture.props = null;
  setCenterSpy.mockClear();
  fitViewSpy.mockClear();
});

describe("WorkflowCanvas 受控聚焦（focusNodeRequest）", () => {
  it("TC-FOCUS-01 nonce 初次到达 → setCenter 以回退尺寸中心调用", () => {
    const { rerenderWith } = renderCanvas();
    primeInstance();
    rerenderWith({ nodeId: "n1", nonce: 1 });
    expect(setCenterSpy).toHaveBeenCalledTimes(1);
    // 回退尺寸 180×60 → 中心 = (100+90, 50+30)
    expect(setCenterSpy).toHaveBeenCalledWith(190, 80, {
      zoom: 1,
      duration: 300,
    });
  });

  it("TC-FOCUS-02 同 nonce 重放不重复触发（dimensions 更新驱动 effect 重跑）", () => {
    const { rerenderWith } = renderCanvas();
    primeInstance();
    rerenderWith({ nodeId: "n1", nonce: 1 });
    expect(setCenterSpy).toHaveBeenCalledTimes(1);

    // nodeDimensions 变更 → effect 依赖变化重跑 → nonce 已消费，跳过
    act(() => {
      getProps().onNodesChange([dimensionsChange("n1", 120, 50)]);
    });
    rerenderWith({ nodeId: "n1", nonce: 1 });
    expect(setCenterSpy).toHaveBeenCalledTimes(1);
  });

  it("TC-FOCUS-03 nonce 递增新请求再次触发（目标切换）", () => {
    const { rerenderWith } = renderCanvas();
    primeInstance();
    rerenderWith({ nodeId: "n1", nonce: 1 });
    rerenderWith({ nodeId: "n2", nonce: 2 });
    expect(setCenterSpy).toHaveBeenCalledTimes(2);
    // n2 回退中心 = (300+90, 200+30)
    expect(setCenterSpy).toHaveBeenLastCalledWith(390, 230, {
      zoom: 1,
      duration: 300,
    });
  });

  it("TC-FOCUS-04 已测量尺寸优先：setCenter 用实测中心", () => {
    const { rerenderWith } = renderCanvas();
    primeInstance();
    act(() => {
      getProps().onNodesChange([dimensionsChange("n1", 200, 80)]);
    });
    rerenderWith({ nodeId: "n1", nonce: 1 });
    expect(setCenterSpy).toHaveBeenCalledTimes(1);
    // 实测 200×80 → 中心 = (100+100, 50+40)
    expect(setCenterSpy).toHaveBeenCalledWith(200, 90, {
      zoom: 1,
      duration: 300,
    });
  });

  it("TC-FOCUS-05 目标节点不存在 → 不触发且不消费（节点后补后兑现）", () => {
    const { rerenderWith } = renderCanvas();
    primeInstance();
    rerenderWith({ nodeId: "ghost", nonce: 1 });
    expect(setCenterSpy).not.toHaveBeenCalled();

    // nonce 未消费：后续渲染目标就绪即兑现
    rerenderWith({ nodeId: "n1", nonce: 1 });
    expect(setCenterSpy).toHaveBeenCalledTimes(1);
    expect(setCenterSpy).toHaveBeenCalledWith(190, 80, {
      zoom: 1,
      duration: 300,
    });
  });

  it("TC-FOCUS-06 实例未就绪时请求保留，实例就绪后下一次渲染兑现", () => {
    const { rerenderWith } = renderCanvas();
    // 实例未注入：请求不消费
    rerenderWith({ nodeId: "n1", nonce: 1 });
    expect(setCenterSpy).not.toHaveBeenCalled();

    primeInstance();
    // 下一次渲染（同 nonce）兑现
    rerenderWith({ nodeId: "n1", nonce: 1 });
    expect(setCenterSpy).toHaveBeenCalledTimes(1);
    expect(setCenterSpy).toHaveBeenCalledWith(190, 80, {
      zoom: 1,
      duration: 300,
    });
  });
});
