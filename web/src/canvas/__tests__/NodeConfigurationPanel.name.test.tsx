// =============================================================================
// 节点名称编辑集成与端到端测试（canvas/__tests__/NodeConfigurationPanel.name.test.tsx）
//
// 留待非沙箱环境执行：cd web && npx vitest run NodeConfigurationPanel.name
//
// 覆盖用例（node_name_edit / DC-1~DC-7，TC-NE-01~08）：
//   TC-NE-01 updateName 函数式回写：仅替换目标节点 data.name，非目标节点引用不变，edges 透传
//   TC-NE-02 commitName 合法分支：committedNameRef 更新，无 validationError
//   TC-NE-03 commitName 非法分支：validationError 设置，updateName 回滚回写
//   TC-NE-04 StrictMode 双调用幂等：updater 双调用无副作用累积
//   TC-NE-05 键入字符 → 画布标题同帧更新（onChange 逐字符回写）
//   TC-NE-06 清空后失焦 → 回滚至 committedName，提示"节点名称不能为空"
//   TC-NE-07 输入 33 字符后回车 → 回滚，提示"节点名称不能超过 32 个字符"
//   TC-NE-08 切换选中节点 → committedName 重置，提示清除
//
// 沙箱限制声明：沙箱内 tsc 全量编译超时、vitest 无法运行，本文件作为交付物入库，
// 请在非沙箱环境执行。
// =============================================================================

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, screen } from "@testing-library/react";
import { StrictMode } from "react";
import type { SetStateAction } from "react";
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

/** 受控驱动器：捕获 onCanvasStateChange 调用并以函数式 updater 更新内部状态 */
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

/** 构造 trigger 节点数据 */
function makeTriggerNode(id: string, name?: string): CanvasNodeData {
  return {
    node_id: id,
    name: name ?? id,
    node_type: "trigger",
    configuration: {
      node_type: "trigger",
      trigger_type: "manual",
      input_template: {},
    } as TriggerNodeConfiguration,
  };
}

/** 构造画布状态 */
function makeCanvasState(nodes: CanvasNodeData[]): WorkflowCanvasState {
  return {
    nodes: nodes.map((data) => ({
      id: data.node_id,
      type: "default",
      position: { x: 0, y: 0 },
      data,
    })) as CanvasNode[],
    edges: [],
  };
}

/** 查找名称输入框 */
function getNameInput(): HTMLInputElement {
  const input = document.querySelector<HTMLInputElement>(
    'input[aria-describedby="wfp-ncp-hint-common-name"]',
  );
  if (!input) {
    throw new Error("未找到节点名称输入框");
  }
  return input;
}

