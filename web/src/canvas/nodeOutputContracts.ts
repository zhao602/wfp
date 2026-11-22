// =============================================================================
// 节点类型固定输出格式契约层（src/canvas/nodeOutputContracts.ts）
//
// 职责：以纯数据表定义每种节点类型的固定输出格式契约（类别、可引用性、
//       说明、是否可结构化细化），供上游限定候选收集与配置面板契约展示复用。
// 设计意图：零 React 依赖、零副作用、零 any；契约取值与后端既有执行器产出
//           语义对齐（只读对齐，不新增/修改后端模型与枚举）。
// =============================================================================

import type { NodeType, VectorNodeMode } from "../workflow-definition/definitionTypes";

// ---------------------------------------------------------------------------
// 类型
// ---------------------------------------------------------------------------

/** 节点输出格式契约（只读） */
export interface NodeOutputContract {
  /** 输出类别文案（如"结构化对象"） */
  readonly category: string;
  /** 是否可被下游节点引用为输入 */
  readonly referable: boolean;
  /** 契约要点说明 */
  readonly detail: string;
  /** 是否具备结构化细化编辑（仅 Agent 为 true） */
  readonly schemaEditable: boolean;
  /**
   * 按节点模式差异化输出契约（vkb_write_mode，可选只读）。
   * 未声明 / 未命中模式时消费方取顶层 category/detail（零影响既有消费方，R6）。
   */
  readonly variantByMode?: Readonly<
    Partial<Record<VectorNodeMode, { category: string; detail: string }>>
  >;
}

// ---------------------------------------------------------------------------
// 前后端节点类型对齐记录（只读映射，覆盖前端 12 类 ↔ 后端 14 类）
//
// 后端 NodeType（domain/models/enums.py）共 14 类，前端建模其中 12 类。
// 后端专有类型 plugin / supervisor 前端未建模，导入此类节点时按"未定义契约"
// 降级为只读提示，不参与输入引用候选。
// ---------------------------------------------------------------------------

/** 前端 12 类节点类型 ↔ 后端枚举名映射 */
export const NODE_TYPE_BACKEND_MAP: Readonly<Record<NodeType, string>> = {
  trigger: "TRIGGER",
  agent: "AGENT",
  tool: "TOOL",
  data_source: "DATA_SOURCE",
  control_flow: "CONTROL_FLOW",
  subworkflow: "SUB_WORKFLOW",
  dynamic_injector: "DYNAMIC_INJECTOR",
  data_transform: "DATA_TRANSFORM",
  output_display: "OUTPUT_DISPLAY",
  vector_knowledge_base: "VECTOR_KNOWLEDGE_BASE",
  llm: "LLM",
  code: "CODE",
};

/** 后端专有（前端未建模）节点类型：导入后降级为"未定义契约" */
export const BACKEND_ONLY_NODE_TYPES: readonly string[] = ["plugin", "supervisor"];

// ---------------------------------------------------------------------------
// 契约数据表
//
// 可引用性语义依据：控制流节点产出写入 SharedState.branch_selections /
// loop_counters，而非 node_outputs，故 referable=false，不产出可引用业务数据。
// llm / code 均 referable=true（v2 修订，取代 v1 拟定 false，D-06）：
// 代码节点有真实输出 {"result": ...}，下游可以引用——v1 的 referable=false
// 因「不执行」前提失效（DC-13-4）。
// ---------------------------------------------------------------------------

/** 节点类型固定输出格式契约表（全部前端类型穷尽） */
export const NODE_OUTPUT_CONTRACTS: Readonly<Record<NodeType, NodeOutputContract>> = {
  trigger: {
    category: "输入参数集合（键值对）",
    referable: true,
    detail: "触发器产出其输入参数集合，可供下游节点引用。",
    schemaEditable: false,
  },
  agent: {
    category: "结构化对象",
    referable: true,
    detail: "Agent 节点产出结构化对象，字段结构可受输出约束（output_schema）细化。",
    schemaEditable: true,
  },
  tool: {
    category: "工具调用结果",
    referable: true,
    detail: "工具节点产出工具调用结果，可供下游节点引用。",
    schemaEditable: false,
  },
  data_source: {
    category: "数据集（记录集合）",
    referable: true,
    detail: "数据源节点产出数据集（记录集合），可供下游节点引用。",
    schemaEditable: false,
  },
  control_flow: {
    category: "控制信号",
    referable: false,
    detail: "该类型产出控制信号，不产出可引用业务数据输出。",
    schemaEditable: false,
  },
  subworkflow: {
    category: "键值对集合",
    referable: true,
    detail: "子工作流节点产出键值对集合，可供下游节点引用。",
    schemaEditable: false,
  },
  dynamic_injector: {
    category: "注入清单",
    referable: true,
    detail: "动态注入节点产出注入清单，可供下游节点引用。",
    schemaEditable: false,
  },
  data_transform: {
    category: "数据集（记录集合）",
    referable: true,
    detail: "数据转换节点产出数据集（记录集合），可供下游节点引用。",
    schemaEditable: false,
  },
  output_display: {
    category: "上游输出透传",
    referable: true,
    detail: "输出展示节点透传上游命名输出，原样可供下游节点引用。",
    schemaEditable: false,
  },
  vector_knowledge_base: {
    category: "片段集合（文本片段列表）",
    referable: true,
    detail: "向量知识库节点产出检索命中的文本片段集合（数量不超过 top_k），可供下游节点引用。",
    schemaEditable: false,
    // vkb_write_mode：按模式差异化输出类别（顶层 category/referable/schemaEditable 取值不变，R6）
    variantByMode: {
      retrieve: {
        category: "片段集合（文本片段列表）",
        detail: "检索模式产出检索命中的文本片段集合（数量不超过 top_k）。",
      },
      write: {
        category: "写入报告",
        detail: "写入模式产出写入报告（status/chunk_count/upserted_count/failed 等），记录本次写入统计与失败清单。",
      },
    },
  },
  llm: {
    category: "结构化对象",
    referable: true,
    detail:
      "大模型节点产出 {\"content\": 推理文本} 结构化对象（inputs 三态选择集装配提示词拼接），可供下游节点引用。",
    schemaEditable: false,
  },
  code: {
    category: "函数返回值（result）",
    referable: true,
    detail:
      "代码节点以上游装配产物注入 params、脚本产出 result，节点输出 {\"result\": 返回值} 可供下游节点引用；脚本受限进程内执行，非进程级隔离（INV-06）。",
    schemaEditable: false,
  },
};

// ---------------------------------------------------------------------------
// 查表函数
// ---------------------------------------------------------------------------

/**
 * 按节点类型查输出契约。
 * 对全部前端类型恒命中；对后端专有类型（plugin/supervisor）或未知类型
 * 返回 undefined，触发安全降级。
 */
export function resolveOutputContract(
  nodeType: string,
): NodeOutputContract | undefined {
  if (Object.prototype.hasOwnProperty.call(NODE_OUTPUT_CONTRACTS, nodeType)) {
    return NODE_OUTPUT_CONTRACTS[nodeType as NodeType];
  }
  return undefined;
}