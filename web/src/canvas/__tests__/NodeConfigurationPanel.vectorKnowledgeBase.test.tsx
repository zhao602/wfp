// =============================================================================
// 向量知识库节点配置面板测试（canvas/__tests__/NodeConfigurationPanel.vectorKnowledgeBase.test.tsx）
//
// 留待非沙箱环境执行：cd web && npx vitest run NodeConfigurationPanel.vectorKnowledgeBase
//
// 覆盖用例（design.md §4.1.2 批次 4-b 面板组，对应 tasks.md T5.1/T5.2/T5.3 验收）：
//   VKB-P01 选中向量知识库节点渲染全部配置项与默认态（AC-05）
//   VKB-P02 query_mode 切换互斥可见，切换不清理已录入值（AC-06/FR-07）
//   VKB-P03 可见性谓词真值表与缺项降级（T5.2/CHG-17/DC-03）
//   VKB-P04 提示单一来源：节点级 + 7 字段级 helper/placeholder（AC-44）
//   VKB-P05 输出契约展示片段集合且可引用（无"不可引用"徽标，AC-38）
//   VKB-P06 重排开关：开启注入默认 api 配置、关闭置 undefined（AC-05/FR-25）
//   VKB-P07 embedding 区只读展示绑定模型摘要，无加载方式互斥切换（AC-46/DC-08）
//   VKB-P08 top_k 输入钳制 1~100 与函数式原子回写（AC-05）
//   VKB-P09 reference 模式上游候选仅列上游可达且可引用的命名输出（AC-42，T7.1）
// =============================================================================

import { useState } from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, cleanup, fireEvent, waitFor } from "@testing-library/react";
import NodeConfigurationPanel from "../NodeConfigurationPanel";
import type {
  CanvasNode,
  CanvasNodeData,
  WorkflowCanvasState,
} from "../canvasTypes";
import type {
  NodeConfiguration,
  VectorKnowledgeBaseNodeConfiguration,
} from "../../workflow-definition/definitionTypes";
import { isVectorKnowledgeBaseFieldVisible } from "../nodeConfigFieldVisibility";
import {
  buildHintId,
  getNodeHint,
  resolveFieldHint,
} from "../nodeConfigHints";
import { kbManagementApiClient } from "../../api/kbManagementApiClient";
import type { KnowledgeBaseView } from "../../knowledge_base/kbManagementTypes";

// 主题 mock：保留真实 darkTokens 导出，仅钉住 useThemeMode 返回值
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

/** 向量知识库配置工厂（manual 模式最小合法配置 + 可覆盖项） */
function makeVectorConfig(
  overrides: Partial<VectorKnowledgeBaseNodeConfiguration> = {},
): VectorKnowledgeBaseNodeConfiguration {
  return {
    node_type: "vector_knowledge_base",
    knowledge_base_id: "kb_main",
    query_mode: "manual",
    query_value: "量子计算的基本原理",
    top_k: 5,
    embedding_model: { mode: "api", model_identifier: "emb-default" },
    ...overrides,
  };
}

/** 后台知识库视图工厂（KnowledgeBaseView，零凭据摘要） */
function makeKbView(
  kbId: string,
  name: string,
  modelIdentifier = "emb-default",
  mode: "api" | "local" = "api",
): KnowledgeBaseView {
  return {
    knowledge_base_id: kbId,
    name,
    embedding_model: {
      mode,
      model_identifier: modelIdentifier,
      allow_degraded_fallback: false,
    },
    document_count: 0,
    chunk_count: 0,
  };
}

/** 构造面板节点数据（selectedNode 与画布内 data 同引用） */
function makeNode(configuration: NodeConfiguration): CanvasNodeData {
  return {
    node_id: `node-${configuration.node_type}`,
    name: `节点-${configuration.node_type}`,
    node_type: configuration.node_type,
    configuration,
  };
}

/** 构造画布节点壳（reactflow CanvasNode） */
function makeCanvasNode(data: CanvasNodeData): CanvasNode {
  return {
    id: data.node_id,
    type: data.node_type,
    position: { x: 0, y: 0 },
    data,
  };
}

