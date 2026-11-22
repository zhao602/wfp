// =============================================================================
// 知识库详情面板（web/src/knowledge_base/KnowledgeBaseDetailPanel.tsx）
//
// 职责：知识库详情 + 绑定摘要（只读）+ 集合诊断 + 文档列表（导入入口）
// （T8.5 CHG-26）。
// 设计意图（INV-D10）：
//   1. 绑定摘要只读呈现（零凭据：无服务地址字段）；
//   2. 诊断只读透传：ok=false 不崩溃（issues 列表呈现，缺失集合可诊断）；
//   3. 文档列表为会话内导入文档（导入向导确认成功后登记），逐文档可进入
//      分片编辑器。
// =============================================================================

import { useCallback, useEffect, useState } from 'react';
import { useThemeMode, darkTokens } from '../theme/themeMode';
import { kbManagementApiClient } from '../api/kbManagementApiClient';
import { ApiError } from '../api/apiError';
import { ImportWizard } from './ImportWizard';
import { ChunkEditor } from './ChunkEditor';
import type {
  DiagnosticReport,
  KnowledgeBaseView,
} from './kbManagementTypes';

/** 详情面板属性 */
export interface KnowledgeBaseDetailPanelProps {
  knowledgeBaseId: string;
  /** 返回列表回调（缺省隐藏返回按钮） */
  onBack?: () => void;
}

