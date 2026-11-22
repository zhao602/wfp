// =============================================================================
// 画布运行时尺寸回填单元测试（canvas/__tests__/WorkflowCanvas.dimensions.test.tsx）
//
// 留待非沙箱环境执行：npx vitest run src/canvas/__tests__/WorkflowCanvas.dimensions.test.tsx
//
// 覆盖用例（对应 CHG-7 / DC-9 / FR-3.1 / FR-3.3 / INV-2 / INV-14）：
//   TC-DIM-01 dimensions 到达后 nodeView 含正数 width/height
//   TC-DIM-02 dimensions 变更不产生 onCanvasStateChange 调用（保 V-06）
//   TC-DIM-03 代际变化后 nodeDimensions 清空 → 同名 id 节点不再携带上一代际尺寸
//
// 测试策略：复用 vi.mock("reactflow") 桩手法，捕获 nodes（= nodeView）以观察尺寸回填，
// 通过 onNodesChange 驱动 dimensions 变更，通过 rerender 变更 canvasGeneration 驱动代际切换。
//
// 沙箱限制声明：沙箱内 tsc 全量编译超时、vitest 无法运行，本文件作为交付物入库（NFR-009）。
// =============================================================================

import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, render } from "@testing-library/react";
import type { SetStateAction } from "react";
import type { Node, NodeChange, ReactFlowInstance } from "reactflow";
import WorkflowCanvas from "../WorkflowCanvas";
import type { WorkflowCanvasState, CanvasNode, CanvasNodeData } from "../canvasTypes";
import type { NodeConfiguration } from "../../workflow-definition/definitionTypes";

/** 捕获的回调 props 形状：含 nodes（= nodeView，观察尺寸回填） */
interface CapturedReactFlowProps {
  nodes: Node<CanvasNodeData>[];
  onNodesChange: (changes: NodeChange[]) => void;
  onInit: (instance: ReactFlowInstance) => void;
}

/** 被捕获的 ReactFlow props */
const rfCapture = vi.hoisted(() => ({
  props: null as CapturedReactFlowProps | null,
}));

/** fitView spy（实例桩需要） */
const fitViewSpy = vi.fn();

// 以桩组件替换 ReactFlow 默认导出：捕获 nodes/回调 props
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

function makeNode(
  id: string,
  name: string,
  configuration: NodeConfiguration,
): CanvasNode {
  return {
    id,
    type: configuration.node_type,
    position: { x: 0, y: 0 },
    data: {
      node_id: id,
      name,
      node_type: configuration.node_type,
      configuration,
    },
  };
}

function dimensionsChange(id: string, width = 100, height = 40): NodeChange {
  return { type: "dimensions", id, dimensions: { width, height } };
}

/** 渲染受控画布，支持 canvasGeneration 注入与 rerender */
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
  const rerenderWith = (state: WorkflowCanvasState, gen?: number) => {
    if (gen !== undefined) currentGen = gen;
    result.rerender(buildElement(state, currentGen));
  };
  return { onStateChange, onSelectNode, rerenderWith };
}

/** 注入实例桩 */
function primeInstance(): void {
  const stub = {
    screenToFlowPosition: () => ({ x: 100, y: 200 }),
    fitView: fitViewSpy,
  };
  getProps().onInit(stub as unknown as ReactFlowInstance);
}

beforeEach(() => {
  rfCapture.props = null;
  fitViewSpy.mockClear();
});

describe("WorkflowCanvas 运行时尺寸回填", () => {
  it("TC-DIM-01 dimensions 到达后 nodeView 含正数 width/height", () => {
    const state: WorkflowCanvasState = {
      nodes: [makeNode("n1", "节点", configA)],
      edges: [],
    };

    const { rerenderWith } = renderCanvas(state, 1);
    primeInstance();

    // 初始渲染：nodeView 不含尺寸（nodeDimensions 为空）
    expect(getProps().nodes[0].width).toBeUndefined();
    expect(getProps().nodes[0].height).toBeUndefined();

    // 投递 dimensions（width>0 && height>0）；act 包裹使 setNodeDimensions 同步提交后断言
    act(() => {
      getProps().onNodesChange([dimensionsChange("n1", 120, 50)]);
    });

    // nodeView 回填正数尺寸
    expect(getProps().nodes[0].width).toBe(120);
    expect(getProps().nodes[0].height).toBe(50);

    // 无变化时不重渲染（同尺寸再投递）
    act(() => {
      getProps().onNodesChange([dimensionsChange("n1", 120, 50)]);
    });
    expect(getProps().nodes[0].width).toBe(120);
    expect(getProps().nodes[0].height).toBe(50);

    rerenderWith(state, 1);
  });

  it("TC-DIM-02 dimensions 变更不产生 onCanvasStateChange 调用", () => {
    const state: WorkflowCanvasState = {
      nodes: [makeNode("n1", "节点", configA)],
      edges: [],
    };

    const { onStateChange } = renderCanvas(state, 1);
    primeInstance();
    onStateChange.mockClear();

    // 纯 dimensions 变更：不回写父层
    getProps().onNodesChange([dimensionsChange("n1")]);
    expect(onStateChange).not.toHaveBeenCalled();

    // 持久结构中无 width/height（canvasState.nodes 仍为原始结构）
    expect(state.nodes[0]).not.toHaveProperty("width");
    expect(state.nodes[0]).not.toHaveProperty("height");
  });

  it("TC-DIM-03 代际变化后 nodeDimensions 清空 → 同名 id 节点不再携带上一代际尺寸", () => {
    const state: WorkflowCanvasState = {
      nodes: [makeNode("trigger_start", "触发器", configA)],
      edges: [],
    };

    const { rerenderWith } = renderCanvas(state, 1);
    primeInstance();

    // 代际 1：dimensions 到达 → nodeView 含尺寸
    act(() => {
      getProps().onNodesChange([dimensionsChange("trigger_start", 100, 40)]);
    });
    expect(getProps().nodes[0].width).toBe(100);
    expect(getProps().nodes[0].height).toBe(40);

    // 代际 2：同名 id trigger_start，代际变化 → nodeDimensions 清空
    rerenderWith(state, 2);
    primeInstance();

    // nodeView 不再携带上一代际尺寸（须重新测量）
    expect(getProps().nodes[0].width).toBeUndefined();
    expect(getProps().nodes[0].height).toBeUndefined();

    // 新 dimensions 到达后回填新尺寸
    act(() => {
      getProps().onNodesChange([dimensionsChange("trigger_start", 110, 45)]);
    });
    expect(getProps().nodes[0].width).toBe(110);
    expect(getProps().nodes[0].height).toBe(45);
  });
});