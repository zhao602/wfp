// =============================================================================
// NL 编排面板组件（NLOrchestrationPanel.tsx）
//
// 职责：提供自然语言编排入口，用户输入指令后展示生成的 YAML 预览与校验报告。
// 设计意图（对应偏差修正 T87）：
//   - 自然语言输入框 + 意图类型选择（创建/修改）
//   - 提交后展示生成的 YAML 预览（可编辑）与校验报告
//   - 校验失败时展示错误码与错误清单
// =============================================================================

import { useState, useCallback } from 'react';
import { useThemeMode, darkTokens } from '../theme/themeMode';
import type {
  NLOrchestrationResponse,
  ValidationIssue,
} from './nlOrchestrationTypes';
import { createFromNl, modifyFromNl } from './nlOrchestrationApiClient';

interface NLOrchestrationPanelProps {
  token: string;
  tenantId: string;
  userId: string;
}

export function NLOrchestrationPanel({
  token,
  tenantId,
  userId,
}: NLOrchestrationPanelProps): JSX.Element {
  const themeMode = useThemeMode();
  const isDark = themeMode === 'dark';
  const [directive, setDirective] = useState<string>('');
  const [intentType, setIntentType] = useState<'create' | 'modify'>('create');
  const [targetYaml, setTargetYaml] = useState<string>('');
  const [result, setResult] = useState<NLOrchestrationResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(false);

  const handleSubmit = useCallback(async (): Promise<void> => {
    setLoading(true);
    setResult(null);
    try {
      if (intentType === 'create') {
        const response = await createFromNl(
          { directive, tenant_id: tenantId, user_id: userId },
          token,
        );
        setResult(response);
      } else {
        const response = await modifyFromNl(
          { directive, target_yaml: targetYaml, tenant_id: tenantId, user_id: userId },
          token,
        );
        setResult(response);
      }
    } catch (err) {
      setResult({
        success: false,
        error_code: 'nl_parse_failed',
        error_message: String(err),
        validation_issues: [],
      });
    } finally {
      setLoading(false);
    }
  }, [directive, intentType, targetYaml, tenantId, userId, token]);

  return (
    <div className="wfp-nlo-panel">
      <style>{`
.wfp-nlo-input {
  width: 100%;
  box-sizing: border-box;
  padding: 7px 10px;
  font-size: 13px;
  font-weight: 500;
  font-family: inherit;
  color: ${isDark ? darkTokens.text.primary : '#333333'};
  background-color: ${isDark ? darkTokens.surface.bg : '#ffffff'};
  border: 1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};
  border-radius: 5px;
  outline: none;
  transition: background-color 120ms ease, border-color 120ms ease;
}
.wfp-nlo-input:hover {
  background-color: ${isDark ? darkTokens.surface.bgHover : '#f5f5f5'};
  border-color: ${isDark ? darkTokens.surface.border : '#bdbdbd'};
}
.wfp-nlo-input:focus {
  border-color: ${isDark ? darkTokens.text.focus : '#1a1a1a'};
}
.wfp-nlo-input::placeholder {
  color: ${isDark ? darkTokens.text.hint : '#999999'};
}
.wfp-nlo-textarea {
  width: 100%;
  box-sizing: border-box;
  padding: 7px 10px;
  font-size: 13px;
  font-weight: 400;
  font-family: inherit;
  line-height: 1.5;
  resize: vertical;
  color: ${isDark ? darkTokens.text.primary : '#333333'};
  background-color: ${isDark ? darkTokens.surface.bg : '#ffffff'};
  border: 1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};
  border-radius: 5px;
  outline: none;
  transition: background-color 120ms ease, border-color 120ms ease;
}
.wfp-nlo-textarea:hover {
  background-color: ${isDark ? darkTokens.surface.bgHover : '#f5f5f5'};
  border-color: ${isDark ? darkTokens.surface.border : '#bdbdbd'};
}
.wfp-nlo-textarea:focus {
  border-color: ${isDark ? darkTokens.text.focus : '#1a1a1a'};
}
.wfp-nlo-textarea::placeholder {
  color: ${isDark ? darkTokens.text.hint : '#999999'};
}
.wfp-nlo-radio {
  width: 14px;
  height: 14px;
  border: 1.5px solid ${isDark ? darkTokens.surface.border : '#d0d0d0'};
  border-radius: 50%;
  cursor: pointer;
  accent-color: ${isDark ? darkTokens.text.primary : '#1a1a1a'};
  outline: none;
}
.wfp-nlo-radio:focus {
  box-shadow: 0 0 0 2px ${isDark ? darkTokens.text.focus : '#cccccc'};
}
.wfp-nlo-btn {
  font-size: 13px;
  font-weight: 500;
  font-family: inherit;
  padding: 6px 14px;
  border-radius: 6px;
  cursor: pointer;
  outline: none;
  border: 1px solid ${isDark ? darkTokens.surface.border : '#d0d0d0'};
  background: ${isDark ? darkTokens.surface.bg : '#ffffff'};
  color: ${isDark ? darkTokens.text.primary : '#1a1a1a'};
  transition: background-color 120ms ease, border-color 120ms ease;
}
.wfp-nlo-btn:hover {
  background: ${isDark ? darkTokens.surface.bgHover : '#f5f5f5'};
  border-color: ${isDark ? darkTokens.surface.borderStrong : '#bdbdbd'};
}
.wfp-nlo-btn:active {
  background: ${isDark ? darkTokens.surface.bgActive : '#e0e0e0'};
}
.wfp-nlo-btn--primary {
  background: ${isDark ? darkTokens.surface.bgActive : '#1a1a1a'};
  color: ${isDark ? darkTokens.text.primary : '#ffffff'};
  border-color: ${isDark ? darkTokens.surface.borderStrong : '#1a1a1a'};
}
.wfp-nlo-btn--primary:hover {
  background: ${isDark ? darkTokens.surface.bgHover : '#333333'};
}
.wfp-nlo-btn--danger {
  background: ${isDark ? darkTokens.danger : '#d32f2f'};
  color: #ffffff;
  border-color: ${isDark ? darkTokens.danger : '#d32f2f'};
}
.wfp-nlo-btn--sm {
  font-size: 12px;
  padding: 4px 10px;
}
/* @experimental: 预置样式，当前无使用点，未来 API 可能变动 */
.wfp-nlo-checkbox {
  width: 14px;
  height: 14px;
  border: 1.5px solid ${isDark ? darkTokens.surface.border : '#d0d0d0'};
  border-radius: 3px;
  cursor: pointer;
  accent-color: ${isDark ? darkTokens.text.primary : '#1a1a1a'};
  outline: none;
}

.wfp-nlo-panel {
  padding: 20px 24px;
  background: ${isDark ? darkTokens.surface.bg : '#ffffff'};
  border: 1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};
  border-radius: 8px;
}
.wfp-nlo-title {
  font-size: 15px;
  font-weight: 600;
  margin: 0 0 16px;
  padding-bottom: 12px;
  border-bottom: 1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};
  color: ${isDark ? darkTokens.text.primary : '#1a1a1a'};
}
.wfp-nlo-intent {
  display: flex;
  gap: 20px;
  margin-bottom: 18px;
}
.wfp-nlo-intent label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  user-select: none;
  color: ${isDark ? darkTokens.text.secondary : '#424242'};
  transition: color 120ms ease;
}
.wfp-nlo-intent label:hover {
  color: ${isDark ? darkTokens.text.primary : '#1a1a1a'};
}
.wfp-nlo-field {
  margin-bottom: 16px;
}
.wfp-nlo-field > label {
  display: block;
  font-size: 12px;
  font-weight: 500;
  margin-bottom: 6px;
  color: ${isDark ? darkTokens.text.secondary : '#616161'};
}
.wfp-nlo-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.wfp-nlo-btn:disabled:hover {
  background: ${isDark ? darkTokens.surface.bg : '#ffffff'};
  border-color: ${isDark ? darkTokens.surface.border : '#d0d0d0'};
}
.wfp-nlo-result {
  margin-top: 20px;
  padding: 16px;
  border-radius: 8px;
  background: ${isDark ? darkTokens.surface.bgHover : '#fafafa'};
  border: 1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};
}
.wfp-nlo-success {
  border-left: 3px solid ${isDark ? '#66bb6a' : '#4caf50'};
}
.wfp-nlo-error {
  border-left: 3px solid ${isDark ? darkTokens.danger : '#d32f2f'};
}
.wfp-nlo-result h3 {
  font-size: 14px;
  font-weight: 600;
  margin: 0 0 12px;
  color: ${isDark ? darkTokens.text.primary : '#1a1a1a'};
}
.wfp-nlo-result p {
  font-size: 12px;
  margin: 4px 0;
  color: ${isDark ? darkTokens.text.secondary : '#616161'};
}
.wfp-nlo-issues h4 {
  font-size: 13px;
  font-weight: 600;
  margin: 14px 0 8px;
  color: ${isDark ? darkTokens.text.primary : '#1a1a1a'};
}
.wfp-nlo-issues ul {
  list-style: none;
  padding: 0;
  margin: 0;
}
.wfp-nlo-issues li {
  font-size: 12px;
  line-height: 1.6;
  padding: 6px 0;
  border-bottom: 1px solid ${isDark ? darkTokens.surface.divider : '#f0f0f0'};
  color: ${isDark ? darkTokens.text.secondary : '#616161'};
}
.wfp-nlo-issues li:last-child {
  border-bottom: none;
}
.wfp-nlo-issues strong {
  color: ${isDark ? darkTokens.danger : '#d32f2f'};
  font-weight: 600;
}
.wfp-nlo-issues span {
  color: ${isDark ? darkTokens.text.hint : '#999999'};
  margin-left: 4px;
}
`}</style>
      <h2 className="wfp-nlo-title">自然语言编排</h2>

      {/* 意图类型选择 */}
      <div className="wfp-nlo-intent">
        <label>
          <input
            type="radio"
            className="wfp-nlo-radio"
            checked={intentType === 'create'}
            onChange={(): void => setIntentType('create')}
          />
          创建工作流
        </label>
        <label>
          <input
            type="radio"
            className="wfp-nlo-radio"
            checked={intentType === 'modify'}
            onChange={(): void => setIntentType('modify')}
          />
          修改工作流
        </label>
      </div>

      {/* 修改意图时需要目标 YAML */}
      {intentType === 'modify' && (
        <div className="wfp-nlo-field">
          <label>目标工作流 YAML：</label>
          <textarea
            className="wfp-nlo-textarea"
            value={targetYaml}
            onChange={(e): void => setTargetYaml(e.target.value)}
            rows={6}
            placeholder="粘贴既有工作流 YAML..."
          />
        </div>
      )}

      {/* 自然语言指令输入 */}
      <div className="wfp-nlo-field">
        <label>自然语言指令：</label>
        <textarea
          className="wfp-nlo-textarea"
          value={directive}
          onChange={(e): void => setDirective(e.target.value)}
          rows={4}
          placeholder={
            intentType === 'create'
              ? '描述要创建的工作流，如"创建一个先检索再总结的工作流"'
              : '描述修改指令，如"在 Agent A 后增加一个数据校验节点"'
          }
        />
      </div>

      {/* 提交按钮 */}
      <button className="wfp-nlo-btn wfp-nlo-btn--primary" onClick={handleSubmit} disabled={loading || !directive.trim()}>
        {loading ? '解析中...' : '提交'}
      </button>

      {/* 结果展示 */}
      {result !== null && (
        <div className="wfp-nlo-result">
          {result.success ? (
            <div className="wfp-nlo-success">
              <h3>生成成功</h3>
              <label>YAML 预览（可编辑）：</label>
              <textarea
                className="wfp-nlo-textarea"
                value={result.yaml_text ?? ''}
                readOnly
                rows={12}
              />
            </div>
          ) : (
            <div className="wfp-nlo-error">
              <h3>生成失败</h3>
              <p>错误码：{result.error_code ?? 'unknown'}</p>
              <p>错误信息：{result.error_message ?? ''}</p>
              {result.validation_issues.length > 0 && (
                <div className="wfp-nlo-issues">
                  <h4>校验问题清单：</h4>
                  <ul>
                    {result.validation_issues.map(
                      (issue: ValidationIssue, idx: number): JSX.Element => (
                        <li key={idx}>
                          <strong>[{issue.code}]</strong> {issue.message}
                          {issue.node_id !== null && issue.node_id !== undefined && (
                            <span>（节点: {issue.node_id}）</span>
                          )}
                        </li>
                      ),
                    )}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
