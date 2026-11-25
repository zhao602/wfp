// =============================================================================
// 审批队列类型（web/src/approval-queue/approvalQueueTypes.ts，B16 F4）
//
// 对齐后端 approval_router.py 契约：
//   GET  /api/v1/approvals            → { approvals: ApprovalRow[] }
//   POST /api/v1/approvals/batch-review → BatchReviewResponse
// 行字段与 ApprovalQueueService._build_row 一一对应；
// definition_version 为条件键（后端 None 时省略，B11 先例）。
// =============================================================================

/** 待审批行（队列聚合：注册表模式 + worker 模式双源去重） */
export interface ApprovalRow {
  instance_id: string;
  definition_id: string;
  /** 条件键：实例未携带定义版本时后端省略（B11 条件键先例） */
  definition_version?: number;
  tenant_id: string | null;
  status: string;
  checkpoint_id: string;
  /** ISO 时间戳；挂起载荷显式键缺失时后端回退事件时间 */
  suspended_at: string | null;
  /** 复核超时秒数（settings.review_timeout_seconds，默认 3600） */
  review_timeout_seconds: number;
  /** 截止时刻 ISO 时间戳；缺 suspended_at / 非法 / 非正值时为 null */
  deadline: string | null;
  /** 超时动作（review_timeout_action，如 reject / fail） */
  timeout_action: string;
  /** 复核说明（review 节点 instructions，可为空串） */
  review_instructions: string;
}

/** 批量复核决策（与后端 ReviewDecision 字面量一致） */
export type ReviewDecision = 'approve' | 'reject' | 'timeout_action';

/** 批量复核请求体（instance_ids 1..100，后端 422 校验） */
export interface BatchReviewRequest {
  instance_ids: string[];
  decision: ReviewDecision;
  actor: string;
  comment?: string;
}

/** 单实例复核结果项 */
export interface BatchReviewItemResult {
  instance_id: string;
  status: 'ok' | 'skipped' | 'error';
  result?: Record<string, unknown>;
  http_status?: number;
  error?: string;
}

/** 批量复核汇总（total 按输入项计，含重复 id） */
export interface BatchReviewSummary {
  total: number;
  ok: number;
  skipped: number;
  error: number;
}

/** 批量复核响应体 */
export interface BatchReviewResponse {
  results: BatchReviewItemResult[];
  summary: BatchReviewSummary;
}
