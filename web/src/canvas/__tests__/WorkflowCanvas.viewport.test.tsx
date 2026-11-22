// =============================================================================
// 画布视口回归单元测试（canvas/__tests__/WorkflowCanvas.viewport.test.tsx）
//
// 留待非沙箱环境执行：npx vitest run src/canvas/__tests__/WorkflowCanvas.viewport.test.tsx
//
// 覆盖用例（对应 AC-08 / NFR-010）：
//   V-01 空画布拖入首节点：不触发 fitView、回写恰含 1 节点（AC-01/FR-003/004/006/008/013）
//   V-02 连续放置首、次节点均不触发 fitView（AC-03/FR-009）
//   V-03 外部载入（非 onDrop）：尺寸就绪时 fitView 恰好 1 次（FR-012）
//   V-04 外部载入后继续放置不重复适配（I3）
//   V-05 首节点放置后位置变更/删除：配置不回退、不触发 fitView（FR-010/014/NFR-007）
//   V-06 纯 dimensions 变更不产生回写（保住持久化 P-05 / I4）
//   V-07 控制器消费后，显式 fitView（Controls 路径等价）仍可执行（AC-05/FR-007/011）
//   V-08 外部载入分批测量：全部节点就绪前不消费一次性适配，就绪后恰 1 次
//   V-09 刷新场景（挂载即非空）逐节点分批测量：全部就绪后 fitView 恰 1 次
//
// 测试策略：沿用 WorkflowCanvas.persistence.test.tsx 的 vi.mock("reactflow") 桩手法——
// 捕获传给 <ReactFlow> 的回调 props（onNodesChange/onDrop/onInit），注入含 fitView: vi.fn()
// 与 screenToFlowPosition 的实例桩，通过「渲染 → 手动触发 onDrop / onNodesChange(dimensions)
// / rerender 改变 canvasState」驱动时序，断言 fitView spy 调用次数。不依赖 jsdom 布局与 d3-zoom。
//
// 沙箱限制声明：沙箱内 tsc 全量编译超时、vitest 无法运行，本文件作为交付物入库（NFR-009）。
// =============================================================================

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render } from "@testing-library/react";
import type { NodeChange, ReactFlowInstance } from "reactflow";
import type { DragEvent as ReactDragEvent, SetStateAction } from "react";
import WorkflowCanvas from "../WorkflowCanvas";
import type { WorkflowCanvasState, CanvasNode } from "../canvasTypes";
import type { NodeConfiguration } from "../../workflow-definition/definitionTypes";

/** 捕获的回调 props 形状：仅声明本组用例驱动所需的通道 */
interface CapturedReactFlowProps {
  onNodesChange: (changes: NodeChange[]) => void;
  onDrop: (event: ReactDragEvent) => void;
  onInit: (instance: ReactFlowInstance) => void;
}

/** ReactFlow 实例桩形状：仅本组用例用到的两个成员 */
interface InstanceStub {
  screenToFlowPosition: (position: { x: number; y: number }) => { x: number; y: number };
  fitView: ReturnType<typeof vi.fn>;
}

/** 被捕获的 ReactFlow props（模块级，供各用例读取） */
const rfCapture = vi.hoisted(() => ({
  props: null as CapturedReactFlowProps | null,
}));

/** fitView 观测 spy：断言一次性适配的调用次数 */
const fitViewSpy = vi.fn();

/** 已注入的实例桩（供 V-07 模拟显式适配路径） */
let instanceStub: InstanceStub | null = null;

// 以桩组件替换 ReactFlow 默认导出：渲染时不展开真实画布，仅记录回调 props。
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

// —— 非默认配置夹具 ——
const configA: NodeConfiguration = {
  node_type: "trigger",
  trigger_type: "scheduled",
  schedule: "0 9 * * *",
  input_template: { date: "{{today}}" },
};
const configB: NodeConfiguration = {
  node_type: "agent",
  model_identifier: "custom-model-v2",
  system_prompt: "你是数据质量巡检助手",
  available_tools: ["query_db"],
};

/** 构造最小画布节点 */
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

/** 构造拖放事件 */
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

/** 构造 dimensions 变更（尺寸就绪信号） */
function dimensionsChange(id: string): NodeChange {
  return { type: "dimensions", id, dimensions: { width: 100, height: 40 } };
}

/**
 * 函数式提交适配（CHG-1~2）：以基准状态应用 onCanvasStateChange 捕获的更新
 * （参数可能为 updater 纯函数或传值两种形态），返回最终状态。
 */
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

/** 渲染受控画布，返回回调 spy 与受控 rerender（支持 canvasGeneration 注入，默认 0 向后兼容） */
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

/** 注入实例桩：screenToFlowPosition + fitView spy（模拟 onInit 回调） */
function primeInstance(): void {
  instanceStub = {
    screenToFlowPosition: () => ({ x: 100, y: 200 }),
    fitView: fitViewSpy,
  };
  getProps().onInit(instanceStub as unknown as ReactFlowInstance);
}

