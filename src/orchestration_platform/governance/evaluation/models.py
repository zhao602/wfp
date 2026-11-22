# =============================================================================
# 评估领域模型（governance/evaluation/models.py）
#
# 职责：定义评估场景、轨迹、评分、基线、回归对比等全部领域模型。
# 设计意图（对应偏差修正 T182-T183）：
#   1. EvaluationScenario / EvaluationScenarioSet —— 评估场景与场景集；
#   2. AgentTrajectory —— Agent 执行轨迹（步骤、成本、延迟、工具调用）；
#   3. ClearScore —— CLEAR 四维评分（成本效率/可靠性/延迟/策略合规）；
#   4. TrajectoryScore —— 单条轨迹评分结果；
#   5. EvaluationMetrics —— 场景集聚合指标；
#   6. BaselineRecord —— 冻结基线记录；
#   7. RegressionComparison —— 回归对比（差异与退化判定）；
#   8. EvaluationTask / EvaluationProgress / EvaluationReport —— 评估任务编排。
# =============================================================================

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from orchestration_platform.domain.models.enums import (
    EvaluationTaskState,
    EvaluationType,
)


def _utc_now() -> datetime:
    """返回当前 UTC 时间。"""
    return datetime.now(timezone.utc)


# =============================================================================
# 评估场景
# =============================================================================

class EvaluationScenario(BaseModel):
    """评估场景：单条 Agent 输入与期望约束。

    期望约束以关键词集合与可选的期望输出文本表达，规则评分据此判定。
    """

    scenario_id: str = Field(description="场景唯一标识")
    scenario_set_id: str = Field(description="所属场景集标识")
    name: str = Field(default="", description="场景名称")
    description: str = Field(default="", description="场景描述")
    input_prompt: str = Field(description="输入提示词")
    expected_output: Optional[str] = Field(
        default=None, description="期望输出文本（可选，用于精确匹配）"
    )
    expected_keywords: List[str] = Field(
        default_factory=list, description="期望关键词集合（用于包含判定）"
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict, description="附加元数据（如领域标签、难度）"
    )
    weight: float = Field(default=1.0, ge=0.0, description="场景权重")
    created_at: datetime = Field(default_factory=_utc_now, description="创建时间")


class EvaluationScenarioSet(BaseModel):
    """评估场景集：一组可复用的评估场景。

    按租户隔离，条目数受 evaluation_max_scenarios 约束。
    """

    set_id: str = Field(description="场景集唯一标识")
    name: str = Field(description="场景集名称")
    description: str = Field(default="", description="场景集描述")
    tenant_id: str = Field(description="所属租户标识")
    scenarios: List[EvaluationScenario] = Field(
        default_factory=list, description="场景条目"
    )
    created_at: datetime = Field(default_factory=_utc_now, description="创建时间")
    updated_at: datetime = Field(default_factory=_utc_now, description="更新时间")


# =============================================================================
# Agent 执行轨迹
# =============================================================================

class AgentTrajectory(BaseModel):
    """Agent 执行轨迹：单次场景执行的步骤序列与汇总指标。

    由评估运行器在执行场景时采集，作为评分输入。
    """

    trajectory_id: str = Field(description="轨迹唯一标识")
    scenario_id: str = Field(description="对应场景标识")
    task_id: str = Field(description="对应评估任务标识")
    steps: List[Dict[str, Any]] = Field(
        default_factory=list, description="执行步骤序列（每步含动作、输入、输出、耗时）"
    )
    final_output: str = Field(default="", description="Agent 最终输出文本")
    total_cost: float = Field(default=0.0, ge=0.0, description="总成本（货币单位）")
    total_latency_ms: int = Field(default=0, ge=0, description="总延迟（毫秒）")
    tool_call_count: int = Field(default=0, ge=0, description="工具调用次数")
    success: bool = Field(default=True, description="是否成功完成")
    error_message: Optional[str] = Field(default=None, description="失败时的错误信息")
    started_at: datetime = Field(default_factory=_utc_now, description="开始时间")
    completed_at: Optional[datetime] = Field(
        default=None, description="完成时间（未完成为 None）"
    )


# =============================================================================
# CLEAR 四维评分
# =============================================================================

class ClearScore(BaseModel):
    """CLEAR 四维评分。

    四个维度均归一化至 [0, 1]，越高越好：
      - cost_efficiency：成本效率（同等产出下成本越低越高）；
      - reliability：可靠性（成功率与输出稳定性）；
      - latency：延迟（延迟越低越高，归一化）；
      - policy_compliance：策略合规（违规越少越高）。
    overall 为加权综合分，亦在 [0, 1]。

    注意：字段未强制 ge/le 约束，以兼容 RegressionComparison.clear_delta
    （差异值可为负）。实际评分由 ClearScorer._clamp 保证落在 [0, 1]。
    """

    cost_efficiency: float = Field(default=0.0, description="成本效率分")
    reliability: float = Field(default=0.0, description="可靠性分")
    latency: float = Field(default=0.0, description="延迟分（归一化，越高越好）")
    policy_compliance: float = Field(
        default=1.0, description="策略合规分"
    )
    overall: float = Field(default=0.0, description="综合分")
    rationale: str = Field(default="", description="评分说明（各维度依据）")


