// =============================================================================
// 文档导入向导（web/src/knowledge_base/ImportWizard.tsx）
//
// 职责：多步导入——选文件 → 选策略/参数 → 预览分片 → 确认导入（T8.6 CHG-27）。
// 设计意图（DC-09 两段式 / AC-18 / AC-19）：
//   1. 复用 ContributionSubmitWizard 的 step 状态机范式（数字步进 + 条件渲染
//      + 步进守卫 + 终态结果步）；
//   2. 策略/参数联动：五策略选择 + max_chunk_length/overlap/similarity_threshold
//      按需呈现（semantic 提示需 embedding 来源）；
//   3. 预览步展示草稿分片（未写向量库）；确认步触发 confirm 调用（AC-18）。
// =============================================================================

import { useRef, useState } from 'react';
import { useThemeMode, darkTokens } from '../theme/themeMode';
import { kbManagementApiClient } from '../api/kbManagementApiClient';
import { ApiError } from '../api/apiError';
import type {
  ChunkStrategy,
  ImportDraftResponse,
  IngestionReportReport,
} from './kbManagementTypes';

/** 导入向导属性 */
export interface ImportWizardProps {
  knowledgeBaseId: string;
  /** 确认导入成功后回调（携带 document_id，供文档列表/分片编辑使用） */
  onImported?: (documentId: string) => void;
}

/** 策略中文说明（AC-19：策略可理解性） */
const STRATEGY_LABELS: Record<ChunkStrategy, string> = {
  paragraph: '段落分块',
  fixed_length: '定长分块',
  recursive: '递归分块',
  structural: '结构分块（标题层级）',
  semantic: '语义分块（实验）',
};

/** 解析自定义标注 JSON（非法/空 → undefined；不阻断导入） */
function parseCustomAnnotations(
  raw: string,
): Record<string, unknown> | undefined {
  const trimmed = raw.trim();
  if (trimmed === '') return undefined;
  try {
    const parsed = JSON.parse(trimmed) as unknown;
    if (parsed !== null && typeof parsed === 'object' && !Array.isArray(parsed)) {
      return parsed as Record<string, unknown>;
    }
  } catch {
    /* 非法 JSON：忽略（保持无自定义标注） */
  }
  return undefined;
}

