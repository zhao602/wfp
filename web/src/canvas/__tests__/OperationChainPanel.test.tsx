// =============================================================================
// 操作链面板测试（canvas/__tests__/OperationChainPanel.test.tsx）
//
// 执行：cd web && npx vitest run src/canvas/__tests__/OperationChainPanel.test.tsx
//
// 覆盖（expand_data_transform_ops DC-09.2 / FR-21 / FR-22 / AC-19）：
//   OCP-01~06 组件级：步骤列表呈现（data-step-index/count）、增步（默认 map）、
//            步数上限 20 禁用、删步与链长 1 保护、上移/下移边界、updateStep 上下文
//   ICP-01 链长 1 呈现：旧单 operation 配置零迁移呈现为单步骤链（挂载不回写）
//   ICP-02 四分类下拉：4 个 optgroup 与分类标签一致、总 21 项、既有四项归入 basic
//   ICP-03 多步链与条件区显隐：双步链逐步骤渲染，条件配置区仅 filter 步骤挂载
//   ICP-04 增步写回：多步链追加默认 map 步、顶层镜像首步（DC-09.4 写回口径）
//   ICP-05 删步写回：链长回落 1 时删除 operations 键、顶层承接剩余步
//   ICP-06 输出格式：缺省 dataset 无提示；切 text 落键+提示+参数区；切回删键
// =============================================================================

import { describe, it, expect, vi } from "vitest";
import { render, fireEvent } from "@testing-library/react";
import type { SetStateAction } from "react";
import OperationChainPanel from "../OperationChainPanel";
import type { OperationStepRenderContext } from "../OperationChainPanel";
import { MAX_OPERATION_CHAIN_STEPS, OPERATION_CATEGORY_LABELS } from "../dataTransformOperations";
import NodeConfigurationPanel from "../NodeConfigurationPanel";
import { isDataTransformConfig } from "../../workflow-definition/definitionTypes";
import type {
  DataTransformStep,
  DataTransformOperation,
  DataTransformNodeConfiguration,
  DataTransformOperationParams,
} from "../../workflow-definition/definitionTypes";
import type { CanvasNodeData, CanvasNode, WorkflowCanvasState } from "../canvasTypes";

// 主题 mock：保留真实 darkTokens 供 <style> 插值（atomic/hints 同款约定）
let mockTheme: "light" | "dark" = "light";
vi.mock("../../theme/themeMode", async () => {
  const actual = await vi.importActual<typeof import("../../theme/themeMode")>(
    "../../theme/themeMode",
  );
  return { ...actual, useThemeMode: () => mockTheme };
});

/** 构造步骤列表 */
function makeSteps(operations: DataTransformOperation[]): DataTransformStep[] {
  return operations.map((operation) => ({ operation, params: {} }));
}

// ---------------------------------------------------------------------------
// 组件级：OperationChainPanel（renderStep 桩注入）
// ---------------------------------------------------------------------------

