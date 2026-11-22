// =============================================================================
// 节点输入项登记表（src/canvas/nodeInputBindings.ts）
//
// 职责：按节点类型提取「可寻址输入位置」及其绑定引用（upstream_input_ref
//       CHG-F01 / design §2.5 候选生成矩阵的前端登记层）。
// 设计意图：
//   1. 纯函数、零 React 依赖、零副作用、零 any；保持节点内字段声明顺序；
//   2. 字段集与后端 collect_input_references / build_input_position_index
//      逐类对齐（design §2.5 矩阵）：trigger.input_template /
//      tool.argument_mapping（static_arguments 常量项不入）/
//      subworkflow.input_mapping / data_transform.input_dataset_reference
//      （+ join 时 params.right_dataset_reference）/ output_display.input_reference
//      （非空时）/ agent.inputs（须 alias 非空）；
//   3. control_flow / dynamic_injector / data_source / vector_knowledge_base
//      及未登记类型安全降级返回 []（design §2.5 矩阵 ❌ 行，不崩溃）；
//   4. 本登记表只负责「位置 → 绑定原文」的声明提取（含未绑定的空值位置）；
//      「入候选三条件」（已绑定可解析 / 形态 A / owner 上游且可引用，OP-02/OP-08）
//      由 nodeIoTopology.collectUpstreamInputCandidates 门控，两层解耦。
// =============================================================================

import type { CanvasNode } from "./canvasTypes";
import {
  isAgentConfig,
  isDataTransformConfig,
  isOutputDisplayConfig,
  isSubWorkflowConfig,
  isToolConfig,
  isTriggerConfig,
} from "../workflow-definition/definitionTypes";

/** 节点输入项绑定：输入位置名 + 绑定引用表达式原文 */
export interface NodeInputBinding {
  /** 输入位置名（映射键名 / 固定字段名 / agent alias；寻址键 `node_id::positionName` 的右段） */
  readonly positionName: string;
  /** 绑定引用表达式原文（可为空串 = 未绑定；形态与可引用性判定由候选门控层负责） */
  readonly reference: string;
}

/**
 * 收集单个节点的可寻址输入项绑定（登记表，design §2.5 矩阵）。
 * @param node 画布节点
 * @returns 输入项绑定列表（保持配置内声明顺序）；
 *          未登记类型安全降级返回 []（不崩溃）
 */
export function collectNodeInputBindings(node: CanvasNode): NodeInputBinding[] {
  const config = node.data.configuration;
  const bindings: NodeInputBinding[] = [];

  if (isTriggerConfig(config)) {
    // G2-④ 真值守卫（fix_agent_null_length v3）：映射字段 null（YAML 空值透传）
    // 时 Object.entries(null) 抛错，`?? {}` 等效安全降级为无登记项
    for (const [positionName, reference] of Object.entries(
      config.input_template ?? {},
    )) {
      bindings.push({ positionName, reference });
    }
  } else if (isToolConfig(config)) {
    // static_arguments 常量项不入（design §2.5 矩阵：无引用语义，OP-02②）
    for (const [positionName, reference] of Object.entries(
      config.argument_mapping ?? {},
    )) {
      bindings.push({ positionName, reference });
    }
  } else if (isSubWorkflowConfig(config)) {
    for (const [positionName, reference] of Object.entries(
      config.input_mapping ?? {},
    )) {
      bindings.push({ positionName, reference });
    }
  } else if (isDataTransformConfig(config)) {
    bindings.push({
      positionName: "input_dataset_reference",
      reference: config.input_dataset_reference,
    });
    // G2-⑤ 真值守卫（fix_agent_null_length v3）：params null 时可选链安全降级，
    // 避免 null 解引用（reading 'right_dataset_reference'）
    const rightReference = config.params?.right_dataset_reference;
    if (
      config.operation === "join" &&
      typeof rightReference === "string" &&
      rightReference.length > 0
    ) {
      bindings.push({
        positionName: "params.right_dataset_reference",
        reference: rightReference,
      });
    }
  } else if (isOutputDisplayConfig(config)) {
    // 单一引用字段；仅非空字符串形态入登记
    // （空值 = 展示上游全部，无位置绑定语义，design §2.5 矩阵「非空时」）
    if (
      typeof config.input_reference === "string" &&
      config.input_reference.length > 0
    ) {
      bindings.push({
        positionName: "input_reference",
        reference: config.input_reference,
      });
    }
  } else if (isAgentConfig(config)) {
    // G2-② 真值守卫（fix_agent_null_length v3）：inputs null 时
    // `!== undefined` 穿透导致 for...of null 抛错，Array.isArray 三态拦截
    if (Array.isArray(config.inputs)) {
      for (const entry of config.inputs) {
        // 须 alias 非空：缺省名随引用漂移，非稳定寻址键（design §2.5 / DC-02 备选 C 否决）
        // G2-① 崩溃点修复（:100:54，用户堆栈逐字复现）：`!== undefined` 对 null
        // 穿透（alias: null 经 YAML 空值透传），改真值守卫——非空字符串 alias 才入登记
        if (typeof entry.alias === "string" && entry.alias.length > 0) {
          bindings.push({
            positionName: entry.alias,
            reference: entry.reference,
          });
        }
      }
    }
  }
  // control_flow / dynamic_injector / data_source / vector_knowledge_base
  // 及未登记类型 → 安全降级 []（design §2.5 矩阵）
  return bindings;
}
