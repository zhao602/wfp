// =============================================================================
// 边删除链路测试（canvas/__tests__/WorkflowCanvas.edgeDelete.test.tsx）
//
// 留待非沙箱环境执行：cd web && npx vitest run
//
// 覆盖用例（add_edge_delete / CHG-E14，ED-01~07，对应任务 T17.1~T17.7）：
//   ED-01 选中边 Delete/Backspace 双键 → onDeleteEdge 上抛、目标边移除、nodes 引用不变
//         （FR-003/FR-006/FR-007/FR-013；AC-005/AC-006/AC-010/AC-012/AC-021；
//          INV-D01/INV-D05；必做守护 G-02：expect(next.nodes).toBe(prev.nodes)）
//   ED-02 输入控件聚焦守卫：INPUT 内按删除键不上抛（FR-012；AC-020；INV-009/INV-D07）
//   ED-03 无选中静默 / 节点优先（FR-001/FR-014；AC-002/AC-022）
//   ED-04 删 1 条边其余边与两端节点逐字段不变（FR-006/FR-007；AC-010~AC-013；INV-D01/INV-D05）
//   ED-05 目标不存在幂等安全、不入栈（FR-015；AC-023；INV-D05）
//   ED-06 删除→撤销→重做端到端：逐字段复现、逆序无漂移、新删除清空 redo
//         （FR-010/FR-011；AC-016~AC-019；INV-008/INV-D08）
//   ED-07 onEdgesChange 过滤 select 变更：数据纯净、remove/reset 仍生效（INV-D04/INV-D06；risk R1）
//
// 测试策略：沿用 WorkflowCanvas.edges.test.tsx 的 vi.mock 桩模式捕获 ReactFlow props；
// 键盘事件走真实外层容器 div（onKeyDown 未桩化）；
// App 级编排（handleDeleteEdge/handleUndo/handleRedo，与 App.tsx 同形）以「真实
// useCanvasHistory（renderHook）+ 受控驱动器」复刻（T17 preamble 允许的受控驱动器路径，
// 不渲染 App 本体）。
//
// 沙箱限制声明：本文件作为交付物入库，请在非沙箱环境执行。
// =============================================================================

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, act, renderHook } from "@testing-library/react";
import type { MouseEvent as ReactMouseEvent } from "react";
import type { SetStateAction } from "react";
import type { Edge, EdgeChange } from "reactflow";
import WorkflowCanvas from "../WorkflowCanvas";
import { useCanvasHistory } from "../useCanvasHistory";
import type { CanvasHistory } from "../useCanvasHistory";
import type { WorkflowCanvasState, CanvasNode } from "../canvasTypes";
import type { NodeConfiguration } from "../../workflow-definition/definitionTypes";

/** 受控驱动器回调形态：与 onCanvasStateChange 契约一致（updater/传值两形态） */
type StateUpdate = SetStateAction<WorkflowCanvasState>;

/** 捕获的回调 props 形状：仅声明本文件测试驱动所需的通道 */
interface CapturedReactFlowProps {
  onEdgesChange: (changes: EdgeChange[]) => void;
  onEdgeClick: (event: ReactMouseEvent, edge: Edge) => void;
}

/** 被捕获的 ReactFlow props（模块级，供各用例读取） */
const rfCapture = vi.hoisted(() => ({
  props: null as CapturedReactFlowProps | null,
}));

// 以桩组件替换 ReactFlow 默认导出：渲染时不展开真实画布，仅记录回调 props
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
    throw new Error("尚未捕获 ReactFlow props：WorkflowCanvas 未渲染或未传回写通道");
  }
  return rfCapture.props;
}

/** 受控更新驱动器：承接 onCanvasStateChange 的两种形态（updater/传值） */
function createDriver(initial: WorkflowCanvasState) {
  let state = initial;
  const onStateChange = vi.fn((update: StateUpdate) => {
    state =
      typeof update === "function"
        ? (update as (prev: WorkflowCanvasState) => WorkflowCanvasState)(state)
        : update;
  });
  return { onStateChange, getState: (): WorkflowCanvasState => state };
}

