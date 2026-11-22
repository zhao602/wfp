// =============================================================================
// SSO 设置面板（web/src/sso/SsoSettingsPanel.tsx）
// =============================================================================

import { useEffect, useState, useCallback } from 'react';
import type { IdpConfiguration } from './ssoTypes';
import { listIdps } from './ssoApiClient';
import { useThemeMode, darkTokens } from '../theme/themeMode';
import { isSsoEnabled } from '../config/ssoConfig';

export function SsoSettingsPanel({ tenantId }: { tenantId: string }): JSX.Element {
  const [idps, setIdps] = useState<IdpConfiguration[]>([]);
  const [showForm, setShowForm] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const themeMode = useThemeMode();
  const isDark = themeMode === 'dark';
  const ssoEnabled = isSsoEnabled();

  const loadIdps = useCallback(async () => {
    try {
      setIdps(await listIdps(tenantId));
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载失败');
    }
  }, [tenantId]);

  useEffect(() => { if (ssoEnabled) loadIdps(); }, [loadIdps, ssoEnabled]);

  if (!ssoEnabled) {
    return (
      <div className="wfp-sso-panel">
        <style>{`
.wfp-sso-panel{padding:20px 24px;background:${isDark?darkTokens.surface.bg:"#ffffff"};border:1px solid ${isDark?darkTokens.surface.divider:"#e0e0e0"};border-radius:8px;}
.wfp-sso-title{font-size:15px;font-weight:600;margin:0 0 16px;padding-bottom:12px;border-bottom:1px solid ${isDark?darkTokens.surface.divider:"#e0e0e0"};color:${isDark?darkTokens.text.primary:"#1a1a1a"};}
.wfp-sso-disabled{text-align:center;padding:48px 16px;font-size:14px;color:${isDark?darkTokens.text.hint:"#9e9e9e"};}
`}</style>
        <h2 className="wfp-sso-title">SSO 企业身份集成</h2>
        <div className="wfp-sso-disabled">SSO 企业身份集成未启用</div>
      </div>
    );
  }

  return (
    <div className="wfp-sso-panel">
      <style>{`
.wfp-sso-table{width:100%;border-collapse:separate;border-spacing:0;font-size:12px;border:1px solid ${isDark?darkTokens.surface.divider:"#e0e0e0"};border-radius:8px;overflow:hidden;}
.wfp-sso-table thead th{background:${isDark?darkTokens.surface.bgHover:"#f5f5f5"};font-weight:600;text-align:left;padding:8px 12px;border-bottom:1px solid ${isDark?darkTokens.surface.divider:"#e0e0e0"};color:${isDark?darkTokens.text.primary:"#1a1a1a"}}
.wfp-sso-table tbody td{padding:7px 12px;border-bottom:1px solid ${isDark?darkTokens.surface.divider:"#eee"};color:${isDark?darkTokens.text.primary:"#1a1a1a"}}
.wfp-sso-table tbody tr:last-child td{border-bottom:none}
.wfp-sso-table tbody tr:hover{background:${isDark?darkTokens.surface.bgHover:"#fafafa"}}
.wfp-sso-btn{font-size:13px;font-weight:500;font-family:inherit;padding:6px 14px;border-radius:6px;cursor:pointer;outline:none;border:1px solid ${isDark?darkTokens.surface.border:"#d0d0d0"};background:${isDark?darkTokens.surface.bg:"#ffffff"};color:${isDark?darkTokens.text.primary:"#1a1a1a"};transition:background-color 120ms ease,border-color 120ms ease;}
.wfp-sso-btn:hover{background:${isDark?darkTokens.surface.bgHover:"#f5f5f5"};border-color:${isDark?darkTokens.surface.borderStrong:"#b8b8b8"}}
.wfp-sso-btn:active{background:${isDark?darkTokens.surface.bgActive:"#e8e8e8"}}
.wfp-sso-btn:disabled{opacity:0.5;cursor:not-allowed}
.wfp-sso-btn--primary{background:${isDark?darkTokens.surface.bgActive:"#1a1a1a"};color:${isDark?darkTokens.text.primary:"#ffffff"};border-color:${isDark?darkTokens.surface.borderStrong:"#1a1a1a"}}
.wfp-sso-btn--primary:hover{background:${isDark?darkTokens.surface.bgHover:"#333333"}}
.wfp-sso-panel{padding:20px 24px;background:${isDark?darkTokens.surface.bg:"#ffffff"};border:1px solid ${isDark?darkTokens.surface.divider:"#e0e0e0"};border-radius:8px;}
.wfp-sso-title{font-size:15px;font-weight:600;margin:0 0 16px;padding-bottom:12px;border-bottom:1px solid ${isDark?darkTokens.surface.divider:"#e0e0e0"};color:${isDark?darkTokens.text.primary:"#1a1a1a"};}
.wfp-sso-error{margin-bottom:16px;padding:12px 16px;border-radius:8px;border:1px solid ${isDark?darkTokens.surface.divider:"#e0e0e0"};border-left:3px solid ${isDark?darkTokens.danger:"#d32f2f"};background:${isDark?"rgba(255,107,107,0.08)":"#fdecea"};font-size:12px;color:${isDark?darkTokens.danger:"#d32f2f"};}
.wfp-sso-panel>.wfp-sso-btn{margin-bottom:16px;}
.wfp-sso-empty{text-align:center;padding:24px 16px;color:${isDark?darkTokens.text.hint:"#9e9e9e"};font-size:13px;}
.wfp-sso-disabled{text-align:center;padding:48px 16px;font-size:14px;color:${isDark?darkTokens.text.hint:"#9e9e9e"};}
`}</style>
      <h2 className="wfp-sso-title">SSO 企业身份集成</h2>
      {error && <div className="wfp-sso-error">{error}</div>}
      <button className="wfp-sso-btn wfp-sso-btn--primary" onClick={() => setShowForm(!showForm)}>配置 IdP</button>
      <table className="wfp-sso-table" data-testid="idp-table">
        <thead>
          <tr><th>IdP 标识</th><th>协议</th><th>端点</th><th>凭据版本</th><th>启用</th></tr>
        </thead>
        <tbody>
          {idps.length === 0 ? (
            <tr><td colSpan={5} className="wfp-sso-empty">暂无 IdP 配置</td></tr>
          ) : idps.map((idp) => (
            <tr key={idp.idp_id}>
              <td>{idp.idp_id}</td>
              <td>{idp.protocol_type}</td>
              <td>{idp.auth_endpoint}</td>
              <td>v{idp.credential_version}</td>
              <td>{idp.enabled ? '是' : '否'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
