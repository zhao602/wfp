// =============================================================================
// Agent 绩效查询面板（web/src/agent_performance/AgentPerformancePanel.tsx）
//
// 职责：Agent 维度绩效四维聚合查询与可视化呈现（第五阶段 G3/T236）。
// 设计意图（对应 spec 5.3.3、design D5）：
//   1. agent_id + 起止时间查询表单，调用
//      GET /api/v1/governance/agent-performance 一次请求返回完整聚合
//      （不逐条拉取拼装，性能约束 4.1.3）；
//   2. 一次渲染成功率/平均耗时/Token 消耗/质量评分/sample_count/data_source；
//   3. 查询结果支持网页可视化（指标卡片 + 成功率进度条）并支持导出
//      下载到本地（JSON/CSV）；
//   4. 功能开关关闭（路由 404）时展示"功能未启用"提示而非报错；
//   5. 无数据时展示零值聚合（A3.3.a）。
// =============================================================================

import { useCallback, useState } from 'react';
import { useThemeMode, darkTokens } from '../theme/themeMode';

// -----------------------------------------------------------------------------
// 类型定义（与后端 Pydantic 出参 AgentPerformanceAggregate 对齐）
// -----------------------------------------------------------------------------

/** 后端聚合响应：四维指标字段恒在（无数据以零值表达）。 */
export interface AgentPerformanceAggregate {
  agent_id: string;
  start: string;
  end: string;
  success_rate: number;
  avg_duration_ms: number;
  total_tokens: number;
  avg_quality_score: number;
  sample_count: number;
  data_source: string;
}

/** 导出行（CSV 用）。 */
interface MetricRow {
  指标: string;
  值: string;
}

// -----------------------------------------------------------------------------
// API 调用
// -----------------------------------------------------------------------------

/**
 * 查询 Agent 绩效聚合。
 * 后端开关关闭时路由未注册，FastAPI 返回 404——据此抛出 NotEnabledError，
 * 由面板展示"功能未启用"提示而非通用报错。
 */
async function fetchAgentPerformance(
  agentId: string,
  start: string,
  end: string,
): Promise<AgentPerformanceAggregate> {
  const params = new URLSearchParams({
    agent_id: agentId,
    start: start,
    end: end,
  });
  const resp = await fetch(`/api/v1/governance/agent-performance?${params.toString()}`);
  if (resp.status === 404) {
    throw new NotEnabledError('Agent 绩效查询功能未启用');
  }
  if (resp.status === 422) {
    const body: unknown = await resp.json();
    let detail = '参数非法';
    if (body && typeof body === 'object' && 'detail' in body) {
      const d = (body as { detail: { code?: string; message?: string } }).detail;
      detail = d.message ?? d.code ?? detail;
    }
    throw new Error(detail);
  }
  if (!resp.ok) {
    throw new Error(`查询失败（HTTP ${resp.status}）`);
  }
  return (await resp.json()) as AgentPerformanceAggregate;
}

/** 功能未启用专用错误（404 时抛出）。 */
class NotEnabledError extends Error {}

// -----------------------------------------------------------------------------
// 导出下载（JSON / CSV）
// -----------------------------------------------------------------------------

