// =============================================================================
// 画布拖拽放置单元测试（canvas/__tests__/WorkflowCanvas.drop.test.tsx）
//
// 留待非沙箱环境执行：vitest run src/canvas/__tests__/WorkflowCanvas.drop.test.tsx
//
// 覆盖：
//   1. onDragOver 调用 preventDefault + 设置 dropEffect = "move"
//   2. onDrop 读取 dataTransfer → 坐标转换 → 生成节点 → 追加
//   3. 非法类型被拒绝
//   4. 空类型被拒绝
//   5. data_transform 新建节点默认带预制映射表达式（data_transform_preset_expressions T-C1/AC-03）
//   6. llm 放行：默认配置采样不落盘、双提示词空串（llm_code_nodes_v2 CHG-F06/DC-14，B3）
//   7. code 放行：language 固定 python、entry_function 取 main（CHG-F06/FR-16/FR-18，B3）
//   8. DEFAULT_NODE_NAMES 与 NODE_TEMPLATES 的 label 逐类型一致（新建入口两键同源，B1/B3）
// =============================================================================

import { describe, it, expect, vi, afterEach } from "vitest";
import { render } from "@testing-library/react";
import WorkflowCanvas, { DEFAULT_NODE_NAMES } from "../WorkflowCanvas";
import { NODE_TEMPLATES } from "../NodePalette";
import {
  isDataTransformConfig,
  isLlmConfig,
  isCodeConfig,
} from "../../workflow-definition/definitionTypes";
import type { DragEvent as ReactDragEvent } from "react";
import type { WorkflowCanvasState } from "../canvasTypes";

// jsdom 无 ResizeObserver，reactflow 画布在测试环境渲染需要桩实现（仅测试环境补丁，
// 与 footerExportButtonVisual 等画布渲染测试同款约定；不改变任何用例断言）
vi.stubGlobal(
  "ResizeObserver",
  class {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  },
);

// jsdom 未实现 DragEvent 构造器（既有用例在沙箱长期失败的环境根因）：
// 以 Event 扩展桩补齐用例所需的 bubbles/cancelable/clientX/clientY 形态；
// dataTransfer 由用例内 Object.defineProperty 注入，桩无需承载。不改变任何用例断言。
class DragEventStub extends Event {
  readonly clientX: number;
  readonly clientY: number;
  constructor(
    type: string,
    init?: { bubbles?: boolean; cancelable?: boolean; clientX?: number; clientY?: number },
  ) {
    super(type, init);
    this.clientX = init?.clientX ?? 0;
    this.clientY = init?.clientY ?? 0;
  }
}
vi.stubGlobal("DragEvent", DragEventStub);

// 真实 reactflow 的实例注入是异步的：内部 onInit 由 setTimeout(..., 1) 触发
// （@reactflow/core dist/esm/index.mjs:3643；沙箱调试实证：渲染后同步派发
// SYNC_CALLED=0、宏任务后再派发 ASYNC_CALLED=1），同步用例体内 rfInstanceRef
// 恒为 null，onDrop 在实例判空（WorkflowCanvas.tsx:456）处提前返回——这是既有
// 用例在沙箱长期失败的第二重环境根因。以下以桩组件替换 ReactFlow 默认导出
// （与 race/edges/persistence 等兄弟测试同款 vi.mock 约定）：渲染 .react-flow
// 容器、直通 onDragOver/onDrop，并在挂载期同步注入仅含 screenToFlowPosition
// 的实例桩（render 内联 act 会同步冲刷 useEffect）。既有 4 例用例体零改动
// （T2.2 约束），不改变任何断言。
vi.mock("reactflow", async (importOriginal) => {
  const actual = await importOriginal<typeof import("reactflow")>();
  const { createElement, useEffect } = await import("react");
  const MockReactFlow = (props: {
    onInit?: (instance: {
      screenToFlowPosition: (position: { x: number; y: number }) => { x: number; y: number };
    }) => void;
    onDragOver?: (event: ReactDragEvent) => void;
    onDrop?: (event: ReactDragEvent) => void;
  }) => {
    useEffect(() => {
      props.onInit?.({
        screenToFlowPosition: (position: { x: number; y: number }) => ({
          x: position.x,
          y: position.y,
        }),
      });
    }, []);
    return createElement("div", {
      className: "react-flow",
      onDragOver: props.onDragOver,
      onDrop: props.onDrop,
    });
  };
  return { ...actual, default: MockReactFlow };
});

/** 构造最小可用画布状态 */
function makeCanvasState(): WorkflowCanvasState {
  return { nodes: [], edges: [] };
}

