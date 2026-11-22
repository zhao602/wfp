// =============================================================================
// 节点输入输出拓扑纯函数层（src/canvas/nodeIoTopology.ts）
//
// 职责：提供"上游限定"的拓扑判定、候选收集与输入引用合法性校验的纯函数集合。
// 设计意图：零 React 依赖、零副作用、零 any；拓扑判定的唯一出处，
//           供 VariableReferenceSelector 与配置面板契约展示复用。
// 扩展（upstream_input_ref）：形态 B（`node_id::输入位置名`）输入参数候选
//           收集（CHG-F03）与合法性判定分支（design §2.5 / tasks T2.3、T2.5）；
//           形态 A 既有路径逐行不变（INV-E01/INV-E04/INV-E08）。
// =============================================================================

import type { CanvasEdge, WorkflowCanvasState } from "./canvasTypes";
import type { InputParameterCandidate, NamedOutputCandidate } from "./mapEditorShared";
import { collectNamedOutputs, OUTPUT_NAME_REGEX } from "./mapEditorShared";
import { collectNodeInputBindings } from "./nodeInputBindings";
import type { NodeOutputContract } from "./nodeOutputContracts";
import type { NodeType } from "../workflow-definition/definitionTypes";

// ---------------------------------------------------------------------------
// 形态 B 文法常量（与后端 reference_expression.py 权威常量对齐；前端内部使用）
// ---------------------------------------------------------------------------

/** 形态 B 作用域限定符（对齐后端 INPUT_POSITION_SEPARATOR） */
const INPUT_POSITION_SEPARATOR = "::";

/** 引用表达式总长上限（对齐后端 MAX_EXPRESSION_LENGTH） */
const MAX_EXPRESSION_LENGTH = 256;

/** 字段路径段数上限（对齐后端 MAX_PATH_SEGMENTS） */
const MAX_PATH_SEGMENTS = 8;

// ---------------------------------------------------------------------------
// 拓扑判定（设计 §2.2.2 接口 A1）
// ---------------------------------------------------------------------------

/**
 * 求目标节点的全部可达前驱（含直接与间接，不含自身）。
 * 构建逆邻接表（target → [sources]）后以 BFS 遍历，visited 去重保证环结构终止。
 * @param edges 当前画布连线集合
 * @param targetNodeId 目标节点标识
 * @returns 上游节点标识只读集合；无上游或目标不在边集合时返回空集
 */
export function collectUpstreamNodeIds(
  edges: readonly CanvasEdge[],
  targetNodeId: string,
): ReadonlySet<string> {
  const reverseAdjacency = new Map<string, string[]>();
  for (const edge of edges) {
    const sources = reverseAdjacency.get(edge.target);
    if (sources === undefined) {
      reverseAdjacency.set(edge.target, [edge.source]);
    } else {
      sources.push(edge.source);
    }
  }

  const visited = new Set<string>();
  const queue: string[] = [];

  const directSources = reverseAdjacency.get(targetNodeId);
  if (directSources !== undefined) {
    for (const source of directSources) {
      if (!visited.has(source)) {
        visited.add(source);
        queue.push(source);
      }
    }
  }

  let head = 0;
  while (head < queue.length) {
    const current = queue[head];
    head += 1;
    const sources = reverseAdjacency.get(current);
    if (sources === undefined) {
      continue;
    }
    for (const source of sources) {
      if (!visited.has(source)) {
        visited.add(source);
        queue.push(source);
      }
    }
  }

  visited.delete(targetNodeId);
  return visited;
}

/**
 * 单点上游判定：candidateNodeId 是否为 targetNodeId 的上游节点。
 * 内部复用 collectUpstreamNodeIds，供引用校验与契约展示复用。
 */
export function isUpstreamOf(
  state: WorkflowCanvasState,
  candidateNodeId: string,
  targetNodeId: string,
): boolean {
  return collectUpstreamNodeIds(state.edges, targetNodeId).has(candidateNodeId);
}

