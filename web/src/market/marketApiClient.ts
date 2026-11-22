// =============================================================================
// Agent 市场与 Tool 市场 API 客户端（web/src/market/marketApiClient.ts）
//
// 请求管线统一于 httpClient 基元（B1 批次 T3.3）；本模块仅保留端点定义。
// 设计意图：
//   1. 错误映射：非 2xx 经基元统一解析（{code,message,details} / FastAPI {detail}
//      字符串 / {detail:{error_code,error_message}} 三形态）抛 ApiError——原裸
//      Error 固定中文文案改结构化透出（登记于迁移对照表）；
//   2. 无鉴权：空 provider 压制基元默认 token 注入（Authorization 外形零变化）；
//   3. 无超时无重试（沿用现值，HC-01 行为兼容红线）。
// =============================================================================

import type {
  AgentMarketEntry,
  AgentTemplate,
  MarketEntryStatus,
  ToolMarketEntry,
} from './marketTypes';
import { createHttpClient } from '../api/httpClient';

const AGENT_MARKET_BASE = '/api/v1/agent-market';
const TOOL_MARKET_BASE = '/api/v1/tool-market';

const agentMarketClient = createHttpClient({
  baseUrl: AGENT_MARKET_BASE,
  getAccessToken: () => '',
});

const toolMarketClient = createHttpClient({
  baseUrl: TOOL_MARKET_BASE,
  getAccessToken: () => '',
});

/** 查询 Agent 市场条目清单（E-1） */
export async function listAgentMarketEntries(params?: {
  q?: string;
  category?: string;
  tag?: string;
  status?: MarketEntryStatus;
}): Promise<{ entries: AgentMarketEntry[]; total: number }> {
  const search = new URLSearchParams();
  if (params?.q) {
    search.set('q', params.q);
  }
  if (params?.category) {
    search.set('category', params.category);
  }
  if (params?.tag) {
    search.set('tag', params.tag);
  }
  if (params?.status) {
    search.set('status', params.status);
  }
  const query = search.toString();
  return agentMarketClient.request<{ entries: AgentMarketEntry[]; total: number }>({
    path: `/entries${query ? `?${query}` : ''}`,
  });
}

/** 查询 Agent 市场条目详情（E-2） */
export async function getAgentMarketEntry(entryId: string): Promise<AgentMarketEntry> {
  return agentMarketClient.request<AgentMarketEntry>({
    path: `/entries/${encodeURIComponent(entryId)}`,
  });
}

/** 查询 Agent 市场分类计数（E-3） */
export async function listAgentMarketCategories(): Promise<{
  categories: Array<{ category: string; count: number }>;
  total: number;
}> {
  return agentMarketClient.request<{
    categories: Array<{ category: string; count: number }>;
    total: number;
  }>({ path: '/categories' });
}

/** 注册 Agent 市场条目（E-4，同标识覆盖语义由后端裁决） */
export async function registerAgentMarketEntry(payload: unknown): Promise<AgentMarketEntry> {
  return agentMarketClient.request<AgentMarketEntry>({
    path: '/entries',
    method: 'POST',
    body: payload,
  });
}

/** 查询内置模板清单（E-5，total 恒 5） */
export async function listAgentTemplates(): Promise<{
  templates: Array<
    Pick<AgentTemplate, 'template_id' | 'name' | 'kind' | 'max_tool_rounds' | 'allowed_tools'>
  >;
  total: number;
}> {
  return agentMarketClient.request<{
    templates: Array<
      Pick<AgentTemplate, 'template_id' | 'name' | 'kind' | 'max_tool_rounds' | 'allowed_tools'>
    >;
    total: number;
  }>({ path: '/templates' });
}

/** 查询内置模板详情（E-6，含 preset / doc / recipe） */
export async function getAgentTemplate(templateId: string): Promise<AgentTemplate> {
  return agentMarketClient.request<AgentTemplate>({
    path: `/templates/${encodeURIComponent(templateId)}`,
  });
}

/** 查询 Tool 市场工具清单（E-7，无筛选时与 /api/v1/tools 标识集合一致） */
export async function listToolMarketTools(params?: {
  q?: string;
  category?: string;
  permission?: string;
}): Promise<{ tools: ToolMarketEntry[]; total: number }> {
  const search = new URLSearchParams();
  if (params?.q) {
    search.set('q', params.q);
  }
  if (params?.category) {
    search.set('category', params.category);
  }
  if (params?.permission) {
    search.set('permission', params.permission);
  }
  const query = search.toString();
  return toolMarketClient.request<{ tools: ToolMarketEntry[]; total: number }>({
    path: `/tools${query ? `?${query}` : ''}`,
  });
}

/** 查询 Tool 市场工具详情（E-8） */
export async function getToolMarketTool(toolIdentifier: string): Promise<ToolMarketEntry> {
  return toolMarketClient.request<ToolMarketEntry>({
    path: `/tools/${encodeURIComponent(toolIdentifier)}`,
  });
}

/** 查询 Tool 市场分类计数（E-9） */
export async function listToolMarketCategories(): Promise<{
  categories: Array<{ category: string; count: number }>;
  total: number;
}> {
  return toolMarketClient.request<{
    categories: Array<{ category: string; count: number }>;
    total: number;
  }>({ path: '/categories' });
}

/** 注册 Tool 市场工具（E-10，复用后端既有注册语义） */
export async function registerToolMarketTool(payload: unknown): Promise<{
  success: boolean;
  tool_identifier: string;
  version: string;
  conflict_reason: string | null;
}> {
  return toolMarketClient.request<{
    success: boolean;
    tool_identifier: string;
    version: string;
    conflict_reason: string | null;
  }>({
    path: '/tools',
    method: 'POST',
    body: payload,
  });
}
