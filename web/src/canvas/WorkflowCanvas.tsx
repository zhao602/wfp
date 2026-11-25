// =============================================================================
// 画布容器组件（src/canvas/WorkflowCanvas.tsx）
//
// 职责：基于 React Flow 提供画布容器与节点/连线状态管理。
// 设计意图（对应需求规格 5.5.1.1、任务 T18.1）：
//   1. 注册五类节点渲染组件，按 node_type 分派；
//   2. 连线前调用 ConnectionValidator 做本地环检测，拒绝成环连线；
//   3. 支持节点选中（供配置面板使用）与节点拖拽移动。
// =============================================================================

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactFlow, {
  type Node,
  type Edge,
  type EdgeChange,
  type Connection,
  type NodeChange,
  type NodeDimensionChange,
  type OnConnect,
  type NodeTypes,
  type ReactFlowInstance,
  type Viewport,
  Background,
  Controls,
  addEdge,
  applyNodeChanges,
  applyEdgeChanges,
} from "reactflow";
import "reactflow/dist/style.css";
import TriggerNodeView from "./nodes/TriggerNodeView";
import AgentNodeView from "./nodes/AgentNodeView";
import ToolNodeView from "./nodes/ToolNodeView";
import DataSourceNodeView from "./nodes/DataSourceNodeView";
import ControlFlowNodeView from "./nodes/ControlFlowNodeView";
import SubWorkflowNodeView from "./nodes/SubWorkflowNodeView";
import DynamicInjectorNodeView from "./nodes/DynamicInjectorNodeView";
import DataTransformNodeView from "./nodes/DataTransformNodeView";
import OutputDisplayNodeView from "./nodes/OutputDisplayNodeView";
import VectorKnowledgeBaseNodeView from "./nodes/VectorKnowledgeBaseNodeView";
import LlmNodeView from "./nodes/LlmNodeView";
import CodeNodeView from "./nodes/CodeNodeView";
import { validateConnection } from "./ConnectionValidator";
import { generateNodeId } from "./NodePalette";
import type { WorkflowCanvasState, CanvasNodeData, CanvasNode } from "./canvasTypes";
import type { NodeType, NodeConfiguration } from "../workflow-definition/definitionTypes";
import { useThemeMode, darkTokens } from "../theme/themeMode";
import EdgeContextMenu from "./EdgeContextMenu";
import { getPresetExpression } from "./dataTransformPresetExpressions";
import {
  computeWorkflowSignature,
  clearStoredViewport,
  isUsableViewport,
  readStoredViewport,
  resolveInitialViewportPlan,
  writeStoredViewport,
  type InitialViewportPlan,
  type PaneSize,
  VIEWPORT_FIT_MAX_ZOOM,
  VIEWPORT_FIT_PADDING,
  VIEWPORT_MAX_ZOOM,
  VIEWPORT_MIN_ZOOM,
} from "./canvasViewport";

/**
 * 初始视口应用失败（库尚未就绪：d3 缩放器/尺寸未进内部注册表）时的最大重试帧数。
 * 有界重试避免"永不适配"与"无限空转"两个极端。
 */
const VIEWPORT_APPLY_MAX_ATTEMPTS = 12;

/**
 * 适配置信窗口时长（ms）：适配时容器可能尚未拿到最终尺寸（首帧布局未稳定），
 * 导致工作流被画在视口偏上位置；窗口内若容器尺寸变化则重新适配一次。
 */
const AUTO_FIT_WINDOW_MS = 1500;

/**
 * 受控聚焦回退尺寸（B13 U3）：目标节点尚未被 React Flow 测量（nodeDimensions 缺项）时，
 * setCenter 中心点计算的估算基准；测量到达后自动改用实测值。
 */
const FOCUS_FALLBACK_NODE_WIDTH = 180;
const FOCUS_FALLBACK_NODE_HEIGHT = 60;

/** 受控聚焦请求（B13 U3）：nonce 单调递增驱动一次性视口平移（同 nonce 重放不重复触发） */
export interface FocusNodeRequest {
  nodeId: string;
  nonce: number;
}

/** 画布组件属性 */
interface WorkflowCanvasProps {
  canvasState: WorkflowCanvasState;
  /**
   * 画布状态提交契约（CHG-1/DC-1）：支持两种形态——
   * 函数式原子提交（updater 纯函数，updater 内禁止副作用，StrictMode 双调用安全）
   * 与传值两种形态；传值用法向后兼容。
   */
  onCanvasStateChange: (update: React.SetStateAction<WorkflowCanvasState>) => void;
  selectedNodeId: string | null;
  onSelectNode: (nodeId: string | null) => void;
  /** 删除节点意图上抛；未传时键盘删除入口不生效（可选，向后兼容） */
  onDeleteNode?: (nodeId: string) => void;
  /** 删除连线意图上抛；未传时键盘/右键删除入口不生效（可选，向后兼容，对称 onDeleteNode?） */
  onDeleteEdge?: (edgeId: string) => void;
  /**
   * 画布内容代际号（CHG-8/DC-10）：可选、默认 0（未传时行为与修复前一致，向后兼容）。
   * 仅"整批外部载入"（范例加载 / YAML 导入成功）自增；用于 (a) 代际级视口适配，
   * (b) <ReactFlow key> 代际重挂载以清空库内部注册表（nodeInternals）。
   */
  canvasGeneration?: number;
  /**
   * 受控聚焦请求（B13 U3，可选，默认 null 不生效，向后兼容）：nonce 变化时将
   * 视口平移至 nodeId 节点中心（setCenter，zoom 1 / duration 300ms）；
   * 一次性消费语义——已消费 nonce 重放 / StrictMode 双跑不重复触发；
   * 实例未就绪或目标不存在时请求保留，待后续渲染兑现。
   */
  focusNodeRequest?: FocusNodeRequest | null;
}

