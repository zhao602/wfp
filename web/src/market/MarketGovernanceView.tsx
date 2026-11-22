// =============================================================================
// 市场治理只读视图（web/src/market/MarketGovernanceView.tsx）
//
// 职责：治理只读视图 —— 复用 GET /api/v1/agent-market/entries?status=...（后端零新增端点），
//       条目表 + MarketEntryStatus 状态徽章 + "写操作后续开放"禁用提示。
// 设计意图（DC-10）：
//   1. 本期零写操作：不提供批准/驳回/流转入口（OP-7，禁用按钮无任何 onClick 写操作）；
//   2. 状态徽章色映射对齐 ContributionReviewBoard 范式：
//      builtin→teal / available→green / draft→gray / pending_review→yellow；
//   3. 复用 wfp-crb-* 同类内联样式范式（wfp-mgv-* 前缀，AC-23）。
// =============================================================================

import { useEffect, useState, useCallback } from 'react';
import type { AgentMarketEntry, MarketEntryStatus } from './marketTypes';
import { listAgentMarketEntries } from './marketApiClient';
import { useThemeMode, darkTokens } from '../theme/themeMode';

/** 市场条目状态徽章色映射（DC-10） */
const GOVERNANCE_STATUS_COLORS: Record<MarketEntryStatus, string> = {
  builtin: 'teal',
  available: 'green',
  draft: 'gray',
  pending_review: 'yellow',
};

/** 状态筛选选项（只读检索维度，非写操作） */
const STATUS_OPTIONS: Array<{ value: string; label: string }> = [
  { value: '', label: '全部状态' },
  { value: 'builtin', label: '内置' },
  { value: 'available', label: '可用' },
  { value: 'draft', label: '草稿' },
  { value: 'pending_review', label: '待审核' },
];

