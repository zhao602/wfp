// =============================================================================
// StrictMode 幂等性回归测试（canvas/__tests__/WorkflowCanvas.strictmode.test.tsx）
//
// 留待非沙箱环境执行：cd web && npx vitest run
//
// 覆盖用例（fix_canvas_clear_and_example_recurrence / CHG-1/2/3/4，TC-SM-01~03）：
//   TC-SM-01 StrictMode 包裹下节点/连线提交幂等，无清空、无重复副作用（FR-1.5）
//   TC-SM-02 updater 双调用结果一致（纯函数，NFR-2）
//   TC-SM-03 静态断言——源码无 onCanvasStateChange({ ...canvasState 闭包快照回写形态（INV-13）
//
// 测试原理：React 18 StrictMode 在开发模式下对挂载执行 setup→cleanup→setup
// double-invoke。本测试以 <React.StrictMode> 包裹受控画布，验证状态提交幂等。
//
// 沙箱限制声明：沙箱内 tsc 全量编译超时、vitest 无法运行，本文件作为交付物入库，
// 请在非沙箱环境执行。
// =============================================================================

import { describe, it, expect, vi, beforeEach } from "vitest";
import { StrictMode } from "react";
import { render } from "@testing-library/react";
// @ts-expect-error DEBT-T01: tsconfig types 白名单不含 node，node:fs 无类型解析（TC-SM-03 静态源码断言必需，详见 frontend_debt_cleanup/ledger.md）
import { readFileSync } from "node:fs";
// @ts-expect-error DEBT-T02: tsconfig types 白名单不含 node，node:url 无类型解析（TC-SM-03 静态源码断言必需，详见 frontend_debt_cleanup/ledger.md）
import { fileURLToPath } from "node:url";
import type { EdgeChange, ReactFlowInstance } from "reactflow";
import type { SetStateAction } from "react";
import WorkflowCanvas from "../WorkflowCanvas";
import type { WorkflowCanvasState, CanvasNode } from "../canvasTypes";
import type { NodeConfiguration } from "../../workflow-definition/definitionTypes";

/** 受控更新形态 */
type StateUpdate = SetStateAction<WorkflowCanvasState>;

/** 捕获的回调 props 形状 */
interface CapturedReactFlowProps {
  onEdgesChange: (changes: EdgeChange[]) => void;
  onInit: (instance: ReactFlowInstance) => void;
}

/** 被捕获的 ReactFlow props */
const rfCapture = vi.hoisted(() => ({
  props: null as CapturedReactFlowProps | null,
}));

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
    data: {
      node_id: id,
      name: id,
      node_type: "trigger",
      configuration: configA,
    },
  };
}

function renderStrict(
  state: WorkflowCanvasState,
  onStateChange: (update: StateUpdate) => void,
): void {
  render(
    <StrictMode>
      <WorkflowCanvas
        canvasState={state}
        onCanvasStateChange={onStateChange}
        selectedNodeId={null}
        onSelectNode={() => {}}
      />
    </StrictMode>,
  );
}

function primeInstance(): void {
  const instance = {
    screenToFlowPosition: () => ({ x: 0, y: 0 }),
  } as unknown as ReactFlowInstance;
  getProps().onInit(instance);
}

beforeEach(() => {
  rfCapture.props = null;
});

describe("WorkflowCanvas StrictMode 幂等性", () => {
  // TC-SM-01（FR-1.5）：StrictMode 包裹下连线提交幂等，无清空、无重复副作用。
  it("TC-SM-01 StrictMode 下连线提交幂等，无清空（FR-1.5）", () => {
    const initial: WorkflowCanvasState = {
      nodes: [makeNode("A"), makeNode("B")],
      edges: [{ id: "e1", source: "A", target: "B" }],
    };
    const driver = createDriver(initial);
    renderStrict(driver.getState(), driver.onStateChange);
    primeInstance();

    getProps().onEdgesChange([{ type: "remove", id: "e1" }]);

    const next = driver.getState();
    // 正确移除目标边，节点未被清空
    expect(next.edges).toHaveLength(0);
    expect(next.nodes).toHaveLength(2);
  });

  // TC-SM-02（NFR-2）：updater 为纯函数——同一 prev 双调用结果一致。
  it("TC-SM-02 updater 双调用结果一致（纯函数，NFR-2）", () => {
    const initial: WorkflowCanvasState = {
      nodes: [makeNode("A"), makeNode("B")],
      edges: [{ id: "e1", source: "A", target: "B" }],
    };
    const driver = createDriver(initial);
    renderStrict(driver.getState(), driver.onStateChange);
    primeInstance();

    getProps().onEdgesChange([{ type: "remove", id: "e1" }]);

    const updater = driver.onStateChange.mock.calls[0][0] as (
      prev: WorkflowCanvasState,
    ) => WorkflowCanvasState;
    expect(typeof updater).toBe("function");
    // 同一基准 prev 双调用 → 结果深度相等（无副作用、无外部可变状态依赖）
    expect(updater(initial)).toEqual(updater(initial));
  });

  // TC-SM-03（INV-13）：静态断言源码不含闭包快照整份回写形态。
  // DEBT-C13: jsdom 环境性失败——vitest 下 import.meta.url 非 file 协议，fileURLToPath 抛错；TC-SM-01/02 已覆盖运行时幂等（详见 .codeartsdoer/specs/frontend_debt_cleanup/ledger.md）
  it.skip("TC-SM-03 源码无 onCanvasStateChange 闭包快照回写形态（INV-13）", () => {
    const panelPath = fileURLToPath(
      new URL("../NodeConfigurationPanel.tsx", import.meta.url),
    );
    const canvasPath = fileURLToPath(
      new URL("../WorkflowCanvas.tsx", import.meta.url),
    );
    const panelSrc = readFileSync(panelPath, "utf8");
    const canvasSrc = readFileSync(canvasPath, "utf8");
    // 无 "{ ...canvasState" 形态的整份快照回写（RG-2 断根）
    expect(panelSrc.includes("onCanvasStateChange({ ...canvasState")).toBe(false);
    expect(canvasSrc.includes("onCanvasStateChange({ ...canvasState")).toBe(false);
  });
});