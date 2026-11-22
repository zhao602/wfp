// =============================================================================
// 沙箱策略管理面板（web/src/sandbox/SandboxPolicyPanel.tsx）
// =============================================================================

import { useEffect, useState, useCallback } from 'react';
import type {
  PolicyResponse,
  ViolationRecord,
  SandboxStatus,
  CreatePolicyRequest,
  ViolationType,
} from './sandboxTypes';
import {
  createPolicy,
  listPolicies,
  deletePolicy,
  listViolations,
  getSandboxStatus,
} from './sandboxApiClient';
import { useThemeMode, darkTokens } from '../theme/themeMode';

/** 沙箱策略管理面板 */
export function SandboxPolicyPanel({ tenantId }: { tenantId: string }): JSX.Element {
  const [policies, setPolicies] = useState<PolicyResponse[]>([]);
  const [violations, setViolations] = useState<ViolationRecord[]>([]);
  const [status, setStatus] = useState<SandboxStatus | null>(null);
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [violationFilter, setViolationFilter] = useState<ViolationType | ''>('');
  const themeMode = useThemeMode();
  const isDark = themeMode === 'dark';

  const loadData = useCallback(async () => {
    try {
      const [p, v, s] = await Promise.all([
        listPolicies(tenantId),
        listViolations(tenantId, violationFilter || undefined),
        getSandboxStatus(),
      ]);
      setPolicies(p);
      setViolations(v);
      setStatus(s);
    } catch (err) {
      console.error('加载沙箱数据失败:', err);
    }
  }, [tenantId, violationFilter]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const handleCreate = async (request: CreatePolicyRequest) => {
    await createPolicy(request);
    setShowCreateForm(false);
    loadData();
  };

  const handleDelete = async (policyId: string) => {
    await deletePolicy(policyId, tenantId);
    loadData();
  };

  if (status && !status.sandbox_enabled) {
    return <div className="wfp-sbx-disabled">沙箱功能未启用</div>;
  }

  return (
    <div className="wfp-sbx-panel">
      <style>{`
.wfp-sbx-input { width: 100%; box-sizing: border-box; padding: 7px 10px; font-size: 13px; font-weight: 500; font-family: inherit; color: ${isDark ? darkTokens.text.primary : "#333333"}; background-color: ${isDark ? darkTokens.surface.bg : "#ffffff"}; border: 1px solid ${isDark ? darkTokens.surface.divider : "#e0e0e0"}; border-radius: 5px; outline: none; transition: background-color 120ms ease, border-color 120ms ease; }
.wfp-sbx-input:hover { background-color: ${isDark ? darkTokens.surface.bgHover : "#fafafa"}; }
.wfp-sbx-input:focus { border-color: ${isDark ? darkTokens.surface.borderStrong : "#bdbdbd"}; }
.wfp-sbx-input::placeholder { color: ${isDark ? darkTokens.text.hint : "#b0b0b0"}; }
.wfp-sbx-table { width: 100%; border-collapse: separate; border-spacing: 0; font-size: 12px; border: 1px solid ${isDark ? darkTokens.surface.divider : "#e0e0e0"}; border-radius: 8px; overflow: hidden; }
.wfp-sbx-table thead th { background: ${isDark ? darkTokens.surface.bgHover : "#f5f5f5"}; font-weight: 600; text-align: left; padding: 8px 12px; border-bottom: 1px solid ${isDark ? darkTokens.surface.divider : "#e0e0e0"}; color: ${isDark ? darkTokens.text.primary : "#1a1a1a"}; }
.wfp-sbx-table tbody td { padding: 7px 12px; border-bottom: 1px solid ${isDark ? darkTokens.surface.divider : "#eee"}; color: ${isDark ? darkTokens.text.primary : "#1a1a1a"}; }
.wfp-sbx-table tbody tr:last-child td { border-bottom: none; }
.wfp-sbx-table tbody tr:hover { background: ${isDark ? darkTokens.surface.bgHover : "#fafafa"}; }
.wfp-sbx-btn { font-size: 13px; font-weight: 500; font-family: inherit; padding: 6px 14px; border-radius: 6px; cursor: pointer; outline: none; border: 1px solid ${isDark ? darkTokens.surface.border : "#d0d0d0"}; background: ${isDark ? darkTokens.surface.bg : "#ffffff"}; color: ${isDark ? darkTokens.text.primary : "#1a1a1a"}; transition: background-color 120ms ease, border-color 120ms ease; }
.wfp-sbx-btn:hover { background: ${isDark ? darkTokens.surface.bgHover : "#f5f5f5"}; border-color: ${isDark ? darkTokens.surface.borderStrong : "#b8b8b8"}; }
.wfp-sbx-btn:active { background: ${isDark ? darkTokens.surface.bgActive : "#e8e8e8"}; }
.wfp-sbx-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.wfp-sbx-btn--primary { background: ${isDark ? darkTokens.text.primary : "#1a1a1a"}; color: ${isDark ? darkTokens.surface.bg : "#ffffff"}; border-color: ${isDark ? darkTokens.text.primary : "#1a1a1a"}; }
.wfp-sbx-btn--primary:hover { background: ${isDark ? darkTokens.text.secondary : "#333333"}; }
.wfp-sbx-btn--danger { color: ${isDark ? darkTokens.danger : "#d32f2f"}; background: transparent; border-color: ${isDark ? darkTokens.danger : "#d32f2f"}; }
.wfp-sbx-btn--danger:hover { background: ${isDark ? "rgba(255,107,107,0.12)" : "#fdecea"}; }
.wfp-sbx-btn--sm { padding: 4px 10px; border-radius: 4px; font-size: 12px; }
.wfp-sbx-select { width: 100%; box-sizing: border-box; padding: 7px 26px 7px 10px; font-size: 13px; font-weight: 500; font-family: inherit; color: ${isDark ? darkTokens.text.primary : "#333333"}; background-color: ${isDark ? darkTokens.surface.bg : "#ffffff"}; background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='8' height='5' viewBox='0 0 8 5'%3E%3Cpath d='M0 0 L4 5 L8 0 Z' fill='${isDark ? "%238a8a8a" : "%23757575"}'/%3E%3C/svg%3E"); background-repeat: no-repeat; background-position: right 9px center; border: 1px solid ${isDark ? darkTokens.surface.divider : "#e0e0e0"}; border-radius: 5px; cursor: pointer; outline: none; -webkit-appearance: none; -moz-appearance: none; appearance: none; color-scheme: ${isDark ? "dark" : "light"}; transition: background-color 120ms ease, border-color 120ms ease; }
.wfp-sbx-select:hover { background-color: ${isDark ? darkTokens.surface.bgHover : "#fafafa"}; }
.wfp-sbx-select:focus { border-color: ${isDark ? darkTokens.surface.borderStrong : "#bdbdbd"}; }

.wfp-sbx-panel { padding: 20px 24px; background: ${isDark ? darkTokens.surface.bg : "#ffffff"}; border: 1px solid ${isDark ? darkTokens.surface.divider : "#e0e0e0"}; border-radius: 8px; }
.wfp-sbx-title { font-size: 15px; font-weight: 600; margin: 0 0 16px; padding-bottom: 12px; border-bottom: 1px solid ${isDark ? darkTokens.surface.divider : "#e0e0e0"}; color: ${isDark ? darkTokens.text.primary : "#1a1a1a"}; }
.wfp-sbx-disabled { text-align: center; padding: 48px 16px; font-size: 14px; color: ${isDark ? darkTokens.text.hint : "#9e9e9e"}; }
.wfp-sbx-warning { margin-bottom: 16px; padding: 12px 16px; border-radius: 8px; border: 1px solid ${isDark ? darkTokens.surface.divider : "#e0e0e0"}; border-left: 3px solid ${isDark ? "#ffa726" : "#ff9800"}; background: ${isDark ? "rgba(255,167,38,0.08)" : "#fff8e1"}; font-size: 12px; color: ${isDark ? darkTokens.text.secondary : "#616161"}; }
.wfp-sbx-warning > div { padding: 2px 0; }
.wfp-sbx-section { margin-bottom: 20px; }
.wfp-sbx-section:last-child { margin-bottom: 0; }
.wfp-sbx-section-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.wfp-sbx-section-title { font-size: 14px; font-weight: 600; margin: 0; color: ${isDark ? darkTokens.text.primary : "#1a1a1a"}; }
.wfp-sbx-section-header .wfp-sbx-select { width: auto; max-width: 200px; }
.wfp-sbx-form { margin-bottom: 16px; padding: 16px; background: ${isDark ? darkTokens.surface.bgHover : "#fafafa"}; border: 1px solid ${isDark ? darkTokens.surface.divider : "#e0e0e0"}; border-radius: 8px; }
.wfp-sbx-form .wfp-sbx-input, .wfp-sbx-form .wfp-sbx-select { margin-bottom: 10px; }
.wfp-sbx-form .wfp-sbx-btn { margin-top: 4px; }
.wfp-sbx-violation { display: flex; align-items: center; gap: 12px; padding: 8px 12px; border-bottom: 1px solid ${isDark ? darkTokens.surface.divider : "#f0f0f0"}; font-size: 12px; }
.wfp-sbx-violation:last-child { border-bottom: none; }
.wfp-sbx-violation-type { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 500; color: #ffffff; background: ${isDark ? darkTokens.danger : "#d32f2f"}; white-space: nowrap; }
.wfp-sbx-violation-detail { flex: 1; color: ${isDark ? darkTokens.text.secondary : "#616161"}; }
.wfp-sbx-violation-time { color: ${isDark ? darkTokens.text.hint : "#9e9e9e"}; font-size: 11px; white-space: nowrap; }
.wfp-sbx-empty { text-align: center; padding: 24px 16px; color: ${isDark ? darkTokens.text.hint : "#9e9e9e"}; font-size: 13px; }
      `}</style>
      <h2 className="wfp-sbx-title">沙箱策略管理</h2>

      {status?.degraded.sandbox_degraded && (
        <div className="wfp-sbx-warning">
          {status.degraded.message}
          {status.degraded.degraded_components.map((c, i) => (
            <div key={i}>{c.component}: {c.reason} → {c.mode}</div>
          ))}
        </div>
      )}

      <div className="wfp-sbx-section">
        <div className="wfp-sbx-section-header">
          <h3 className="wfp-sbx-section-title">策略列表</h3>
          <button className="wfp-sbx-btn" onClick={() => setShowCreateForm(!showCreateForm)}>
            {showCreateForm ? '取消' : '新建策略'}
          </button>
        </div>

        {showCreateForm && (
          <CreatePolicyForm tenantId={tenantId} onSubmit={handleCreate} />
        )}

        <table className="wfp-sbx-table">
          <thead>
            <tr>
              <th>策略ID</th>
              <th>名称</th>
              <th>隔离模式</th>
              <th>状态</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {policies.length === 0 ? (
              <tr><td colSpan={5} className="wfp-sbx-empty">暂无策略记录</td></tr>
            ) : policies.map((p) => (
              <tr key={p.policy_id}>
                <td>{p.policy_id}</td>
                <td>{p.name}</td>
                <td>{p.isolation_mode}</td>
                <td>{p.is_active ? '启用' : '停用'}</td>
                <td>
                  <button className="wfp-sbx-btn wfp-sbx-btn--danger wfp-sbx-btn--sm" onClick={() => handleDelete(p.policy_id)}>删除</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="wfp-sbx-section">
        <div className="wfp-sbx-section-header">
          <h3 className="wfp-sbx-section-title">违规记录</h3>
          <select
            className="wfp-sbx-select"
            value={violationFilter}
            onChange={(e) => setViolationFilter(e.target.value as ViolationType | '')}
          >
            <option value="">全部</option>
            <option value="file_escape">路径穿越</option>
            <option value="network_denied">网络拒绝</option>
            <option value="resource_exceeded">资源超限</option>
            <option value="timeout">超时</option>
          </select>
        </div>

        <div className="wfp-sbx-timeline">
          {violations.length === 0 ? (
            <div className="wfp-sbx-empty">暂无违规记录</div>
          ) : violations.map((v) => (
            <div key={v.record_id} className="wfp-sbx-violation">
              <span className="wfp-sbx-violation-type">{v.violation_type}</span>
              <span className="wfp-sbx-violation-detail">{v.detail}</span>
              <span className="wfp-sbx-violation-time">{v.occurred_at}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/** 创建策略表单 */
function CreatePolicyForm({
  tenantId,
  onSubmit,
}: {
  tenantId: string;
  onSubmit: (request: CreatePolicyRequest) => Promise<void>;
}): JSX.Element {
  const [form, setForm] = useState<CreatePolicyRequest>({
    tenant_id: tenantId,
    name: '',
    description: '',
    isolation_mode: 'logical',
    allow_degrade: true,
    cpu_seconds_limit: 10.0,
    memory_mb_limit: 512,
    output_bytes_limit: 1048576,
    execution_timeout_seconds: 60,
    workdir: '',
    network_policy: 'default_deny',
    network_whitelist: [],
    allowed_path_prefixes: [],
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onSubmit(form);
  };

  return (
    <form onSubmit={handleSubmit} className="wfp-sbx-form">
      <input
        className="wfp-sbx-input"
        placeholder="策略名称"
        value={form.name}
        onChange={(e) => setForm({ ...form, name: e.target.value })}
      />
      <input
        className="wfp-sbx-input"
        placeholder="工作目录（绝对路径）"
        value={form.workdir}
        onChange={(e) => setForm({ ...form, workdir: e.target.value })}
        required
      />
      <select
        className="wfp-sbx-select"
        value={form.isolation_mode}
        onChange={(e) => setForm({ ...form, isolation_mode: e.target.value as 'logical' | 'physical' })}
      >
        <option value="logical">逻辑隔离</option>
        <option value="physical">物理隔离</option>
      </select>
      <select
        className="wfp-sbx-select"
        value={form.network_policy}
        onChange={(e) => setForm({ ...form, network_policy: e.target.value as 'default_deny' | 'whitelist' })}
      >
        <option value="default_deny">默认拒绝</option>
        <option value="whitelist">白名单</option>
      </select>
      <input
        className="wfp-sbx-input"
        type="number"
        placeholder="CPU 时间上限（秒）"
        value={form.cpu_seconds_limit}
        onChange={(e) => setForm({ ...form, cpu_seconds_limit: Number(e.target.value) })}
      />
      <input
        className="wfp-sbx-input"
        type="number"
        placeholder="内存上限（MB）"
        value={form.memory_mb_limit}
        onChange={(e) => setForm({ ...form, memory_mb_limit: Number(e.target.value) })}
      />
      <input
        className="wfp-sbx-input"
        type="number"
        placeholder="执行超时（秒）"
        value={form.execution_timeout_seconds}
        onChange={(e) => setForm({ ...form, execution_timeout_seconds: Number(e.target.value) })}
      />
      <button className="wfp-sbx-btn wfp-sbx-btn--primary" type="submit">保存</button>
    </form>
  );
}
