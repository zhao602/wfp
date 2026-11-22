// =============================================================================
// 评估框架类型定义（web/src/evaluation/evaluationTypes.ts）
//
// 与后端 Pydantic 模型一一对应（TypeScript 严格模式）
// 对应偏差修正 T182-T191 / M28
// =============================================================================

/** 评估类型 */
export type EvaluationType = 'baseline' | 'regression';

/** 评估任务状态 */
export type EvaluationTaskState = 'pending' | 'running' | 'completed' | 'interrupted' | 'cancelled';

/** 评分器类型 */
export type ScorerType = 'rule_based' | 'llm';

/** CLEAR 四维评分 */
export interface ClearScore {
  cost_efficiency: number;
  reliability: number;
  latency: number;
  policy_compliance: number;
  overall: number;
  rationale: string;
}

/** 场景条目（请求） */
export interface ScenarioItem {
  name: string;
  description: string;
  input_prompt: string;
  expected_output?: string | null;
  expected_keywords: string[];
  metadata: Record<string, unknown>;
  weight: number;
}

/** 创建场景集请求 */
export interface CreateScenarioSetRequest {
  name: string;
  tenant_id: string;
  description: string;
  scenarios: ScenarioItem[];
}

/** 场景集响应 */
export interface ScenarioSetResponse {
  set_id: string;
  name: string;
  description: string;
  tenant_id: string;
  scenario_count: number;
  created_at: string;
}

/** 启动评估请求 */
export interface StartEvaluationRequest {
  scenario_set_id: string;
  tenant_id: string;
  evaluation_type: EvaluationType;
  baseline_id?: string | null;
}

/** 评估任务响应 */
export interface EvaluationTaskResponse {
  task_id: string;
  scenario_set_id: string;
  tenant_id: string;
  evaluation_type: EvaluationType;
  state: EvaluationTaskState;
  progress_percentage: number;
  passed_scenarios: number;
  failed_scenarios: number;
  total_scenarios: number;
  degraded: boolean;
  average_overall: number;
  completed_at: string | null;
  error_message: string | null;
}

/** 创建基线请求 */
export interface CreateBaselineRequest {
  task_id: string;
  description: string;
}

/** 基线响应 */
export interface BaselineResponse {
  baseline_id: string;
  scenario_set_id: string;
  tenant_id: string;
  description: string;
  average_overall: number;
  success_rate: number;
  created_at: string;
}

/** 评估框架状态 */
export interface EvaluationStatus {
  evaluation_enabled: boolean;
  llm_scoring_enabled: boolean;
  random_seed: number;
  regression_degrade_threshold: number;
  max_scenarios: number;
}

/** 轨迹评分（报告内嵌） */
export interface TrajectoryScore {
  trajectory_id: string;
  scenario_id: string;
  clear_score: ClearScore;
  scorer_type: ScorerType;
  scored_at: string;
}

/** 聚合指标（报告内嵌） */
export interface EvaluationMetrics {
  total_scenarios: number;
  passed_scenarios: number;
  failed_scenarios: number;
  average_clear_score: ClearScore;
  total_cost: number;
  average_latency_ms: number;
  success_rate: number;
}

/** 回归对比（报告内嵌） */
export interface RegressionComparison {
  comparison_id: string;
  baseline_id: string;
  current_metrics: EvaluationMetrics;
  baseline_metrics: EvaluationMetrics;
  clear_delta: ClearScore;
  degraded: boolean;
  degrade_details: string[];
  compared_at: string;
}

/** 评估报告 */
export interface EvaluationReport {
  report_id: string;
  task_id: string;
  scenario_set_id: string;
  evaluation_type: EvaluationType;
  metrics: EvaluationMetrics;
  trajectory_scores: TrajectoryScore[];
  regression_comparison: RegressionComparison | null;
  generated_at: string;
}
