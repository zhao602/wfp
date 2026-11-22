// =============================================================================
// 画布状态兼容映射（src/canvas/canvasStateMigrator.ts）
//
// 职责：将旧画布状态（旧枚举值 / 旧字段名 / 旧结构）迁移到对齐后端 schema 的新状态。
// 设计意图（DC-10）：
//   1. 纯函数设计，不抛异常，不修改输入；
//   2. 按 node_type 分派，仅迁移需要转换的四类节点（control_flow / dynamic_injector /
//      data_transform / agent），其余原样返回（agent 归一见 fix_agent_null_length DC-04 L2）；
//   3. 旧字段尽可能保留语义映射到新字段，无法映射的丢弃。
// =============================================================================

import type {
  NodeConfiguration,
  ControlFlowNodeConfiguration,
  DynamicInjectorNodeConfiguration,
  DataTransformNodeConfiguration,
  DataTransformOperation,
  DataTransformOperationParams,
  DataTransformStep,
  NodeTemplate,
  NodeType,
} from "../workflow-definition/definitionTypes";
import { normalizeAgentNodeConfiguration } from "../workflow-definition/agentConfigNormalization";
import type { WorkflowCanvasState, CanvasNodeData } from "./canvasTypes";

// ---------------------------------------------------------------------------
// 1. migrateControlFlowConfig：枚举值映射
// ---------------------------------------------------------------------------

/** 将旧 control_flow_kind 枚举值 "human_review" 迁移为 "human_review_gate" */
function migrateControlFlowConfig(
  config: ControlFlowNodeConfiguration,
): ControlFlowNodeConfiguration {
  if (config.control_flow_kind === "human_review" as string) {
    return { ...config, control_flow_kind: "human_review_gate" };
  }
  return config;
}

// ---------------------------------------------------------------------------
// 2. migrateDynamicInjectorConfig：字段名映射
// ---------------------------------------------------------------------------

/** 旧 dynamic_injector 配置形状（宽松类型，用于读取 localStorage 旧数据） */
interface LegacyDynamicInjectorConfig {
  node_type: "dynamic_injector";
  source_expression?: string;
  template_node_id?: string;
  max_injection_count?: number;
  injection_key_field?: string;
  injection_condition?: string;
  node_templates?: NodeTemplate[];
  target_position?: string;
}

/** 将旧 dynamic_injector 字段名迁移到新字段名 */
function migrateDynamicInjectorConfig(
  config: DynamicInjectorNodeConfiguration,
): DynamicInjectorNodeConfiguration {
  const legacy = config as unknown as LegacyDynamicInjectorConfig;

  // 如果已经是新格式（有 injection_condition），直接返回
  if (legacy.injection_condition !== undefined || legacy.source_expression === undefined) {
    return config;
  }

  // 旧格式迁移
  const oldTemplateNodeId = legacy.template_node_id ?? "";
  const nodeTemplates: NodeTemplate[] = oldTemplateNodeId.length > 0
    ? [{ template_id: oldTemplateNodeId, node_type: "agent" as NodeType, name: oldTemplateNodeId, configuration: {} }]
    : [];

  return {
    node_type: "dynamic_injector",
    injection_condition: legacy.source_expression ?? "",
    node_templates: legacy.node_templates ?? nodeTemplates,
    max_injection_count: legacy.max_injection_count ?? 100,
    ...(legacy.target_position !== undefined ? { target_position: legacy.target_position } : {}),
  };
}

// ---------------------------------------------------------------------------
// 3. migrateDataTransformConfig：结构映射
// ---------------------------------------------------------------------------

/** 旧 data_transform 配置形状（宽松类型，用于读取 localStorage 旧数据） */
interface LegacyDataTransformConfig {
  node_type: "data_transform";
  operations?: Array<{
    operation: DataTransformOperation;
    params: Record<string, unknown>;
  }>;
  operation?: DataTransformOperation;
  input_dataset_reference?: string;
  params?: DataTransformOperationParams;
}

