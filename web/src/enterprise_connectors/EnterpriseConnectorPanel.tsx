// =============================================================================
// 企业连接器面板（web/src/enterprise_connectors/EnterpriseConnectorPanel.tsx）
// =============================================================================

import { useEffect, useState, useCallback } from 'react';
import type { ConnectionTestResult } from './enterpriseConnectorTypes';
import { listConnectors } from './enterpriseConnectorApiClient';
import { useThemeMode, darkTokens } from '../theme/themeMode';

export function EnterpriseConnectorPanel({ tenantId }: { tenantId: string }): JSX.Element {
  const [, setConnectors] = useState<unknown[]>([]);
  const [testResult] = useState<ConnectionTestResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const themeMode = useThemeMode();
  const isDark = themeMode === 'dark';

  const loadData = useCallback(async () => {
    try {
      setConnectors(await listConnectors(tenantId));
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载失败');
    }
  }, [tenantId]);

  useEffect(() => { loadData(); }, [loadData]);

  return (
    <div className="wfp-ecp-panel">
      <style>{`
.wfp-ecp-panel{padding:20px 24px;background:${isDark?darkTokens.surface.bg:"#ffffff"};border:1px solid ${isDark?darkTokens.surface.divider:"#e0e0e0"};border-radius:8px;}
.wfp-ecp-title{font-size:15px;font-weight:600;margin:0 0 16px;padding-bottom:12px;border-bottom:1px solid ${isDark?darkTokens.surface.divider:"#e0e0e0"};color:${isDark?darkTokens.text.primary:"#1a1a1a"};}
.wfp-ecp-error{margin-bottom:16px;padding:12px 16px;border-radius:8px;border:1px solid ${isDark?darkTokens.surface.divider:"#e0e0e0"};border-left:3px solid ${isDark?darkTokens.danger:"#d32f2f"};background:${isDark?"rgba(255,107,107,0.08)":"#fdecea"};font-size:12px;color:${isDark?darkTokens.danger:"#d32f2f"};}
.wfp-ecp-result{margin-bottom:16px;padding:14px 16px;border-radius:8px;background:${isDark?darkTokens.surface.bgHover:"#fafafa"};border:1px solid ${isDark?darkTokens.surface.divider:"#e0e0e0"};border-left:3px solid ${isDark?"#66bb6a":"#4caf50"};}
.wfp-ecp-result p{font-size:12px;margin:4px 0;color:${isDark?darkTokens.text.secondary:"#616161"};}
`}</style>
      <h2 className="wfp-ecp-title">企业连接器</h2>
      {error && <div className="wfp-ecp-error">{error}</div>}
      {testResult && (
        <div className="wfp-ecp-result" data-testid="test-result">
          <p>连通: {testResult.reachable ? '是' : '否'}</p>
          <p>延迟: {testResult.response_latency_ms}ms</p>
          <p>诊断: {testResult.diagnosis}</p>
        </div>
      )}
    </div>
  );
}
