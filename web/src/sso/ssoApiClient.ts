// =============================================================================
// SSO API 客户端（web/src/sso/ssoApiClient.ts）
// =============================================================================

import { createHttpClient } from '../api/httpClient';
import type { AuthRedirect, IdpConfiguration, SsoLoginResult } from './ssoTypes';

// SSO 端点原无鉴权：空 provider 压制基元默认 token 注入（Authorization 外形零变化）
const client = createHttpClient({
  baseUrl: '/api/v1/auth/sso',
  getAccessToken: () => '',
});

export async function listIdps(tenantId: string): Promise<IdpConfiguration[]> {
  return client.request<IdpConfiguration[]>({
    path: `/idps?tenant_id=${encodeURIComponent(tenantId)}`,
  });
}

export async function createIdp(request: Omit<IdpConfiguration, 'encrypted_credential' | 'credential_version'> & {
  credential_plaintext: string;
}): Promise<IdpConfiguration> {
  return client.request<IdpConfiguration>({
    path: '/idps',
    method: 'POST',
    body: request,
  });
}

export async function initiateSso(idpId: string): Promise<AuthRedirect> {
  return client.request<AuthRedirect>({
    path: `/${encodeURIComponent(idpId)}/initiate`,
  });
}

export async function ssoCallback(idpId: string, callbackPayload: Record<string, unknown>): Promise<SsoLoginResult> {
  return client.request<SsoLoginResult>({
    path: `/${encodeURIComponent(idpId)}/callback`,
    method: 'POST',
    body: { callback_payload: callbackPayload },
  });
}