function downloadFile(content: string, filename: string, mime: string): void {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

function toCsv(rows: MetricRow[]): string {
  const header = '指标,值';
  const lines = rows.map((r) => `${r.指标},${r.值}`);
  // BOM 头保证 Excel 打开中文不乱码
  return '\uFEFF' + header + '\n' + lines.join('\n');
}

// -----------------------------------------------------------------------------
// 面板组件
// -----------------------------------------------------------------------------

/** Agent 绩效查询面板。 */
export function AgentPerformancePanel(): JSX.Element {
  const themeMode = useThemeMode();
  const isDark = themeMode === 'dark';
  const [agentId, setAgentId] = useState<string>('agent_1');
  const [start, setStart] = useState<string>('2026-09-25T00:00');
  const [end, setEnd] = useState<string>('2026-09-26T23:59');
  const [result, setResult] = useState<AgentPerformanceAggregate | null>(null);
  const [error, setError] = useState<string>('');
  const [notEnabled, setNotEnabled] = useState<boolean>(false);
  const [loading, setLoading] = useState<boolean>(false);

  const handleQuery = useCallback(async () => {
    setLoading(true);
    setError('');
    setNotEnabled(false);
    try {
      // datetime-local 值转 ISO8601（后端要求 ISO 格式）
      const startIso = new Date(start).toISOString();
      const endIso = new Date(end).toISOString();
      const agg = await fetchAgentPerformance(agentId, startIso, endIso);
      setResult(agg);
    } catch (err) {
      if (err instanceof NotEnabledError) {
        setNotEnabled(true);
        setResult(null);
      } else {
        setError(err instanceof Error ? err.message : '查询失败');
        setResult(null);
      }
    } finally {
      setLoading(false);
    }
  }, [agentId, start, end]);

  const handleExportJson = useCallback(() => {
    if (!result) return;
    downloadFile(
      JSON.stringify(result, null, 2),
      `agent_performance_${result.agent_id}.json`,
      'application/json;charset=utf-8',
    );
  }, [result]);

  const handleExportCsv = useCallback(() => {
    if (!result) return;
    const rows: MetricRow[] = [
      { 指标: 'Agent ID', 值: result.agent_id },
      { 指标: '统计起始', 值: result.start },
      { 指标: '统计结束', 值: result.end },
      { 指标: '成功率', 值: `${(result.success_rate * 100).toFixed(1)}%` },
      { 指标: '平均耗时(ms)', 值: String(result.avg_duration_ms) },
      { 指标: 'Token 总量', 值: String(result.total_tokens) },
      { 指标: '质量评分均值', 值: String(result.avg_quality_score) },
      { 指标: '样本数', 值: String(result.sample_count) },
      { 指标: '数据来源', 值: result.data_source },
    ];
    downloadFile(toCsv(rows), `agent_performance_${result.agent_id}.csv`, 'text/csv;charset=utf-8');
  }, [result]);

  // ----- 功能未启用（后端开关关闭 / 路由 404）提示 -----
  if (notEnabled) {
    return (
      <div className="wfp-apt-disabled">
        <h2 className="wfp-apt-title">Agent 绩效聚合</h2>
        <p>
          功能未启用：后端 AGENT_PERFORMANCE_ENABLED 开关为关闭状态，
          查询路由未注册。请在 .env 中开启后重启服务。
        </p>
      </div>
    );
  }

  const successRatePct = result ? (result.success_rate * 100).toFixed(1) : '0.0';

  return (
    <div className="wfp-apt-panel">
      <style>{`
.wfp-apt-input{width:100%;box-sizing:border-box;padding:7px 10px;font-size:13px;font-weight:500;font-family:inherit;color:${isDark ? darkTokens.text.primary : '#333333'};background-color:${isDark ? darkTokens.surface.bg : '#ffffff'};border:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};border-radius:5px;outline:none;transition:background-color 120ms ease,border-color 120ms ease;}
.wfp-apt-input:hover{background-color:${isDark ? darkTokens.surface.bgHover : '#fafafa'}}
.wfp-apt-input:focus{border-color:${isDark ? darkTokens.surface.borderStrong : '#bdbdbd'}}
.wfp-apt-input::placeholder{color:${isDark ? darkTokens.text.hint : '#b0b0b0'}}
.wfp-apt-table{width:100%;border-collapse:separate;border-spacing:0;font-size:12px;border:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};border-radius:8px;overflow:hidden;}
.wfp-apt-table thead th{background:${isDark ? darkTokens.surface.bgHover : '#f5f5f5'};font-weight:600;text-align:left;padding:8px 12px;border-bottom:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};color:${isDark ? darkTokens.text.primary : '#1a1a1a'}}
.wfp-apt-table tbody td{padding:7px 12px;border-bottom:1px solid ${isDark ? darkTokens.surface.divider : '#eee'};color:${isDark ? darkTokens.text.primary : '#1a1a1a'}}
.wfp-apt-table tbody tr:last-child td{border-bottom:none}
.wfp-apt-table tbody tr:hover{background:${isDark ? darkTokens.surface.bgHover : '#fafafa'}}
.wfp-apt-btn{font-size:13px;font-weight:500;font-family:inherit;padding:6px 14px;border-radius:6px;cursor:pointer;outline:none;border:1px solid ${isDark ? darkTokens.surface.border : '#d0d0d0'};background:${isDark ? darkTokens.surface.bg : '#ffffff'};color:${isDark ? darkTokens.text.primary : '#1a1a1a'};transition:background-color 120ms ease,border-color 120ms ease;}
.wfp-apt-btn:hover{background:${isDark ? darkTokens.surface.bgHover : '#f5f5f5'};border-color:${isDark ? darkTokens.surface.borderStrong : '#b8b8b8'}}
.wfp-apt-btn:active{background:${isDark ? darkTokens.surface.bgActive : '#e8e8e8'}}
.wfp-apt-btn:disabled{opacity:0.5;cursor:not-allowed}
.wfp-apt-btn--primary{background:${isDark?darkTokens.surface.bgActive:"#1a1a1a"};color:${isDark?darkTokens.text.primary:"#ffffff"};border-color:${isDark?darkTokens.surface.borderStrong:"#1a1a1a"}}
.wfp-apt-btn--primary:hover{background:${isDark?darkTokens.surface.bgHover:"#333333"}}
.wfp-apt-panel{padding:20px 24px;background:${isDark?darkTokens.surface.bg:"#ffffff"};border:1px solid ${isDark?darkTokens.surface.divider:"#e0e0e0"};border-radius:8px;}
.wfp-apt-disabled{text-align:center;padding:48px 16px;font-size:14px;color:${isDark?darkTokens.text.hint:"#9e9e9e"};}
.wfp-apt-title{font-size:15px;font-weight:600;margin:0 0 16px;padding-bottom:12px;border-bottom:1px solid ${isDark?darkTokens.surface.divider:"#e0e0e0"};color:${isDark?darkTokens.text.primary:"#1a1a1a"};}
.wfp-apt-form{display:flex;gap:12px;align-items:flex-end;flex-wrap:wrap;margin-bottom:16px;}
.wfp-apt-field{display:flex;flex-direction:column;font-size:12px;color:${isDark?darkTokens.text.secondary:"#616161"};}
.wfp-apt-field>.wfp-apt-input{margin-top:4px;}
.wfp-apt-error{margin-bottom:16px;font-size:13px;color:${isDark?darkTokens.danger:"#d32f2f"};}
.wfp-apt-result{margin-top:16px;}
.wfp-apt-metrics{display:flex;gap:12px;flex-wrap:wrap;}
.wfp-apt-metric{border:1px solid ${isDark?darkTokens.surface.divider:"#e0e0e0"};border-radius:6px;padding:12px 16px;min-width:140px;background:${isDark?darkTokens.surface.bg:"#ffffff"};}
.wfp-apt-metric-label{font-size:12px;color:${isDark?darkTokens.text.hint:"#9e9e9e"};}
.wfp-apt-metric-value{font-size:20px;font-weight:600;margin-top:4px;color:${isDark?darkTokens.text.primary:"#1a1a1a"};}
.wfp-apt-progress-section{margin-top:16px;}
.wfp-apt-progress-label{font-size:12px;color:${isDark?darkTokens.text.secondary:"#616161"};margin-bottom:4px;}
.wfp-apt-progress{width:100%;max-width:480px;height:14px;background:${isDark?darkTokens.surface.bgHover:"#eeeeee"};border-radius:7px;overflow:hidden;}
.wfp-apt-progress-bar{height:100%;background:${isDark?"#66bb6a":"#2e7d32"};transition:width 0.3s;}
.wfp-apt-actions{margin-top:12px;display:flex;gap:8px;}
.wfp-apt-warning{color:${isDark?"#ffa726":"#f57c00"};font-size:13px;margin-top:12px;}
`}</style>
      <h2 className="wfp-apt-title">Agent 绩效聚合</h2>

      {/* 查询表单 */}
      <div className="wfp-apt-form">
        <label className="wfp-apt-field">
          Agent ID
          <input
            value={agentId}
            onChange={(e) => setAgentId(e.target.value)}
            className="wfp-apt-input"
            style={{ minWidth: '160px' }}
          />
        </label>
        <label className="wfp-apt-field">
          起始时间
          <input
            type="datetime-local"
            value={start}
            onChange={(e) => setStart(e.target.value)}
            className="wfp-apt-input"
          />
        </label>
        <label className="wfp-apt-field">
          结束时间
          <input
            type="datetime-local"
            value={end}
            onChange={(e) => setEnd(e.target.value)}
            className="wfp-apt-input"
          />
        </label>
        <button onClick={handleQuery} disabled={loading} className="wfp-apt-btn wfp-apt-btn--primary">
          {loading ? '查询中…' : '查询'}
        </button>
      </div>

      {error && (
        <p className="wfp-apt-error">查询失败：{error}</p>
      )}

      {/* 聚合结果可视化 */}
      {result && (
        <div className="wfp-apt-result">
          {/* 四维指标卡片 */}
          <div className="wfp-apt-metrics">
            <MetricCard label="成功率" value={`${successRatePct}%`} />
            <MetricCard label="平均耗时" value={`${result.avg_duration_ms.toFixed(1)} ms`} />
            <MetricCard label="Token 消耗" value={String(result.total_tokens)} />
            <MetricCard label="质量评分均值" value={result.avg_quality_score.toFixed(3)} />
          </div>

          {/* 成功率进度条（网页可视化） */}
          <div className="wfp-apt-progress-section">
            <div className="wfp-apt-progress-label">
              成功率可视化
            </div>
            <div className="wfp-apt-progress">
              <div
                className="wfp-apt-progress-bar"
                style={{ width: `${successRatePct}%` }}
              />
            </div>
          </div>

          {/* 明细信息 */}
          <table className="wfp-apt-table" style={{ marginTop: '16px' }}>
            <tbody>
              <TableRow label="样本数" value={String(result.sample_count)} />
              <TableRow label="数据来源" value={result.data_source} />
              <TableRow label="统计起始" value={result.start} />
              <TableRow label="统计结束" value={result.end} />
            </tbody>
          </table>

          {/* 导出下载 */}
          <div className="wfp-apt-actions">
            <button onClick={handleExportJson} className="wfp-apt-btn">导出 JSON</button>
            <button onClick={handleExportCsv} className="wfp-apt-btn">导出 CSV</button>
          </div>

          {/* 无数据零值提示（A3.3.a） */}
          {result.sample_count === 0 && (
            <p className="wfp-apt-warning">
              统计范围内无绩效明细记录，以上为零值聚合结果。
            </p>
          )}
        </div>
      )}
    </div>
  );
}

/** 指标卡片子组件。 */
function MetricCard({ label, value }: { label: string; value: string }): JSX.Element {
  return (
    <div className="wfp-apt-metric">
      <div className="wfp-apt-metric-label">{label}</div>
      <div className="wfp-apt-metric-value">{value}</div>
    </div>
  );
}

/** 明细表格行子组件。 */
function TableRow({ label, value }: { label: string; value: string }): JSX.Element {
  return (
    <tr>
      <td>{label}</td>
      <td>{value}</td>
    </tr>
  );
}
