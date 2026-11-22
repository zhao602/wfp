// =============================================================================
// 贡献提交向导（web/src/market/ContributionSubmitWizard.tsx）
//
// 开发者视图：分步提交——上传节点包→填写贡献说明→签名信息→提交结果
// =============================================================================

import { useState, useRef } from 'react';
import { useThemeMode, darkTokens } from '../theme/themeMode';
import type { SubmitResponse } from './contributionTypes';
import { submitContribution } from './contributionApiClient';

/** 贡献提交向导 */
export function ContributionSubmitWizard({ contributorId }: { contributorId: string }): JSX.Element {
  const themeMode = useThemeMode();
  const isDark = themeMode === 'dark';
  const [step, setStep] = useState(1);
  const [packageFile, setPackageFile] = useState<File | null>(null);
  const [nodeName, setNodeName] = useState('');
  const [version, setVersion] = useState('');
  const [description, setDescription] = useState('');
  const [signatureValue, setSignatureValue] = useState('');
  const [signatureAlgorithm, setSignatureAlgorithm] = useState('hmac-sha256');
  const [result, setResult] = useState<SubmitResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleFileSelect = () => {
    const file = fileInputRef.current?.files?.[0];
    if (file) setPackageFile(file);
  };

  const handleSubmit = async () => {
    if (!packageFile || !nodeName || !version || !signatureValue) return;
    setError(null);
    try {
      const response = await submitContribution(
        packageFile,
        nodeName,
        version,
        contributorId,
        signatureValue,
        signatureAlgorithm,
      );
      setResult(response);
      setStep(4);
    } catch (err) {
      setError(err instanceof Error ? err.message : '提交失败');
    }
  };

  const handleReset = () => {
    setStep(1);
    setPackageFile(null);
    setNodeName('');
    setVersion('');
    setDescription('');
    setSignatureValue('');
    setResult(null);
    setError(null);
  };

  return (
    <div className="wfp-csw-panel">
      <style>{`
.wfp-csw-input { width: 100%; box-sizing: border-box; padding: 7px 10px; font-size: 13px; font-weight: 500; font-family: inherit; color: ${isDark ? darkTokens.text.primary : '#333333'}; background-color: ${isDark ? darkTokens.surface.bg : '#ffffff'}; border: 1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'}; border-radius: 5px; outline: none; transition: background-color 120ms ease, border-color 120ms ease; }
.wfp-csw-input:hover { background-color: ${isDark ? darkTokens.surface.bgHover : '#fafafa'}; }
.wfp-csw-input:focus { border-color: ${isDark ? darkTokens.surface.borderStrong : '#bdbdbd'}; }
.wfp-csw-input::placeholder { color: ${isDark ? darkTokens.text.hint : '#b0b0b0'}; }
.wfp-csw-btn { font-size: 13px; font-weight: 500; font-family: inherit; padding: 6px 14px; border-radius: 6px; cursor: pointer; outline: none; border: 1px solid ${isDark ? darkTokens.surface.border : '#d0d0d0'}; background: ${isDark ? darkTokens.surface.bg : '#ffffff'}; color: ${isDark ? darkTokens.text.primary : '#1a1a1a'}; transition: background-color 120ms ease, border-color 120ms ease; }
.wfp-csw-btn:hover { background: ${isDark ? darkTokens.surface.bgHover : '#f5f5f5'}; border-color: ${isDark ? darkTokens.surface.borderStrong : '#b8b8b8'}; }
.wfp-csw-btn:active { background: ${isDark ? darkTokens.surface.bgActive : '#e8e8e8'}; }
.wfp-csw-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.wfp-csw-btn--primary { background: ${isDark ? darkTokens.text.primary : '#1a1a1a'}; color: ${isDark ? darkTokens.surface.bg : '#ffffff'}; border-color: ${isDark ? darkTokens.text.primary : '#1a1a1a'}; }
.wfp-csw-btn--primary:hover { background: ${isDark ? darkTokens.text.secondary : '#333333'}; }
.wfp-csw-btn--danger { color: ${isDark ? darkTokens.danger : '#d32f2f'}; background: transparent; border-color: ${isDark ? darkTokens.danger : '#d32f2f'}; }
.wfp-csw-btn--danger:hover { background: ${isDark ? 'rgba(255,107,107,0.12)' : '#fdecea'}; }
.wfp-csw-btn--sm { padding: 4px 10px; border-radius: 4px; font-size: 12px; }
.wfp-csw-select { width: 100%; box-sizing: border-box; padding: 7px 26px 7px 10px; font-size: 13px; font-weight: 500; font-family: inherit; color: ${isDark ? darkTokens.text.primary : '#333333'}; background-color: ${isDark ? darkTokens.surface.bg : '#ffffff'}; background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='8' height='5' viewBox='0 0 8 5'%3E%3Cpath d='M0 0 L4 5 L8 0 Z' fill='${isDark ? '%238a8a8a' : '%23757575'}'/%3E%3C/svg%3E"); background-repeat: no-repeat; background-position: right 9px center; border: 1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'}; border-radius: 5px; cursor: pointer; outline: none; -webkit-appearance: none; -moz-appearance: none; appearance: none; color-scheme: ${isDark ? 'dark' : 'light'}; transition: background-color 120ms ease, border-color 120ms ease; }
.wfp-csw-select:hover { background-color: ${isDark ? darkTokens.surface.bgHover : '#fafafa'}; }
.wfp-csw-select:focus { border-color: ${isDark ? darkTokens.surface.borderStrong : '#bdbdbd'}; }
.wfp-csw-textarea { width: 100%; box-sizing: border-box; padding: 7px 10px; font-size: 13px; font-weight: 400; font-family: inherit; line-height: 1.5; resize: vertical; color: ${isDark ? darkTokens.text.primary : '#333333'}; background-color: ${isDark ? darkTokens.surface.bg : '#ffffff'}; border: 1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'}; border-radius: 5px; outline: none; transition: background-color 120ms ease, border-color 120ms ease; }
.wfp-csw-textarea:hover { background-color: ${isDark ? darkTokens.surface.bgHover : '#fafafa'}; }
.wfp-csw-textarea:focus { border-color: ${isDark ? darkTokens.surface.borderStrong : '#bdbdbd'}; }
.wfp-csw-textarea::placeholder { color: ${isDark ? darkTokens.text.hint : '#b0b0b0'}; }
.wfp-csw-panel { padding: 20px 24px; background: ${isDark ? darkTokens.surface.bg : '#ffffff'}; border: 1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'}; border-radius: 8px; }
.wfp-csw-title { font-size: 15px; font-weight: 600; margin: 0 0 16px; padding-bottom: 12px; border-bottom: 1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'}; color: ${isDark ? darkTokens.text.primary : '#1a1a1a'}; }
.wfp-csw-error { margin-bottom: 16px; padding: 12px 16px; border-radius: 8px; border: 1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'}; border-left: 3px solid ${isDark ? darkTokens.danger : '#d32f2f'}; background: ${isDark ? 'rgba(255,107,107,0.08)' : '#fdecea'}; font-size: 12px; color: ${isDark ? darkTokens.danger : '#d32f2f'}; }
.wfp-csw-step { padding: 16px; background: ${isDark ? darkTokens.surface.bgHover : '#fafafa'}; border: 1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'}; border-radius: 8px; }
.wfp-csw-step-title { font-size: 14px; font-weight: 600; margin: 0 0 14px; color: ${isDark ? darkTokens.text.primary : '#1a1a1a'}; }
.wfp-csw-step > .wfp-csw-input { margin-bottom: 10px; }
.wfp-csw-step p { font-size: 12px; margin: 6px 0; color: ${isDark ? darkTokens.text.secondary : '#616161'}; }
.wfp-csw-step .wfp-csw-btn { margin-right: 8px; }
.wfp-csw-field { display: block; margin-bottom: 12px; font-size: 12px; font-weight: 500; color: ${isDark ? darkTokens.text.secondary : '#616161'}; }
.wfp-csw-field > input, .wfp-csw-field > select, .wfp-csw-field > textarea { display: block; margin-top: 6px; }
.wfp-csw-success { border-left: 3px solid ${isDark ? '#66bb6a' : '#4caf50'}; padding: 12px 16px; margin-bottom: 12px; border-radius: 0 6px 6px 0; background: ${isDark ? 'rgba(102,187,106,0.08)' : '#f1f8f4'}; }
.wfp-csw-failure { border-left: 3px solid ${isDark ? darkTokens.danger : '#d32f2f'}; padding: 12px 16px; margin-bottom: 12px; border-radius: 0 6px 6px 0; background: ${isDark ? 'rgba(255,107,107,0.08)' : '#fdecea'}; }
.wfp-csw-success p, .wfp-csw-failure p { font-size: 12px; margin: 4px 0; color: ${isDark ? darkTokens.text.secondary : '#616161'}; }
`}</style>
      <h2 className="wfp-csw-title">提交节点贡献</h2>
      {error && <div className="wfp-csw-error">{error}</div>}

      {/* 步骤 1：上传节点包 */}
      {step === 1 && (
        <div className="wfp-csw-step" data-testid="step-upload">
          <h3 className="wfp-csw-step-title">步骤 1：上传节点包</h3>
          <input
            className="wfp-csw-input"
            ref={fileInputRef}
            type="file"
            accept=".zip"
            onChange={handleFileSelect}
            data-testid="package-input"
          />
          {packageFile && <p>已选择: {packageFile.name}</p>}
          <button
            className="wfp-csw-btn wfp-csw-btn--primary"
            disabled={!packageFile}
            onClick={() => setStep(2)}
            data-testid="next-step-1"
          >
            下一步
          </button>
        </div>
      )}

      {/* 步骤 2：填写贡献说明 */}
      {step === 2 && (
        <div className="wfp-csw-step" data-testid="step-info">
          <h3 className="wfp-csw-step-title">步骤 2：贡献说明</h3>
          <label className="wfp-csw-field">节点名称: <input className="wfp-csw-input" type="text" value={nodeName} onChange={(e) => setNodeName(e.target.value)} /></label>
          <label className="wfp-csw-field">版本号: <input className="wfp-csw-input" type="text" value={version} onChange={(e) => setVersion(e.target.value)} /></label>
          <label className="wfp-csw-field">说明: <textarea className="wfp-csw-textarea" value={description} onChange={(e) => setDescription(e.target.value)} /></label>
          <button className="wfp-csw-btn" onClick={() => setStep(1)}>上一步</button>
          <button
            className="wfp-csw-btn wfp-csw-btn--primary"
            disabled={!nodeName || !version}
            onClick={() => setStep(3)}
            data-testid="next-step-2"
          >
            下一步
          </button>
        </div>
      )}

      {/* 步骤 3：签名信息 */}
      {step === 3 && (
        <div className="wfp-csw-step" data-testid="step-signature">
          <h3 className="wfp-csw-step-title">步骤 3：签名信息</h3>
          <label className="wfp-csw-field">签名算法:
            <select className="wfp-csw-select" value={signatureAlgorithm} onChange={(e) => setSignatureAlgorithm(e.target.value)}>
              <option value="hmac-sha256">HMAC-SHA256</option>
              <option value="rsa-sha256">RSA-SHA256</option>
            </select>
          </label>
          <label className="wfp-csw-field">签名值: <input className="wfp-csw-input" type="text" value={signatureValue} onChange={(e) => setSignatureValue(e.target.value)} /></label>
          <button className="wfp-csw-btn" onClick={() => setStep(2)}>上一步</button>
          <button
            className="wfp-csw-btn wfp-csw-btn--primary"
            disabled={!signatureValue}
            onClick={handleSubmit}
            data-testid="submit-button"
          >
            提交
          </button>
        </div>
      )}

      {/* 步骤 4：提交结果 */}
      {step === 4 && result && (
        <div className="wfp-csw-step" data-testid="step-result">
          <h3 className="wfp-csw-step-title">提交结果</h3>
          {result.accepted ? (
            <div className="wfp-csw-success">
              <p>提交成功</p>
              <p>贡献标识: {result.contribution_id}</p>
              <p>状态: {result.state}</p>
            </div>
          ) : (
            <div className="wfp-csw-failure">
              <p>提交被拒绝</p>
              <p>驳回原因: {result.rejection_reason}</p>
            </div>
          )}
          <button className="wfp-csw-btn wfp-csw-btn--primary" onClick={handleReset}>提交新贡献</button>
        </div>
      )}
    </div>
  );
}
