// =============================================================================
// 治理前端类型定义（governanceTypes.ts）
//
// 职责：定义治理模块的 TypeScript 类型，与后端模型一一对应。
// 设计意图（对应 spec 5.5、design 2.8、任务 T74.1）：
//   1. TypeScript 严格模式，禁止 any；
//   2. 类型与后端 governance/ 模型一一对应；
//   3. 全部类型保持判别联合特性。
// =============================================================================

// ----- 枚举类型 -----

export type ResourceType =
  | 'workflow_definition'
  | 'execution_instance'
  | 'mcp_tool'
  | 'data_source_connector'
  | 'tenant_config'
  | 'audit_log'
  | 'governance_policy';

export type OperationType = 'create' | 'read' | 'update' | 'delete' | 'execute' | 'export';

export type ResourceScope = 'tenant' | 'org' | 'self';

export type TenantStatus = 'active' | 'suspended' | 'terminated';

export type UserStatus = 'active' | 'disabled' | 'locked';

export type TokenStatus = 'active' | 'expired' | 'revoked';

export type InstancePriority = 'low' | 'normal' | 'high' | 'urgent';

export type BudgetPeriod = 'daily' | 'weekly' | 'monthly' | 'yearly';

export type BudgetScope = 'tenant' | 'workflow' | 'instance';

export type CircuitBreakStrategy = 'hard_stop' | 'soft_alert';

export type MaskMethod = 'full_mask' | 'partial_mask' | 'hash' | 'replace';

export type SchedulerPolicyType = 'strict_priority' | 'fair_share' | 'weighted_queue';

export type ResourcePoolType = 'concurrency_slot' | 'cpu' | 'memory';

export type AuditOperationResult = 'success' | 'failure' | 'denied';

// ----- 领域模型接口 -----

export interface Tenant {
  tenant_id: string;
  tenant_name: string;
  status: TenantStatus;
  created_at: string;
  updated_at: string;
  is_default: boolean;
}

export interface TenantQuota {
  tenant_id: string;
  max_instances: number;
  max_workflows: number;
  max_tokens_per_day: number | null;
  max_storage_gb: number | null;
  updated_at: string;
}

export interface OrgNode {
  org_node_id: string;
  tenant_id: string;
  node_name: string;
  parent_node_id: string | null;
  node_path: string;
  created_at: string;
}

export interface User {
  user_id: string;
  tenant_id: string;
  username: string;
  display_name: string;
  email: string | null;
  org_node_id: string | null;
  status: UserStatus;
  created_at: string;
  updated_at: string;
}

export interface PermissionItem {
  permission_id: string;
  resource_type: ResourceType;
  operation: OperationType;
  scope: ResourceScope;
  description: string;
}

export interface Role {
  role_id: string;
  role_name: string;
  tenant_id: string;
  permissions: PermissionItem[];
  parent_role_id: string | null;
  is_system_role: boolean;
  created_at: string;
}

export interface RoleBinding {
  binding_id: string;
  role_id: string;
  user_id: string;
  org_node_id: string | null;
  tenant_id: string;
  created_at: string;
}

export interface AccessToken {
  token_id: string;
  user_id: string;
  tenant_id: string;
  token_hash: string;
  status: TokenStatus;
  issued_at: string;
  expires_at: string;
  revoked_at: string | null;
  last_used_at: string | null;
}

export interface TokenUsageRecord {
  record_id: string;
  tenant_id: string;
  instance_id: string;
  node_id: string;
  agent_node_id: string | null;
  model_identifier: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  recorded_at: string;
  workflow_definition_id: string | null;
}

export interface PricingRule {
  rule_id: string;
  model_identifier: string;
  prompt_token_price: number;
  completion_token_price: number;
  effective_from: string;
  effective_to: string | null;
  tenant_id: string | null;
}

export interface CostRecord {
  cost_id: string;
  tenant_id: string;
  usage_record_id: string;
  instance_id: string;
  cost_amount: number;
  pricing_rule_id: string;
  calculated_at: string;
}

export interface Budget {
  budget_id: string;
  tenant_id: string;
  scope: BudgetScope;
  target_id: string;
  period: BudgetPeriod;
  budget_limit: number;
  alert_threshold: number;
  circuit_break_strategy: CircuitBreakStrategy;
  current_usage: number;
  is_circuit_broken: boolean;
  period_start: string;
  period_end: string;
  updated_at: string;
}

export interface AuditLogEntry {
  entry_id: string;
  tenant_id: string;
  actor_id: string;
  actor_type: string;
  resource_type: ResourceType;
  resource_id: string;
  operation: OperationType;
  operation_result: AuditOperationResult;
  request_payload: Record<string, unknown> | null;
  response_summary: string | null;
  trace_id: string;
  ip_address: string | null;
  occurred_at: string;
  previous_hash: string;
  entry_hash: string;
}

export interface MaskRule {
  rule_id: string;
  tenant_id: string | null;
  field_path: string;
  mask_method: MaskMethod;
  replacement: string | null;
  keep_prefix: number;
  keep_suffix: number;
  is_active: boolean;
}

export interface SchedulerPolicy {
  policy_id: string;
  tenant_id: string | null;
  policy_type: SchedulerPolicyType;
  default_priority: InstancePriority;
  fair_weight: number;
  max_queue_size: number;
  preemption_enabled: boolean;
  preemption_max_count: number;
  decision_timeout_ms: number;
  updated_at: string;
}

export interface ResourcePool {
  pool_id: string;
  tenant_id: string | null;
  pool_type: ResourcePoolType;
  total_capacity: number;
  allocated: number;
  available: number;
  oversubscription_ratio: number;
  max_capacity: number;
  updated_at: string;
}

// ----- API 响应类型 -----

export interface ApiResponse<T> {
  data: T;
  error?: ApiError;
}

export interface ApiError {
  code: string;
  message: string;
  details: Record<string, unknown>;
}

export interface BudgetStatus {
  budget_id: string;
  current_usage: number;
  budget_limit: number;
  remaining: number;
  usage_ratio: number;
  is_circuit_broken: boolean;
  alert_threshold_reached: boolean;
}

export interface ResourcePoolUtilization {
  pool_id: string;
  pool_type: ResourcePoolType;
  total_capacity: number;
  allocated: number;
  available: number;
  utilization_ratio: number;
  max_capacity: number;
}

export interface SchedulerStatus {
  enabled: boolean;
  queue_size: number;
  running_count: number;
  policy_type: SchedulerPolicyType;
}
