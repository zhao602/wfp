// =============================================================================
// 知识库面板（web/src/knowledge_base/KnowledgeBasePanel.tsx）
//
// 职责：向量知识库列表 + 新建（T8.4 CHG-25，DC-09）。
// 设计意图：
//   1. 列表渲染零凭据（绑定模型摘要仅 mode + 模型标识，INV-D10）；
//   2. 新建走 EmbeddingBindingControl（候选选择 / 空候选手工填写）；
//   3. 空态与错误降级：拉取失败 → 错误条且页面可渲染；
//   4. 选中行进入 KnowledgeBaseDetailPanel（详情/诊断/导入/分片编辑）。
// =============================================================================

import { useCallback, useEffect, useState } from 'react';
import { useThemeMode, darkTokens } from '../theme/themeMode';
import { kbManagementApiClient } from '../api/kbManagementApiClient';
import { ApiError } from '../api/apiError';
import {
  EmbeddingBindingControl,
} from './EmbeddingBindingControl';
import type { EmbeddingBindingDraft } from './EmbeddingBindingControl';
import { KnowledgeBaseDetailPanel } from './KnowledgeBaseDetailPanel';
import type {
  ChunkStrategy,
  KnowledgeBaseListResponse,
} from './kbManagementTypes';

/** 新建表单初始绑定草稿（手工填写兜底默认值） */
const INITIAL_BINDING_DRAFT: EmbeddingBindingDraft = {
  mode: 'local',
  model_identifier: '',
  allow_degraded_fallback: false,
};

/** 分片策略选项（新建时缺省策略可选；空值 = 不设置） */
const STRATEGY_OPTIONS: Array<{ value: string; label: string }> = [
  { value: '', label: '（导入时选择）' },
  { value: 'paragraph', label: '段落分块' },
  { value: 'fixed_length', label: '定长分块' },
  { value: 'recursive', label: '递归分块' },
  { value: 'structural', label: '结构分块' },
  { value: 'semantic', label: '语义分块' },
];

