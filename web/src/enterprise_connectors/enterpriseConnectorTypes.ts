// =============================================================================
// 企业连接器类型定义（web/src/enterprise_connectors/enterpriseConnectorTypes.ts）
// =============================================================================

export type ConnectorType = 'crm' | 'erp' | 'oa';

export interface EnterpriseConnectorConfig {
  connector_id: string;
  tenant_id: string;
  connector_type: ConnectorType;
  target_endpoint: string;
  timeout_seconds: number;
  readable_resources: string[];
  writable_resources: string[];
  enabled: boolean;
}

export interface ConnectionTestResult {
  reachable: boolean;
  response_latency_ms: number;
  diagnosis: string;
  tested_at: string;
}

export interface CredentialVersionInfo {
  version: number;
  created_by: string;
  created_at: string;
}