describe("OperationChainPanel 组件（DC-09.2）", () => {
  /** 静态桩：透出步骤操作与下标 */
  const stubStep = (ctx: OperationStepRenderContext) => (
    <div className="ocp-stub" data-op={ctx.step.operation} data-index={ctx.stepIndex} />
  );

  it("OCP-01 步骤列表呈现：data-step-count 与逐步骤 data-step-index、renderStep 逐参注入", () => {
    const onChangeSteps = vi.fn();
    const { container } = render(
      <OperationChainPanel
        steps={makeSteps(["map", "filter"])}
        onChangeSteps={onChangeSteps}
        renderStep={stubStep}
      />,
    );
    const root = container.querySelector(".wfp-ocp-root");
    expect(root?.getAttribute("data-step-count")).toBe("2");
    const stepDivs = container.querySelectorAll(".wfp-ocp-step");
    expect(stepDivs.length).toBe(2);
    expect(stepDivs[0].getAttribute("data-step-index")).toBe("0");
    expect(stepDivs[1].getAttribute("data-step-index")).toBe("1");
    const stubs = container.querySelectorAll(".ocp-stub");
    expect(Array.from(stubs).map((stub) => stub.getAttribute("data-op"))).toEqual(["map", "filter"]);
    expect(Array.from(stubs).map((stub) => stub.getAttribute("data-index"))).toEqual(["0", "1"]);
  });

  it("OCP-02 增步：追加默认 map 步骤（参数零填充）", () => {
    const onChangeSteps = vi.fn();
    const { container } = render(
      <OperationChainPanel
        steps={makeSteps(["map"])}
        onChangeSteps={onChangeSteps}
        renderStep={stubStep}
      />,
    );
    fireEvent.click(container.querySelector(".wfp-ocp-add") as HTMLButtonElement);
    expect(onChangeSteps).toHaveBeenCalledTimes(1);
    expect(onChangeSteps).toHaveBeenCalledWith([
      { operation: "map", params: {} },
      { operation: "map", params: {} },
    ]);
  });

  it("OCP-03 步数上限：链长 20 时增步禁用且提示上限", () => {
    const onChangeSteps = vi.fn();
    const fullSteps = makeSteps(Array.from({ length: MAX_OPERATION_CHAIN_STEPS }, () => "map"));
    const { container } = render(
      <OperationChainPanel steps={fullSteps} onChangeSteps={onChangeSteps} renderStep={stubStep} />,
    );
    const addButton = container.querySelector(".wfp-ocp-add") as HTMLButtonElement;
    expect(addButton.disabled).toBe(true);
    fireEvent.click(addButton);
    expect(onChangeSteps).not.toHaveBeenCalled();
    expect((container.textContent ?? "").includes(`已达步数上限（${MAX_OPERATION_CHAIN_STEPS}）`)).toBe(
      true,
    );
  });

  it("OCP-04 删步与链长 1 保护：多步可删，单步禁删", () => {
    const onChangeSteps = vi.fn();
    const { container } = render(
      <OperationChainPanel
        steps={makeSteps(["map", "filter"])}
        onChangeSteps={onChangeSteps}
        renderStep={stubStep}
      />,
    );
    const removeButtons = container.querySelectorAll(".wfp-ocp-remove");
    expect(removeButtons.length).toBe(2);
    expect((removeButtons[0] as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(removeButtons[0] as HTMLButtonElement);
    expect(onChangeSteps).toHaveBeenCalledWith([{ operation: "filter", params: {} }]);

    const single = render(
      <OperationChainPanel steps={makeSteps(["map"])} onChangeSteps={onChangeSteps} renderStep={stubStep} />,
    );
    const singleRemove = single.container.querySelector(".wfp-ocp-remove") as HTMLButtonElement;
    expect(singleRemove.disabled).toBe(true);
    fireEvent.click(singleRemove);
    expect(onChangeSteps).toHaveBeenCalledTimes(1);
  });

  it("OCP-05 上移/下移边界：首步不可上移、末步不可下移、中间步交换顺序", () => {
    // 上移中间步
    const upSpy = vi.fn();
    const upView = render(
      <OperationChainPanel
        steps={makeSteps(["map", "filter", "sort"])}
        onChangeSteps={upSpy}
        renderStep={stubStep}
      />,
    );
    const upButtons = upView.container.querySelectorAll(".wfp-ocp-up");
    expect((upButtons[0] as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(upButtons[1] as HTMLButtonElement);
    expect(upSpy).toHaveBeenCalledWith(makeSteps(["filter", "map", "sort"]));

    // 下移中间步
    const downSpy = vi.fn();
    const downView = render(
      <OperationChainPanel
        steps={makeSteps(["map", "filter", "sort"])}
        onChangeSteps={downSpy}
        renderStep={stubStep}
      />,
    );
    const downButtons = downView.container.querySelectorAll(".wfp-ocp-down");
    expect((downButtons[2] as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(downButtons[1] as HTMLButtonElement);
    expect(downSpy).toHaveBeenCalledWith(makeSteps(["map", "sort", "filter"]));
  });

  it("OCP-06 updateStep 上下文：整步替换写回对应下标", () => {
    const onChangeSteps = vi.fn();
    const interactiveStep = (ctx: OperationStepRenderContext) => (
      <button
        type="button"
        className="stub-update"
        onClick={() => ctx.updateStep({ operation: "sort", params: {} })}
      >
        替换
      </button>
    );
    const { container } = render(
      <OperationChainPanel
        steps={makeSteps(["map", "filter"])}
        onChangeSteps={onChangeSteps}
        renderStep={interactiveStep}
      />,
    );
    const updateButtons = container.querySelectorAll(".stub-update");
    fireEvent.click(updateButtons[1] as HTMLButtonElement);
    expect(onChangeSteps).toHaveBeenCalledWith([
      { operation: "map", params: {} },
      { operation: "sort", params: {} },
    ]);
  });
});

// ---------------------------------------------------------------------------
// 集成级：DataTransformConfigForm（经 NodeConfigurationPanel 全链路）
// ---------------------------------------------------------------------------

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

/** 构造旧单 operation 配置（链长 1 形态） */
function makeConfig(
  operation: DataTransformOperation,
  params: DataTransformOperationParams,
): DataTransformNodeConfiguration {
  return {
    node_type: "data_transform",
    operation,
    input_dataset_reference: "",
    params,
  };
}

/** 构造多步操作链配置 */
function makeChainConfig(steps: DataTransformStep[]): DataTransformNodeConfiguration {
  return {
    node_type: "data_transform",
    operation: steps[0].operation,
    input_dataset_reference: "",
    params: steps[0].params ?? {},
    operations: steps,
  };
}

/** 按标签文案查找字段输入框（Field 控件位于 label 内） */
function findFieldInput(container: HTMLElement, labelText: string): HTMLInputElement {
  const label = Array.from(container.querySelectorAll("label")).find((l) =>
    (l.textContent ?? "").includes(labelText),
  );
  const input = label?.querySelector("input.wfp-ncp-input") ?? null;
  if (!(input instanceof HTMLInputElement)) {
    throw new Error(`未找到字段输入框：${labelText}`);
  }
  return input;
}

describe("DataTransformConfigForm 集成（DC-09.2/DC-09.4）", () => {
  it("ICP-01 链长 1 呈现：旧单 operation 配置零迁移呈现为单步骤链且挂载不回写", () => {
    const { driver, container } = renderPanel(makeConfig("map", { map_expression: "True" }));
    expect(container.querySelector(".wfp-ocp-root")?.getAttribute("data-step-count")).toBe("1");
    const select = container.querySelector("select.wfp-ncp-select") as HTMLSelectElement;
    expect(select.value).toBe("map");
    const textarea = container.querySelector("textarea.wfp-ncp-input") as HTMLTextAreaElement;
    expect(textarea.value).toBe("True");
    const resetButton = container.querySelector("button.wfp-ncp-btn") as HTMLButtonElement;
    expect(resetButton.disabled).toBe(true);
    const config = readConfig(driver);
    expect(config.operations).toBeUndefined();
    expect(config.operation).toBe("map");
    expect(config.params.map_expression).toBe("True");
  });

  it("ICP-02 四分类下拉：4 个 optgroup、总 21 项、既有四项归入 basic", () => {
    const { container } = renderPanel(makeConfig("map", {}));
    const select = container.querySelector("select.wfp-ncp-select") as HTMLSelectElement;
    const optgroups = select.querySelectorAll("optgroup");
    expect(optgroups.length).toBe(4);
    const expectedLabels = ["basic", "cleaning", "ai", "advanced"].map(
      (category) =>
        OPERATION_CATEGORY_LABELS[category as keyof typeof OPERATION_CATEGORY_LABELS],
    );
    expect(Array.from(optgroups).map((group) => group.label)).toEqual(expectedLabels);
    expect(select.querySelectorAll("option").length).toBe(21);
    const basicOptions = Array.from(optgroups[0].querySelectorAll("option")).map(
      (option) => option.value,
    );
    for (const id of ["map", "filter", "aggregate", "join"]) {
      expect(basicOptions).toContain(id);
    }
  });

  it("ICP-03 多步链与条件区显隐：逐步骤渲染，条件配置区仅 filter 步骤挂载", () => {
    const chain: DataTransformStep[] = [
      { operation: "map", params: { map_expression: "True" } },
      { operation: "filter", params: { filter_condition: "value >= min_value" } },
    ];
    const { container } = renderPanel(makeChainConfig(chain));
    expect(container.querySelector(".wfp-ocp-root")?.getAttribute("data-step-count")).toBe("2");
    const selects = container.querySelectorAll("select.wfp-ncp-select");
    expect(selects.length).toBe(2);
    expect((selects[0] as HTMLSelectElement).value).toBe("map");
    expect((selects[1] as HTMLSelectElement).value).toBe("filter");
    const stepDivs = container.querySelectorAll(".wfp-ocp-step");
    expect(stepDivs[0].querySelectorAll(".wfp-cge-root").length).toBe(0);
    expect(stepDivs[1].querySelectorAll(".wfp-cge-root").length).toBe(1);
  });

  it("ICP-04 增步写回：追加默认 map 步，顶层镜像首步（DC-09.4）", () => {
    const chain: DataTransformStep[] = [
      { operation: "map", params: { map_expression: "True" } },
      { operation: "filter", params: { filter_condition: "value >= min_value" } },
    ];
    const { driver, rerender, container } = renderPanel(makeChainConfig(chain));
    fireEvent.click(container.querySelector(".wfp-ocp-add") as HTMLButtonElement);
    rerender();
    const config = readConfig(driver);
    expect(config.operations?.length).toBe(3);
    expect(config.operations?.[2]).toEqual({ operation: "map", params: {} });
    expect(config.operation).toBe("map");
    expect(config.params).toEqual({ map_expression: "True" });
    expect(container.querySelector(".wfp-ocp-root")?.getAttribute("data-step-count")).toBe("3");
  });

  it("ICP-05 删步写回：链长回落 1 时删除 operations 键、顶层承接剩余步", () => {
    const chain: DataTransformStep[] = [
      { operation: "map", params: { map_expression: "True" } },
      { operation: "filter", params: { filter_condition: "value >= min_value" } },
    ];
    const { driver, rerender, container } = renderPanel(makeChainConfig(chain));
    const removeButtons = container.querySelectorAll(".wfp-ocp-remove");
    fireEvent.click(removeButtons[0] as HTMLButtonElement);
    rerender();
    const config = readConfig(driver);
    expect(config.operations).toBeUndefined();
    expect(config.operation).toBe("filter");
    expect(config.params).toEqual({ filter_condition: "value >= min_value" });
    expect(container.querySelector(".wfp-ocp-root")?.getAttribute("data-step-count")).toBe("1");
    const select = container.querySelector("select.wfp-ncp-select") as HTMLSelectElement;
    expect(select.value).toBe("filter");
  });

  it("ICP-06 输出格式：缺省 dataset 无提示；切 text 落键+提示+参数接线；切回删键", () => {
    const { driver, rerender, container } = renderPanel(makeConfig("map", {}));
    // 缺省 dataset：aria-pressed 与无提示
    const datasetButton = container.querySelector(
      ".wfp-dtf-format[data-format='dataset']",
    ) as HTMLButtonElement;
    expect(datasetButton.getAttribute("aria-pressed")).toBe("true");
    expect(container.querySelectorAll(".wfp-dtf-format").length).toBe(4);
    expect(container.querySelectorAll(".wfp-dtf-format-note").length).toBe(0);
    // 切 text：落键 + 提示 + 参数区
    fireEvent.click(
      container.querySelector(".wfp-dtf-format[data-format='text']") as HTMLButtonElement,
    );
    rerender();
    expect(readConfig(driver).output_format).toBe("text");
    expect(readConfig(driver).output_format_params).toEqual({});
    expect(container.querySelectorAll(".wfp-dtf-format-note").length).toBe(1);
    fireEvent.change(findFieldInput(container, "拼接字段名"), { target: { value: "content" } });
    rerender();
    expect(readConfig(driver).output_format_params?.text_field).toBe("content");
    // 注：type="text" 输入框按 HTML 值净化算法剥离开行符（"\n" 赋值后值不变，
    // React 值跟踪会吞掉 change 事件），故此处以非换行分隔符验证接线。
    fireEvent.change(findFieldInput(container, "行间分隔符"), { target: { value: ";" } });
    rerender();
    expect(readConfig(driver).output_format_params?.text_separator).toBe(";");
    // 切回 dataset：删键 + 提示消失
    fireEvent.click(
      container.querySelector(".wfp-dtf-format[data-format='dataset']") as HTMLButtonElement,
    );
    rerender();
    expect(readConfig(driver).output_format).toBeUndefined();
    expect(readConfig(driver).output_format_params).toBeUndefined();
    expect(container.querySelectorAll(".wfp-dtf-format-note").length).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// L-03 steps 运行时宽容归一（fix_agent_null_length G-06 / DC-05）
// 双态：null / undefined → 回落空链安全渲染；合法数组同引用零回归（对照 OCP-01）
// ---------------------------------------------------------------------------

describe("L-03 steps 运行时宽容归一（fix_agent_null_length）", () => {
  const stubStep = (ctx: OperationStepRenderContext) => (
    <div className="ocp-stub" data-op={ctx.step.operation} data-index={ctx.stepIndex} />
  );

  it("L-03-a steps=null：不崩溃，data-step-count=0、零步骤、增步按钮可用", () => {
    const onChangeSteps = vi.fn();
    const { container } = render(
      <OperationChainPanel
        steps={null as unknown as DataTransformStep[]}
        onChangeSteps={onChangeSteps}
        renderStep={stubStep}
      />,
    );
    expect(container.querySelector(".wfp-ocp-root")?.getAttribute("data-step-count")).toBe("0");
    expect(container.querySelectorAll(".wfp-ocp-step").length).toBe(0);
    const addButton = container.querySelector(".wfp-ocp-add") as HTMLButtonElement;
    expect(addButton.disabled).toBe(false);
  });

  it("L-03-b steps=undefined：同口径安全渲染", () => {
    const onChangeSteps = vi.fn();
    const { container } = render(
      <OperationChainPanel
        steps={undefined as unknown as DataTransformStep[]}
        onChangeSteps={onChangeSteps}
        renderStep={stubStep}
      />,
    );
    expect(container.querySelector(".wfp-ocp-root")?.getAttribute("data-step-count")).toBe("0");
    expect(container.querySelectorAll(".wfp-ocp-step").length).toBe(0);
  });

  it("L-03-c 合法数组对照：非空链正常渲染，入口归一零回归", () => {
    const onChangeSteps = vi.fn();
    const { container } = render(
      <OperationChainPanel
        steps={makeSteps(["map", "filter"])}
        onChangeSteps={onChangeSteps}
        renderStep={stubStep}
      />,
    );
    expect(container.querySelector(".wfp-ocp-root")?.getAttribute("data-step-count")).toBe("2");
    expect(container.querySelectorAll(".wfp-ocp-step").length).toBe(2);
  });
});
