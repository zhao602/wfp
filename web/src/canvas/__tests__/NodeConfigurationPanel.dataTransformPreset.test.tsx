// =============================================================================
// 数据转换预制表达式表单行为测试（canvas/__tests__/NodeConfigurationPanel.dataTransformPreset.test.tsx）
//
// 执行：cd web && npx vitest run src/canvas/__tests__/NodeConfigurationPanel.dataTransformPreset.test.tsx
//
// 覆盖用例（data_transform_preset_expressions / CHG-F03，design §8.1-B T-B1~T-B8）：
//   T-B1 新建填充呈现：{operation:"map", params:{map_expression:"True"}} → 映射表达式框值 True（FR-03/AC-03）
//   T-B2 切换补空（UI 触发）：空 params 节点 select 切"过滤" → 过滤条件框 True（FR-04/AC-04）
//   T-B3 切换不覆盖（UI 触发）：改 map 值 → 切 filter → 切回 map → 原值保留（FR-04/FR-06）
//   T-B4 加载零填充：{operation:"map", params:{}} 渲染 → 框为空且 onChange 未被调用（FR-05/AC-05）
//   T-B5 加载非空不改写：params.map_expression="自定义值" → 原样显示、未改写（FR-05）
//   T-B6 恢复默认入口可用性：值≠预制值可用；值=预制值 disabled、点击无变化不报错（FR-07/DC-05）
//   T-B7 恢复默认点击：值 "x == 1" 点击 → 回写 map_expression:"True"、按钮转 disabled（FR-07/AC-07）
//   T-B8 重渲染幂等：多次 rerender 同一 config → 字段值不变、不触发回写（FR-06）
//
// 测试策略：受控驱动器（atomic 同款）承接函数式 onCanvasStateChange；UI 触发走
// fireEvent 经面板 → DataTransformConfigForm → buildOperationChange 全链路；断言
// 混合 DOM 与驱动器状态（配置回写载荷）。受控组件语义下每次 UI 触发后先 rerender
// 再断言/续操作，模拟父层同步应用更新后的最新 props。
//
// 查询说明：Field 的 <label> 内含提示 span，dom-testing-library 的 label 文本匹配
// 会并入提示文案（getLabelContent 递归非控件子节点文本），getByLabelText 精确
// 匹配不可用；沿用 hints 测试的 container.querySelector 惯例（map/filter 视图下
// select/textarea/恢复按钮各唯一，已由 DOM 结构核实）。
//
// fixture 约束：一律使用单 operation + params 的合法 DataTransformNodeConfiguration。
// =============================================================================

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent } from "@testing-library/react";
import type { SetStateAction } from "react";
import NodeConfigurationPanel from "../NodeConfigurationPanel";
import type { CanvasNodeData, CanvasNode, WorkflowCanvasState } from "../canvasTypes";
import { isDataTransformConfig } from "../../workflow-definition/definitionTypes";
import type {
  DataTransformNodeConfiguration,
  DataTransformOperationParams,
} from "../../workflow-definition/definitionTypes";

// 主题 mock：保留真实 darkTokens 供 <style> 插值（atomic/hints 同款约定）
let mockTheme: "light" | "dark" = "light";
vi.mock("../../theme/themeMode", async () => {
  const actual = await vi.importActual<typeof import("../../theme/themeMode")>(
    "../../theme/themeMode",
  );
  return { ...actual, useThemeMode: () => mockTheme };
});

/** 受控更新形态 */
type StateUpdate = SetStateAction<WorkflowCanvasState>;

/** 受控驱动器：承接 onCanvasStateChange 的 updater/传值两种形态 */
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

/** 构造合法单 operation + params 的 data_transform 配置（fixture 约束） */
function makeConfig(
  operation: DataTransformNodeConfiguration["operation"],
  params: DataTransformOperationParams,
): DataTransformNodeConfiguration {
  return {
    node_type: "data_transform",
    operation,
    input_dataset_reference: "",
    params,
  };
}

