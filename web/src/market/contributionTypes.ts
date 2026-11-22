// =============================================================================
// 贡献市场类型定义（web/src/market/contributionTypes.ts）
//
// 与后端 Pydantic 模型一一对应（TypeScript 严格模式）
// =============================================================================

/** 贡献状态 */
export type ContributionState =
  | 'draft'
  | 'submitted'
  | 'security_scan'
  | 'reviewing'
  | 'approved'
  | 'rejected'
  | 'published';

/** 贡献者类型 */
export type ContributorType = 'third_party' | 'community';

/** 签名信息 */
export interface SignatureInfo {
  algorithm: string;
  signature_value: string;
  signed_by: string;
  created_at: string;
}

/** 扫描单项发现 */
export interface ScanFinding {
  rule_id: string;
  severity: string;
  message: string;
  location: string | null;
}

/** 扫描报告 */
export interface ScanReport {
  report_id: string;
  passed: boolean;
  findings: ScanFinding[];
  scanned_at: string;
  scanner_version: string;
  error: string | null;
}

/** 状态流转记录 */
export interface StateTransitionRecord {
  from_state: ContributionState;
  to_state: ContributionState;
  operator_id: string;
  reason: string;
  occurred_at: string;
}

/** 插件 manifest */
export interface PluginManifest {
  node_name: string;
  version: string;
  description: string;
  input_schema: Record<string, string>;
  output_schema: Record<string, string>;
  dependencies: string[];
  declared_file_paths: string[];
  declared_network_targets: string[];
  entry_point: string;
}

/** 节点贡献 */
export interface NodeContribution {
  contribution_id: string;
  node_name: string;
  version: string;
  contributor_id: string;
  contributor_type: ContributorType;
  state: ContributionState;
  manifest: PluginManifest | null;
  package_digest: string;
  signature: SignatureInfo | null;
  scan_reports: ScanReport[];
  state_history: StateTransitionRecord[];
  rejection_reason: string;
  created_at: string;
  updated_at: string;
}

/** 贡献列表响应 */
export interface ContributionListResponse {
  contributions: NodeContribution[];
  total: number;
}

/** 提交响应 */
export interface SubmitResponse {
  contribution_id: string;
  state: string;
  accepted: boolean;
  rejection_reason: string;
}

/** 流转响应 */
export interface TransitionResponse {
  contribution_id: string;
  from_state: string;
  to_state: string;
  success: boolean;
  error_message: string;
}

/** 历史响应 */
export interface HistoryResponse {
  contribution_id: string;
  history: StateTransitionRecord[];
}
