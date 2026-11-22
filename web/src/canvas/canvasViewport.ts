// =============================================================================
// 画布视口持久化与初始视口决策（src/canvas/canvasViewport.ts）
//
// 职责：
//   1. 以 localStorage 持久化「上一次视口」（缩放比例 + 平移偏移），供刷新后恢复；
//   2. 以「节点 id 集合签名」判断持久化视口是否仍属于当前工作流内容；
//   3. 纯函数 resolveInitialViewportPlan 给出初始视口决策（恢复 / 适配 / 不动）。
//
// 设计约束：
//   - 持久化键与画布状态键（op_canvas_state）分离：视口是展示态，不污染工作流定义；
//   - 任何 localStorage / JSON 异常均静默降级（隐私模式、容量超限、数据损坏）；
//   - 所有数值须为有限数且 zoom 在 [VIEWPORT_MIN_ZOOM, VIEWPORT_MAX_ZOOM] 内才视为可用，
//     损坏或越界记录一律走 fit view 回落，避免"刷新后视图错乱"。
// =============================================================================

import type { Viewport } from "reactflow";

/** 视口持久化键（与 App 的 op_canvas_state 分离，二者生命周期不同） */
export const VIEWPORT_STORAGE_KEY = "op_canvas_viewport";

/** 缩放下限：大规模工作流（数十节点）需要更小的最小缩放才能整体入画 */
export const VIEWPORT_MIN_ZOOM = 0.1;

/** 缩放上限：与 React Flow 默认一致（用户手动放大上限） */
export const VIEWPORT_MAX_ZOOM = 2;

/** 适配时允许的最大缩放：小规模工作流不做夸张放大（全局上限仍为 VIEWPORT_MAX_ZOOM） */
export const VIEWPORT_FIT_MAX_ZOOM = 1.2;

/** 适配留白（对称）：四周各留 15%，避免节点贴边 */
export const VIEWPORT_FIT_PADDING = 0.15;

/** 容器尺寸与记录尺寸的相对偏差容忍度：超出则视为"窗口已改变"，改为 fit view */
export const VIEWPORT_SIZE_TOLERANCE = 0.25;

/** 容器尺寸（px） */
export interface PaneSize {
  width: number;
  height: number;
}

/** 持久化记录：视口 + 归属签名 + 保存时的容器尺寸 */
export interface StoredCanvasViewport extends Viewport {
  /** 节点 id 集合签名（见 computeWorkflowSignature） */
  signature: string;
  /** 保存时的画布容器尺寸；用于判断窗口是否已显著变化 */
  size: PaneSize | null;
}

/** 数值是否可用（有限数） */
function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

/**
 * 工作流内容签名：对排序后的节点 id 集合做 FNV-1a 32 位哈希。
 * 仅与"有哪些节点"相关，与位置/配置/连线无关——因此拖动节点、改配置、增删连线
 * 都不会使已保存的视口失效；而换一份工作流（节点集合不同）会自然失配。
 */
export function computeWorkflowSignature(
  nodes: ReadonlyArray<{ id: string }>,
): string {
  const ids = nodes.map((node) => node.id).sort();
  let hash = 0x811c9dc5;
  for (const id of ids) {
    for (let index = 0; index < id.length; index += 1) {
      hash ^= id.charCodeAt(index);
      hash = Math.imul(hash, 0x01000193);
    }
    // 分隔符：防止 ["ab","c"] 与 ["a","bc"] 哈希相同
    hash ^= 0x1f;
    hash = Math.imul(hash, 0x01000193);
  }
  return `wfp-v1:${ids.length}:${(hash >>> 0).toString(36)}`;
}

/** 视口是否可用：三项均为有限数，且 zoom 落在允许区间内 */
export function isUsableViewport(viewport: unknown): viewport is Viewport {
  if (typeof viewport !== "object" || viewport === null) return false;
  const candidate = viewport as Partial<Viewport>;
  if (
    !isFiniteNumber(candidate.x) ||
    !isFiniteNumber(candidate.y) ||
    !isFiniteNumber(candidate.zoom)
  ) {
    return false;
  }
  return candidate.zoom >= VIEWPORT_MIN_ZOOM && candidate.zoom <= VIEWPORT_MAX_ZOOM;
}

