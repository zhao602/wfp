// =============================================================================
// 模板详情视图（web/src/market/TemplateDetailView.tsx）
//
// 职责：五维度分段渲染（固定次序）+ 工具调用配置 4 项 + 实现关键点/依赖条件 +
//       性能与安全边界 + 评价指标（含可采集性标注）+ recipe 节点/连线摘要 + "应用模板"。
// 设计意图（DC-07/DC-08）：
//   1. 五段标题次序固定：①核心原理与执行流程 ②应用特性 ③适用场景
//      ④优势与局限 ⑤与相关方案对比；
//   2. 内容全部来自后端 TemplateDoc 元数据，前端不硬编码（OP-3）；
//   3. 应用回调缺失时降级复制预设 JSON（DC-06 通道 2，T16.6）；
//   4. 复用 ContributionReviewBoard 的 wfp-crb-* 同类内联样式范式（wfp-tdv-* 前缀，AC-23）。
// =============================================================================

import { useState } from 'react';
import type { AgentTemplate } from './marketTypes';
import { useThemeMode, darkTokens } from '../theme/themeMode';

/** 模板详情视图属性 */
export interface TemplateDetailViewProps {
  /** 模板详情（由父面板经 marketApiClient 取得后传入） */
  template: AgentTemplate;
  /** 应用模板回调（可选，DC-06 通道 1；缺失时降级复制预设 JSON） */
  onApplyTemplate?: (t: AgentTemplate, targetNodeId?: string | null) => void;
}

/** 评价指标可采集性标注（AC-18） */
const COLLECTABLE_LABELS: Record<string, string> = {
  static: '静态可采集',
  runtime: '运行时可采集',
  not_quantifiable: '不可量化',
};

/** 五维度分段（固定次序，DC-07） */
const DOC_SECTIONS: Array<{ title: string; key: keyof AgentTemplate['doc'] }> = [
  { title: '核心原理与执行流程', key: 'core_principle_flow' },
  { title: '应用特性', key: 'application_features' },
  { title: '适用场景', key: 'applicable_scenarios' },
  { title: '优势与局限', key: 'advantages_limitations' },
  { title: '与相关方案对比', key: 'comparison' },
];