/** 构造画布节点数据 */
function makeNodeData(configuration: DataTransformNodeConfiguration): CanvasNodeData {
  return {
    node_id: "dt-1",
    name: "数据转换节点",
    node_type: "data_transform",
    configuration,
  };
}

/** 构造最小画布状态（仅含目标节点） */
function makeCanvasState(data: CanvasNodeData): WorkflowCanvasState {
  const canvasNode: CanvasNode = {
    id: data.node_id,
    type: data.node_type,
    position: { x: 0, y: 0 },
    data,
  };
  return { nodes: [canvasNode], edges: [] };
}

/** 从驱动器状态读取目标节点当前配置 */
function readConfig(driver: { getState(): WorkflowCanvasState }): DataTransformNodeConfiguration {
  const node = driver.getState().nodes.find((n) => n.id === "dt-1");
  if (!node) {
    throw new Error("目标节点缺失于画布状态");
  }
  const configuration = node.data.configuration;
  if (!isDataTransformConfig(configuration)) {
    throw new Error("目标节点配置非 data_transform");
  }
  return configuration;
}

/** 受控渲染：rerender 以驱动器最新状态注入面板 */
function renderPanel(configuration: DataTransformNodeConfiguration) {
  const driver = createDriver(makeCanvasState(makeNodeData(configuration)));
  const currentProps = () => {
    const state = driver.getState();
    const node = state.nodes.find((n) => n.id === "dt-1");
    if (!node) {
      throw new Error("目标节点缺失于画布状态");
    }
    return {
      selectedNode: node.data,
      canvasState: state,
      onCanvasStateChange: driver.onStateChange,
    };
  };
  const view = render(<NodeConfigurationPanel {...currentProps()} />);
  const rerender = () => view.rerender(<NodeConfigurationPanel {...currentProps()} />);
  return {
    driver,
    rerender,
    unmount: () => view.unmount(),
    container: view.container,
  };
}

/** 操作类型下拉（map/filter 视图下唯一 select） */
function getOperationSelect(container: HTMLElement): HTMLSelectElement {
  const select = container.querySelector("select.wfp-ncp-select");
  if (!(select instanceof HTMLSelectElement)) {
    throw new Error("未找到操作类型下拉框");
  }
  return select;
}

/** 表达式文本域（map/filter 视图下唯一 textarea） */
function getExpressionTextarea(container: HTMLElement): HTMLTextAreaElement {
  const textarea = container.querySelector("textarea.wfp-ncp-input");
  if (!(textarea instanceof HTMLTextAreaElement)) {
    throw new Error("未找到表达式文本域");
  }
  return textarea;
}

/** 恢复默认表达式按钮（无 onDeleteNode 注入时唯一 wfp-ncp-btn 按钮） */
function getRestoreButton(container: HTMLElement): HTMLButtonElement {
  const button = container.querySelector("button.wfp-ncp-btn");
  if (!(button instanceof HTMLButtonElement)) {
    throw new Error("未找到恢复默认表达式按钮");
  }
  return button;
}

beforeEach(() => {
  mockTheme = "light";
});