/** 最小触发配置夹具 */
const configA: NodeConfiguration = {
  node_type: "trigger",
  trigger_type: "manual",
  input_template: {},
};

/** 构造最小画布节点 */
function makeNode(id: string, configuration: NodeConfiguration): CanvasNode {
  return {
    id,
    type: configuration.node_type,
    position: { x: 0, y: 0 },
    data: {
      node_id: id,
      name: id,
      node_type: configuration.node_type,
      configuration,
    },
  };
}

/** 构造最小连线 */
function makeEdge(id: string, source: string, target: string): Edge {
  return { id, source, target };
}

/** 渲染选项：按需注入选中节点与删除回调 */
interface RenderCanvasOptions {
  state: WorkflowCanvasState;
  onStateChange: (update: StateUpdate) => void;
  selectedNodeId?: string | null;
  onDeleteNode?: (nodeId: string) => void;
  onDeleteEdge?: (edgeId: string) => void;
}

/** 渲染受控画布并返回承载 onKeyDown 的外层容器 div */
function renderCanvas(options: RenderCanvasOptions): HTMLElement {
  const { container } = render(
    <WorkflowCanvas
      canvasState={options.state}
      onCanvasStateChange={options.onStateChange}
      selectedNodeId={options.selectedNodeId ?? null}
      onSelectNode={() => {}}
      onDeleteNode={options.onDeleteNode}
      onDeleteEdge={options.onDeleteEdge}
    />,
  );
  const wrapper = container.firstElementChild;
  if (!(wrapper instanceof HTMLElement)) {
    throw new Error("未找到画布外层容器：WorkflowCanvas 未渲染");
  }
  return wrapper;
}

/**
 * App 级编排复刻（与 App.tsx handleDeleteEdge/handleUndo/handleRedo 同形）：
 * 真实 useCanvasHistory（renderHook 挂载）+ 受控驱动器；
 * handleDeleteEdge 三段式：存在性预判 → 变更前快照入栈（外置，INV-D09）→ 一次原子回写。
 */
function createAppOrchestration(initial: WorkflowCanvasState) {
  const historyHook = renderHook(() => useCanvasHistory<WorkflowCanvasState>(50));
  const driver = createDriver(initial);
  const handleDeleteEdge = vi.fn((edgeId: string) => {
    const current = driver.getState();
    // 存在性预判（AC-023，幂等安全）：目标不存在静默返回、不入栈
    if (!current.edges.some((edge) => edge.id === edgeId)) {
      return;
    }
    // 变更前快照入栈（外置于 updater，INV-D09；新变更清空 redo 分支，AC-019）
    historyHook.result.current.push(current);
    // 一次原子回写（DC-05/INV-D01）：nodes 引用透传，仅过滤目标边
    driver.onStateChange((prev) => ({
      nodes: prev.nodes,
      edges: prev.edges.filter((edge) => edge.id !== edgeId),
    }));
  });
  // 撤销/重做（CHG-E11）：先取快照（同步段）→ 再 setCanvasState(快照)
  const handleUndo = () => {
    const snapshot = historyHook.result.current.undo(driver.getState());
    if (snapshot !== undefined) {
      driver.onStateChange(snapshot);
    }
  };
  const handleRedo = () => {
    const snapshot = historyHook.result.current.redo(driver.getState());
    if (snapshot !== undefined) {
      driver.onStateChange(snapshot);
    }
  };
  return {
    driver,
    handleDeleteEdge,
    handleUndo,
    handleRedo,
    history: (): CanvasHistory<WorkflowCanvasState> => historyHook.result.current,
  };
}

beforeEach(() => {
  rfCapture.props = null;
});

