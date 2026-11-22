// =============================================================================
// 沙箱类型定义（web/src/sandbox/sandboxTypes.ts）
//
// 与后端 Pydantic 模型一一对应（TypeScript 严格模式）
// =============================================================================

/** 隔离模式 */
export type IsolationMode = 'logical' | 'physical';

/** 网络策略模式 */
export type NetworkPolicyMode = 'default_deny' | 'whitelist';

/** 违规类型 */
export type ViolationType = 'file_escape' | 'network_denied' | 'resource_exceeded' | 'timeout';

/** 沙箱策略 */
export interface SandboxPolicy {
  policy_id: string;
  tenant_id: string;
  name: string;
  description: string;
  isolation_mode: IsolationMode;
  allow_degrade: boolean;
  cpu_seconds_limit: number;
  memory_mb_limit: number;
  output_bytes_limit: number;
  execution_timeout_seconds: number;
  workdir: string;
  network_policy: NetworkPolicyMode;
  network_whitelist: string[];
  allowed_path_prefixes: string[];
  is_active: boolean;
}

/** 创建策略请求 */
export interface CreatePolicyRequest {
  tenant_id: string;
  name: string;
  description: string;
  isolation_mode: IsolationMode;
  allow_degrade: boolean;
  cpu_seconds_limit: number;
  memory_mb_limit: number;
  output_bytes_limit: number;
  execution_timeout_seconds: number;
  workdir: string;
  network_policy: NetworkPolicyMode;
  network_whitelist: string[];
  allowed_path_prefixes: string[];
}

/** 策略响应 */
export interface PolicyResponse {
  policy_id: string;
  tenant_id: string;
  name: string;
  description: string;
  isolation_mode: IsolationMode;
  is_active: boolean;
}

/** 违规记录 */
export interface ViolationRecord {
  record_id: string;
  session_id: string;
  tenant_id: string;
  policy_id: string;
  violation_type: ViolationType;
  detail: string;
  occurred_at: string;
}

/** 沙箱状态 */
export interface SandboxStatus {
  sandbox_enabled: boolean;
  default_isolation: string;
  subprocess_mode: boolean;
  degraded: {
    sandbox_degraded: boolean;
    degraded_components: Array<{
      component: string;
      reason: string;
      mode: string;
    }>;
    message: string;
  };
}