describe("NodeConfigurationPanel data_transform 预制表达式", () => {
  // T-B1（FR-03/AC-03）：新建填充呈现——工厂产出的默认配置渲染即显示预制映射表达式。
  it("T-B1 新建默认配置渲染即显示预制映射表达式 True", () => {
    const { container } = renderPanel(makeConfig("map", { map_expression: "True" }));
    expect(getOperationSelect(container).value).toBe("map");
    expect(getExpressionTextarea(container).value).toBe("True");
  });

  // T-B2（FR-04/AC-04）：切换补空——空 params 节点 UI 切"过滤"，过滤条件补入预制值。
  it("T-B2 空 params 节点 UI 切换到过滤时补入预制条件 True", () => {
    const { container, driver, rerender } = renderPanel(makeConfig("map", {}));
    fireEvent.change(getOperationSelect(container), { target: { value: "filter" } });
    rerender();
    expect(getExpressionTextarea(container).value).toBe("True");
    const config = readConfig(driver);
    expect(config.operation).toBe("filter");
    expect(config.params.filter_condition).toBe("True");
  });

  // T-B3（FR-04/FR-06）：切换不覆盖——已编辑映射值经 map→filter→map 往返后原值保留。
  it("T-B3 切换往返不覆盖已编辑的映射表达式", () => {
    const { container, driver, rerender } = renderPanel(makeConfig("map", {}));
    fireEvent.change(getExpressionTextarea(container), { target: { value: "value > 1" } });
    rerender();
    fireEvent.change(getOperationSelect(container), { target: { value: "filter" } });
    rerender();
    // 过滤条件为空被补入预制值（S2），映射值原样保留（FR-04 仅补空不覆盖）
    expect(getExpressionTextarea(container).value).toBe("True");
    expect(readConfig(driver).params.map_expression).toBe("value > 1");
    fireEvent.change(getOperationSelect(container), { target: { value: "map" } });
    rerender();
    expect(getExpressionTextarea(container).value).toBe("value > 1");
  });

  // T-B4（FR-05/AC-05）：加载零填充——模拟 YAML 缺字段的 params:{} 渲染为空且不回写。
  it("T-B4 加载空 params 渲染为空且不触发回写（零填充）", () => {
    const { container, driver } = renderPanel(makeConfig("map", {}));
    expect(getExpressionTextarea(container).value).toBe("");
    expect(driver.onStateChange).not.toHaveBeenCalled();
  });

  // T-B5（FR-05）：加载非空不改写——自定义值原样显示，加载/重渲染过程不触发回写。
  it("T-B5 加载自定义表达式原样显示且不改写", () => {
    const { container, driver, rerender } = renderPanel(
      makeConfig("map", { map_expression: "自定义值" }),
    );
    expect(getExpressionTextarea(container).value).toBe("自定义值");
    rerender();
    expect(getExpressionTextarea(container).value).toBe("自定义值");
    expect(driver.onStateChange).not.toHaveBeenCalled();
  });

  // T-B6（FR-07/DC-05）：恢复默认入口可用性——值≠预制值可用；值=预制值 disabled、
  // 点击无变化且不报错。
  it("T-B6 恢复默认入口可用性随当前值切换", () => {
    const editable = renderPanel(makeConfig("map", { map_expression: "x == 1" }));
    expect(getRestoreButton(editable.container).disabled).toBe(false);
    editable.unmount();
    const preset = renderPanel(makeConfig("map", { map_expression: "True" }));
    expect(getRestoreButton(preset.container).disabled).toBe(true);
    fireEvent.click(getRestoreButton(preset.container));
    expect(preset.driver.onStateChange).not.toHaveBeenCalled();
    expect(getExpressionTextarea(preset.container).value).toBe("True");
  });

  // T-B7（FR-07/AC-07）：恢复默认点击——"x == 1" 点击后回写预制值且按钮转 disabled。
  it("T-B7 点击恢复默认回写预制表达式并更新可用性", () => {
    const { container, driver, rerender } = renderPanel(
      makeConfig("map", { map_expression: "x == 1" }),
    );
    fireEvent.click(getRestoreButton(container));
    expect(readConfig(driver).params.map_expression).toBe("True");
    rerender();
    expect(getExpressionTextarea(container).value).toBe("True");
    expect(getRestoreButton(container).disabled).toBe(true);
  });

  // T-B8（FR-06）：重渲染幂等——同一配置多次 rerender 字段值不变、不触发回写。
  it("T-B8 多次重渲染同一配置字段值保持不变", () => {
    const { container, driver, rerender } = renderPanel(
      makeConfig("map", { map_expression: "自定义值" }),
    );
    for (let i = 0; i < 3; i += 1) {
      rerender();
      expect(getExpressionTextarea(container).value).toBe("自定义值");
    }
    expect(driver.onStateChange).not.toHaveBeenCalled();
  });
});
