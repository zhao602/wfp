// =============================================================================
// 开发包审核台（web/src/market/DevReviewBoard.tsx）
//
// 审核者视图（FR-27 规则 3：与提交向导自成分离）：清单 + 详情 + 三层报告
// （按 layer 分组）+ 流转历史 + 动作（批准 / 驳回含原因 / 重新校验 /
// 触发上架）+ 状态徽章（DC-06）。
// 状态徽章复用同一色板语义（draft=gray…published=teal，与
// ContributionReviewBoard.STATE_COLORS 一致）；该文件属零改动登记
// （design §4.2.5，逐字节不变，不可加 export），故此处按同值镜像复用
// 同一色板，不引入另一套语义；wfp-crb-* 内联样式 + useThemeMode/
// darkTokens 双轨配色 + data-testid 钩子对齐既有范式；
// 接口失败时页面可渲染 + 错误提示 + 既有配置不清空（AC-DEV-22）；
// 面板异常由既有 AdminErrorBoundary 兜底（AC-DEV-23）。
// =============================================================================

import { useEffect, useState, useCallback } from 'react';
import { useThemeMode, darkTokens } from '../theme/themeMode';
import type {
  DevContributionState,
  DevFinding,
  DevStateTransitionRecord,
  DevSubmission,
  DevSubmissionDetailResponse,
  DevValidationReport,
} from './devSubmissionTypes';
import {
  getDevSubmission,
  getHistory,
  listDevSubmissions,
  rescanDevSubmission,
  transitionDevSubmission,
} from './devSubmissionApiClient';

/**
 * 状态徽章颜色映射（与 ContributionReviewBoard.STATE_COLORS 同一色板语义，
 * draft=gray…published=teal；非另一套色板，design DC-06 / T20.1）。
 */
const STATE_COLORS: Record<DevContributionState, string> = {
  draft: 'gray',
  submitted: 'blue',
  security_scan: 'orange',
  reviewing: 'yellow',
  approved: 'green',
  rejected: 'red',
  published: 'teal',
};

/** 报告发现项按 layer 分组（L1/L2/L3 三层，DC-06） */
function groupFindingsByLayer(report: DevValidationReport): DevFinding[][] {
  const layers = ['L1', 'L2', 'L3'];
  return layers.map((layer) =>
    report.findings.filter((finding) => finding.layer === layer),
  );
}

