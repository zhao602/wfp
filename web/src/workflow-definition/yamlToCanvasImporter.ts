// =============================================================================
// YAML 导入画布（src/workflow-definition/yamlToCanvasImporter.ts）
//
// 职责：将 YAML 文本导入还原为画布状态。
// 设计意图（对应需求规格 5.5.1.5、任务 T19.2）：
//   1. 解析 YAML 为工作流定义对象，再转换为画布节点/连线；
//   2. 导入失败时返回 { ok: false, error }，调用方保持当前画布内容不变；
//   3. 节点位置自动布局：按拓扑顺序从左到右排列。
// =============================================================================

import yaml from "yaml";
import type { WorkflowCanvasState, CanvasNode, CanvasEdge } from "../canvas/canvasTypes";
import type {
  WorkflowDefinition,
  NodeDefinition,
  EdgeDefinition,
  NodeConfiguration,
} from "./definitionTypes";
import { normalizeAgentNodeConfiguration } from "./agentConfigNormalization";

/** 导入结果：成功携带画布状态，失败携带错误信息 */
export type ImportResult =
  | { ok: true; canvasState: WorkflowCanvasState }
  | { ok: false; error: string };

/**
 * 将 YAML 文本导入还原为画布状态。
 *
 * @param yamlText YAML 文本
 * @returns 导入结果：成功时 ok=true 并携带画布状态；失败时 ok=false 并携带错误信息
 */
export function yamlToCanvas(yamlText: string): ImportResult {
  let definition: WorkflowDefinition;
  try {
    const parsed = yaml.parse(yamlText) as WorkflowDefinition;
    definition = parsed;
  } catch (error) {
    return { ok: false, error: `YAML 解析失败: ${String(error)}` };
  }

  if (!definition.nodes || !definition.edges) {
    return { ok: false, error: "YAML 缺少 nodes 或 edges 字段" };
  }

  // 空集合视为非法导入数据：从源头断绝空画布经导入链路合法生成（design DC-7，H1-b 断根）
  if (definition.nodes.length === 0 || definition.edges.length === 0) {
    return { ok: false, error: "范例定义中节点或连线集合为空，已拒绝导入" };
  }

  const canvasState = definitionToCanvas(definition);
  return { ok: true, canvasState };
}

/**
 * 将工作流定义对象转换为画布状态。
 * 节点位置按拓扑顺序自动布局，每列间隔 200px，每行间隔 100px。
 */
export function definitionToCanvas(
  definition: WorkflowDefinition,
): WorkflowCanvasState {
  const layout = computeLayout(definition.nodes, definition.edges);

  const nodes: CanvasNode[] = definition.nodes.map((node) => {
    const assembled = { ...node.configuration, node_type: node.node_type } as NodeConfiguration;
    // L1 主防线（G-01，fix_agent_null_length DC-04）：agent 节点配置经数据边界归一，
    // null/undefined/非数组集合字段不得以未清洗状态进入渲染树（覆盖自备文件与范例文件两条输入）
    const configuration =
      node.node_type === "agent"
        ? normalizeAgentNodeConfiguration(assembled)
        : assembled;
    return {
      id: node.node_id,
      type: node.node_type,
      position: layout[node.node_id] ?? { x: 0, y: 0 },
      data: {
        node_id: node.node_id,
        name: node.name,
        node_type: node.node_type,
        ...(node.output_name ? { output_name: node.output_name } : {}),
        configuration,
      },
    };
  });

  const edges: CanvasEdge[] = definition.edges.map((edge) => ({
    id: edge.edge_id,
    source: edge.source_node_id,
    target: edge.target_node_id,
    data: { edge_id: edge.edge_id },
  }));

  return { nodes, edges };
}

/**
 * 简单自动布局：按节点在 nodes 数组中的顺序排列为网格。
 * 每行最多 4 个节点，列间隔 250px，行间隔 120px。
 */
function computeLayout(
  nodes: NodeDefinition[],
  _edges: EdgeDefinition[],
): Record<string, { x: number; y: number }> {
  const layout: Record<string, { x: number; y: number }> = {};
  const cols = 4;
  const colSpacing = 250;
  const rowSpacing = 120;

  nodes.forEach((node, index) => {
    const col = index % cols;
    const row = Math.floor(index / cols);
    layout[node.node_id] = { x: col * colSpacing, y: row * rowSpacing };
  });

  return layout;
}
