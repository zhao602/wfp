// =============================================================================
// 墓碑管理面板（TombstonePanel.tsx）
// =============================================================================

import { useState, useEffect, useCallback } from 'react';
import type { ForgetResult, TombstoneMetadataView } from './tombstoneTypes';
import { forgetUser, listTombstones } from './tombstoneApiClient';
import { useThemeMode, darkTokens } from '../theme/themeMode';

interface TombstonePanelProps {
  token: string;
  tenantId: string;
}

export function TombstonePanel({ token, tenantId }: TombstonePanelProps): JSX.Element {
  const [userId, setUserId] = useState<string>('');
  const [operatorId, setOperatorId] = useState<string>('');
  const [forgetResult, setForgetResult] = useState<ForgetResult | null>(null);
  const [tombstones, setTombstones] = useState<TombstoneMetadataView[]>([]);
  const [loading, setLoading] = useState<boolean>(false);
  const themeMode = useThemeMode();
  const isDark = themeMode === 'dark';

  const refreshTombstones = useCallback(async (): Promise<void> => {
    setLoading(true);
    try {
      const result = await listTombstones(tenantId, token);
      setTombstones(result);
    } catch {
      setTombstones([]);
    } finally {
      setLoading(false);
    }
  }, [tenantId, token]);

  useEffect((): void => { refreshTombstones(); }, [refreshTombstones]);

  const handleForget = useCallback(async (): Promise<void> => {
    setForgetResult(null);
    try {
      const result = await forgetUser(
        { user_id: userId, operator_id: operatorId, tenant_id: tenantId },
        token,
      );
      setForgetResult(result);
      await refreshTombstones();
    } catch {
      // 错误处理
    }
  }, [userId, operatorId, tenantId, token, refreshTombstones]);

  return (
    <div className="wfp-tbs-panel">
      <style>{`
.wfp-tbs-input{width:100%;box-sizing:border-box;padding:7px 10px;font-size:13px;font-weight:500;font-family:inherit;color:${isDark?darkTokens.text.primary:"#333333"};background-color:${isDark?darkTokens.surface.bg:"#ffffff"};border:1px solid ${isDark?darkTokens.surface.divider:"#e0e0e0"};border-radius:5px;outline:none;transition:background-color 120ms ease,border-color 120ms ease;}
.wfp-tbs-input:hover{background-color:${isDark?darkTokens.surface.bgHover:"#fafafa"}}
.wfp-tbs-input:focus{border-color:${isDark?darkTokens.surface.borderStrong:"#bdbdbd"}}
.wfp-tbs-input::placeholder{color:${isDark?darkTokens.text.hint:"#b0b0b0"}}
.wfp-tbs-table{width:100%;border-collapse:separate;border-spacing:0;font-size:12px;border:1px solid ${isDark?darkTokens.surface.divider:"#e0e0e0"};border-radius:8px;overflow:hidden;}
.wfp-tbs-table thead th{background:${isDark?darkTokens.surface.bgHover:"#f5f5f5"};font-weight:600;text-align:left;padding:8px 12px;border-bottom:1px solid ${isDark?darkTokens.surface.divider:"#e0e0e0"};color:${isDark?darkTokens.text.primary:"#1a1a1a"}}
.wfp-tbs-table tbody td{padding:7px 12px;border-bottom:1px solid ${isDark?darkTokens.surface.divider:"#eee"};color:${isDark?darkTokens.text.primary:"#1a1a1a"}}
.wfp-tbs-table tbody tr:last-child td{border-bottom:none}
.wfp-tbs-table tbody tr:hover{background:${isDark?darkTokens.surface.bgHover:"#fafafa"}}
.wfp-tbs-btn{font-size:13px;font-weight:500;font-family:inherit;padding:6px 14px;border-radius:6px;cursor:pointer;outline:none;border:1px solid ${isDark?darkTokens.surface.border:"#d0d0d0"};background:${isDark?darkTokens.surface.bg:"#ffffff"};color:${isDark?darkTokens.text.primary:"#1a1a1a"};transition:background-color 120ms ease,border-color 120ms ease;}
.wfp-tbs-btn:hover{background:${isDark?darkTokens.surface.bgHover:"#f5f5f5"};border-color:${isDark?darkTokens.surface.borderStrong:"#b8b8b8"}}
.wfp-tbs-btn:active{background:${isDark?darkTokens.surface.bgActive:"#e8e8e8"}}
.wfp-tbs-btn:disabled{opacity:0.5;cursor:not-allowed}
.wfp-tbs-btn--primary{background:${isDark?darkTokens.surface.bgActive:"#1a1a1a"};color:${isDark?darkTokens.text.primary:"#ffffff"};border-color:${isDark?darkTokens.surface.borderStrong:"#1a1a1a"}}
.wfp-tbs-btn--primary:hover{background:${isDark?darkTokens.surface.bgHover:"#333333"}}
.wfp-tbs-btn--primary:disabled:hover{background:${isDark?darkTokens.surface.bgActive:"#1a1a1a"};border-color:${isDark?darkTokens.surface.borderStrong:"#1a1a1a"}}

.wfp-tbs-panel{padding:20px 24px;background:${isDark?darkTokens.surface.bg:"#ffffff"};border:1px solid ${isDark?darkTokens.surface.divider:"#e0e0e0"};border-radius:8px;}
.wfp-tbs-title{font-size:15px;font-weight:600;margin:0 0 16px;padding-bottom:12px;border-bottom:1px solid ${isDark?darkTokens.surface.divider:"#e0e0e0"};color:${isDark?darkTokens.text.primary:"#1a1a1a"};}
.wfp-tbs-form{margin-bottom:20px;padding:16px;background:${isDark?darkTokens.surface.bgHover:"#fafafa"};border:1px solid ${isDark?darkTokens.surface.divider:"#e0e0e0"};border-radius:8px;}
.wfp-tbs-section-title{font-size:14px;font-weight:600;margin:0 0 12px;color:${isDark?darkTokens.text.primary:"#1a1a1a"};}
.wfp-tbs-form .wfp-tbs-input{margin-bottom:10px;}
.wfp-tbs-result{margin-bottom:20px;padding:14px 16px;border-radius:8px;background:${isDark?darkTokens.surface.bgHover:"#fafafa"};border:1px solid ${isDark?darkTokens.surface.divider:"#e0e0e0"};border-left:3px solid ${isDark?"#66bb6a":"#4caf50"};}
.wfp-tbs-result p{font-size:12px;margin:4px 0;color:${isDark?darkTokens.text.secondary:"#616161"};}
.wfp-tbs-list{margin-top:4px;}
.wfp-tbs-empty{text-align:center;padding:24px 16px;color:${isDark?darkTokens.text.hint:"#9e9e9e"};font-size:13px;}
`}</style>
      <h2 className="wfp-tbs-title">墓碑与被遗忘权</h2>

      <div className="wfp-tbs-form">
        <h3 className="wfp-tbs-section-title">执行被遗忘权</h3>
        <input className="wfp-tbs-input" placeholder="用户标识" value={userId} onChange={(e): void => setUserId(e.target.value)} />
        <input className="wfp-tbs-input" placeholder="操作人标识" value={operatorId} onChange={(e): void => setOperatorId(e.target.value)} />
        <button className="wfp-tbs-btn wfp-tbs-btn--primary" onClick={handleForget} disabled={!userId || !operatorId}>执行被遗忘权</button>
      </div>

      {forgetResult !== null && (
        <div className="wfp-tbs-result">
          <p>总条目: {forgetResult.total_count}</p>
          <p>成功标记: {forgetResult.success_count}</p>
          <p>失败: {forgetResult.failed_count}</p>
          <p>操作日志ID: {forgetResult.operation_log_id}</p>
        </div>
      )}

      <div className="wfp-tbs-list">
        <h3 className="wfp-tbs-section-title">墓碑记录 {loading ? '（加载中...）' : ''}</h3>
        <table className="wfp-tbs-table">
          <thead>
            <tr><th>条目ID</th><th>删除时间</th><th>操作人</th><th>操作批次</th></tr>
          </thead>
          <tbody>
            {tombstones.length === 0 ? (
              <tr><td colSpan={4} className="wfp-tbs-empty">暂无墓碑记录</td></tr>
            ) : tombstones.map((t) => (
              <tr key={t.entry_id}>
                <td>{t.entry_id}</td>
                <td>{t.deleted_at ?? '-'}</td>
                <td>{t.deleted_by ?? '-'}</td>
                <td>{t.forget_operation_id ?? '-'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
