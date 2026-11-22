// =============================================================================
// Agent 发现类型定义（web/src/agent_discovery/discoveryTypes.ts）
// =============================================================================

/** 描述来源三态 */
export type DescriptionSource = 'provider' | 'llm_generated' | 'manual_revision';

/** Agent 语义档案 */
export interface AgentSemanticProfile {
  agent_id: string;
  name: string;
  description: string;
  description_source: DescriptionSource;
  tags: string[];
  capabilities: string[];
  required_permissions: string[];
  tenant_id: string;
  metadata: Record<string, string>;
}

/** 调用方上下文 */
export interface CallerContext {
  user_id: string;
  tenant_id: string;
  roles: string[];
  permissions: string[];
}

/** 发现查询请求 */
export interface DiscoveryRequest {
  intent_text: string;
  caller: CallerContext;
  tags_filter: string[];
  limit: number | null;
}

/** 发现结果项 */
export interface DiscoveryResultItem {
  agent_id: string;
  name: string;
  description: string;
  description_source: DescriptionSource;
  tags: string[];
  capabilities: string[];
  relevance_score: number;
  matched_evidence: string[];
}

/** 发现结果汇总 */
export interface DiscoveryResult {
  items: DiscoveryResultItem[];
  total: number;
  query: string;
  backend: 'keyword' | 'hybrid';
}

/** 语义档案新增/更新请求 */
export interface ProfileUpsertRequest {
  agent_id: string;
  name: string;
  description: string;
  description_source: DescriptionSource;
  tags: string[];
  capabilities: string[];
  required_permissions: string[];
  tenant_id: string;
  metadata: Record<string, string>;
}
