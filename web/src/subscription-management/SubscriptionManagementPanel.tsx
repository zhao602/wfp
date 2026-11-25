// =============================================================================
// 订阅管理面板（web/src/subscription-management/SubscriptionManagementPanel.tsx，B16 F5）
//
// Webhook 订阅运营台（对齐 sandbox 面板范式）：
//   1. 订阅 CRUD：表单 upsert（subscription_id 主键）+ 删除；版本钉住字段
//      （definition_id/definition_version，钉版本必先钉定义——客户端前置校验
//      + 服务端 422 兜底）；
//   2. 启停：:enable / :disable 冒号动作路径；
//   3. 密钥轮换：:rotate-secret 服务端生成，响应 secret 一次性明文弹窗展示
//      （D6——关闭后不可再查，提示立即复制）；
//   4. 投递日志：按订阅展开投递记录（终态 delivered/failed 徽标）；
//   5. 死信队列：全量死信条目 + 逐条重投（redelivered=false 仍留队列提示）；
//   6. 窄屏适配：三表横向滚动容器。
// =============================================================================

import { useCallback, useEffect, useState, type JSX } from 'react';
import type {
  Subscription,
  SubscriptionUpsertRequest,
  RotatedSecretResponse,
  DeliveryRecord,
  DeadLetterEntry,
} from './subscriptionTypes';
import {
  listSubscriptions,
  upsertSubscription,
  deleteSubscription,
  enableSubscription,
  disableSubscription,
  rotateSecret,
  listDeliveries,
  listDeadLetters,
  redriveDeadLetter,
} from './subscriptionClient';
import { useThemeMode, darkTokens } from '../theme/themeMode';

interface SubscriptionFormState {
  subscription_id: string;
  tenant_id: string;
  url: string;
  secret: string;
  event_types: string;
  definition_id: string;
  definition_version: string;
  enabled: boolean;
  description: string;
}

const EMPTY_FORM: SubscriptionFormState = {
  subscription_id: '',
  tenant_id: '',
  url: '',
  secret: '',
  event_types: '',
  definition_id: '',
  definition_version: '',
  enabled: true,
  description: '',
};

function formatDateTime(value: string | null): string {
  if (value === null) return '—';
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString();
}

/** 表单状态 → 请求体（event_types 逗号拆分；钉住字段空串归一 null） */
function parseForm(form: SubscriptionFormState): SubscriptionUpsertRequest | null {
  const trimmed = {
    subscription_id: form.subscription_id.trim(),
    tenant_id: form.tenant_id.trim(),
    url: form.url.trim(),
    definition_id: form.definition_id.trim(),
    definition_version: form.definition_version.trim(),
    description: form.description.trim(),
  };
  if (trimmed.subscription_id === '' || trimmed.tenant_id === '' || trimmed.url === '' || form.secret === '') {
    return null;
  }
  if (!trimmed.url.startsWith('http://') && !trimmed.url.startsWith('https://')) {
    return null;
  }
  let version: number | null = null;
  if (trimmed.definition_version !== '') {
    const parsed = Number.parseInt(trimmed.definition_version, 10);
    if (Number.isNaN(parsed) || parsed < 1) {
      return null;
    }
    version = parsed;
  }
  if (version !== null && trimmed.definition_id === '') {
    return null;
  }
  return {
    subscription_id: trimmed.subscription_id,
    tenant_id: trimmed.tenant_id,
    url: trimmed.url,
    secret: form.secret,
    event_types: form.event_types
      .split(',')
      .map((entry) => entry.trim())
      .filter((entry) => entry !== ''),
    definition_id: trimmed.definition_id === '' ? null : trimmed.definition_id,
    definition_version: version,
    enabled: form.enabled,
    description: trimmed.description,
  };
}

