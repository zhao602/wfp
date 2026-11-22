// =============================================================================
// SSO 类型定义（web/src/sso/ssoTypes.ts）
// =============================================================================

export type SsoProtocolType = 'saml2' | 'oidc';

export interface IdpConfiguration {
  idp_id: string;
  tenant_id: string;
  protocol_type: SsoProtocolType;
  auth_endpoint: string;
  token_endpoint: string;
  audience: string;
  encrypted_credential: string;
  credential_version: number;
  attribute_mappings: Record<string, string>;
  role_mappings: Record<string, string>;
  enabled: boolean;
}

export interface SsoLoginResult {
  success: boolean;
  token: string;
  expires_at: string;
  error_code: string | null;
  error_message: string | null;
  user_id: string | null;
  is_new_user: boolean;
}

export interface AuthRedirect {
  redirect_url: string;
  state: string;
  is_available: boolean;
  degraded_message: string;
}
