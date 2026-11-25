// =============================================================================
// 审批队列面板（web/src/approval-queue/ApprovalQueuePanel.tsx，B16 F4）
//
// 人工复核挂起实例待办台（对齐 sandbox 面板范式）：
//   1. 队列 5s 自动刷新 + 手动刷新；倒计时 1s 秒级走秒（deadline = suspended_at
//      + review_timeout_seconds，服务端计算，UI 仅展示）；
//   2. 超时红标：deadline 已过 → 「已超时」红徽标；剩余 ≤60s → 倒计时红色预警；
//   3. 批量复核：多选 + 通过 / 驳回 / 按超时动作 三决策 + 可选备注（actor 必填）；
//   4. 窄屏适配：表格容器横向滚动（min-width 固定列幅，不挤压换行）；
//   5. 租户范围由服务端裁定（RBAC 激活优先），前端不传租户参数。
// =============================================================================

import { useCallback, useEffect, useState, type JSX } from 'react';
import type {
  ApprovalRow,
  BatchReviewResponse,
  ReviewDecision,
} from './approvalQueueTypes';
import { listApprovals, batchReview } from './approvalQueueClient';
import { useThemeMode, darkTokens } from '../theme/themeMode';

/** 超时动作展示映射（未知值原样透出） */
const TIMEOUT_ACTION_LABELS: Record<string, string> = {
  reject: '驳回',
  fail: '失败',
  continue: '继续',
};

/** 批量决策展示映射 */
const DECISION_LABELS: Record<ReviewDecision, string> = {
  approve: '通过',
  reject: '驳回',
  timeout_action: '按超时动作',
};

function formatDateTime(value: string | null): string {
  if (value === null) return '—';
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString();
}

/** 剩余毫秒（deadline 缺失/非法 → null） */
function remainingMs(deadline: string | null, nowMs: number): number | null {
  if (deadline === null) return null;
  const t = new Date(deadline).getTime();
  if (Number.isNaN(t)) return null;
  return t - nowMs;
}

