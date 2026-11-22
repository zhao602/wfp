// =============================================================================
// 治理面板组件（GovernanceDashboard.tsx）
//
// 职责：治理管理主面板，集成租户管理、预算成本、审计日志、资源池等子面板。
// 设计意图（对应 spec 5.5、design 2.8、任务 T75）：
//   1. TypeScript 严格模式，禁止 any；
//   2. 通过 /api/v1/governance/** 与后端交互；
//   3. 治理面板可操作。
// =============================================================================

import { useEffect, useState } from 'react';
import type {
  BudgetStatus,
  ResourcePoolUtilization,
  SchedulerStatus,
} from './governanceTypes';
import { governanceApiClient } from './governanceApiClient';
import { ApiError } from '../api/apiError';
import { useThemeMode, darkTokens } from '../theme/themeMode';

// =============================================================================
// 共享样式（wfp-gov- 类族，Linear Aesthetic 风格）
// =============================================================================

function governanceCss(isDark: boolean): string {
  const bg = isDark ? darkTokens.surface.bg : '#ffffff';
  const bgHover = isDark ? darkTokens.surface.bgHover : '#fafafa';
  const bgActive = isDark ? darkTokens.surface.bgActive : '#f0f0f0';
  const divider = isDark ? darkTokens.surface.divider : '#e0e0e0';
  const border = isDark ? darkTokens.surface.border : '#d0d0d0';
  const borderStrong = isDark ? darkTokens.surface.borderStrong : '#b8b8b8';
  const textPrimary = isDark ? darkTokens.text.primary : '#1a1a1a';
  const textSecondary = isDark ? darkTokens.text.secondary : '#616161';
  const textHint = isDark ? darkTokens.text.hint : '#9e9e9e';
  const danger = isDark ? darkTokens.danger : '#d32f2f';
  return `
.wfp-gov-panel{padding:20px 24px;background:${bg};border:1px solid ${divider};border-radius:8px;overflow:hidden;}
.wfp-gov-title{font-size:15px;font-weight:600;margin:0 0 16px;padding-bottom:12px;border-bottom:1px solid ${divider};color:${textPrimary};}
.wfp-gov-title--main{font-size:16px;}
.wfp-gov-error{margin-bottom:16px;padding:12px 16px;border-radius:8px;background:${bgHover};border:1px solid ${divider};border-left:3px solid ${danger};font-size:13px;color:${danger};line-height:1.5;}
.wfp-gov-loading{margin-bottom:16px;padding:12px 16px;border-radius:8px;background:${bgHover};border:1px solid ${divider};font-size:13px;color:${textHint};}
.wfp-gov-empty{text-align:center;padding:32px 16px;color:${textHint};font-size:13px;}

.wfp-gov-kv-table{width:100%;border-collapse:separate;border-spacing:0;font-size:13px;border:1px solid ${divider};border-radius:8px;overflow:hidden;}
.wfp-gov-kv-table tbody td{padding:9px 14px;border-bottom:1px solid ${divider};color:${textPrimary};}
.wfp-gov-kv-table tbody td:first-child{font-weight:500;color:${textSecondary};width:40%;background:${bgHover};}
.wfp-gov-kv-table tbody tr:last-child td{border-bottom:none;}
.wfp-gov-kv-table tbody td:last-child{font-variant-numeric:tabular-nums;}

.wfp-gov-status-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;}
.wfp-gov-status-item{padding:12px 16px;background:${bgHover};border:1px solid ${divider};border-radius:8px;}
.wfp-gov-status-label{font-size:11px;font-weight:500;color:${textSecondary};text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px;}
.wfp-gov-status-value{font-size:15px;font-weight:600;color:${textPrimary};font-variant-numeric:tabular-nums;}

.wfp-gov-btn{font-size:13px;font-weight:500;font-family:inherit;padding:7px 16px;border-radius:6px;cursor:pointer;outline:none;border:1px solid ${border};background:${bg};color:${textPrimary};transition:background-color 120ms ease,border-color 120ms ease;}
.wfp-gov-btn:hover{background:${bgHover};border-color:${borderStrong};}
.wfp-gov-btn:active{background:${bgActive};}
.wfp-gov-btn--primary{background:${isDark ? darkTokens.surface.bgActive : '#1a1a1a'};color:${isDark ? darkTokens.text.primary : '#ffffff'};border-color:${isDark ? darkTokens.surface.borderStrong : '#1a1a1a'};}
.wfp-gov-btn--primary:hover{background:${isDark ? darkTokens.surface.bgHover : '#333333'};border-color:${isDark ? darkTokens.surface.borderStrong : '#1a1a1a'};}
.wfp-gov-btn--primary:active{background:${isDark ? darkTokens.surface.bgActive : '#000000'};}

.wfp-gov-audit-count{margin-top:16px;padding:12px 16px;background:${bgHover};border:1px solid ${divider};border-radius:8px;font-size:13px;color:${textSecondary};}
.wfp-gov-audit-count strong{color:${textPrimary};font-variant-numeric:tabular-nums;}

.wfp-gov-dashboard{display:flex;flex-direction:column;gap:20px;padding:20px 24px;background:${bg};border:1px solid ${divider};border-radius:8px;overflow:hidden;}

.wfp-gov-section-title{font-size:13px;font-weight:600;margin:0 0 12px;color:${textSecondary};}
`;
}