// ---------------------------------------------------------------------------
// 上游限定候选收集（设计 §2.2.2 接口 A2）
// ---------------------------------------------------------------------------

/**
 * 收集目标节点上游范围内、已声明命名输出且输出可引用的候选列表。
 * @param state 当前画布状态
 * @param targetNodeId 目标节点标识
 * @param contracts 输出契约表（用于可引用性门控与类别填充）
 * @param includeAll 为 true 时退化为全节点候选（向后兼容非拓扑场景）
 * @returns 候选列表；保持节点声明顺序，确定性输出
 */
export function collectUpstreamOutputCandidates(
  state: WorkflowCanvasState,
  targetNodeId: string,
  contracts: Readonly<Record<NodeType, NodeOutputContract>>,
  includeAll?: boolean,
): NamedOutputCandidate[] {
  const all = collectNamedOutputs(state);
  if (includeAll === true) {
    return all;
  }

  const upstream = collectUpstreamNodeIds(state.edges, targetNodeId);
  const nodeTypeById = new Map<string, NodeType>();
  for (const node of state.nodes) {
    nodeTypeById.set(node.data.node_id, node.data.node_type);
  }

  const result: NamedOutputCandidate[] = [];
  for (const candidate of all) {
    if (!upstream.has(candidate.nodeId)) {
      continue;
    }
    const nodeType = nodeTypeById.get(candidate.nodeId);
    if (nodeType === undefined) {
      continue;
    }
    const contract = contracts[nodeType];
    if (contract === undefined || contract.referable !== true) {
      continue;
    }
    result.push({ ...candidate, category: contract.category });
  }
  return result;
}

/**
 * 判定绑定引用表达式是否为可解析的形态 A（`命名输出名[.字段路径]`，CHG-F03 私有）。
 * 与后端 `parse_reference` 形态 A 分支同口径（INV-E05 单一权威的前端镜像）：
 * 首段不含 `::` 且匹配 OUTPUT_NAME_REGEX；余段非空、不含空白；
 * 路径段数 ≤ MAX_PATH_SEGMENTS；总长 ≤ MAX_EXPRESSION_LENGTH。
 */
function isFormAReferenceExpression(reference: string): boolean {
  // G2 真值守卫（fix_agent_null_length v3）：绑定引用 null（登记表透传）时
  // .trim() 抛错，非字符串一律不入形态 A（门控语义不变：null 引用不入候选）
  if (typeof reference !== "string") {
    return false;
  }
  const trimmed = reference.trim();
  if (trimmed.length === 0 || trimmed.length > MAX_EXPRESSION_LENGTH) {
    return false;
  }
  const segments = trimmed.split(".");
  const firstSegment = segments[0];
  if (firstSegment.includes(INPUT_POSITION_SEPARATOR)) {
    return false;
  }
  if (!OUTPUT_NAME_REGEX.test(firstSegment)) {
    return false;
  }
  const pathSegments = segments.slice(1);
  if (pathSegments.length > MAX_PATH_SEGMENTS) {
    return false;
  }
  for (const segment of pathSegments) {
    if (segment.length === 0 || /\s/.test(segment)) {
      return false;
    }
  }
  return true;
}

/**
 * 收集目标节点上游范围内、可一次透传解析的输入参数候选（CHG-F03，design §2.5）。
 *
 * 入候选三条件（全满足；design §2.5 / tasks T2.3）：
 *   ① 已绑定可解析——绑定引用非空，且其首段与某节点 output_name 匹配（owner 存在）；
 *   ② 绑定引用为形态 A——可被形态 A 文法解析（形态 B 绑定不入，DC-03 不递归）；
 *   ③ 绑定 owner 位于**来源节点**（输入位置所属节点）上游可达集内，
 *      且 owner 类别 referable = true（OP-08 可引用门控）。
 *
 * 口径备注：trigger 的 input_template 绑定（触发上下文字段 / 工作流级入参名）
 * 在画布状态内通常无节点 owner（画布不声明工作流级入参），条件 ① 不满足，
 * 不产生前端候选；后端经工作流级入参的接受路径由加载期校验裁决（批次 3）。
 * llm / code / vector_knowledge_base 等未登记类型经登记表安全降级，不产生候选。
 *
 * @param state 当前画布状态
 * @param targetNodeId 目标节点标识
 * @param contracts 输出契约表（用于绑定 owner 可引用性门控）
 * @returns 候选列表；保持节点声明顺序 + 字段声明顺序，确定性输出（INV-E11）；
 *          候选仅含结构性标识，不含运行期数据值（INV-E12）
 */
