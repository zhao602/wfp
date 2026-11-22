// =============================================================================
// 分片编辑器（web/src/knowledge_base/ChunkEditor.tsx）
//
// 职责：分片列表（分页）+ 合并/拆分/编辑 + 草稿/确认分离 + 冲突提示
// （T8.7 CHG-28，AC-30~AC-35 / AC-38 / INV-D19）。
// 设计意图：
//   1. 分页服务端承载（page/page_size），大量分片不崩溃（AC-30）；
//   2. 草稿/确认两段分离：调整仅生成草稿（本地预览），确认才触发向量重算；
//      草稿未确认不影响检索（INV-D19，界面明示）；
//   3. 客户端前置校验：合并相邻校验 / 拆分点区间与递增校验 / 编辑非空与
//      超长拦截（AC-32~AC-35）；
//   4. 服务端版本冲突（DRAFT_VERSION_CONFLICT）→ 冲突提示 + 引导刷新。
// =============================================================================

import { useCallback, useEffect, useState } from 'react';
import { useThemeMode, darkTokens } from '../theme/themeMode';
import { kbManagementApiClient } from '../api/kbManagementApiClient';
import { ApiError } from '../api/apiError';
import type {
  BulkAnnotateInput,
  ChunkDraftConfirmResponse,
  ChunkDraftResponse,
  ChunkListResponse,
  ChunkOperation,
} from './kbManagementTypes';
import {
  formatPublishedAt,
  resolveChunkAnnotations,
} from './kbManagementTypes';

/** 已确认分片的基线草稿版本（确认导入后草稿版本恒为 "v1"） */
const BASE_DRAFT_VERSION = 'v1';

/** 单页分片数（服务端分页） */
const PAGE_SIZE = 20;

/** 分片编辑器属性 */
export interface ChunkEditorProps {
  knowledgeBaseId: string;
  documentId: string;
}

/** 解析拆分点输入：非法返回 null */
function parseSplitPoints(raw: string): number[] | null {
  const parts = raw
    .split(',')
    .map((part) => part.trim())
    .filter((part) => part !== '');
  if (parts.length === 0) return null;
  const points: number[] = [];
  for (const part of parts) {
    const point = Number(part);
    if (!Number.isInteger(point)) return null;
    points.push(point);
  }
  return points;
}

/** 标注展示（来源/发布时间/自定义；published_at=null → "未知"，超长确定性截断标注） */
function renderAnnotations(source: Record<string, unknown> | undefined): string {
  const annotations = resolveChunkAnnotations(source);
  if (!annotations) return '—';
  const parts = [
    `来源：${annotations.origin}`,
    `发布时间：${formatPublishedAt(annotations)}`,
  ];
  const custom = annotations.custom;
  if (custom) {
    for (const [name, value] of Object.entries(custom)) {
      const text = typeof value === 'string' ? value : JSON.stringify(value);
      const shown = text.length > 30 ? `${text.slice(0, 30)}…` : text;
      parts.push(`${name}：${shown}`);
    }
  }
  return parts.join('；');
}