/** 市场治理只读视图：条目 + 状态徽章 + 禁用写操作提示（零写操作入口） */
export function MarketGovernanceView(): JSX.Element {
  const [entries, setEntries] = useState<AgentMarketEntry[]>([]);
  const [total, setTotal] = useState<number>(0);
  const [status, setStatus] = useState('');
  const [error, setError] = useState<string | null>(null);
  const themeMode = useThemeMode();
  const isDark = themeMode === 'dark';

  /** 拉取条目（复用 entries?status=，DC-10）：失败 → 错误条且不清空既有数据（AC-24 规则 2） */
  const loadEntries = useCallback(async () => {
    try {
      const response = await listAgentMarketEntries({
        status: status === '' ? undefined : (status as MarketEntryStatus),
      });
      setEntries(response.entries);
      setTotal(response.total);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载失败');
    }
  }, [status]);

  useEffect(() => {
    loadEntries();
  }, [loadEntries]);

  return (
    <div className="wfp-mgv-panel">
      <style>{`
.wfp-mgv-select{padding:6px 10px;font-size:13px;font-family:inherit;color:${isDark ? darkTokens.text.primary : '#333333'};background-color:${isDark ? darkTokens.surface.bg : '#ffffff'};border:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};border-radius:5px;outline:none;}
.wfp-mgv-table{width:100%;border-collapse:separate;border-spacing:0;font-size:12px;border:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};border-radius:8px;overflow:hidden;}
.wfp-mgv-table thead th{background:${isDark ? darkTokens.surface.bgHover : '#f5f5f5'};font-weight:600;text-align:left;padding:8px 12px;border-bottom:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};color:${isDark ? darkTokens.text.primary : '#1a1a1a'}}
.wfp-mgv-table tbody td{padding:7px 12px;border-bottom:1px solid ${isDark ? darkTokens.surface.divider : '#eee'};color:${isDark ? darkTokens.text.primary : '#1a1a1a'}}
.wfp-mgv-table tbody tr:last-child td{border-bottom:none}
.wfp-mgv-table tbody tr:hover{background:${isDark ? darkTokens.surface.bgHover : '#fafafa'}}
.wfp-mgv-btn{font-size:13px;font-weight:500;font-family:inherit;padding:6px 14px;border-radius:6px;outline:none;border:1px solid ${isDark ? darkTokens.surface.border : '#d0d0d0'};background:${isDark ? darkTokens.surface.bg : '#ffffff'};color:${isDark ? darkTokens.text.primary : '#1a1a1a'};}
.wfp-mgv-btn:disabled{opacity:0.5;cursor:not-allowed}
.wfp-mgv-panel{padding:20px 24px;background:${isDark ? darkTokens.surface.bg : '#ffffff'};border:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};border-radius:8px;}
.wfp-mgv-title{font-size:15px;font-weight:600;margin:0 0 16px;padding-bottom:12px;border-bottom:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};color:${isDark ? darkTokens.text.primary : '#1a1a1a'};}
.wfp-mgv-error{margin-bottom:16px;padding:12px 16px;border-radius:8px;border:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};border-left:3px solid ${isDark ? darkTokens.danger : '#d32f2f'};background:${isDark ? 'rgba(255,107,107,0.08)' : '#fdecea'};font-size:12px;color:${isDark ? darkTokens.danger : '#d32f2f'};}
.wfp-mgv-badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:500;background:${isDark ? darkTokens.surface.bgActive : '#f0f0f0'};}
.wfp-mgv-toolbar{display:flex;gap:8px;align-items:center;margin-bottom:12px;flex-wrap:wrap;}
.wfp-mgv-notice{margin-top:16px;padding:10px 14px;border-radius:8px;border:1px dashed ${isDark ? darkTokens.surface.border : '#d0d0d0'};font-size:12px;color:${isDark ? darkTokens.text.hint : '#9e9e9e'};}
.wfp-mgv-empty{text-align:center;padding:24px 16px;color:${isDark ? darkTokens.text.hint : '#9e9e9e'};font-size:13px;}
`}</style>
      <h2 className="wfp-mgv-title">市场治理（只读，共 {total} 条）</h2>
      {error && <div className="wfp-mgv-error" data-testid="market-governance-error">{error}</div>}

      <div className="wfp-mgv-toolbar">
        <select
          className="wfp-mgv-select"
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          data-testid="market-governance-status-select"
        >
          {STATUS_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        {/* 零写操作入口（DC-10/AC-22）：禁用按钮，无任何 onClick 写操作 */}
        <button className="wfp-mgv-btn" disabled title="审核写操作于后续任务开放" data-testid="market-governance-disabled-action">
          审核 / 批准 / 驳回（后续开放）
        </button>
      </div>

      <table className="wfp-mgv-table" data-testid="market-governance-table">
        <thead>
          <tr>
            <th>名称</th>
            <th>分类</th>
            <th>状态</th>
            <th>来源</th>
            <th>更新时间</th>
          </tr>
        </thead>
        <tbody>
          {entries.length === 0 ? (
            <tr>
              <td colSpan={5} className="wfp-mgv-empty">暂无市场条目</td>
            </tr>
          ) : (
            entries.map((entry) => (
              <tr key={entry.entry_id} data-testid={`market-governance-entry-${entry.entry_id}`}>
                <td>{entry.name}</td>
                <td>{entry.category}</td>
                <td>
                  <span className="wfp-mgv-badge" style={{ color: GOVERNANCE_STATUS_COLORS[entry.status] }}>
                    {entry.status}
                  </span>
                </td>
                <td>
                  <span className="wfp-mgv-badge" style={{ color: entry.source_kind === 'builtin' ? 'teal' : entry.source_kind === 'user_dev' ? 'purple' : 'blue' }}>
                    {entry.source_kind === 'builtin' ? '内置' : entry.source_kind === 'user_dev' ? '用户开发' : '已注册'}
                  </span>
                </td>
                <td>{entry.updated_at}</td>
              </tr>
            ))
          )}
        </tbody>
      </table>

      <div className="wfp-mgv-notice">审核写操作（批准 / 驳回 / 流转）于后续任务开放，本期仅提供只读治理视图（OP-7 / DC-10）。</div>
    </div>
  );
}
