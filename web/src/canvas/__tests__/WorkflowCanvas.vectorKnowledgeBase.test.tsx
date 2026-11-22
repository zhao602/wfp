// =============================================================================
// 向量知识库节点画布链路测试（canvas/__tests__/WorkflowCanvas.vectorKnowledgeBase.test.tsx）
//
// 留待非沙箱环境执行：npx vitest run src/canvas/__tests__/WorkflowCanvas.vectorKnowledgeBase.test.tsx
//
// 覆盖 design.md §4.1.2 批次 4-a 用例组：
//   VKB-C01 节点面板出现"向量知识库"卡片（图标 + #2e7d32 类型色条，AC-01）
//   VKB-C02 拖拽数据携带 application/node-type = vector_knowledge_base（AC-01）
//   VKB-C03 拖入画布生成最小合法默认配置（AC-03 / AC-04）
//   VKB-C04 深色令牌 typeFill / typeBorder 双轨键补齐（AC-43）
//   VKB-C05 节点视图浅色渲染完整可见（AC-43）
//   VKB-C06 节点视图深色渲染完整可见（AC-43）
// =============================================================================

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { ReactFlowProvider } from "reactflow";
import type { DragEvent as ReactDragEvent } from "react";
import NodePalette from "../NodePalette";
import WorkflowCanvas from "../WorkflowCanvas";
import VectorKnowledgeBaseNodeView from "../nodes/VectorKnowledgeBaseNodeView";
import { darkTokens } from "../../theme/themeMode";
import { isVectorKnowledgeBaseConfig } from "../../workflow-definition/definitionTypes";
import type { WorkflowCanvasState } from "../canvasTypes";

// 主题 mock：darkTokens 经 importActual 保留真实值，useThemeMode 可控切换深浅
let mockTheme: "light" | "dark" = "light";
vi.mock("../../theme/themeMode", async () => {
  const actual = await vi.importActual<typeof import("../../theme/themeMode")>(
    "../../theme/themeMode",
  );
  return { ...actual, useThemeMode: () => mockTheme };
});

// jsdom 无 ResizeObserver，reactflow 画布在测试环境渲染需要桩实现
// （与 WorkflowCanvas.drop.test.tsx 同款约定，不改变任何用例断言）
vi.stubGlobal(
  "ResizeObserver",
  class {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  },
);

// jsdom 未实现 DragEvent 构造器：以 Event 扩展桩补齐
// bubbles/cancelable/clientX/clientY 形态（与 WorkflowCanvas.drop.test.tsx 同款约定）
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

// 真实 reactflow 实例注入为异步（onInit 由 setTimeout 触发），同步用例体内
// rfInstanceRef 恒为 null——以桩组件替换 ReactFlow 默认导出，挂载期同步注入
// 仅含 screenToFlowPosition 的实例桩（与 WorkflowCanvas.drop.test.tsx 同款约定）
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

/** 构造拖拽事件所需的 dataTransfer stub（jsdom 无原生 DataTransfer） */
function makeDataTransferStub() {
  return {
    setData: vi.fn(),
    effectAllowed: "",
  } as unknown as DataTransfer;
}

/** 构造最小可用画布状态 */
function makeCanvasState(): WorkflowCanvasState {
  return { nodes: [], edges: [] };
}

