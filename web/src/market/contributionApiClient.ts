// =============================================================================
// 贡献市场 API 客户端（web/src/market/contributionApiClient.ts）
//
// 请求管线统一于 httpClient 基元（B1 批次 T3.3）；本模块仅保留端点定义。
// 设计意图：
//   1. 错误映射：非 2xx 经基元统一解析（`detail.error_message` 提取由基元三形态
//      解析承接）抛 ApiError——原裸 Error 固定中文兜底改 HTTP 兜底（登记）；
//   2. multipart 提交（submit/transition/rescan）经 `multipart:true` + FormData
//      原样传递（不注入 Content-Type，boundary 由运行时生成）；
//   3. 无鉴权：空 provider 压制基元默认 token 注入（Authorization 外形零变化）；
//   4. 无超时无重试（沿用现值，HC-01 行为兼容红线）。
// =============================================================================

import type {
  ContributionListResponse,
  ContributionState,
  HistoryResponse,
  ScanReport,
  SubmitResponse,
  TransitionResponse,
} from './contributionTypes';
import { createHttpClient } from '../api/httpClient';

const API_BASE = '/api/v1/market/contributions';

const client = createHttpClient({
  baseUrl: API_BASE,
  getAccessToken: () => '',
});

/** 列出贡献 */
export async function listContributions(
  state?: ContributionState,
  nodeName?: string,
): Promise<ContributionListResponse> {
  const params = new URLSearchParams();
  if (state) params.set('state', state);
  if (nodeName) params.set('node_name', nodeName);
  const query = params.toString();
  return client.request<ContributionListResponse>({
    path: `${query ? `?${query}` : ''}`,
  });
}

/** 提交贡献包 */
export async function submitContribution(
  packageFile: File,
  nodeName: string,
  version: string,
  contributorId: string,
  signatureValue: string,
  signatureAlgorithm: string = 'hmac-sha256',
): Promise<SubmitResponse> {
  const formData = new FormData();
  formData.append('package', packageFile);
  formData.append('node_name', nodeName);
  formData.append('version', version);
  formData.append('contributor_id', contributorId);
  formData.append('signature_value', signatureValue);
  formData.append('signature_algorithm', signatureAlgorithm);
  return client.request<SubmitResponse>({
    path: '',
    method: 'POST',
    body: formData,
    multipart: true,
  });
}

/** 状态流转 */
export async function transitionContribution(
  contributionId: string,
  toState: ContributionState,
  operatorId: string,
  reason: string = '',
): Promise<TransitionResponse> {
  const formData = new FormData();
  formData.append('to_state', toState);
  formData.append('operator_id', operatorId);
  formData.append('reason', reason);
  return client.request<TransitionResponse>({
    path: `/${encodeURIComponent(contributionId)}/transition`,
    method: 'POST',
    body: formData,
    multipart: true,
  });
}

/** 重新扫描 */
export async function rescanContribution(
  contributionId: string,
  operatorId: string,
): Promise<ScanReport> {
  const formData = new FormData();
  formData.append('operator_id', operatorId);
  return client.request<ScanReport>({
    path: `/${encodeURIComponent(contributionId)}/rescan`,
    method: 'POST',
    body: formData,
    multipart: true,
  });
}

/** 获取流转历史 */
export async function getHistory(contributionId: string): Promise<HistoryResponse> {
  return client.request<HistoryResponse>({
    path: `/${encodeURIComponent(contributionId)}/history`,
  });
}