/** 开发包审核台 */
export function DevReviewBoard({ operatorId }: { operatorId: string }): JSX.Element {
  const [submissions, setSubmissions] = useState<DevSubmission[]>([]);
  const [selected, setSelected] = useState<DevSubmission | null>(null);
  const [detail, setDetail] = useState<DevSubmissionDetailResponse | null>(null);
  const [history, setHistory] = useState<DevStateTransitionRecord[]>([]);
  const [rejectionReason, setRejectionReason] = useState('');
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const themeMode = useThemeMode();
  const isDark = themeMode === 'dark';

  const loadData = useCallback(async () => {
    try {
      const response = await listDevSubmissions();
      setSubmissions(response.submissions);
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载开发提交清单失败');
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const handleSelect = async (submission: DevSubmission) => {
    setSelected(submission);
    setRejectionReason('');
    setMessage(null);
    try {
      const detailResponse = await getDevSubmission(submission.submission_id);
      setDetail(detailResponse);
    } catch {
      setDetail(null);
    }
    try {
      const historyResponse = await getHistory(submission.submission_id);
      setHistory(historyResponse.history);
    } catch {
      setHistory([]);
    }
  };

  /** 执行状态流转（DV-6）；失败 → 错误条 + 状态不变由后端保证 */
  const runTransition = async (
    toState: DevContributionState,
    reason: string = '',
  ) => {
    if (!selected) return;
    setError(null);
    setMessage(null);
    try {
      const response = await transitionDevSubmission(
        selected.submission_id,
        toState,
        operatorId,
        reason,
      );
      setMessage(response.message || '流转成功');
      await loadData();
      const refreshed = await getDevSubmission(selected.submission_id);
      setDetail(refreshed);
      setSelected(refreshed.submission);
      const historyResponse = await getHistory(selected.submission_id);
      setHistory(historyResponse.history);
    } catch (err) {
      setError(err instanceof Error ? err.message : '审核流转失败');
    }
  };

  /** 重新校验（DV-7）：追加报告留档，不覆盖历史 */
  const handleRescan = async () => {
    if (!selected) return;
    setError(null);
    setMessage(null);
    try {
      await rescanDevSubmission(selected.submission_id, operatorId);
      setMessage('已重新校验，报告已追加留档');
      const detailResponse = await getDevSubmission(selected.submission_id);
      setDetail(detailResponse);
      setSelected(detailResponse.submission);
    } catch (err) {
      setError(err instanceof Error ? err.message : '重新校验失败');
    }
  };

  const latestReport = detail?.latest_report ?? null;
  const groupedFindings = latestReport ? groupFindingsByLayer(latestReport) : [];

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
.wfp-crb-message{margin-bottom:16px;padding:12px 16px;border-radius:8px;border:1px solid ${isDark?darkTokens.surface.divider:"#e0e0e0"};border-left:3px solid ${isDark?"#66bb6a":"#4caf50"};background:${isDark?"rgba(102,187,106,0.08)":"#f1f8f4"};font-size:12px;color:${isDark?darkTokens.text.secondary:"#616161"};}
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
.wfp-crb-actions{display:flex;gap:8px;align-items:center;margin-top:12px;flex-wrap:wrap;}
.wfp-crb-actions .wfp-crb-input{flex:1;min-width:120px;}
.wfp-crb-empty{text-align:center;padding:24px 16px;color:${isDark?darkTokens.text.hint:"#9e9e9e"};font-size:13px;}
`}</style>
      <h2 className="wfp-crb-title">开发包审核</h2>
      {error && <div className="wfp-crb-error" data-testid="dev-review-error">{error}</div>}
      {message && <div className="wfp-crb-message" data-testid="dev-review-message">{message}</div>}

      <div className="wfp-crb-layout">
        {/* 提交清单 */}
        <div className="wfp-crb-list">
          <table className="wfp-crb-table" data-testid="dev-review-table">
            <thead>
              <tr>
                <th>节点名称</th>
                <th>版本</th>
                <th>状态</th>
                <th>开发者</th>
              </tr>
            </thead>
            <tbody>
              {submissions.length === 0 ? (
                <tr><td colSpan={4} className="wfp-crb-empty">暂无开发提交</td></tr>
              ) : submissions.map((s) => (
                <tr key={s.submission_id} onClick={() => handleSelect(s)}>
                  <td>{s.node_name}</td>
                  <td>{s.version}</td>
                  <td>
                    <span className="wfp-crb-badge" style={{ color: STATE_COLORS[s.state] }}>{s.state}</span>
                  </td>
                  <td>{s.developer_id}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* 提交详情 */}
        {selected && (
          <div className="wfp-crb-detail" data-testid="dev-review-detail">
            <h3 className="wfp-crb-detail-title">{selected.node_name}@{selected.version}</h3>
            <p>状态: <span className="wfp-crb-badge" style={{ color: STATE_COLORS[selected.state] }}>{selected.state}</span></p>
            <p>提交标识: {selected.submission_id}</p>
            <p>包摘要: {selected.package_digest.slice(0, 16)}…（{selected.package_size} 字节 / {selected.file_count} 文件）</p>
            {selected.rejection_reason && <p>驳回原因: {selected.rejection_reason}</p>}

            {/* 三层报告（按 layer 分组，仅最新一份） */}
            {latestReport && (
              <div className="wfp-crb-reports" data-testid="dev-review-report">
                <h4 className="wfp-crb-section-title">校验报告（{latestReport.valid ? '通过' : '未通过'}；扫描器 {latestReport.scanner_version}）</h4>
                {['L1', 'L2', 'L3'].map((layer, index) => (
                  <div key={layer} data-testid={`dev-review-report-${layer}`}>
                    <p>{layer} 层发现（{groupedFindings[index].length} 项）</p>
                    {groupedFindings[index].length > 0 && (
                      <ul>
                        {groupedFindings[index].map((finding, idx) => (
                          <li key={idx}>
                            [{finding.severity}] {finding.rule_id}：{finding.message}
                            {finding.location ? `（${finding.location}）` : ''}
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                ))}
              </div>
            )}

            {/* 流转历史 */}
            {history.length > 0 && (
              <div className="wfp-crb-history" data-testid="dev-review-history">
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

            {/* 审核动作（按合法迁移可用） */}
            <div className="wfp-crb-actions">
              {selected.state === 'submitted' && (
                <button
                  className="wfp-crb-btn"
                  onClick={() => runTransition('security_scan')}
                  data-testid="dev-review-scan-button"
                >
                  进入安全扫描
                </button>
              )}
              {selected.state === 'security_scan' && (
                <>
                  <button
                    className="wfp-crb-btn"
                    onClick={handleRescan}
                    data-testid="dev-review-rescan-button"
                  >
                    重新校验
                  </button>
                  <button
                    className="wfp-crb-btn"
                    onClick={() => runTransition('reviewing')}
                    data-testid="dev-review-review-button"
                  >
                    送人工审核
                  </button>
                </>
              )}
              {selected.state === 'reviewing' && (
                <button
                  className="wfp-crb-btn wfp-crb-btn--primary"
                  onClick={() => runTransition('approved')}
                  data-testid="dev-review-approve-button"
                >
                  批准
                </button>
              )}
              {['submitted', 'security_scan', 'reviewing'].includes(selected.state) && (
                <>
                  <input
                    className="wfp-crb-input"
                    type="text"
                    placeholder="驳回理由"
                    value={rejectionReason}
                    onChange={(e) => setRejectionReason(e.target.value)}
                    data-testid="dev-review-reject-reason-input"
                  />
                  <button
                    className="wfp-crb-btn wfp-crb-btn--danger"
                    onClick={() => runTransition('rejected', rejectionReason)}
                    disabled={!rejectionReason}
                    data-testid="dev-review-reject-button"
                  >
                    驳回
                  </button>
                </>
              )}
              {selected.state === 'approved' && (
                <button
                  className="wfp-crb-btn wfp-crb-btn--primary"
                  onClick={() => runTransition('published')}
                  data-testid="dev-review-publish-button"
                >
                  触发上架
                </button>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
