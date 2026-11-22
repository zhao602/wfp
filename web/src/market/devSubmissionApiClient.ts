// =============================================================================
// 用户自主开发 API 客户端（web/src/market/devSubmissionApiClient.ts）
//
// 请求管线统一于 httpClient 基元（B1 批次 T3.3）；本模块仅保留端点定义。
// 设计意图：
//   1. 错误映射：非 2xx 经基元统一解析（`detail.error_message` 提取由基元三形态
//      解析承接）抛 ApiError——原裸 Error 固定中文兜底改 HTTP 兜底（登记）；
//   2. multipart 提交（DV-3/DV-6/DV-7）经 `multipart:true` + FormData 原样传递；
//   3. 无鉴权：空 provider 压制基元默认 token 注入（Authorization 外形零变化）；
//   4. 无超时无重试（沿用现值，HC-01 行为兼容红线）；
//   5. 对应端点：DV-1 ~ DV-8（design §2.2.2 / DC-06）。
// =============================================================================

import type {
  DevContributionState,
  DevHistoryResponse,
  DevScaffoldDetail,
  DevScaffoldListResponse,
  DevSubmissionDetailResponse,
  DevSubmissionListResponse,
  DevSubmissionResponse,
  DevTransitionResponse,
  DevValidationReport,
} from './devSubmissionTypes';
import { createHttpClient } from '../api/httpClient';

const DEV_TEMPLATES_BASE = '/api/v1/agent-market/dev-templates';
const DEV_SUBMISSIONS_BASE = '/api/v1/agent-market/dev-submissions';

const devTemplatesClient = createHttpClient({
  baseUrl: DEV_TEMPLATES_BASE,
  getAccessToken: () => '',
});

const devSubmissionsClient = createHttpClient({
  baseUrl: DEV_SUBMISSIONS_BASE,
  getAccessToken: () => '',
});

/** DV-1：列出开发脚手架 */
export async function listDevScaffolds(): Promise<DevScaffoldListResponse> {
  return devTemplatesClient.request<DevScaffoldListResponse>({ path: '' });
}

/** DV-2：开发脚手架详情（五要素 + 文件模板内联 + 获取指引） */
export async function getDevScaffold(scaffoldId: string): Promise<DevScaffoldDetail> {
  return devTemplatesClient.request<DevScaffoldDetail>({
    path: `/${encodeURIComponent(scaffoldId)}`,
  });
}

/** DV-3：提交开发包（multipart：package + developer_id + node_name + version） */
export async function submitDevPackage(
  packageFile: File,
  developerId: string,
  nodeName: string,
  version: string,
): Promise<DevSubmissionResponse> {
  const formData = new FormData();
  formData.append('package', packageFile);
  formData.append('developer_id', developerId);
  formData.append('node_name', nodeName);
  formData.append('version', version);
  return devSubmissionsClient.request<DevSubmissionResponse>({
    path: '',
    method: 'POST',
    body: formData,
    multipart: true,
  });
}

/** DV-4：开发提交清单（state / node_name 筛选） */
export async function listDevSubmissions(
  state?: DevContributionState,
  nodeName?: string,
): Promise<DevSubmissionListResponse> {
  const params = new URLSearchParams();
  if (state) params.set('state', state);
  if (nodeName) params.set('node_name', nodeName);
  const query = params.toString();
  return devSubmissionsClient.request<DevSubmissionListResponse>({
    path: `${query ? `?${query}` : ''}`,
  });
}

/** DV-5：开发提交详情（主体 + 最新报告 + 来源标注） */
export async function getDevSubmission(
  submissionId: string,
): Promise<DevSubmissionDetailResponse> {
  return devSubmissionsClient.request<DevSubmissionDetailResponse>({
    path: `/${encodeURIComponent(submissionId)}`,
  });
}

/** DV-6：审核流转（to_state / operator_id / reason） */
export async function transitionDevSubmission(
  submissionId: string,
  toState: DevContributionState,
  operatorId: string,
  reason: string = '',
): Promise<DevTransitionResponse> {
  const formData = new FormData();
  formData.append('to_state', toState);
  formData.append('operator_id', operatorId);
  formData.append('reason', reason);
  return devSubmissionsClient.request<DevTransitionResponse>({
    path: `/${encodeURIComponent(submissionId)}/transition`,
    method: 'POST',
    body: formData,
    multipart: true,
  });
}

/** DV-7：重新执行三层校验（报告追加留档，不覆盖历史） */
export async function rescanDevSubmission(
  submissionId: string,
  operatorId: string,
): Promise<DevValidationReport> {
  const formData = new FormData();
  formData.append('operator_id', operatorId);
  return devSubmissionsClient.request<DevValidationReport>({
    path: `/${encodeURIComponent(submissionId)}/rescan`,
    method: 'POST',
    body: formData,
    multipart: true,
  });
}

/** DV-8：流转历史（按时间有序） */
export async function getHistory(submissionId: string): Promise<DevHistoryResponse> {
  return devSubmissionsClient.request<DevHistoryResponse>({
    path: `/${encodeURIComponent(submissionId)}/history`,
  });
}