describe("NodeConfigurationPanel — 节点名称编辑", () => {
  beforeEach(() => {
    mockTheme = "light";
  });

  // TC-NE-01 updateName 函数式回写
  describe("TC-NE-01 updateName 函数式回写", () => {
    it("仅替换目标节点 data.name，非目标节点引用不变，edges 透传", () => {
      const nodeA = makeTriggerNode("node-a", "原名称A");
      const nodeB = makeTriggerNode("node-b", "原名称B");
      const initial = makeCanvasState([nodeA, nodeB]);
      const driver = createDriver(initial);

      render(
        <NodeConfigurationPanel
          selectedNode={nodeA}
          canvasState={driver.getState()}
          onCanvasStateChange={driver.onStateChange}
        />,
      );

      const input = getNameInput();
      fireEvent.change(input, { target: { value: "新名称A" } });

      expect(driver.onStateChange).toHaveBeenCalledTimes(1);
      const next = driver.getState();
      expect(next.nodes[0].data.name).toBe("新名称A");
      expect(next.nodes[1].data.name).toBe("原名称B");
      expect(next.edges).toEqual([]);
    });
  });

  // TC-NE-02 + TC-NE-03 commitName 合法/非法分支
  describe("TC-NE-02/03 commitName 提交校验", () => {
    it("合法名称 blur 后无提示", () => {
      const node = makeTriggerNode("node-1", "合法名称");
      const initial = makeCanvasState([node]);
      const driver = createDriver(initial);

      render(
        <NodeConfigurationPanel
          selectedNode={node}
          canvasState={driver.getState()}
          onCanvasStateChange={driver.onStateChange}
        />,
      );

      const input = getNameInput();
      fireEvent.change(input, { target: { value: "新合法名称" } });
      fireEvent.blur(input);

      // 合法时不应出现"节点名称不能为空"提示
      expect(screen.queryByText("节点名称不能为空")).toBeNull();
    });

    it("清空后 blur → 回滚 + 提示非空", () => {
      const node = makeTriggerNode("node-1", "原名称");
      const initial = makeCanvasState([node]);
      const driver = createDriver(initial);

      const { rerender } = render(
        <NodeConfigurationPanel
          selectedNode={node}
          canvasState={driver.getState()}
          onCanvasStateChange={driver.onStateChange}
        />,
      );

      const input = getNameInput();
      fireEvent.change(input, { target: { value: "" } });
      rerender(
        <NodeConfigurationPanel
          selectedNode={driver.getState().nodes[0].data}
          canvasState={driver.getState()}
          onCanvasStateChange={driver.onStateChange}
        />,
      );
      fireEvent.blur(input);

      // 应显示非空提示
      expect(screen.getByText("节点名称不能为空")).toBeTruthy();
      // 画布状态应回滚至原名称
      expect(driver.getState().nodes[0].data.name).toBe("原名称");
    });

    it("33 字符后 Enter → 回滚 + 提示超长", () => {
      const node = makeTriggerNode("node-1", "原名称");
      const initial = makeCanvasState([node]);
      const driver = createDriver(initial);

      const { rerender } = render(
        <NodeConfigurationPanel
          selectedNode={node}
          canvasState={driver.getState()}
          onCanvasStateChange={driver.onStateChange}
        />,
      );

      const input = getNameInput();
      const longName = "a".repeat(33);
      fireEvent.change(input, { target: { value: longName } });
      rerender(
        <NodeConfigurationPanel
          selectedNode={driver.getState().nodes[0].data}
          canvasState={driver.getState()}
          onCanvasStateChange={driver.onStateChange}
        />,
      );
      fireEvent.keyDown(input, { key: "Enter" });

      expect(screen.getByText("节点名称不能超过 32 个字符")).toBeTruthy();
      expect(driver.getState().nodes[0].data.name).toBe("原名称");
    });
  });

  // TC-NE-04 StrictMode 双调用幂等
  describe("TC-NE-04 StrictMode 幂等", () => {
    it("StrictMode 下 onChange 双调用无副作用累积", () => {
      const node = makeTriggerNode("node-1", "原名称");
      const initial = makeCanvasState([node]);
      const driver = createDriver(initial);

      render(
        <StrictMode>
          <NodeConfigurationPanel
            selectedNode={node}
            canvasState={driver.getState()}
            onCanvasStateChange={driver.onStateChange}
          />
        </StrictMode>,
      );

      const input = getNameInput();
      fireEvent.change(input, { target: { value: "新名称" } });

      const next = driver.getState();
      expect(next.nodes[0].data.name).toBe("新名称");
    });
  });

  // TC-NE-05 逐字符实时同步
  describe("TC-NE-05 逐字符实时同步", () => {
    it("键入每个字符触发 onCanvasStateChange", () => {
      const node = makeTriggerNode("node-1", "A");
      const initial = makeCanvasState([node]);
      const driver = createDriver(initial);

      render(
        <NodeConfigurationPanel
          selectedNode={node}
          canvasState={driver.getState()}
          onCanvasStateChange={driver.onStateChange}
        />,
      );

      const input = getNameInput();
      fireEvent.change(input, { target: { value: "AB" } });
      fireEvent.change(input, { target: { value: "ABC" } });

      expect(driver.getState().nodes[0].data.name).toBe("ABC");
    });
  });

  // TC-NE-08 切换选中节点重置
  describe("TC-NE-08 切换选中节点", () => {
    it("切换节点后提示清除", () => {
      const nodeA = makeTriggerNode("node-a", "节点A");
      const nodeB = makeTriggerNode("node-b", "节点B");
      const initial = makeCanvasState([nodeA, nodeB]);
      const driver = createDriver(initial);

      const { rerender } = render(
        <NodeConfigurationPanel
          selectedNode={nodeA}
          canvasState={driver.getState()}
          onCanvasStateChange={driver.onStateChange}
        />,
      );

      // 先在 nodeA 触发非法校验产生提示
      const input = getNameInput();
      fireEvent.change(input, { target: { value: "" } });
      rerender(
        <NodeConfigurationPanel
          selectedNode={driver.getState().nodes[0].data}
          canvasState={driver.getState()}
          onCanvasStateChange={driver.onStateChange}
        />,
      );
      fireEvent.blur(input);
      expect(screen.getByText("节点名称不能为空")).toBeTruthy();

      // 切换到 nodeB → 提示应清除
      rerender(
        <NodeConfigurationPanel
          selectedNode={nodeB}
          canvasState={driver.getState()}
          onCanvasStateChange={driver.onStateChange}
        />,
      );

      expect(screen.queryByText("节点名称不能为空")).toBeNull();
    });
  });
});