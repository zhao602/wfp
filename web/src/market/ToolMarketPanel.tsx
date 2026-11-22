// =============================================================================
// Tool 市场面板（web/src/market/ToolMarketPanel.tsx）
//
// 职责：工具列表（来源 platform/mcp/local 徽章 + 权限徽章）+ 关键字/分类/权限筛选 +
//       选中详情（元数据 + schema 摘要）。
// 设计意图（DC-08）：
//   1. 挂载拉取工具清单（E-7）；拉取失败 → 错误条且页面可渲染（AC-21/AC-24）；
//   2. 复用 ContributionReviewBoard 的 wfp-crb-* 同类内联样式范式（wfp-tmk-* 前缀，AC-23）。
// =============================================================================

import { useEffect, useState, useCallback } from 'react';
import type { ToolMarketEntry } from './marketTypes';
import { listToolMarketTools, listToolMarketCategories } from './marketApiClient';
import { useThemeMode, darkTokens } from '../theme/themeMode';

/** 工具来源徽章色映射（三源 1:1 派生，AC-7） */
const TOOL_SOURCE_COLORS: Record<ToolMarketEntry['source'], string> = {
  platform: 'blue',
  mcp: 'orange',
  local: 'gray',
};

/** Tool 市场面板：工具检索 + 来源/权限徽章 + 详情 */
export function ToolMarketPanel(): JSX.Element {
  const [tools, setTools] = useState<ToolMarketEntry[]>([]);
  const [total, setTotal] = useState<number>(0);
  const [categories, setCategories] = useState<Array<{ category: string; count: number }>>([]);
  const [keywordInput, setKeywordInput] = useState('');
  const [keyword, setKeyword] = useState('');
  const [category, setCategory] = useState('');
  const [permission, setPermission] = useState('');
  const [selectedTool, setSelectedTool] = useState<ToolMarketEntry | null>(null);
  const [error, setError] = useState<string | null>(null);
  const themeMode = useThemeMode();
  const isDark = themeMode === 'dark';

  /** 拉取工具清单：失败 → 错误条且不清空既有数据（AC-24 规则 2） */
  const loadTools = useCallback(async () => {
    try {
      const response = await listToolMarketTools({
        q: keyword || undefined,
        category: category || undefined,
        permission: permission || undefined,
      });
      setTools(response.tools);
      setTotal(response.total);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载失败');
    }
  }, [keyword, category, permission]);

  useEffect(() => {
    loadTools();
  }, [loadTools]);

  /** 分类计数：挂载拉取一次，失败静默回落空分类（不阻塞主列表） */
  useEffect(() => {
    listToolMarketCategories()
      .then((response) => setCategories(response.categories))
      .catch(() => setCategories([]));
  }, []);

  return (
    <div className="wfp-tmk-panel">
      <style>{`
.wfp-tmk-input{width:100%;box-sizing:border-box;padding:7px 10px;font-size:13px;font-weight:500;font-family:inherit;color:${isDark ? darkTokens.text.primary : '#333333'};background-color:${isDark ? darkTokens.surface.bg : '#ffffff'};border:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};border-radius:5px;outline:none;transition:background-color 120ms ease,border-color 120ms ease;}
.wfp-tmk-input:hover{background-color:${isDark ? darkTokens.surface.bgHover : '#fafafa'}}
.wfp-tmk-input:focus{border-color:${isDark ? darkTokens.surface.borderStrong : '#bdbdbd'}}
.wfp-tmk-input::placeholder{color:${isDark ? darkTokens.text.hint : '#b0b0b0'}}
.wfp-tmk-select{padding:6px 10px;font-size:13px;font-family:inherit;color:${isDark ? darkTokens.text.primary : '#333333'};background-color:${isDark ? darkTokens.surface.bg : '#ffffff'};border:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};border-radius:5px;outline:none;}
.wfp-tmk-table{width:100%;border-collapse:separate;border-spacing:0;font-size:12px;border:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};border-radius:8px;overflow:hidden;}
.wfp-tmk-table thead th{background:${isDark ? darkTokens.surface.bgHover : '#f5f5f5'};font-weight:600;text-align:left;padding:8px 12px;border-bottom:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};color:${isDark ? darkTokens.text.primary : '#1a1a1a'}}
.wfp-tmk-table tbody td{padding:7px 12px;border-bottom:1px solid ${isDark ? darkTokens.surface.divider : '#eee'};color:${isDark ? darkTokens.text.primary : '#1a1a1a'}}
.wfp-tmk-table tbody tr:last-child td{border-bottom:none}
.wfp-tmk-table tbody tr:hover{background:${isDark ? darkTokens.surface.bgHover : '#fafafa'}}
.wfp-tmk-btn{font-size:13px;font-weight:500;font-family:inherit;padding:6px 14px;border-radius:6px;cursor:pointer;outline:none;border:1px solid ${isDark ? darkTokens.surface.border : '#d0d0d0'};background:${isDark ? darkTokens.surface.bg : '#ffffff'};color:${isDark ? darkTokens.text.primary : '#1a1a1a'};transition:background-color 120ms ease,border-color 120ms ease;}
.wfp-tmk-btn:hover{background:${isDark ? darkTokens.surface.bgHover : '#f5f5f5'};border-color:${isDark ? darkTokens.surface.borderStrong : '#b8b8b8'}}
.wfp-tmk-panel{padding:20px 24px;background:${isDark ? darkTokens.surface.bg : '#ffffff'};border:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};border-radius:8px;}
.wfp-tmk-title{font-size:15px;font-weight:600;margin:0 0 16px;padding-bottom:12px;border-bottom:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};color:${isDark ? darkTokens.text.primary : '#1a1a1a'};}
.wfp-tmk-error{margin-bottom:16px;padding:12px 16px;border-radius:8px;border:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};border-left:3px solid ${isDark ? darkTokens.danger : '#d32f2f'};background:${isDark ? 'rgba(255,107,107,0.08)' : '#fdecea'};font-size:12px;color:${isDark ? darkTokens.danger : '#d32f2f'};}
.wfp-tmk-layout{display:flex;gap:16px;}
.wfp-tmk-list{flex:1;min-width:0;}
.wfp-tmk-detail{flex:1;min-width:0;padding:16px;background:${isDark ? darkTokens.surface.bgHover : '#fafafa'};border:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};border-radius:8px;}
.wfp-tmk-detail-title{font-size:14px;font-weight:600;margin:0 0 12px;color:${isDark ? darkTokens.text.primary : '#1a1a1a'};}
.wfp-tmk-detail p{font-size:12px;margin:4px 0;color:${isDark ? darkTokens.text.secondary : '#616161'};}
.wfp-tmk-badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:500;background:${isDark ? darkTokens.surface.bgActive : '#f0f0f0'};}
.wfp-tmk-searchbar{display:flex;gap:8px;margin-bottom:12px;align-items:center;flex-wrap:wrap;}
.wfp-tmk-searchbar .wfp-tmk-input{flex:1;min-width:140px;}
.wfp-tmk-empty{text-align:center;padding:24px 16px;color:${isDark ? darkTokens.text.hint : '#9e9e9e'};font-size:13px;}
.wfp-tmk-schema{margin-top:8px;}
.wfp-tmk-schema-title{font-size:13px;font-weight:600;margin:12px 0 8px;color:${isDark ? darkTokens.text.primary : '#1a1a1a'};}
.wfp-tmk-schema ul{list-style:none;padding:0;margin:0;}
.wfp-tmk-schema li{font-size:12px;padding:4px 0;border-bottom:1px solid ${isDark ? darkTokens.surface.divider : '#f0f0f0'};color:${isDark ? darkTokens.text.secondary : '#616161'};}
.wfp-tmk-schema li:last-child{border-bottom:none;}
`}</style>
      <h2 className="wfp-tmk-title">Tool 市场（共 {total} 个工具）</h2>
      {error && <div className="wfp-tmk-error" data-testid="tool-market-error">{error}</div>}

      <div className="wfp-tmk-layout">
        {/* 工具列表 */}
        <div className="wfp-tmk-list">
          <div className="wfp-tmk-searchbar">
            <input
              className="wfp-tmk-input"
              type="text"
              placeholder="按标识/名称检索"
              value={keywordInput}
              onChange={(e) => setKeywordInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') setKeyword(keywordInput);
              }}
              data-testid="tool-market-keyword-input"
            />
            <select
              className="wfp-tmk-select"
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              data-testid="tool-market-category-select"
            >
              <option value="">全部分类</option>
              {categories.map((item) => (
                <option key={item.category} value={item.category}>
                  {item.category}（{item.count}）
                </option>
              ))}
            </select>
            <select
              className="wfp-tmk-select"
              value={permission}
              onChange={(e) => setPermission(e.target.value)}
              data-testid="tool-market-permission-select"
            >
              <option value="">全部权限</option>
              <option value="readonly">readonly</option>
              <option value="readwrite">readwrite</option>
            </select>
            <button className="wfp-tmk-btn" onClick={() => setKeyword(keywordInput)} data-testid="tool-market-search-button">
              搜索
            </button>
          </div>
          <table className="wfp-tmk-table" data-testid="tool-market-table">
            <thead>
              <tr>
                <th>标识</th>
                <th>名称</th>
                <th>来源</th>
                <th>权限</th>
                <th>版本</th>
              </tr>
            </thead>
            <tbody>
              {tools.length === 0 ? (
                <tr>
                  <td colSpan={5} className="wfp-tmk-empty">暂无市场工具</td>
                </tr>
              ) : (
                tools.map((tool) => (
                  <tr key={tool.tool_identifier} onClick={() => setSelectedTool(tool)} data-testid={`tool-market-tool-${tool.tool_identifier}`}>
                    <td>{tool.tool_identifier}</td>
                    <td>{tool.name}</td>
                    <td>
                      <span className="wfp-tmk-badge" style={{ color: TOOL_SOURCE_COLORS[tool.source] }}>
                        {tool.source}
                      </span>
                    </td>
                    <td>
                      <span className="wfp-tmk-badge">{tool.permission}</span>
                    </td>
                    <td>{tool.version}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {/* 选中详情区 */}
        {selectedTool && (
          <div className="wfp-tmk-detail" data-testid="tool-market-detail">
            <h3 className="wfp-tmk-detail-title">
              {selectedTool.name}
              <span className="wfp-tmk-badge" style={{ marginLeft: 8, color: TOOL_SOURCE_COLORS[selectedTool.source] }}>
                {selectedTool.source}
              </span>
            </h3>
            <p>标识：{selectedTool.tool_identifier}</p>
            <p>描述：{selectedTool.description}</p>
            <p>权限：{selectedTool.permission}</p>
            <p>版本：{selectedTool.version}</p>
            <p>标签：{selectedTool.tags.length > 0 ? selectedTool.tags.join('、') : '（无）'}</p>
            <div className="wfp-tmk-schema" data-testid="tool-market-input-schema">
              <h4 className="wfp-tmk-schema-title">输入参数</h4>
              <ul>
                {selectedTool.input_schema_summary.length === 0 ? (
                  <li>（无参数）</li>
                ) : (
                  selectedTool.input_schema_summary.map((param, index) => (
                    <li key={index}>
                      {param.param}：{param.type}
                    </li>
                  ))
                )}
              </ul>
            </div>
            <div className="wfp-tmk-schema" data-testid="tool-market-output-schema">
              <h4 className="wfp-tmk-schema-title">输出结构</h4>
              <ul>
                {selectedTool.output_schema_summary.length === 0 ? (
                  <li>（无结构）</li>
                ) : (
                  selectedTool.output_schema_summary.map((param, index) => (
                    <li key={index}>
                      {param.param}：{param.type}
                    </li>
                  ))
                )}
              </ul>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
