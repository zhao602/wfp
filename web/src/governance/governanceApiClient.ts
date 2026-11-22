// =============================================================================
// 治理 API 客户端封装（governanceApiClient.ts）
//
// 职责：封装全部 /api/v1/governance/** 接口的 TypeScript 客户端。
// 设计意图（对应 spec 5.5、design 2.8、任务 T74.2 / B1 批次 T3.2）：
//   1. 请求管线统一于 httpClient 基元（错误映射 / Content-Type 恒定），
//      本模块仅保留端点定义与令牌内存态（setToken/clearToken，非 localStorage 模式）；
//   2. 错误类型统一 ApiError 族：HTTP 级错误抛 GovernanceApiError（类名与 import
//      路径保留，instanceof 兼容既有契约），传输级错误（网络失败）抛 ApiError 基类；
//   3. 无超时无重试（沿用现值，HC-01 行为兼容红线）。
// =============================================================================

import type {
  AuditLogEntry,
  BudgetStatus,
  ResourcePoolUtilization,
  SchedulerStatus,
  Tenant,
} from './governanceTypes';
import { ApiError } from '../api/apiError';
import { createHttpClient } from '../api/httpClient';
import type { ApiErrorFactory } from '../api/httpClient';

const GOVERNANCE_API_BASE = '/api/v1/governance';

// =============================================================================
// 错误处理
// =============================================================================

export class GovernanceApiError extends ApiError {
  constructor(
    code: string,
    message: string,
    details: Record<string, unknown> = {},
    httpStatus?: number,
  ) {
    super(code, message, details, httpStatus);
    this.name = 'GovernanceApiError';
  }
}

// =============================================================================
// HTTP 客户端（基元管线；令牌内存态经 getAccessToken provider 注入）
// =============================================================================

let governanceToken: string | null = null;

const errorFactory: ApiErrorFactory = (code, message, details, httpStatus, layer) =>
  httpStatus === undefined
    ? new ApiError(code, message, details, undefined, layer)
    : new GovernanceApiError(code, message, details, httpStatus);

const client = createHttpClient({
  baseUrl: GOVERNANCE_API_BASE,
  getAccessToken: () => governanceToken ?? '',
  errorFactory,
});

// =============================================================================
// 治理 API 客户端
// =============================================================================

export const governanceApiClient = {
  // ----- 认证 -----
  auth: {
    async issueToken(userId: string, tenantId: string, ttlSeconds?: number): Promise<{ token: string; token_type: string }> {
      return client.request<{ token: string; token_type: string }>({
        path: '/auth/token',
        method: 'POST',
        body: {
          user_id: userId,
          tenant_id: tenantId,
          ttl_seconds: ttlSeconds,
        },
      });
    },

    async revokeToken(tokenId: string): Promise<{ status: string }> {
      return client.request<{ status: string }>({
        path: '/auth/token/revoke',
        method: 'POST',
        body: { token_id: tokenId },
      });
    },
  },

  // ----- 令牌管理 -----
  setToken(token: string): void {
    governanceToken = token;
  },

  clearToken(): void {
    governanceToken = null;
  },

  // ----- 租户管理 -----
  tenants: {
    async create(tenantId: string, tenantName: string): Promise<Tenant> {
      return client.request<Tenant>({
        path: '/tenants',
        method: 'POST',
        body: {
          tenant_id: tenantId,
          tenant_name: tenantName,
        },
      });
    },

    async list(): Promise<{ tenants: Tenant[] }> {
      return client.request<{ tenants: Tenant[] }>({ path: '/tenants' });
    },
  },

  // ----- 用量查询 -----
  usage: {
    async getSummary(tenantId: string, instanceId?: string): Promise<Record<string, unknown>> {
      const params = instanceId ? `?tenant_id=${tenantId}&instance_id=${instanceId}` : `?tenant_id=${tenantId}`;
      return client.request<Record<string, unknown>>({ path: `/usage${params}` });
    },
  },

  // ----- 成本查询 -----
  costs: {
    async getSummary(tenantId: string): Promise<Record<string, unknown>> {
      return client.request<Record<string, unknown>>({ path: `/costs?tenant_id=${tenantId}` });
    },
  },

  // ----- 预算管理 -----
  budgets: {
    async getStatus(budgetId: string): Promise<BudgetStatus> {
      return client.request<BudgetStatus>({ path: `/budgets/${budgetId}/status` });
    },
  },

  // ----- 审计日志 -----
  auditLogs: {
    async query(params: {
      tenantId?: string;
      startTime?: string;
      endTime?: string;
      limit?: number;
    }): Promise<{ entries: AuditLogEntry[] }> {
      const queryParts: string[] = [];
      if (params.tenantId) queryParts.push(`tenant_id=${params.tenantId}`);
      if (params.startTime) queryParts.push(`start_time=${params.startTime}`);
      if (params.endTime) queryParts.push(`end_time=${params.endTime}`);
      if (params.limit) queryParts.push(`limit=${params.limit}`);
      const query = queryParts.length > 0 ? `?${queryParts.join('&')}` : '';
      return client.request<{ entries: AuditLogEntry[] }>({ path: `/audit-logs${query}` });
    },

    async export(params: {
      tenantId?: string;
      startTime?: string;
      endTime?: string;
      limit?: number;
    }): Promise<{ exported_data: string; record_count: number }> {
      return client.request<{ exported_data: string; record_count: number }>({
        path: '/audit-logs/export',
        method: 'POST',
        body: params,
      });
    },
  },

  // ----- 调度策略 -----
  scheduler: {
    async getPolicy(): Promise<SchedulerStatus> {
      return client.request<SchedulerStatus>({ path: '/scheduler/policy' });
    },
  },

  // ----- 资源池 -----
  resourcePools: {
    async getStatus(): Promise<{ pools: ResourcePoolUtilization[] }> {
      return client.request<{ pools: ResourcePoolUtilization[] }>({ path: '/resource-pools' });
    },
  },
};
