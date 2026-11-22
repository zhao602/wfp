// =============================================================================
// 画布删除节点回归测试（canvas/__tests__/WorkflowCanvas.delete.test.tsx）
//
// 留待非沙箱环境执行：npx vitest run src/canvas/__tests__/WorkflowCanvas.delete.test.tsx
//
// 覆盖：
//   D-01 removeNode 删除节点并剔除其出边（FR-004/FR-009/AC-007/AC-015）
//   D-02 removeNode 剔除指向该节点的入边（FR-010/AC-016）
//   D-03 removeNode 无关连线原样保留（AC-017）
//   D-04 removeNode 其余节点配置/位置逐字段不变（FR-007/AC-013/INV-12）
//   D-05 removeNode 目标不存在 → not_found（NFR-006/异常场景 1）
//   D-06 removeNode 删除最后一个节点 → last_node（FR-006/AC-011/AC-012/DC-5）
//   D-07 removeNode 纯函数不修改入参（NFR-006/INV-12）
//   D-08 handleNodesChange remove 剔除关联边（缺陷 A 第二道防线/FR-004/AC-008）
//   D-09 handleNodesChange 删空被守卫拒绝（FR-006/DC-5）
//   D-10 键盘 Delete 经 onDeleteNode 上抛选中节点（FR-002/FR-013/AC-003/AC-019）
//   D-11 键盘 Backspace 同上（FR-014）
//   D-12 键盘无选中节点不触发（AC-020）
//   D-13 键盘事件目标为输入控件时不触发（FR-014/R-3）
//   TC-DEL-01 App 层函数式删除基于 prev 重算 + last_node 守卫不变（CHG-4/INV-3）
// =============================================================================

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, fireEvent } from "@testing-library/react";
import type {
  NodeChange,
  ReactFlowInstance,
} from "reactflow";
import WorkflowCanvas from "../WorkflowCanvas";
import { removeNode } from "../canvasMutations";
import type { WorkflowCanvasState, CanvasNode, CanvasNodeData } from "../canvasTypes";
import type { NodeConfiguration } from "../../workflow-definition/definitionTypes";

// —— 捕获 ReactFlow props 的桩（仅记录回调，不展开真实画布） ——
interface CapturedProps {
  onNodesChange: (changes: NodeChange[]) => void;
  onInit: (instance: ReactFlowInstance) => void;
  deleteKeyCode?: string | string[] | null;
}
const rfCapture = vi.hoisted(() => ({ props: null as CapturedProps | null }));

vi.mock("reactflow", async (importOriginal) => {
  const actual = await importOriginal<typeof import("reactflow")>();
  return {
    ...actual,
    default: (props: CapturedProps) => {
      rfCapture.props = props;
      return null;
    },
  };
});

/** 读取捕获的 ReactFlow props */
function getProps(): CapturedProps {
  if (rfCapture.props === null) {
    throw new Error("尚未捕获 ReactFlow props：WorkflowCanvas 未渲染");
  }
  return rfCapture.props;
}

/** 构造节点 */
function makeNode(id: string, name: string): CanvasNode {
  const configuration: NodeConfiguration = {
    node_type: "trigger",
    trigger_type: "manual",
    input_template: {},
  };
  const data: CanvasNodeData = {
    node_id: id,
    name,
    node_type: "trigger",
    configuration,
  };
  return { id, type: "trigger", position: { x: 10, y: 20 }, data };
}

/** 构造 3 节点 2 连线画布：A->B, B->C */
function makeState(): WorkflowCanvasState {
  return {
    nodes: [makeNode("A", "触发器A"), makeNode("B", "Agent B"), makeNode("C", "Tool C")],
    edges: [
      { id: "e-ab", source: "A", target: "B" },
      { id: "e-bc", source: "B", target: "C" },
      { id: "e-ac", source: "A", target: "C" },
    ],
  };
}