function formatRemaining(ms: number): string {
  const totalSec = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  const pad = (n: number): string => String(n).padStart(2, '0');
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

function summarizeBatch(resp: BatchReviewResponse, label: string): string {
  const { total, ok, skipped, error } = resp.summary;
  return `批量${label}完成（共 ${total} 项）：成功 ${ok}，跳过 ${skipped}，失败 ${error}`;
}

export interface ApprovalQueuePanelProps {
  /** 复核操作人（批量复核 actor 必填，后端 422 校验） */
  actorId?: string;
}

/** 审批队列面板 */
export function ApprovalQueuePanel({ actorId = 'admin' }: ApprovalQueuePanelProps): JSX.Element {
  const [rows, setRows] = useState<ApprovalRow[]>([]);
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState<number>(() => Date.now());
  const [comment, setComment] = useState('');
  const [reviewing, setReviewing] = useState(false);
  const [batchMessage, setBatchMessage] = useState<string | null>(null);
  const [batchError, setBatchError] = useState<string | null>(null);
  const themeMode = useThemeMode();
  const isDark = themeMode === 'dark';

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const next = await listApprovals();
      setRows(next);
      setError(null);
      setSelected((prev) => {
        if (prev.size === 0) return prev;
        const ids = new Set(next.map((r) => r.instance_id));
        const pruned = new Set([...prev].filter((id) => ids.has(id)));
        return pruned.size === prev.size ? prev : pruned;
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  // 队列 5s 自动刷新（挂起→复核完成即时出队）
  useEffect(() => {
    loadData();
    const id = window.setInterval(() => {
      void loadData();
    }, 5000);
    return () => window.clearInterval(id);
  }, [loadData]);

  // 倒计时 1s 走秒（仅本地时钟，不发请求）
  useEffect(() => {
    const id = window.setInterval(() => {
      setNow(Date.now());
    }, 1000);
    return () => window.clearInterval(id);
  }, []);

  const allSelected = rows.length > 0 && rows.every((r) => selected.has(r.instance_id));

  const toggleRow = (instanceId: string): void => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(instanceId)) {
        next.delete(instanceId);
      } else {
        next.add(instanceId);
      }
      return next;
    });
  };

  const toggleAll = (): void => {
    setSelected(allSelected ? new Set() : new Set(rows.map((r) => r.instance_id)));
  };

  const runBatch = useCallback(
    async (decision: ReviewDecision) => {
      if (selected.size === 0 || reviewing) return;
      setReviewing(true);
      setBatchMessage(null);
      setBatchError(null);
      try {
        const trimmed = comment.trim();
        const resp = await batchReview({
          instance_ids: [...selected],
          decision,
          actor: actorId,
          comment: trimmed === '' ? undefined : trimmed,
        });
        setBatchMessage(summarizeBatch(resp, DECISION_LABELS[decision]));
        setSelected(new Set());
        setComment('');
        await loadData();
      } catch (err) {
        setBatchError(err instanceof Error ? err.message : String(err));
      } finally {
        setReviewing(false);
      }
    },
    [selected, reviewing, comment, actorId, loadData],
  );

  return (
    <div className="wfp-apv-panel">
      <style>{`
.wfp-apv-panel { padding: 20px 24px; background: ${isDark ? darkTokens.surface.bg : "#ffffff"}; border: 1px solid ${isDark ? darkTokens.surface.divider : "#e0e0e0"}; border-radius: 8px; }
.wfp-apv-title { font-size: 15px; font-weight: 600; margin: 0; color: ${isDark ? darkTokens.text.primary : "#1a1a1a"}; }
.wfp-apv-header { display: flex; align-items: center; gap: 12px; padding-bottom: 12px; margin-bottom: 16px; border-bottom: 1px solid ${isDark ? darkTokens.surface.divider : "#e0e0e0"}; flex-wrap: wrap; }
.wfp-apv-count { display: inline-block; padding: 2px 10px; border-radius: 10px; font-size: 12px; font-weight: 500; color: ${isDark ? darkTokens.text.secondary : "#616161"}; background: ${isDark ? darkTokens.surface.bgHover : "#f5f5f5"}; white-space: nowrap; }
.wfp-apv-hint { font-size: 12px; color: ${isDark ? darkTokens.text.hint : "#9e9e9e"}; }
.wfp-apv-btn { font-size: 13px; font-weight: 500; font-family: inherit; padding: 6px 14px; border-radius: 6px; cursor: pointer; outline: none; border: 1px solid ${isDark ? darkTokens.surface.border : "#d0d0d0"}; background: ${isDark ? darkTokens.surface.bg : "#ffffff"}; color: ${isDark ? darkTokens.text.primary : "#1a1a1a"}; transition: background-color 120ms ease, border-color 120ms ease; }
.wfp-apv-btn:hover { background: ${isDark ? darkTokens.surface.bgHover : "#f5f5f5"}; border-color: ${isDark ? darkTokens.surface.borderStrong : "#b8b8b8"}; }
.wfp-apv-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.wfp-apv-btn--danger { color: ${isDark ? darkTokens.danger : "#d32f2f"}; border-color: ${isDark ? darkTokens.danger : "#d32f2f"}; background: transparent; }
.wfp-apv-btn--danger:hover:not(:disabled) { background: ${isDark ? "rgba(255,107,107,0.12)" : "#fdecea"}; }
.wfp-apv-toolbar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 12px; padding: 10px 12px; background: ${isDark ? darkTokens.surface.bgHover : "#fafafa"}; border: 1px solid ${isDark ? darkTokens.surface.divider : "#e0e0e0"}; border-radius: 8px; }
.wfp-apv-selected { font-size: 12px; font-weight: 600; color: ${isDark ? darkTokens.text.primary : "#1a1a1a"}; white-space: nowrap; }
.wfp-apv-input { flex: 1; min-width: 180px; box-sizing: border-box; padding: 6px 10px; font-size: 12px; font-family: inherit; color: ${isDark ? darkTokens.text.primary : "#333333"}; background-color: ${isDark ? darkTokens.surface.bg : "#ffffff"}; border: 1px solid ${isDark ? darkTokens.surface.divider : "#e0e0e0"}; border-radius: 5px; outline: none; }
.wfp-apv-input::placeholder { color: ${isDark ? darkTokens.text.hint : "#b0b0b0"}; }
.wfp-apv-input:focus { border-color: ${isDark ? darkTokens.surface.borderStrong : "#bdbdbd"}; }
.wfp-apv-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.wfp-apv-tablewrap { overflow-x: auto; }
.wfp-apv-table { width: 100%; min-width: 900px; border-collapse: separate; border-spacing: 0; font-size: 12px; border: 1px solid ${isDark ? darkTokens.surface.divider : "#e0e0e0"}; border-radius: 8px; overflow: hidden; }
.wfp-apv-table thead th { background: ${isDark ? darkTokens.surface.bgHover : "#f5f5f5"}; font-weight: 600; text-align: left; padding: 8px 12px; border-bottom: 1px solid ${isDark ? darkTokens.surface.divider : "#e0e0e0"}; color: ${isDark ? darkTokens.text.primary : "#1a1a1a"}; white-space: nowrap; }
.wfp-apv-table tbody td { padding: 7px 12px; border-bottom: 1px solid ${isDark ? darkTokens.surface.divider : "#eee"}; color: ${isDark ? darkTokens.text.primary : "#1a1a1a"}; vertical-align: top; }
.wfp-apv-table tbody tr:last-child td { border-bottom: none; }
.wfp-apv-table tbody tr:hover { background: ${isDark ? darkTokens.surface.bgHover : "#fafafa"}; }
.wfp-apv-empty { text-align: center; padding: 24px 16px; color: ${isDark ? darkTokens.text.hint : "#9e9e9e"}; font-size: 13px; }
.wfp-apv-mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 11px; word-break: break-all; }
.wfp-apv-sub { margin-top: 3px; font-size: 11px; color: ${isDark ? darkTokens.text.hint : "#9e9e9e"}; word-break: break-all; }
.wfp-apv-muted { color: ${isDark ? darkTokens.text.hint : "#9e9e9e"}; }
.wfp-apv-countdown { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-weight: 600; white-space: nowrap; }
.wfp-apv-countdown--danger { color: ${isDark ? darkTokens.danger : "#d32f2f"}; }
.wfp-apv-timeout { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 600; color: #ffffff; background: ${isDark ? darkTokens.danger : "#d32f2f"}; white-space: nowrap; }
.wfp-apv-badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 500; color: ${isDark ? darkTokens.text.secondary : "#616161"}; background: ${isDark ? darkTokens.surface.bgHover : "#f0f0f0"}; white-space: nowrap; }
.wfp-apv-instr { max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; display: inline-block; vertical-align: bottom; }
.wfp-apv-msg { margin-bottom: 12px; padding: 10px 14px; border-radius: 8px; font-size: 12px; }
.wfp-apv-msg--success { border: 1px solid ${isDark ? darkTokens.surface.divider : "#c8e6c9"}; background: ${isDark ? "rgba(105,207,126,0.08)" : "#f1f8f2"}; color: ${isDark ? darkTokens.text.secondary : "#2e7d32"}; }
.wfp-apv-msg--error { border: 1px solid ${isDark ? darkTokens.surface.divider : "#ffcdd2"}; background: ${isDark ? "rgba(255,107,107,0.08)" : "#fdecea"}; color: ${isDark ? darkTokens.danger : "#c62828"}; }
.wfp-apv-msg--error .wfp-apv-btn { margin-left: 10px; }
      `}</style>
      <div className="wfp-apv-header">
        <h2 className="wfp-apv-title">审批队列</h2>
        <span className="wfp-apv-count">待审批 {rows.length} 项</span>
        <span className="wfp-apv-hint">{loading ? '刷新中…' : '每 5 秒自动刷新'}</span>
        <button
          className="wfp-apv-btn"
          style={{ marginLeft: 'auto' }}
          onClick={() => {
            void loadData();
          }}
          disabled={loading}
        >
          手动刷新
        </button>
      </div>

      {error !== null && (
        <div className="wfp-apv-msg wfp-apv-msg--error">
          队列加载失败：{error}
          <button
            className="wfp-apv-btn"
            onClick={() => {
              void loadData();
            }}
          >
            重试
          </button>
        </div>
      )}
      {batchMessage !== null && (
        <div className="wfp-apv-msg wfp-apv-msg--success">{batchMessage}</div>
      )}
      {batchError !== null && (
        <div className="wfp-apv-msg wfp-apv-msg--error">批量复核失败：{batchError}</div>
      )}

      {rows.length > 0 && (
        <div className="wfp-apv-toolbar">
          <span className="wfp-apv-selected">已选 {selected.size} 项</span>
          <input
            className="wfp-apv-input"
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            placeholder="复核备注（可选）"
          />
          <div className="wfp-apv-actions">
            <button
              className="wfp-apv-btn"
              onClick={() => {
                void runBatch('approve');
              }}
              disabled={selected.size === 0 || reviewing}
            >
              通过
            </button>
            <button
              className="wfp-apv-btn wfp-apv-btn--danger"
              onClick={() => {
                void runBatch('reject');
              }}
              disabled={selected.size === 0 || reviewing}
            >
              驳回
            </button>
            <button
              className="wfp-apv-btn"
              onClick={() => {
                void runBatch('timeout_action');
              }}
              disabled={selected.size === 0 || reviewing}
            >
              按超时动作
            </button>
          </div>
        </div>
      )}

      <div className="wfp-apv-tablewrap">
        <table className="wfp-apv-table" data-testid="approval-queue-table">
          <thead>
            <tr>
              <th>
                <input
                  type="checkbox"
                  aria-label="全选当页"
                  checked={allSelected}
                  onChange={toggleAll}
                />
              </th>
              <th>实例</th>
              <th>挂起时间</th>
              <th>剩余时限</th>
              <th>超时动作</th>
              <th>复核说明</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={6} className="wfp-apv-empty">
                  暂无待审批实例
                </td>
              </tr>
            ) : (
              rows.map((row) => {
                const remaining = remainingMs(row.deadline, now);
                const timedOut = remaining !== null && remaining <= 0;
                const danger = !timedOut && remaining !== null && remaining <= 60_000;
                return (
                  <tr key={row.instance_id}>
                    <td>
                      <input
                        type="checkbox"
                        aria-label={`选择实例 ${row.instance_id}`}
                        checked={selected.has(row.instance_id)}
                        onChange={() => toggleRow(row.instance_id)}
                      />
                    </td>
                    <td>
                      <div className="wfp-apv-mono">{row.instance_id}</div>
                      <div className="wfp-apv-sub">
                        {row.definition_id}
                        {row.definition_version !== undefined ? ` · v${row.definition_version}` : ''}
                        {row.tenant_id !== null ? ` · ${row.tenant_id}` : ''}
                      </div>
                    </td>
                    <td>{formatDateTime(row.suspended_at)}</td>
                    <td>
                      {timedOut ? (
                        <span className="wfp-apv-timeout">已超时</span>
                      ) : remaining === null ? (
                        <span className="wfp-apv-muted">—</span>
                      ) : (
                        <span
                          className={
                            danger ? 'wfp-apv-countdown wfp-apv-countdown--danger' : 'wfp-apv-countdown'
                          }
                        >
                          {formatRemaining(remaining)}
                        </span>
                      )}
                    </td>
                    <td>
                      <span className="wfp-apv-badge">
                        {TIMEOUT_ACTION_LABELS[row.timeout_action] ?? row.timeout_action}
                      </span>
                    </td>
                    <td>
                      {row.review_instructions === '' ? (
                        <span className="wfp-apv-muted">—</span>
                      ) : (
                        <span className="wfp-apv-instr" title={row.review_instructions}>
                          {row.review_instructions}
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
