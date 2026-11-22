// =============================================================================
// 画布节点配置持久化单元测试（canvas/__tests__/WorkflowCanvas.persistence.test.tsx）
//
// 留待非沙箱环境执行：vitest run src/canvas/__tests__/WorkflowCanvas.persistence.test.tsx
//
// 覆盖用例：
//   P-01 切换选中节点不丢失/不改变既有配置（FR-004/FR-011/AC-01）
//   P-02 拖入新节点不覆盖既有配置（FR-011/FR-012/AC-04）
//   P-03 位置变更与连线回写均不改写既有配置（FR-012/AC-04）
//   P-04 多节点交替编辑与切换互不串扰（FR-007/AC-03）
//   P-05 运行时字段隔离：select/dimensions 不回写，回写载荷不含运行时字段（NFR-007/C-5）
//   P-06 删除选中节点时清理选中态且不影响其余配置（FR-014）
//
// 测试策略：以受控 canvasState 直接驱动 WorkflowCanvas 的回写通道——通过 vi.mock 捕获
// 传给 ReactFlow 的回调 props（onNodesChange/onDrop/onConnect/onNodeClick 等）后直接调用，
// 断言"回写载荷不将被保存配置改为默认值"这一核心不变量，比依赖 jsdom 布局/d3-drag 的
// 端到端交互更稳定。命名导出（applyNodeChanges 等）保持真实实现。
//
// 沙箱限制声明：沙箱内 tsc 全量编译超时、vitest 无法运行，本文件作为交付物入库
// （NFR-010/AC-08），请在非沙箱环境执行。
// =============================================================================

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, act } from "@testing-library/react";
import type {
  Node,
  Connection,
  ReactFlowInstance,
  NodeChange,
} from "reactflow";
import type {
  MouseEvent as ReactMouseEvent,
  DragEvent as ReactDragEvent,
  SetStateAction,
} from "react";
import WorkflowCanvas from "../WorkflowCanvas";
import type { WorkflowCanvasState, CanvasNode } from "../canvasTypes";
import type { NodeConfiguration } from "../../workflow-definition/definitionTypes";

/** 捕获的回调 props 形状：仅声明测试驱动所需的回写通道 */
interface CapturedReactFlowProps {
  onNodesChange: (changes: NodeChange[]) => void;
  onDrop: (event: ReactDragEvent) => void;
  onConnect: (connection: Connection) => void;
  onNodeClick: (event: ReactMouseEvent, node: Node) => void;
  onPaneClick: () => void;
  onInit: (instance: ReactFlowInstance) => void;
}

/** 被捕获的 ReactFlow props（模块级，供各用例读取） */
const rfCapture = vi.hoisted(() => ({
  props: null as CapturedReactFlowProps | null,
}));

// 以桩组件替换 ReactFlow 默认导出：渲染时不展开真实画布，仅记录回调 props，
// 便于测试直接驱动 onNodesChange/onDrop/onConnect 等回写通道。
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

