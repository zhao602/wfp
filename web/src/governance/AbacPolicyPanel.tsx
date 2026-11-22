// =============================================================================
// ABAC 策略管理面板（AbacPolicyPanel.tsx）
//
// 职责：提供 ABAC 策略的创建、列表展示与删除功能。
// =============================================================================

import { useState, useEffect, useCallback } from 'react';
import type {
  AbacCondition,
  AttributeSource,
  ConditionOperator,
  FailMode,
  PolicyEffect,
  PolicyResponse,
} from './abacTypes';
import { createPolicy, listPolicies, deletePolicy } from './abacApiClient';
import { useThemeMode, darkTokens } from '../theme/themeMode';

interface AbacPolicyPanelProps {
  token: string;
  tenantId: string;
}

export function AbacPolicyPanel({
  token,
  tenantId,
}: AbacPolicyPanelProps): JSX.Element {
  const [policies, setPolicies] = useState<PolicyResponse[]>([]);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  // 策略创建表单状态
  const [policyId, setPolicyId] = useState<string>('');
  const [effect, setEffect] = useState<PolicyEffect>('allow');
  const [failMode, setFailMode] = useState<FailMode>('deny');
  const [priority, setPriority] = useState<number>(0);
  const [description, setDescription] = useState<string>('');
  const [conditions, setConditions] = useState<AbacCondition[]>([
    { attribute_name: '', attribute_source: 'subject', operator: 'eq', expected_value: '' },
  ]);
  const themeMode = useThemeMode();
  const isDark = themeMode === 'dark';

  const refreshPolicies = useCallback(async (): Promise<void> => {
    setLoading(true);
    setError(null);
    try {
      const result = await listPolicies(tenantId, token);
      setPolicies(result);
    } catch (err) {
      setPolicies([]);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [tenantId, token]);

  useEffect((): void => {
    refreshPolicies();
  }, [refreshPolicies]);

  const handleCreate = useCallback(async (): Promise<void> => {
    setError(null);
    try {
      await createPolicy(
        {
          policy_id: policyId,
          tenant_id: tenantId,
          conditions,
          effect,
          fail_mode: failMode,
          priority,
          description,
        },
        token,
      );
      await refreshPolicies();
      setPolicyId('');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [policyId, tenantId, conditions, effect, failMode, priority, description, token, refreshPolicies]);

  const handleDelete = useCallback(
    async (pid: string): Promise<void> => {
      setError(null);
      try {
        await deletePolicy(pid, tenantId, token);
        await refreshPolicies();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    },
    [tenantId, token, refreshPolicies],
  );

  return (
    <div className="wfp-abac-panel">
      <style>{`
.wfp-abac-panel{padding:20px 24px;background:${isDark ? darkTokens.surface.bg : '#ffffff'};border:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};border-radius:8px;}
.wfp-abac-title{font-size:15px;font-weight:600;margin:0 0 16px;padding-bottom:12px;border-bottom:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};color:${isDark ? darkTokens.text.primary : '#1a1a1a'};}
.wfp-abac-error{margin-bottom:16px;padding:12px 16px;border-radius:8px;background:${isDark ? darkTokens.surface.bgHover : '#fafafa'};border:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};border-left:3px solid ${isDark ? darkTokens.danger : '#d32f2f'};font-size:13px;color:${isDark ? darkTokens.danger : '#d32f2f'};line-height:1.5;}
.wfp-abac-loading{margin-bottom:16px;padding:12px 16px;border-radius:8px;background:${isDark ? darkTokens.surface.bgHover : '#fafafa'};border:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};font-size:13px;color:${isDark ? darkTokens.text.hint : '#9e9e9e'};}
.wfp-abac-empty{text-align:center;padding:32px 16px;color:${isDark ? darkTokens.text.hint : '#9e9e9e'};font-size:13px;}

.wfp-abac-form{margin-bottom:20px;padding:16px;background:${isDark ? darkTokens.surface.bgHover : '#fafafa'};border:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};border-radius:8px;}
.wfp-abac-section-title{font-size:14px;font-weight:600;margin:0 0 12px;color:${isDark ? darkTokens.text.primary : '#1a1a1a'};}
.wfp-abac-subsection-title{font-size:13px;font-weight:600;margin:0 0 10px;color:${isDark ? darkTokens.text.secondary : '#616161'};}

.wfp-abac-field-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;}
.wfp-abac-field{display:flex;flex-direction:column;gap:4px;}
.wfp-abac-field-label{font-size:11px;font-weight:500;color:${isDark ? darkTokens.text.secondary : '#616161'};text-transform:uppercase;letter-spacing:0.5px;}
.wfp-abac-field--full{grid-column:1 / -1;}

.wfp-abac-conditions{margin-top:14px;padding-top:14px;border-top:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};}
.wfp-abac-condition-row{display:grid;grid-template-columns:1.2fr 1fr 1fr 1.2fr;gap:10px;align-items:start;}
.wfp-abac-condition-row .wfp-abac-field{gap:3px;}

.wfp-abac-list{margin-top:4px;}
.wfp-abac-list .wfp-abac-section-title{margin-bottom:10px;}

.wfp-abac-input{width:100%;box-sizing:border-box;padding:7px 10px;font-size:13px;font-weight:500;font-family:inherit;color:${isDark ? darkTokens.text.primary : '#333333'};background-color:${isDark ? darkTokens.surface.bg : '#ffffff'};border:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};border-radius:5px;outline:none;transition:background-color 120ms ease,border-color 120ms ease;}
.wfp-abac-input:hover{background-color:${isDark ? darkTokens.surface.bgHover : '#fafafa'};}
.wfp-abac-input:focus{border-color:${isDark ? darkTokens.surface.borderStrong : '#bdbdbd'};}
.wfp-abac-input::placeholder{color:${isDark ? darkTokens.text.hint : '#b0b0b0'};}

.wfp-abac-select{width:100%;box-sizing:border-box;padding:7px 26px 7px 10px;font-size:13px;font-weight:500;font-family:inherit;color:${isDark ? darkTokens.text.primary : '#333333'};background-color:${isDark ? darkTokens.surface.bg : '#ffffff'};background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='8' height='5' viewBox='0 0 8 5'%3E%3Cpath d='M0 0 L4 5 L8 0 Z' fill='${isDark ? '%238a8a8a' : '%23757575'}'/%3E%3C/svg%3E");background-repeat:no-repeat;background-position:right 9px center;border:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};border-radius:5px;cursor:pointer;outline:none;-webkit-appearance:none;-moz-appearance:none;appearance:none;color-scheme:${isDark ? 'dark' : 'light'};transition:background-color 120ms ease,border-color 120ms ease;}
.wfp-abac-select:hover{background-color:${isDark ? darkTokens.surface.bgHover : '#fafafa'};}
.wfp-abac-select:focus{border-color:${isDark ? darkTokens.surface.borderStrong : '#bdbdbd'};}

.wfp-abac-table{width:100%;border-collapse:separate;border-spacing:0;font-size:12px;border:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};border-radius:8px;overflow:hidden;}
.wfp-abac-table thead th{background:${isDark ? darkTokens.surface.bgHover : '#f5f5f5'};font-weight:600;text-align:left;padding:8px 12px;border-bottom:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};color:${isDark ? darkTokens.text.primary : '#1a1a1a'};}
.wfp-abac-table tbody td{padding:7px 12px;border-bottom:1px solid ${isDark ? darkTokens.surface.divider : '#eee'};color:${isDark ? darkTokens.text.primary : '#1a1a1a'};}
.wfp-abac-table tbody tr:last-child td{border-bottom:none;}
.wfp-abac-table tbody tr:hover{background:${isDark ? darkTokens.surface.bgHover : '#fafafa'};}

.wfp-abac-btn{font-size:13px;font-weight:500;font-family:inherit;padding:6px 14px;border-radius:6px;cursor:pointer;outline:none;border:1px solid ${isDark ? darkTokens.surface.border : '#d0d0d0'};background:${isDark ? darkTokens.surface.bg : '#ffffff'};color:${isDark ? darkTokens.text.primary : '#1a1a1a'};transition:background-color 120ms ease,border-color 120ms ease;}
.wfp-abac-btn:hover{background:${isDark ? darkTokens.surface.bgHover : '#f5f5f5'};border-color:${isDark ? darkTokens.surface.borderStrong : '#b8b8b8'};}
.wfp-abac-btn:active{background:${isDark ? darkTokens.surface.bgActive : '#e8e8e8'};}
.wfp-abac-btn:disabled{opacity:0.5;cursor:not-allowed;}
.wfp-abac-btn--primary{background:${isDark ? darkTokens.text.primary : '#1a1a1a'};color:${isDark ? darkTokens.surface.bg : '#ffffff'};border-color:${isDark ? darkTokens.text.primary : '#1a1a1a'};}
.wfp-abac-btn--primary:hover{background:${isDark ? darkTokens.text.secondary : '#333333'};}
.wfp-abac-btn--primary:disabled:hover{background:${isDark ? darkTokens.text.primary : '#1a1a1a'};border-color:${isDark ? darkTokens.text.primary : '#1a1a1a'};}
.wfp-abac-btn--danger{color:${isDark ? darkTokens.danger : '#d32f2f'};background:transparent;border-color:${isDark ? darkTokens.danger : '#d32f2f'};}
.wfp-abac-btn--danger:hover{background:${isDark ? 'rgba(255,107,107,0.12)' : '#fdecea'};}
.wfp-abac-btn--sm{padding:4px 10px;border-radius:4px;font-size:12px;}
      `}</style>
      <h2 className="wfp-abac-title">ABAC 策略管理</h2>

      {error && <div className="wfp-abac-error">{error}</div>}
      {loading && <div className="wfp-abac-loading">加载中...</div>}

      {/* 策略创建表单 */}
      <div className="wfp-abac-form">
        <h3 className="wfp-abac-section-title">创建策略</h3>
        <div className="wfp-abac-field-grid">
          <div className="wfp-abac-field">
            <span className="wfp-abac-field-label">策略标识</span>
            <input
              className="wfp-abac-input"
              placeholder="policy-id"
              value={policyId}
              onChange={(e): void => setPolicyId(e.target.value)}
            />
          </div>
          <div className="wfp-abac-field">
            <span className="wfp-abac-field-label">优先级</span>
            <input
              className="wfp-abac-input"
              type="number"
              value={priority}
              onChange={(e): void => setPriority(Number(e.target.value))}
            />
          </div>
          <div className="wfp-abac-field">
            <span className="wfp-abac-field-label">效果</span>
            <select
              className="wfp-abac-select"
              value={effect}
              onChange={(e): void => setEffect(e.target.value as PolicyEffect)}
            >
              <option value="allow">允许</option>
              <option value="deny">拒绝</option>
            </select>
          </div>
          <div className="wfp-abac-field">
            <span className="wfp-abac-field-label">失败模式</span>
            <select
              className="wfp-abac-select"
              value={failMode}
              onChange={(e): void => setFailMode(e.target.value as FailMode)}
            >
              <option value="deny">失败时拒绝</option>
              <option value="allow">失败时放行</option>
            </select>
          </div>
          <div className="wfp-abac-field wfp-abac-field--full">
            <span className="wfp-abac-field-label">描述</span>
            <input
              className="wfp-abac-input"
              placeholder="策略描述（可选）"
              value={description}
              onChange={(e): void => setDescription(e.target.value)}
            />
          </div>
        </div>

        {/* 条件列表 */}
        <div className="wfp-abac-conditions">
          <h4 className="wfp-abac-subsection-title">条件集合（合取）</h4>
          {conditions.map((cond, idx) => (
            <div key={idx} className="wfp-abac-condition-row">
              <div className="wfp-abac-field">
                <span className="wfp-abac-field-label">属性名</span>
                <input
                  className="wfp-abac-input"
                  placeholder="attribute"
                  value={cond.attribute_name}
                  onChange={(e): void => {
                    const newConditions = [...conditions];
                    newConditions[idx] = { ...cond, attribute_name: e.target.value };
                    setConditions(newConditions);
                  }}
                />
              </div>
              <div className="wfp-abac-field">
                <span className="wfp-abac-field-label">来源</span>
                <select
                  className="wfp-abac-select"
                  value={cond.attribute_source}
                  onChange={(e): void => {
                    const newConditions = [...conditions];
                    newConditions[idx] = { ...cond, attribute_source: e.target.value as AttributeSource };
                    setConditions(newConditions);
                  }}
                >
                  <option value="subject">主体</option>
                  <option value="resource">资源</option>
                  <option value="environment">环境</option>
                </select>
              </div>
              <div className="wfp-abac-field">
                <span className="wfp-abac-field-label">操作符</span>
                <select
                  className="wfp-abac-select"
                  value={cond.operator}
                  onChange={(e): void => {
                    const newConditions = [...conditions];
                    newConditions[idx] = { ...cond, operator: e.target.value as ConditionOperator };
                    setConditions(newConditions);
                  }}
                >
                  <option value="eq">等于</option>
                  <option value="neq">不等于</option>
                  <option value="in">属于</option>
                  <option value="not_in">不属于</option>
                  <option value="range">范围</option>
                  <option value="ip_in_range">IP段</option>
                  <option value="time_in_range">时段</option>
                </select>
              </div>
              <div className="wfp-abac-field">
                <span className="wfp-abac-field-label">期望值</span>
                <input
                  className="wfp-abac-input"
                  placeholder="value"
                  value={cond.expected_value}
                  onChange={(e): void => {
                    const newConditions = [...conditions];
                    newConditions[idx] = { ...cond, expected_value: e.target.value };
                    setConditions(newConditions);
                  }}
                />
              </div>
            </div>
          ))}
        </div>

        <button
          className="wfp-abac-btn wfp-abac-btn--primary"
          onClick={handleCreate}
          disabled={!policyId || !conditions[0]?.attribute_name}
          style={{ marginTop: '14px' }}
        >
          创建策略
        </button>
      </div>

      {/* 策略列表 */}
      <div className="wfp-abac-list">
        <h3 className="wfp-abac-section-title">策略列表</h3>
        {policies.length === 0 && !loading && (
          <div className="wfp-abac-empty">暂无策略数据</div>
        )}
        {policies.length > 0 && (
          <table className="wfp-abac-table">
            <thead>
              <tr>
                <th>策略ID</th>
                <th>效果</th>
                <th>优先级</th>
                <th>条件数</th>
                <th>状态</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {policies.map((p) => (
                <tr key={p.policy_id}>
                  <td style={{ fontFamily: 'monospace', fontSize: '12px' }}>{p.policy_id}</td>
                  <td>{p.effect}</td>
                  <td style={{ fontVariantNumeric: 'tabular-nums' }}>{p.priority}</td>
                  <td style={{ fontVariantNumeric: 'tabular-nums' }}>{p.conditions_count}</td>
                  <td>{p.is_active ? '启用' : '已禁用'}</td>
                  <td>
                    <button
                      className="wfp-abac-btn wfp-abac-btn--danger wfp-abac-btn--sm"
                      onClick={(): Promise<void> => handleDelete(p.policy_id)}
                    >
                      删除
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