/** 节点类型注册表：将 node_type 映射到渲染组件 */
const nodeTypes: NodeTypes = {
  trigger: TriggerNodeView,
  agent: AgentNodeView,
  tool: ToolNodeView,
  data_source: DataSourceNodeView,
  control_flow: ControlFlowNodeView,
  subworkflow: SubWorkflowNodeView,
  dynamic_injector: DynamicInjectorNodeView,
  data_transform: DataTransformNodeView,
  output_display: OutputDisplayNodeView,
  vector_knowledge_base: VectorKnowledgeBaseNodeView,
  llm: LlmNodeView,
  code: CodeNodeView,
};

/**
 * 运行时校验集合：拖拽 dataTransfer 携带的类型须为合法 NodeType。
 * "tool" 已移除（merge_tool_into_agent_node DC-C）：新建入口禁绝，
 * 存量 tool 节点渲染走 nodeTypes 注册表（INV-8），不经此集合。
 */
const VALID_NODE_TYPES: ReadonlySet<NodeType> = new Set<NodeType>([
  "trigger", "agent", "data_source",
  "control_flow", "subworkflow", "dynamic_injector", "data_transform",
  "output_display", "vector_knowledge_base", "llm", "code",
]);

/** 携带有效尺寸的 dimensions 变更（收窄 `dimensions` 为非可选） */
type MeasuredDimensionChange = NodeDimensionChange & {
  dimensions: { width: number; height: number };
};

/** 节点默认显示名映射 —— 与 NodePalette.NODE_TEMPLATES[].label 同源，修改需双向同步。导出供测试做双向一致性断言（design 附录 A.4 表 B B1/B3） */
export const DEFAULT_NODE_NAMES: Readonly<Record<NodeType, string>> = {
  trigger: "触发器",
  agent: "Agent",
  tool: "Tool",
  data_source: "数据源",
  control_flow: "控制流",
  subworkflow: "子工作流",
  dynamic_injector: "动态注入",
  data_transform: "数据转换",
  output_display: "输出展示",
  vector_knowledge_base: "向量知识库",
  llm: "大模型",
  code: "代码",
};

/** 按节点类型返回判别联合对应的最小合法初始配置 */
function createDefaultConfiguration(type: NodeType): NodeConfiguration {
  switch (type) {
    case "trigger":
      return { node_type: "trigger", trigger_type: "manual", input_template: {} };
    case "agent":
      return { node_type: "agent", model_identifier: "", system_prompt: "", available_tools: [] };
    case "tool":
      return { node_type: "tool", tool_identifier: "", tool_source: "local", argument_mapping: {}, static_arguments: {} };
    case "data_source":
      return { node_type: "data_source", connection_reference: "", query: "" };
    case "control_flow":
      return { node_type: "control_flow", control_flow_kind: "conditional_branch", conditions: [], parallel_failure_strategy: "fast_fail", review_timeout_action: "reject" };
    case "subworkflow":
      return { node_type: "subworkflow", subworkflow_definition_id: "", input_mapping: {}, output_mapping: {}, context_strategy: "inherit", max_nesting_depth: 1 };
    case "dynamic_injector":
      return { node_type: "dynamic_injector", injection_condition: "", node_templates: [], max_injection_count: 100 };
    case "data_transform":
      // 新建默认填入预制表达式（FR-03/DC-02）：默认操作类型 map 即带 map_expression 预制值；
      // 值经单一来源模块取得，禁止内联字符串（INV-D01）。加载路径不经过本工厂（FR-05 零填充）。
      return { node_type: "data_transform", operation: "map", input_dataset_reference: "", params: { map_expression: getPresetExpression("map") } };
    case "output_display":
      return { node_type: "output_display" };
    case "vector_knowledge_base":
      // 最小合法配置：知识库标识留空待填；query 缺省手动录入；top_k 缺省 5；
      // embedding 必填以 api 模式占位（模型标识待填）；rerank 未配置即不重排。
      return {
        node_type: "vector_knowledge_base",
        knowledge_base_id: "",
        query_mode: "manual",
        top_k: 5,
        embedding_model: { mode: "api", model_identifier: "" },
      };
    case "llm":
      // 最小合法配置：模型标识留空待填，双提示词取后端缺省空串；
      // 采样参数全部不落盘（未配置即省略键，显式配置才保留，与后端 model_dump 口径对称，DC-14）。
      return { node_type: "llm", model_identifier: "", system_prompt: "", user_prompt: "" };
    case "code":
      // 最小合法配置：空脚本待填；language 固定 python（本期取值域，FR-16）；
      // entry_function 取后端缺省 main（展示语义，FR-18）；不含样例代码（DC-13）。
      return { node_type: "code", language: "python", code: "", entry_function: "main" };
  }
}

/**
 * 剥离 React Flow 运行时字段：回写父层前仅保留持久字段 id/type/position/data，
 * 剔除 selected/measured/positionAbsolute/dragging 等运行时属性，避免污染持久结构（契约 C-5）。
 */
function stripRuntimeFields(nodes: Node<CanvasNodeData>[]): CanvasNode[] {
  return nodes.map((node) => ({
    id: node.id,
    type: node.type ?? "",
    position: node.position,
    data: node.data,
  }));
}