/** 文档导入向导 */
export function ImportWizard({
  knowledgeBaseId,
  onImported,
}: ImportWizardProps): JSX.Element {
  const themeMode = useThemeMode();
  const isDark = themeMode === 'dark';
  const [step, setStep] = useState(1);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [strategy, setStrategy] = useState<ChunkStrategy>('paragraph');
  const [maxChunkLength, setMaxChunkLength] = useState(800);
  const [overlap, setOverlap] = useState(0);
  const [similarityThreshold, setSimilarityThreshold] = useState(0.5);
  const [hierarchy, setHierarchy] = useState('');
  const [annotationOrigin, setAnnotationOrigin] = useState('');
  const [annotationPublishedAt, setAnnotationPublishedAt] = useState('');
  const [annotationCustom, setAnnotationCustom] = useState('');
  const [parsing, setParsing] = useState(false);
  const [draft, setDraft] = useState<ImportDraftResponse | null>(null);
  const [allowDegradedFallback, setAllowDegradedFallback] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [report, setReport] = useState<IngestionReportReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleFileSelect = () => {
    const selected = fileInputRef.current?.files?.[0];
    if (selected) setFile(selected);
  };

  /** 解析预览：上传 → 草稿分片（未写向量库，DC-09 两段式第一段） */
  const handleParse = async () => {
    if (!file) return;
    setError(null);
    setParsing(true);
    try {
      const response = await kbManagementApiClient.importDocument(
        knowledgeBaseId,
        file,
        {
          strategy,
          ...(strategy === 'fixed_length' || strategy === 'recursive'
            ? { max_chunk_length: maxChunkLength, overlap }
            : {}),
          ...(strategy === 'semantic'
            ? { similarity_threshold: similarityThreshold }
            : {}),
          ...(strategy === 'structural' && hierarchy.trim() !== ''
            ? { hierarchy: hierarchy.split(',').map((level) => level.trim()) }
            : {}),
          // vkb_doc_annotation：切片标注输入（来源必填由后端受理期拦截，AC-11）
          ...(annotationOrigin.trim() !== ''
            ? { annotation_origin: annotationOrigin.trim() }
            : {}),
          ...(annotationPublishedAt.trim() !== ''
            ? { annotation_published_at: annotationPublishedAt.trim() }
            : {}),
          ...(parseCustomAnnotations(annotationCustom) !== undefined
            ? { annotation_custom: parseCustomAnnotations(annotationCustom) }
            : {}),
        },
      );
      setDraft(response);
      setStep(3);
    } catch (err) {
      setError(err instanceof Error ? err.message : '解析失败');
    } finally {
      setParsing(false);
    }
  };

  /** 确认导入：触发向量写入（DC-09 两段式第二段） */
  const handleConfirm = async () => {
    if (!draft) return;
    setError(null);
    setConfirming(true);
    try {
      // 新导入草稿的初始版本恒为 "v1"（ingestion 模块 INITIAL draft_version）
      const result = await kbManagementApiClient.confirmImport(
        knowledgeBaseId,
        draft.document_id,
        {
          job_id: draft.job_id,
          snapshot_version: 'v1',
          allow_degraded_fallback: allowDegradedFallback,
        },
      );
      setReport(result);
      if (onImported && result.status !== 'failed') {
        onImported(draft.document_id);
      }
    } catch (err) {
      if (err instanceof ApiError) {
        setError(`确认导入失败（${err.code}）：${err.message}`);
      } else {
        setError(err instanceof Error ? err.message : '确认导入失败');
      }
    } finally {
      setConfirming(false);
    }
  };

  const handleReset = () => {
    setStep(1);
    setFile(null);
    setDraft(null);
    setReport(null);
    setError(null);
    setAllowDegradedFallback(false);
  };

  return (
    <div className="wfp-kbm-wizard" data-testid="import-wizard">
      <style>{`
.wfp-kbm-wizard{padding:16px;background:${isDark ? darkTokens.surface.bgHover : '#fafafa'};border:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};border-radius:8px;}
.wfp-kbm-wizard-title{font-size:14px;font-weight:600;margin:0 0 12px;color:${isDark ? darkTokens.text.primary : '#1a1a1a'};}
.wfp-kbm-wizard p{font-size:12px;margin:6px 0;color:${isDark ? darkTokens.text.secondary : '#616161'};}
.wfp-kbm-error{margin-bottom:12px;padding:10px 14px;border-radius:8px;border:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};border-left:3px solid ${isDark ? darkTokens.danger : '#d32f2f'};background:${isDark ? 'rgba(255,107,107,0.08)' : '#fdecea'};font-size:12px;color:${isDark ? darkTokens.danger : '#d32f2f'};}
.wfp-kbm-btn{font-size:13px;font-weight:500;font-family:inherit;padding:6px 14px;border-radius:6px;cursor:pointer;outline:none;border:1px solid ${isDark ? darkTokens.surface.border : '#d0d0d0'};background:${isDark ? darkTokens.surface.bg : '#ffffff'};color:${isDark ? darkTokens.text.primary : '#1a1a1a'};}
.wfp-kbm-btn:hover{background:${isDark ? darkTokens.surface.bgHover : '#f5f5f5'};border-color:${isDark ? darkTokens.surface.borderStrong : '#b8b8b8'};}
.wfp-kbm-btn:disabled{opacity:0.5;cursor:not-allowed;}
.wfp-kbm-btn--primary{background:${isDark ? darkTokens.text.primary : '#1a1a1a'};color:${isDark ? darkTokens.surface.bg : '#ffffff'};border-color:${isDark ? darkTokens.text.primary : '#1a1a1a'};}
.wfp-kbm-btn--primary:hover{background:${isDark ? darkTokens.text.secondary : '#333333'};}
.wfp-kbm-input{box-sizing:border-box;padding:7px 10px;font-size:13px;font-weight:500;font-family:inherit;color:${isDark ? darkTokens.text.primary : '#333333'};background-color:${isDark ? darkTokens.surface.bg : '#ffffff'};border:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};border-radius:5px;outline:none;}
.wfp-kbm-select{padding:7px 10px;font-size:13px;font-family:inherit;color:${isDark ? darkTokens.text.primary : '#333333'};background-color:${isDark ? darkTokens.surface.bg : '#ffffff'};border:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};border-radius:5px;outline:none;}
.wfp-kbm-field{display:block;margin-bottom:10px;font-size:12px;font-weight:500;color:${isDark ? darkTokens.text.secondary : '#616161'};}
.wfp-kbm-field > input, .wfp-kbm-field > select{display:block;margin-top:6px;}
.wfp-kbm-param{margin:8px 0;padding:10px;background:${isDark ? darkTokens.surface.bg : '#ffffff'};border:1px solid ${isDark ? darkTokens.surface.divider : '#eee'};border-radius:6px;}
.wfp-kbm-notice{padding:8px 12px;border-radius:6px;background:${isDark ? 'rgba(255,213,79,0.10)' : '#fff8e1'};color:${isDark ? '#ffd54f' : '#f57f17'};font-size:12px;margin:6px 0;}
.wfp-kbm-chunklist{max-height:220px;overflow-y:auto;margin:8px 0;border:1px solid ${isDark ? darkTokens.surface.divider : '#eee'};border-radius:6px;}
.wfp-kbm-chunkitem{padding:8px 12px;font-size:12px;color:${isDark ? darkTokens.text.primary : '#333333'};border-bottom:1px solid ${isDark ? darkTokens.surface.divider : '#f0f0f0'};}
.wfp-kbm-chunkitem:last-child{border-bottom:none;}
.wfp-kbm-chunkitem-index{font-weight:600;margin-right:8px;color:${isDark ? darkTokens.text.secondary : '#616161'};}
.wfp-kbm-report{padding:12px 16px;margin:8px 0;border-radius:6px;border:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};background:${isDark ? darkTokens.surface.bg : '#ffffff'};}
.wfp-kbm-check{display:flex;align-items:center;gap:6px;font-size:12px;color:${isDark ? darkTokens.text.secondary : '#616161'};cursor:pointer;margin:8px 0;}
`}</style>
      <h3 className="wfp-kbm-wizard-title">导入文档</h3>
      {error && <div className="wfp-kbm-error" data-testid="wizard-error">{error}</div>}

      {/* 步骤 1：选择文件 */}
      {step === 1 && (
        <div data-testid="step-file">
          <p>步骤 1：选择待导入文档（txt / md / docx / pdf）</p>
          <input
            ref={fileInputRef}
            type="file"
            accept=".txt,.md,.docx,.pdf"
            onChange={handleFileSelect}
            data-testid="wizard-file-input"
          />
          {file && <p>已选择: {file.name}</p>}
          <button
            className="wfp-kbm-btn wfp-kbm-btn--primary"
            disabled={!file}
            onClick={() => setStep(2)}
            data-testid="next-step-1"
          >
            下一步
          </button>
        </div>
      )}

      {/* 步骤 2：策略与参数（联动呈现） */}
      {step === 2 && (
        <div data-testid="step-strategy">
          <p>步骤 2：选择分片策略与参数</p>
          <label className="wfp-kbm-field">
            分片策略:
            <select
              className="wfp-kbm-select"
              value={strategy}
              onChange={(e) => setStrategy(e.target.value as ChunkStrategy)}
              data-testid="strategy-select"
            >
              {(Object.keys(STRATEGY_LABELS) as ChunkStrategy[]).map((key) => (
                <option key={key} value={key}>
                  {STRATEGY_LABELS[key]}
                </option>
              ))}
            </select>
          </label>
          {(strategy === 'fixed_length' || strategy === 'recursive') && (
            <div className="wfp-kbm-param" data-testid="strategy-param-length">
              <label className="wfp-kbm-field">
                分块最大长度（字符）:
                <input
                  className="wfp-kbm-input"
                  type="number"
                  min={50}
                  max={2000}
                  value={maxChunkLength}
                  onChange={(e) => setMaxChunkLength(Number(e.target.value))}
                  data-testid="max-chunk-length-input"
                />
              </label>
              <label className="wfp-kbm-field">
                相邻分片重叠（字符）:
                <input
                  className="wfp-kbm-input"
                  type="number"
                  min={0}
                  value={overlap}
                  onChange={(e) => setOverlap(Number(e.target.value))}
                  data-testid="overlap-input"
                />
              </label>
            </div>
          )}
          {strategy === 'semantic' && (
            <div className="wfp-kbm-param" data-testid="strategy-param-semantic">
              <div className="wfp-kbm-notice" data-testid="semantic-hint">
                语义分块需知识库绑定 embedding 来源（本地模型或 API 服务）
              </div>
              <label className="wfp-kbm-field">
                语义相似度阈值:
                <input
                  className="wfp-kbm-input"
                  type="number"
                  min={0}
                  max={1}
                  step={0.05}
                  value={similarityThreshold}
                  onChange={(e) => setSimilarityThreshold(Number(e.target.value))}
                  data-testid="similarity-threshold-input"
                />
              </label>
            </div>
          )}
          {strategy === 'structural' && (
            <div className="wfp-kbm-param" data-testid="strategy-param-structural">
              <label className="wfp-kbm-field">
                标题层级（逗号分隔，如: 1,2,3）:
                <input
                  className="wfp-kbm-input"
                  type="text"
                  value={hierarchy}
                  onChange={(e) => setHierarchy(e.target.value)}
                  data-testid="hierarchy-input"
                />
              </label>
            </div>
          )}
          {/* vkb_doc_annotation：切片标注输入（来源必填 / 发布时间可"未知" / 自定义） */}
          <div className="wfp-kbm-param" data-testid="annotation-inputs">
            <label className="wfp-kbm-field">
              标注-来源（必填）:
              <input
                className="wfp-kbm-input"
                type="text"
                value={annotationOrigin}
                onChange={(e) => setAnnotationOrigin(e.target.value)}
                data-testid="annotation-origin-input"
              />
            </label>
            <label className="wfp-kbm-field">
              标注-发布时间（可选，留空=未知）:
              <input
                className="wfp-kbm-input"
                type="text"
                value={annotationPublishedAt}
                onChange={(e) => setAnnotationPublishedAt(e.target.value)}
                data-testid="annotation-published-at-input"
              />
            </label>
            <label className="wfp-kbm-field">
              标注-自定义（JSON 对象，可选）:
              <input
                className="wfp-kbm-input"
                type="text"
                value={annotationCustom}
                placeholder='{"版本":"v2"}'
                onChange={(e) => setAnnotationCustom(e.target.value)}
                data-testid="annotation-custom-input"
              />
            </label>
          </div>
          <button className="wfp-kbm-btn" onClick={() => setStep(1)}>
            上一步
          </button>
          <button
            className="wfp-kbm-btn wfp-kbm-btn--primary"
            disabled={!file || parsing}
            onClick={handleParse}
            data-testid="parse-preview-button"
          >
            {parsing ? '解析中…' : '解析预览'}
          </button>
        </div>
      )}

      {/* 步骤 3：分片预览（草稿态，未写向量库） */}
      {step === 3 && draft && (
        <div data-testid="step-preview">
          <p>
            步骤 3：分片预览（共 {draft.chunk_count} 片，实际策略：
            {STRATEGY_LABELS[draft.strategy_effective]}，解析状态：
            {draft.parse_status}）
          </p>
          {draft.notices.map((notice, index) => (
            <div key={index} className="wfp-kbm-notice">
              {notice}
            </div>
          ))}
          <div className="wfp-kbm-chunklist" data-testid="chunk-preview-list">
            {draft.chunks.map((chunk) => (
              <div key={chunk.chunk_id} className="wfp-kbm-chunkitem">
                <span className="wfp-kbm-chunkitem-index">#{chunk.index}</span>
                {chunk.text.length > 120
                  ? `${chunk.text.slice(0, 120)}…`
                  : chunk.text}
              </div>
            ))}
          </div>
          <button className="wfp-kbm-btn" onClick={() => setStep(2)}>
            上一步（重新选择策略）
          </button>
          <button
            className="wfp-kbm-btn wfp-kbm-btn--primary"
            onClick={() => setStep(4)}
            data-testid="next-step-3"
          >
            下一步（确认导入）
          </button>
        </div>
      )}

      {/* 步骤 4：确认导入（触发向量写入） */}
      {step === 4 && draft && !report && (
        <div data-testid="step-confirm">
          <p>
            步骤 4：确认导入（文档 {draft.document_id}，{draft.chunk_count} 片将写入向量库）
          </p>
          <label className="wfp-kbm-check">
            <input
              type="checkbox"
              checked={allowDegradedFallback}
              onChange={(e) => setAllowDegradedFallback(e.target.checked)}
              data-testid="fallback-checkbox"
            />
            允许降级兜底（Embedding 不可用时哈希降级写入）
          </label>
          <button className="wfp-kbm-btn" onClick={() => setStep(3)}>
            上一步
          </button>
          <button
            className="wfp-kbm-btn wfp-kbm-btn--primary"
            disabled={confirming}
            onClick={handleConfirm}
            data-testid="confirm-import-button"
          >
            {confirming ? '导入中…' : '确认导入'}
          </button>
        </div>
      )}

      {/* 确认结果 */}
      {report && (
        <div data-testid="step-result">
          <div className="wfp-kbm-report" data-testid="import-report">
            <p>导入完成：{report.status}</p>
            <p>分片总数：{report.chunk_count}，成功写入：{report.upserted_count}</p>
            {report.failed.length > 0 && (
              <p>
                失败 {report.failed.length} 片：
                {report.failed.map((item) => `${item.chunk_id}（${item.reason}）`).join('、')}
              </p>
            )}
            {report.local_degraded && (
              <p>注意：本次写入发生 Embedding 降级（{report.local_degraded_reason ?? '哈希降级'}）</p>
            )}
            <p>耗时：{report.duration_ms}ms</p>
          </div>
          <button
            className="wfp-kbm-btn wfp-kbm-btn--primary"
            onClick={handleReset}
            data-testid="reset-import-button"
          >
            继续导入新文档
          </button>
        </div>
      )}
    </div>
  );
}
