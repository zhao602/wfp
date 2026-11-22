// =============================================================================
// 模板应用纯函数（web/src/canvas/applyAgentTemplate.ts）
//
// 职责：将 Agent 模板应用到画布 —— 纯函数、零副作用、不可变更新（DC-06）：
//   · single_node：目标 Agent 节点合并预设（同名键模板优先、其余字段不变，
//     available_tools 去重保序合并）；未指定目标且画布恰一个 Agent 节点 → 合并；
//     否则新建 Agent 节点（默认配置与预设保序合一）；
//   · recipe：批量实例化 nodes + edges（local_ref → node_id 映射，
//     node_id = ${template_id}_${local_ref}_${nonce}），网格偏移布局避免重叠；
//   · 预设键白名单防御过滤（排除 sandbox_policy_id，INV-D10/INV-D08）；
//   · max_tool_rounds 缺省语义：模板未声明该键 → 不写入字段（保持 undefined → 回落全局 5，AC-13 规则 3）。
//   · context_strategy（差异化扩展）：白名单新增键，四值守卫通过才写入节点配置。
//
// 设计意图：
//   1. 不经过后端写接口（后端模板端点恒只读，DC-06）；
//   2. 可单测、零副作用（AC-14 机检基础）；
//   3. 入参不可变：任何分支均返回新对象/新数组，不修改传入 canvasState / template。
// =============================================================================

import type { CanvasEdge, CanvasNode, WorkflowCanvasState } from "./canvasTypes";
import type {
  AgentInputEntry,
  AgentNodeConfiguration,
  NodeConfiguration,
  NodeType,
} from "../workflow-definition/definitionTypes";
import { isAgentConfig } from "../workflow-definition/definitionTypes";
import { normalizeStringArrayField } from "../workflow-definition/agentConfigNormalization";
import type { AgentTemplate } from "../market/marketTypes";

/** 模板应用结果（DC-06 纯函数契约） */
export interface ApplyTemplateResult {
  /** 应用后的新画布状态（不可变更新，入参不变） */
  canvasState: WorkflowCanvasState;
  /** 是否成功应用 */
  applied: boolean;
  /** 新建节点 id 全量列表（recipe 批量 / single_node 新建） */
  createdNodeIds: string[];
  /** 被合并写入的目标节点 id（single_node 合并分支非空） */
  replacedNodeId: string | null;
  /** 附加说明（预设越界键过滤 / applied=false 的原因） */
  reason?: string;
}

/** 预设键白名单（与后端 agent_templates.py TEMPLATE_PRESET_ALLOWED_KEYS 对齐，排除 sandbox_policy_id，T16.5/INV-D10/INV-D08；context_strategy 为差异化扩展新增键） */
const TEMPLATE_PRESET_ALLOWED_KEYS: ReadonlySet<string> = new Set([
  "model_identifier",
  "system_prompt",
  "available_tools",
  "max_tool_rounds",
  "output_max_retries",
  "output_schema",
  "inputs",
  "context_strategy",
]);

/** context_strategy 合法取值守卫（与后端 ContextWindowStrategyType 四值对齐） */
const CONTEXT_STRATEGY_VALUES: ReadonlySet<string> = new Set([
  "sliding_window",
  "summary_compression",
  "importance_ranking",
  "structural_lossless",
]);

/** 网格布局参数（DC-06：新节点网格偏移布局避免重叠） */
const RECIPE_GRID = {
  originX: 80,
  originY: 80,
  stepX: 220,
  stepY: 150,
  columns: 3,
} as const;

/** 新建 Agent 节点默认配置（与 WorkflowCanvas.createDefaultConfiguration("agent") 语义同源，Z-14：不改既有工厂） */
const DEFAULT_AGENT_CONFIGURATION: AgentNodeConfiguration = {
  node_type: "agent",
  model_identifier: "",
  system_prompt: "",
  available_tools: [],
};

/** Agent 输入选择集条目守卫（unified_node_input：reference/alias 结构） */
function isAgentInputEntry(value: unknown): value is AgentInputEntry {
  return (
    typeof value === "object" &&
    value !== null &&
    "reference" in value &&
    typeof value.reference === "string"
  );
}

/** 字符串数组守卫 */
function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

/** string→string 记录守卫（output_schema） */
function isStringRecord(value: unknown): value is Record<string, string> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return false;
  }
  return Object.values(value).every((item) => typeof item === "string");
}

/** 预设白名单防御过滤（T16.5）：越界键剔除（不写入画布）并返回被过滤键列表 */
function filterPresetKeys(
  preset: Record<string, unknown>,
): { allowed: Record<string, unknown>; dropped: string[] } {
  const allowed: Record<string, unknown> = {};
  const dropped: string[] = [];
  for (const key of Object.keys(preset)) {
    if (TEMPLATE_PRESET_ALLOWED_KEYS.has(key)) {
      allowed[key] = preset[key];
    } else {
      dropped.push(key);
    }
  }
  return { allowed, dropped };
}