/** 向量知识库管理面板（后台菜单入口） */
export function KnowledgeBasePanel(): JSX.Element {
  const themeMode = useThemeMode();
  const isDark = themeMode === 'dark';
  const [list, setList] = useState<KnowledgeBaseListResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedKbId, setSelectedKbId] = useState<string | null>(null);

  // 新建表单状态
  const [creating, setCreating] = useState(false);
  const [newKbId, setNewKbId] = useState('');
  const [newName, setNewName] = useState('');
  const [newStrategy, setNewStrategy] = useState('');
  const [bindingDraft, setBindingDraft] = useState<EmbeddingBindingDraft>(
    INITIAL_BINDING_DRAFT,
  );
  const [createError, setCreateError] = useState<string | null>(null);

  const loadList = useCallback(async () => {
    setLoading(true);
    try {
      const response = await kbManagementApiClient.listKnowledgeBases();
      setList(response);
      setError(null);
    } catch (err) {
      // 错误降级：错误条可见 + 既有数据保留（页面不崩溃）
      if (err instanceof ApiError) {
        setError(`知识库列表加载失败（${err.code}）：${err.message}`);
      } else {
        setError(err instanceof Error ? err.message : '知识库列表加载失败');
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadList();
  }, [loadList]);

  /** 新建知识库（绑定模型单值必填，AC-40） */
  const handleCreate = async () => {
    setCreateError(null);
    setCreating(true);
    try {
      await kbManagementApiClient.createKnowledgeBase({
        knowledge_base_id: newKbId.trim(),
        name: newName.trim(),
        embedding_model: {
          mode: bindingDraft.mode,
          model_identifier: bindingDraft.model_identifier.trim(),
          allow_degraded_fallback: bindingDraft.allow_degraded_fallback,
        },
        ...(newStrategy !== ''
          ? { default_chunk_strategy: newStrategy as ChunkStrategy }
          : {}),
      });
      setNewKbId('');
      setNewName('');
      setNewStrategy('');
      setBindingDraft(INITIAL_BINDING_DRAFT);
      setCreating(false);
      loadList();
    } catch (err) {
      setCreating(false);
      if (err instanceof ApiError) {
        setCreateError(`创建失败（${err.code}）：${err.message}`);
      } else {
        setCreateError(err instanceof Error ? err.message : '创建失败');
      }
    }
  };

  // 详情视图（选中行）
  if (selectedKbId !== null) {
    return (
      <KnowledgeBaseDetailPanel
        knowledgeBaseId={selectedKbId}
        onBack={() => {
          setSelectedKbId(null);
          loadList();
        }}
      />
    );
  }

  const items = list?.items ?? [];
  const canCreate =
    newKbId.trim() !== '' &&
    newName.trim() !== '' &&
    bindingDraft.model_identifier.trim() !== '';

  return (
    <div className="wfp-kbm-panel" data-testid="kb-panel">
      <style>{`
.wfp-kbm-panel{padding:20px 24px;background:${isDark ? darkTokens.surface.bg : '#ffffff'};border:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};border-radius:8px;}
.wfp-kbm-title{font-size:15px;font-weight:600;margin:0 0 16px;padding-bottom:12px;border-bottom:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};color:${isDark ? darkTokens.text.primary : '#1a1a1a'};}
.wfp-kbm-error{margin-bottom:16px;padding:12px 16px;border-radius:8px;border:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};border-left:3px solid ${isDark ? darkTokens.danger : '#d32f2f'};background:${isDark ? 'rgba(255,107,107,0.08)' : '#fdecea'};font-size:12px;color:${isDark ? darkTokens.danger : '#d32f2f'};}
.wfp-kbm-table{width:100%;border-collapse:separate;border-spacing:0;font-size:12px;border:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};border-radius:8px;overflow:hidden;}
.wfp-kbm-table thead th{background:${isDark ? darkTokens.surface.bgHover : '#f5f5f5'};font-weight:600;text-align:left;padding:8px 12px;border-bottom:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};color:${isDark ? darkTokens.text.primary : '#1a1a1a'};}
.wfp-kbm-table tbody td{padding:7px 12px;border-bottom:1px solid ${isDark ? darkTokens.surface.divider : '#eee'};color:${isDark ? darkTokens.text.primary : '#1a1a1a'};}
.wfp-kbm-table tbody tr:last-child td{border-bottom:none;}
.wfp-kbm-table tbody tr:hover{background:${isDark ? darkTokens.surface.bgHover : '#fafafa'};}
.wfp-kbm-btn{font-size:13px;font-weight:500;font-family:inherit;padding:6px 14px;border-radius:6px;cursor:pointer;outline:none;border:1px solid ${isDark ? darkTokens.surface.border : '#d0d0d0'};background:${isDark ? darkTokens.surface.bg : '#ffffff'};color:${isDark ? darkTokens.text.primary : '#1a1a1a'};transition:background-color 120ms ease,border-color 120ms ease;}
.wfp-kbm-btn:hover{background:${isDark ? darkTokens.surface.bgHover : '#f5f5f5'};border-color:${isDark ? darkTokens.surface.borderStrong : '#b8b8b8'};}
.wfp-kbm-btn:disabled{opacity:0.5;cursor:not-allowed;}
.wfp-kbm-btn--primary{background:${isDark ? darkTokens.text.primary : '#1a1a1a'};color:${isDark ? darkTokens.surface.bg : '#ffffff'};border-color:${isDark ? darkTokens.text.primary : '#1a1a1a'};}
.wfp-kbm-btn--primary:hover{background:${isDark ? darkTokens.text.secondary : '#333333'};}
.wfp-kbm-badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:500;background:${isDark ? darkTokens.surface.bgActive : '#f0f0f0'};color:${isDark ? darkTokens.text.secondary : '#616161'};}
.wfp-kbm-empty{text-align:center;padding:24px 16px;color:${isDark ? darkTokens.text.hint : '#9e9e9e'};font-size:13px;}
.wfp-kbm-create{margin-top:16px;padding:16px;background:${isDark ? darkTokens.surface.bgHover : '#fafafa'};border:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};border-radius:8px;}
.wfp-kbm-create-title{font-size:14px;font-weight:600;margin:0 0 12px;color:${isDark ? darkTokens.text.primary : '#1a1a1a'};}
.wfp-kbm-field{display:block;margin-bottom:12px;font-size:12px;font-weight:500;color:${isDark ? darkTokens.text.secondary : '#616161'};}
.wfp-kbm-field > input, .wfp-kbm-field > select{display:block;margin-top:6px;}
.wfp-kbm-input{width:100%;box-sizing:border-box;padding:7px 10px;font-size:13px;font-weight:500;font-family:inherit;color:${isDark ? darkTokens.text.primary : '#333333'};background-color:${isDark ? darkTokens.surface.bg : '#ffffff'};border:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};border-radius:5px;outline:none;}
.wfp-kbm-input:focus{border-color:${isDark ? darkTokens.surface.borderStrong : '#bdbdbd'};}
.wfp-kbm-select{padding:7px 10px;font-size:13px;font-family:inherit;color:${isDark ? darkTokens.text.primary : '#333333'};background-color:${isDark ? darkTokens.surface.bg : '#ffffff'};border:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};border-radius:5px;outline:none;}
.wfp-kbm-create-actions{display:flex;gap:8px;align-items:center;margin-top:12px;}
`}</style>
      <h2 className="wfp-kbm-title">
        向量知识库管理（共 {list?.total ?? 0} 个知识库）
      </h2>
      {error && <div className="wfp-kbm-error" data-testid="kb-panel-error">{error}</div>}

      {/* 知识库列表（零凭据：绑定摘要仅模式 + 标识） */}
      <table className="wfp-kbm-table" data-testid="kb-list-table">
        <thead>
          <tr>
            <th>标识</th>
            <th>名称</th>
            <th>绑定模型</th>
            <th>文档数</th>
            <th>分片数</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {items.length === 0 ? (
            <tr>
              <td colSpan={6} className="wfp-kbm-empty" data-testid="kb-empty">
                {loading ? '加载中…' : '暂无知识库，请在下方创建'}
              </td>
            </tr>
          ) : (
            items.map((kb) => (
              <tr key={kb.knowledge_base_id} data-testid={`kb-row-${kb.knowledge_base_id}`}>
                <td>{kb.knowledge_base_id}</td>
                <td>{kb.name}</td>
                <td>
                  <span className="wfp-kbm-badge">{kb.embedding_model.mode}</span>{' '}
                  {kb.embedding_model.model_identifier}
                </td>
                <td>{kb.document_count}</td>
                <td>{kb.chunk_count}</td>
                <td>
                  <button
                    className="wfp-kbm-btn"
                    onClick={() => setSelectedKbId(kb.knowledge_base_id)}
                    data-testid={`view-detail-${kb.knowledge_base_id}`}
                  >
                    查看
                  </button>
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>

      {/* 新建知识库（EmbeddingBindingControl：候选选择 / 空候选手工填写） */}
      <div className="wfp-kbm-create" data-testid="create-form">
        <h3 className="wfp-kbm-create-title">新建知识库</h3>
        {createError && (
          <div className="wfp-kbm-error" data-testid="create-error">{createError}</div>
        )}
        <label className="wfp-kbm-field">
          知识库标识（唯一，创建后不可改）:
          <input
            className="wfp-kbm-input"
            type="text"
            placeholder="如: product_docs"
            value={newKbId}
            onChange={(e) => setNewKbId(e.target.value)}
            data-testid="kb-id-input"
          />
        </label>
        <label className="wfp-kbm-field">
          名称:
          <input
            className="wfp-kbm-input"
            type="text"
            placeholder="如: 产品文档库"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            data-testid="kb-name-input"
          />
        </label>
        <label className="wfp-kbm-field">
          缺省分片策略:
          <select
            className="wfp-kbm-select"
            value={newStrategy}
            onChange={(e) => setNewStrategy(e.target.value)}
            data-testid="kb-strategy-select"
          >
            {STRATEGY_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
        <EmbeddingBindingControl value={bindingDraft} onChange={setBindingDraft} />
        <div className="wfp-kbm-create-actions">
          <button
            className="wfp-kbm-btn wfp-kbm-btn--primary"
            disabled={!canCreate || creating}
            onClick={handleCreate}
            data-testid="create-submit-button"
          >
            {creating ? '创建中…' : '创建知识库'}
          </button>
        </div>
      </div>
    </div>
  );
}
