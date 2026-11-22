// =============================================================================
// Agent 市场面板（web/src/market/AgentMarketPanel.tsx）
//
// 职责：列表 + 关键字/分类检索 + 选中详情区 + "模板详情"（TemplateDetailView）+
//       "应用模板"入口 + 内置/已注册来源徽章。
// 设计意图（DC-08/DC-06）：
//   1. 挂载拉取条目清单（E-1）；拉取失败 → 错误条且页面可渲染，不清空既有选择（AC-20/AC-24）；
//   2. "应用模板"经 marketApiClient 取得模板详情后走 onApplyTemplate 回调（通道 1）；
//      回调缺失 → 降级复制预设 JSON（通道 2，T16.6）；
//   3. 复用 ContributionReviewBoard 的 wfp-crb-* 同类内联样式范式（wfp-amk-* 前缀，AC-23）。
// =============================================================================

import { useEffect, useState, useCallback } from 'react';
import type { AgentMarketEntry, AgentTemplate, MarketEntryStatus } from './marketTypes';
import {
  listAgentMarketEntries,
  listAgentMarketCategories,
  getAgentTemplate,
} from './marketApiClient';
import { TemplateDetailView } from './TemplateDetailView';
import { useThemeMode, darkTokens } from '../theme/themeMode';

/** Agent 市场面板属性（DC-08） */
export interface AgentMarketPanelProps {
  /** 应用模板回调（可选，DC-06 通道 1；缺失时面板降级复制预设 JSON） */
  onApplyTemplate?: (t: AgentTemplate, targetNodeId?: string | null) => void;
}

/** 市场条目状态徽章色映射（DC-10：builtin→teal / available→green / draft→gray / pending_review→yellow） */
const MARKET_STATUS_COLORS: Record<MarketEntryStatus, string> = {
  builtin: 'teal',
  available: 'green',
  draft: 'gray',
  pending_review: 'yellow',
};

