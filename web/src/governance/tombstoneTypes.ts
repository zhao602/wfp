// =============================================================================
// 墓碑前端类型定义（tombstoneTypes.ts）
// =============================================================================

export interface TombstoneResult {
  success: boolean;
  entry_id: string;
  error_code?: string | null;
}

export interface ForgetResult {
  success_count: number;
  failed_count: number;
  total_count: number;
  operation_log_id: string;
}

export interface TombstoneMetadataView {
  entry_id: string;
  deleted_at?: string | null;
  deleted_by?: string | null;
  forget_operation_id?: string | null;
  tenant_id: string;
  occurred_at: string;
}

export interface MarkTombstoneRequest {
  entry_id: string;
  operator_id: string;
  tenant_id: string;
}

export interface ForgetUserRequest {
  user_id: string;
  operator_id: string;
  tenant_id: string;
}
