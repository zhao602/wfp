// =============================================================================
// 企业连接器 API 客户端（web/src/enterprise_connectors/enterpriseConnectorApiClient.ts）
//
// 请求管线统一于 httpClient 基元（B1 批次 T3.3）；本模块仅保留端点定义。
// 设计意图：
//   1. 错误映射：非 2xx 经基元统一解析抛 ApiError——原裸 Error 固定中文兜底
//      改 HTTP 兜底（登记）；
//   2. listConnectors 补返回类型 `ConnectorResponse[]`（原无注解推断 any；
//      与后端 enterprise_connector_router.py GET /connectors/enterprise
//      response_model=List[ConnectorResponse] 对齐，调用方
//      EnterpriseConnectorPanel setConnectors(await ...) 类型闭环）；
//   3. 无鉴权：空 provider 压制基元默认 token 注入（Authorization 外形零变化）；
//   4. 无超时无重试（沿用现值，HC-01 行为兼容红线）。
// =============================================================================

import type { ConnectorResponse } from '../api/apiTypes';
import type { ConnectionTestResult, CredentialVersionInfo } from './enterpriseConnectorTypes';
import { createHttpClient } from '../api/httpClient';

const API_BASE = '/api/v1/connectors/enterprise';

const client = createHttpClient({
  baseUrl: API_BASE,
  getAccessToken: () => '',
});

export async function listConnectors(tenantId: string): Promise<ConnectorResponse[]> {
  return client.request<ConnectorResponse[]>({
    path: `?tenant_id=${encodeURIComponent(tenantId)}`,
  });
}

export async function testConnection(connectorId: string): Promise<ConnectionTestResult> {
  return client.request<ConnectionTestResult>({
    path: `/${encodeURIComponent(connectorId)}/test`,
    method: 'POST',
  });
}

export async function updateCredential(
  connectorId: string,
  plaintext: string,
  createdBy: string,
): Promise<CredentialVersionInfo[]> {
  return client.request<CredentialVersionInfo[]>({
    path: `/${encodeURIComponent(connectorId)}/credentials`,
    method: 'PUT',
    body: { credential_plaintext: plaintext, created_by: createdBy },
  });
}