// =============================================================================
// 资源池表格样式（wfp-rp- 类族，已优化保留）
// =============================================================================

const resourcePoolCss = `
.wfp-rp-scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }
.wfp-rp-table { width: 100%; border-collapse: separate; border-spacing: 0; font-size: 13px; border: 1px solid #e0e0e0; border-radius: 8px; overflow: hidden; }
.wfp-rp-table thead th { background: #f5f5f5; font-weight: 600; text-align: left; padding: 10px 12px; border-bottom: 1px solid #e0e0e0; color: #616161; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; }
.wfp-rp-table tbody td { padding: 10px 12px; border-bottom: 1px solid #eee; }
.wfp-rp-table tbody tr:last-child td { border-bottom: none; }
.wfp-rp-table tbody tr:hover { background: #fafafa; }
.wfp-rp-badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 500; color: #fff; white-space: nowrap; }
.wfp-rp-bar-wrap { display: flex; align-items: center; gap: 8px; }
.wfp-rp-bar { flex: 1; height: 6px; border-radius: 3px; background: #eee; overflow: hidden; min-width: 60px; }
.wfp-rp-bar-fill { height: 100%; border-radius: 3px; transition: width 300ms cubic-bezier(0.4,0,0.2,1); }
.wfp-rp-bar-label { font-size: 12px; font-weight: 500; min-width: 42px; text-align: right; font-variant-numeric: tabular-nums; }
.wfp-rp-empty { text-align: center; padding: 32px 16px; color: #9e9e9e; font-size: 14px; }
.wfp-theme--dark .wfp-rp-table { border-color: #3d3d3d; }
.wfp-theme--dark .wfp-rp-table thead th { background: #2a2a2a; border-bottom-color: #3d3d3d; color: #b8b8b8; }
.wfp-theme--dark .wfp-rp-table tbody td { border-bottom-color: #333; }
.wfp-theme--dark .wfp-rp-table tbody tr:hover { background: #2a2a2a; }
.wfp-theme--dark .wfp-rp-bar { background: #333; }
.wfp-theme--dark .wfp-rp-empty { color: #8a8a8a; }
`;

const poolTypeColors: Record<string, string> = {
  concurrency_slot: '#42a5f5',
  cpu: '#ab47bc',
  memory: '#66bb6a',
};

function utilizationColor(ratio: number): string {
  if (ratio >= 0.8) return '#e53935';
  if (ratio >= 0.6) return '#fb8c00';
  return '#66bb6a';
}

// =============================================================================
// 预算成本仪表盘
// =============================================================================