// —— 非默认配置夹具：用于验证"回写不得将其改写为默认值" ——
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
const configC: NodeConfiguration = {
  node_type: "tool",
  tool_identifier: "quality_check",
  tool_source: "mcp",
  argument_mapping: { table: "{{table}}" },
  static_arguments: {},
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

/** 构造最小 React Flow 节点引用（仅供 onNodeClick 触发选中） */
function nodeRef(id: string): Node {
  return { id, position: { x: 0, y: 0 }, data: {} };
}

/**
 * 函数式提交适配（CHG-1~4）：以基准状态应用 onCanvasStateChange 捕获的更新
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

/** 渲染受控画布并返回回调 spy */
function renderCanvas(
  state: WorkflowCanvasState,
  selectedNodeId: string | null = null,
) {
  const onStateChange = vi.fn<(update: SetStateAction<WorkflowCanvasState>) => void>();
  const onSelectNode = vi.fn<(nodeId: string | null) => void>();
  render(
    <WorkflowCanvas
      canvasState={state}
      onCanvasStateChange={onStateChange}
      selectedNodeId={selectedNodeId}
      onSelectNode={onSelectNode}
    />,
  );
  return { onStateChange, onSelectNode };
}

/** 注入 ReactFlow 实例桩：供 onDrop 调用 screenToFlowPosition 做坐标转换 */
function primeInstance(): void {
  const instance = {
    screenToFlowPosition: () => ({ x: 100, y: 200 }),
  } as unknown as ReactFlowInstance;
  getProps().onInit(instance);
}

beforeEach(() => {
  rfCapture.props = null;
});

describe("WorkflowCanvas 节点配置持久化", () => {
  it("P-01 切换选中节点仅更新选中态、不回写且不改写既有配置（FR-004/FR-011/AC-01）", () => {
    const state: WorkflowCanvasState = {
      nodes: [
        makeNode("A", "触发器A", configA),
        makeNode("B", "Agent B", configB),
      ],
      edges: [],
    };
    const { onStateChange, onSelectNode } = renderCanvas(state, "A");

    // 选中 A → 选中 B → 切回 A
    getProps().onNodeClick({} as ReactMouseEvent, nodeRef("B"));
    getProps().onNodeClick({} as ReactMouseEvent, nodeRef("A"));

    expect(onSelectNode).toHaveBeenNthCalledWith(1, "B");
    expect(onSelectNode).toHaveBeenNthCalledWith(2, "A");
    // 切换选中不触碰画布状态 → 已保存配置不会被视为变更而丢失
    expect(onStateChange).not.toHaveBeenCalled();
    expect(state.nodes[0].data.configuration).toEqual(configA);
    expect(state.nodes[1].data.configuration).toEqual(configB);
  });

  it("P-02 拖入新节点时既有节点配置逐字段保留（FR-011/FR-012/AC-04）", () => {
    const state: WorkflowCanvasState = {
      nodes: [makeNode("A", "触发器A", configA)],
      edges: [],
    };
    const { onStateChange } = renderCanvas(state);
    primeInstance();

    const dropEvent = {
      preventDefault: vi.fn(),
      dataTransfer: {
        getData: (key: string) =>
          key === "application/node-type" ? "agent" : "",
      },
      clientX: 100,
      clientY: 200,
    } as unknown as ReactDragEvent;
    getProps().onDrop(dropEvent);

    expect(onStateChange).toHaveBeenCalledTimes(1);
    // 函数式提交适配（CHG-2）：以基准状态应用 updater 后断言
    const payload = applyCapturedUpdate(onStateChange.mock.calls, state);
    expect(payload.nodes).toHaveLength(2);
    // 既有节点配置不得被默认值覆盖
    expect(payload.nodes[0].data.configuration).toEqual(configA);
    // 新节点为默认配置的合法节点
    expect(payload.nodes[1].type).toBe("agent");
  });

  it("P-03 位置变更与连线回写均不改写既有配置（FR-012/AC-04）", () => {
    const state: WorkflowCanvasState = {
      nodes: [
        makeNode("A", "触发器A", configA),
        makeNode("B", "Agent B", configB),
      ],
      edges: [],
    };
    const { onStateChange } = renderCanvas(state);

    // 位置变更（受控 onNodesChange）
    getProps().onNodesChange([
      { type: "position", id: "A", position: { x: 120, y: 80 }, dragging: false },
    ]);
    expect(onStateChange).toHaveBeenCalledTimes(1);
    // 函数式提交适配（CHG-3）：以基准状态应用 updater 后断言
    let payload = applyCapturedUpdate(onStateChange.mock.calls, state);
    expect(payload.nodes.find((n) => n.id === "A")?.position).toEqual({
      x: 120,
      y: 80,
    });
    expect(payload.nodes.map((n) => n.data.configuration)).toEqual([
      configA,
      configB,
    ]);

    // 连线（无环，允许建立）：nodes 以唯一源透传，配置不变
    onStateChange.mockClear();
    act(() => {
      getProps().onConnect({
        source: "A",
        target: "B",
        sourceHandle: null,
        targetHandle: null,
      });
    });
    expect(onStateChange).toHaveBeenCalledTimes(1);
    // 函数式提交适配（CHG-4）：以位置变更后的状态为基准应用 updater
    payload = applyCapturedUpdate(onStateChange.mock.calls, payload);
    expect(payload.edges).toHaveLength(1);
    expect(payload.nodes.map((n) => n.data.configuration)).toEqual([
      configA,
      configB,
    ]);
  });

  it("P-04 多节点交替切换与编辑互不串扰（FR-007/AC-03）", () => {
    const state: WorkflowCanvasState = {
      nodes: [
        makeNode("A", "触发器A", configA),
        makeNode("B", "Agent B", configB),
        makeNode("C", "Tool C", configC),
      ],
      edges: [],
    };
    const { onStateChange, onSelectNode } = renderCanvas(state, "A");

    // 任意顺序切换选中：仅更新选中态
    getProps().onNodeClick({} as ReactMouseEvent, nodeRef("B"));
    getProps().onNodeClick({} as ReactMouseEvent, nodeRef("C"));
    getProps().onNodeClick({} as ReactMouseEvent, nodeRef("A"));
    expect(onSelectNode).toHaveBeenCalledTimes(3);
    expect(onStateChange).not.toHaveBeenCalled();

    // 一次位置回写后，三节点配置各自保持、互不覆盖
    getProps().onNodesChange([
      { type: "position", id: "B", position: { x: 10, y: 10 } },
    ]);
    // 函数式提交适配（CHG-3）：以基准状态应用 updater 后断言
    const payload = applyCapturedUpdate(onStateChange.mock.calls, state);
    expect(payload.nodes.map((n) => n.data.configuration)).toEqual([
      configA,
      configB,
      configC,
    ]);
  });

  it("P-05 运行时变更不回写且回写载荷不含运行时字段（NFR-007/C-5）", () => {
    const state: WorkflowCanvasState = {
      nodes: [makeNode("A", "触发器A", configA)],
      edges: [],
    };
    const { onStateChange } = renderCanvas(state, "A");

    // 纯运行时变更（选中/尺寸测量）应被忽略，不产生回写
    getProps().onNodesChange([
      { type: "select", id: "A", selected: true },
      { type: "dimensions", id: "A", dimensions: { width: 100, height: 40 } },
    ]);
    expect(onStateChange).not.toHaveBeenCalled();

    // 持久变更回写时，载荷节点不得携带运行时字段
    getProps().onNodesChange([
      { type: "position", id: "A", position: { x: 5, y: 5 } },
    ]);
    expect(onStateChange).toHaveBeenCalledTimes(1);
    // 函数式提交适配（CHG-3）：以基准状态应用 updater 后断言
    const payload = applyCapturedUpdate(onStateChange.mock.calls, state);
    expect(payload.nodes[0]).not.toHaveProperty("selected");
    expect(payload.nodes[0]).not.toHaveProperty("measured");
    expect(payload.nodes[0]).not.toHaveProperty("positionAbsolute");
    expect(payload.nodes[0]).not.toHaveProperty("dragging");
    // 持久字段与配置原样保留
    expect(payload.nodes[0].data.configuration).toEqual(configA);
  });

  it("P-06 删除选中节点时清理选中态且不影响其余配置（FR-014）", () => {
    const state: WorkflowCanvasState = {
      nodes: [
        makeNode("A", "触发器A", configA),
        makeNode("B", "Agent B", configB),
      ],
      edges: [],
    };
    const { onStateChange, onSelectNode } = renderCanvas(state, "A");

    // 删除当前选中节点 A：清理悬空选中态，其余节点配置保留
    getProps().onNodesChange([{ type: "remove", id: "A" }]);
    expect(onSelectNode).toHaveBeenCalledWith(null);
    expect(onStateChange).toHaveBeenCalledTimes(1);
    // 函数式提交适配（CHG-3）：以基准状态应用 updater 后断言
    const payload = applyCapturedUpdate(onStateChange.mock.calls, state);
    expect(payload.nodes.map((n) => n.id)).toEqual(["B"]);
    expect(payload.nodes[0].data.configuration).toEqual(configB);

    // 删除非选中节点 B：不得误清选中态
    onSelectNode.mockClear();
    onStateChange.mockClear();
    getProps().onNodesChange([{ type: "remove", id: "B" }]);
    expect(onSelectNode).not.toHaveBeenCalled();
    expect(onStateChange).toHaveBeenCalledTimes(1);
  });
});