export function collectUpstreamInputCandidates(
  state: WorkflowCanvasState,
  targetNodeId: string,
  contracts: Readonly<Record<NodeType, NodeOutputContract>>,
): InputParameterCandidate[] {
  const upstream = collectUpstreamNodeIds(state.edges, targetNodeId);
  const result: InputParameterCandidate[] = [];

  for (const sourceNode of state.nodes) {
    if (!upstream.has(sourceNode.data.node_id)) {
      continue;
    }
    const bindings = collectNodeInputBindings(sourceNode);
    if (bindings.length === 0) {
      continue;
    }
    // 条件 ③ 的可达集按「来源节点」计算：owner 须先于来源节点执行
    const upstreamOfSource = collectUpstreamNodeIds(
      state.edges,
      sourceNode.data.node_id,
    );

    for (const binding of bindings) {
      // 条件 ①②：非空 + 形态 A 文法（与后端解析器同口径）
      if (!isFormAReferenceExpression(binding.reference)) {
        continue;
      }
      const boundOutputName = binding.reference.trim().split(".")[0];
      const owner = state.nodes.find(
        (node) => node.data.output_name === boundOutputName,
      );
      // 条件 ①：owner 存在（工作流级入参名等无节点 owner → 不入）
      if (owner === undefined) {
        continue;
      }
      // 条件 ③：owner 位于来源节点上游可达集内（不含来源节点自身）
      if (!upstreamOfSource.has(owner.data.node_id)) {
        continue;
      }
      // 条件 ③：owner 类别可引用（OP-08；未定义契约类型 → undefined 不入）
      const ownerContract = contracts[owner.data.node_type];
      if (ownerContract === undefined || ownerContract.referable !== true) {
        continue;
      }
      result.push({
        nodeId: sourceNode.data.node_id,
        nodeName: sourceNode.data.name,
        positionName: binding.positionName,
        reference: binding.reference,
        category: "输入参数",
      });
    }
  }
  return result;
}

// ---------------------------------------------------------------------------
// 输入引用合法性校验（设计 §2.2.2 接口 A3）
// ---------------------------------------------------------------------------

/** 引用合法性校验结果（五态判别联合） */
export type ReferenceValidation =
  | { kind: "valid" }
  | { kind: "unbound" }
  | { kind: "notExist"; outputName: string }
  | { kind: "notUpstream"; outputName: string }
  | { kind: "selfReference"; outputName: string };

/**
 * 形态 B（输入位置引用 `node_id::输入位置名[.字段路径]`）判定分支（CHG-F03，私有）。
 * 判定顺序（tasks T2.5 / design §2.3 规则 2′~5′ 的前端映射）：
 * 来源节点不存在 → notExist；与目标节点相同 → selfReference；
 * 非上游 → notUpstream；位置名缺失 / 绑定为空 / 绑定为形态 B → notExist
 * （规则 5′ 前端映射：加载期 ERROR 的编辑期前哨警告；ReferenceValidation
 * 不新增 kind，复用「目标不存在」语义，collectReferenceIssues / warningText 零改动）；
 * 否则 → valid。字段路径段不参与判定（与形态 A 仅取首段同口径）。
 */