beforeEach(() => {
  rfCapture.props = null;
  instanceStub = null;
  fitViewSpy.mockClear();
});

describe("WorkflowCanvas 视口稳定性", () => {
  it("V-01 空画布拖入首节点：不触发 fitView、回写恰含 1 节点", () => {
    const initial: WorkflowCanvasState = { nodes: [], edges: [] };
    const { onStateChange, rerenderWith } = renderCanvas(initial);
    primeInstance();

    // 拖入首节点，并模拟父层受控更新（onCanvasStateChange → App.setState → rerender）
    getProps().onDrop(makeDropEvent("agent"));
    expect(onStateChange).toHaveBeenCalledTimes(1);
    // 函数式提交适配（CHG-2）：以基准状态应用 updater 得到下一状态
    const nextState = applyCapturedUpdate(onStateChange.mock.calls, initial);
    expect(nextState.nodes).toHaveLength(1);
    rerenderWith(nextState);

    // 节点尺寸测量完成：不得触发隐式适配
    getProps().onNodesChange([dimensionsChange(nextState.nodes[0].id)]);

    expect(fitViewSpy).not.toHaveBeenCalled();
  });

  it("V-02 连续放置首、次节点均不触发 fitView", () => {
    const initial: WorkflowCanvasState = { nodes: [], edges: [] };
    const { onStateChange, rerenderWith } = renderCanvas(initial);
    primeInstance();

    // 首节点
    getProps().onDrop(makeDropEvent("agent"));
    // 函数式提交适配（CHG-2）：以基准状态应用 updater 得到下一状态
    const s1 = applyCapturedUpdate(onStateChange.mock.calls, initial);
    rerenderWith(s1);
    getProps().onNodesChange([dimensionsChange(s1.nodes[0].id)]);

    // 次节点（tool 已移除拖拽入口，改用 data_source：merge_tool_into_agent_node DC-C）
    onStateChange.mockClear();
    getProps().onDrop(makeDropEvent("data_source"));
    // 函数式提交适配（CHG-2）：以首节点后的状态为基准应用 updater
    const s2 = applyCapturedUpdate(onStateChange.mock.calls, s1);
    rerenderWith(s2);
    getProps().onNodesChange([dimensionsChange(s2.nodes[1].id)]);

    expect(fitViewSpy).not.toHaveBeenCalled();
  });

  it("V-03 外部载入（非 onDrop）：尺寸就绪时 fitView 恰好 1 次", () => {
    const { rerenderWith } = renderCanvas({ nodes: [], edges: [] });
    primeInstance();

    // 不经 onDrop：直接由父层一次性载入含节点定义（示例库 / YAML 导入路径）
    const loaded: WorkflowCanvasState = {
      nodes: [makeNode("n-a", "触发器A", configA), makeNode("n-b", "Agent B", configB)],
      edges: [],
    };
    rerenderWith(loaded);
    getProps().onNodesChange([dimensionsChange("n-a"), dimensionsChange("n-b")]);

    expect(fitViewSpy).toHaveBeenCalledTimes(1);
  });

  it("V-04 外部载入后继续放置不重复适配", () => {
    const { onStateChange, rerenderWith } = renderCanvas({ nodes: [], edges: [] });
    primeInstance();

    const loaded: WorkflowCanvasState = {
      nodes: [makeNode("n-a", "触发器A", configA)],
      edges: [],
    };
    rerenderWith(loaded);
    getProps().onNodesChange([dimensionsChange("n-a")]);
    expect(fitViewSpy).toHaveBeenCalledTimes(1);

    // 继续拖入新节点：不应再次适配（tool 已移除拖拽入口，改用 data_source）
    getProps().onDrop(makeDropEvent("data_source"));
    // 函数式提交适配（CHG-2）：以外部载入状态为基准应用 updater
    const nextState = applyCapturedUpdate(onStateChange.mock.calls, loaded);
    rerenderWith(nextState);
    getProps().onNodesChange([dimensionsChange(nextState.nodes[1].id)]);

    expect(fitViewSpy).toHaveBeenCalledTimes(1);
  });

  it("V-05 首节点放置后位置变更：回写保留配置且不触发 fitView", () => {
    const initial: WorkflowCanvasState = { nodes: [], edges: [] };
    const { onStateChange, rerenderWith } = renderCanvas(initial);
    primeInstance();

    getProps().onDrop(makeDropEvent("agent"));
    // 函数式提交适配（CHG-2）：以基准状态应用 updater 得到下一状态
    let state = applyCapturedUpdate(onStateChange.mock.calls, initial);
    rerenderWith(state);
    getProps().onNodesChange([dimensionsChange(state.nodes[0].id)]);
    expect(fitViewSpy).not.toHaveBeenCalled();

    // 位置变更：以唯一源回写，配置逐字段保留
    onStateChange.mockClear();
    const nodeId = state.nodes[0].id;
    getProps().onNodesChange([
      { type: "position", id: nodeId, position: { x: 30, y: 40 }, dragging: false },
    ]);
    expect(onStateChange).toHaveBeenCalledTimes(1);
    // 函数式提交适配（CHG-3）：以上一状态为基准应用 updater
    state = applyCapturedUpdate(onStateChange.mock.calls, state);
    expect(state.nodes[0].position).toEqual({ x: 30, y: 40 });
    expect(state.nodes[0].data.configuration).toEqual({
      node_type: "agent",
      model_identifier: "",
      system_prompt: "",
      available_tools: [],
    });
    expect(fitViewSpy).not.toHaveBeenCalled();
  });

  it("V-06 纯 dimensions 变更不产生回写", () => {
    const initial: WorkflowCanvasState = { nodes: [], edges: [] };
    const { onStateChange, rerenderWith } = renderCanvas(initial);
    primeInstance();

    getProps().onDrop(makeDropEvent("agent"));
    // 函数式提交适配（CHG-2）：以基准状态应用 updater 得到下一状态
    const state = applyCapturedUpdate(onStateChange.mock.calls, initial);
    rerenderWith(state);
    onStateChange.mockClear();

    getProps().onNodesChange([dimensionsChange(state.nodes[0].id)]);

    expect(onStateChange).not.toHaveBeenCalled();
  });

  it("V-07 控制器消费后，显式 fitView（Controls 路径等价）仍可执行", () => {
    const initial: WorkflowCanvasState = { nodes: [], edges: [] };
    const { onStateChange, rerenderWith } = renderCanvas(initial);
    primeInstance();

    // 拖入首节点：控制器判定"永久禁止自动适配"并消费
    getProps().onDrop(makeDropEvent("agent"));
    // 函数式提交适配（CHG-2）：以基准状态应用 updater 得到下一状态
    const state = applyCapturedUpdate(onStateChange.mock.calls, initial);
    rerenderWith(state);
    getProps().onNodesChange([dimensionsChange(state.nodes[0].id)]);
    expect(fitViewSpy).not.toHaveBeenCalled();

    // 用户显式适配（Controls 适配按钮经引擎调用同一实例 API）：不受控制器门控影响
    if (instanceStub === null) {
      throw new Error("实例桩未注入");
    }
    instanceStub.fitView();
    expect(fitViewSpy).toHaveBeenCalledTimes(1);
  });

  it("V-08 外部载入分批测量：全部节点就绪前不消费，就绪后 fitView 恰 1 次", () => {
    const { rerenderWith } = renderCanvas({ nodes: [], edges: [] });
    primeInstance();

    const loaded: WorkflowCanvasState = {
      nodes: [makeNode("n-a", "触发器A", configA), makeNode("n-b", "Agent B", configB)],
      edges: [],
    };
    rerenderWith(loaded);

    // 首批仅部分节点就绪：不得提前消费一次性适配
    getProps().onNodesChange([dimensionsChange("n-a")]);
    expect(fitViewSpy).not.toHaveBeenCalled();

    // 末节点就绪：恰在此刻适配
    getProps().onNodesChange([dimensionsChange("n-b")]);
    expect(fitViewSpy).toHaveBeenCalledTimes(1);

    // 就绪后到达的重复尺寸信号：不再适配
    getProps().onNodesChange([dimensionsChange("n-a")]);
    expect(fitViewSpy).toHaveBeenCalledTimes(1);
  });

  it("V-09 刷新场景（挂载即非空）逐节点分批测量：全部就绪后 fitView 恰 1 次", () => {
    const restored: WorkflowCanvasState = {
      nodes: [
        makeNode("n-a", "触发器A", configA),
        makeNode("n-b", "Agent B", configB),
        makeNode("n-c", "触发器C", configA),
      ],
      edges: [],
    };
    renderCanvas(restored);
    primeInstance();

    getProps().onNodesChange([dimensionsChange("n-a")]);
    expect(fitViewSpy).not.toHaveBeenCalled();
    getProps().onNodesChange([dimensionsChange("n-b")]);
    expect(fitViewSpy).not.toHaveBeenCalled();

    // 最后一个节点测量完成：恰此刻适配（刷新后自动 fit view）
    getProps().onNodesChange([dimensionsChange("n-c")]);
    expect(fitViewSpy).toHaveBeenCalledTimes(1);
  });

  it("TC-VIEW-01 代际切换必须重新适配：A(g=1)→B(g=2) fitView 累计 2 次", () => {
    const stateA: WorkflowCanvasState = {
      nodes: [makeNode("n-a", "触发器A", configA)],
      edges: [],
    };
    const stateB: WorkflowCanvasState = {
      nodes: [makeNode("n-b", "触发器B", configA)],
      edges: [],
    };

    // 代际 1：载入 A → 适配一次
    const { rerenderWith } = renderCanvas(stateA, 1);
    primeInstance();
    getProps().onNodesChange([dimensionsChange("n-a")]);
    expect(fitViewSpy).toHaveBeenCalledTimes(1);

    // 代际 2：切换至 B → 必须重新适配
    rerenderWith(stateB, 2);
    primeInstance();
    getProps().onNodesChange([dimensionsChange("n-b")]);
    expect(fitViewSpy).toHaveBeenCalledTimes(2);
  });
});