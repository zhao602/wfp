// =============================================================================
// 评估仪表盘（web/src/evaluation/EvaluationDashboard.tsx）
//
// 职责：评估场景集、任务、基线、回归对比的可视化面板。
// 设计意图（对应偏差修正 T191）：
//   - 场景集列表与新建；
//   - 启动基线/回归评估并展示进度与 CLEAR 四维评分；
//   - 基线冻结与回归退化告警；
//   - evaluation_enabled=False 时提示未启用。
// =============================================================================

import { useEffect, useState, useCallback } from 'react';
import { useThemeMode, darkTokens } from '../theme/themeMode';
import type {
  BaselineResponse,
  EvaluationStatus,
  EvaluationTaskResponse,
  EvaluationType,
  ScenarioItem,
  ScenarioSetResponse,
} from './evaluationTypes';
import {
  createScenarioSet,
  deleteScenarioSet,
  getEvaluationStatus,
  listBaselines,
  listScenarioSets,
  listTasks,
  startEvaluation,
  createBaseline,
} from './evaluationApiClient';

/** 评估仪表盘 */
export function EvaluationDashboard({ tenantId }: { tenantId: string }): JSX.Element {
  const themeMode = useThemeMode();
  const isDark = themeMode === 'dark';
  const [status, setStatus] = useState<EvaluationStatus | null>(null);
  const [scenarioSets, setScenarioSets] = useState<ScenarioSetResponse[]>([]);
  const [tasks, setTasks] = useState<EvaluationTaskResponse[]>([]);
  const [baselines, setBaselines] = useState<BaselineResponse[]>([]);
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [selectedSetId, setSelectedSetId] = useState<string>('');

  const loadData = useCallback(async () => {
    try {
      const [s, sets, ts, bls] = await Promise.all([
        getEvaluationStatus(),
        listScenarioSets(tenantId),
        listTasks(tenantId),
        listBaselines(tenantId),
      ]);
      setStatus(s);
      setScenarioSets(sets);
      setTasks(ts);
      setBaselines(bls);
    } catch (err) {
      console.error('加载评估数据失败:', err);
    }
  }, [tenantId]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  if (status && !status.evaluation_enabled) {
    return <div className="wfp-adv-disabled">Agent 效能评估框架未启用</div>;
  }

  const handleStartEvaluation = async (
    setId: string,
    type: EvaluationType,
    baselineId?: string,
  ) => {
    try {
      await startEvaluation({
        scenario_set_id: setId,
        tenant_id: tenantId,
        evaluation_type: type,
        baseline_id: baselineId || null,
      });
      loadData();
    } catch (err) {
      console.error('启动评估失败:', err);
    }
  };

  const handleFreezeBaseline = async (taskId: string) => {
    try {
      await createBaseline({ task_id: taskId, description: '' });
      loadData();
    } catch (err) {
      console.error('冻结基线失败:', err);
    }
  };

  const handleDeleteSet = async (setId: string) => {
    try {
      await deleteScenarioSet(setId, tenantId);
      loadData();
    } catch (err) {
      console.error('删除场景集失败:', err);
    }
  };

  return (
    <div className="wfp-adv-panel">
      <style>{`
.wfp-adv-input { width: 100%; box-sizing: border-box; padding: 7px 10px; font-size: 13px; font-weight: 500; font-family: inherit; color: ${isDark ? darkTokens.text.primary : '#333333'}; background-color: ${isDark ? darkTokens.surface.bg : '#ffffff'}; border: 1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'}; border-radius: 5px; outline: none; transition: background-color 120ms ease, border-color 120ms ease; }
.wfp-adv-input:hover { background-color: ${isDark ? darkTokens.surface.bgHover : '#fafafa'}; }
.wfp-adv-input:focus { border-color: ${isDark ? darkTokens.surface.borderStrong : '#bdbdbd'}; }
.wfp-adv-input::placeholder { color: ${isDark ? darkTokens.text.hint : '#b0b0b0'}; }
.wfp-adv-table { width: 100%; border-collapse: separate; border-spacing: 0; font-size: 12px; border: 1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'}; border-radius: 8px; overflow: hidden; }
.wfp-adv-table thead th { background: ${isDark ? darkTokens.surface.bgHover : '#f5f5f5'}; font-weight: 600; text-align: left; padding: 8px 12px; border-bottom: 1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'}; color: ${isDark ? darkTokens.text.primary : '#1a1a1a'}; }
.wfp-adv-table tbody td { padding: 7px 12px; border-bottom: 1px solid ${isDark ? darkTokens.surface.divider : '#eee'}; color: ${isDark ? darkTokens.text.primary : '#1a1a1a'}; }
.wfp-adv-table tbody tr:last-child td { border-bottom: none; }
.wfp-adv-table tbody tr:hover { background: ${isDark ? darkTokens.surface.bgHover : '#fafafa'}; }
.wfp-adv-btn { font-size: 13px; font-weight: 500; font-family: inherit; padding: 6px 14px; border-radius: 6px; cursor: pointer; outline: none; border: 1px solid ${isDark ? darkTokens.surface.border : '#d0d0d0'}; background: ${isDark ? darkTokens.surface.bg : '#ffffff'}; color: ${isDark ? darkTokens.text.primary : '#1a1a1a'}; transition: background-color 120ms ease, border-color 120ms ease; }
.wfp-adv-btn:hover { background: ${isDark ? darkTokens.surface.bgHover : '#f5f5f5'}; border-color: ${isDark ? darkTokens.surface.borderStrong : '#b8b8b8'}; }
.wfp-adv-btn:active { background: ${isDark ? darkTokens.surface.bgActive : '#e8e8e8'}; }
.wfp-adv-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.wfp-adv-btn--primary { background: ${isDark ? darkTokens.text.primary : '#1a1a1a'}; color: ${isDark ? darkTokens.surface.bg : '#ffffff'}; border-color: ${isDark ? darkTokens.text.primary : '#1a1a1a'}; }
.wfp-adv-btn--primary:hover { background: ${isDark ? darkTokens.text.secondary : '#333333'}; }
.wfp-adv-btn--danger { color: ${isDark ? darkTokens.danger : '#d32f2f'}; background: transparent; border-color: ${isDark ? darkTokens.danger : '#d32f2f'}; }
.wfp-adv-btn--danger:hover { background: ${isDark ? 'rgba(255,107,107,0.12)' : '#fdecea'}; }
.wfp-adv-btn--sm { padding: 4px 10px; border-radius: 4px; font-size: 12px; }
.wfp-adv-select { width: 100%; box-sizing: border-box; padding: 7px 26px 7px 10px; font-size: 13px; font-weight: 500; font-family: inherit; color: ${isDark ? darkTokens.text.primary : '#333333'}; background-color: ${isDark ? darkTokens.surface.bg : '#ffffff'}; background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='8' height='5' viewBox='0 0 8 5'%3E%3Cpath d='M0 0 L4 5 L8 0 Z' fill='${isDark ? '%238a8a8a' : '%23757575'}'/%3E%3C/svg%3E"); background-repeat: no-repeat; background-position: right 9px center; border: 1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'}; border-radius: 5px; cursor: pointer; outline: none; -webkit-appearance: none; -moz-appearance: none; appearance: none; color-scheme: ${isDark ? 'dark' : 'light'}; transition: background-color 120ms ease, border-color 120ms ease; }
.wfp-adv-select:hover { background-color: ${isDark ? darkTokens.surface.bgHover : '#fafafa'}; }
.wfp-adv-select:focus { border-color: ${isDark ? darkTokens.surface.borderStrong : '#bdbdbd'}; }
.wfp-adv-panel { padding: 20px 24px; background: ${isDark ? darkTokens.surface.bg : '#ffffff'}; border: 1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'}; border-radius: 8px; }
.wfp-adv-disabled { text-align: center; padding: 48px 16px; font-size: 14px; color: ${isDark ? darkTokens.text.hint : '#9e9e9e'}; }
.wfp-adv-title { font-size: 15px; font-weight: 600; margin: 0 0 16px; padding-bottom: 12px; border-bottom: 1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'}; color: ${isDark ? darkTokens.text.primary : '#1a1a1a'}; }
.wfp-adv-status-bar { display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 16px; padding: 10px 14px; background: ${isDark ? darkTokens.surface.bgHover : '#fafafa'}; border: 1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'}; border-radius: 6px; font-size: 12px; color: ${isDark ? darkTokens.text.secondary : '#616161'}; }
.wfp-adv-section { margin-bottom: 20px; }
.wfp-adv-section:last-child { margin-bottom: 0; }
.wfp-adv-section-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.wfp-adv-section-title { font-size: 14px; font-weight: 600; margin: 0; color: ${isDark ? darkTokens.text.primary : '#1a1a1a'}; }
.wfp-adv-form { margin-bottom: 16px; padding: 16px; background: ${isDark ? darkTokens.surface.bgHover : '#fafafa'}; border: 1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'}; border-radius: 8px; }
.wfp-adv-form .wfp-adv-input { margin-bottom: 10px; }
.wfp-adv-scenario-row { margin-bottom: 10px; padding-bottom: 10px; border-bottom: 1px solid ${isDark ? darkTokens.surface.divider : '#f0f0f0'}; }
.wfp-adv-scenario-row .wfp-adv-input { margin-bottom: 6px; }
.wfp-adv-form .wfp-adv-btn { margin-right: 8px; }
.wfp-adv-row-degraded { background: ${isDark ? 'rgba(255,107,107,0.06)' : '#fff5f5'}; }
.wfp-adv-empty { text-align: center; padding: 24px 16px; color: ${isDark ? darkTokens.text.hint : '#9e9e9e'}; font-size: 13px; }
`}</style>
      <h2 className="wfp-adv-title">Agent 效能评估</h2>

      {status && (
        <div className="wfp-adv-status-bar">
          <span>LLM 评分: {status.llm_scoring_enabled ? '启用' : '未启用（纯规则）'}</span>
          <span>随机种子: {status.random_seed}</span>
          <span>退化阈值: {status.regression_degrade_threshold}</span>
          <span>场景上限: {status.max_scenarios}</span>
        </div>
      )}

      {/* 场景集管理 */}
      <section className="wfp-adv-section">
        <div className="wfp-adv-section-header">
          <h3 className="wfp-adv-section-title">场景集</h3>
          <button className="wfp-adv-btn wfp-adv-btn--primary" onClick={() => setShowCreateForm(!showCreateForm)}>
            {showCreateForm ? '取消' : '新建场景集'}
          </button>
        </div>
        {showCreateForm && (
          <CreateScenarioSetForm
            tenantId={tenantId}
            onCreated={() => {
              setShowCreateForm(false);
              loadData();
            }}
          />
        )}
        <table className="wfp-adv-table">
          <thead>
            <tr>
              <th>名称</th>
              <th>场景数</th>
              <th>创建时间</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {scenarioSets.length === 0 ? (
              <tr><td colSpan={4} className="wfp-adv-empty">暂无场景集</td></tr>
            ) : scenarioSets.map((s) => (
              <tr key={s.set_id}>
                <td>{s.name}</td>
                <td>{s.scenario_count}</td>
                <td>{s.created_at}</td>
                <td>
                  <button className="wfp-adv-btn" onClick={() => handleStartEvaluation(s.set_id, 'baseline')}>
                    基线评估
                  </button>
                  {baselines.length > 0 && (
                    <select
                      className="wfp-adv-select"
                      value={selectedSetId}
                      onChange={(e) => setSelectedSetId(e.target.value)}
                    >
                      <option value="">选择基线...</option>
                      {baselines
                        .filter((b) => b.scenario_set_id === s.set_id)
                        .map((b) => (
                          <option key={b.baseline_id} value={b.baseline_id}>
                            {b.baseline_id.slice(-8)} ({(b.average_overall * 100).toFixed(1)}%)
                          </option>
                        ))}
                    </select>
                  )}
                  {selectedSetId && (
                    <button
                      className="wfp-adv-btn"
                      onClick={() =>
                        handleStartEvaluation(s.set_id, 'regression', selectedSetId)
                      }
                    >
                      回归评估
                    </button>
                  )}
                  <button className="wfp-adv-btn wfp-adv-btn--danger wfp-adv-btn--sm" onClick={() => handleDeleteSet(s.set_id)}>删除</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {/* 评估任务 */}
      <section className="wfp-adv-section">
        <h3 className="wfp-adv-section-title">评估任务</h3>
        <table className="wfp-adv-table">
          <thead>
            <tr>
              <th>任务ID</th>
              <th>类型</th>
              <th>状态</th>
              <th>进度</th>
              <th>通过/总数</th>
              <th>综合分</th>
              <th>退化</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {tasks.length === 0 ? (
              <tr><td colSpan={8} className="wfp-adv-empty">暂无评估任务</td></tr>
            ) : tasks.map((t) => (
              <tr key={t.task_id} className={t.degraded ? 'wfp-adv-row-degraded' : ''}>
                <td>{t.task_id.slice(-8)}</td>
                <td>{t.evaluation_type === 'baseline' ? '基线' : '回归'}</td>
                <td>{t.state}</td>
                <td>{t.progress_percentage.toFixed(1)}%</td>
                <td>
                  {t.passed_scenarios}/{t.total_scenarios}
                </td>
                <td>{(t.average_overall * 100).toFixed(1)}%</td>
                <td>{t.degraded ? '⚠ 退化' : '—'}</td>
                <td>
                  {t.evaluation_type === 'baseline' && t.state === 'completed' && (
                    <button className="wfp-adv-btn" onClick={() => handleFreezeBaseline(t.task_id)}>
                      冻结基线
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {/* 基线列表 */}
      <section className="wfp-adv-section">
        <h3 className="wfp-adv-section-title">基线记录</h3>
        <table className="wfp-adv-table">
          <thead>
            <tr>
              <th>基线ID</th>
              <th>场景集</th>
              <th>综合分</th>
              <th>成功率</th>
              <th>创建时间</th>
            </tr>
          </thead>
          <tbody>
            {baselines.length === 0 ? (
              <tr><td colSpan={5} className="wfp-adv-empty">暂无基线记录</td></tr>
            ) : baselines.map((b) => (
              <tr key={b.baseline_id}>
                <td>{b.baseline_id.slice(-12)}</td>
                <td>{b.scenario_set_id.slice(-8)}</td>
                <td>{(b.average_overall * 100).toFixed(1)}%</td>
                <td>{(b.success_rate * 100).toFixed(1)}%</td>
                <td>{b.created_at}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}

/** 创建场景集表单 */
function CreateScenarioSetForm({
  tenantId,
  onCreated,
}: {
  tenantId: string;
  onCreated: () => void;
}): JSX.Element {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [scenarios, setScenarios] = useState<ScenarioItem[]>([
    {
      name: '',
      description: '',
      input_prompt: '',
      expected_output: null,
      expected_keywords: [],
      metadata: {},
      weight: 1.0,
    },
  ]);

  const handleAddScenario = () => {
    setScenarios([
      ...scenarios,
      {
        name: '',
        description: '',
        input_prompt: '',
        expected_output: null,
        expected_keywords: [],
        metadata: {},
        weight: 1.0,
      },
    ]);
  };

  const handleScenarioChange = (index: number, field: keyof ScenarioItem, value: unknown) => {
    const updated = [...scenarios];
    (updated[index] as unknown as Record<string, unknown>)[field] = value;
    setScenarios(updated);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await createScenarioSet({ name, tenant_id: tenantId, description, scenarios });
      onCreated();
    } catch (err) {
      console.error('创建场景集失败:', err);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="wfp-adv-form">
      <input
        className="wfp-adv-input"
        placeholder="场景集名称"
        value={name}
        onChange={(e) => setName(e.target.value)}
        required
      />
      <input
        className="wfp-adv-input"
        placeholder="描述（可选）"
        value={description}
        onChange={(e) => setDescription(e.target.value)}
      />
      {scenarios.map((sc, i) => (
        <div key={i} className="wfp-adv-scenario-row">
          <input
            className="wfp-adv-input"
            placeholder="场景名称"
            value={sc.name}
            onChange={(e) => handleScenarioChange(i, 'name', e.target.value)}
          />
          <input
            className="wfp-adv-input"
            placeholder="输入提示词"
            value={sc.input_prompt}
            onChange={(e) => handleScenarioChange(i, 'input_prompt', e.target.value)}
            required
          />
          <input
            className="wfp-adv-input"
            placeholder="期望关键词（逗号分隔）"
            value={sc.expected_keywords.join(', ')}
            onChange={(e) =>
              handleScenarioChange(
                i,
                'expected_keywords',
                e.target.value.split(',').map((s) => s.trim()).filter(Boolean),
              )
            }
          />
        </div>
      ))}
      <button className="wfp-adv-btn" type="button" onClick={handleAddScenario}>
        添加场景
      </button>
      <button className="wfp-adv-btn wfp-adv-btn--primary" type="submit">保存场景集</button>
    </form>
  );
}