/** 分片编辑器 */
export function ChunkEditor({
  knowledgeBaseId,
  documentId,
}: ChunkEditorProps): JSX.Element {
  const themeMode = useThemeMode();
  const isDark = themeMode === 'dark';
  const [chunkList, setChunkList] = useState<ChunkListResponse | null>(null);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conflictMessage, setConflictMessage] = useState<string | null>(null);

  // 合并：多选相邻分片
  const [mergeSelection, setMergeSelection] = useState<string[]>([]);
  const [mergeError, setMergeError] = useState<string | null>(null);

  // 拆分：目标分片 + 拆分点
  const [splitTargetId, setSplitTargetId] = useState('');
  const [splitPointsInput, setSplitPointsInput] = useState('');
  const [splitError, setSplitError] = useState<string | null>(null);

  // 编辑：目标分片 + 新文本
  const [editTargetId, setEditTargetId] = useState('');
  const [editText, setEditText] = useState('');
  const [editError, setEditError] = useState<string | null>(null);

  // 草稿（未确认不影响检索，INV-D19）
  const [draft, setDraft] = useState<ChunkDraftResponse | null>(null);
  const [draftResult, setDraftResult] = useState<ChunkDraftConfirmResponse | null>(null);
  const [applying, setApplying] = useState(false);
  const [confirming, setConfirming] = useState(false);
  // vkb_doc_annotation：一键标注 / 统一小写状态
  const [bulkOrigin, setBulkOrigin] = useState('');
  const [bulkPublishedAt, setBulkPublishedAt] = useState('');
  const [bulkRunning, setBulkRunning] = useState(false);
  const [bulkMessage, setBulkMessage] = useState<string | null>(null);
  const [confirmLowercase, setConfirmLowercase] = useState(false);

  const loadChunks = useCallback(
    async (targetPage: number) => {
      setLoading(true);
      try {
        const response = await kbManagementApiClient.listChunks(
          knowledgeBaseId,
          documentId,
          targetPage,
          PAGE_SIZE,
          true,
        );
        setChunkList(response);
        setPage(response.page);
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : '分片列表加载失败');
      } finally {
        setLoading(false);
      }
    },
    [knowledgeBaseId, documentId],
  );

  useEffect(() => {
    loadChunks(1);
  }, [loadChunks]);

  const items = chunkList?.items ?? [];
  const totalPages =
    chunkList !== null && chunkList.total > 0
      ? Math.ceil(chunkList.total / chunkList.page_size)
      : 1;

  /** 重置操作区局部校验信息 */
  const resetOperationErrors = () => {
    setMergeError(null);
    setSplitError(null);
    setEditError(null);
  };

  /** 提交调整草稿（单操作一轮；草稿未确认不影响检索） */
  const applyDraft = async (operations: ChunkOperation[]) => {
    setError(null);
    setConflictMessage(null);
    setApplying(true);
    try {
      const response = await kbManagementApiClient.applyChunkDraft(
        knowledgeBaseId,
        documentId,
        { base_version: BASE_DRAFT_VERSION, operations },
      );
      setDraft(response);
      resetOperationErrors();
      setMergeSelection([]);
      setSplitPointsInput('');
      setEditText('');
    } catch (err) {
      if (err instanceof ApiError) {
        setError(`草稿提交失败（${err.code}）：${err.message}`);
      } else {
        setError(err instanceof Error ? err.message : '草稿提交失败');
      }
    } finally {
      setApplying(false);
    }
  };

  /** 合并：相邻性校验（AC-32：仅允许相邻分片合并） */
  const handleMerge = () => {
    if (mergeSelection.length < 2) {
      setMergeError('请至少选择 2 个分片');
      return;
    }
    const selectedIndexList = mergeSelection
      .map((chunkId) => items.find((item) => item.chunk_id === chunkId)?.index)
      .filter((index): index is number => index !== undefined)
      .sort((a, b) => a - b);
    if (selectedIndexList.length !== mergeSelection.length) {
      setMergeError('所选分片不在当前页，请先翻页取消选择');
      return;
    }
    for (let i = 1; i < selectedIndexList.length; i += 1) {
      if (selectedIndexList[i] !== selectedIndexList[i - 1] + 1) {
        setMergeError('仅允许合并相邻分片（索引必须连续）');
        return;
      }
    }
    // 按索引次序提交（与后端相邻性约定一致）
    const orderedIds = selectedIndexList
      .map((index) => items.find((item) => item.index === index)?.chunk_id)
      .filter((chunkId): chunkId is string => chunkId !== undefined);
    applyDraft([{ op: 'merge', chunk_ids: orderedIds }]);
  };

  /** 拆分：拆分点区间与严格递增校验（AC-33） */
  const handleSplit = () => {
    const target = items.find((item) => item.chunk_id === splitTargetId);
    if (!target) {
      setSplitError('请选择拆分目标分片');
      return;
    }
    const points = parseSplitPoints(splitPointsInput);
    if (points === null) {
      setSplitError('拆分点必须为逗号分隔的整数');
      return;
    }
    for (let i = 0; i < points.length; i += 1) {
      if (points[i] <= 0 || points[i] >= target.text.length) {
        setSplitError(`拆分点必须位于 (0, ${target.text.length}) 区间`);
        return;
      }
      if (i > 0 && points[i] <= points[i - 1]) {
        setSplitError('拆分点必须严格递增');
        return;
      }
    }
    applyDraft([{ op: 'split', chunk_id: splitTargetId, split_points: points }]);
  };

  /** 编辑：非空与超长拦截（AC-34：非空 ≤ 2000 字符） */
  const handleEdit = () => {
    if (!editTargetId) {
      setEditError('请选择编辑目标分片');
      return;
    }
    const trimmed = editText.trim();
    if (trimmed === '') {
      setEditError('分片文本不能为空');
      return;
    }
    if (trimmed.length > 2000) {
      setEditError('分片文本不能超过 2000 字符');
      return;
    }
    applyDraft([{ op: 'edit', chunk_id: editTargetId, text: trimmed }]);
  };

  /** 确认草稿：触发受影响分片向量重算（AC-38） */
  const handleConfirmDraft = async () => {
    if (!draft) return;
    setError(null);
    setConflictMessage(null);
    setConfirming(true);
    try {
      const result = await kbManagementApiClient.confirmChunkDraft(
        knowledgeBaseId,
        documentId,
        { draft_version: draft.draft_version },
      );
      setDraftResult(result);
      setDraft(null);
      loadChunks(1);
    } catch (err) {
      if (err instanceof ApiError && err.code === 'DRAFT_VERSION_CONFLICT') {
        setConflictMessage('草稿版本冲突：分片已被其他操作修改，请刷新后重试');
      } else if (err instanceof ApiError) {
        setError(`草稿确认失败（${err.code}）：${err.message}`);
      } else {
        setError(err instanceof Error ? err.message : '草稿确认失败');
      }
    } finally {
      setConfirming(false);
    }
  };

  /** 放弃草稿（仅本地丢弃；服务端草稿可被后续同版本提交覆盖） */
  const handleDiscardDraft = () => {
    setDraft(null);
    setDraftResult(null);
  };

  /** 一键标注所有切片（vkb_doc_annotation / AC-22） */
  const handleBulkAnnotate = async () => {
    setBulkMessage(null);
    setBulkRunning(true);
    try {
      const request: BulkAnnotateInput = {
        ...(bulkOrigin.trim() !== '' ? { origin: bulkOrigin.trim() } : {}),
        ...(bulkPublishedAt.trim() !== ''
          ? { published_at: bulkPublishedAt.trim() }
          : {}),
      };
      const report = await kbManagementApiClient.bulkAnnotate(
        knowledgeBaseId,
        documentId,
        request,
      );
      setBulkMessage(
        `一键标注完成：${report.status}（目标 ${report.targeted_count}，更新 ${report.updated_count}）`,
      );
      loadChunks(page);
    } catch (err) {
      setBulkMessage(
        err instanceof ApiError
          ? `一键标注失败（${err.code}）：${err.message}`
          : '一键标注失败',
      );
    } finally {
      setBulkRunning(false);
    }
  };

  /** 一键统一小写入口（先弹不可逆二次确认，AC-29） */
  const handleLowercaseClick = () => {
    setBulkMessage(null);
    setConfirmLowercase(true);
  };

  /** 确认执行一键统一小写（仅标注值，不可逆） */
  const handleLowercaseConfirm = async () => {
    setConfirmLowercase(false);
    setBulkRunning(true);
    try {
      const report = await kbManagementApiClient.lowercaseAnnotations(
        knowledgeBaseId,
        documentId,
        { scope: 'annotations', confirmed: true },
      );
      setBulkMessage(
        `统一小写完成：${report.status}（转换值 ${report.converted_values}，受影响切片 ${report.converted_chunks}）`,
      );
      loadChunks(page);
    } catch (err) {
      setBulkMessage(
        err instanceof ApiError
          ? `统一小写失败（${err.code}）：${err.message}`
          : '统一小写失败',
      );
    } finally {
      setBulkRunning(false);
    }
  };

  return (
    <div className="wfp-kbm-editor" data-testid="chunk-editor">
      <style>{`
.wfp-kbm-editor{padding:16px;background:${isDark ? darkTokens.surface.bgHover : '#fafafa'};border:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};border-radius:8px;}
.wfp-kbm-editor-title{font-size:14px;font-weight:600;margin:0 0 12px;color:${isDark ? darkTokens.text.primary : '#1a1a1a'};}
.wfp-kbm-editor p{font-size:12px;margin:6px 0;color:${isDark ? darkTokens.text.secondary : '#616161'};}
.wfp-kbm-error{margin-bottom:12px;padding:10px 14px;border-radius:8px;border:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};border-left:3px solid ${isDark ? darkTokens.danger : '#d32f2f'};background:${isDark ? 'rgba(255,107,107,0.08)' : '#fdecea'};font-size:12px;color:${isDark ? darkTokens.danger : '#d32f2f'};}
.wfp-kbm-conflict{margin-bottom:12px;padding:10px 14px;border-radius:8px;border:1px solid ${isDark ? darkTokens.surface.divider : '#ffe082'};border-left:3px solid ${isDark ? '#ffd54f' : '#f57f17'};background:${isDark ? 'rgba(255,213,79,0.10)' : '#fff8e1'};font-size:12px;color:${isDark ? '#ffd54f' : '#f57f17'};}
.wfp-kbm-table{width:100%;border-collapse:separate;border-spacing:0;font-size:12px;border:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};border-radius:8px;overflow:hidden;}
.wfp-kbm-table thead th{background:${isDark ? darkTokens.surface.bgActive : '#f5f5f5'};font-weight:600;text-align:left;padding:7px 10px;border-bottom:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};color:${isDark ? darkTokens.text.primary : '#1a1a1a'};}
.wfp-kbm-table tbody td{padding:6px 10px;border-bottom:1px solid ${isDark ? darkTokens.surface.divider : '#eee'};color:${isDark ? darkTokens.text.primary : '#1a1a1a'};}
.wfp-kbm-table tbody tr:last-child td{border-bottom:none;}
.wfp-kbm-table tbody tr:hover{background:${isDark ? darkTokens.surface.bgHover : '#fafafa'};}
.wfp-kbm-btn{font-size:12px;font-weight:500;font-family:inherit;padding:5px 12px;border-radius:5px;cursor:pointer;outline:none;border:1px solid ${isDark ? darkTokens.surface.border : '#d0d0d0'};background:${isDark ? darkTokens.surface.bg : '#ffffff'};color:${isDark ? darkTokens.text.primary : '#1a1a1a'};}
.wfp-kbm-btn:hover{background:${isDark ? darkTokens.surface.bgHover : '#f5f5f5'};}
.wfp-kbm-btn:disabled{opacity:0.5;cursor:not-allowed;}
.wfp-kbm-btn--primary{background:${isDark ? darkTokens.text.primary : '#1a1a1a'};color:${isDark ? darkTokens.surface.bg : '#ffffff'};border-color:${isDark ? darkTokens.text.primary : '#1a1a1a'};}
.wfp-kbm-btn--primary:hover{background:${isDark ? darkTokens.text.secondary : '#333333'};}
.wfp-kbm-input,.wfp-kbm-select,.wfp-kbm-textarea{box-sizing:border-box;padding:6px 10px;font-size:12px;font-family:inherit;color:${isDark ? darkTokens.text.primary : '#333333'};background-color:${isDark ? darkTokens.surface.bg : '#ffffff'};border:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};border-radius:5px;outline:none;}
.wfp-kbm-textarea{width:100%;resize:vertical;line-height:1.5;}
.wfp-kbm-opbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:10px 0;}
.wfp-kbm-opsection{margin:10px 0;padding:10px;background:${isDark ? darkTokens.surface.bg : '#ffffff'};border:1px solid ${isDark ? darkTokens.surface.divider : '#eee'};border-radius:6px;}
.wfp-kbm-operror{font-size:12px;color:${isDark ? darkTokens.danger : '#d32f2f'};margin:6px 0;}
.wfp-kbm-pager{display:flex;gap:8px;align-items:center;margin:10px 0;font-size:12px;color:${isDark ? darkTokens.text.secondary : '#616161'};}
.wfp-kbm-draftbanner{margin:10px 0;padding:10px 14px;border-radius:6px;border:1px solid ${isDark ? darkTokens.surface.divider : '#ffe082'};border-left:3px solid ${isDark ? '#ffd54f' : '#f57f17'};background:${isDark ? 'rgba(255,213,79,0.10)' : '#fff8e1'};font-size:12px;color:${isDark ? '#ffd54f' : '#f57f17'};}
.wfp-kbm-draftlist{max-height:200px;overflow-y:auto;margin:8px 0;border:1px solid ${isDark ? darkTokens.surface.divider : '#eee'};border-radius:6px;background:${isDark ? darkTokens.surface.bg : '#ffffff'};}
.wfp-kbm-chunkitem{padding:7px 10px;font-size:12px;color:${isDark ? darkTokens.text.primary : '#333333'};border-bottom:1px solid ${isDark ? darkTokens.surface.divider : '#f0f0f0'};}
.wfp-kbm-chunkitem:last-child{border-bottom:none;}
.wfp-kbm-chunkitem-index{font-weight:600;margin-right:8px;color:${isDark ? darkTokens.text.secondary : '#616161'};}
.wfp-kbm-badge{display:inline-block;padding:1px 7px;border-radius:9px;font-size:11px;background:${isDark ? darkTokens.surface.bgActive : '#f0f0f0'};color:${isDark ? darkTokens.text.secondary : '#616161'};}
.wfp-kbm-result{padding:10px 14px;margin:8px 0;border-radius:6px;background:${isDark ? darkTokens.surface.bg : '#ffffff'};border:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};}
`}</style>
      <h3 className="wfp-kbm-editor-title">分片编辑（文档 {documentId}）</h3>
      {error && <div className="wfp-kbm-error" data-testid="chunk-editor-error">{error}</div>}
      {conflictMessage && (
        <div className="wfp-kbm-conflict" data-testid="conflict-message">
          {conflictMessage}
        </div>
      )}

      {draft ? (
        /* 草稿态：未确认不影响检索（INV-D19 界面明示） */
        <div data-testid="chunk-draft-panel">
          <div className="wfp-kbm-draftbanner" data-testid="draft-banner">
            草稿未确认不影响检索——当前调整为本地草稿（版本 {draft.draft_version}），确认后才触发向量重算
          </div>
          {draft.notices.map((notice, index) => (
            <p key={index}>提示：{notice}</p>
          ))}
          <div className="wfp-kbm-draftlist" data-testid="draft-chunk-list">
            {draft.chunks.map((chunk) => (
              <div key={chunk.chunk_id} className="wfp-kbm-chunkitem">
                <span className="wfp-kbm-chunkitem-index">#{chunk.index}</span>
                {chunk.text.length > 100 ? `${chunk.text.slice(0, 100)}…` : chunk.text}
              </div>
            ))}
          </div>
          <button
            className="wfp-kbm-btn wfp-kbm-btn--primary"
            disabled={confirming}
            onClick={handleConfirmDraft}
            data-testid="confirm-draft-button"
          >
            {confirming ? '确认中…' : '确认草稿（触发向量重算）'}
          </button>
          <button className="wfp-kbm-btn" onClick={handleDiscardDraft} data-testid="discard-draft-button">
            放弃草稿
          </button>
        </div>
      ) : (
        <>
          {draftResult && (
            <div className="wfp-kbm-result" data-testid="draft-result">
              <p>上一轮确认结果：{draftResult.status}</p>
              <p>重算 {draftResult.rewritten.length} 片，删除 {draftResult.deleted.length} 片</p>
              {draftResult.failed_at && <p>失败分片：{draftResult.failed_at}（{draftResult.reason}）</p>}
            </div>
          )}

          {/* 分片列表（服务端分页，AC-30） */}
          <table className="wfp-kbm-table" data-testid="chunk-list-table">
            <thead>
              <tr>
                <th>选择</th>
                <th>索引</th>
                <th>文本</th>
                <th>标注</th>
                <th>向量化</th>
              </tr>
            </thead>
            <tbody>
              {items.length === 0 ? (
                <tr>
                  <td colSpan={5}>
                    {loading ? '加载中…' : '暂无分片'}
                  </td>
                </tr>
              ) : (
                items.map((chunk) => (
                  <tr key={chunk.chunk_id} data-testid={`chunk-row-${chunk.chunk_id}`}>
                    <td>
                      <input
                        type="checkbox"
                        checked={mergeSelection.includes(chunk.chunk_id)}
                        onChange={(e) => {
                          setMergeSelection((prev) =>
                            e.target.checked
                              ? [...prev, chunk.chunk_id]
                              : prev.filter((id) => id !== chunk.chunk_id),
                          );
                        }}
                        data-testid={`chunk-checkbox-${chunk.chunk_id}`}
                      />
                    </td>
                    <td>#{chunk.index}</td>
                    <td>
                      {chunk.text.length > 60
                        ? `${chunk.text.slice(0, 60)}…`
                        : chunk.text}
                    </td>
                    <td data-testid={`chunk-annotations-${chunk.chunk_id}`}>
                      {renderAnnotations(chunk.source)}
                    </td>
                    <td>
                      <span className="wfp-kbm-badge">
                        {chunk.vectorized ? '已向量化' : '未向量化'}
                      </span>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>

          {/* vkb_doc_annotation：一键标注 / 一键统一小写（不可逆，二次确认） */}
          <div className="wfp-kbm-opsection" data-testid="annotation-section">
            <p>切片标注：一键标注当前文档全部切片</p>
            <div className="wfp-kbm-opbar">
              <input
                className="wfp-kbm-input"
                type="text"
                placeholder="标注-来源（必填）"
                value={bulkOrigin}
                onChange={(e) => setBulkOrigin(e.target.value)}
                data-testid="bulk-origin-input"
              />
              <input
                className="wfp-kbm-input"
                type="text"
                placeholder="发布时间（可选，留空=未知）"
                value={bulkPublishedAt}
                onChange={(e) => setBulkPublishedAt(e.target.value)}
                data-testid="bulk-published-at-input"
              />
              <button
                className="wfp-kbm-btn"
                disabled={bulkRunning}
                onClick={handleBulkAnnotate}
                data-testid="bulk-annotate-button"
              >
                {bulkRunning ? '标注中…' : '一键标注所有切片'}
              </button>
              <button
                className="wfp-kbm-btn wfp-kbm-btn--primary"
                disabled={bulkRunning}
                onClick={handleLowercaseClick}
                data-testid="lowercase-button"
              >
                一键统一小写（标注值）
              </button>
            </div>
            {bulkMessage && (
              <div className="wfp-kbm-result" data-testid="annotation-result">
                {bulkMessage}
              </div>
            )}
            {confirmLowercase && (
              <div className="wfp-kbm-draftbanner" data-testid="lowercase-confirm">
                <p>「一键统一小写」不可逆（原文大小写不可还原）。确认执行？</p>
                <div className="wfp-kbm-opbar">
                  <button
                    className="wfp-kbm-btn wfp-kbm-btn--primary"
                    onClick={handleLowercaseConfirm}
                    data-testid="lowercase-confirm-button"
                  >
                    确认执行（不可逆）
                  </button>
                  <button
                    className="wfp-kbm-btn"
                    onClick={() => setConfirmLowercase(false)}
                    data-testid="lowercase-cancel-button"
                  >
                    取消
                  </button>
                </div>
              </div>
            )}
          </div>

          {/* 分页导航（大量分片不崩溃） */}
          <div className="wfp-kbm-pager">
            <button
              className="wfp-kbm-btn"
              disabled={page <= 1 || loading}
              onClick={() => {
                setMergeSelection([]);
                loadChunks(page - 1);
              }}
              data-testid="prev-page-button"
            >
              上一页
            </button>
            <span data-testid="page-indicator">
              第 {page} / {totalPages} 页（共 {chunkList?.total ?? 0} 片）
            </span>
            <button
              className="wfp-kbm-btn"
              disabled={page >= totalPages || loading}
              onClick={() => {
                setMergeSelection([]);
                loadChunks(page + 1);
              }}
              data-testid="next-page-button"
            >
              下一页
            </button>
          </div>

          {/* 合并操作（相邻校验） */}
          <div className="wfp-kbm-opsection" data-testid="merge-section">
            <p>合并分片：勾选相邻分片后合并</p>
            <div className="wfp-kbm-opbar">
              <button
                className="wfp-kbm-btn"
                disabled={applying}
                onClick={handleMerge}
                data-testid="merge-button"
              >
                合并所选（{mergeSelection.length}）
              </button>
            </div>
            {mergeError && (
              <div className="wfp-kbm-operror" data-testid="merge-error">{mergeError}</div>
            )}
          </div>

          {/* 拆分操作（拆分点校验） */}
          <div className="wfp-kbm-opsection" data-testid="split-section">
            <p>拆分分片：选择目标分片并输入拆分点（字符偏移，逗号分隔）</p>
            <div className="wfp-kbm-opbar">
              <select
                className="wfp-kbm-select"
                value={splitTargetId}
                onChange={(e) => setSplitTargetId(e.target.value)}
                data-testid="split-target-select"
              >
                <option value="">选择目标分片</option>
                {items.map((chunk) => (
                  <option key={chunk.chunk_id} value={chunk.chunk_id}>
                    #{chunk.index}（{chunk.text.length} 字符）
                  </option>
                ))}
              </select>
              <input
                className="wfp-kbm-input"
                type="text"
                placeholder="如: 20,45"
                value={splitPointsInput}
                onChange={(e) => setSplitPointsInput(e.target.value)}
                data-testid="split-points-input"
              />
              <button
                className="wfp-kbm-btn"
                disabled={applying}
                onClick={handleSplit}
                data-testid="split-button"
              >
                拆分
              </button>
            </div>
            {splitError && (
              <div className="wfp-kbm-operror" data-testid="split-error">{splitError}</div>
            )}
          </div>

          {/* 编辑操作（非空/超长拦截） */}
          <div className="wfp-kbm-opsection" data-testid="edit-section">
            <p>编辑分片文本：选择目标分片并输入新文本</p>
            <div className="wfp-kbm-opbar">
              <select
                className="wfp-kbm-select"
                value={editTargetId}
                onChange={(e) => {
                  setEditTargetId(e.target.value);
                  const target = items.find((item) => item.chunk_id === e.target.value);
                  setEditText(target ? target.text : '');
                }}
                data-testid="edit-target-select"
              >
                <option value="">选择目标分片</option>
                {items.map((chunk) => (
                  <option key={chunk.chunk_id} value={chunk.chunk_id}>
                    #{chunk.index}
                  </option>
                ))}
              </select>
            </div>
            <textarea
              className="wfp-kbm-textarea"
              rows={3}
              value={editText}
              onChange={(e) => setEditText(e.target.value)}
              data-testid="edit-textarea"
            />
            <div className="wfp-kbm-opbar">
              <button
                className="wfp-kbm-btn"
                disabled={applying}
                onClick={handleEdit}
                data-testid="edit-button"
              >
                保存编辑（生成草稿）
              </button>
            </div>
            {editError && (
              <div className="wfp-kbm-operror" data-testid="edit-error">{editError}</div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
