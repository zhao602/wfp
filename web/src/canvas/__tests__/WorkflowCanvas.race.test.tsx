// =============================================================================
// 画布竞态回归测试（canvas/__tests__/WorkflowCanvas.race.test.tsx）
//
// 留待非沙箱环境执行：cd web && npx vitest run
//
// 覆盖用例（fix-canvas-drag-clear-and-edge-loop-bugs / T11，TC-RACE-01~03）：
//   TC-RACE-01 同一渲染批次内连续两次 onDrop → 全部变更保留（FR-1.3）
//   TC-RACE-02 连续高频 position 变更 → 既有节点/连线不丢失（FR-1.1）
//   TC-RACE-03 onDrop 与 onNodesChange 交错 → 全部变更保留（FR-1.2/FR-1.3）
//
// 测试策略：以 vi.mock 捕获 ReactFlow 回调 props 后直接驱动；onCanvasStateChange
// 以受控驱动器承接——捕获 updater（或传值）后应用在最新状态上，对齐 React 受控
// 批处理语义。旧实现的"闭包快照整份回写"在此驱动器下会暴露为变更丢失（竞态检测
// 的测试原理）；函数式提交（CHG-1~3）后 updater 链保证变更原子保留。
//
// 沙箱限制声明：沙箱内 tsc 全量编译超时、vitest 无法运行，本文件作为交付物入库，
// 请在非沙箱环境执行。
// =============================================================================

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render } from "@testing-library/react";
import type {
  Connection,
  ReactFlowInstance,
  NodeChange,
} from "reactflow";
import type { DragEvent as ReactDragEvent, SetStateAction } from "react";
import WorkflowCanvas from "../WorkflowCanvas";
import type { WorkflowCanvasState, CanvasNode } from "../canvasTypes";
import type { NodeConfiguration } from "../../workflow-definition/definitionTypes";

/** 受控驱动器回调形态：与 CHG-1 放宽后的 onCanvasStateChange 契约一致 */
type StateUpdate = SetStateAction<WorkflowCanvasState>;

/** 捕获的回调 props 形状：仅声明测试驱动所需的通道 */
interface CapturedReactFlowProps {
  onNodesChange: (changes: NodeChange[]) => void;
  onDrop: (event: ReactDragEvent) => void;
  onConnect: (connection: Connection) => void;
  onInit: (instance: ReactFlowInstance) => void;
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

/**
 * 受控更新驱动器：承接 onCanvasStateChange 的两种形态（updater/传值），以
 * "应用在最新状态上"模拟 React 受控批处理语义——updater 恒以最新 prev 计算，
 * 旧实现的闭包快照回写在此驱动器下会暴露为变更丢失。
 */
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

/** 渲染受控画布：状态应用走受控驱动器（不依赖 rerender，聚焦提交语义） */
function renderCanvas(
  state: WorkflowCanvasState,
  onStateChange: (update: StateUpdate) => void,
): void {
  render(
    <WorkflowCanvas
      canvasState={state}
      onCanvasStateChange={onStateChange}
      selectedNodeId={null}
      onSelectNode={() => {}}
    />,
  );
}

/** 注入 ReactFlow 实例桩：供 onDrop 调用 screenToFlowPosition 做坐标转换 */
function primeInstance(): void {
  const instance = {
    screenToFlowPosition: () => ({ x: 100, y: 200 }),
  } as unknown as ReactFlowInstance;
  getProps().onInit(instance);
}

/** 构造合法类型拖拽释放事件 */
function makeDropEvent(): ReactDragEvent {
  return {
    preventDefault: vi.fn(),
    dataTransfer: {
      getData: (key: string) =>
        key === "application/node-type" ? "agent" : "",
    },
    clientX: 100,
    clientY: 200,
  } as unknown as ReactDragEvent;
}

beforeEach(() => {
  rfCapture.props = null;
});

describe("WorkflowCanvas 竞态回归", () => {
  // TC-RACE-01（FR-1.3）：同一渲染批次内连续两次 onDrop → 最终节点数为 2，无变更丢失。
  // 模拟过期闭包提交序列：两次提交间不重渲染（闭包快照恒为初始空集），
  // 旧实现第二次整份回写会覆盖第一次（节点数 1）；函数式提交后 updater 链保留全部变更。
  it("TC-RACE-01 同一渲染批次内连续两次 onDrop 全部变更保留（FR-1.3）", () => {
    const driver = createDriver({ nodes: [], edges: [] });
    renderCanvas(driver.getState(), driver.onStateChange);
    primeInstance();

    const props = getProps();
    props.onDrop(makeDropEvent());
    props.onDrop(makeDropEvent());

    // 两次放置均为原子追加：最终节点数为 2，无变更丢失
    expect(driver.getState().nodes).toHaveLength(2);
    expect(driver.getState().edges).toHaveLength(0);
  });

  // TC-RACE-02（FR-1.1）：连续高频 position 变更 → 既有节点/连线数量与内容不丢失。
  // 模拟拖拽 mousemove 每帧多次触发的 position 高频路径。
  it("TC-RACE-02 连续高频 position 变更不丢失既有节点与连线（FR-1.1）", () => {
    const initial: WorkflowCanvasState = {
      nodes: [makeNode("A", configA), makeNode("B", configA)],
      edges: [{ id: "e1", source: "A", target: "B" }],
    };
    const driver = createDriver(initial);
    renderCanvas(driver.getState(), driver.onStateChange);
    const props = getProps();

    for (let frame = 0; frame < 50; frame++) {
      props.onNodesChange([
        {
          type: "position",
          id: "B",
          position: { x: frame, y: frame },
          dragging: true,
        },
      ]);
    }

    const finalState = driver.getState();
    expect(finalState.nodes).toHaveLength(2);
    expect(finalState.edges).toHaveLength(1);
    // 最后一次位置生效（updater 链顺序应用）
    const nodeB = finalState.nodes.find((n) => n.id === "B");
    expect(nodeB?.position).toEqual({ x: 49, y: 49 });
  });

  // TC-RACE-03（FR-1.2/FR-1.3）：放置期间并发触发变更提交（onDrop 与
  // onNodesChange 交错）→ 最终状态包含全部变更结果。
  it("TC-RACE-03 onDrop 与 onNodesChange 交错提交全部变更保留（FR-1.2/FR-1.3）", () => {
    const initial: WorkflowCanvasState = {
      nodes: [makeNode("A", configA)],
      edges: [],
    };
    const driver = createDriver(initial);
    renderCanvas(driver.getState(), driver.onStateChange);
    primeInstance();
    const props = getProps();

    // 交错序列：放置 C → 移动 A → 放置 D → 再移动 A
    props.onDrop(makeDropEvent());
    props.onNodesChange([
      { type: "position", id: "A", position: { x: 10, y: 10 } },
    ]);
    props.onDrop(makeDropEvent());
    props.onNodesChange([
      { type: "position", id: "A", position: { x: 20, y: 20 } },
    ]);

    const finalState = driver.getState();
    expect(finalState.nodes).toHaveLength(3);
    const nodeA = finalState.nodes.find((n) => n.id === "A");
    expect(nodeA?.position).toEqual({ x: 20, y: 20 });
  });
});