/** 将旧 data_transform operations 数组结构迁移为操作链（DC-10：整体提升为链） */
function migrateDataTransformConfig(
  config: DataTransformNodeConfiguration,
): DataTransformNodeConfiguration {
  const legacy = config as unknown as LegacyDataTransformConfig;

  // 如果已经是新格式（有 operation 单数字段），或旧链字段为非法形态
  // （null/undefined/非数组，L-12：原 === undefined 判空对 null/非数组穿透致 :111 崩溃），
  // 直接返回（非数组旧链交由渲染层 deriveDataTransformSteps 真值守卫兜底）。
  if (legacy.operation !== undefined || !Array.isArray(legacy.operations)) {
    return config;
  }

  // 旧格式迁移：整体提升为链（保留全部元素，顶层镜像首步）
  const oldOperations = legacy.operations;
  if (oldOperations.length > 0) {
    const operations: DataTransformStep[] = oldOperations.map((step) => ({
      operation: step.operation,
      params: migrateDataTransformParams(step.params ?? {}),
    }));
    return {
      node_type: "data_transform",
      operation: operations[0].operation,
      input_dataset_reference: legacy.input_dataset_reference ?? "",
      params: operations[0].params ?? {},
      operations,
    };
  }

  // 空数组 → 默认值
  return {
    node_type: "data_transform",
    operation: "map",
    input_dataset_reference: legacy.input_dataset_reference ?? "",
    params: {},
  };
}

/** 将旧 params（Record<string, unknown>）映射到 DataTransformOperationParams */
function migrateDataTransformParams(
  oldParams: Record<string, unknown>,
): DataTransformOperationParams {
  const result: DataTransformOperationParams = {};
  if (typeof oldParams.map_expression === "string")
    result.map_expression = oldParams.map_expression;
  if (typeof oldParams.filter_condition === "string")
    result.filter_condition = oldParams.filter_condition;
  if (Array.isArray(oldParams.group_by))
    result.group_by = oldParams.group_by.filter((v): v is string => typeof v === "string");
  if (typeof oldParams.aggregate_function === "string")
    result.aggregate_function = oldParams.aggregate_function as DataTransformOperationParams["aggregate_function"];
  if (typeof oldParams.aggregate_field === "string")
    result.aggregate_field = oldParams.aggregate_field;
  if (typeof oldParams.join_type === "string")
    result.join_type = oldParams.join_type as DataTransformOperationParams["join_type"];
  if (typeof oldParams.join_key === "string")
    result.join_key = oldParams.join_key;
  if (typeof oldParams.right_dataset_reference === "string")
    result.right_dataset_reference = oldParams.right_dataset_reference;
  return result;
}

// ---------------------------------------------------------------------------
// 4. migrateNodeConfiguration：按 node_type 分派
// ---------------------------------------------------------------------------

/** 按 node_type 分派迁移，仅迁移四类节点（control_flow / dynamic_injector / data_transform / agent），其余原样返回 */
function migrateNodeConfiguration(config: NodeConfiguration): NodeConfiguration {
  switch (config.node_type) {
    case "control_flow":
      return migrateControlFlowConfig(config);
    case "dynamic_injector":
      return migrateDynamicInjectorConfig(config);
    case "data_transform":
      return migrateDataTransformConfig(config);
    case "agent":
      // L2 主防线（G-02，fix_agent_null_length DC-04）：持久化恢复链对 agent 配置归一，
      // 旧状态 null/undefined 集合字段清洗为安全形态（AC-05）
      return normalizeAgentNodeConfiguration(config);
    default:
      return config;
  }
}

// ---------------------------------------------------------------------------
// 5. migrateCanvasState：入口函数
// ---------------------------------------------------------------------------

/**
 * 迁移画布状态：遍历所有节点，对每节点 configuration 执行兼容映射。
 * 纯函数，不修改输入，不抛异常。
 * 节点 ID 与连线不变，仅 configuration 字段可能被转换。
 */
export function migrateCanvasState(state: WorkflowCanvasState): WorkflowCanvasState {
  return {
    nodes: state.nodes.map((node) => ({
      ...node,
      data: {
        ...node.data,
        configuration: migrateNodeConfiguration(node.data.configuration),
      } as CanvasNodeData,
    })),
    edges: state.edges,
  };
}