/** 知识库详情面板 */
export function KnowledgeBaseDetailPanel({
  knowledgeBaseId,
  onBack,
}: KnowledgeBaseDetailPanelProps): JSX.Element {
  const themeMode = useThemeMode();
  const isDark = themeMode === 'dark';
  const [detail, setDetail] = useState<KnowledgeBaseView | null>(null);
  const [diagnosis, setDiagnosis] = useState<DiagnosticReport | null>(null);
  const [documents, setDocuments] = useState<string[]>([]);
  const [editingDocumentId, setEditingDocumentId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [view, report] = await Promise.all([
        kbManagementApiClient.getKnowledgeBase(knowledgeBaseId),
        kbManagementApiClient.diagnoseKnowledgeBase(knowledgeBaseId),
      ]);
      setDetail(view);
      setDiagnosis(report);
      setError(null);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(`详情加载失败（${err.code}）：${err.message}`);
      } else {
        setError(err instanceof Error ? err.message : '详情加载失败');
      }
    }
  }, [knowledgeBaseId]);

  useEffect(() => {
    setDocuments([]);
    setEditingDocumentId(null);
    load();
  }, [load]);

  /** 导入向导确认成功回调：登记文档并进入分片编辑入口列表 */
  const handleImported = (documentId: string) => {
    setDocuments((prev) => (prev.includes(documentId) ? prev : [...prev, documentId]));
    setEditingDocumentId(documentId);
  };

  return (
    <div className="wfp-kbm-detail" data-testid="kb-detail">
      <style>{`
.wfp-kbm-detail{padding:16px;background:${isDark ? darkTokens.surface.bgHover : '#fafafa'};border:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};border-radius:8px;}
.wfp-kbm-detail-title{font-size:14px;font-weight:600;margin:0 0 12px;color:${isDark ? darkTokens.text.primary : '#1a1a1a'};}
.wfp-kbm-detail p{font-size:12px;margin:5px 0;color:${isDark ? darkTokens.text.secondary : '#616161'};}
.wfp-kbm-error{margin-bottom:12px;padding:10px 14px;border-radius:8px;border:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};border-left:3px solid ${isDark ? darkTokens.danger : '#d32f2f'};background:${isDark ? 'rgba(255,107,107,0.08)' : '#fdecea'};font-size:12px;color:${isDark ? darkTokens.danger : '#d32f2f'};}
.wfp-kbm-btn{font-size:12px;font-weight:500;font-family:inherit;padding:5px 12px;border-radius:5px;cursor:pointer;outline:none;border:1px solid ${isDark ? darkTokens.surface.border : '#d0d0d0'};background:${isDark ? darkTokens.surface.bg : '#ffffff'};color:${isDark ? darkTokens.text.primary : '#1a1a1a'};}
.wfp-kbm-btn:hover{background:${isDark ? darkTokens.surface.bgHover : '#f5f5f5'};}
.wfp-kbm-card{padding:12px;margin-bottom:12px;background:${isDark ? darkTokens.surface.bg : '#ffffff'};border:1px solid ${isDark ? darkTokens.surface.divider : '#eee'};border-radius:6px;}
.wfp-kbm-card-title{font-size:13px;font-weight:600;margin:0 0 8px;color:${isDark ? darkTokens.text.primary : '#1a1a1a'};}
.wfp-kbm-badge{display:inline-block;padding:1px 7px;border-radius:9px;font-size:11px;background:${isDark ? darkTokens.surface.bgActive : '#f0f0f0'};color:${isDark ? darkTokens.text.secondary : '#616161'};}
.wfp-kbm-badge--ok{background:${isDark ? 'rgba(129,199,132,0.18)' : '#e8f5e9'};color:${isDark ? '#81c784' : '#2e7d32'};}
.wfp-kbm-badge--bad{background:${isDark ? 'rgba(255,107,107,0.15)' : '#fdecea'};color:${isDark ? '#ff6b6b' : '#c62828'};}
.wfp-kbm-docitem{display:flex;align-items:center;gap:8px;padding:6px 0;font-size:12px;color:${isDark ? darkTokens.text.primary : '#333333'};border-bottom:1px solid ${isDark ? darkTokens.surface.divider : '#f0f0f0'};}
.wfp-kbm-docitem:last-child{border-bottom:none;}
`}</style>
      <h3 className="wfp-kbm-detail-title">
        知识库详情：{detail?.name ?? knowledgeBaseId}
      </h3>
      {onBack && (
        <button className="wfp-kbm-btn" onClick={onBack} data-testid="back-to-list">
          返回列表
        </button>
      )}
      {error && <div className="wfp-kbm-error" data-testid="kb-detail-error">{error}</div>}

      {detail && (
        <div className="wfp-kbm-card" data-testid="kb-detail-summary">
          <p>标识：{detail.knowledge_base_id}</p>
          <p>名称：{detail.name}</p>
          <p>缺省分片策略：{detail.default_chunk_strategy ?? '（导入时选择）'}</p>
          <p>文档数：{detail.document_count}，分片数：{detail.chunk_count}</p>
        </div>
      )}

      {/* 绑定摘要（只读，零凭据：无服务地址字段，INV-D10） */}
      {detail && (
        <div className="wfp-kbm-card" data-testid="binding-summary">
          <h4 className="wfp-kbm-card-title">Embedding 绑定（只读）</h4>
          <p>
            模型标识：{detail.embedding_model.model_identifier}
            <span className="wfp-kbm-badge" style={{ marginLeft: 8 }}>
              {detail.embedding_model.mode}
            </span>
          </p>
          <p>允许降级兜底：{detail.embedding_model.allow_degraded_fallback ? '是' : '否'}</p>
          {detail.embedding_model.timeout_seconds !== undefined && (
            <p>超时（秒）：{detail.embedding_model.timeout_seconds}</p>
          )}
          {detail.embedding_model.device !== undefined && (
            <p>运行设备：{detail.embedding_model.device}</p>
          )}
        </div>
      )}

      {/* 集合诊断（只读透传：ok=false 不崩溃） */}
      {diagnosis && (
        <div className="wfp-kbm-card" data-testid="diagnosis-panel">
          <h4 className="wfp-kbm-card-title">向量集合诊断</h4>
          <p>
            状态：
            <span
              className={`wfp-kbm-badge ${diagnosis.ok ? 'wfp-kbm-badge--ok' : 'wfp-kbm-badge--bad'}`}
              data-testid="diagnosis-ok"
            >
              {diagnosis.ok ? '正常' : '异常'}
            </span>
          </p>
          <p>集合标识：{diagnosis.collection_id}</p>
          <p>记录数：{diagnosis.record_count}，格式版本：{diagnosis.format_version}</p>
          <p>完整性：{diagnosis.integrity_check}</p>
          <p>维度一致：{diagnosis.dimension_consistent ? '是' : '否'}</p>
          {diagnosis.declared_dimension !== undefined && (
            <p>声明维度：{diagnosis.declared_dimension}</p>
          )}
          {diagnosis.issues.length > 0 && (
            <div data-testid="diagnosis-issues">
              {diagnosis.issues.map((issue, index) => (
                <p key={index}>问题：{issue}</p>
              ))}
            </div>
          )}
        </div>
      )}

      {/* 导入向导（确认成功后登记文档） */}
      <ImportWizard knowledgeBaseId={knowledgeBaseId} onImported={handleImported} />

      {/* 会话内文档列表（导入确认后可进入分片编辑） */}
      {documents.length > 0 && (
        <div className="wfp-kbm-card" data-testid="doc-list">
          <h4 className="wfp-kbm-card-title">已导入文档</h4>
          {documents.map((documentId) => (
            <div key={documentId} className="wfp-kbm-docitem" data-testid={`doc-item-${documentId}`}>
              <span>文档：{documentId}</span>
              <button
                className="wfp-kbm-btn"
                onClick={() =>
                  setEditingDocumentId((prev) => (prev === documentId ? null : documentId))
                }
                data-testid={`edit-chunks-${documentId}`}
              >
                {editingDocumentId === documentId ? '收起分片编辑' : '编辑分片'}
              </button>
            </div>
          ))}
        </div>
      )}

      {/* 分片编辑器（草稿/确认分离） */}
      {editingDocumentId !== null && (
        <ChunkEditor knowledgeBaseId={knowledgeBaseId} documentId={editingDocumentId} />
      )}
    </div>
  );
}