/** 模板详情视图：五维度分段渲染 + 附加段落 + "应用模板" */
export function TemplateDetailView({ template, onApplyTemplate }: TemplateDetailViewProps): JSX.Element {
  const [applyHint, setApplyHint] = useState<string | null>(null);
  const themeMode = useThemeMode();
  const isDark = themeMode === 'dark';

  /** 应用模板：回调已注入 → 通道 1；回调缺失 → 通道 2 降级复制预设 JSON（DC-06） */
  const handleApply = () => {
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
  };

  return (
    <div className="wfp-tdv-panel" data-testid="template-detail">
      <style>{`
.wfp-tdv-panel{padding:16px;background:${isDark ? darkTokens.surface.bgHover : '#fafafa'};border:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};border-radius:8px;}
.wfp-tdv-title{font-size:14px;font-weight:600;margin:0 0 4px;color:${isDark ? darkTokens.text.primary : '#1a1a1a'};}
.wfp-tdv-subtitle{font-size:12px;margin:0 0 12px;color:${isDark ? darkTokens.text.secondary : '#616161'};}
.wfp-tdv-badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:500;background:${isDark ? darkTokens.surface.bgActive : '#f0f0f0'};}
.wfp-tdv-section-title{font-size:13px;font-weight:600;margin:16px 0 8px;color:${isDark ? darkTokens.text.primary : '#1a1a1a'};}
.wfp-tdv-doc{font-size:12px;margin:0;line-height:1.7;color:${isDark ? darkTokens.text.secondary : '#616161'};white-space:pre-wrap;}
.wfp-tdv-list{list-style:none;padding:0;margin:0;}
.wfp-tdv-list li{font-size:12px;padding:4px 0;border-bottom:1px solid ${isDark ? darkTokens.surface.divider : '#f0f0f0'};color:${isDark ? darkTokens.text.secondary : '#616161'};}
.wfp-tdv-list li:last-child{border-bottom:none;}
.wfp-tdv-kv{font-size:12px;margin:4px 0;color:${isDark ? darkTokens.text.secondary : '#616161'};}
.wfp-tdv-table{width:100%;border-collapse:separate;border-spacing:0;font-size:12px;border:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};border-radius:8px;overflow:hidden;}
.wfp-tdv-table thead th{background:${isDark ? darkTokens.surface.bgHover : '#f5f5f5'};font-weight:600;text-align:left;padding:8px 12px;border-bottom:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};color:${isDark ? darkTokens.text.primary : '#1a1a1a'};}
.wfp-tdv-table tbody td{padding:7px 12px;border-bottom:1px solid ${isDark ? darkTokens.surface.divider : '#eee'};color:${isDark ? darkTokens.text.primary : '#1a1a1a'};}
.wfp-tdv-table tbody tr:last-child td{border-bottom:none;}
.wfp-tdv-btn{font-size:13px;font-weight:500;font-family:inherit;padding:6px 14px;border-radius:6px;cursor:pointer;outline:none;border:1px solid ${isDark ? darkTokens.surface.border : '#d0d0d0'};background:${isDark ? darkTokens.surface.bg : '#ffffff'};color:${isDark ? darkTokens.text.primary : '#1a1a1a'};transition:background-color 120ms ease,border-color 120ms ease;}
.wfp-tdv-btn:hover{background:${isDark ? darkTokens.surface.bgHover : '#f5f5f5'};border-color:${isDark ? darkTokens.surface.borderStrong : '#b8b8b8'}}
.wfp-tdv-btn--primary{background:${isDark ? '#1976d2' : '#1976d2'};color:#ffffff;border-color:#1976d2;}
.wfp-tdv-btn--primary:hover{background:#1565c0;border-color:#1565c0;}
.wfp-tdv-hint{margin-top:8px;font-size:12px;color:${isDark ? darkTokens.text.secondary : '#616161'};}
`}</style>
      <h3 className="wfp-tdv-title">
        {template.name}
        <span className="wfp-tdv-badge" style={{ marginLeft: 8, color: template.kind === 'recipe' ? 'orange' : 'teal' }}>
          {template.kind === 'recipe' ? '配方模板' : '单节点模板'}
        </span>
      </h3>
      <p className="wfp-tdv-subtitle">
        模板标识：{template.template_id}｜调用方式：{template.tool_config.invocation_mode}
      </p>

      {/* 五维度分段（固定次序，DC-07） */}
      {DOC_SECTIONS.map((section, index) => (
        <section key={section.key} data-testid={`template-doc-section-${index}`}>
          <h4 className="wfp-tdv-section-title">{section.title}</h4>
          <p className="wfp-tdv-doc">{template.doc[section.key]}</p>
        </section>
      ))}

      {/* 工具调用配置 4 项 */}
      <section data-testid="template-tool-config">
        <h4 className="wfp-tdv-section-title">工具调用配置</h4>
        <p className="wfp-tdv-kv">工具类型：{template.tool_config.tool_types.join('、') || '（无）'}</p>
        <p className="wfp-tdv-kv">调用方式：{template.tool_config.invocation_mode}</p>
        <p className="wfp-tdv-kv">参数约束：{template.tool_config.parameter_constraints}</p>
        <p className="wfp-tdv-kv">失败重试与异常策略：{template.tool_config.retry_and_exception_policy}</p>
      </section>

      {/* 实现关键点 / 依赖条件 */}
      <section>
        <h4 className="wfp-tdv-section-title">实现关键点</h4>
        <ul className="wfp-tdv-list" data-testid="template-key-points">
          {template.implementation_key_points.map((point, index) => (
            <li key={index}>{point}</li>
          ))}
        </ul>
      </section>
      <section>
        <h4 className="wfp-tdv-section-title">依赖条件</h4>
        <ul className="wfp-tdv-list" data-testid="template-dependency-conditions">
          {template.dependency_conditions.map((condition, index) => (
            <li key={index}>{condition}</li>
          ))}
        </ul>
      </section>

      {/* 性能与安全边界 */}
      <section data-testid="template-performance-boundaries">
        <h4 className="wfp-tdv-section-title">性能边界</h4>
        <p className="wfp-tdv-kv">
          最大工具轮次：{template.performance_boundaries.max_tool_rounds ?? '缺省（回落全局 5）'}
        </p>
        <p className="wfp-tdv-kv">工具调用超时：{template.performance_boundaries.tool_timeout_seconds} 秒</p>
        <p className="wfp-tdv-kv">
          输出重试上限：{template.performance_boundaries.output_max_retries ?? '缺省'}
        </p>
      </section>
      <section data-testid="template-security-boundaries">
        <h4 className="wfp-tdv-section-title">安全边界</h4>
        <ul className="wfp-tdv-list">
          {template.security_boundaries.map((boundary, index) => (
            <li key={index}>{boundary}</li>
          ))}
        </ul>
      </section>

      {/* 评价指标（含可采集性标注，AC-18） */}
      <section data-testid="template-evaluation-metrics">
        <h4 className="wfp-tdv-section-title">评价指标</h4>
        <table className="wfp-tdv-table">
          <thead>
            <tr>
              <th>指标</th>
              <th>可采集性</th>
            </tr>
          </thead>
          <tbody>
            {template.evaluation_metrics.map((metric, index) => (
              <tr key={index}>
                <td>{metric.metric}</td>
                <td>
                  <span className="wfp-tdv-badge" style={{ color: metric.collectable === 'runtime' ? 'green' : 'gray' }}>
                    {COLLECTABLE_LABELS[metric.collectable] ?? metric.collectable}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {/* recipe 模板节点/连线摘要 */}
      {template.kind === 'recipe' && template.recipe && (
        <section data-testid="template-recipe-summary">
          <h4 className="wfp-tdv-section-title">配方节点与连线</h4>
          <table className="wfp-tdv-table">
            <thead>
              <tr>
                <th>本地引用</th>
                <th>节点类型</th>
                <th>名称</th>
              </tr>
            </thead>
            <tbody>
              {template.recipe.nodes.map((node, index) => (
                <tr key={index}>
                  <td>{node.local_ref}</td>
                  <td>{node.node_type}</td>
                  <td>{node.name}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <ul className="wfp-tdv-list" style={{ marginTop: 8 }}>
            {template.recipe.edges.map((edge, index) => (
              <li key={index}>
                {edge.source_ref} → {edge.target_ref}
                {edge.condition_label ? `（${edge.condition_label}）` : ''}
              </li>
            ))}
          </ul>
          {template.recipe.nodes.some(
            (node) =>
              node.configuration.model_identifier === '' ||
              node.configuration.knowledge_base_id === '',
          ) && (
            <p className="wfp-tdv-hint">
              注意：部分节点存在待补填的必填标识（模型标识 model_identifier / 知识库标识
              knowledge_base_id），应用到画布后需在节点配置中填写，否则实例运行会失败。
            </p>
          )}
        </section>
      )}

      {/* 应用模板（DC-06：回调缺失时降级复制预设 JSON） */}
      <div style={{ marginTop: 16 }}>
        <button className="wfp-tdv-btn wfp-tdv-btn--primary" onClick={handleApply} data-testid="template-apply-button">
          应用模板
        </button>
        {applyHint && <p className="wfp-tdv-hint" data-testid="template-apply-hint">{applyHint}</p>}
      </div>
    </div>
  );
}
