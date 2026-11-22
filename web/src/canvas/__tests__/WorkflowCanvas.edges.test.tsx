// =============================================================================
// 画布连线双状态源消除回归测试（canvas/__tests__/WorkflowCanvas.edges.test.tsx）
//
// 留待非沙箱环境执行：cd web && npx vitest run
//
// 覆盖用例（fix_canvas_clear_and_example_recurrence / CHG-1，TC-EDGE-01~04）：
//   TC-EDGE-01 投递 remove 变更 → 父层 edges 移除该边、nodes 不变（FR-1.1）
//   TC-EDGE-02 投递 reset 变更 → 父层 edges 被正确替换而非不可自愈地清空（FR-1.4）
//   TC-EDGE-03 渲染 edges 与父层 canvasState.edges 受控直通（FR-2.2）
//   TC-EDGE-04 onEdgesChange 回写不修改 nodes、不污染持久结构（INV-1）
//
// 测试策略：沿用 WorkflowCanvas.race.test.tsx 的 vi.mock 桩模式，额外捕获
// onEdgesChange 通道；以受控驱动器承接 onCanvasStateChange 的函数式提交。
//
// 沙箱限制声明：沙箱内 tsc 全量编译超时、vitest 无法运行，本文件作为交付物入库，
// 请在非沙箱环境执行。
// =============================================================================

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render } from "@testing-library/react";
import type {
  Edge,
  EdgeChange,
  ReactFlowInstance,
} from "reactflow";
import type { SetStateAction } from "react";
import WorkflowCanvas from "../WorkflowCanvas";
import type { WorkflowCanvasState, CanvasNode } from "../canvasTypes";
import type { NodeConfiguration } from "../../workflow-definition/definitionTypes";

/** 受控驱动器回调形态：与 CHG-1 放宽后的 onCanvasStateChange 契约一致 */
type StateUpdate = SetStateAction<WorkflowCanvasState>;

/** 捕获的回调 props 形状：仅声明测试驱动所需的通道 */
interface CapturedReactFlowProps {
  edges: Edge[];
  onEdgesChange: (changes: EdgeChange[]) => void;
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

/** 渲染受控画布：状态应用走受控驱动器 */
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

/** 注入 ReactFlow 实例桩 */
function primeInstance(): void {
  const instance = {
    screenToFlowPosition: () => ({ x: 0, y: 0 }),
  } as unknown as ReactFlowInstance;
  getProps().onInit(instance);
}

beforeEach(() => {
  rfCapture.props = null;
});

describe("WorkflowCanvas 连线双状态源消除", () => {
  // TC-EDGE-01（FR-1.1）：remove 变更经函数式回写后，父层 edges 移除该边，nodes 不变。
  it("TC-EDGE-01 投递 remove 变更 → 父层移除该边且 nodes 不变（FR-1.1）", () => {
    const initial: WorkflowCanvasState = {
      nodes: [makeNode("A", configA), makeNode("B", configA)],
      edges: [makeEdge("e1", "A", "B")],
    };
    const driver = createDriver(initial);
    renderCanvas(driver.getState(), driver.onStateChange);
    primeInstance();

    getProps().onEdgesChange([{ type: "remove", id: "e1" }]);

    const next = driver.getState();
    expect(next.edges).toHaveLength(0);
    expect(next.nodes).toHaveLength(2);
  });

  // TC-EDGE-02（FR-1.4）：库层受控替换投递的 reset 变更被父层正确接住，
  // 父层 edges 被替换而非不可自愈地清空。
  it("TC-EDGE-02 投递 reset 变更 → 父层 edges 被正确替换（FR-1.4）", () => {
    const initial: WorkflowCanvasState = {
      nodes: [makeNode("A", configA), makeNode("B", configA)],
      edges: [makeEdge("e1", "A", "B")],
    };
    const driver = createDriver(initial);
    renderCanvas(driver.getState(), driver.onStateChange);
    primeInstance();

    const replacement = makeEdge("e2", "B", "A");
    getProps().onEdgesChange([{ type: "reset", item: replacement }]);

    const next = driver.getState();
    expect(next.edges).toHaveLength(1);
    expect(next.edges[0].id).toBe("e2");
  });

  // TC-EDGE-03（FR-2.2）：渲染的 edges 受控直通父层 canvasState.edges，
  // 不存在"本地清空、父层有值却不渲染"的不可自愈窗口。
  it("TC-EDGE-03 渲染 edges 与父层 canvasState.edges 受控直通（FR-2.2）", () => {
    const initial: WorkflowCanvasState = {
      nodes: [makeNode("A", configA), makeNode("B", configA)],
      edges: [makeEdge("e1", "A", "B")],
    };
    const driver = createDriver(initial);
    renderCanvas(driver.getState(), driver.onStateChange);

    // 受控直通：传入的 edges 引用即渲染所用引用，无本地镜像
    expect(getProps().edges).toBe(initial.edges);
  });

  // TC-EDGE-04（INV-1）：onEdgesChange 回写不修改 nodes、不污染持久结构。
  it("TC-EDGE-04 onEdgesChange 回写不修改 nodes（INV-1）", () => {
    const nodes = [makeNode("A", configA), makeNode("B", configA)];
    const initial: WorkflowCanvasState = {
      nodes,
      edges: [makeEdge("e1", "A", "B"), makeEdge("e2", "B", "A")],
    };
    const driver = createDriver(initial);
    renderCanvas(driver.getState(), driver.onStateChange);
    primeInstance();

    getProps().onEdgesChange([{ type: "remove", id: "e1" }]);

    const next = driver.getState();
    // nodes 引用原样透传（prev.nodes），未被替换
    expect(next.nodes).toBe(nodes);
  });
});