/** 读取持久化视口；无记录 / 已损坏 / 不可用时返回 null（调用方回落 fit view） */
export function readStoredViewport(): StoredCanvasViewport | null {
  try {
    const raw = localStorage.getItem(VIEWPORT_STORAGE_KEY);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null) return null;
    const record = parsed as Partial<StoredCanvasViewport>;
    if (typeof record.signature !== "string" || record.signature === "") return null;
    const viewportCandidate: unknown = { x: record.x, y: record.y, zoom: record.zoom };
    if (!isUsableViewport(viewportCandidate)) return null;
    const rawSize: unknown = record.size;
    const size =
      typeof rawSize === "object" &&
      rawSize !== null &&
      isFiniteNumber((rawSize as PaneSize).width) &&
      isFiniteNumber((rawSize as PaneSize).height) &&
      (rawSize as PaneSize).width > 0 &&
      (rawSize as PaneSize).height > 0
        ? { width: (rawSize as PaneSize).width, height: (rawSize as PaneSize).height }
        : null;
    return { ...viewportCandidate, signature: record.signature, size };
  } catch {
    // localStorage 不可用或内容损坏：静默降级为"无记录"
    return null;
  }
}

/** 写入持久化视口；不可用视口直接忽略，写入失败静默 */
export function writeStoredViewport(
  viewport: Viewport,
  signature: string,
  size: PaneSize | null,
): void {
  if (!isUsableViewport(viewport)) return;
  try {
    const record: StoredCanvasViewport = {
      x: viewport.x,
      y: viewport.y,
      zoom: viewport.zoom,
      signature,
      size,
    };
    localStorage.setItem(VIEWPORT_STORAGE_KEY, JSON.stringify(record));
  } catch {
    // 写入失败（隐私模式/容量超限）：静默忽略，本次会话仍按内存态工作
  }
}

/** 清除持久化视口（整批外部载入新内容时调用：旧视口对新内容无意义） */
export function clearStoredViewport(): void {
  try {
    localStorage.removeItem(VIEWPORT_STORAGE_KEY);
  } catch {
    // 不可用时无副作用
  }
}

/** 初始视口决策：none（不处理）/ restore（恢复上次视口）/ fit（自动适配） */
export type InitialViewportPlan =
  | { kind: "none" }
  | { kind: "restore"; viewport: Viewport }
  | { kind: "fit" };

/** 决策输入 */
export interface InitialViewportContext {
  /** 当前节点数（0 表示空画布，不做任何视口动作） */
  nodeCount: number;
  /** 是否由"整批外部载入"引起（范例加载 / YAML 导入）：内容已换，禁止恢复旧视口 */
  generationChanged: boolean;
  /** 持久化记录（无则 null） */
  storedViewport: StoredCanvasViewport | null;
  /** 当前节点集合签名 */
  signature: string;
  /** 当前容器尺寸（测不到时为 null，此时不做尺寸比较） */
  paneSize: PaneSize | null;
  /** 尺寸相对偏差容忍度，默认 VIEWPORT_SIZE_TOLERANCE */
  sizeTolerance?: number;
}

/**
 * 初始视口决策（纯函数，决定"恢复上次位置"还是"自动适配"）：
 *   1. 空画布 → none；
 *   2. 整批外部载入 → fit（换内容了，旧视口无意义）；
 *   3. 无记录 / 签名不匹配（另一份工作流）/ 记录不可用 → fit；
 *   4. 记录尺寸与当前容器偏差超阈值（换了窗口大小）→ fit（否则会偏心）；
 *   5. 其余 → restore（精确还原缩放与平移）。
 */
export function resolveInitialViewportPlan(
  context: InitialViewportContext,
): InitialViewportPlan {
  const { nodeCount, generationChanged, storedViewport, signature, paneSize } = context;
  const tolerance = context.sizeTolerance ?? VIEWPORT_SIZE_TOLERANCE;
  if (nodeCount <= 0) return { kind: "none" };
  if (generationChanged) return { kind: "fit" };
  if (storedViewport === null) return { kind: "fit" };
  if (storedViewport.signature !== signature) return { kind: "fit" };
  if (!isUsableViewport(storedViewport)) return { kind: "fit" };
  if (storedViewport.size && paneSize) {
    const widthDrift = Math.abs(paneSize.width - storedViewport.size.width) / paneSize.width;
    const heightDrift =
      Math.abs(paneSize.height - storedViewport.size.height) / paneSize.height;
    if (widthDrift > tolerance || heightDrift > tolerance) return { kind: "fit" };
  }
  return {
    kind: "restore",
    viewport: { x: storedViewport.x, y: storedViewport.y, zoom: storedViewport.zoom },
  };
}
