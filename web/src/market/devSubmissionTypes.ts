// =============================================================================
// 用户自主开发类型定义（web/src/market/devSubmissionTypes.ts）
//
// 与后端 market/dev_models.py 响应模型一一对应（TypeScript 严格模式，无宽泛类型）
// 对应端点：DV-1 ~ DV-8（design §2.2.2）
// =============================================================================

/** 校验层次 */
export type DevLayer = 'L1' | 'L2' | 'L3';

/** 发现项严重级别 */
export type DevSeverity = 'error' | 'warning' | 'info';

/** 提交状态（复用后端 ContributionState 七态） */
export type DevContributionState =
  | 'draft'
  | 'submitted'
  | 'security_scan'
  | 'reviewing'
  | 'approved'
  | 'rejected'
  | 'published';

/** 三层校验发现项 */
export interface DevFinding {
  layer: DevLayer;
  rule_id: string;
  severity: DevSeverity;
  message: string;
  location: string | null;
}

/** 三层校验统一报告 */
export interface DevValidationReport {
  report_id: string;
  valid: boolean;
  layers: string[];
  findings: DevFinding[];
  scanner_version: string;
  checked_at: string;
  error: string | null;
}

/** 脚手架文件内容模板 */
export interface DevFileTemplate {
  path: string;
  content: string;
  required: boolean;
}

/** 脚手架清单条目（DV-1） */
export interface DevScaffoldSummary {
  scaffold_id: string;
  name: string;
  version: string;
  summary: string;
  required_files: string[];
}

/** DV-1 脚手架清单响应 */
export interface DevScaffoldListResponse {
  scaffolds: DevScaffoldSummary[];
  total: number;
}

/** DV-2 脚手架详情（五要素 + 文件模板内联 + 获取指引） */
export interface DevScaffoldDetail {
  scaffold_id: string;
  name: string;
  version: string;
  summary: string;
  directory_structure: string;
  required_files: string[];
  optional_files: string[];
  manifest_spec: string;
  interface_contract: string;
  dependency_and_runtime: string;
  file_templates: DevFileTemplate[];
  fetch_hint: string;
}

/** 状态流转记录（复用既有 StateTransitionRecord 语义） */
export interface DevStateTransitionRecord {
  from_state: DevContributionState;
  to_state: DevContributionState;
  operator_id: string;
  reason: string;
  occurred_at: string;
}

/** 开发提交主体（spec §9.2） */
export interface DevSubmission {
  submission_id: string;
  developer_id: string;
  node_name: string;
  version: string;
  package_digest: string;
  package_size: number;
  file_count: number;
  state: DevContributionState;
  validation_reports: DevValidationReport[];
  rejection_reason: string;
  state_history: DevStateTransitionRecord[];
  created_at: string;
  updated_at: string;
}

/** DV-3 提交受理响应 */
export interface DevSubmissionResponse {
  submission_id: string;
  state: DevContributionState;
  accepted: boolean;
  validation_report: DevValidationReport;
  rejection_reason: string;
  package_digest: string;
  package_size: number;
  file_count: number;
}

/** DV-4 提交清单响应 */
export interface DevSubmissionListResponse {
  submissions: DevSubmission[];
  total: number;
}

/** DV-5 提交详情响应 */
export interface DevSubmissionDetailResponse {
  submission: DevSubmission;
  latest_report: DevValidationReport | null;
  source_kind: string;
}

/** DV-6 审核流转响应 */
export interface DevTransitionResponse {
  submission_id: string;
  from_state: DevContributionState;
  to_state: DevContributionState;
  success: boolean;
  message: string;
}

/** DV-8 流转历史响应 */
export interface DevHistoryResponse {
  submission_id: string;
  history: DevStateTransitionRecord[];
}