/** 订阅管理面板 */
export function SubscriptionManagementPanel(): JSX.Element {
  const [subs, setSubs] = useState<Subscription[]>([]);
  const [deadLetters, setDeadLetters] = useState<DeadLetterEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<SubscriptionFormState>(EMPTY_FORM);
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [deliveriesFor, setDeliveriesFor] = useState<string | null>(null);
  const [deliveries, setDeliveries] = useState<DeliveryRecord[]>([]);
  const [rotateResult, setRotateResult] = useState<RotatedSecretResponse | null>(null);
  const [secretCopied, setSecretCopied] = useState(false);
  const themeMode = useThemeMode();
  const isDark = themeMode === 'dark';

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [nextSubs, nextDeadLetters] = await Promise.all([
        listSubscriptions(),
        listDeadLetters(),
      ]);
      setSubs(nextSubs);
      setDeadLetters(nextDeadLetters);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const withActionGuard = useCallback(
    async (fn: () => Promise<void>): Promise<void> => {
      setBusy(true);
      setActionMessage(null);
      setActionError(null);
      try {
        await fn();
      } catch (err) {
        setActionError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  const updateForm = (patch: Partial<SubscriptionFormState>): void => {
    setForm((prev) => ({ ...prev, ...patch }));
  };

  const openCreateForm = (): void => {
    setForm(EMPTY_FORM);
    setFormError(null);
    setShowForm(true);
  };

  const openEditForm = (sub: Subscription): void => {
    setForm({
      subscription_id: sub.subscription_id,
      tenant_id: sub.tenant_id,
      url: sub.url,
      secret: '',
      event_types: sub.event_types.join(', '),
      definition_id: sub.definition_id ?? '',
      definition_version: sub.definition_version === null ? '' : String(sub.definition_version),
      enabled: sub.enabled,
      description: sub.description,
    });
    setFormError(null);
    setShowForm(true);
  };

  const handleUpsert = (): void => {
    const parsed = parseForm(form);
    if (parsed === null) {
      setFormError(
        '表单校验失败：标识/租户/回调地址/密钥必填，url 仅支持 http/https，' +
          '版本钉住需与定义标识同时提供且 ≥1',
      );
      return;
    }
    void withActionGuard(async () => {
      await upsertSubscription(parsed);
      setShowForm(false);
      setForm(EMPTY_FORM);
      setFormError(null);
      setActionMessage(`订阅 ${parsed.subscription_id} 已保存`);
      await loadData();
    });
  };

  const handleToggleDeliveries = (subscriptionId: string): void => {
    if (deliveriesFor === subscriptionId) {
      setDeliveriesFor(null);
      setDeliveries([]);
      return;
    }
    void withActionGuard(async () => {
      setDeliveriesFor(subscriptionId);
      setDeliveries([]);
      const records = await listDeliveries(subscriptionId);
      setDeliveries(records);
    });
  };

  const handleToggleEnabled = (sub: Subscription): void => {
    void withActionGuard(async () => {
      if (sub.enabled) {
        await disableSubscription(sub.subscription_id);
        setActionMessage(`订阅 ${sub.subscription_id} 已停用`);
      } else {
        await enableSubscription(sub.subscription_id);
        setActionMessage(`订阅 ${sub.subscription_id} 已启用`);
      }
      await loadData();
    });
  };

  const handleRotate = (subscriptionId: string): void => {
    void withActionGuard(async () => {
      const rotated = await rotateSecret(subscriptionId);
      setSecretCopied(false);
      setRotateResult(rotated);
      setActionMessage(`订阅 ${subscriptionId} 密钥已轮换（v${rotated.secret_version}）`);
      await loadData();
    });
  };

  const handleDelete = (subscriptionId: string): void => {
    void withActionGuard(async () => {
      await deleteSubscription(subscriptionId);
      if (deliveriesFor === subscriptionId) {
        setDeliveriesFor(null);
        setDeliveries([]);
      }
      setActionMessage(`订阅 ${subscriptionId} 已删除`);
      await loadData();
    });
  };

  const handleRedrive = (entryId: string): void => {
    void withActionGuard(async () => {
      const resp = await redriveDeadLetter(entryId);
      setActionMessage(
        resp.redelivered
          ? `死信 ${entryId} 重投成功，已移出死信队列`
          : `死信 ${entryId} 重投完成（仍失败，保留在死信队列）`,
      );
      await loadData();
    });
  };

  const closeRotate = (): void => {
    setRotateResult(null);
    setSecretCopied(false);
  };

  const copySecret = (): Promise<void> => {
    if (rotateResult === null) {
      return Promise.resolve();
    }
    return navigator.clipboard
      .writeText(rotateResult.secret)
      .then(() => {
        setSecretCopied(true);
      })
      .catch(() => {
        setSecretCopied(false);
      });
  };

  return (
    <div className="wfp-sub-panel">
      <style>{`
.wfp-sub-panel { padding: 20px 24px; background: ${isDark ? darkTokens.surface.bg : "#ffffff"}; border: 1px solid ${isDark ? darkTokens.surface.divider : "#e0e0e0"}; border-radius: 8px; }
.wfp-sub-title { font-size: 15px; font-weight: 600; margin: 0; color: ${isDark ? darkTokens.text.primary : "#1a1a1a"}; }
.wfp-sub-header { display: flex; align-items: center; gap: 12px; padding-bottom: 12px; margin-bottom: 16px; border-bottom: 1px solid ${isDark ? darkTokens.surface.divider : "#e0e0e0"}; flex-wrap: wrap; }
.wfp-sub-count { display: inline-block; padding: 2px 10px; border-radius: 10px; font-size: 12px; font-weight: 500; color: ${isDark ? darkTokens.text.secondary : "#616161"}; background: ${isDark ? darkTokens.surface.bgHover : "#f5f5f5"}; white-space: nowrap; }
.wfp-sub-hint { font-size: 12px; color: ${isDark ? darkTokens.text.hint : "#9e9e9e"}; }
.wfp-sub-btn { font-size: 13px; font-weight: 500; font-family: inherit; padding: 6px 14px; border-radius: 6px; cursor: pointer; outline: none; border: 1px solid ${isDark ? darkTokens.surface.border : "#d0d0d0"}; background: ${isDark ? darkTokens.surface.bg : "#ffffff"}; color: ${isDark ? darkTokens.text.primary : "#1a1a1a"}; transition: background-color 120ms ease, border-color 120ms ease; }
.wfp-sub-btn:hover:not(:disabled) { background: ${isDark ? darkTokens.surface.bgHover : "#f5f5f5"}; border-color: ${isDark ? darkTokens.surface.borderStrong : "#b8b8b8"}; }
.wfp-sub-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.wfp-sub-btn--danger { color: ${isDark ? darkTokens.danger : "#d32f2f"}; border-color: ${isDark ? darkTokens.danger : "#d32f2f"}; background: transparent; }
.wfp-sub-btn--danger:hover:not(:disabled) { background: ${isDark ? "rgba(255,107,107,0.12)" : "#fdecea"}; }
.wfp-sub-btn--sm { padding: 4px 10px; border-radius: 4px; font-size: 12px; }
.wfp-sub-section { margin-bottom: 20px; }
.wfp-sub-section:last-child { margin-bottom: 0; }
.wfp-sub-section-header { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; flex-wrap: wrap; }
.wfp-sub-section-title { font-size: 14px; font-weight: 600; margin: 0; color: ${isDark ? darkTokens.text.primary : "#1a1a1a"}; }
.wfp-sub-form { margin-bottom: 16px; padding: 16px; background: ${isDark ? darkTokens.surface.bgHover : "#fafafa"}; border: 1px solid ${isDark ? darkTokens.surface.divider : "#e0e0e0"}; border-radius: 8px; }
.wfp-sub-form-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 10px 12px; }
.wfp-sub-field label { display: block; font-size: 11px; font-weight: 600; color: ${isDark ? darkTokens.text.hint : "#9e9e9e"}; margin-bottom: 4px; }
.wfp-sub-input { width: 100%; box-sizing: border-box; padding: 6px 10px; font-size: 12px; font-family: inherit; color: ${isDark ? darkTokens.text.primary : "#333333"}; background-color: ${isDark ? darkTokens.surface.bg : "#ffffff"}; border: 1px solid ${isDark ? darkTokens.surface.divider : "#e0e0e0"}; border-radius: 5px; outline: none; }
.wfp-sub-input:focus { border-color: ${isDark ? darkTokens.surface.borderStrong : "#bdbdbd"}; }
.wfp-sub-input::placeholder { color: ${isDark ? darkTokens.text.hint : "#b0b0b0"}; }
.wfp-sub-check { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: ${isDark ? darkTokens.text.primary : "#333333"}; padding-top: 16px; }
.wfp-sub-form-actions { display: flex; gap: 8px; margin-top: 12px; }
.wfp-sub-tablewrap { overflow-x: auto; }
.wfp-sub-table { width: 100%; min-width: 980px; border-collapse: separate; border-spacing: 0; font-size: 12px; border: 1px solid ${isDark ? darkTokens.surface.divider : "#e0e0e0"}; border-radius: 8px; overflow: hidden; }
.wfp-sub-table--compact { min-width: 760px; }
.wfp-sub-table thead th { background: ${isDark ? darkTokens.surface.bgHover : "#f5f5f5"}; font-weight: 600; text-align: left; padding: 8px 12px; border-bottom: 1px solid ${isDark ? darkTokens.surface.divider : "#e0e0e0"}; color: ${isDark ? darkTokens.text.primary : "#1a1a1a"}; white-space: nowrap; }
.wfp-sub-table tbody td { padding: 7px 12px; border-bottom: 1px solid ${isDark ? darkTokens.surface.divider : "#eee"}; color: ${isDark ? darkTokens.text.primary : "#1a1a1a"}; vertical-align: top; }
.wfp-sub-table tbody tr:last-child td { border-bottom: none; }
.wfp-sub-table tbody tr:hover { background: ${isDark ? darkTokens.surface.bgHover : "#fafafa"}; }
.wfp-sub-empty { text-align: center; padding: 20px 16px; color: ${isDark ? darkTokens.text.hint : "#9e9e9e"}; font-size: 13px; }
.wfp-sub-mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 11px; word-break: break-all; }
.wfp-sub-sub { margin-top: 3px; font-size: 11px; color: ${isDark ? darkTokens.text.hint : "#9e9e9e"}; word-break: break-all; }
.wfp-sub-muted { color: ${isDark ? darkTokens.text.hint : "#9e9e9e"}; }
.wfp-sub-trunc { max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; display: inline-block; vertical-align: bottom; }
.wfp-sub-badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 500; white-space: nowrap; }
.wfp-sub-badge--on { color: #2e7d32; background: #e8f5e9; }
.wfp-sub-badge--off { color: ${isDark ? darkTokens.text.hint : "#9e9e9e"}; background: ${isDark ? darkTokens.surface.bgHover : "#f0f0f0"}; }
.wfp-sub-badge--ok { color: #2e7d32; background: #e8f5e9; }
.wfp-sub-badge--fail { color: ${isDark ? darkTokens.danger : "#d32f2f"}; background: ${isDark ? "rgba(255,107,107,0.12)" : "#fdecea"}; }
.wfp-sub-msg { margin-bottom: 12px; padding: 10px 14px; border-radius: 8px; font-size: 12px; }
.wfp-sub-msg--success { border: 1px solid ${isDark ? darkTokens.surface.divider : "#c8e6c9"}; background: ${isDark ? "rgba(105,207,126,0.08)" : "#f1f8f2"}; color: ${isDark ? darkTokens.text.secondary : "#2e7d32"}; }
.wfp-sub-msg--error { border: 1px solid ${isDark ? darkTokens.surface.divider : "#ffcdd2"}; background: ${isDark ? "rgba(255,107,107,0.08)" : "#fdecea"}; color: ${isDark ? darkTokens.danger : "#c62828"}; }
.wfp-sub-msg--error .wfp-sub-btn { margin-left: 10px; }
.wfp-sub-actions { display: flex; gap: 6px; flex-wrap: wrap; }
.wfp-sub-overlay { position: fixed; inset: 0; background: rgba(0, 0, 0, 0.45); display: flex; align-items: center; justify-content: center; z-index: 1000; }
.wfp-sub-modal { width: min(520px, calc(100vw - 32px)); box-sizing: border-box; padding: 20px 24px; border-radius: 10px; background: ${isDark ? darkTokens.surface.bg : "#ffffff"}; border: 1px solid ${isDark ? darkTokens.surface.border : "#e0e0e0"}; box-shadow: 0 12px 40px rgba(0, 0, 0, 0.25); }
.wfp-sub-modal h3 { margin: 0 0 10px; font-size: 15px; font-weight: 600; color: ${isDark ? darkTokens.text.primary : "#1a1a1a"}; }
.wfp-sub-modal p { margin: 6px 0; font-size: 12px; color: ${isDark ? darkTokens.text.secondary : "#616161"}; }
.wfp-sub-secret { margin: 10px 0; padding: 10px 12px; border-radius: 6px; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; word-break: break-all; background: ${isDark ? darkTokens.surface.bgHover : "#f5f5f5"}; border: 1px dashed ${isDark ? darkTokens.surface.borderStrong : "#bdbdbd"}; color: ${isDark ? darkTokens.text.primary : "#1a1a1a"}; }
.wfp-sub-warn { color: ${isDark ? darkTokens.danger : "#c62828"}; font-weight: 500; }
.wfp-sub-modal-actions { display: flex; gap: 8px; margin-top: 12px; }
      `}</style>
      <div className="wfp-sub-header">
        <h2 className="wfp-sub-title">订阅管理</h2>
        <span className="wfp-sub-count">订阅 {subs.length} 项</span>
        <span className="wfp-sub-count">死信 {deadLetters.length} 条</span>
        <span className="wfp-sub-hint">{loading ? '刷新中…' : '事件出口 Webhook 订阅'}</span>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
          <button className="wfp-sub-btn" onClick={openCreateForm}>
            新建订阅
          </button>
          <button
            className="wfp-sub-btn"
            onClick={() => {
              void loadData();
            }}
            disabled={loading}
          >
            刷新
          </button>
        </div>
      </div>

      {error !== null && (
        <div className="wfp-sub-msg wfp-sub-msg--error">
          数据加载失败：{error}
          <button
            className="wfp-sub-btn"
            onClick={() => {
              void loadData();
            }}
          >
            重试
          </button>
        </div>
      )}
      {actionMessage !== null && (
        <div className="wfp-sub-msg wfp-sub-msg--success">{actionMessage}</div>
      )}
      {actionError !== null && (
        <div className="wfp-sub-msg wfp-sub-msg--error">操作失败：{actionError}</div>
      )}

      <div className="wfp-sub-section">
        <div className="wfp-sub-section-header">
          <h3 className="wfp-sub-section-title">订阅列表</h3>
        </div>

        {showForm && (
          <div className="wfp-sub-form">
            <div className="wfp-sub-form-grid">
              <div className="wfp-sub-field">
                <label>订阅标识 *</label>
                <input
                  className="wfp-sub-input"
                  value={form.subscription_id}
                  onChange={(e) => updateForm({ subscription_id: e.target.value })}
                  placeholder="订阅标识"
                />
              </div>
              <div className="wfp-sub-field">
                <label>租户标识 *</label>
                <input
                  className="wfp-sub-input"
                  value={form.tenant_id}
                  onChange={(e) => updateForm({ tenant_id: e.target.value })}
                  placeholder="租户标识"
                />
              </div>
              <div className="wfp-sub-field">
                <label>回调地址 *（http/https）</label>
                <input
                  className="wfp-sub-input"
                  value={form.url}
                  onChange={(e) => updateForm({ url: e.target.value })}
                  placeholder="https://example.com/webhook"
                />
              </div>
              <div className="wfp-sub-field">
                <label>签名密钥 *</label>
                <input
                  className="wfp-sub-input"
                  value={form.secret}
                  onChange={(e) => updateForm({ secret: e.target.value })}
                  placeholder="HMAC-SHA256 密钥"
                />
              </div>
              <div className="wfp-sub-field">
                <label>事件类型（逗号分隔，空=全部，以 . 结尾=前缀通配）</label>
                <input
                  className="wfp-sub-input"
                  value={form.event_types}
                  onChange={(e) => updateForm({ event_types: e.target.value })}
                  placeholder="instance.completed, node."
                />
              </div>
              <div className="wfp-sub-field">
                <label>定义钉住（可选，空=匹配全部）</label>
                <input
                  className="wfp-sub-input"
                  value={form.definition_id}
                  onChange={(e) => updateForm({ definition_id: e.target.value })}
                  placeholder="定义标识"
                />
              </div>
              <div className="wfp-sub-field">
                <label>版本钉住（可选，需同时钉定义；≥1）</label>
                <input
                  className="wfp-sub-input"
                  value={form.definition_version}
                  onChange={(e) => updateForm({ definition_version: e.target.value })}
                  placeholder="如 2"
                  inputMode="numeric"
                />
              </div>
              <div className="wfp-sub-field">
                <label>描述</label>
                <input
                  className="wfp-sub-input"
                  value={form.description}
                  onChange={(e) => updateForm({ description: e.target.value })}
                  placeholder="订阅描述"
                />
              </div>
              <div className="wfp-sub-field">
                <label className="wfp-sub-check">
                  <input
                    type="checkbox"
                    checked={form.enabled}
                    onChange={(e) => updateForm({ enabled: e.target.checked })}
                  />
                  创建后立即启用
                </label>
              </div>
            </div>
            {formError !== null && (
              <div className="wfp-sub-msg wfp-sub-msg--error" style={{ marginTop: 10, marginBottom: 0 }}>
                {formError}
              </div>
            )}
            <div className="wfp-sub-form-actions">
              <button className="wfp-sub-btn" onClick={handleUpsert} disabled={busy}>
                保存订阅
              </button>
              <button
                className="wfp-sub-btn"
                onClick={() => {
                  setShowForm(false);
                  setFormError(null);
                }}
              >
                取消
              </button>
            </div>
          </div>
        )}

        <div className="wfp-sub-tablewrap">
          <table className="wfp-sub-table" data-testid="subscription-table">
            <thead>
              <tr>
                <th>订阅</th>
                <th>回调地址</th>
                <th>事件类型</th>
                <th>钉住</th>
                <th>状态</th>
                <th>密钥版本</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {subs.length === 0 ? (
                <tr>
                  <td colSpan={7} className="wfp-sub-empty">
                    暂无订阅
                  </td>
                </tr>
              ) : (
                subs.map((sub) => (
                  <tr key={sub.subscription_id}>
                    <td>
                      <div className="wfp-sub-mono">{sub.subscription_id}</div>
                      <div className="wfp-sub-sub">
                        {sub.tenant_id}
                        {sub.description !== '' ? ` · ${sub.description}` : ''}
                      </div>
                    </td>
                    <td>
                      <span className="wfp-sub-trunc" title={sub.url}>
                        {sub.url}
                      </span>
                    </td>
                    <td>
                      {sub.event_types.length === 0 ? (
                        <span className="wfp-sub-muted">全部</span>
                      ) : (
                        sub.event_types.join(', ')
                      )}
                    </td>
                    <td>
                      {sub.definition_id === null ? (
                        <span className="wfp-sub-muted">全部版本</span>
                      ) : (
                        `${sub.definition_id}${sub.definition_version !== null ? ` · v${sub.definition_version}` : ''}`
                      )}
                    </td>
                    <td>
                      <span
                        className={sub.enabled ? 'wfp-sub-badge wfp-sub-badge--on' : 'wfp-sub-badge wfp-sub-badge--off'}
                      >
                        {sub.enabled ? '启用' : '停用'}
                      </span>
                    </td>
                    <td>
                      <div>v{sub.secret_version}</div>
                      {sub.rotated_at !== null && (
                        <div className="wfp-sub-sub">轮换于 {formatDateTime(sub.rotated_at)}</div>
                      )}
                    </td>
                    <td>
                      <div className="wfp-sub-actions">
                        <button
                          className="wfp-sub-btn wfp-sub-btn--sm"
                          onClick={() => handleToggleDeliveries(sub.subscription_id)}
                        >
                          投递日志
                        </button>
                        <button
                          className="wfp-sub-btn wfp-sub-btn--sm"
                          onClick={() => handleRotate(sub.subscription_id)}
                          disabled={busy}
                        >
                          轮换密钥
                        </button>
                        <button
                          className="wfp-sub-btn wfp-sub-btn--sm"
                          onClick={() => handleToggleEnabled(sub)}
                          disabled={busy}
                        >
                          {sub.enabled ? '停用' : '启用'}
                        </button>
                        <button
                          className="wfp-sub-btn wfp-sub-btn--sm"
                          onClick={() => openEditForm(sub)}
                        >
                          编辑
                        </button>
                        <button
                          className="wfp-sub-btn wfp-sub-btn--sm wfp-sub-btn--danger"
                          onClick={() => handleDelete(sub.subscription_id)}
                          disabled={busy}
                        >
                          删除
                        </button>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {deliveriesFor !== null && (
          <div className="wfp-sub-section" style={{ marginTop: 12 }}>
            <div className="wfp-sub-section-header">
              <h3 className="wfp-sub-section-title">投递记录：{deliveriesFor}</h3>
              <button
                className="wfp-sub-btn wfp-sub-btn--sm"
                onClick={() => {
                  setDeliveriesFor(null);
                  setDeliveries([]);
                }}
              >
                收起
              </button>
            </div>
            <div className="wfp-sub-tablewrap">
              <table className="wfp-sub-table wfp-sub-table--compact" data-testid="deliveries-table">
                <thead>
                  <tr>
                    <th>事件类型</th>
                    <th>实例</th>
                    <th>尝试次数</th>
                    <th>终态</th>
                    <th>投递时间</th>
                    <th>最后错误</th>
                  </tr>
                </thead>
                <tbody>
                  {deliveries.length === 0 ? (
                    <tr>
                      <td colSpan={6} className="wfp-sub-empty">
                        暂无投递记录
                      </td>
                    </tr>
                  ) : (
                    deliveries.map((record) => (
                      <tr key={record.event_id}>
                        <td>{record.event_type}</td>
                        <td className="wfp-sub-mono">{record.instance_id}</td>
                        <td>{record.attempts}</td>
                        <td>
                          <span
                            className={
                              record.last_status === 'delivered'
                                ? 'wfp-sub-badge wfp-sub-badge--ok'
                                : 'wfp-sub-badge wfp-sub-badge--fail'
                            }
                          >
                            {record.last_status === 'delivered' ? '已投递' : '失败'}
                          </span>
                        </td>
                        <td>{formatDateTime(record.delivered_at)}</td>
                        <td>
                          {record.last_error === '' ? (
                            <span className="wfp-sub-muted">—</span>
                          ) : (
                            <span className="wfp-sub-trunc" title={record.last_error}>
                              {record.last_error}
                            </span>
                          )}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>

      <div className="wfp-sub-section">
        <div className="wfp-sub-section-header">
          <h3 className="wfp-sub-section-title">死信队列</h3>
          <span className="wfp-sub-count">{deadLetters.length} 条</span>
        </div>
        <div className="wfp-sub-tablewrap">
          <table className="wfp-sub-table wfp-sub-table--compact" data-testid="deadletter-table">
            <thead>
              <tr>
                <th>条目</th>
                <th>订阅</th>
                <th>事件类型</th>
                <th>实例</th>
                <th>尝试次数</th>
                <th>入队时间</th>
                <th>最后错误</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {deadLetters.length === 0 ? (
                <tr>
                  <td colSpan={8} className="wfp-sub-empty">
                    暂无死信条目
                  </td>
                </tr>
              ) : (
                deadLetters.map((entry) => (
                  <tr key={entry.entry_id}>
                    <td className="wfp-sub-mono">{entry.entry_id}</td>
                    <td className="wfp-sub-mono">{entry.subscription_id}</td>
                    <td>{entry.event_type}</td>
                    <td className="wfp-sub-mono">{entry.instance_id}</td>
                    <td>{entry.attempts}</td>
                    <td>{formatDateTime(entry.failed_at)}</td>
                    <td>
                      <span className="wfp-sub-trunc" title={entry.last_error}>
                        {entry.last_error}
                      </span>
                    </td>
                    <td>
                      <button
                        className="wfp-sub-btn wfp-sub-btn--sm"
                        onClick={() => handleRedrive(entry.entry_id)}
                        disabled={busy}
                      >
                        重投
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {rotateResult !== null && (
        <div
          className="wfp-sub-overlay"
          onClick={() => {
            closeRotate();
          }}
        >
          <div
            className="wfp-sub-modal"
            data-testid="rotate-modal"
            onClick={(e) => {
              e.stopPropagation();
            }}
          >
            <h3>密钥轮换成功</h3>
            <p>
              订阅 <b>{rotateResult.subscription_id}</b> 的新密钥（v{rotateResult.secret_version}
              ，轮换于 {formatDateTime(rotateResult.rotated_at)}）：
            </p>
            <div className="wfp-sub-secret" data-testid="rotated-secret">
              {rotateResult.secret}
            </div>
            <p className="wfp-sub-warn">该密钥仅本次展示，关闭后无法再次查看，请立即复制保存。</p>
            <div className="wfp-sub-modal-actions">
              <button className="wfp-sub-btn" onClick={() => void copySecret()}>
                {secretCopied ? '已复制' : '复制密钥'}
              </button>
              <button className="wfp-sub-btn" onClick={closeRotate}>
                关闭
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