/**
 * ReactFlow 深色作用域注入块：以 `.wfp-theme--dark` 祖先限定覆盖 reactflow 自带类样式
 * （style.css 类样式非内联，特异性 (0,2,0) > 自带 (0,1,0)，spec 5.5.1.9/6.4.2）。
 */
const rfThemeCss = `
/* 画布底色：必须设在 .react-flow 外层容器上。renderer 层 z-index:4 会盖住其下的
   点阵 SVG（Background 的 svg 无 z-index），若把背景色设在 renderer 上会遮住点阵
   导致点完全不可见（spec 5.5.1.9） */
.react-flow {
  background: radial-gradient(ellipse at 50% 0%, #ffffff 0%, #f6f6f6 100%);
}
.wfp-theme--dark .react-flow {
  background: radial-gradient(ellipse at 50% 0%, ${darkTokens.reactflow.viewportBg} 0%, #1a1a1a 100%);
}
.wfp-theme--dark .react-flow__edge-path,
.wfp-theme--dark .react-flow__connection-path {
  stroke: ${darkTokens.reactflow.edgeStroke};
}
.wfp-theme--dark .react-flow__edge.selected .react-flow__edge-path,
.wfp-theme--dark .react-flow__edge:focus .react-flow__edge-path,
.wfp-theme--dark .react-flow__edge:focus-visible .react-flow__edge-path {
  stroke: ${darkTokens.reactflow.edgeStrokeSelected};
}
.wfp-theme--dark .react-flow__controls {
  box-shadow: 0 0 2px 1px rgba(0, 0, 0, 0.5);
}
.wfp-theme--dark .react-flow__controls-button {
  background: ${darkTokens.reactflow.controlsBg};
  border-bottom: 1px solid ${darkTokens.reactflow.controlsBorder};
  fill: ${darkTokens.reactflow.controlsIcon};
  color: ${darkTokens.reactflow.controlsIcon};
}
.wfp-theme--dark .react-flow__controls-button:hover {
  background: ${darkTokens.reactflow.controlsBgHover};
}
.wfp-theme--dark .react-flow__attribution {
  background: ${darkTokens.reactflow.attributionBg};
}
.wfp-theme--dark .react-flow__attribution a {
  color: ${darkTokens.reactflow.attributionLink};
}
/* —— 节点外壳悬停态（NodeShell 视觉体系）——
   投影挂在外层 filter 上，跟随剪影而非矩形外框；
   过渡只挂 filter，拖拽流畅度不受影响 */
.react-flow__node .wfp-node-shell {
  transition: filter 120ms ease;
}
.react-flow__node:hover .wfp-node-shell {
  filter: drop-shadow(0 5px 8px rgba(0, 0, 0, 0.22));
}
.wfp-theme--dark .react-flow__node:hover .wfp-node-shell {
  filter: drop-shadow(0 5px 8px rgba(0, 0, 0, 0.55));
}
/* —— 连接点（Handle）统一造型：小圆点 + 面底色描边，弱化存在感 —— */
.react-flow__handle {
  width: 8px;
  height: 8px;
  background: #a0a0a0;
  border: 2px solid #ffffff;
}
.wfp-theme--dark .react-flow__handle {
  background: #5f5f5f;
  border-color: ${darkTokens.reactflow.viewportBg};
}
`;