/** available_tools 去重保序合并（既有顺序在前，对齐后端 _dedupe_available_tools 语义，AC-14） */
function mergeAvailableTools(existing: string[], additions: string[]): string[] {
  const merged: string[] = [...existing];
  for (const tool of additions) {
    if (!merged.includes(tool)) {
      merged.push(tool);
    }
  }
  return merged;
}

/**
 * 预设合并写入 Agent 节点配置（T16.2/T16.3）：
 * 同名键模板优先、其余字段不变；available_tools 去重保序合并；
 * max_tool_rounds / output_max_retries 未声明 → 不写入（保持缺省，AC-13 规则 3）；
 * inputs 数组形态合法即写入（含空数组 = 显式无输入，unified_node_input 三态）。
 */
function mergeAgentPreset(
  base: AgentNodeConfiguration,
  preset: Record<string, unknown>,
): AgentNodeConfiguration {
  // L3 主防线（G-03，fix_agent_null_length DC-04）：base 侧 available_tools 先经归一
  //（null/undefined/非数组 → []），堵 [...null] not iterable（E2-f，fix_raw_not_iterable 同模式）
  const next: AgentNodeConfiguration = {
    ...base,
    available_tools: normalizeStringArrayField(base.available_tools),
  };
  if (typeof preset.model_identifier === "string") {
    next.model_identifier = preset.model_identifier;
  }
  if (typeof preset.system_prompt === "string") {
    next.system_prompt = preset.system_prompt;
  }
  if (isStringArray(preset.available_tools)) {
    next.available_tools = mergeAvailableTools(next.available_tools, preset.available_tools);
  }
  if (typeof preset.max_tool_rounds === "number") {
    next.max_tool_rounds = preset.max_tool_rounds;
  }
  if (typeof preset.output_max_retries === "number") {
    next.output_max_retries = preset.output_max_retries;
  }
  if (isStringRecord(preset.output_schema)) {
    next.output_schema = preset.output_schema;
  }
  if (Array.isArray(preset.inputs)) {
    next.inputs = preset.inputs.filter(isAgentInputEntry);
  }
  if (
    typeof preset.context_strategy === "string" &&
    CONTEXT_STRATEGY_VALUES.has(preset.context_strategy)
  ) {
    next.context_strategy = preset.context_strategy as AgentNodeConfiguration["context_strategy"];
  }
  return next;
}

/** 画布内容下方网格基线 Y（空画布回落原点，避免与既有节点重叠） */
function gridBaseY(canvasState: WorkflowCanvasState): number {
  if (canvasState.nodes.length === 0) {
    return RECIPE_GRID.originY;
  }
  const maxY = canvasState.nodes.reduce(
    (max, node) => Math.max(max, node.position.y),
    Number.NEGATIVE_INFINITY,
  );
  return Math.max(RECIPE_GRID.originY, maxY + RECIPE_GRID.stepY);
}

/** 画布内递增序号：最小 nonce 使候选 node_id 不与既有节点冲突（DC-06） */
function smallestNonce(isTaken: (nonce: number) => boolean): number {
  let nonce = 1;
  while (isTaken(nonce)) {
    nonce += 1;
  }
  return nonce;
}

/**
 * 配方实例化（T16.4）：批量新增 nodes + edges；
 * node_id = `${template_id}_${local_ref}_${nonce}`（local_ref → node_id 映射）；
 * 节点位置按网格偏移布局避免重叠；createdNodeIds 全量返回。
 */
