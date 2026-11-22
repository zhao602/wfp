// =============================================================================
// 配置面板运行时宽容守卫测试（canvas/__tests__/NodeConfigurationPanel.runtimeGuard.test.tsx）
//
// 执行：cd web && npx vitest run src/canvas/__tests__/NodeConfigurationPanel.runtimeGuard.test.tsx
//
// 覆盖（fix_agent_null_length 隐患 L-09 / L-10 / DC-05，五要素映射）：
//   L-09-a dynamic_injector node_templates=null → 面板渲染不崩溃、模板列表空态、可新增
//   L-09-b node_templates 键缺失（undefined）→ 同口径
//   L-09-c 合法对照：非空模板清单渲染「模板 1」行（既有功能零回归）
//   L-10-a vkb 写入模式 write_chunk_params.hierarchy=null → 修改分片长度不崩溃、回写成立
//   L-10-b 合法对照：hierarchy=["chapter"] 修改分片长度 → hierarchy 保真回写
// =============================================================================

import { describe, it, expect, vi } from "vitest";
import { render, fireEvent } from "@testing-library/react";
import NodeConfigurationPanel from "../NodeConfigurationPanel";
import type {
  CanvasNode,
  CanvasNodeData,
  WorkflowCanvasState,
} from "../canvasTypes";
import type {
  NodeConfiguration,
  DynamicInjectorNodeConfiguration,
  VectorKnowledgeBaseNodeConfiguration,
  WriteChunkParams,
} from "../../workflow-definition/definitionTypes";

// 主题 mock：保留真实 darkTokens 供 <style> 插值（atomic/hints 同款约定）
let mockTheme: "light" | "dark" = "light";
vi.mock("../../theme/themeMode", async () => {
  const actual = await vi.importActual<typeof import("../../theme/themeMode")>(
    "../../theme/themeMode",
  );
  return { ...actual, useThemeMode: () => mockTheme };
});

// ---------------------------------------------------------------------------
// 测试数据工厂
// ---------------------------------------------------------------------------

/** 构造面板节点数据 */
function makeNode(configuration: NodeConfiguration): CanvasNodeData {
  return {
    node_id: `node-${configuration.node_type}`,
    name: `节点-${configuration.node_type}`,
    node_type: configuration.node_type,
    configuration,
  };
}

/** 构造画布节点壳 */
function makeCanvasNode(data: CanvasNodeData): CanvasNode {
  return {
    id: data.node_id,
    type: data.node_type,
    position: { x: 0, y: 0 },
    data,
  };
}

/** 捕获式渲染：onCanvasStateChange 收集回写，latestState 还原最新画布状态 */
function renderCapturingPanel(config: NodeConfiguration) {
  const onCanvasStateChange = vi.fn();
  const node = makeNode(config);
  const baseState: WorkflowCanvasState = {
    nodes: [makeCanvasNode(node)],
    edges: [],
  };
  const rendered = render(
    <NodeConfigurationPanel
      selectedNode={node}
      canvasState={baseState}
      onCanvasStateChange={onCanvasStateChange}
    />,
  );
  const latestState = (): WorkflowCanvasState => {
    expect(onCanvasStateChange).toHaveBeenCalled();
    const update = onCanvasStateChange.mock.calls[
      onCanvasStateChange.mock.calls.length - 1
    ][0] as WorkflowCanvasState | ((prev: WorkflowCanvasState) => WorkflowCanvasState);
    return typeof update === "function" ? update(baseState) : update;
  };
  return { ...rendered, onCanvasStateChange, baseState, latestState };
}

// ---------------------------------------------------------------------------
// L-09 DynamicInjectorConfigForm node_templates 空态渲染
// ---------------------------------------------------------------------------

describe("L-09 dynamic_injector node_templates 面板渲染守卫（fix_agent_null_length）", () => {
  const base = {
    node_type: "dynamic_injector" as const,
    injection_condition: "items",
    max_injection_count: 5,
  };

  it("L-09-a node_templates=null：渲染不崩溃、模板列表空态、新增按钮可用", () => {
    const config = {
      ...base,
      node_templates: null,
    } as unknown as DynamicInjectorNodeConfiguration;
    const { container } = renderCapturingPanel(config);
    expect(container.textContent).toContain("模板节点列表");
    expect(container.textContent).not.toContain("模板 1");
    const addButton = Array.from(container.querySelectorAll("button")).find((btn) =>
      btn.textContent?.includes("+ 新增模板"),
    ) as HTMLButtonElement | undefined;
    expect(addButton).toBeTruthy();
    expect(addButton.disabled).toBe(false);
  });

  it("L-09-b node_templates 键缺失（undefined）：同口径安全渲染", () => {
    const config = base as unknown as DynamicInjectorNodeConfiguration;
    const { container } = renderCapturingPanel(config);
    expect(container.textContent).toContain("模板节点列表");
    expect(container.textContent).not.toContain("模板 1");
  });

  it("L-09-c 合法对照：非空模板清单渲染「模板 1」行", () => {
    const config: DynamicInjectorNodeConfiguration = {
      ...base,
      node_templates: [
        {
          template_id: "tpl-1",
          node_type: "agent",
          name: "模板-1",
          configuration: {},
        },
      ],
    };
    const { container } = renderCapturingPanel(config);
    expect(container.textContent).toContain("模板 1");
  });
});

// ---------------------------------------------------------------------------
// L-10 WriteChunkParamsFields hierarchy 真值守卫（INV-01 违规修复）
// ---------------------------------------------------------------------------

describe("L-10 vkb write_chunk_params hierarchy 守卫（fix_agent_null_length）", () => {
  /** 写入模式 vkb 配置工厂 */
  function makeWriteConfig(
    writeChunkParams: unknown,
  ): VectorKnowledgeBaseNodeConfiguration {
    return {
      node_type: "vector_knowledge_base",
      knowledge_base_id: "kb_main",
      query_mode: "manual",
      top_k: 5,
      mode: "write",
      write_chunk_params: writeChunkParams as WriteChunkParams | undefined,
    };
  }

  /** 定位分片长度输入框（placeholder 锚点） */
  function findChunkLengthInput(container: HTMLElement): HTMLInputElement {
    const input = container.querySelector(
      "input[placeholder*='分片长度上限']",
    ) as HTMLInputElement | null;
    expect(input).toBeTruthy();
    return input as HTMLInputElement;
  }

  it("L-10-a hierarchy=null：修改分片长度不崩溃、write_chunk_params 回写成立（null 不再穿透 !== undefined 判空）", () => {
    const config = makeWriteConfig({ hierarchy: null });
    const { container, latestState } = renderCapturingPanel(config);
    const input = findChunkLengthInput(container);
    fireEvent.change(input, { target: { value: "600" } });
    const next = latestState();
    const nextConfig = next.nodes[0].data.configuration as VectorKnowledgeBaseNodeConfiguration;
    expect(nextConfig.write_chunk_params?.max_chunk_length).toBe(600);
  });

  it("L-10-b 合法对照：hierarchy=['chapter'] 修改分片长度 → hierarchy 保真回写", () => {
    const config = makeWriteConfig({ hierarchy: ["chapter"] });
    const { container, latestState } = renderCapturingPanel(config);
    const input = findChunkLengthInput(container);
    fireEvent.change(input, { target: { value: "600" } });
    const next = latestState();
    const nextConfig = next.nodes[0].data.configuration as VectorKnowledgeBaseNodeConfiguration;
    expect(nextConfig.write_chunk_params?.hierarchy).toEqual(["chapter"]);
    expect(nextConfig.write_chunk_params?.max_chunk_length).toBe(600);
  });
});
