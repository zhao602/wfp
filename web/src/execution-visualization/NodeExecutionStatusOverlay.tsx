// =============================================================================
// 节点执行状态叠加组件（src/execution-visualization/NodeExecutionStatusOverlay.tsx）
//
// 职责：将节点状态映射为待执行、执行中、成功、失败、已跳过五种视觉态。
// 设计意图（对应需求规格 5.5.1.7、任务 T20.2）：
//   1. 订阅 SSE 事件流，按事件更新节点状态映射；
//   2. 状态更新延迟 ≤ 1s（SSE 推送保障）；
//   3. 以叠加层形式在画布右上角展示状态图例与实时计数。
// 增量（run_workflow_error_feedback）：在五态计数下方追加"执行失败反馈区"，
//   捕获节点级 / 实例级失败原因与连接异常并作非空呈现，消除"运行失败却无提示"的静默失败。
// 增量（optimize_output_display_node CHG-11）：按节点 output_mode（归一化，缺省 ["screen"]）
//   决定输出展示条目渲染；含 "file" 时呈现"下载"按钮（就地六步下载，DC-08/INV-D20）；
//   NODE_COMPLETED 载荷的 display_content 判定键不变（DC-09）。
// =============================================================================

import { useEffect, useState, useCallback, useRef } from "react";
import { useThemeMode, darkTokens } from "../theme/themeMode";
import { ExecutionEventStreamClient } from "./ExecutionEventStreamClient";
import { orchestrationApiClient } from "../api/orchestrationApiClient";
import {
  extractError,
  formatNodeFailure,
  formatInstanceFailure,
  mergeNodeFailure,
  type NodeFailureEntry,
} from "./failureFeedback";
import type { WorkflowCanvasState, NodeStatusMap } from "../canvas/canvasTypes";
import type { NodeExecutionStatus, ExecutionEvent } from "../api/apiTypes";
import { normalizeOutputMode } from "../workflow-definition/outputDisplayConfig";

/** 状态叠加组件属性 */
interface NodeExecutionStatusOverlayProps {
  instanceId: string;
  canvasState: WorkflowCanvasState;
}

/** 状态颜色映射 */
const STATUS_COLORS: Record<NodeExecutionStatus, string> = {
  pending: "#9e9e9e",
  running: "#1976d2",
  succeeded: "#388e3c",
  failed: "#d32f2f",
  skipped: "#757575",
};

/** 状态中文标签 */
const STATUS_LABELS: Record<NodeExecutionStatus, string> = {
  pending: "待执行",
  running: "执行中",
  succeeded: "成功",
  failed: "失败",
  skipped: "已跳过",
};

/**
 * 读取节点输出方式集合（经 normalizeOutputMode 归一化，缺省/未知 → ["screen"]）。
 * 节点已不在画布（画布编辑后）或配置未声明 output_mode → 兼容旧语义（仅屏幕）。
 */
function readOutputMode(canvas: WorkflowCanvasState, nodeId: string): string[] {
  const config = canvas.nodes.find((node) => node.id === nodeId)?.data
    .configuration;
  if (
    config &&
    config.node_type === "output_display" &&
    Array.isArray(config.output_mode)
  ) {
    return normalizeOutputMode(config.output_mode);
  }
  return normalizeOutputMode(undefined);
}