function instantiateRecipe(
  canvasState: WorkflowCanvasState,
  template: AgentTemplate,
): { canvasState: WorkflowCanvasState; createdNodeIds: string[] } {
  const recipe = template.recipe;
  if (!recipe) {
    return { canvasState, createdNodeIds: [] };
  }
  const existingIds = new Set(canvasState.nodes.map((node) => node.id));
  const nonce = smallestNonce((candidate) =>
    recipe.nodes.some((node) => existingIds.has(`${template.template_id}_${node.local_ref}_${candidate}`)),
  );
  const baseY = gridBaseY(canvasState);
  const localRefToId = new Map<string, string>();
  const createdNodes: CanvasNode[] = recipe.nodes.map((node, index) => {
    const nodeId = `${template.template_id}_${node.local_ref}_${nonce}`;
    localRefToId.set(node.local_ref, nodeId);
    const column = index % RECIPE_GRID.columns;
    const row = Math.floor(index / RECIPE_GRID.columns);
    return {
      id: nodeId,
      type: node.node_type,
      position: {
        x: RECIPE_GRID.originX + column * RECIPE_GRID.stepX,
        y: baseY + row * RECIPE_GRID.stepY,
      },
      data: {
        node_id: nodeId,
        name: node.name,
        // 后端 _validate_templates 导入期校验保证 recipe 节点类型 ∈ 既有 NodeType（INV-D03）
        node_type: node.node_type as NodeType,
        // 后端导入期校验保证 recipe 节点配置复用既有配置字段（INV-D03，防御性收窄）
        configuration: node.configuration as unknown as NodeConfiguration,
      },
    };
  });
  const createdEdges: CanvasEdge[] = [];
  recipe.edges.forEach((edge, index) => {
    const source = localRefToId.get(edge.source_ref);
    const target = localRefToId.get(edge.target_ref);
    if (!source || !target) {
      return;
    }
    const edgeId = `${template.template_id}_${nonce}_edge_${index + 1}`;
    createdEdges.push({ id: edgeId, source, target, data: { edge_id: edgeId } });
  });
  return {
    canvasState: {
      nodes: [...canvasState.nodes, ...createdNodes],
      edges: [...canvasState.edges, ...createdEdges],
    },
    createdNodeIds: createdNodes.map((node) => node.id),
  };
}

/**
 * 将模板应用到画布（DC-06）：single_node → 合并/新建 Agent 节点；recipe → 批量新增节点与连线。
 * 纯函数：不修改入参，返回新 canvasState（不可变更新，INV-D13/R-6）。
 */
export function applyAgentTemplateToCanvas(
  canvasState: WorkflowCanvasState,
  template: AgentTemplate,
  targetNodeId?: string | null,
): ApplyTemplateResult {
  const { allowed: preset, dropped } = filterPresetKeys(template.preset);
  const filterReason = dropped.length > 0 ? `预设越界键已过滤：${dropped.join("、")}` : undefined;
  const optionalReason = filterReason === undefined ? {} : { reason: filterReason };

  if (template.kind === "recipe") {
    if (!template.recipe) {
      return {
        canvasState,
        applied: false,
        createdNodeIds: [],
        replacedNodeId: null,
        reason: "模板缺少 recipe 定义",
      };
    }
    const instantiated = instantiateRecipe(canvasState, template);
    return {
      canvasState: instantiated.canvasState,
      applied: true,
      createdNodeIds: instantiated.createdNodeIds,
      replacedNodeId: null,
      ...optionalReason,
    };
  }

  // single_node 目标选择规则（T16.2）：
  // ① targetNodeId 指向 agent 节点 → 合并；② 未指定且画布恰一个 agent 节点 → 合并；③ 否则新建
  let targetNode: CanvasNode | undefined;
  if (typeof targetNodeId === "string" && targetNodeId !== "") {
    const found = canvasState.nodes.find((node) => node.id === targetNodeId);
    if (found !== undefined && isAgentConfig(found.data.configuration)) {
      targetNode = found;
    }
  } else {
    const agentNodes = canvasState.nodes.filter((node) => isAgentConfig(node.data.configuration));
    if (agentNodes.length === 1) {
      targetNode = agentNodes[0];
    }
  }

  if (targetNode !== undefined && isAgentConfig(targetNode.data.configuration)) {
    const merged = mergeAgentPreset(targetNode.data.configuration, preset);
    const nextNodes = canvasState.nodes.map((node) =>
      node.id === targetNode.id
        ? { ...node, data: { ...node.data, configuration: merged } }
        : node,
    );
    return {
      canvasState: { nodes: nextNodes, edges: canvasState.edges },
      applied: true,
      createdNodeIds: [],
      replacedNodeId: targetNode.id,
      ...optionalReason,
    };
  }

  // 新建 agent 节点：默认配置与预设保序合一（T16.2 ③）
  const existingIds = new Set(canvasState.nodes.map((node) => node.id));
  const nonce = smallestNonce((candidate) => existingIds.has(`${template.template_id}_agent_${candidate}`));
  const nodeId = `${template.template_id}_agent_${nonce}`;
  const configuration = mergeAgentPreset(DEFAULT_AGENT_CONFIGURATION, preset);
  const newNode: CanvasNode = {
    id: nodeId,
    type: "agent",
    position: {
      x: RECIPE_GRID.originX + (canvasState.nodes.length % RECIPE_GRID.columns) * RECIPE_GRID.stepX,
      y: gridBaseY(canvasState),
    },
    data: {
      node_id: nodeId,
      name: template.name,
      node_type: "agent",
      configuration,
    },
  };
  return {
    canvasState: { nodes: [...canvasState.nodes, newNode], edges: canvasState.edges },
    applied: true,
    createdNodeIds: [nodeId],
    replacedNodeId: null,
    ...optionalReason,
  };
}