class TrajectoryScore(BaseModel):
    """单条轨迹评分结果。"""

    trajectory_id: str = Field(description="轨迹标识")
    scenario_id: str = Field(description="场景标识")
    clear_score: ClearScore = Field(description="CLEAR 四维评分")
    scorer_type: str = Field(
        default="rule_based", description="评分器类型：rule_based / llm"
    )
    scored_at: datetime = Field(default_factory=_utc_now, description="评分时间")


# =============================================================================
# 聚合指标
# =============================================================================

class EvaluationMetrics(BaseModel):
    """场景集聚合指标。

    汇总全部场景的通过情况、平均 CLEAR 分、总成本、平均延迟、成功率。
    """

    total_scenarios: int = Field(default=0, ge=0, description="场景总数")
    passed_scenarios: int = Field(default=0, ge=0, description="通过场景数")
    failed_scenarios: int = Field(default=0, ge=0, description="失败场景数")
    average_clear_score: ClearScore = Field(
        default_factory=ClearScore, description="平均 CLEAR 四维评分"
    )
    total_cost: float = Field(default=0.0, ge=0.0, description="总成本")
    average_latency_ms: float = Field(
        default=0.0, ge=0.0, description="平均延迟（毫秒）"
    )
    success_rate: float = Field(
        default=0.0, ge=0.0, le=1.0, description="成功率"
    )


# =============================================================================
# 基线与回归
# =============================================================================

class BaselineRecord(BaseModel):
    """基线记录：冻结某次基线评估的指标与轨迹评分。

    作为后续回归评估的对比基准，不可变。
    """

    baseline_id: str = Field(description="基线唯一标识")
    scenario_set_id: str = Field(description="对应场景集标识")
    tenant_id: str = Field(description="所属租户标识")
    description: str = Field(default="", description="基线描述")
    metrics: EvaluationMetrics = Field(description="基线聚合指标")
    trajectory_scores: List[TrajectoryScore] = Field(
        default_factory=list, description="基线轨迹评分清单"
    )
    created_at: datetime = Field(default_factory=_utc_now, description="创建时间")


class RegressionComparison(BaseModel):
    """回归对比：当前评估与基线的差异及退化判定。

    clear_delta 各维度为 当前减基线，正为提升，负为退化。
    degraded 任一维度退化超过阈值即为 True。
    """

    comparison_id: str = Field(description="对比唯一标识")
    baseline_id: str = Field(description="对比基线标识")
    current_metrics: EvaluationMetrics = Field(description="当前指标")
    baseline_metrics: EvaluationMetrics = Field(description="基线指标")
    clear_delta: ClearScore = Field(description="CLEAR 各维度差异（当前减基线）")
    degraded: bool = Field(default=False, description="是否退化")
    degrade_details: List[str] = Field(
        default_factory=list, description="退化维度明细"
    )
    compared_at: datetime = Field(default_factory=_utc_now, description="对比时间")


# =============================================================================
# 评估任务编排
# =============================================================================

class EvaluationProgress(BaseModel):
    """评估任务进度。"""

    total_scenarios: int = Field(default=0, ge=0, description="场景总数")
    completed_scenarios: int = Field(default=0, ge=0, description="已完成场景数")
    failed_scenarios: int = Field(default=0, ge=0, description="失败场景数")
    current_scenario_id: Optional[str] = Field(
        default=None, description="当前正在执行的场景标识"
    )
    percentage: float = Field(default=0.0, ge=0.0, le=100.0, description="完成百分比")


class EvaluationReport(BaseModel):
    """评估报告：单次评估任务的完整产出。"""

    report_id: str = Field(description="报告唯一标识")
    task_id: str = Field(description="对应评估任务标识")
    scenario_set_id: str = Field(description="对应场景集标识")
    evaluation_type: EvaluationType = Field(description="评估类型")
    metrics: EvaluationMetrics = Field(description="聚合指标")
    trajectory_scores: List[TrajectoryScore] = Field(
        default_factory=list, description="轨迹评分清单"
    )
    regression_comparison: Optional[RegressionComparison] = Field(
        default=None, description="回归对比（仅回归评估有值）"
    )
    generated_at: datetime = Field(default_factory=_utc_now, description="生成时间")


class EvaluationTask(BaseModel):
    """评估任务：一次基线或回归评估的执行单元。"""

    task_id: str = Field(description="任务唯一标识")
    scenario_set_id: str = Field(description="对应场景集标识")
    tenant_id: str = Field(description="所属租户标识")
    evaluation_type: EvaluationType = Field(description="评估类型")
    state: EvaluationTaskState = Field(
        default=EvaluationTaskState.PENDING, description="任务状态"
    )
    baseline_id: Optional[str] = Field(
        default=None, description="回归对比基线标识（仅回归评估）"
    )
    progress: EvaluationProgress = Field(
        default_factory=EvaluationProgress, description="执行进度"
    )
    report: Optional[EvaluationReport] = Field(
        default=None, description="评估报告（完成后填充）"
    )
    created_at: datetime = Field(default_factory=_utc_now, description="创建时间")
    started_at: Optional[datetime] = Field(
        default=None, description="开始时间"
    )
    completed_at: Optional[datetime] = Field(
        default=None, description="完成时间"
    )
    error_message: Optional[str] = Field(
        default=None, description="错误信息（中断/失败时填充）"
    )