/** 渲染面板（非受控：onCanvasStateChange 为 mock） */
function renderPanelWithConfig(config: VectorKnowledgeBaseNodeConfiguration) {
  const node = makeNode(config);
  const canvasState: WorkflowCanvasState = {
    nodes: [makeCanvasNode(node)],
    edges: [],
  };
  return render(
    <NodeConfigurationPanel
      selectedNode={node}
      canvasState={canvasState}
      onCanvasStateChange={vi.fn()}
    />,
  );
}

/** 受控面板：内部持有画布状态，回写后重渲染（模拟真实接线，AC-06 状态驱动） */
function StatefulPanel({
  initialConfig,
}: {
  initialConfig: VectorKnowledgeBaseNodeConfiguration;
}): JSX.Element {
  const [state, setState] = useState<WorkflowCanvasState>(() => ({
    nodes: [makeCanvasNode(makeNode(initialConfig))],
    edges: [],
  }));
  return (
    <NodeConfigurationPanel
      selectedNode={state.nodes[0].data}
      canvasState={state}
      onCanvasStateChange={setState}
    />
  );
}

/**
 * 捕获式渲染：mock 回写收集函数式 updater，仅应用最近一次调用并返回最新画布状态。
 * 画布含 2 节点（触发器 + 向量知识库），用于原子回写断言（非目标节点/edges 引用透传）。
 */