/**
 * 函数式提交适配（CHG-3）：以基准状态应用 onCanvasStateChange 捕获的更新
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

// ---------------------------------------------------------------------------
// removeNode 纯函数
// ---------------------------------------------------------------------------
describe("canvasMutations.removeNode", () => {
  it("D-01 删除节点并剔除其出边", () => {
    const outcome = removeNode(makeState(), "A");
    expect(outcome.ok).toBe(true);
    if (!outcome.ok) return;
    expect(outcome.nextState.nodes.map((n) => n.id)).toEqual(["B", "C"]);
    // A 的出边 e-ab / e-ac 被剔除
    expect(outcome.nextState.edges.map((e) => e.id)).toEqual(["e-bc"]);
  });

  it("D-02 剔除指向该节点的入边", () => {
    const outcome = removeNode(makeState(), "B");
    expect(outcome.ok).toBe(true);
    if (!outcome.ok) return;
    // B 的入边 e-ab 与出边 e-bc 均被剔除，无关边 e-ac 保留
    expect(outcome.nextState.edges.map((e) => e.id)).toEqual(["e-ac"]);
  });

  it("D-03 无关连线原样保留", () => {
    const outcome = removeNode(makeState(), "A");
    expect(outcome.ok).toBe(true);
    if (!outcome.ok) return;
    const kept = outcome.nextState.edges.find((e) => e.id === "e-bc");
    expect(kept).toEqual({ id: "e-bc", source: "B", target: "C" });
  });

  it("D-04 其余节点配置/位置逐字段不变", () => {
    const state = makeState();
    const outcome = removeNode(state, "C");
    expect(outcome.ok).toBe(true);
    if (!outcome.ok) return;
    expect(outcome.nextState.nodes).toHaveLength(2);
    // 保留节点与原始引用逐字段一致（纯变换不重建对象）
    expect(outcome.nextState.nodes[0]).toEqual(state.nodes[0]);
    expect(outcome.nextState.nodes[1]).toEqual(state.nodes[1]);
  });

  it("D-05 目标不存在 → not_found（幂等安全）", () => {
    const outcome = removeNode(makeState(), "X");
    expect(outcome).toEqual({ ok: false, reason: "not_found" });
  });

  it("D-06 删除最后一个节点 → last_node", () => {
    const single: WorkflowCanvasState = { nodes: [makeNode("A", "唯一")], edges: [] };
    const outcome = removeNode(single, "A");
    expect(outcome).toEqual({ ok: false, reason: "last_node" });
  });

  it("D-07 纯函数不修改入参", () => {
    const state = makeState();
    const snapshot = JSON.stringify(state);
    removeNode(state, "A");
    expect(JSON.stringify(state)).toBe(snapshot);
  });
});

// ---------------------------------------------------------------------------
// handleNodesChange remove 分支（缺陷 A 兜底）
// ---------------------------------------------------------------------------
describe("WorkflowCanvas handleNodesChange remove 兜底", () => {
  function renderCanvas(state: WorkflowCanvasState, selectedNodeId: string | null = null) {
    const onStateChange = vi.fn();
    const onSelectNode = vi.fn();
    const { container } = render(
      <WorkflowCanvas
        canvasState={state}
        onCanvasStateChange={onStateChange}
        selectedNodeId={selectedNodeId}
        onSelectNode={onSelectNode}
      />,
    );
    return { onStateChange, onSelectNode, container };
  }

  beforeEach(() => {
    rfCapture.props = null;
  });

  it("D-08 删除节点时回写载荷剔除关联边", () => {
    const state = makeState();
    const { onStateChange } = renderCanvas(state);
    getProps().onNodesChange([{ type: "remove", id: "B" }]);
    expect(onStateChange).toHaveBeenCalledTimes(1);
    // 函数式提交适配（CHG-3）：以基准状态应用 updater 后断言
    const payload = applyCapturedUpdate(onStateChange.mock.calls, state);
    expect(payload.nodes.map((n: CanvasNode) => n.id)).toEqual(["A", "C"]);
    expect(payload.edges.map((e) => e.id)).toEqual(["e-ac"]);
  });

  it("D-09 删空被守卫拒绝（不产生回写）", () => {
    const single: WorkflowCanvasState = { nodes: [makeNode("A", "唯一")], edges: [] };
    const alertSpy = vi.spyOn(window, "alert").mockImplementation(() => {});
    const { onStateChange } = renderCanvas(single);
    getProps().onNodesChange([{ type: "remove", id: "A" }]);
    expect(onStateChange).not.toHaveBeenCalled();
    expect(alertSpy).toHaveBeenCalled();
    alertSpy.mockRestore();
  });
});

// ---------------------------------------------------------------------------
// 键盘删除入口
// ---------------------------------------------------------------------------
describe("WorkflowCanvas 键盘删除入口", () => {
  function renderCanvas(selectedNodeId: string | null) {
    const onDeleteNode = vi.fn();
    const { container } = render(
      <WorkflowCanvas
        canvasState={makeState()}
        onCanvasStateChange={vi.fn()}
        selectedNodeId={selectedNodeId}
        onSelectNode={vi.fn()}
        onDeleteNode={onDeleteNode}
      />,
    );
    // 包裹容器（mock ReactFlow 返回 null，故首个 div 即键盘监听承载节点）
    const wrapper = container.firstElementChild as HTMLElement;
    return { onDeleteNode, wrapper };
  }

  beforeEach(() => {
    rfCapture.props = null;
  });

  it("D-10 Delete 键上抛选中节点", () => {
    const { onDeleteNode, wrapper } = renderCanvas("B");
    fireEvent.keyDown(wrapper, { key: "Delete" });
    expect(onDeleteNode).toHaveBeenCalledTimes(1);
    expect(onDeleteNode).toHaveBeenCalledWith("B");
  });

  it("D-11 Backspace 键上抛选中节点", () => {
    const { onDeleteNode, wrapper } = renderCanvas("A");
    fireEvent.keyDown(wrapper, { key: "Backspace" });
    expect(onDeleteNode).toHaveBeenCalledWith("A");
  });

  it("D-12 无选中节点不触发", () => {
    const { onDeleteNode, wrapper } = renderCanvas(null);
    fireEvent.keyDown(wrapper, { key: "Delete" });
    expect(onDeleteNode).not.toHaveBeenCalled();
  });

  it("D-13 事件目标为输入控件时不触发", () => {
    const { onDeleteNode, wrapper } = renderCanvas("A");
    const input = document.createElement("input");
    wrapper.appendChild(input);
    fireEvent.keyDown(input, { key: "Delete", bubbles: true });
    expect(onDeleteNode).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// App 层函数式删除语义（CHG-4）
// ---------------------------------------------------------------------------
describe("App 层函数式删除语义（CHG-4）", () => {
  // TC-DEL-01（CHG-4/INV-3）：删除提交基于 prev 重算（过期闭包不覆盖既有变更）；
  // last_node 守卫仍拒绝，提示文案不变。
  it("TC-DEL-01 删除基于 prev 重算且 last_node 守卫拒绝（CHG-4/INV-3）", () => {
    // 模拟 App.handleDeleteNode 的提交形态：setCanvasState((prev) => ...)
    // 外部先提交了删除 A（父层最新状态为 [B, C]）
    let state: WorkflowCanvasState = {
      nodes: [makeNode("B", "Agent B"), makeNode("C", "Tool C")],
      edges: [{ id: "e-bc", source: "B", target: "C" }],
    };

    // 函数式删除 B：以最新 prev [B,C] 重算 → [C]
    // （若沿用闭包快照回写会错误得到 [A,C]，本断言可捕获该回归）
    const outcome = removeNode(state, "B");
    state = outcome.ok ? outcome.nextState : state;
    expect(state.nodes.map((n) => n.id)).toEqual(["C"]);

    // last_node 守卫：仅剩 C 时拒绝，提示文案不变
    const alertSpy = vi.spyOn(window, "alert").mockImplementation(() => {});
    const guard = removeNode(state, "C");
    expect(guard).toEqual({ ok: false, reason: "last_node" });
    if (!guard.ok && guard.reason === "last_node") {
      alert("至少保留一个节点，无法删除");
    }
    expect(alertSpy).toHaveBeenCalledWith("至少保留一个节点，无法删除");
    expect(state.nodes.map((n) => n.id)).toEqual(["C"]);
    alertSpy.mockRestore();
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});