describe("WorkflowCanvas 边删除链路", () => {
  // ED-01（T17.1）：选中边后 Delete/Backspace 双键均上抛删除意图；
  // App 编排后仅目标边移除，nodes 引用恒不变（G-02），变更前快照逐次入栈。
  it("ED-01 选中边 Delete/Backspace → onDeleteEdge 上抛、目标边移除、nodes 引用不变（AC-005/AC-006/AC-010/AC-012/AC-021，G-02）", () => {
    const nodes = [makeNode("A", configA), makeNode("B", configA), makeNode("C", configA)];
    const initial: WorkflowCanvasState = {
      nodes,
      edges: [makeEdge("e1", "A", "B"), makeEdge("e2", "B", "C")],
    };
    const app = createAppOrchestration(initial);
    const wrapper = renderCanvas({
      state: app.driver.getState(),
      onStateChange: app.driver.onStateChange,
      onDeleteEdge: app.handleDeleteEdge,
    });

    // Backspace 删 e1
    act(() => {
      getProps().onEdgeClick({} as ReactMouseEvent, makeEdge("e1", "A", "B"));
    });
    fireEvent.keyDown(wrapper, { key: "Backspace" });
    expect(app.handleDeleteEdge).toHaveBeenNthCalledWith(1, "e1");
    let next = app.driver.getState();
    expect(next.edges.map((edge) => edge.id)).toEqual(["e2"]);
    expect(next.nodes).toBe(nodes); // 必做守护 G-02：nodes 引用透传

    // Delete 删 e2
    act(() => {
      getProps().onEdgeClick({} as ReactMouseEvent, makeEdge("e2", "B", "C"));
    });
    fireEvent.keyDown(wrapper, { key: "Delete" });
    expect(app.handleDeleteEdge).toHaveBeenNthCalledWith(2, "e2");
    next = app.driver.getState();
    expect(next.edges).toHaveLength(0);
    expect(next.nodes).toBe(nodes); // G-02：两次删除 nodes 引用恒不变
    expect(app.history().canUndo).toBe(true); // 变更前快照逐次入栈
  });

  // ED-02（T17.2）：焦点在 INPUT 内按删除键 → 守卫前置拦截，不上抛、不回写。
  it("ED-02 焦点在 INPUT 内按删除键 → 不上抛、状态不变（AC-020/INV-009）", () => {
    const initial: WorkflowCanvasState = {
      nodes: [makeNode("A", configA), makeNode("B", configA)],
      edges: [makeEdge("e1", "A", "B")],
    };
    const driver = createDriver(initial);
    const onDeleteNode = vi.fn();
    const onDeleteEdge = vi.fn();
    const wrapper = renderCanvas({
      state: driver.getState(),
      onStateChange: driver.onStateChange,
      onDeleteNode,
      onDeleteEdge,
    });
    // 先选中边：证明后续静默来自输入控件守卫而非「无选中」
    act(() => {
      getProps().onEdgeClick({} as ReactMouseEvent, makeEdge("e1", "A", "B"));
    });
    const input = document.createElement("input");
    wrapper.appendChild(input);
    fireEvent.keyDown(input, { key: "Backspace" });
    expect(onDeleteNode).not.toHaveBeenCalled();
    expect(onDeleteEdge).not.toHaveBeenCalled();
    expect(driver.onStateChange).not.toHaveBeenCalled();
  });

  // ED-03（T17.3）：选中节点+边时节点优先（只删节点）；均无选中时静默。
  it("ED-03 节点优先：选中节点+边时按键只删节点；均无选中时静默（AC-002/AC-022）", () => {
    // 节点优先：selectedNodeId 与 selectedEdgeId 并存 → 只上抛 onDeleteNode
    const initial: WorkflowCanvasState = {
      nodes: [makeNode("A", configA), makeNode("B", configA)],
      edges: [makeEdge("e1", "A", "B")],
    };
    const driver = createDriver(initial);
    const onDeleteNode = vi.fn();
    const onDeleteEdge = vi.fn();
    const wrapper = renderCanvas({
      state: driver.getState(),
      onStateChange: driver.onStateChange,
      selectedNodeId: "A",
      onDeleteNode,
      onDeleteEdge,
    });
    act(() => {
      getProps().onEdgeClick({} as ReactMouseEvent, makeEdge("e1", "A", "B"));
    });
    fireEvent.keyDown(wrapper, { key: "Delete" });
    expect(onDeleteNode).toHaveBeenCalledTimes(1);
    expect(onDeleteNode).toHaveBeenCalledWith("A");
    expect(onDeleteEdge).not.toHaveBeenCalled();

    // 均无选中：静默，无任何上抛与回写
    const driverSilent = createDriver(initial);
    const onDeleteNodeSilent = vi.fn();
    const onDeleteEdgeSilent = vi.fn();
    const wrapperSilent = renderCanvas({
      state: driverSilent.getState(),
      onStateChange: driverSilent.onStateChange,
      onDeleteNode: onDeleteNodeSilent,
      onDeleteEdge: onDeleteEdgeSilent,
    });
    fireEvent.keyDown(wrapperSilent, { key: "Delete" });
    expect(onDeleteNodeSilent).not.toHaveBeenCalled();
    expect(onDeleteEdgeSilent).not.toHaveBeenCalled();
    expect(driverSilent.onStateChange).not.toHaveBeenCalled();
  });

  // ED-04（T17.4）：3 条边删 1 条 → 其余 2 条逐字段不变、两端节点逐字段不变。
  it("ED-04 删 1 条边 → 其余边与两端节点逐字段不变（AC-010/AC-011/AC-012/AC-013）", () => {
    const nodes = [makeNode("A", configA), makeNode("B", configA), makeNode("C", configA)];
    const e1 = makeEdge("e1", "A", "B");
    const e2 = makeEdge("e2", "B", "C");
    const e3 = makeEdge("e3", "C", "A");
    const initial: WorkflowCanvasState = { nodes, edges: [e1, e2, e3] };
    const app = createAppOrchestration(initial);

    act(() => {
      app.handleDeleteEdge("e2"); // 键盘链路已由 ED-01 覆盖，此处直驱 App 编排
    });

    const next = app.driver.getState();
    expect(next.edges).toHaveLength(2);
    expect(next.edges[0]).toEqual(e1); // 其余连线逐字段不变
    expect(next.edges[1]).toEqual(e3);
    expect(next.nodes).toBe(nodes); // 两端节点集合引用不变（INV-D01）
    expect(next.nodes.map((node) => node.id)).toEqual(["A", "B", "C"]);
    expect(next.nodes.map((node) => node.type)).toEqual(["trigger", "trigger", "trigger"]);
  });

  // ED-05（T17.5）：投递不存在的边 id → 状态不变、不崩溃、不入栈（幂等）。
  it("ED-05 投递不存在的边 id → 状态不变、不崩溃、不入栈（AC-023）", () => {
    const initial: WorkflowCanvasState = {
      nodes: [makeNode("A", configA), makeNode("B", configA)],
      edges: [makeEdge("e1", "A", "B")],
    };
    const app = createAppOrchestration(initial);
    expect(() => {
      act(() => {
        app.handleDeleteEdge("ghost-edge");
      });
    }).not.toThrow();
    expect(app.handleDeleteEdge).toHaveBeenCalledTimes(1);
    expect(app.driver.getState()).toBe(initial); // 未发生任何回写，状态引用不变
    expect(app.history().canUndo).toBe(false); // 未入栈
  });

  // ED-06（T17.6）：删除→撤销→逐字段复现→重做→再消失；连续撤销逆序无漂移；新删除清空 redo。
  it("ED-06 删除→撤销→重做端到端：逐字段复现、逆序无漂移、新删除清空 redo（AC-016/AC-017/AC-018/AC-019，INV-008）", () => {
    const nodes = [makeNode("A", configA), makeNode("B", configA), makeNode("C", configA)];
    const e1 = makeEdge("e1", "A", "B");
    const e2 = makeEdge("e2", "B", "C");
    const e3 = makeEdge("e3", "C", "A");
    const initial: WorkflowCanvasState = { nodes, edges: [e1, e2, e3] };
    const app = createAppOrchestration(initial);
    const wrapper = renderCanvas({
      state: app.driver.getState(),
      onStateChange: app.driver.onStateChange,
      onDeleteEdge: app.handleDeleteEdge,
    });

    // 删 e1（键盘链路）
    act(() => {
      getProps().onEdgeClick({} as ReactMouseEvent, e1);
    });
    fireEvent.keyDown(wrapper, { key: "Delete" });
    expect(app.driver.getState().edges.map((edge) => edge.id)).toEqual(["e2", "e3"]);

    // 撤销 → e1 逐字段复现、连接关系一致（AC-016）
    act(() => {
      app.handleUndo();
    });
    let state = app.driver.getState();
    expect(state.edges).toHaveLength(3);
    expect(state.edges[0]).toEqual(e1);
    expect(state.edges).toEqual([e1, e2, e3]);

    // 重做 → e1 再次消失（AC-017）
    act(() => {
      app.handleRedo();
    });
    expect(app.driver.getState().edges.map((edge) => edge.id)).toEqual(["e2", "e3"]);

    // 连续撤销逆序无漂移：再删 e2 → 两段历史，撤销两次按逆序恢复
    act(() => {
      getProps().onEdgeClick({} as ReactMouseEvent, e2);
    });
    fireEvent.keyDown(wrapper, { key: "Delete" });
    expect(app.driver.getState().edges.map((edge) => edge.id)).toEqual(["e3"]);
    act(() => {
      app.handleUndo();
    });
    expect(app.driver.getState().edges.map((edge) => edge.id)).toEqual(["e2", "e3"]);
    act(() => {
      app.handleUndo();
    });
    state = app.driver.getState();
    expect(state.edges).toEqual([e1, e2, e3]); // 回到最初，无漂移
    expect(state.nodes).toBe(nodes);

    // 新删除后 redo 栈清空（AC-019）：撤销一段 → 删除 → redo 安全返回不动状态
    act(() => {
      app.handleRedo();
    });
    expect(app.driver.getState().edges.map((edge) => edge.id)).toEqual(["e2", "e3"]);
    act(() => {
      getProps().onEdgeClick({} as ReactMouseEvent, e3);
    });
    fireEvent.keyDown(wrapper, { key: "Delete" });
    expect(app.driver.getState().edges.map((edge) => edge.id)).toEqual(["e2"]);
    act(() => {
      app.handleRedo(); // future 已被新删除清空 → undefined → 状态不变
    });
    expect(app.driver.getState().edges.map((edge) => edge.id)).toEqual(["e2"]);
    expect(app.history().canRedo).toBe(false);
  });

  // ED-07（T17.7）：onEdgesChange 过滤 select 变更 → 数据纯净；remove/reset 仍生效。
  it("ED-07 onEdgesChange 过滤 select 变更 → edges 数据纯净、remove/reset 仍生效（INV-D04/INV-D06）", () => {
    const initial: WorkflowCanvasState = {
      nodes: [makeNode("A", configA), makeNode("B", configA)],
      edges: [makeEdge("e1", "A", "B")],
    };
    const driver = createDriver(initial);
    renderCanvas({
      state: driver.getState(),
      onStateChange: driver.onStateChange,
    });

    // select 变更被过滤：不触发回写，edges 保持纯净（不含 selected 字段）
    getProps().onEdgesChange([{ type: "select", id: "e1", selected: true }]);
    expect(driver.onStateChange).not.toHaveBeenCalled();
    expect(driver.getState().edges.every((edge) => !("selected" in edge))).toBe(true);

    // remove 仍生效（TC-EDGE-01 等价）
    getProps().onEdgesChange([{ type: "remove", id: "e1" }]);
    expect(driver.getState().edges).toHaveLength(0);

    // reset 仍生效（TC-EDGE-02 等价）
    getProps().onEdgesChange([{ type: "reset", item: makeEdge("e2", "B", "A") }]);
    expect(driver.getState().edges[0]?.id).toBe("e2");
  });
});
