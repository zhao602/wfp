// =============================================================================
// ABAC 前端类型定义（abacTypes.ts）
//
// 职责：定义 ABAC 模块的 TypeScript 类型，与后端模型一一对应。
// =============================================================================

export type AttributeSource = 'subject' | 'resource' | 'environment';
export type PolicyEffect = 'allow' | 'deny';
export type FailMode = 'deny' | 'allow';
export type ConditionOperator =
  | 'eq' | 'neq' | 'in' | 'not_in' | 'range' | 'ip_in_range' | 'time_in_range';

export interface AbacCondition {
  attribute_name: string;
  attribute_source: AttributeSource;
  operator: ConditionOperator;
  expected_value: string;
}

export interface AbacPolicy {
  policy_id: string;
  tenant_id: string;
  conditions: AbacCondition[];
  effect: PolicyEffect;
  fail_mode: FailMode;
  priority: number;
  is_active: boolean;
  description: string;
}

export interface CreatePolicyRequest {
  policy_id: string;
  tenant_id: string;
  conditions: AbacCondition[];
  effect: PolicyEffect;
  fail_mode: FailMode;
  priority: number;
  description: string;
}

export interface PolicyResponse {
  policy_id: string;
  tenant_id: string;
  effect: PolicyEffect;
  fail_mode: FailMode;
  priority: number;
  is_active: boolean;
  description: string;
  conditions_count: number;
}