function validateInputPositionReference(
  firstSegment: string,
  targetNodeId: string,
  state: WorkflowCanvasState,
): ReferenceValidation {
  const separatorIndex = firstSegment.indexOf(INPUT_POSITION_SEPARATOR);
  const scopeNodeId = firstSegment.slice(0, separatorIndex);
  const positionName = firstSegment.slice(
    separatorIndex + INPUT_POSITION_SEPARATOR.length,
  );

  const scopeNode = state.nodes.find(
    (node) => node.data.node_id === scopeNodeId,
  );
  if (scopeNode === undefined) {
    return { kind: "notExist", outputName: scopeNodeId };
  }
  if (scopeNodeId === targetNodeId) {
    return { kind: "selfReference", outputName: scopeNodeId };
  }
  const upstream = collectUpstreamNodeIds(state.edges, targetNodeId);
  if (!upstream.has(scopeNodeId)) {
    return { kind: "notUpstream", outputName: scopeNodeId };
  }

  // 规则 5′（design §2.3）：无该位置名 / 绑定为空 / 绑定为形态 B（不可一次解析）
  const binding = collectNodeInputBindings(scopeNode).find(
    (item) => item.positionName === positionName,
  );
  // G2 真值守卫（fix_agent_null_length v3）：绑定引用 null 时 .trim() 抛错，
  // 非字符串按「目标不存在」等效降级（规则 5′ 前端映射口径不变）
  if (
    binding === undefined ||
    typeof binding.reference !== "string" ||
    binding.reference.trim().length === 0 ||
    binding.reference.trim().split(".")[0].includes(INPUT_POSITION_SEPARATOR)
  ) {
    return { kind: "notExist", outputName: scopeNodeId };
  }
  return { kind: "valid" };
}

/**
 * 判定一条输入引用表达式是否合法，并区分非法类型。
 * 判定顺序（短路返回）：空串 → unbound；首段含 `::` → 形态 B 分支
 * （见 validateInputPositionReference，规则 2′~5′ 前端映射）；owner 不存在 → notExist；
 * owner 为自身 → selfReference；owner 非上游或不可引用 → notUpstream；否则 valid。
 * @param reference 引用表达式（可含点号字段路径，可为空）
 * @param targetNodeId 目标节点标识
 * @param state 当前画布状态
 * @param contracts 输出契约表（用于可引用性判定）
 * @returns 五态判别结果；绝不修改入参
 */
export function validateReference(
  reference: string,
  targetNodeId: string,
  state: WorkflowCanvasState,
  contracts: Readonly<Record<NodeType, NodeOutputContract>>,
): ReferenceValidation {
  // G2 真值守卫（fix_agent_null_length v3）：引用值 null（props 空值面）时
  // .trim() 抛错，非字符串与空串同态 → unbound（语义合理：无绑定）
  if (typeof reference !== "string") {
    return { kind: "unbound" };
  }
  const trimmed = reference.trim();
  if (trimmed.length === 0) {
    return { kind: "unbound" };
  }

  // 消歧只看首段（DC-01）：首段含 `::` → 形态 B（输入位置引用）判定分支
  const firstSegment = trimmed.split(".")[0];
  if (firstSegment.includes(INPUT_POSITION_SEPARATOR)) {
    return validateInputPositionReference(firstSegment, targetNodeId, state);
  }

  const outputName = trimmed.split(".")[0];
  const owner = state.nodes.find(
    (node) => node.data.output_name === outputName,
  );
  if (owner === undefined) {
    return { kind: "notExist", outputName };
  }
  if (owner.data.node_id === targetNodeId) {
    return { kind: "selfReference", outputName };
  }

  const upstream = collectUpstreamNodeIds(state.edges, targetNodeId);
  if (!upstream.has(owner.data.node_id)) {
    return { kind: "notUpstream", outputName };
  }

  const contract = contracts[owner.data.node_type];
  if (contract !== undefined && contract.referable === false) {
    return { kind: "notUpstream", outputName };
  }

  return { kind: "valid" };
}