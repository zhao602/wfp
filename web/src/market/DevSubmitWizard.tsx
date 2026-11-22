// =============================================================================
// 开发包提交向导（web/src/market/DevSubmitWizard.tsx）
//
// 开发者视图（FR-27 规则 3：与审核台自成分离）：
//   步骤 1 获取脚手架（DV-1/DV-2）→ 步骤 2 上传包 → 步骤 3 填写元数据
//   （developer_id / node_name / version）→ 步骤 4 展示三层结构化报告
//   （按 layer 分组，DC-06）。
// 复用 wfp-csw-* 内联样式 + useThemeMode/darkTokens 双轨配色 + data-testid
// 钩子（对齐 ContributionSubmitWizard 范式；该文件属零改动登记，此处仅
// 复用同类名样式定义）；接口失败时页面可渲染 + 错误提示 + 既有配置不清空
// （AC-DEV-22）；面板异常由既有 AdminErrorBoundary 兜底（AC-DEV-23）。
// =============================================================================

import { useEffect, useState, useRef } from 'react';
import { useThemeMode, darkTokens } from '../theme/themeMode';
import type {
  DevFinding,
  DevScaffoldDetail,
  DevScaffoldSummary,
  DevSubmissionResponse,
  DevValidationReport,
} from './devSubmissionTypes';
import {
  getDevScaffold,
  listDevScaffolds,
  submitDevPackage,
} from './devSubmissionApiClient';

/** 报告发现项按 layer 分组（L1/L2/L3 三层，DC-06） */
function groupFindingsByLayer(report: DevValidationReport): DevFinding[][] {
  const layers = ['L1', 'L2', 'L3'];
  return layers.map((layer) =>
    report.findings.filter((finding) => finding.layer === layer),
  );
}