/**
 * 函数式提交适配：以基准状态应用 onCanvasStateChange 捕获的更新
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
  cleanup();
  mockTheme = "light";
});

// ---------------------------------------------------------------------------
// VKB-C01 / VKB-C02：节点面板包含向量知识库卡片（AC-01）
// ---------------------------------------------------------------------------

describe("VKB-C01: NodePalette 包含向量知识库卡片（AC-01）", () => {
  it("渲染 NodePalette 出现向量知识库卡片，含语义图标与 #2e7d32 类型色条", () => {
    render(<NodePalette />);
    const card = screen.getByText("向量知识库");
    expect(card).toHaveStyle({ cursor: "grab" });
    expect(card).toHaveStyle({ borderLeftColor: "#2e7d32" });
    const icon = card.querySelector("svg");
    expect(icon).toBeTruthy();
    const paths = icon?.querySelectorAll("path");
    expect(paths?.length ?? 0).toBeGreaterThan(0);
  });
});

describe("VKB-C02: 向量知识库卡片拖拽数据（AC-01）", () => {
  it("dragStart 携带 application/node-type = vector_knowledge_base 且 effectAllowed = move", () => {
    render(<NodePalette />);
    const card = screen.getByText("向量知识库");
    const dataTransfer = makeDataTransferStub();
    const setDataSpy = vi.spyOn(dataTransfer, "setData");
    fireEvent.dragStart(card, { dataTransfer });
    expect(setDataSpy).toHaveBeenCalledWith(
      "application/node-type",
      "vector_knowledge_base",
    );
    expect(dataTransfer.effectAllowed).toBe("move");
  });
});

// ---------------------------------------------------------------------------
// VKB-C03：拖入画布生成最小合法默认配置（AC-03 / AC-04）
// ---------------------------------------------------------------------------

describe("VKB-C03: 拖入画布生成最小合法默认配置（AC-03/AC-04）", () => {
  it("onDrop vector_knowledge_base 时创建节点并携带最小合法配置", () => {
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
          key === "application/node-type" ? "vector_knowledge_base" : "",
        ),
        dropEffect: "",
      },
    });

    pane.dispatchEvent(dropEvent);
    expect(onStateChange).toHaveBeenCalled();
    const newState = applyCapturedUpdate(onStateChange.mock.calls, state);
    expect(newState.nodes).toHaveLength(1);
    expect(newState.nodes[0].type).toBe("vector_knowledge_base");
    // 默认显示名与 NodePalette 模板同源（DEFAULT_NODE_NAMES）
    expect(newState.nodes[0].data.name).toBe("向量知识库");
    expect(newState.nodes[0].data.node_type).toBe("vector_knowledge_base");
    // 最小合法配置（AC-04）：知识库标识留空待填、manual 录入、top_k=5、
    // embedding 以 api 模式占位、rerank 未配置
    const configuration = newState.nodes[0].data.configuration;
    expect(isVectorKnowledgeBaseConfig(configuration)).toBe(true);
    if (!isVectorKnowledgeBaseConfig(configuration)) {
      throw new Error("expected vector_knowledge_base configuration");
    }
    expect(configuration.knowledge_base_id).toBe("");
    expect(configuration.query_mode).toBe("manual");
    expect(configuration.top_k).toBe(5);
    expect(configuration.embedding_model).toEqual({
      mode: "api",
      model_identifier: "",
    });
    expect(configuration.rerank_model).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// VKB-C04：深色令牌双轨键补齐（AC-43）
// ---------------------------------------------------------------------------

describe("VKB-C04: 深色令牌双轨键补齐（AC-43）", () => {
  it("darkTokens.typeFill / typeBorder 含 vector_knowledge_base 降饱和/提亮变体", () => {
    expect(darkTokens.typeFill.vector_knowledge_base).toBe("#1a3a22");
    expect(darkTokens.typeBorder.vector_knowledge_base).toBe("#4caf50");
  });
});

// ---------------------------------------------------------------------------
// VKB-C05 / VKB-C06：节点视图深浅双主题渲染（AC-43）
// ---------------------------------------------------------------------------

describe("VKB-C05: 节点视图浅色渲染完整可见（AC-43）", () => {
  it("浅色主题：#e8f5e9 填充 + #2e7d32 描边 + 左右 Handle + 类型标签", () => {
    const { container } = render(
      <ReactFlowProvider>
        <VectorKnowledgeBaseNodeView data={{ name: "知识库检索" }} />
      </ReactFlowProvider>,
    );
    const node = container.querySelector('[data-testid="node-shell"]') as HTMLElement;
    expect(node).not.toBeNull();
    expect(node.querySelector('[data-testid="node-fill"]')).toHaveStyle({
      background: "#e8f5e9",
    });
    expect(node.querySelector('[data-testid="node-outline"]')).toHaveStyle({
      background: "#2e7d32",
    });
    expect(node.textContent).toContain("知识库检索");
    expect(node.textContent).toContain("向量知识库");
    expect(container.querySelectorAll(".react-flow__handle").length).toBe(2);
  });
});

describe("VKB-C06: 节点视图深色渲染完整可见（AC-43）", () => {
  it("深色主题：darkTokens.typeFill / typeBorder 取色", () => {
    mockTheme = "dark";
    const { container } = render(
      <ReactFlowProvider>
        <VectorKnowledgeBaseNodeView data={{ name: "知识库检索" }} />
      </ReactFlowProvider>,
    );
    const node = container.querySelector('[data-testid="node-shell"]') as HTMLElement;
    expect(node.querySelector('[data-testid="node-fill"]')).toHaveStyle({
      background: "#1a3a22",
    });
    expect(node.querySelector('[data-testid="node-outline"]')).toHaveStyle({
      background: "#4caf50",
    });
  });
});