function renderCapturingPanel(config: VectorKnowledgeBaseNodeConfiguration) {
  const onCanvasStateChange = vi.fn();
  const otherNode: CanvasNode = makeCanvasNode(
    makeNode({
      node_type: "trigger",
      trigger_type: "manual",
      input_template: {},
    }),
  );
  const vkbNode = makeCanvasNode(makeNode(config));
  const baseState: WorkflowCanvasState = {
    nodes: [otherNode, vkbNode],
    edges: [],
  };
  const rendered = render(
    <NodeConfigurationPanel
      selectedNode={vkbNode.data}
      canvasState={baseState}
      onCanvasStateChange={onCanvasStateChange}
    />,
  );
  /** 应用最近一次回写（函数式 updater 或直传值）到基准画布状态 */
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
// 面板用例
// ---------------------------------------------------------------------------

describe("向量知识库节点配置面板（T5.1 表单接线）", () => {
  beforeEach(() => {
    mockTheme = "light";
  });

  afterEach(() => {
    vi.restoreAllMocks();
    cleanup();
  });

  it("VKB-P01 选中向量知识库节点渲染全部配置项与默认态（AC-05）", async () => {
    // 后台清单 + 详情绑定（AC-47/AC-48）：面板内两组件挂载期拉取（AC-49 不缓存）
    vi.spyOn(kbManagementApiClient, "listKnowledgeBases").mockResolvedValue({
      items: [makeKbView("kb_main", "主知识库"), makeKbView("kb_docs", "文档知识库")],
      total: 2,
    });
    vi.spyOn(kbManagementApiClient, "getKnowledgeBase").mockResolvedValue(
      makeKbView("kb_main", "主知识库"),
    );
    const { container } = render(
      <StatefulPanel initialConfig={makeVectorConfig()} />,
    );

    // 知识库标识：文本输入 + 提示挂钩（保留自由文本输入，DC-08）
    const kbInput = container.querySelector(
      `input[aria-describedby="${buildHintId("vector_knowledge_base", "knowledge_base_id")}"]`,
    ) as HTMLInputElement;
    expect(kbInput).not.toBeNull();
    expect(kbInput.value).toBe("kb_main");

    // 查询录入模式：下拉 manual/reference
    const modeSelect = container.querySelector(
      `select[aria-describedby="${buildHintId("vector_knowledge_base", "query_mode")}"]`,
    ) as HTMLSelectElement;
    expect(modeSelect).not.toBeNull();
    expect(modeSelect.value).toBe("manual");

    // 查询文本：manual 模式下 textarea 可见
    const queryTextarea = container.querySelector(
      `textarea[aria-describedby="${buildHintId("vector_knowledge_base", "query_value")}"]`,
    ) as HTMLTextAreaElement;
    expect(queryTextarea).not.toBeNull();
    expect(queryTextarea.value).toBe("量子计算的基本原理");

    // 召回数量上限：数值输入
    const topKInput = container.querySelector(
      `input[aria-describedby="${buildHintId("vector_knowledge_base", "top_k")}"]`,
    ) as HTMLInputElement;
    expect(topKInput).not.toBeNull();
    expect(topKInput.type).toBe("number");
    expect(topKInput.value).toBe("5");

    // 向量化模型区只读化（CHG-33/DC-08）：无加载方式下拉与 api/local 互斥子表单（不可编辑）
    expect(
      container.querySelector(
        `select[aria-describedby="${buildHintId("vector_knowledge_base", "embedding_model")}"]`,
      ),
    ).toBeNull();
    expect(container.textContent).not.toContain("服务地址");
    expect(container.textContent).not.toContain("超时（秒）");
    expect(container.textContent).not.toContain("模型路径");

    // 绑定模型摘要只读展示（AC-48）：mode + model_identifier
    await waitFor(() => {
      expect(container.querySelector(".wfp-kbb-summary")).not.toBeNull();
    });
    expect(container.querySelector(".wfp-kbb-summary")?.textContent).toContain("api");
    expect(container.querySelector(".wfp-kbb-summary")?.textContent).toContain("emb-default");

    // 知识库候选清单（AC-47）：后台清单可选，当前值高亮
    await waitFor(() => {
      expect(container.querySelectorAll(".wfp-kbsl-card")).toHaveLength(2);
    });
    const cards = container.querySelectorAll(".wfp-kbsl-card");
    expect(cards[0].className).toContain("wfp-kbsl-card-selected");
    expect(cards[0].textContent).toContain("kb_main");
    expect(cards[0].textContent).toContain("主知识库");

    // 从后台清单选择：单击候选卡写回 knowledge_base_id（受控单值）
    fireEvent.click(cards[1]);
    await waitFor(() => {
      expect(
        (
          container.querySelector(
            `input[aria-describedby="${buildHintId("vector_knowledge_base", "knowledge_base_id")}"]`,
          ) as HTMLInputElement
        ).value,
      ).toBe("kb_docs");
    });

    // 重排模型默认关闭：仅"启用重排"一个复选框且未勾选，rerank 子表单不渲染
    const checkboxes = container.querySelectorAll('input[type="checkbox"]');
    expect(checkboxes).toHaveLength(1);
    expect((checkboxes[0] as HTMLInputElement).checked).toBe(false);
    expect(
      container.querySelector(`#${buildHintId("vector_knowledge_base", "rerank_model")}`),
    ).toBeNull();
  });

  it("VKB-P02 query_mode 切换互斥可见，切换不清理已录入值（AC-06/FR-07）", () => {
    const { container } = render(
      <StatefulPanel
        initialConfig={makeVectorConfig({
          query_value: "量子计算的基本原理",
          query_reference: "upstream_output",
        })}
      />,
    );
    const modeSelect = () =>
      container.querySelector(
        `select[aria-describedby="${buildHintId("vector_knowledge_base", "query_mode")}"]`,
      ) as HTMLSelectElement;

    // 初始 manual：查询文本可见、查询引用隐藏
    expect(
      container.querySelector(`#${buildHintId("vector_knowledge_base", "query_value")}`),
    ).not.toBeNull();
    expect(
      container.querySelector(`#${buildHintId("vector_knowledge_base", "query_reference")}`),
    ).toBeNull();

    // 切到 reference：互斥翻转
    fireEvent.change(modeSelect(), { target: { value: "reference" } });
    expect(
      container.querySelector(`#${buildHintId("vector_knowledge_base", "query_value")}`),
    ).toBeNull();
    expect(
      container.querySelector(`#${buildHintId("vector_knowledge_base", "query_reference")}`),
    ).not.toBeNull();
    // 查询引用控件复用既有变量引用选择器（AC-42）
    expect(container.querySelector("input.wfp-vrs-input")).not.toBeNull();

    // 切回 manual：查询文本重新可见，已录入值保留（FR-07：双字段可共存，仅激活方参与）
    fireEvent.change(modeSelect(), { target: { value: "manual" } });
    const queryTextarea = container.querySelector(
      `textarea[aria-describedby="${buildHintId("vector_knowledge_base", "query_value")}"]`,
    ) as HTMLTextAreaElement;
    expect(queryTextarea).not.toBeNull();
    expect(queryTextarea.value).toBe("量子计算的基本原理");
  });

  it("VKB-P03 可见性谓词真值表与缺项降级（T5.2/CHG-17/DC-03）", () => {
    const manual = makeVectorConfig({ query_mode: "manual" });
    const reference = makeVectorConfig({ query_mode: "reference" });

    // manual：query_value 可见、query_reference 隐藏
    expect(isVectorKnowledgeBaseFieldVisible("query_value", manual)).toBe(true);
    expect(isVectorKnowledgeBaseFieldVisible("query_reference", manual)).toBe(false);

    // reference：互斥翻转
    expect(isVectorKnowledgeBaseFieldVisible("query_value", reference)).toBe(false);
    expect(isVectorKnowledgeBaseFieldVisible("query_reference", reference)).toBe(true);

    // 未声明字段默认可见（安全降级，DC-03）
    expect(isVectorKnowledgeBaseFieldVisible("knowledge_base_id", manual)).toBe(true);
    expect(isVectorKnowledgeBaseFieldVisible("top_k", reference)).toBe(true);
    expect(isVectorKnowledgeBaseFieldVisible("任意未声明字段", manual)).toBe(true);
  });

  it("VKB-P04 提示单一来源：节点级 + 7 字段级提示与 NODE_HINTS 渲染一致（AC-44）", () => {
    // 7 个字段提示全部登记（含仅特定模式可见的 query_value/query_reference）
    const allFields = [
      "knowledge_base_id",
      "query_mode",
      "query_value",
      "query_reference",
      "top_k",
      "embedding_model",
      "rerank_model",
    ] as const;
    for (const field of allFields) {
      const hint = resolveFieldHint("vector_knowledge_base", field);
      expect(hint?.helper, `字段 ${field} 提示缺失`).toBeTruthy();
    }

    // 面板渲染：节点级提示
    const { container } = renderPanelWithConfig(makeVectorConfig());
    const nodeHintEl = container.querySelector(".wfp-ncp-node-hint");
    expect(nodeHintEl?.textContent).toBe(getNodeHint("vector_knowledge_base"));

    // manual 模式下可见字段的提示文案与单一来源一致
    const visibleFields = [
      "knowledge_base_id",
      "query_mode",
      "query_value",
      "top_k",
      "embedding_model",
    ] as const;
    for (const field of visibleFields) {
      const hint = resolveFieldHint("vector_knowledge_base", field);
      const el = container.querySelector(`#${buildHintId("vector_knowledge_base", field)}`);
      expect(el?.textContent).toBe(hint?.helper);
    }

    // 文本输入/多行文本占位符透传
    const kbInput = container.querySelector(
      `input[aria-describedby="${buildHintId("vector_knowledge_base", "knowledge_base_id")}"]`,
    ) as HTMLInputElement;
    expect(kbInput.placeholder).toBe(
      resolveFieldHint("vector_knowledge_base", "knowledge_base_id")?.placeholder,
    );
    const queryTextarea = container.querySelector(
      `textarea[aria-describedby="${buildHintId("vector_knowledge_base", "query_value")}"]`,
    ) as HTMLTextAreaElement;
    expect(queryTextarea.placeholder).toBe(
      resolveFieldHint("vector_knowledge_base", "query_value")?.placeholder,
    );

    // reference 模式：query_reference 提示渲染（自定义控件不挂 aria，提示区仍渲染）
    const { container: refContainer } = renderPanelWithConfig(
      makeVectorConfig({
        query_mode: "reference",
        query_value: undefined,
        query_reference: "upstream_output",
      }),
    );
    expect(
      refContainer.querySelector(`#${buildHintId("vector_knowledge_base", "query_reference")}`)
        ?.textContent,
    ).toBe(resolveFieldHint("vector_knowledge_base", "query_reference")?.helper);
  });

  it("VKB-P05 输出契约展示片段集合（文本片段列表）且无不可引用徽标（AC-38）", () => {
    const { container } = renderPanelWithConfig(makeVectorConfig());

    const category = container.querySelector(".wfp-ncp-contract-category");
    expect(category?.textContent).toBe("片段集合（文本片段列表）");

    // referable=true：无"不可引用"徽标
    expect(container.querySelector(".wfp-ncp-contract-badge--noref")).toBeNull();

    // 契约详情含 top_k 说明
    const detail = container.querySelector(".wfp-ncp-contract-detail");
    expect(detail?.textContent).toContain("top_k");
  });

  it("VKB-P06 重排开关：开启注入默认 api 配置、关闭置 undefined（AC-05/FR-25）", () => {
    // 关闭→开启：注入 { mode: "api", model_identifier: "" }
    const on = renderCapturingPanel(makeVectorConfig());
    const toggleOn = on.container.querySelector(
      'input[type="checkbox"]',
    ) as HTMLInputElement;
    expect(toggleOn.checked).toBe(false);
    fireEvent.click(toggleOn);
    const enabled = on.latestState().nodes[1].data
      .configuration as VectorKnowledgeBaseNodeConfiguration;
    expect(enabled.rerank_model).toEqual({ mode: "api", model_identifier: "" });

    // 开启态渲染 rerank_model 提示（与单一来源一致）
    const { container } = render(
      <StatefulPanel
        initialConfig={makeVectorConfig({
          rerank_model: { mode: "api", model_identifier: "rerank-1" },
        })}
      />,
    );
    const rerankHintEl = container.querySelector(
      `#${buildHintId("vector_knowledge_base", "rerank_model")}`,
    );
    expect(rerankHintEl?.textContent).toBe(
      resolveFieldHint("vector_knowledge_base", "rerank_model")?.helper,
    );

    // 开启→关闭：rerank_model 置 undefined
    const off = renderCapturingPanel(
      makeVectorConfig({
        rerank_model: {
          mode: "local",
          model_identifier: "rerank-1",
          model_path: "/models/rerank",
          device: "gpu",
        },
      }),
    );
    const checkboxes = off.container.querySelectorAll('input[type="checkbox"]');
    expect(checkboxes).toHaveLength(2); // 启用重排 + 降级兜底（local 模式）
    fireEvent.click(checkboxes[0] as HTMLInputElement); // 第一枚为"启用重排"
    const disabled = off.latestState().nodes[1].data
      .configuration as VectorKnowledgeBaseNodeConfiguration;
    expect(disabled.rerank_model).toBeUndefined();
  });

  it("VKB-P07 embedding 区只读展示绑定模型摘要，无加载方式互斥切换（AC-46/DC-08）", async () => {
    // 后台绑定（AC-48）：绑定优先展示；旧配置遗留 embedding_model 触发覆盖提示
    vi.spyOn(kbManagementApiClient, "listKnowledgeBases").mockResolvedValue({
      items: [makeKbView("kb_main", "主知识库")],
      total: 1,
    });
    vi.spyOn(kbManagementApiClient, "getKnowledgeBase").mockResolvedValue(
      makeKbView("kb_main", "主知识库", "emb-bound"),
    );
    const { container } = render(
      <StatefulPanel
        initialConfig={makeVectorConfig({
          embedding_model: {
            mode: "api",
            model_identifier: "emb-1",
            base_url: "https://emb.example.com",
            timeout_seconds: 30,
          },
        })}
      />,
    );

    // 绑定模型摘要只读展示（AC-48）：mode + model_identifier（绑定值，非遗留值）
    await waitFor(() => {
      expect(container.querySelector(".wfp-kbb-summary")).not.toBeNull();
    });
    expect(container.querySelector(".wfp-kbb-summary")?.textContent).toContain("api");
    expect(container.querySelector(".wfp-kbb-summary")?.textContent).toContain("emb-bound");
    // 旧配置携带 embedding_model → "已由知识库绑定覆盖"冲突提示（AC-48 绑定优先）
    expect(container.textContent).toContain("已由知识库绑定覆盖");

    // 无加载方式下拉与 api↔local 互斥子表单（不可编辑，无切换控件）
    expect(
      container.querySelector(
        `select[aria-describedby="${buildHintId("vector_knowledge_base", "embedding_model")}"]`,
      ),
    ).toBeNull();
    expect(container.textContent).not.toContain("服务地址");
    expect(container.textContent).not.toContain("超时（秒）");
    expect(container.textContent).not.toContain("模型路径");
    expect(container.textContent).not.toContain("推理设备");
    expect(container.textContent).not.toContain("依赖缺失时允许降级兜底");
  });

  it("VKB-P08 top_k 输入钳制 1~100 并函数式原子回写（AC-05）", () => {
    const { container, latestState, baseState } = renderCapturingPanel(
      makeVectorConfig(),
    );
    const topKInput = container.querySelector(
      `input[aria-describedby="${buildHintId("vector_knowledge_base", "top_k")}"]`,
    ) as HTMLInputElement;

    const latestTopK = (): number =>
      (latestState().nodes[1].data.configuration as VectorKnowledgeBaseNodeConfiguration)
        .top_k;

    // 正常值
    fireEvent.change(topKInput, { target: { value: "10" } });
    expect(latestTopK()).toBe(10);

    // 上界钳制
    fireEvent.change(topKInput, { target: { value: "500" } });
    expect(latestTopK()).toBe(100);

    // 下界钳制
    fireEvent.change(topKInput, { target: { value: "0" } });
    expect(latestTopK()).toBe(1);

    // 四舍五入
    fireEvent.change(topKInput, { target: { value: "12.6" } });
    const finalState = latestState();
    const finalConfig = finalState.nodes[1].data
      .configuration as VectorKnowledgeBaseNodeConfiguration;
    expect(finalConfig.top_k).toBe(13);

    // 原子回写：非目标节点与 edges 原引用透传，其余配置字段不变
    expect(finalState.nodes[0]).toBe(baseState.nodes[0]);
    expect(finalState.edges).toBe(baseState.edges);
    expect(finalConfig.knowledge_base_id).toBe("kb_main");
    expect(finalConfig.query_mode).toBe("manual");
    expect(finalConfig.query_value).toBe("量子计算的基本原理");
    expect(finalConfig.embedding_model).toEqual({
      mode: "api",
      model_identifier: "emb-default",
    });
  });

  it("VKB-P09 reference 模式上游候选仅列上游可达且可引用的命名输出（AC-42）", () => {
    // 画布三节点：上游触发器（命名输出）→ 向量知识库（目标）；远端智能体不可达
    const vkbConfig = makeVectorConfig({
      query_mode: "reference",
      query_value: undefined,
      query_reference: "",
    });
    const vkbData = makeNode(vkbConfig);
    const triggerData: CanvasNodeData = {
      node_id: "trig",
      name: "手动触发",
      node_type: "trigger",
      configuration: {
        node_type: "trigger",
        trigger_type: "manual",
        input_template: {},
      },
      output_name: "trigger_output",
    };
    const farData: CanvasNodeData = {
      node_id: "far_agent",
      name: "远端智能体",
      node_type: "agent",
      configuration: {
        node_type: "agent",
        model_identifier: "m",
        system_prompt: "",
        available_tools: [],
      },
      output_name: "far_output",
    };
    const canvasState: WorkflowCanvasState = {
      nodes: [makeCanvasNode(triggerData), makeCanvasNode(farData), makeCanvasNode(vkbData)],
      edges: [{ id: "e-trig-vkb", source: "trig", target: vkbData.node_id }],
    };
    const { container } = render(
      <NodeConfigurationPanel
        selectedNode={vkbData}
        canvasState={canvasState}
        onCanvasStateChange={vi.fn()}
      />,
    );

    // reference 模式渲染查询引用选择器；展开候选面板
    const toggle = container.querySelector(".wfp-vrs-toggle") as HTMLButtonElement;
    expect(toggle).not.toBeNull();
    fireEvent.click(toggle);

    // 仅上游可达的命名输出入列；不可达输出不入列（AC-42）
    const optionNames = Array.from(
      container.querySelectorAll(".wfp-vrs-option-name"),
    ).map((el) => el.textContent);
    expect(optionNames).toEqual(["trigger_output"]);
    expect(container.textContent).not.toContain("far_output");
  });
});
