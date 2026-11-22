// =============================================================================
// 插件节点 API 客户端（web/src/plugin_nodes/pluginNodeApiClient.ts）
//
// 请求管线统一于 httpClient 基元（B1 批次 T3.3）；本模块仅保留端点定义。
// 设计意图：
//   1. 错误映射：非 2xx 经基元统一解析（`detail.error_message` 提取由基元三形态
//      解析承接）抛 ApiError——原裸 Error 固定中文兜底改 HTTP 兜底（登记）；
//   2. multipart 上传（installPluginNode）经 `multipart:true` + FormData 原样传递；
//   3. 无鉴权：空 provider 压制基元默认 token 注入（Authorization 外形零变化）；
//   4. 无超时无重试（沿用现值，HC-01 行为兼容红线）。
// =============================================================================

import type {
  InstallResponse,
  PluginNodeListResponse,
  UninstallResponse,
} from './pluginNodeTypes';
import { createHttpClient } from '../api/httpClient';

const API_BASE = '/api/v1/plugin';

const client = createHttpClient({
  baseUrl: API_BASE,
  getAccessToken: () => '',
});

/** 列出已安装插件节点 */
export async function listPluginNodes(): Promise<PluginNodeListResponse> {
  return client.request<PluginNodeListResponse>({ path: '/nodes' });
}

/** 安装插件节点包（multipart 上传） */
export async function installPluginNode(packageFile: File): Promise<InstallResponse> {
  const formData = new FormData();
  formData.append('package', packageFile);
  return client.request<InstallResponse>({
    path: '/nodes/install',
    method: 'POST',
    body: formData,
    multipart: true,
  });
}

/** 卸载插件节点 */
export async function uninstallPluginNode(nodeName: string): Promise<UninstallResponse> {
  return client.request<UninstallResponse>({
    path: `/nodes/${encodeURIComponent(nodeName)}`,
    method: 'DELETE',
  });
}