/** Agent 市场面板：条目检索 + 详情 + 模板详情/应用入口 */
export function AgentMarketPanel({ onApplyTemplate }: AgentMarketPanelProps): JSX.Element {
  const [entries, setEntries] = useState<AgentMarketEntry[]>([]);
  const [total, setTotal] = useState<number>(0);
  const [categories, setCategories] = useState<Array<{ category: string; count: number }>>([]);
  const [keywordInput, setKeywordInput] = useState('');
  const [keyword, setKeyword] = useState('');
  const [category, setCategory] = useState('');
  const [selectedEntry, setSelectedEntry] = useState<AgentMarketEntry | null>(null);
  const [detailTemplate, setDetailTemplate] = useState<AgentTemplate | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [applyHint, setApplyHint] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const themeMode = useThemeMode();
  const isDark = themeMode === 'dark';

  /** 拉取条目清单：失败 → 错误条且不清空既有数据（AC-24 规则 2，对齐 crb.loadData 范式） */
  const loadEntries = useCallback(async () => {
    try {
      const response = await listAgentMarketEntries({
        q: keyword || undefined,
        category: category || undefined,
      });
      setEntries(response.entries);
      setTotal(response.total);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载失败');
    }
  }, [keyword, category]);

  useEffect(() => {
    loadEntries();
  }, [loadEntries]);

  /** 分类计数：挂载拉取一次，失败静默回落空分类（不阻塞主列表） */
  useEffect(() => {
    listAgentMarketCategories()
      .then((response) => setCategories(response.categories))
      .catch(() => setCategories([]));
  }, []);

  /** 选中条目：进入详情区（保留既有列表与检索状态） */
  const handleSelectEntry = (entry: AgentMarketEntry) => {
    setSelectedEntry(entry);
    setDetailTemplate(null);
    setDetailError(null);
    setApplyHint(null);
  };

  /** "模板详情"：经 E-6 取得模板详情后交由 TemplateDetailView 分段渲染 */
  const handleShowTemplateDetail = async () => {
    if (!selectedEntry?.preset_ref) return;
    try {
      const template = await getAgentTemplate(selectedEntry.preset_ref);
      setDetailTemplate(template);
      setDetailError(null);
    } catch (err) {
      setDetailError(err instanceof Error ? err.message : '模板详情加载失败');
    }
  };

  /** "应用模板"：取得模板详情 → 回调通道 1；回调缺失 → 降级复制预设 JSON（DC-06 通道 2） */
  const handleApplyTemplate = async () => {
    if (!selectedEntry?.preset_ref) return;
    try {
      const template = await getAgentTemplate(selectedEntry.preset_ref);
      if (onApplyTemplate) {
        onApplyTemplate(template);
        setApplyHint('模板已提交应用到画布');
        return;
      }
      const clipboard = navigator.clipboard;
      if (clipboard) {
        clipboard
          .writeText(JSON.stringify(template.preset))
          .then(
            () => setApplyHint('应用回调未注入：预设 JSON 已复制，可粘贴至画布 Agent 节点配置'),
            () => setApplyHint('应用回调未注入：预设 JSON 复制失败，请手动复制'),
          );
      } else {
        setApplyHint('应用回调未注入：剪贴板不可用，请手动复制预设 JSON');
      }
    } catch (err) {
      setDetailError(err instanceof Error ? err.message : '模板详情加载失败');
    }
  };

  return (
    <div className="wfp-amk-panel">
      <style>{`
.wfp-amk-input{width:100%;box-sizing:border-box;padding:7px 10px;font-size:13px;font-weight:500;font-family:inherit;color:${isDark ? darkTokens.text.primary : '#333333'};background-color:${isDark ? darkTokens.surface.bg : '#ffffff'};border:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};border-radius:5px;outline:none;transition:background-color 120ms ease,border-color 120ms ease;}
.wfp-amk-input:hover{background-color:${isDark ? darkTokens.surface.bgHover : '#fafafa'}}
.wfp-amk-input:focus{border-color:${isDark ? darkTokens.surface.borderStrong : '#bdbdbd'}}
.wfp-amk-input::placeholder{color:${isDark ? darkTokens.text.hint : '#b0b0b0'}}
.wfp-amk-select{padding:6px 10px;font-size:13px;font-family:inherit;color:${isDark ? darkTokens.text.primary : '#333333'};background-color:${isDark ? darkTokens.surface.bg : '#ffffff'};border:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};border-radius:5px;outline:none;}
.wfp-amk-table{width:100%;border-collapse:separate;border-spacing:0;font-size:12px;border:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};border-radius:8px;overflow:hidden;}
.wfp-amk-table thead th{background:${isDark ? darkTokens.surface.bgHover : '#f5f5f5'};font-weight:600;text-align:left;padding:8px 12px;border-bottom:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};color:${isDark ? darkTokens.text.primary : '#1a1a1a'}}
.wfp-amk-table tbody td{padding:7px 12px;border-bottom:1px solid ${isDark ? darkTokens.surface.divider : '#eee'};color:${isDark ? darkTokens.text.primary : '#1a1a1a'}}
.wfp-amk-table tbody tr{cursor:pointer;transition:background-color 120ms ease;}
.wfp-amk-table tbody tr:last-child td{border-bottom:none}
.wfp-amk-table tbody tr:hover{background:${isDark ? darkTokens.surface.bgHover : '#fafafa'}}
.wfp-amk-btn{font-size:13px;font-weight:500;font-family:inherit;padding:6px 14px;border-radius:6px;cursor:pointer;outline:none;border:1px solid ${isDark ? darkTokens.surface.border : '#d0d0d0'};background:${isDark ? darkTokens.surface.bg : '#ffffff'};color:${isDark ? darkTokens.text.primary : '#1a1a1a'};transition:background-color 120ms ease,border-color 120ms ease;}
.wfp-amk-btn:hover{background:${isDark ? darkTokens.surface.bgHover : '#f5f5f5'};border-color:${isDark ? darkTokens.surface.borderStrong : '#b8b8b8'}}
.wfp-amk-btn:active{background:${isDark ? darkTokens.surface.bgActive : '#e8e8e8'}}
.wfp-amk-btn:disabled{opacity:0.5;cursor:not-allowed}
.wfp-amk-btn--primary{background:${isDark ? '#1976d2' : '#1976d2'};color:#ffffff;border-color:#1976d2;}
.wfp-amk-btn--primary:hover{background:#1565c0;border-color:#1565c0;}
.wfp-amk-panel{padding:20px 24px;background:${isDark ? darkTokens.surface.bg : '#ffffff'};border:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};border-radius:8px;}
.wfp-amk-title{font-size:15px;font-weight:600;margin:0 0 16px;padding-bottom:12px;border-bottom:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};color:${isDark ? darkTokens.text.primary : '#1a1a1a'};}
.wfp-amk-error{margin-bottom:16px;padding:12px 16px;border-radius:8px;border:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};border-left:3px solid ${isDark ? darkTokens.danger : '#d32f2f'};background:${isDark ? 'rgba(255,107,107,0.08)' : '#fdecea'};font-size:12px;color:${isDark ? darkTokens.danger : '#d32f2f'};}
.wfp-amk-layout{display:flex;gap:16px;}
.wfp-amk-list{flex:1;min-width:0;}
.wfp-amk-detail{flex:1;min-width:0;padding:16px;background:${isDark ? darkTokens.surface.bgHover : '#fafafa'};border:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};border-radius:8px;}
.wfp-amk-detail-title{font-size:14px;font-weight:600;margin:0 0 12px;color:${isDark ? darkTokens.text.primary : '#1a1a1a'};}
.wfp-amk-detail p{font-size:12px;margin:4px 0;color:${isDark ? darkTokens.text.secondary : '#616161'};}
.wfp-amk-badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:500;background:${isDark ? darkTokens.surface.bgActive : '#f0f0f0'};}
.wfp-amk-actions{display:flex;gap:8px;align-items:center;margin-top:12px;flex-wrap:wrap;}
.wfp-amk-searchbar{display:flex;gap:8px;margin-bottom:12px;align-items:center;flex-wrap:wrap;}
.wfp-amk-searchbar .wfp-amk-input{flex:1;min-width:160px;}
.wfp-amk-empty{text-align:center;padding:24px 16px;color:${isDark ? darkTokens.text.hint : '#9e9e9e'};font-size:13px;}
.wfp-amk-hint{margin-top:8px;font-size:12px;color:${isDark ? darkTokens.text.secondary : '#616161'};}
`}</style>
      <h2 className="wfp-amk-title">Agent 市场（共 {total} 条）</h2>
      {error && <div className="wfp-amk-error" data-testid="agent-market-error">{error}</div>}

      <div className="wfp-amk-layout">
        {/* 条目列表 */}
        <div className="wfp-amk-list">
          <div className="wfp-amk-searchbar">
            <input
              className="wfp-amk-input"
              type="text"
              placeholder="按名称/描述/标签检索"
              value={keywordInput}
              onChange={(e) => setKeywordInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') setKeyword(keywordInput);
              }}
              data-testid="agent-market-keyword-input"
            />
            <select
              className="wfp-amk-select"
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              data-testid="agent-market-category-select"
            >
              <option value="">全部分类</option>
              {categories.map((item) => (
                <option key={item.category} value={item.category}>
                  {item.category}（{item.count}）
                </option>
              ))}
            </select>
            <button className="wfp-amk-btn" onClick={() => setKeyword(keywordInput)} data-testid="agent-market-search-button">
              搜索
            </button>
          </div>
          <table className="wfp-amk-table" data-testid="agent-market-table">
            <thead>
              <tr>
                <th>名称</th>
                <th>描述</th>
                <th>分类</th>
                <th>状态</th>
                <th>来源</th>
              </tr>
            </thead>
            <tbody>
              {entries.length === 0 ? (
                <tr>
                  <td colSpan={5} className="wfp-amk-empty">暂无市场条目</td>
                </tr>
              ) : (
                entries.map((entry) => (
                  <tr key={entry.entry_id} onClick={() => handleSelectEntry(entry)} data-testid={`agent-market-entry-${entry.entry_id}`}>
                    <td>{entry.name}</td>
                    <td>{entry.description}</td>
                    <td>{entry.category}</td>
                    <td>
                      <span className="wfp-amk-badge" style={{ color: MARKET_STATUS_COLORS[entry.status] }}>
                        {entry.status}
                      </span>
                    </td>
                    <td>
                      <span className="wfp-amk-badge" style={{ color: entry.source_kind === 'builtin' ? 'teal' : entry.source_kind === 'user_dev' ? 'purple' : 'blue' }}>
                        {entry.source_kind === 'builtin' ? '内置' : entry.source_kind === 'user_dev' ? '用户开发' : '已注册'}
                      </span>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {/* 选中详情区 */}
        {selectedEntry && (
          <div className="wfp-amk-detail" data-testid="agent-market-detail">
            <h3 className="wfp-amk-detail-title">{selectedEntry.name}</h3>
            <p>标识：{selectedEntry.entry_id}</p>
            <p>描述：{selectedEntry.description}</p>
            <p>
              状态：
              <span className="wfp-amk-badge" style={{ color: MARKET_STATUS_COLORS[selectedEntry.status] }}>
                {selectedEntry.status}
              </span>
            </p>
            <p>分类：{selectedEntry.category}</p>
            <p>标签：{selectedEntry.tags.length > 0 ? selectedEntry.tags.join('、') : '（无）'}</p>
            <p>关联模板：{selectedEntry.preset_ref ?? '（无）'}</p>
            <p>引用工具：{selectedEntry.tool_refs.length > 0 ? selectedEntry.tool_refs.join('、') : '（无）'}</p>
            <p>更新时间：{selectedEntry.updated_at}</p>
            <div className="wfp-amk-actions">
              <button
                className="wfp-amk-btn"
                onClick={handleShowTemplateDetail}
                disabled={!selectedEntry.preset_ref}
                data-testid="agent-market-template-button"
              >
                模板详情
              </button>
              <button
                className="wfp-amk-btn wfp-amk-btn--primary"
                onClick={handleApplyTemplate}
                disabled={!selectedEntry.preset_ref}
                data-testid="agent-market-apply-button"
              >
                应用模板
              </button>
            </div>
            {detailError && <p style={{ color: isDark ? darkTokens.danger : '#d32f2f' }}>{detailError}</p>}
            {applyHint && <p className="wfp-amk-hint" data-testid="agent-market-apply-hint">{applyHint}</p>}
            {/* 模板详情（五维度分段，DC-07） */}
            {detailTemplate && (
              <div style={{ marginTop: 16 }} data-testid="agent-market-template-detail">
                <TemplateDetailView template={detailTemplate} onApplyTemplate={onApplyTemplate} />
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
