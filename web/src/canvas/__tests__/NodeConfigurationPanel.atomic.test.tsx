// =============================================================================
// 节点配置面板原子回写回归测试（canvas/__tests__/NodeConfigurationPanel.atomic.test.tsx）
//
// 留待非沙箱环境执行：cd web && npx vitest run
//
// 覆盖用例（fix_canvas_clear_and_example_recurrence / CHG-2，TC-NCP-01~03）：
//   TC-NCP-01 updater 接收最新 prev 而非闭包快照；两节点场景下另一节点不被覆盖（FR-1.1）
//   TC-NCP-02 过期闭包序列下最终状态保留最新节点集（无回退/清空，RG-2）
//   TC-NCP-03 未改动非目标节点的 data.configuration 引用（INV-1）
//
// 测试策略：渲染面板 → 触发配置 onChange → 以受控驱动器应用捕获的 updater 到
// 驱动器最新状态（可注入外部变更模拟"面板 prop 过期但父层已更新"的竞态）。
//
// 沙箱限制声明：沙箱内 tsc 全量编译超时、vitest 无法运行，本文件作为交付物入库，
// 请在非沙箱环境执行。
// =============================================================================

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, screen } from "@testing-library/react";
import type { ReactNode, SetStateAction } from "react";
import NodeConfigurationPanel from "../NodeConfigurationPanel";
import type { CanvasNodeData, CanvasNode, WorkflowCanvasState } from "../canvasTypes";
import type { TriggerNodeConfiguration } from "../../workflow-definition/definitionTypes";

// 主题 mock：保留真实 darkTokens 供 <style> 插值
let mockTheme: "light" | "dark" = "light";
vi.mock("../../theme/themeMode", async () => {
  const actual = await vi.importActual<typeof import("../../theme/themeMode")>(
    "../../theme/themeMode",
  );
  return { ...actual, useThemeMode: () => mockTheme };
});

/** 受控更新形态 */
type StateUpdate = SetStateAction<WorkflowCanvasState>;

/** 受控驱动器：支持外部注入（模拟父层已更新而面板 prop 过期） */
function createDriver(initial: WorkflowCanvasState) {
  let state = initial;
  const onStateChange = vi.fn((update: StateUpdate) => {
    state =
      typeof update === "function"
        ? (update as (prev: WorkflowCanvasState) => WorkflowCanvasState)(state)
        : update;
  });
  const setExternal = (next: WorkflowCanvasState) => {
    state = next;
  };
  return { onStateChange, getState: (): WorkflowCanvasState => state, setExternal };
}

/** 构造 trigger 节点数据 */
function makeTriggerNode(id: string): CanvasNodeData {
  return {
    node_id: id,
    name: id,
    node_type: "trigger",
    configuration: {
      node_type: "trigger",
      trigger_type: "manual",
      input_template: {},
    },
  };
}

/** 构造画布节点 */
function makeCanvasNode(data: CanvasNodeData): CanvasNode {
  return {
    id: data.node_id,
    type: data.node_type,
    position: { x: 0, y: 0 },
    data,
  };
}

/** 渲染面板（selectedNode = nodeA），返回驱动器 */
function renderPanel(nodeA: CanvasNodeData, canvasState: WorkflowCanvasState) {
  const driver = createDriver(canvasState);
  const { container } = render(
    <NodeConfigurationPanel
      selectedNode={nodeA}
      canvasState={canvasState}
      onCanvasStateChange={driver.onStateChange}
    /> as ReactNode,
  );
  return { driver, container };
}

/** 触发选中节点的 trigger_type 切换 */
function changeTriggerType(): void {
  // getByDisplayValue 对 select 匹配选中项文本（非 option.value）
  const select = screen.getByDisplayValue("手动") as HTMLSelectElement;
  fireEvent.change(select, { target: { value: "scheduled" } });
}

beforeEach(() => {
  mockTheme = "light";
});

describe("NodeConfigurationPanel 原子回写", () => {
  // TC-NCP-01（FR-1.1）：updater 基于最新 prev——外部新增节点 C 后触发配置变更，
  // C 不被覆盖丢失。
  it("TC-NCP-01 updater 接收最新 prev，外部新增节点不被覆盖（FR-1.1）", () => {
    const nodeA = makeTriggerNode("A");
    const canvasState: WorkflowCanvasState = {
      nodes: [makeCanvasNode(nodeA)],
      edges: [],
    };
    const { driver } = renderPanel(nodeA, canvasState);

    // 模拟外部新增节点 C：驱动器最新状态比面板 prop 新
    const nodeC = makeTriggerNode("C");
    driver.setExternal({
      nodes: [makeCanvasNode(nodeA), makeCanvasNode(nodeC)],
      edges: [],
    });

    changeTriggerType();

    const next = driver.getState();
    // C 未被覆盖，A 的配置已更新
    expect(next.nodes).toHaveLength(2);
    const updatedA = next.nodes.find((n) => n.id === "A")?.data;
    const cfgA = updatedA?.configuration as TriggerNodeConfiguration;
    expect(cfgA.trigger_type).toBe("scheduled");
  });

  // TC-NCP-02（RG-2）：过期闭包序列下，最终状态保留最新节点集（无回退/清空）。
  it("TC-NCP-02 过期闭包序列下保留最新节点集（RG-2）", () => {
    const nodeA = makeTriggerNode("A");
    const nodeB = makeTriggerNode("B");
    const canvasState: WorkflowCanvasState = {
      nodes: [makeCanvasNode(nodeA), makeCanvasNode(nodeB)],
      edges: [],
    };
    const { driver } = renderPanel(nodeA, canvasState);

    // 父层先提交了包含 C 的最新节点集（面板 prop 仍为 [A,B]，构成过期闭包场景）
    const nodeC = makeTriggerNode("C");
    driver.setExternal({
      nodes: [makeCanvasNode(nodeA), makeCanvasNode(nodeB), makeCanvasNode(nodeC)],
      edges: [],
    });

    changeTriggerType();

    // 函数式回写基于 prev（含 C），不退回闭包快照的 [A,B]
    expect(driver.getState().nodes).toHaveLength(3);
  });

  // TC-NCP-03（INV-1）：非目标节点 C 的 data 引用原样保留。
  it("TC-NCP-03 未改动非目标节点的 data.configuration 引用（INV-1）", () => {
    const nodeA = makeTriggerNode("A");
    const nodeB = makeTriggerNode("B");
    const canvasState: WorkflowCanvasState = {
      nodes: [makeCanvasNode(nodeA), makeCanvasNode(nodeB)],
      edges: [],
    };
    const { driver } = renderPanel(nodeA, canvasState);

    changeTriggerType();

    const next = driver.getState();
    const keptB = next.nodes.find((n) => n.id === "B")?.data;
    // B 的 data 引用与原始一致（未被替换）
    expect(keptB).toBe(nodeB);
  });
});