/** 构造下载文件名：节点标识 + 净化后展示标题（非法字符替换下划线），保唯一与可读。 */
function buildDownloadFilename(nodeId: string, title: string): string {
  const safeTitle = title
    .replace(/[\r\n\\/:*?"<>|\s]+/g, "_")
    .replace(/^_+|_+$/g, "");
  return safeTitle ? `${nodeId}-${safeTitle}.json` : `${nodeId}.json`;
}

/**
 * 就地下载实现（DC-08：不抽共享模块、不改 AgentPerformancePanel.downloadFile、不改 App.tsx）。
 * 六步：display_content 序列化 → Blob → createObjectURL → a.download → click → revokeObjectURL。
 * INV-D20 登记：文件产出由用户在前端浏览器主动触发，发生在节点执行之外，
 * 不属节点执行副作用（底座 §4.3-1"禁止写文件"的边界=节点执行器内，DC-08 备选 A）。
 */
function downloadOutputContent(content: unknown, filename: string): void {
  const blob = new Blob([JSON.stringify(content, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

/** 将事件类型映射为节点状态 */
function eventToStatus(eventType: string): NodeExecutionStatus | null {
  switch (eventType) {
    case "NODE_STARTED":
      return "running";
    case "NODE_COMPLETED":
      return "succeeded";
    case "NODE_FAILED":
      return "failed";
    case "NODE_SKIPPED":
      return "skipped";
    default:
      return null;
  }
}

/**
 * 失败反馈区样式注入块（随叠加层挂载注入、卸载复位）：
 * 类选择器统一 `wfp-overlay-` 前缀 + BEM，与 `wfp-footer-` / `wfp-topbar-` 前缀隔离（DC-4）；
 * 浅色规则用既有字面量、深色规则追加 `.wfp-theme--dark` 作用域，双轨取色复用 `darkTokens`（DC-5）。
 */
const overlayCss = `
.wfp-overlay-feedback{margin-top:8px;padding-top:8px;border-top:1px solid #e0e0e0;max-width:320px;}
.wfp-overlay-feedback__list{margin:0;padding:0;list-style:none;}
.wfp-overlay-feedback__item{color:#d32f2f;font-size:12px;line-height:1.5;white-space:pre-wrap;word-break:break-word;max-height:160px;overflow-y:auto;}
.wfp-overlay-feedback--instance{color:#d32f2f;font-size:12px;line-height:1.5;margin-top:4px;white-space:pre-wrap;word-break:break-word;max-height:160px;overflow-y:auto;}
.wfp-overlay-feedback--connection{color:#9e9e9e;font-size:12px;line-height:1.5;margin-top:4px;}
.wfp-theme--dark .wfp-overlay-feedback{border-top-color:${darkTokens.surface.divider};}
.wfp-theme--dark .wfp-overlay-feedback__item{color:${darkTokens.danger};}
.wfp-theme--dark .wfp-overlay-feedback--instance{color:${darkTokens.danger};}
.wfp-theme--dark .wfp-overlay-feedback--connection{color:${darkTokens.text.secondary};}
/* —— 输出展示区：独立类族 wfp-overlay-output，不复用 wfp-overlay-feedback（INV-14）—— */
.wfp-overlay-output{margin-top:8px;padding-top:8px;border-top:1px solid #e0e0e0;max-width:320px;}
.wfp-overlay-output__title{font-weight:600;font-size:12px;margin-bottom:2px;color:#333;}
.wfp-overlay-output__item{margin-bottom:6px;}
.wfp-overlay-output__content{font-size:11px;line-height:1.4;white-space:pre-wrap;word-break:break-word;max-height:200px;overflow-y:auto;background:#f5f5f5;border-radius:3px;padding:4px 6px;color:#555;}
.wfp-overlay-output__empty{font-size:11px;color:#999;font-style:italic;}
.wfp-overlay-output__download{margin:2px 0 4px;font-size:11px;padding:2px 10px;border:1px solid #1976d2;border-radius:3px;background:#ffffff;color:#1976d2;cursor:pointer;}
.wfp-overlay-output__download:hover{background:#e3f2fd;}
.wfp-theme--dark .wfp-overlay-output{border-top-color:${darkTokens.surface.divider};}
.wfp-theme--dark .wfp-overlay-output__title{color:${darkTokens.text.primary};}
.wfp-theme--dark .wfp-overlay-output__content{background:${darkTokens.surface.bgHover};color:${darkTokens.text.secondary};}
.wfp-theme--dark .wfp-overlay-output__empty{color:${darkTokens.text.hint};}
.wfp-theme--dark .wfp-overlay-output__download{border-color:${darkTokens.status.running};background:${darkTokens.surface.bg};color:${darkTokens.status.running};}
.wfp-theme--dark .wfp-overlay-output__download:hover{background:${darkTokens.surface.bgHover};}
`;

/** 节点执行状态叠加层 */
export default function NodeExecutionStatusOverlay({
  instanceId,
  canvasState,
}: NodeExecutionStatusOverlayProps) {
  const themeMode = useThemeMode();
  const isDark = themeMode === "dark";
  const [statusMap, setStatusMap] = useState<NodeStatusMap>({});
  /** 节点级失败条目列表（按 nodeId 去重、保留最早，DC-11） */
  const [nodeFailures, setNodeFailures] = useState<NodeFailureEntry[]>([]);
  /** 实例级失败提示文本（追加不覆盖节点级信息，DC-3/DC-11） */
  const [instanceFailure, setInstanceFailure] = useState<string | null>(null);
  /** 连接异常提示（仅 SSE 重连耗尽时置真，DC-8/AC-16） */
  const [connectionLost, setConnectionLost] = useState(false);
  /** 输出展示节点产出：nodeId → { title, content }（DC-9，独立于失败反馈区 INV-14） */
  const [nodeOutputs, setNodeOutputs] = useState<Record<string, { title: string; content: unknown }>>({});

  /** 最新画布状态引用：供事件回调解析节点展示名，避免因画布编辑导致订阅重建 */
  const canvasStateRef = useRef(canvasState);
  canvasStateRef.current = canvasState;
  /** 本轮是否已持有任一失败原因（用于 STREAM_END 兜底判定，DC-6） */
  const hasFailureRef = useRef(false);
  /** 本轮是否已触发实例终态兜底查询（按 instanceId 去重，DC-6/DC-11） */
  const fallbackQueriedRef = useRef(false);

  /**
   * 实例终态兜底查询：事件流未提供失败原因时以既有 getInstance 补齐失败感知（DC-6/AC-15）。
   * 一轮执行内仅触发一次；查询失败保留既有提示、不静默、不产生空白（AC-18/6.4.3）。
   */
  const triggerFallbackQuery = useCallback(() => {
    if (fallbackQueriedRef.current) return;
    fallbackQueriedRef.current = true;
    orchestrationApiClient
      .getInstance(instanceId)
      .then((res) => {
        if (res.status === "failed") {
          hasFailureRef.current = true;
          setInstanceFailure((prev) => prev ?? formatInstanceFailure(""));
        }
      })
      .catch(() => {
        // 兜底查询失败（404/网络异常）：保留既有失败/连接提示，不静默
      });
  }, [instanceId]);

  /** SSE 重连耗尽：呈现连接异常提示并触发终态兜底查询（DC-8/AC-16） */
  const handleRetryExhausted = useCallback(() => {
    setConnectionLost(true);
    triggerFallbackQuery();
  }, [triggerFallbackQuery]);

  /** 处理 SSE 事件：节点级/实例级两级分流，失败原因留存（DC-2/DC-3） */
  const handleEvent = useCallback((event: ExecutionEvent) => {
    // 收到任一事件即视为连接恢复：清除连接异常提示（AC-17）
    setConnectionLost(false);

    if (event.node_id) {
      // —— 节点分支：状态映射与改动前逐字符等价（INV-4/DC-9）——
      const status = eventToStatus(event.event_type);
      if (!status) return;
      const nodeId = event.node_id;
      setStatusMap((prev) => ({ ...prev, [nodeId]: status }));
      // 输出展示节点完成事件：提取 display_content/display_title（DC-9，仅追加不修改既有逻辑）
      if (event.event_type === "NODE_COMPLETED" && event.payload && "display_content" in event.payload) {
        const displayContent = event.payload.display_content;
        const displayTitle = (event.payload.display_title as string) ??
          canvasStateRef.current.nodes.find((n) => n.id === nodeId)?.data.name ?? nodeId;
        setNodeOutputs((prev) => ({
          ...prev,
          [nodeId]: { title: displayTitle, content: displayContent },
        }));
      }
      // 节点失败：并存提取失败原因，不再丢弃 payload.error（DC-2/AC-5）
      if (event.event_type === "NODE_FAILED") {
        const rawError = extractError(event.payload);
        const displayName = canvasStateRef.current.nodes.find(
          (node) => node.id === nodeId,
        )?.data.name;
        const entry: NodeFailureEntry = {
          nodeId,
          displayName,
          reason: formatNodeFailure(nodeId, rawError, displayName),
        };
        hasFailureRef.current = true;
        setNodeFailures((prev) => mergeNodeFailure(prev, entry));
      }
      return;
    }

    // —— 无 node_id 分支：仅处理实例级失败，其余安全忽略（DC-3）——
    if (event.event_type === "INSTANCE_FAILED") {
      hasFailureRef.current = true;
      setInstanceFailure((prev) => prev ?? formatInstanceFailure(extractError(event.payload)));
      return;
    }
    if (event.event_type === "STREAM_END") {
      // 事件流正常收尾：仅当尚无任何失败原因时触发终态兜底查询（DC-6）
      if (!hasFailureRef.current) {
        triggerFallbackQuery();
      }
      return;
    }
  }, [triggerFallbackQuery]);

  /** 订阅 SSE 事件流 */
  useEffect(() => {
    // 新一轮运行（instanceId 变更）：复位上一轮失败态（DC-11/AC-13）
    setNodeFailures([]);
    setInstanceFailure(null);
    setConnectionLost(false);
    setNodeOutputs({});
    hasFailureRef.current = false;
    fallbackQueriedRef.current = false;

    const client = new ExecutionEventStreamClient({
      instanceId,
      onEvent: handleEvent,
      onRetryExhausted: handleRetryExhausted,
    });
    client.connect();
    return () => client.disconnect();
  }, [instanceId, handleEvent, handleRetryExhausted]);

  /** 统计各状态节点数 */
  const statusCounts = Object.values(statusMap).reduce(
    (acc, status) => {
      acc[status] = (acc[status] ?? 0) + 1;
      return acc;
    },
    {} as Record<string, number>,
  );

  /** 是否存在任一失败信息或连接异常（决定反馈区是否渲染，Q-2/AC-10） */
  const hasFeedback =
    nodeFailures.length > 0 || instanceFailure !== null || connectionLost;

  return (
    <div
      style={{
        position: "absolute",
        top: "8px",
        right: "8px",
        background: isDark ? "rgba(30,30,30,0.95)" : "rgba(255, 255, 255, 0.95)",
        border: `1px solid ${isDark ? darkTokens.surface.divider : "#e0e0e0"}`,
        borderRadius: "4px",
        padding: "8px 12px",
        fontSize: "12px",
        color: isDark ? darkTokens.text.primary : undefined,
        zIndex: 10,
      }}
    >
      <style>{overlayCss}</style>
      <div style={{ fontWeight: 600, marginBottom: "4px" }}>执行状态</div>
      {(Object.keys(STATUS_LABELS) as NodeExecutionStatus[]).map((status) => (
        <div key={status} style={{ display: "flex", alignItems: "center", gap: "4px" }}>
          <span
            style={{
              display: "inline-block",
              width: "8px",
              height: "8px",
              borderRadius: "50%",
              background: isDark ? darkTokens.status[status] : STATUS_COLORS[status],
            }}
          />
          {STATUS_LABELS[status]}: {statusCounts[status] ?? 0}
        </div>
      ))}
      {Object.keys(nodeOutputs).length > 0 && (
        <div className="wfp-overlay-output">
          <div className="wfp-overlay-output__title">输出展示</div>
          {Object.entries(nodeOutputs).map(([nodeId, output]) => {
            // CHG-11：按节点 output_mode 决定渲染（screen → 内容条目 / file → 下载按钮）
            const modes = readOutputMode(canvasState, nodeId);
            const showContent = modes.includes("screen");
            const showDownload = modes.includes("file");
            return (
              <div key={nodeId} className="wfp-overlay-output__item">
                <div className="wfp-overlay-output__title">{output.title}</div>
                {showDownload && (
                  <button
                    type="button"
                    className="wfp-overlay-output__download"
                    onClick={() =>
                      downloadOutputContent(
                        output.content,
                        buildDownloadFilename(nodeId, output.title),
                      )
                    }
                  >
                    下载
                  </button>
                )}
                {showContent &&
                  (output.content === null || output.content === undefined ? (
                    <div className="wfp-overlay-output__empty">（无输出）</div>
                  ) : typeof output.content === "string" ? (
                    <div className="wfp-overlay-output__content">{output.content}</div>
                  ) : (
                    <div className="wfp-overlay-output__content">
                      {JSON.stringify(output.content, null, 2)}
                    </div>
                  ))}
              </div>
            );
          })}
        </div>
      )}
      {hasFeedback && (
        <div className="wfp-overlay-feedback">
          {nodeFailures.length > 0 && (
            <ul className="wfp-overlay-feedback__list">
              {nodeFailures.map((entry) => (
                <li key={entry.nodeId} className="wfp-overlay-feedback__item">
                  {entry.reason}
                </li>
              ))}
            </ul>
          )}
          {instanceFailure !== null && (
            <div className="wfp-overlay-feedback--instance">{instanceFailure}</div>
          )}
          {connectionLost && (
            <div className="wfp-overlay-feedback--connection">执行状态连接已断开</div>
          )}
        </div>
      )}
    </div>
  );
}