/** 画布容器：管理节点/连线状态，处理连线校验与节点选中 */
export default function WorkflowCanvas({
  canvasState,
  onCanvasStateChange,
  selectedNodeId,
  onSelectNode,
  onDeleteNode,
  onDeleteEdge,
  canvasGeneration = 0,
  focusNodeRequest = null,
}: WorkflowCanvasProps) {
  const themeMode = useThemeMode();
  const isDark = themeMode === "dark";

  /**
   * 画布状态镜像（CHG-1）：渲染期同步持有 canvasState 最新引用，仅供
   * 副作用外置预判与连线校验输入，**不成为第二事实源**——写路径仍唯一
   * 收敛于 onCanvasStateChange（design 2.3.2）。引用赋值级代价（NFR-2/INV-10）。
   */
  const canvasStateRef = useRef<WorkflowCanvasState>(canvasState);
  canvasStateRef.current = canvasState;

  /**
   * 运行时节点尺寸缓存（CHG-7/DC-9）：承载 React Flow 测量出的 width/height，供 nodeView
   * 回填渲染门控（initialized）。键 = node.id；**严禁写入 canvasState / localStorage**
   * （保 stripRuntimeFields 与 INV-2/INV-14）。代际变化时清空（见代际适配 effect）。
   */
  const [nodeDimensions, setNodeDimensions] = useState<
    Record<string, { width: number; height: number }>
  >({});

  /**
   * 边选中态（CHG-E02/DC-02）：组件内瞬态（派生态），严禁写入 canvasState / localStorage；
   * 与 selectedNodeId 互斥；由 onEdgeClick 设置、onPaneClick / 边集合变化校正时清空。
   */
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);

  /**
   * 右键菜单瞬态（CHG-E02/DC-03）：视口坐标 + 目标边 id；null 表示菜单关闭。
   * createPortal 渲染，position: fixed；不入 canvasState。
   */
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; edgeId: string } | null>(null);

  // 边集合变化时校正选中态（防"幽灵选中"，CHG-E02/DC-02 决策 6）：被删/整批替换的边不残留选中；
  // 静止时无周期性计算（NFR-002）
  useEffect(() => {
    if (selectedEdgeId === null) return;
    if (!canvasState.edges.some((edge) => edge.id === selectedEdgeId)) {
      setSelectedEdgeId(null);
    }
  }, [canvasState.edges, selectedEdgeId]);

  /**
   * 连线状态（CHG-1/DC-1）：不再由本地连线状态持有，直接受控于唯一事实源
   * canvasState.edges；消除"本地清空不自愈"的第二状态源（RG-1）。
   */
  const edges = canvasState.edges as Edge[];

  /**
   * 连线变更函数式回写（CHG-1/DC-3）：以 applyEdgeChanges 在 updater 内基于 prev.edges
   * 纯计算，副作用外置（updater 内无 alert/ref 写入，StrictMode 双调用安全）；
   * 库层受控替换投递的 reset/remove 由父层接住并决定去留，清空后可自愈（RG-1）。
   */
  const onEdgesChange = useCallback(
    (changes: EdgeChange[]) => {
      // 过滤库层 select 变更（CHG-E03/DC-02/INV-D04）：边选中态不入唯一事实源与持久结构；
      // remove/reset/add 行为逐字保持不变；纯 select 变更不触发任何提交
      const meaningfulChanges = changes.filter(
        (change) => change.type !== "select",
      );
      if (meaningfulChanges.length === 0) return;
      onCanvasStateChange((prev) => ({
        nodes: prev.nodes,
        edges: applyEdgeChanges(
          meaningfulChanges,
          prev.edges as Edge[],
        ) as WorkflowCanvasState["edges"],
      }));
    },
    [onCanvasStateChange],
  );

  /** 持有 ReactFlow 实例，供 onDrop 调用 screenToFlowPosition 做坐标转换 */
  const rfInstanceRef = useRef<ReactFlowInstance | null>(null);

  /** 已消费的聚焦 nonce（B13 U3）：一次性消费语义的归属标记 */
  const lastFocusNonceRef = useRef<number>(-1);
  /**
   * 受控聚焦请求消费（B13 U3）：nonce 变化 → setCenter 平移视口至目标节点中心。
   * 宽高优先取运行时实测（nodeDimensions，CHG-7），未测量时按回退常量估算；
   * 实例未就绪 / 目标不存在时不消费（请求保留，待后续渲染兑现）。
   */
  useEffect(() => {
    if (focusNodeRequest === null) return;
    if (lastFocusNonceRef.current === focusNodeRequest.nonce) return;
    const instance = rfInstanceRef.current;
    if (instance === null) return;
    const target = canvasState.nodes.find(
      (node) => node.id === focusNodeRequest.nodeId,
    );
    if (target === undefined) return;
    lastFocusNonceRef.current = focusNodeRequest.nonce;
    const size = nodeDimensions[target.id] ?? {
      width: FOCUS_FALLBACK_NODE_WIDTH,
      height: FOCUS_FALLBACK_NODE_HEIGHT,
    };
    instance.setCenter(
      target.position.x + size.width / 2,
      target.position.y + size.height / 2,
      { zoom: 1, duration: 300 },
    );
  }, [focusNodeRequest, canvasState.nodes, nodeDimensions]);

  /** 画布容器（与 React Flow pane 同尺寸）：视口测量与用户交互监听的锚点 */
  const containerRef = useRef<HTMLDivElement | null>(null);

  /**
   * 当前节点集合签名：视口持久化与恢复的归属依据（仅节点 id 集合，与位置/配置无关）。
   * 节点增删才改变签名 —— 拖动节点、改配置、连线均不使已保存的视口失效。
   */
  const workflowSignature = useMemo(
    () => computeWorkflowSignature(canvasState.nodes),
    [canvasState.nodes],
  );
  const workflowSignatureRef = useRef<string>(workflowSignature);
  workflowSignatureRef.current = workflowSignature;

  // —— 初始视口控制器（组件瞬态，随卸载复位，不参与 canvasState）——
  /** 本代际的初始视口计划及其是否已生效（null 表示本代际不接管视口） */
  const viewportPlanRef = useRef<{ plan: InitialViewportPlan; applied: boolean } | null>(null);
  /** 应用未就绪时按帧重试的计数（有界，防止无限空转） */
  const viewportAttemptRef = useRef<number>(0);
  /** 适配置信窗口：窗口内容器尺寸变化视为"布局未稳定"，允许再适配一次 */
  const autoFitWindowRef = useRef<boolean>(false);
  /** 上次适配时的容器尺寸（用于判断尺寸是否真的变化） */
  const autoFitSizeRef = useRef<PaneSize | null>(null);
  /** 置信窗口计时器句柄（卸载时清理） */
  const autoFitTimerRef = useRef<number | null>(null);
  /** 本代际初始视口是否已终结（适配完成或恢复完成） */
  const initialFitConsumedRef = useRef<boolean>(false);
  /** 最近一次"节点集合由空变非空"是否由本组件 onDrop 引起 */
  const dropInitiatedNonEmptyRef = useRef<boolean>(false);
  /** 上一提交后的节点计数；-1 表示尚未执行过效应（用于识别"组件挂载即非空"） */
  const prevNodeCountRef = useRef<number>(-1);
  /** 最近一次已"武装/消费"的代际号；初值 -1 保证首次挂载（代际 0）也走一次代际判定 */
  const lastArmedGenerationRef = useRef<number>(-1);
  /** 本代际内已上报有效尺寸的节点 id 集合：初始视口的全量就绪门控（代际变化时清空） */
  const measuredNodeIdsRef = useRef<Set<string>>(new Set());

  /** 读取容器实测尺寸；未布局 / 隐藏 / 无布局引擎时返回 null */
  const measurePane = useCallback((): PaneSize | null => {
    const element = containerRef.current;
    if (element === null) return null;
    const rect = element.getBoundingClientRect();
    if (!(rect.width > 0) || !(rect.height > 0)) return null;
    return { width: rect.width, height: rect.height };
  }, []);

  /** 持久化当前视口（以最新节点签名归属，并记录当时容器尺寸） */
  const persistViewport = useCallback(() => {
    const instance = rfInstanceRef.current;
    if (instance === null) return;
    try {
      const viewport = instance.getViewport();
      if (!isUsableViewport(viewport)) return;
      writeStoredViewport(viewport, workflowSignatureRef.current, measurePane());
    } catch {
      // 实例已释放 / 未初始化：忽略（视口持久化是增强能力，失败不影响画布可用性）
    }
  }, [measurePane]);

  /** 应用未就绪时的按帧重试入口（间接引用，避免自引用依赖环） */
  const applyInitialViewportRef = useRef<() => void>(() => {});

  /** 按帧重试：仅在库尚未就绪（d3 缩放器/尺寸未就绪）时使用，预算 VIEWPORT_APPLY_MAX_ATTEMPTS 帧 */
  const scheduleViewportRetry = useCallback(() => {
    if (viewportAttemptRef.current >= VIEWPORT_APPLY_MAX_ATTEMPTS) return;
    viewportAttemptRef.current += 1;
    if (typeof requestAnimationFrame !== "function") return;
    requestAnimationFrame(() => {
      applyInitialViewportRef.current();
    });
  }, []);

  /** 打开适配置信窗口：适配后布局若才稳定，窗口内的尺寸变化会触发一次重新适配 */
  const armAutoFitWindow = useCallback(() => {
    autoFitSizeRef.current = measurePane();
    autoFitWindowRef.current = true;
    if (autoFitTimerRef.current !== null) window.clearTimeout(autoFitTimerRef.current);
    autoFitTimerRef.current = window.setTimeout(() => {
      autoFitWindowRef.current = false;
      autoFitTimerRef.current = null;
    }, AUTO_FIT_WINDOW_MS);
  }, [measurePane]);

  /**
   * 应用本代际初始视口：有持久化记录且仍属同一工作流 → 恢复（setViewport）；
   * 否则 → 自动适配（fitView）。两道门控确保结果正确：全部节点已测量、容器已有非零尺寸
   * （用 0×0 计算视口会让工作流被画在偏上位置，这正是"刷新后视图不居中"的根因）。
   * fitView 返回 false 表示库尚未就绪，此时撤销消费标记并按帧重试。
   */
  const applyInitialViewport = useCallback(() => {
    const entry = viewportPlanRef.current;
    if (entry === null || entry.applied || entry.plan.kind === "none") return;
    if (
      !canvasStateRef.current.nodes.every((node) =>
        measuredNodeIdsRef.current.has(node.id),
      )
    ) {
      return;
    }
    if (typeof ResizeObserver !== "undefined" && measurePane() === null) {
      scheduleViewportRetry();
      return;
    }
    const instance = rfInstanceRef.current;
    if (instance === null) {
      scheduleViewportRetry();
      return;
    }

    if (entry.plan.kind === "restore") {
      entry.applied = true;
      initialFitConsumedRef.current = true;
      instance.setViewport(entry.plan.viewport);
      persistViewport();
      return;
    }

    entry.applied = true;
    const applied = instance.fitView({
      padding: VIEWPORT_FIT_PADDING,
      maxZoom: VIEWPORT_FIT_MAX_ZOOM,
      duration: 0,
    });
    if (applied === false) {
      entry.applied = false;
      scheduleViewportRetry();
      return;
    }
    initialFitConsumedRef.current = true;
    armAutoFitWindow();
    persistViewport();
  }, [armAutoFitWindow, measurePane, persistViewport, scheduleViewportRetry]);

  applyInitialViewportRef.current = applyInitialViewport;

  /**
   * 代际级初始视口控制器（CHG-8/DC-10，INV-6 修订）：仅在"挂载即非空"（刷新恢复）或
   * "节点计数 0→N 且非 onDrop 来源"（外部载入）或"外部载入代际变化且非空"时接管一次视口；
   * 由 onDrop 引起的 0→N（拖入首节点）不接管，保持"放到哪看哪"的直觉（FR-4.2）。
   * 接管形式由 resolveInitialViewportPlan 纯函数判定：优先恢复上次视口，否则 fit view。
   * 依赖仅为代际号与节点计数标量，静止时无周期性计算（NFR-002）。
   */
  useEffect(() => {
    const nodeCount = canvasState.nodes.length;
    const prevCount = prevNodeCountRef.current;
    prevNodeCountRef.current = nodeCount;

    // 首次武装不是"代际变化"：刷新恢复必须保留持久化视口；仅后续代际跳变才视为整批换内容
    const firstArm = lastArmedGenerationRef.current === -1;
    const generationChanged =
      !firstArm && lastArmedGenerationRef.current !== canvasGeneration;
    if (firstArm || generationChanged) {
      lastArmedGenerationRef.current = canvasGeneration;
      initialFitConsumedRef.current = false;   // 新代际：重置视口接管消费态
      measuredNodeIdsRef.current = new Set();  // 新代际：就绪门控重新累计（key 重挂载后逐节点重新测量）
      viewportPlanRef.current = null;
      viewportAttemptRef.current = 0;
      autoFitWindowRef.current = false;
      autoFitSizeRef.current = null;
      setNodeDimensions({});                   // 新代际：尺寸缓存清空，重新测量（T3.3）
      // 整批外部载入：旧视口属于另一份内容，丢弃以免"恢复"到不相干的位置
      if (generationChanged && nodeCount > 0) clearStoredViewport();
    }
    // 本代际视口已终结：后续任何计数变化（继续放置、删除至空再放置）不再接管视口
    if (initialFitConsumedRef.current) return;

    const mountedNonEmpty = prevCount === -1 && nodeCount > 0;
    const becameNonEmpty = prevCount === 0 && nodeCount > 0;
    const externalGenerationNonEmpty = generationChanged && nodeCount > 0;
    if (!mountedNonEmpty && !becameNonEmpty && !externalGenerationNonEmpty) return;

    if (dropInitiatedNonEmptyRef.current && !generationChanged) {
      // 拖入首节点：不接管视口（保 FR-4.2，FR-006/FR-013）
      initialFitConsumedRef.current = true;
      viewportPlanRef.current = null;
      return;
    }

    const plan = resolveInitialViewportPlan({
      nodeCount,
      generationChanged,
      storedViewport: readStoredViewport(),
      signature: workflowSignatureRef.current,
      paneSize: measurePane(),
    });
    viewportPlanRef.current = { plan, applied: false };
    if (plan.kind === "none") {
      initialFitConsumedRef.current = true;
      return;
    }
    // 立即尝试一次；若节点尺寸/容器尺寸尚未就绪，由尺寸变更与容器观察者继续推动
    applyInitialViewportRef.current();
  }, [canvasGeneration, canvasState.nodes.length, measurePane]);

  /**
   * 容器尺寸观察者（两职责）：
   *   1. 记录实测尺寸，供初始视口决策与应用使用；
   *   2. 适配置信窗口内尺寸变化 → 重新适配。刷新后首帧布局常未稳定（容器高度尚未定型），
   *      此时 fitView 会按偏小的容器计算平移，工作流被画在偏上位置；尺寸定型后重算即归正。
   */
  useEffect(() => {
    const element = containerRef.current;
    if (element === null || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      const size = measurePane();
      const entry = viewportPlanRef.current;
      if (entry !== null && !entry.applied) {
        applyInitialViewportRef.current();
        return;
      }
      if (entry === null || entry.plan.kind !== "fit" || !autoFitWindowRef.current) return;
      if (size === null) return;
      const previous = autoFitSizeRef.current;
      if (
        previous !== null &&
        Math.abs(previous.width - size.width) < 1 &&
        Math.abs(previous.height - size.height) < 1
      ) {
        return;
      }
      if (!canvasStateRef.current.nodes.every((node) => measuredNodeIdsRef.current.has(node.id))) {
        return;
      }
      const instance = rfInstanceRef.current;
      if (instance === null) return;
      autoFitSizeRef.current = size;
      instance.fitView({
        padding: VIEWPORT_FIT_PADDING,
        maxZoom: VIEWPORT_FIT_MAX_ZOOM,
        duration: 0,
      });
      persistViewport();
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [measurePane, persistViewport]);

  /**
   * 用户接管视口即关闭适配置信窗口：一旦用户平移/缩放/按键，后续容器尺寸变化不得再覆盖其视野。
   */
  useEffect(() => {
    const element = containerRef.current;
    if (element === null) return;
    const closeAutoFitWindow = () => {
      autoFitWindowRef.current = false;
    };
    element.addEventListener("pointerdown", closeAutoFitWindow, true);
    element.addEventListener("wheel", closeAutoFitWindow, { capture: true, passive: true });
    element.addEventListener("keydown", closeAutoFitWindow, true);
    return () => {
      element.removeEventListener("pointerdown", closeAutoFitWindow, true);
      element.removeEventListener("wheel", closeAutoFitWindow, true);
      element.removeEventListener("keydown", closeAutoFitWindow, true);
    };
  }, []);

  /** 卸载清理置信窗口计时器 */
  useEffect(
    () => () => {
      if (autoFitTimerRef.current !== null) {
        window.clearTimeout(autoFitTimerRef.current);
        autoFitTimerRef.current = null;
      }
    },
    [],
  );

  /**
   * 视口变更持久化（刷新恢复的数据来源）：仅在手势结束（onMoveEnd）写入，
   * 不进入拖拽/缩放的逐帧路径；写入失败静默（隐私模式/容量超限）。
   */
  const handleMoveEnd = useCallback(
    (_event: MouseEvent | TouchEvent, viewport: Viewport) => {
      if (!isUsableViewport(viewport)) return;
      writeStoredViewport(viewport, workflowSignatureRef.current, measurePane());
    },
    [measurePane],
  );

  /** 节点视图纯派生：以 canvasState.nodes 为唯一源，叠加 selected 标识与运行时尺寸（不改写 data） */
  const nodeView = useMemo<Node<CanvasNodeData>[]>(
    () =>
      (canvasState.nodes as Node<CanvasNodeData>[]).map((node) => ({
        ...node,
        selected: node.id === selectedNodeId,
        ...(nodeDimensions[node.id] ?? {}), // 运行时尺寸回填（不入 canvasState，INV-14）
      })),
    [canvasState.nodes, selectedNodeId, nodeDimensions],
  );

  /**
   * 受控节点变更（CHG-3/DC-4）：仅将「位置/删除」两类持久变更以函数式提交
   * （updater 内以 prev 为基准纯计算，副作用外置预判——删空提示与 ref 写入均在
   * updater 外同步段）回写父层；尺寸测量（dimensions）、选中高亮（select）、
   * 新增（add）、重置（replace）均忽略——高亮由 nodeView 派生，新增统一走 onDrop。
   */
  const handleNodesChange = useCallback(
    (changes: NodeChange[]) => {
      // 尺寸就绪信号：外部载入的一次性初始适配在此消费恰好一次；dimensions 永不回写（保住 P-05）
      if (changes.some((change) => change.type === "dimensions")) {
        // CHG-7/DC-9：采集有效运行时尺寸写入组件私有缓存（不回写父层，INV-2/INV-14）；
        // 变化才写 + 最多一次浅拷贝，避免拖拽高频路径多余重渲染（NFR-3）
        const dimensionChanges = changes.filter(
          (change): change is MeasuredDimensionChange =>
            change.type === "dimensions" &&
            change.dimensions !== undefined &&
            change.dimensions.width > 0 &&
            change.dimensions.height > 0,
        );
        // 全量就绪门控：真实浏览器逐节点分批上报尺寸，而 fitView 仅在全部节点测量完成后
        // 才生效；此处仅累计就绪集合，实际应用由 applyInitialViewport 统一判定
        // （含容器尺寸门控、恢复/适配分支与失败重试）
        for (const change of dimensionChanges) {
          measuredNodeIdsRef.current.add(change.id);
        }
        applyInitialViewportRef.current();

        if (dimensionChanges.length > 0) {
          setNodeDimensions((prev) => {
            let next = prev;
            for (const change of dimensionChanges) {
              const current = prev[change.id];
              const { width, height } = change.dimensions;
              if (current && current.width === width && current.height === height) continue;
              if (next === prev) next = { ...prev };
              next[change.id] = { width, height };
            }
            return next; // 无变化时返回原引用，避免多余重渲染（R-4）
          });
        }
      }

      const persistenceChanges = changes.filter(
        (change) => change.type === "position" || change.type === "remove",
      );
      if (persistenceChanges.length === 0) return;

      // 删空预判（DC-4：副作用外置）：基于最新状态镜像提前拦截"删除全部节点"，
      // 提示文案不变（INV-5）；预判与真实提交间存在窗口时由 updater 返回 prev 兜底
      const removeChanges = persistenceChanges.filter(
        (change) => change.type === "remove",
      );
      if (
        removeChanges.length > 0 &&
        removeChanges.length === canvasStateRef.current.nodes.length
      ) {
        alert("至少保留一个节点，无法删除");
        return;
      }

      const removedSelected = persistenceChanges.some(
        (change) => change.type === "remove" && change.id === selectedNodeId,
      );
      // 缺陷 A 第二道防线：本次 remove 的节点 id 集合，用于同步剔除关联连线，杜绝悬挂死边
      const removedIds = new Set(removeChanges.map((change) => change.id));

      // 函数式原子提交（CHG-3/DC-4，FR-1.1）：updater 内以 prev 为基准纯计算，
      // 无 alert/ref 写入等副作用（StrictMode 双调用安全）；删空时无损忽略返回原 prev
      onCanvasStateChange((prev) => {
        const nextNodes = stripRuntimeFields(
          applyNodeChanges(
            persistenceChanges,
            prev.nodes as Node<CanvasNodeData>[],
          ),
        );
        if (nextNodes.length === 0) {
          return prev;
        }
        const nextEdges =
          removedIds.size > 0
            ? prev.edges.filter(
                (edge) => !removedIds.has(edge.source) && !removedIds.has(edge.target),
              )
            : prev.edges;
        return { nodes: nextNodes, edges: nextEdges };
      });
      // 删除的恰为当前选中节点：清理悬空选中态（契约 C-4，副作用外置于 updater）
      if (removedSelected) onSelectNode(null);
    },
    [selectedNodeId, onCanvasStateChange, onSelectNode],
  );

  /**
   * 连线校验 + 添加（CHG-4/DC-6，FR-3.5）：校验输入取唯一事实源最新边集合镜像
   * （canvasStateRef，替代本地镜像 edges，消除双状态源时序窗口），拒绝文案由校验器
   * 返回（INV-9）；建边唯一收敛于函数式回写父层（第 347-353 行），即时性由父层
   * 受控直接保证。函数式建边：updater 内以 prev.edges 为基准纯计算。
   */
  const onConnect: OnConnect = useCallback(
    (connection: Connection) => {
      if (!connection.source || !connection.target) return;
      const result = validateConnection(
        canvasStateRef.current.edges,
        connection.source,
        connection.target,
      );
      if (!result.ok) {
        alert(result.reason);
        return;
      }
      onCanvasStateChange((prev) => ({
        nodes: prev.nodes,
        edges: addEdge(
          connection,
          prev.edges as Edge[],
        ) as WorkflowCanvasState["edges"],
      }));
    },
    [onCanvasStateChange],
  );

  /** 节点选中（与边选中互斥：点击节点清空边选中态，CHG-E04/DC-02） */
  const onNodeClick = useCallback(
    (_event: React.MouseEvent, node: Node) => {
      onSelectNode(node.id);
      setSelectedEdgeId(null);
    },
    [onSelectNode],
  );

  /** 画布空白处点击：清空节点与边选中态并关闭右键菜单（CHG-E04/DC-02/DC-03） */
  const onPaneClick = useCallback(() => {
    onSelectNode(null);
    setSelectedEdgeId(null);
    setContextMenu(null);
  }, [onSelectNode]);

  /** 边点击选中（CHG-E05/DC-02）：选中该边并清空节点选中（互斥）；视觉高亮依赖库内部 store，禁对 edges 做 map 派生 */
  const onEdgeClick = useCallback(
    (_event: React.MouseEvent, edge: Edge) => {
      setSelectedEdgeId(edge.id);
      onSelectNode(null);
    },
    [onSelectNode],
  );

  /** 边右键（CHG-E05/DC-03）：阻止浏览器默认菜单、记录视口坐标与目标边、视同选中该边 */
  const onEdgeContextMenu = useCallback(
    (event: React.MouseEvent, edge: Edge) => {
      event.preventDefault();
      setContextMenu({ x: event.clientX, y: event.clientY, edgeId: edge.id });
      setSelectedEdgeId(edge.id);
      onSelectNode(null);
    },
    [onSelectNode],
  );

  /** 拖拽悬停：解除浏览器默认禁止放置判定，协商 move 光标 */
  const onDragOver = useCallback((event: React.DragEvent) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
  }, []);

  /** 拖拽释放：读取类型 → 坐标转换 → 生成唯一 id → 构造节点 → 函数式原子提交外部状态 */
  const onDrop = useCallback((event: React.DragEvent) => {
    event.preventDefault();
    const type = event.dataTransfer.getData("application/node-type");
    if (type === "") return;
    if (!VALID_NODE_TYPES.has(type as NodeType)) return;
    const instance = rfInstanceRef.current;
    if (instance === null) return;
    const position = instance.screenToFlowPosition({ x: event.clientX, y: event.clientY });
    if (!Number.isFinite(position.x) || !Number.isFinite(position.y)) return;
    const nodeId = generateNodeId();
    const newNode: CanvasNode = {
      id: nodeId,
      type,
      position,
      data: {
        node_id: nodeId,
        name: DEFAULT_NODE_NAMES[type as NodeType],
        node_type: type as NodeType,
        configuration: createDefaultConfiguration(type as NodeType),
      },
    };
    // 首节点来源标记：以最新状态镜像预判，ref 写入位于 updater 外同步段（StrictMode 安全，DC-2）
    if (canvasStateRef.current.nodes.length === 0) {
      dropInitiatedNonEmptyRef.current = true;
    }
    // 函数式原子提交（CHG-2/DC-3，FR-1.2）：放置结果恒为"既有全部节点 + 新节点"，
    // 杜绝过期闭包快照整份回写造成的变更丢失
    onCanvasStateChange((prev) => ({
      nodes: [...prev.nodes, newNode],
      edges: prev.edges,
    }));
  }, [onCanvasStateChange]);

  /**
   * 画布键盘删除：Delete/Backspace 触发，前置校验全部通过才上抛删除意图（CHG-E06/DC-01）。
   * 校验顺序：按键 → 事件目标非可编辑控件（守卫前置，INV-D07）→ 节点优先 → 其次边 → 均不满足静默返回；
   * 节点上抛 onDeleteNode（与配置面板按钮共用编排），边上抛 onDeleteEdge（与右键菜单共用编排），两入口一致。
   */
  const handleCanvasKeyDown = useCallback(
    (event: React.KeyboardEvent) => {
      if (event.key !== "Delete" && event.key !== "Backspace") return;
      const target = event.target as HTMLElement;
      if (
        target.tagName === "INPUT" ||
        target.tagName === "TEXTAREA" ||
        target.tagName === "SELECT" ||
        target.isContentEditable
      ) {
        return;
      }
      // 节点优先（既有语义保持，CHG-E06/DC-08，D-10~D-13 不回归）
      if (selectedNodeId !== null && onDeleteNode) {
        event.preventDefault();
        onDeleteNode(selectedNodeId);
        return;
      }
      // 其次边（新增分支，CHG-E06/DC-01）
      if (selectedEdgeId !== null && onDeleteEdge) {
        event.preventDefault();
        onDeleteEdge(selectedEdgeId);
      }
    },
    [selectedNodeId, selectedEdgeId, onDeleteNode, onDeleteEdge],
  );

  return (
    <div
      ref={containerRef}
      style={{ width: "100%", height: "100%" }}
      onKeyDown={handleCanvasKeyDown}
    >
      <ReactFlow
        key={canvasGeneration}
        nodes={nodeView}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodesChange={handleNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onNodeClick={onNodeClick}
        onPaneClick={onPaneClick}
        onEdgeClick={onEdgeClick}
        onEdgeContextMenu={onEdgeContextMenu}
        onMoveEnd={handleMoveEnd}
        onInit={(inst: ReactFlowInstance) => { rfInstanceRef.current = inst; }}
        onDragOver={onDragOver}
        onDrop={onDrop}
        deleteKeyCode={null}
        /* 缩放区间：下限 0.1 让数十节点的工作流可整体入画，上限沿用库默认 2 */
        minZoom={VIEWPORT_MIN_ZOOM}
        maxZoom={VIEWPORT_MAX_ZOOM}
      >
        <style>{rfThemeCss}</style>
        {/* 点阵：size=1.2 精致点径、gap=20 舒展间距；浅色 #d4d4d4 柔和、深色柔和档（spec 5.5.1.9） */}
        <Background color={isDark ? darkTokens.reactflow.dotColor : "#d4d4d4"} gap={20} size={1.2} />
        <Controls />
      </ReactFlow>
      {contextMenu && (
        <EdgeContextMenu
          x={contextMenu.x}
          y={contextMenu.y}
          edgeId={contextMenu.edgeId}
          onClose={() => setContextMenu(null)}
          onDelete={(id) => {
            onDeleteEdge?.(id);
            setContextMenu(null);
          }}
        />
      )}
    </div>
  );
}

export type { CanvasNodeData };
