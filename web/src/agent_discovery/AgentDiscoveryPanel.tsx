// =============================================================================
// Agent 发现面板（web/src/agent_discovery/AgentDiscoveryPanel.tsx）
//
// 职责：提供 Agent 语义档案管理与发现查询的前端界面。
// 设计意图（对应偏差修正 T203）：
//   - 档案列表展示与查询
//   - 自然语言意图查询与结果展示
//   - 描述来源三态可视化
// =============================================================================

import { useEffect, useState, useCallback } from 'react';
import { useThemeMode, darkTokens } from '../theme/themeMode';
import type {
  AgentSemanticProfile,
  DiscoveryResult,
  DiscoveryResultItem,
} from './discoveryTypes';
import { listProfiles, discover } from './discoveryApiClient';

/** 描述来源三态中文标签 */
const SOURCE_LABELS: Record<string, string> = {
  provider: '注册方提供',
  llm_generated: 'LLM 生成',
  manual_revision: '人工修订',
};

/** 描述来源三态颜色 */
const SOURCE_COLORS: Record<string, string> = {
  provider: '#6b7280',
  llm_generated: '#3b82f6',
  manual_revision: '#10b981',
};

export function AgentDiscoveryPanel({
  userId,
  tenantId,
  permissions,
}: {
  userId: string;
  tenantId: string;
  permissions: string[];
}): JSX.Element {
  const themeMode = useThemeMode();
  const isDark = themeMode === 'dark';
  const [profiles, setProfiles] = useState<AgentSemanticProfile[]>([]);
  const [queryText, setQueryText] = useState('');
  const [tagsFilter, setTagsFilter] = useState('');
  const [result, setResult] = useState<DiscoveryResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const loadProfiles = useCallback(async () => {
    try {
      setProfiles(await listProfiles());
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载档案失败');
    }
  }, []);

  useEffect(() => {
    loadProfiles();
  }, [loadProfiles]);

  const handleDiscover = useCallback(async () => {
    if (!queryText.trim()) {
      setError('请输入意图查询文本');
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const res = await discover({
        intent_text: queryText,
        caller: { user_id: userId, tenant_id: tenantId, roles: [], permissions },
        tags_filter: tagsFilter
          ? tagsFilter.split(',').map((t) => t.trim()).filter(Boolean)
          : [],
        limit: null,
      });
      setResult(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : '发现查询失败');
    } finally {
      setLoading(false);
    }
  }, [queryText, tagsFilter, userId, tenantId, permissions]);

  return (
    <div className="wfp-adp-panel">
      <style>{`
.wfp-adp-input{width:100%;box-sizing:border-box;padding:7px 10px;font-size:13px;font-weight:500;font-family:inherit;color:${isDark ? darkTokens.text.primary : '#333333'};background-color:${isDark ? darkTokens.surface.bg : '#ffffff'};border:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};border-radius:5px;outline:none;transition:background-color 120ms ease,border-color 120ms ease;}
.wfp-adp-input:hover{background-color:${isDark ? darkTokens.surface.bgHover : '#fafafa'}}
.wfp-adp-input:focus{border-color:${isDark ? darkTokens.surface.borderStrong : '#bdbdbd'}}
.wfp-adp-input::placeholder{color:${isDark ? darkTokens.text.hint : '#b0b0b0'}}
.wfp-adp-textarea{width:100%;box-sizing:border-box;padding:7px 10px;font-size:13px;font-weight:400;font-family:inherit;color:${isDark ? darkTokens.text.primary : '#333333'};background-color:${isDark ? darkTokens.surface.bg : '#ffffff'};border:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};border-radius:5px;outline:none;transition:background-color 120ms ease,border-color 120ms ease;line-height:1.5;resize:vertical;}
.wfp-adp-textarea:hover{background-color:${isDark ? darkTokens.surface.bgHover : '#fafafa'}}
.wfp-adp-textarea:focus{border-color:${isDark ? darkTokens.surface.borderStrong : '#bdbdbd'}}
.wfp-adp-textarea::placeholder{color:${isDark ? darkTokens.text.hint : '#b0b0b0'}}
.wfp-adp-table{width:100%;border-collapse:separate;border-spacing:0;font-size:12px;border:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};border-radius:8px;overflow:hidden;}
.wfp-adp-table thead th{background:${isDark ? darkTokens.surface.bgHover : '#f5f5f5'};font-weight:600;text-align:left;padding:8px 12px;border-bottom:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};color:${isDark ? darkTokens.text.primary : '#1a1a1a'}}
.wfp-adp-table tbody td{padding:7px 12px;border-bottom:1px solid ${isDark ? darkTokens.surface.divider : '#eee'};color:${isDark ? darkTokens.text.primary : '#1a1a1a'}}
.wfp-adp-table tbody tr:last-child td{border-bottom:none}
.wfp-adp-table tbody tr:hover{background:${isDark ? darkTokens.surface.bgHover : '#fafafa'}}
.wfp-adp-btn{font-size:13px;font-weight:500;font-family:inherit;padding:6px 14px;border-radius:6px;cursor:pointer;outline:none;border:1px solid ${isDark ? darkTokens.surface.border : '#d0d0d0'};background:${isDark ? darkTokens.surface.bg : '#ffffff'};color:${isDark ? darkTokens.text.primary : '#1a1a1a'};transition:background-color 120ms ease,border-color 120ms ease;}
.wfp-adp-btn:hover{background:${isDark ? darkTokens.surface.bgHover : '#f5f5f5'};border-color:${isDark ? darkTokens.surface.borderStrong : '#b8b8b8'}}
.wfp-adp-btn:active{background:${isDark ? darkTokens.surface.bgActive : '#e8e8e8'}}
.wfp-adp-btn:disabled{opacity:0.5;cursor:not-allowed}
.wfp-adp-btn--primary{background:${isDark?darkTokens.surface.bgActive:"#1a1a1a"};color:${isDark?darkTokens.text.primary:"#ffffff"};border-color:${isDark?darkTokens.surface.borderStrong:"#1a1a1a"}}
.wfp-adp-btn--primary:hover{background:${isDark?darkTokens.surface.bgHover:"#333333"}}
.wfp-adp-panel{padding:20px 24px;background:${isDark?darkTokens.surface.bg:"#ffffff"};border:1px solid ${isDark?darkTokens.surface.divider:"#e0e0e0"};border-radius:8px;}
.wfp-adp-title{font-size:15px;font-weight:600;margin:0 0 16px;padding-bottom:12px;border-bottom:1px solid ${isDark?darkTokens.surface.divider:"#e0e0e0"};color:${isDark?darkTokens.text.primary:"#1a1a1a"};}
.wfp-adp-error{margin-bottom:16px;padding:12px 16px;border-radius:8px;border:1px solid ${isDark?darkTokens.surface.divider:"#e0e0e0"};border-left:3px solid ${isDark?darkTokens.danger:"#d32f2f"};background:${isDark?"rgba(255,107,107,0.08)":"#fdecea"};font-size:12px;color:${isDark?darkTokens.danger:"#d32f2f"};}
.wfp-adp-section{margin-bottom:20px;}
.wfp-adp-section:last-child{margin-bottom:0;}
.wfp-adp-section-title{font-size:14px;font-weight:600;margin:0 0 12px;color:${isDark?darkTokens.text.primary:"#1a1a1a"};}
.wfp-adp-section .wfp-adp-textarea,.wfp-adp-section .wfp-adp-input{margin-bottom:10px;}
.wfp-adp-result-list{display:flex;flex-direction:column;gap:10px;}
.wfp-adp-result-item{padding:14px;background:${isDark?darkTokens.surface.bgHover:"#fafafa"};border:1px solid ${isDark?darkTokens.surface.divider:"#e0e0e0"};border-radius:8px;}
.wfp-adp-result-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;}
.wfp-adp-result-name{font-size:13px;font-weight:600;color:${isDark?darkTokens.text.primary:"#1a1a1a"};}
.wfp-adp-result-score{font-size:11px;font-weight:500;padding:2px 8px;border-radius:10px;background:${isDark?darkTokens.surface.bgActive:"#f0f0f0"};color:${isDark?darkTokens.text.secondary:"#616161"};}
.wfp-adp-result-desc{font-size:12px;color:${isDark?darkTokens.text.secondary:"#616161"};margin-bottom:8px;line-height:1.5;}
.wfp-adp-result-tags{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:6px;}
.wfp-adp-tag{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:500;background:${isDark?"rgba(66,165,245,0.15)":"#e3f2fd"};color:${isDark?"#8ec5fc":"#1565c0"};}
.wfp-adp-result-source{font-size:11px;}
.wfp-adp-empty{text-align:center;padding:24px 16px;color:${isDark?darkTokens.text.hint:"#9e9e9e"};font-size:13px;}
`}</style>
      <h2 className="wfp-adp-title">语义索引 Agent 发现</h2>

      {error && <div className="wfp-adp-error">{error}</div>}

      {/* 发现查询区 */}
      <section className="wfp-adp-section">
        <h3 className="wfp-adp-section-title">意图查询</h3>
        <textarea
          value={queryText}
          onChange={(e) => setQueryText(e.target.value)}
          placeholder="输入自然语言意图，例如：我需要一个能做数据清洗的 Agent"
          rows={3}
          data-testid="intent-input"
          className="wfp-adp-textarea"
        />
        <input
          type="text"
          value={tagsFilter}
          onChange={(e) => setTagsFilter(e.target.value)}
          placeholder="标签过滤（逗号分隔，AND 语义）"
          data-testid="tags-filter-input"
          className="wfp-adp-input"
        />
        <button
          onClick={handleDiscover}
          disabled={loading}
          data-testid="discover-button"
          className="wfp-adp-btn wfp-adp-btn--primary"
        >
          {loading ? '查询中…' : '发现 Agent'}
        </button>
      </section>

      {/* 发现结果区 */}
      {result && (
        <section className="wfp-adp-section">
          <h3 className="wfp-adp-section-title">
            发现结果（{result.total} 条，后端：{result.backend}）
          </h3>
          <div className="wfp-adp-result-list" data-testid="discovery-result-list">
            {result.items.map((item: DiscoveryResultItem) => (
              <div
                key={item.agent_id}
                className="wfp-adp-result-item"
                data-testid={`result-item-${item.agent_id}`}
              >
                <div className="wfp-adp-result-header">
                  <span className="wfp-adp-result-name">{item.name}</span>
                  <span className="wfp-adp-result-score">
                    相关度：{(item.relevance_score * 100).toFixed(1)}%
                  </span>
                </div>
                <div className="wfp-adp-result-desc">{item.description}</div>
                <div className="wfp-adp-result-tags">
                  {item.tags.map((tag) => (
                    <span key={tag} className="wfp-adp-tag">
                      {tag}
                    </span>
                  ))}
                </div>
                <div className="wfp-adp-result-source">
                  <span
                    style={{
                      color: SOURCE_COLORS[item.description_source] || '#6b7280',
                    }}
                  >
                    {SOURCE_LABELS[item.description_source] || item.description_source}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* 档案列表区 */}
      <section className="wfp-adp-section">
        <h3 className="wfp-adp-section-title">语义档案（{profiles.length} 个）</h3>
        <table data-testid="profile-table" className="wfp-adp-table">
          <thead>
            <tr>
              <th>Agent 标识</th>
              <th>名称</th>
              <th>描述来源</th>
              <th>标签</th>
              <th>所需权限</th>
            </tr>
          </thead>
          <tbody>
            {profiles.length === 0 ? (
              <tr><td colSpan={5} className="wfp-adp-empty">暂无语义档案</td></tr>
            ) : profiles.map((profile) => (
              <tr key={profile.agent_id}>
                <td>{profile.agent_id}</td>
                <td>{profile.name}</td>
                <td>
                  <span
                    style={{
                      color: SOURCE_COLORS[profile.description_source] || '#6b7280',
                    }}
                  >
                    {SOURCE_LABELS[profile.description_source] ||
                      profile.description_source}
                  </span>
                </td>
                <td>{profile.tags.join(', ')}</td>
                <td>{profile.required_permissions.join(', ')}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