/** 开发包提交向导 */
export function DevSubmitWizard({ developerId }: { developerId: string }): JSX.Element {
  const themeMode = useThemeMode();
  const isDark = themeMode === 'dark';
  const [step, setStep] = useState(1);
  const [scaffolds, setScaffolds] = useState<DevScaffoldSummary[]>([]);
  const [selectedScaffoldId, setSelectedScaffoldId] = useState('');
  const [scaffoldDetail, setScaffoldDetail] = useState<DevScaffoldDetail | null>(null);
  const [packageFile, setPackageFile] = useState<File | null>(null);
  const [developer, setDeveloper] = useState(developerId);
  const [nodeName, setNodeName] = useState('');
  const [version, setVersion] = useState('');
  const [result, setResult] = useState<DevSubmissionResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // 步骤 1：加载脚手架清单（失败 → 错误条，页面可渲染，AC-DEV-22）
  useEffect(() => {
    let cancelled = false;
    listDevScaffolds()
      .then((response) => {
        if (!cancelled) setScaffolds(response.scaffolds);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : '加载脚手架清单失败');
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // 选中脚手架 → 加载详情（DV-2 五要素 + 文件模板内联）
  const handleScaffoldSelect = (scaffoldId: string) => {
    setSelectedScaffoldId(scaffoldId);
    setScaffoldDetail(null);
    if (!scaffoldId) return;
    getDevScaffold(scaffoldId)
      .then((detail) => setScaffoldDetail(detail))
      .catch((err: unknown) =>
        setError(err instanceof Error ? err.message : '加载脚手架详情失败'),
      );
  };

  const handleFileSelect = () => {
    const file = fileInputRef.current?.files?.[0];
    if (file) setPackageFile(file);
  };

  // 步骤 3 → 提交（DV-3）；失败 → 错误条 + 既有配置不清空（AC-DEV-22）
  const handleSubmit = async () => {
    if (!packageFile || !developer || !nodeName || !version) return;
    setError(null);
    try {
      const response = await submitDevPackage(
        packageFile,
        developer,
        nodeName,
        version,
      );
      setResult(response);
      setStep(4);
    } catch (err) {
      setError(err instanceof Error ? err.message : '提交开发包失败');
    }
  };

  const handleReset = () => {
    setStep(1);
    setPackageFile(null);
    setNodeName('');
    setVersion('');
    setResult(null);
    setError(null);
  };

  const groupedFindings = result ? groupFindingsByLayer(result.validation_report) : [];

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
.wfp-csw-report { padding: 12px; background: ${isDark ? darkTokens.surface.bg : '#ffffff'}; border: 1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'}; border-radius: 6px; margin-bottom: 8px; }
.wfp-csw-report ul { list-style: none; padding: 0; margin: 0; }
.wfp-csw-report li { font-size: 12px; padding: 4px 0; border-bottom: 1px solid ${isDark ? darkTokens.surface.divider : '#f0f0f0'}; color: ${isDark ? darkTokens.text.secondary : '#616161'}; }
.wfp-csw-report li:last-child { border-bottom: none; }
.wfp-csw-scaffold-detail pre { font-size: 11px; line-height: 1.5; padding: 10px; overflow-x: auto; background: ${isDark ? darkTokens.surface.bgActive : '#f5f5f5'}; border-radius: 6px; color: ${isDark ? darkTokens.text.secondary : '#616161'}; white-space: pre-wrap; word-break: break-all; margin: 6px 0 10px; }
`}</style>
      <h2 className="wfp-csw-title">开发包提交</h2>
      {error && <div className="wfp-csw-error" data-testid="dev-wizard-error">{error}</div>}

      {/* 步骤 1：获取脚手架 */}
      {step === 1 && (
        <div className="wfp-csw-step" data-testid="dev-wizard-step-scaffold">
          <h3 className="wfp-csw-step-title">步骤 1：获取脚手架</h3>
          <label className="wfp-csw-field">脚手架模板:
            <select
              className="wfp-csw-select"
              value={selectedScaffoldId}
              onChange={(e) => handleScaffoldSelect(e.target.value)}
              data-testid="dev-wizard-scaffold-select"
            >
              <option value="">请选择脚手架…</option>
              {scaffolds.map((scaffold) => (
                <option key={scaffold.scaffold_id} value={scaffold.scaffold_id}>
                  {scaffold.name}（{scaffold.scaffold_id} v{scaffold.version}）
                </option>
              ))}
            </select>
          </label>
          {scaffolds.length === 0 && !error && <p>暂无可用脚手架</p>}
          {scaffoldDetail && (
            <div className="wfp-csw-scaffold-detail" data-testid="dev-wizard-scaffold-detail">
              <p>{scaffoldDetail.summary}</p>
              <p>目录结构：</p>
              <pre>{scaffoldDetail.directory_structure}</pre>
              <p>必需文件：{scaffoldDetail.required_files.join('、')}</p>
              <p>manifest 规范：</p>
              <pre>{scaffoldDetail.manifest_spec}</pre>
              <p>接口契约：{scaffoldDetail.interface_contract}</p>
              <p>依赖与运行时：{scaffoldDetail.dependency_and_runtime}</p>
              <p>文件模板：{scaffoldDetail.file_templates.map((t) => t.path).join('、')}</p>
              <p>{scaffoldDetail.fetch_hint}</p>
            </div>
          )}
          <button
            className="wfp-csw-btn wfp-csw-btn--primary"
            disabled={!selectedScaffoldId}
            onClick={() => setStep(2)}
            data-testid="dev-wizard-next-1"
          >
            下一步
          </button>
        </div>
      )}

      {/* 步骤 2：上传开发包 */}
      {step === 2 && (
        <div className="wfp-csw-step" data-testid="dev-wizard-step-upload">
          <h3 className="wfp-csw-step-title">步骤 2：上传开发包</h3>
          <input
            className="wfp-csw-input"
            ref={fileInputRef}
            type="file"
            accept=".zip"
            onChange={handleFileSelect}
            data-testid="dev-wizard-package-input"
          />
          {packageFile && <p>已选择: {packageFile.name}</p>}
          <button className="wfp-csw-btn" onClick={() => setStep(1)}>上一步</button>
          <button
            className="wfp-csw-btn wfp-csw-btn--primary"
            disabled={!packageFile}
            onClick={() => setStep(3)}
            data-testid="dev-wizard-next-2"
          >
            下一步
          </button>
        </div>
      )}

      {/* 步骤 3：填写元数据 */}
      {step === 3 && (
        <div className="wfp-csw-step" data-testid="dev-wizard-step-info">
          <h3 className="wfp-csw-step-title">步骤 3：提交元数据</h3>
          <label className="wfp-csw-field">开发者标识: <input className="wfp-csw-input" type="text" value={developer} onChange={(e) => setDeveloper(e.target.value)} data-testid="dev-wizard-developer-input" /></label>
          <label className="wfp-csw-field">节点名称: <input className="wfp-csw-input" type="text" value={nodeName} onChange={(e) => setNodeName(e.target.value)} data-testid="dev-wizard-node-name-input" /></label>
          <label className="wfp-csw-field">版本号: <input className="wfp-csw-input" type="text" value={version} onChange={(e) => setVersion(e.target.value)} data-testid="dev-wizard-version-input" /></label>
          <button className="wfp-csw-btn" onClick={() => setStep(2)}>上一步</button>
          <button
            className="wfp-csw-btn wfp-csw-btn--primary"
            disabled={!developer || !nodeName || !version}
            onClick={handleSubmit}
            data-testid="dev-wizard-submit-button"
          >
            提交
          </button>
        </div>
      )}

      {/* 步骤 4：三层结构化报告（按 layer 分组） */}
      {step === 4 && result && (
        <div className="wfp-csw-step" data-testid="dev-wizard-step-result">
          <h3 className="wfp-csw-step-title">提交结果</h3>
          {result.accepted ? (
            <div className="wfp-csw-success">
              <p>提交成功（{result.state}）</p>
              <p>提交标识: {result.submission_id}</p>
            </div>
          ) : (
            <div className="wfp-csw-failure">
              <p>提交被拒绝（{result.state}）</p>
              <p>驳回原因: {result.rejection_reason}</p>
            </div>
          )}
          <div className="wfp-csw-report" data-testid="dev-wizard-report">
            <p>校验报告（{result.validation_report.valid ? '通过' : '未通过'}；扫描器 {result.validation_report.scanner_version}）</p>
            {['L1', 'L2', 'L3'].map((layer, index) => (
              <div key={layer} data-testid={`dev-wizard-report-${layer}`}>
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
          <button className="wfp-csw-btn wfp-csw-btn--primary" onClick={handleReset} data-testid="dev-wizard-reset-button">提交新开发包</button>
        </div>
      )}
    </div>
  );
}