/**
 * 函数式提交适配（CHG-1~2）：以基准状态应用 onCanvasStateChange 捕获的更新
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

afterEach(() => {
  vi.restoreAllMocks();
});

describe("WorkflowCanvas 拖拽放置", () => {
  it("onDragOver 调用 preventDefault 并设置 dropEffect", () => {
    const state = makeCanvasState();
    const { container } = render(
      <WorkflowCanvas
        canvasState={state}
        onCanvasStateChange={() => {}}
        selectedNodeId={null}
        onSelectNode={() => {}}
      />,
    );

    const pane = container.querySelector(".react-flow") as HTMLElement;
    const dragOverEvent = new DragEvent("dragover", {
      bubbles: true,
      cancelable: true,
    });
    Object.defineProperty(dragOverEvent, "dataTransfer", {
      value: { dropEffect: "", effectAllowed: "move" },
    });

    pane.dispatchEvent(dragOverEvent);
    expect(dragOverEvent.defaultPrevented).toBe(true);
  });

  it("onDrop 合法类型时创建节点并调用 onCanvasStateChange", () => {
    const state = makeCanvasState();
    const onStateChange = vi.fn();
    const { container } = render(
      <WorkflowCanvas
        canvasState={state}
        onCanvasStateChange={onStateChange}
        selectedNodeId={null}
        onSelectNode={() => {}}
      />,
    );

    const pane = container.querySelector(".react-flow") as HTMLElement;
    const dropEvent = new DragEvent("drop", {
      bubbles: true,
      cancelable: true,
      clientX: 100,
      clientY: 200,
    });
    Object.defineProperty(dropEvent, "dataTransfer", {
      value: {
        getData: vi.fn((key: string) =>
          key === "application/node-type" ? "agent" : "",
        ),
        dropEffect: "",
      },
    });

    pane.dispatchEvent(dropEvent);
    // onCanvasStateChange 应被调用，新节点已追加
    expect(onStateChange).toHaveBeenCalled();
    // 函数式提交适配（CHG-2）：以基准状态应用 updater 后断言
    const newState = applyCapturedUpdate(onStateChange.mock.calls, state);
    expect(newState.nodes).toHaveLength(1);
    expect(newState.nodes[0].type).toBe("agent");
  });

  it("onDrop data_transform 时新建节点带预制映射表达式（T-C1/AC-03）", () => {
    const state = makeCanvasState();
    const onStateChange = vi.fn();
    const { container } = render(
      <WorkflowCanvas
        canvasState={state}
        onCanvasStateChange={onStateChange}
        selectedNodeId={null}
        onSelectNode={() => {}}
      />,
    );

    const pane = container.querySelector(".react-flow") as HTMLElement;
    const dropEvent = new DragEvent("drop", {
      bubbles: true,
      cancelable: true,
      clientX: 100,
      clientY: 200,
    });
    Object.defineProperty(dropEvent, "dataTransfer", {
      value: {
        getData: vi.fn((key: string) =>
          key === "application/node-type" ? "data_transform" : "",
        ),
        dropEffect: "",
      },
    });

    pane.dispatchEvent(dropEvent);
    expect(onStateChange).toHaveBeenCalled();
    const newState = applyCapturedUpdate(onStateChange.mock.calls, state);
    expect(newState.nodes).toHaveLength(1);
    expect(newState.nodes[0].type).toBe("data_transform");
    // 新建默认填入（FR-03/DC-02）：默认操作类型 map，params.map_expression 为预制值
    const configuration = newState.nodes[0].data.configuration;
    expect(isDataTransformConfig(configuration)).toBe(true);
    if (!isDataTransformConfig(configuration)) {
      throw new Error("expected data_transform configuration");
    }
    expect(configuration.operation).toBe("map");
    expect(configuration.params.map_expression).toBe("True");
  });

  it("onDrop 非法类型时不创建节点", () => {
    const state = makeCanvasState();
    const onStateChange = vi.fn();
    const { container } = render(
      <WorkflowCanvas
        canvasState={state}
        onCanvasStateChange={onStateChange}
        selectedNodeId={null}
        onSelectNode={() => {}}
      />,
    );

    const pane = container.querySelector(".react-flow") as HTMLElement;
    const dropEvent = new DragEvent("drop", {
      bubbles: true,
      cancelable: true,
      clientX: 100,
      clientY: 200,
    });
    Object.defineProperty(dropEvent, "dataTransfer", {
      value: {
        getData: vi.fn((key: string) =>
          key === "application/node-type" ? "invalid_type" : "",
        ),
        dropEffect: "",
      },
    });

    pane.dispatchEvent(dropEvent);
    expect(onStateChange).not.toHaveBeenCalled();
  });

  it("onDrop 空类型时不创建节点", () => {
    const state = makeCanvasState();
    const onStateChange = vi.fn();
    const { container } = render(
      <WorkflowCanvas
        canvasState={state}
        onCanvasStateChange={onStateChange}
        selectedNodeId={null}
        onSelectNode={() => {}}
      />,
    );

    const pane = container.querySelector(".react-flow") as HTMLElement;
    const dropEvent = new DragEvent("drop", {
      bubbles: true,
      cancelable: true,
      clientX: 100,
      clientY: 200,
    });
    Object.defineProperty(dropEvent, "dataTransfer", {
      value: {
        getData: vi.fn(() => ""),
        dropEffect: "",
      },
    });

    pane.dispatchEvent(dropEvent);
    expect(onStateChange).not.toHaveBeenCalled();
  });

  it("onDrop llm 时创建节点：采样参数不落盘、默认双提示词空串（CHG-F06/DC-14）", () => {
    const state = makeCanvasState();
    const onStateChange = vi.fn();
    const { container } = render(
      <WorkflowCanvas
        canvasState={state}
        onCanvasStateChange={onStateChange}
        selectedNodeId={null}
        onSelectNode={() => {}}
      />,
    );

    const pane = container.querySelector(".react-flow") as HTMLElement;
    const dropEvent = new DragEvent("drop", {
      bubbles: true,
      cancelable: true,
      clientX: 100,
      clientY: 200,
    });
    Object.defineProperty(dropEvent, "dataTransfer", {
      value: {
        getData: vi.fn((key: string) =>
          key === "application/node-type" ? "llm" : "",
        ),
        dropEffect: "",
      },
    });

    pane.dispatchEvent(dropEvent);
    expect(onStateChange).toHaveBeenCalled();
    const newState = applyCapturedUpdate(onStateChange.mock.calls, state);
    expect(newState.nodes).toHaveLength(1);
    expect(newState.nodes[0].type).toBe("llm");
    // 默认显示名与面板模板 label 同源（CHG-F06）
    expect(newState.nodes[0].data.name).toBe("大模型");
    const configuration = newState.nodes[0].data.configuration;
    expect(isLlmConfig(configuration)).toBe(true);
    if (!isLlmConfig(configuration)) {
      throw new Error("expected llm configuration");
    }
    // 最小合法配置：模型标识留空待填、双提示词取后端缺省空串
    expect(configuration.model_identifier).toBe("");
    expect(configuration.system_prompt).toBe("");
    expect(configuration.user_prompt).toBe("");
    // 采样参数全部不落盘：未配置即 undefined，非 0/空脏值（DC-14 与后端 model_dump 对称）
    expect(configuration.temperature).toBeUndefined();
    expect(configuration.top_p).toBeUndefined();
    expect(configuration.top_k).toBeUndefined();
    expect(configuration.max_tokens).toBeUndefined();
    expect(configuration.stop).toBeUndefined();
    expect(configuration.seed).toBeUndefined();
  });

  it("onDrop code 时创建节点：language 固定 python、entry_function 取 main（CHG-F06/FR-16/FR-18）", () => {
    const state = makeCanvasState();
    const onStateChange = vi.fn();
    const { container } = render(
      <WorkflowCanvas
        canvasState={state}
        onCanvasStateChange={onStateChange}
        selectedNodeId={null}
        onSelectNode={() => {}}
      />,
    );

    const pane = container.querySelector(".react-flow") as HTMLElement;
    const dropEvent = new DragEvent("drop", {
      bubbles: true,
      cancelable: true,
      clientX: 100,
      clientY: 200,
    });
    Object.defineProperty(dropEvent, "dataTransfer", {
      value: {
        getData: vi.fn((key: string) =>
          key === "application/node-type" ? "code" : "",
        ),
        dropEffect: "",
      },
    });

    pane.dispatchEvent(dropEvent);
    expect(onStateChange).toHaveBeenCalled();
    const newState = applyCapturedUpdate(onStateChange.mock.calls, state);
    expect(newState.nodes).toHaveLength(1);
    expect(newState.nodes[0].type).toBe("code");
    expect(newState.nodes[0].data.name).toBe("代码");
    const configuration = newState.nodes[0].data.configuration;
    expect(isCodeConfig(configuration)).toBe(true);
    if (!isCodeConfig(configuration)) {
      throw new Error("expected code configuration");
    }
    // 空脚本待填；language 固定 python（本期取值域）；entry_function 为展示语义缺省（FR-18）
    expect(configuration.language).toBe("python");
    expect(configuration.code).toBe("");
    expect(configuration.entry_function).toBe("main");
  });

  it("DEFAULT_NODE_NAMES 与 NODE_TEMPLATES 的 label 逐类型一致（新建入口两键同源）", () => {
    // 面板模板的每一类型在画布默认名映射中有同值 label（含 llm/code，双向一致性机械判据）
    for (const template of NODE_TEMPLATES) {
      expect(DEFAULT_NODE_NAMES[template.type]).toBe(template.label);
    }
    // tool 已移除新建入口（DC-C）：模板表无 tool，映射中保留存量渲染兼容名
    expect(NODE_TEMPLATES.some((t) => t.type === "tool")).toBe(false);
    expect(DEFAULT_NODE_NAMES.tool).toBe("Tool");
  });
});