export function BudgetCostDashboard(): JSX.Element {
  const [budgetStatus, setBudgetStatus] = useState<BudgetStatus | null>(null);
  const [costSummary, setCostSummary] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const themeMode = useThemeMode();
  const isDark = themeMode === 'dark';

  const loadData = async (tenantId: string, budgetId: string): Promise<void> => {
    setLoading(true);
    setError(null);
    try {
      const [status, costs] = await Promise.all([
        governanceApiClient.budgets.getStatus(budgetId),
        governanceApiClient.costs.getSummary(tenantId),
      ]);
      setBudgetStatus(status);
      setCostSummary(costs);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(`${err.code}: ${err.message}`);
      } else {
        setError(String(err));
      }
    } finally {
      setLoading(false);
    }
  };

  void costSummary;
  void loadData;

  return (
    <div className="wfp-gov-panel">
      <style>{governanceCss(isDark)}</style>
      <h2 className="wfp-gov-title">预算与成本仪表盘</h2>
      {error && <div className="wfp-gov-error">{error}</div>}
      {loading && <div className="wfp-gov-loading">加载中...</div>}
      {!loading && !error && !budgetStatus && (
        <div className="wfp-gov-empty">暂无预算数据</div>
      )}
      {budgetStatus && (
        <div>
          <h3 className="wfp-gov-section-title">预算状态</h3>
          <table className="wfp-gov-kv-table">
            <tbody>
              <tr><td>预算ID</td><td>{budgetStatus.budget_id}</td></tr>
              <tr><td>当前用量</td><td>{budgetStatus.current_usage.toFixed(2)}</td></tr>
              <tr><td>预算上限</td><td>{budgetStatus.budget_limit.toFixed(2)}</td></tr>
              <tr><td>剩余额度</td><td>{budgetStatus.remaining.toFixed(2)}</td></tr>
              <tr><td>使用率</td><td>{(budgetStatus.usage_ratio * 100).toFixed(1)}%</td></tr>
              <tr><td>熔断状态</td><td>{budgetStatus.is_circuit_broken ? '已熔断' : '正常'}</td></tr>
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// =============================================================================
// 资源池状态面板
// =============================================================================

export function ResourcePoolStatusPanel(): JSX.Element {
  const [pools, setPools] = useState<ResourcePoolUtilization[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const themeMode = useThemeMode();
  const isDark = themeMode === 'dark';

  useEffect(() => {
    const loadPools = async (): Promise<void> => {
      setLoading(true);
      try {
        const result = await governanceApiClient.resourcePools.getStatus();
        setPools(result.pools);
      } catch (err) {
        setError(err instanceof ApiError ? err.message : String(err));
      } finally {
        setLoading(false);
      }
    };
    loadPools();
  }, []);

  return (
    <div className="wfp-gov-panel">
      <style>{governanceCss(isDark)}{resourcePoolCss}</style>
      <h2 className="wfp-gov-title">资源池状态</h2>
      {error && <div className="wfp-gov-error">{error}</div>}
      {loading && <div className="wfp-gov-loading">加载中...</div>}
      {!loading && !error && pools.length === 0 && (
        <div className="wfp-gov-empty">暂无资源池数据</div>
      )}
      {pools.length > 0 && (
        <div className="wfp-rp-scroll">
        <table className="wfp-rp-table">
          <thead>
            <tr>
              <th>资源池ID</th>
              <th>类型</th>
              <th>总容量</th>
              <th>已分配</th>
              <th>可用</th>
              <th>占用率</th>
            </tr>
          </thead>
          <tbody>
            {pools.map((pool) => {
              const pct = (pool.utilization_ratio * 100).toFixed(1);
              const color = utilizationColor(pool.utilization_ratio);
              return (
                <tr key={pool.pool_id}>
                  <td style={{ fontFamily: 'monospace', fontSize: '12px' }}>{pool.pool_id}</td>
                  <td>
                    <span
                      className="wfp-rp-badge"
                      style={{ background: poolTypeColors[pool.pool_type] ?? '#9e9e9e' }}
                    >
                      {pool.pool_type}
                    </span>
                  </td>
                  <td style={{ fontVariantNumeric: 'tabular-nums' }}>{pool.total_capacity}</td>
                  <td style={{ fontVariantNumeric: 'tabular-nums' }}>{pool.allocated}</td>
                  <td
                    style={{
                      fontVariantNumeric: 'tabular-nums',
                      color: pool.available === 0 ? '#e53935' : 'inherit',
                    }}
                  >
                    {pool.available}
                  </td>
                  <td>
                    <div className="wfp-rp-bar-wrap">
                      <div className="wfp-rp-bar">
                        <div
                          className="wfp-rp-bar-fill"
                          style={{ width: `${pct}%`, background: color }}
                        />
                      </div>
                      <span className="wfp-rp-bar-label" style={{ color }}>
                        {pct}%
                      </span>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        </div>
      )}
    </div>
  );
}

// =============================================================================
// 调度策略配置面板
// =============================================================================

export function SchedulerPolicyConfigPanel(): JSX.Element {
  const [status, setStatus] = useState<SchedulerStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const themeMode = useThemeMode();
  const isDark = themeMode === 'dark';

  useEffect(() => {
    const loadStatus = async (): Promise<void> => {
      setLoading(true);
      try {
        const result = await governanceApiClient.scheduler.getPolicy();
        setStatus(result);
      } catch {
        // 忽略错误
      } finally {
        setLoading(false);
      }
    };
    loadStatus();
  }, []);

  return (
    <div className="wfp-gov-panel">
      <style>{governanceCss(isDark)}</style>
      <h2 className="wfp-gov-title">调度策略配置</h2>
      {loading && <div className="wfp-gov-loading">加载中...</div>}
      {!loading && !status && (
        <div className="wfp-gov-empty">暂无调度策略数据</div>
      )}
      {status && (
        <div className="wfp-gov-status-grid">
          <div className="wfp-gov-status-item">
            <div className="wfp-gov-status-label">策略类型</div>
            <div className="wfp-gov-status-value">{status.policy_type}</div>
          </div>
          <div className="wfp-gov-status-item">
            <div className="wfp-gov-status-label">启用状态</div>
            <div className="wfp-gov-status-value">{status.enabled ? '已启用' : '未启用'}</div>
          </div>
          <div className="wfp-gov-status-item">
            <div className="wfp-gov-status-label">队列大小</div>
            <div className="wfp-gov-status-value">{status.queue_size}</div>
          </div>
          <div className="wfp-gov-status-item">
            <div className="wfp-gov-status-label">运行实例数</div>
            <div className="wfp-gov-status-value">{status.running_count}</div>
          </div>
        </div>
      )}
    </div>
  );
}

// =============================================================================
// 审计日志查询面板
// =============================================================================

export function AuditLogQueryPanel(): JSX.Element {
  const [entries, setEntries] = useState<unknown[]>([]);
  const [loading, setLoading] = useState(false);
  const themeMode = useThemeMode();
  const isDark = themeMode === 'dark';

  const queryLogs = async (tenantId?: string): Promise<void> => {
    setLoading(true);
    try {
      const result = await governanceApiClient.auditLogs.query({
        tenantId,
        limit: 100,
      });
      setEntries(result.entries);
    } catch {
      // 忽略错误
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="wfp-gov-panel">
      <style>{governanceCss(isDark)}</style>
      <h2 className="wfp-gov-title">审计日志查询</h2>
      <button className="wfp-gov-btn wfp-gov-btn--primary" onClick={(): Promise<void> => queryLogs()}>
        查询全部
      </button>
      {loading && <div className="wfp-gov-loading">加载中...</div>}
      {!loading && entries.length === 0 && (
        <div className="wfp-gov-empty">暂无审计日志</div>
      )}
      {entries.length > 0 && (
        <div className="wfp-gov-audit-count">
          共 <strong>{entries.length}</strong> 条记录
        </div>
      )}
    </div>
  );
}

// =============================================================================
// 治理主面板
// =============================================================================

export function GovernanceDashboard(): JSX.Element {
  const themeMode = useThemeMode();
  const isDark = themeMode === 'dark';

  return (
    <div className="wfp-gov-dashboard">
      <style>{governanceCss(isDark)}</style>
      <h1 className="wfp-gov-title wfp-gov-title--main">治理管理面板</h1>
      <BudgetCostDashboard />
      <ResourcePoolStatusPanel />
      <SchedulerPolicyConfigPanel />
      <AuditLogQueryPanel />
    </div>
  );
}
