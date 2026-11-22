// =============================================================================
// 贡献审核面板（web/src/market/ContributionReviewBoard.tsx）
//
// 治理审核员视图：贡献列表、详情、批准/驳回、重新扫描
// =============================================================================

import { useEffect, useState, useCallback } from 'react';
import type {
  ContributionState,
  NodeContribution,
  ScanReport,
  StateTransitionRecord,
} from './contributionTypes';
import {
  listContributions,
  transitionContribution,
  rescanContribution,
  getHistory,
} from './contributionApiClient';
import { useThemeMode, darkTokens } from '../theme/themeMode';

/** 状态徽章颜色映射 */
const STATE_COLORS: Record<ContributionState, string> = {
  draft: 'gray',
  submitted: 'blue',
  security_scan: 'orange',
  reviewing: 'yellow',
  approved: 'green',
  rejected: 'red',
  published: 'teal',
};

/** 贡献审核面板 */
export function ContributionReviewBoard({ operatorId }: { operatorId: string }): JSX.Element {
  const [contributions, setContributions] = useState<NodeContribution[]>([]);
  const [selected, setSelected] = useState<NodeContribution | null>(null);
  const [history, setHistory] = useState<StateTransitionRecord[]>([]);
  const [rejectionReason, setRejectionReason] = useState('');
  const [error, setError] = useState<string | null>(null);
  const themeMode = useThemeMode();
  const isDark = themeMode === 'dark';

  const loadData = useCallback(async () => {
    try {
      const response = await listContributions();
      setContributions(response.contributions);
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载失败');
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const handleSelect = async (contribution: NodeContribution) => {
    setSelected(contribution);
    setRejectionReason('');
    try {
      const hist = await getHistory(contribution.contribution_id);
      setHistory(hist.history);
    } catch {
      setHistory([]);
    }
  };

  const handleApprove = async () => {
    if (!selected) return;
    try {
      await transitionContribution(selected.contribution_id, 'approved', operatorId, '审核通过');
      loadData();
      handleSelect(selected);
    } catch (err) {
      setError(err instanceof Error ? err.message : '操作失败');
    }
  };

  const handleReject = async () => {
    if (!selected || !rejectionReason) return;
    try {
      await transitionContribution(selected.contribution_id, 'rejected', operatorId, rejectionReason);
      loadData();
      handleSelect(selected);
    } catch (err) {
      setError(err instanceof Error ? err.message : '操作失败');
    }
  };

  const handleRescan = async () => {
    if (!selected) return;
    try {
      await rescanContribution(selected.contribution_id, operatorId);
      loadData();
      handleSelect(selected);
    } catch (err) {
      setError(err instanceof Error ? err.message : '重新扫描失败');
    }
  };

  return (
    <div className="wfp-crb-panel">
      <style>{`
.wfp-crb-input{width:100%;box-sizing:border-box;padding:7px 10px;font-size:13px;font-weight:500;font-family:inherit;color:${isDark?darkTokens.text.primary:"#333333"};background-color:${isDark?darkTokens.surface.bg:"#ffffff"};border:1px solid ${isDark?darkTokens.surface.divider:"#e0e0e0"};border-radius:5px;outline:none;transition:background-color 120ms ease,border-color 120ms ease;}
.wfp-crb-input:hover{background-color:${isDark?darkTokens.surface.bgHover:"#fafafa"}}
.wfp-crb-input:focus{border-color:${isDark?darkTokens.surface.borderStrong:"#bdbdbd"}}
.wfp-crb-input::placeholder{color:${isDark?darkTokens.text.hint:"#b0b0b0"}}
.wfp-crb-table{width:100%;border-collapse:separate;border-spacing:0;font-size:12px;border:1px solid ${isDark?darkTokens.surface.divider:"#e0e0e0"};border-radius:8px;overflow:hidden;}
.wfp-crb-table thead th{background:${isDark?darkTokens.surface.bgHover:"#f5f5f5"};font-weight:600;text-align:left;padding:8px 12px;border-bottom:1px solid ${isDark?darkTokens.surface.divider:"#e0e0e0"};color:${isDark?darkTokens.text.primary:"#1a1a1a"}}
.wfp-crb-table tbody td{padding:7px 12px;border-bottom:1px solid ${isDark?darkTokens.surface.divider:"#eee"};color:${isDark?darkTokens.text.primary:"#1a1a1a"}}
.wfp-crb-table tbody tr:last-child td{border-bottom:none}
.wfp-crb-table tbody tr:hover{background:${isDark?darkTokens.surface.bgHover:"#fafafa"}}
.wfp-crb-btn{font-size:13px;font-weight:500;font-family:inherit;padding:6px 14px;border-radius:6px;cursor:pointer;outline:none;border:1px solid ${isDark?darkTokens.surface.border:"#d0d0d0"};background:${isDark?darkTokens.surface.bg:"#ffffff"};color:${isDark?darkTokens.text.primary:"#1a1a1a"};transition:background-color 120ms ease,border-color 120ms ease;}
.wfp-crb-btn:hover{background:${isDark?darkTokens.surface.bgHover:"#f5f5f5"};border-color:${isDark?darkTokens.surface.borderStrong:"#b8b8b8"}}
.wfp-crb-btn:active{background:${isDark?darkTokens.surface.bgActive:"#e8e8e8"}}
.wfp-crb-btn:disabled{opacity:0.5;cursor:not-allowed}
.wfp-crb-btn--primary{background:${isDark?darkTokens.surface.bgActive:"#1a1a1a"};color:${isDark?darkTokens.text.primary:"#ffffff"};border-color:${isDark?darkTokens.surface.borderStrong:"#1a1a1a"}}
.wfp-crb-btn--primary:hover{background:${isDark?darkTokens.surface.bgHover:"#333333"}}
.wfp-crb-btn--danger{color:${isDark?darkTokens.danger:"#d32f2f"};background:transparent;border-color:${isDark?darkTokens.danger:"#d32f2f"}}
.wfp-crb-btn--danger:hover{background:${isDark?"rgba(255,107,107,0.12)":"#fdecea"}}
.wfp-crb-panel{padding:20px 24px;background:${isDark?darkTokens.surface.bg:"#ffffff"};border:1px solid ${isDark?darkTokens.surface.divider:"#e0e0e0"};border-radius:8px;}
.wfp-crb-title{font-size:15px;font-weight:600;margin:0 0 16px;padding-bottom:12px;border-bottom:1px solid ${isDark?darkTokens.surface.divider:"#e0e0e0"};color:${isDark?darkTokens.text.primary:"#1a1a1a"};}
.wfp-crb-error{margin-bottom:16px;padding:12px 16px;border-radius:8px;border:1px solid ${isDark?darkTokens.surface.divider:"#e0e0e0"};border-left:3px solid ${isDark?darkTokens.danger:"#d32f2f"};background:${isDark?"rgba(255,107,107,0.08)":"#fdecea"};font-size:12px;color:${isDark?darkTokens.danger:"#d32f2f"};}
.wfp-crb-layout{display:flex;gap:16px;}
.wfp-crb-list{flex:1;min-width:0;}
.wfp-crb-detail{flex:1;min-width:0;padding:16px;background:${isDark?darkTokens.surface.bgHover:"#fafafa"};border:1px solid ${isDark?darkTokens.surface.divider:"#e0e0e0"};border-radius:8px;}
.wfp-crb-detail-title{font-size:14px;font-weight:600;margin:0 0 12px;color:${isDark?darkTokens.text.primary:"#1a1a1a"};}
.wfp-crb-detail p{font-size:12px;margin:4px 0;color:${isDark?darkTokens.text.secondary:"#616161"};}
.wfp-crb-badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:500;background:${isDark?darkTokens.surface.bgActive:"#f0f0f0"};}
.wfp-crb-reports,.wfp-crb-history{margin-top:12px;}
.wfp-crb-section-title{font-size:13px;font-weight:600;margin:0 0 8px;color:${isDark?darkTokens.text.primary:"#1a1a1a"};}
.wfp-crb-report{padding:12px;background:${isDark?darkTokens.surface.bg:"#ffffff"};border:1px solid ${isDark?darkTokens.surface.divider:"#e0e0e0"};border-radius:6px;margin-bottom:8px;}
.wfp-crb-report p{font-size:12px;margin:0 0 6px;color:${isDark?darkTokens.text.secondary:"#616161"};}
.wfp-crb-report ul,.wfp-crb-history ul{list-style:none;padding:0;margin:0;}
.wfp-crb-report li,.wfp-crb-history li{font-size:12px;padding:4px 0;border-bottom:1px solid ${isDark?darkTokens.surface.divider:"#f0f0f0"};color:${isDark?darkTokens.text.secondary:"#616161"};}
.wfp-crb-report li:last-child,.wfp-crb-history li:last-child{border-bottom:none;}
.wfp-crb-actions{display:flex;gap:8px;align-items:center;margin-top:12px;}
.wfp-crb-actions .wfp-crb-input{flex:1;}
.wfp-crb-empty{text-align:center;padding:24px 16px;color:${isDark?darkTokens.text.hint:"#9e9e9e"};font-size:13px;}
`}</style>
      <h2 className="wfp-crb-title">贡献审核</h2>
      {error && <div className="wfp-crb-error">{error}</div>}

      <div className="wfp-crb-layout">
        {/* 贡献列表 */}
        <div className="wfp-crb-list">
          <table className="wfp-crb-table" data-testid="contribution-table">
            <thead>
              <tr>
                <th>节点名称</th>
                <th>版本</th>
                <th>状态</th>
                <th>贡献者</th>
              </tr>
            </thead>
            <tbody>
              {contributions.length === 0 ? (
                <tr><td colSpan={4} className="wfp-crb-empty">暂无贡献记录</td></tr>
              ) : contributions.map((c) => (
                <tr key={c.contribution_id} onClick={() => handleSelect(c)}>
                  <td>{c.node_name}</td>
                  <td>{c.version}</td>
                  <td>
                    <span className="wfp-crb-badge" style={{ color: STATE_COLORS[c.state] }}>{c.state}</span>
                  </td>
                  <td>{c.contributor_id}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* 贡献详情 */}
        {selected && (
          <div className="wfp-crb-detail" data-testid="contribution-detail">
            <h3 className="wfp-crb-detail-title">{selected.node_name}@{selected.version}</h3>
            <p>状态: <span className="wfp-crb-badge" style={{ color: STATE_COLORS[selected.state] }}>{selected.state}</span></p>
            <p>签名: {selected.signature ? `${selected.signature.algorithm} ✓` : '缺失'}</p>

            {/* 扫描报告 */}
            {selected.scan_reports.length > 0 && (
              <div className="wfp-crb-reports">
                <h4 className="wfp-crb-section-title">扫描报告</h4>
                {selected.scan_reports.map((report: ScanReport) => (
                  <div key={report.report_id} className="wfp-crb-report">
                    <p>通过: {report.passed ? '是' : '否'}</p>
                    <ul>
                      {report.findings.map((f, idx) => (
                        <li key={idx} className={`wfp-crb-finding--${f.severity}`}>
                          [{f.severity}] {f.message} {f.location && `(${f.location})`}
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            )}

            {/* 流转历史 */}
            {history.length > 0 && (
              <div className="wfp-crb-history">
                <h4 className="wfp-crb-section-title">流转历史</h4>
                <ul>
                  {history.map((h, idx) => (
                    <li key={idx}>
                      {h.occurred_at}: {h.from_state} → {h.to_state}
                      {' '}({h.operator_id}) {h.reason}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/* 操作按钮 */}
            <div className="wfp-crb-actions">
              {selected.state === 'reviewing' && (
                <>
                  <button className="wfp-crb-btn wfp-crb-btn--primary" onClick={handleApprove} data-testid="approve-button">批准</button>
                  <input
                    className="wfp-crb-input"
                    type="text"
                    placeholder="驳回理由"
                    value={rejectionReason}
                    onChange={(e) => setRejectionReason(e.target.value)}
                  />
                  <button
                    className="wfp-crb-btn wfp-crb-btn--danger"
                    onClick={handleReject}
                    disabled={!rejectionReason}
                    data-testid="reject-button"
                  >
                    驳回
                  </button>
                </>
              )}
              {selected.state === 'security_scan' && (
                <button className="wfp-crb-btn" onClick={handleRescan} data-testid="rescan-button">重新扫描</button>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
