// =============================================================================
// 画布代际隔离单元测试（canvas/__tests__/WorkflowCanvas.generation.test.tsx）
//
// 留待非沙箱环境执行：npx vitest run src/canvas/__tests__/WorkflowCanvas.generation.test.tsx
//
// 覆盖用例（对应 CHG-8 / DC-10 / FR-3.4 / INV-6 / INV-15）：
//   TC-GEN-01 外部载入代际切换：A(g=1)→B(g=2) 各触发 fitView 恰好 1 次
//   TC-GEN-02 onDrop 放置（代际不变）：fitView 不被调用（保 V-01/V-02）
//   TC-GEN-03 代际变化：<ReactFlow> key 值改变（重挂载）
//
// 测试策略：沿用 vi.mock("reactflow") 桩手法，捕获 nodes/回调 props，
// 以 useEffect 计数桩挂载次数断言 key 重挂载，注入含 fitView: vi.fn() 的实例桩。
//
// 沙箱限制声明：沙箱内 tsc 全量编译超时、vitest 无法运行，本文件作为交付物入库（NFR-009）。
// =============================================================================

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render } from "@testing-library/react";
import type { Node, NodeChange, ReactFlowInstance } from "reactflow";
import type { DragEvent as ReactDragEvent, SetStateAction } from "react";
import WorkflowCanvas from "../WorkflowCanvas";
import type { WorkflowCanvasState, CanvasNode, CanvasNodeData } from "../canvasTypes";
import type { NodeConfiguration } from "../../workflow-definition/definitionTypes";

/** 捕获的回调 props 形状：含 nodes（受控节点数组） */
interface CapturedReactFlowProps {
  nodes: Node<CanvasNodeData>[];
  onNodesChange: (changes: NodeChange[]) => void;
  onDrop: (event: ReactDragEvent) => void;
  onInit: (instance: ReactFlowInstance) => void;
}

/** ReactFlow 实例桩形状 */
interface InstanceStub {
  screenToFlowPosition: (position: { x: number; y: number }) => { x: number; y: number };
  fitView: ReturnType<typeof vi.fn>;
}

/** 被捕获的 ReactFlow props（模块级，供各用例读取） */
const rfCapture = vi.hoisted(() => ({
  props: null as CapturedReactFlowProps | null,
}));

/** 桩挂载计数（检测 key 变化导致的重挂载） */
const mountCount = vi.hoisted(() => ({ value: 0 }));

/** fitView 观测 spy */
const fitViewSpy = vi.fn();

/** 已注入的实例桩 */
let instanceStub: InstanceStub | null = null;

// 以桩组件替换 ReactFlow 默认导出：捕获 nodes/回调 props，以 useEffect 计数挂载
vi.mock("reactflow", async (importOriginal) => {
  const actual = await importOriginal<typeof import("reactflow")>();
  const { useEffect } = await import("react");
  return {
    ...actual,
    default: (props: CapturedReactFlowProps) => {
      useEffect(() => {
        mountCount.value++;
      }, []);
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
const configB: NodeConfiguration = {
  node_type: "agent",
  model_identifier: "",
  system_prompt: "",
  available_tools: [],
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

function makeDropEvent(type: string): ReactDragEvent {
  return {
    preventDefault: vi.fn(),
    dataTransfer: {
      getData: (key: string) => (key === "application/node-type" ? type : ""),
    },
    clientX: 100,
    clientY: 200,
  } as unknown as ReactDragEvent;
}

function dimensionsChange(id: string): NodeChange {
  return { type: "dimensions", id, dimensions: { width: 100, height: 40 } };
}

/** 渲染受控画布，支持 canvasGeneration 注入与 rerender（可变更 generation + state） */
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
  instanceStub = {
    screenToFlowPosition: () => ({ x: 100, y: 200 }),
    fitView: fitViewSpy,
  };
  getProps().onInit(instanceStub as unknown as ReactFlowInstance);
}

/** 函数式提交适配：以基准状态应用 onCanvasStateChange 捕获的更新 */
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

beforeEach(() => {
  rfCapture.props = null;
  instanceStub = null;
  fitViewSpy.mockClear();
  mountCount.value = 0;
});

describe("WorkflowCanvas 代际隔离", () => {
  it("TC-GEN-01 外部载入代际切换：A(g=1)→B(g=2) 各触发 fitView 恰好 1 次", () => {
    const stateA: WorkflowCanvasState = {
      nodes: [makeNode("trigger_start", "触发器A", configA)],
      edges: [],
    };
    const stateB: WorkflowCanvasState = {
      nodes: [
        makeNode("trigger_start", "触发器B", configA),
        makeNode("agent_b", "Agent B", configB),
      ],
      edges: [],
    };

    // 代际 1：载入 A（挂载即非空 + 代际变化 -1→1 → arm 适配）
    const { rerenderWith } = renderCanvas(stateA, 1);
    primeInstance();
    getProps().onNodesChange([dimensionsChange("trigger_start")]);
    expect(fitViewSpy).toHaveBeenCalledTimes(1);

    // 代际 2：切换至 B（同名 id trigger_start，跨代际隔离 → 重新 arm 适配；
    // 全量就绪门控：B 含两节点，须两者均上报尺寸后才消费）
    rerenderWith(stateB, 2);
    primeInstance();
    getProps().onNodesChange([
      dimensionsChange("trigger_start"),
      dimensionsChange("agent_b"),
    ]);
    expect(fitViewSpy).toHaveBeenCalledTimes(2);
  });

  it("TC-GEN-02 onDrop 放置节点（代际不变）：fitView 不被调用", () => {
    const initial: WorkflowCanvasState = { nodes: [], edges: [] };
    const { onStateChange, rerenderWith } = renderCanvas(initial, 0);
    primeInstance();

    // 拖入首节点（代际不变）
    getProps().onDrop(makeDropEvent("agent"));
    expect(onStateChange).toHaveBeenCalledTimes(1);

    // 模拟父层受控更新（代际不变）
    const nextState = applyCapturedUpdate(onStateChange.mock.calls, initial);
    rerenderWith(nextState, 0);

    getProps().onNodesChange([dimensionsChange(nextState.nodes[0].id)]);
    expect(fitViewSpy).not.toHaveBeenCalled();
  });

  it("TC-GEN-03 代际变化：<ReactFlow> key 值改变（重挂载）", () => {
    const state: WorkflowCanvasState = {
      nodes: [makeNode("n1", "节点", configA)],
      edges: [],
    };

    const { rerenderWith } = renderCanvas(state, 1);
    expect(mountCount.value).toBe(1);

    // 同代际 rerender：不重挂载
    rerenderWith(state, 1);
    expect(mountCount.value).toBe(1);

    // 代际变化：key 改变 → 重挂载
    rerenderWith(state, 2);
    expect(mountCount.value).toBe(2);
  });
});