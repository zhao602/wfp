// =============================================================================
// Agent 发现 API 客户端（web/src/agent_discovery/discoveryApiClient.ts）
//
// 设计意图（B1 批次 T3.2）：
//   1. 请求管线统一于 httpClient 基元；本模块仅保留端点定义；
//   2. 原实现无鉴权——基元默认 token 注入经空 provider 压制（禁 localStorage
//      令牌泄漏进无鉴权端点），Authorization 请求外形零变化；
//   3. 错误映射统一 ApiError 族：原裸 Error 固定中文文案改为后端结构化错误
//      统一透出（登记于迁移对照表）；
//   4. 无超时无重试（沿用现值，HC-01 行为兼容红线）。
// =============================================================================

import type {
  AgentSemanticProfile,
  DiscoveryRequest,
  DiscoveryResult,
  ProfileUpsertRequest,
} from './discoveryTypes';
import { createHttpClient } from '../api/httpClient';

const API_BASE = '/api/v1/governance/agent-discovery';

const client = createHttpClient({
  baseUrl: API_BASE,
  getAccessToken: () => '',
});

/** 新增或更新 Agent 语义档案 */
export async function upsertProfile(
  request: ProfileUpsertRequest,
): Promise<AgentSemanticProfile> {
  return client.request<AgentSemanticProfile>({
    path: '/profiles',
    method: 'POST',
    body: request,
  });
}

/** 查询单个 Agent 语义档案 */
export async function getProfile(agentId: string): Promise<AgentSemanticProfile> {
  return client.request<AgentSemanticProfile>({
    path: `/profiles/${encodeURIComponent(agentId)}`,
  });
}

/** 移除 Agent 语义档案 */
export async function removeProfile(agentId: string): Promise<{ removed: boolean; agent_id: string }> {
  return client.request<{ removed: boolean; agent_id: string }>({
    path: `/profiles/${encodeURIComponent(agentId)}`,
    method: 'DELETE',
  });
}

/** 列出全部 Agent 语义档案 */
export async function listProfiles(): Promise<AgentSemanticProfile[]> {
  return client.request<AgentSemanticProfile[]>({ path: '/profiles' });
}

/** 执行 Agent 发现查询 */
export async function discover(request: DiscoveryRequest): Promise<DiscoveryResult> {
  return client.request<DiscoveryResult>({
    path: '/discover',
    method: 'POST',
    body: request,
  });
}
