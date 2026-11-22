// =============================================================================
// 画布状态类型（src/canvas/canvasTypes.ts）
//
// 职责：定义画布节点/连线状态结构，桥接 React Flow 与工作流定义类型。
// =============================================================================

import type { NodeConfiguration, NodeType } from "../workflow-definition/definitionTypes";

/** 画布节点数据：承载节点配置信息，供节点渲染组件与配置面板使用 */
export interface CanvasNodeData {
  node_id: string;
  name: string;
  node_type: NodeType;
  output_name?: string;
  configuration: NodeConfiguration;
}

/** 画布节点：React Flow 节点 + 自定义数据 */
export interface CanvasNode {
  id: string;
  type: string;
  position: { x: number; y: number };
  data: CanvasNodeData;
}

/** 画布连线：React Flow 边 + 自定义标识 */
export interface CanvasEdge {
  id: string;
  source: string;
  target: string;
  data?: { edge_id: string };
}

/** 画布完整状态：节点集合 + 连线集合 */
export interface WorkflowCanvasState {
  nodes: CanvasNode[];
  edges: CanvasEdge[];
}

/** 节点执行状态映射：node_id -> 状态 */
export type NodeStatusMap = Record<string, "pending" | "running" | "succeeded" | "failed" | "skipped">;
