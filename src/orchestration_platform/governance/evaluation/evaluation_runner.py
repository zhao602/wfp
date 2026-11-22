# =============================================================================
# 评估运行器（governance/evaluation/evaluation_runner.py）
#
# 职责：评估任务的执行编排——按场景集逐条执行、采集轨迹、评分、聚合、产出报告。
# 设计意图（对应偏差修正 T190）：
#   1. 固定随机种子（evaluation_random_seed）保证可复现；
#   2. 规则评分默认；LLM 评分可选（evaluation_llm_scoring_enabled），失败回退规则；
#   3. 基线评估：产出指标并可选冻结基线；
#   4. 回归评估：对比基线产出 RegressionComparison；
#   5. 任务状态机：PENDING → RUNNING → COMPLETED / INTERRUPTED；
#   6. 轨迹执行委托 ITrajectoryExecutor（抽象），默认使用桩执行器（便于测试）。
# =============================================================================

import logging
import random
import uuid
from datetime import datetime, timezone
from typing import Any, List, Optional, Protocol, runtime_checkable

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.errors import (
    EVAL_BASELINE_MISSING,
    EVAL_SCENARIO_INVALID,
    EVAL_TASK_INTERRUPTED,
    DomainError,
)
from orchestration_platform.domain.models.enums import (
    EvaluationTaskState,
    EvaluationType,
)
from orchestration_platform.governance.evaluation.abstractions.evaluation_repository import (
    IEvaluationRepository,
)
from orchestration_platform.governance.evaluation.abstractions.trajectory_scorer import (
    ITrajectoryScorer,
)
from orchestration_platform.governance.evaluation.baseline_store import BaselineStore
from orchestration_platform.governance.evaluation.models import (
    AgentTrajectory,
    ClearScore,
    EvaluationMetrics,
    EvaluationReport,
    EvaluationScenario,
    EvaluationScenarioSet,
    EvaluationTask,
    EvaluationProgress,
    TrajectoryScore,
)
from orchestration_platform.governance.evaluation.regression_reporter import (
    RegressionReporter,
)
from orchestration_platform.governance.evaluation.rule_based_trajectory_scorer import (
    RuleBasedTrajectoryScorer,
)

_logger = logging.getLogger(__name__)


@runtime_checkable
class ITrajectoryExecutor(Protocol):
    """轨迹执行器抽象：执行单个场景并采集 Agent 轨迹。"""

    async def execute(
        self,
        scenario: EvaluationScenario,
        task_id: str,
    ) -> AgentTrajectory:
        """执行场景，返回 Agent 执行轨迹。"""
        ...


class StubTrajectoryExecutor:
    """桩轨迹执行器（默认，用于测试与无 Agent 环境的演示）。

    根据场景输入产出确定性轨迹：final_output 复述输入与期望关键词，
    成本/延迟/工具调用按确定性公式计算，保证可复现。
    """

    async def execute(
        self,
        scenario: EvaluationScenario,
        task_id: str,
    ) -> AgentTrajectory:
        """执行场景，产出确定性轨迹。"""
        # 确定性输出：复述输入并拼接期望关键词
        parts = [scenario.input_prompt]
        if scenario.expected_keywords:
            parts.append(" ".join(scenario.expected_keywords))
        final_output = " ".join(parts)

        # 确定性成本/延迟：基于输入长度与关键词数
        cost = round(0.01 + 0.001 * len(scenario.input_prompt), 6)
        latency = 100 + 10 * len(scenario.expected_keywords) + len(scenario.input_prompt)
        tool_calls = min(len(scenario.expected_keywords), 3)

        return AgentTrajectory(
            trajectory_id=f"eval-traj-{uuid.uuid4().hex[:12]}",
            scenario_id=scenario.scenario_id,
            task_id=task_id,
            steps=[
                {
                    "action": "generate",
                    "input": scenario.input_prompt,
                    "output": final_output,
                }
            ],
            final_output=final_output,
            total_cost=cost,
            total_latency_ms=latency,
            tool_call_count=tool_calls,
            success=True,
            completed_at=datetime.now(timezone.utc),
        )


class EvaluationRunner:
    """评估运行器。

    编排评估任务的全生命周期：执行 → 采集 → 评分 → 聚合 → 报告。
    """

    def __init__(
        self,
        repository: IEvaluationRepository,
        trajectory_scorer: Optional[ITrajectoryScorer] = None,
        trajectory_executor: Optional[Any] = None,
        baseline_store: Optional[BaselineStore] = None,
        regression_reporter: Optional[RegressionReporter] = None,
    ) -> None:
        """构造评估运行器。

        参数：
            repository          —— 评估仓储；
            trajectory_scorer   —— 轨迹评分器，缺省规则评分；
            trajectory_executor —— 轨迹执行器，缺省桩执行器；
            baseline_store      —— 基线存储；
            regression_reporter —— 回归报告器。
        """
        self._repository = repository
        self._scorer = trajectory_scorer or RuleBasedTrajectoryScorer()
        self._executor = trajectory_executor or StubTrajectoryExecutor()
        self._baseline_store = baseline_store or BaselineStore(repository)
        self._regression_reporter = regression_reporter or RegressionReporter()

    @staticmethod
    def generate_task_id() -> str:
        """生成评估任务标识。"""
        return f"eval-task-{uuid.uuid4().hex[:12]}"

    @staticmethod
    def generate_report_id() -> str:
        """生成评估报告标识。"""
        return f"eval-report-{uuid.uuid4().hex[:12]}"

    async def start_evaluation(
        self,
        scenario_set_id: str,
        tenant_id: str,
        evaluation_type: EvaluationType,
        baseline_id: Optional[str] = None,
    ) -> EvaluationTask:
        """启动评估任务并同步执行至完成。

        前置条件：场景集存在；回归评估需提供有效 baseline_id。
        后置条件：任务状态为 COMPLETED（或 INTERRUPTED），报告已填充。
        异常映射：EVAL_SCENARIO_INVALID / EVAL_BASELINE_MISSING / EVAL_TASK_INTERRUPTED。
        """
        # 固定随机种子，保证可复现
        random.seed(get_settings().evaluation_random_seed)

        # 加载场景集
        scenario_set = await self._repository.get_scenario_set(scenario_set_id)
        if scenario_set is None:
            raise DomainError(
                EVAL_SCENARIO_INVALID,
                f"场景集 {scenario_set_id} 不存在",
            )
        if scenario_set.tenant_id != tenant_id:
            raise DomainError(
                EVAL_SCENARIO_INVALID,
                f"场景集 {scenario_set_id} 不属于租户 {tenant_id}",
            )
        if not scenario_set.scenarios:
            raise DomainError(
                EVAL_SCENARIO_INVALID,
                f"场景集 {scenario_set_id} 无场景条目",
            )

        # 回归评估需校验基线
        baseline = None
        if evaluation_type == EvaluationType.REGRESSION:
            if not baseline_id:
                raise DomainError(
                    EVAL_BASELINE_MISSING,
                    "回归评估需提供 baseline_id",
                )
            baseline = await self._baseline_store.get_baseline(baseline_id)
            if baseline is None:
                raise DomainError(
                    EVAL_BASELINE_MISSING,
                    f"基线 {baseline_id} 不存在",
                )

        # 创建任务
        task_id = self.generate_task_id()
        total = len(scenario_set.scenarios)
        task = EvaluationTask(
            task_id=task_id,
            scenario_set_id=scenario_set_id,
            tenant_id=tenant_id,
            evaluation_type=evaluation_type,
            state=EvaluationTaskState.RUNNING,
            baseline_id=baseline_id,
            progress=EvaluationProgress(total_scenarios=total),
            started_at=datetime.now(timezone.utc),
        )
        await self._repository.save_task(task)

        # 逐场景执行与评分
        trajectory_scores: List[TrajectoryScore] = []
        trajectories: List[AgentTrajectory] = []
        completed = 0
        failed = 0

        for idx, scenario in enumerate(scenario_set.scenarios):
            try:
                trajectory = await self._executor.execute(scenario, task_id)
                score = await self._scorer.score(trajectory, scenario)
                trajectory_scores.append(score)
                trajectories.append(trajectory)
                if trajectory.success:
                    completed += 1
                else:
                    failed += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                _logger.error(
                    "场景 %s 执行失败: %s", scenario.scenario_id, exc
                )
                # 记录失败轨迹评分（零分）
                trajectory_scores.append(
                    TrajectoryScore(
                        trajectory_id=f"eval-traj-failed-{uuid.uuid4().hex[:8]}",
                        scenario_id=scenario.scenario_id,
                        clear_score=ClearScore(
                            cost_efficiency=0.0,
                            reliability=0.0,
                            latency=0.0,
                            policy_compliance=0.0,
                            overall=0.0,
                            rationale=f"执行失败: {exc}",
                        ),
                        scorer_type="rule_based",
                    )
                )

            # 更新进度
            progress = EvaluationProgress(
                total_scenarios=total,
                completed_scenarios=completed,
                failed_scenarios=failed,
                current_scenario_id=scenario.scenario_id,
                percentage=round((idx + 1) / total * 100.0, 2),
            )
            task = task.model_copy(update={"progress": progress})
            await self._repository.save_task(task)

        # 聚合指标
        metrics = self._aggregate_metrics(
            scenario_set, trajectories, trajectory_scores
        )

        # 回归对比
        regression_comparison = None
        if evaluation_type == EvaluationType.REGRESSION and baseline is not None:
            regression_comparison = await self._regression_reporter.compare(
                baseline, metrics
            )

        # 产出报告
        report = EvaluationReport(
            report_id=self.generate_report_id(),
            task_id=task_id,
            scenario_set_id=scenario_set_id,
            evaluation_type=evaluation_type,
            metrics=metrics,
            trajectory_scores=trajectory_scores,
            regression_comparison=regression_comparison,
        )

        # 完成任务
        task = task.model_copy(
            update={
                "state": EvaluationTaskState.COMPLETED,
                "progress": task.progress.model_copy(
                    update={"current_scenario_id": None}
                ),
                "report": report,
                "completed_at": datetime.now(timezone.utc),
            }
        )
        await self._repository.save_task(task)
        _logger.info(
            "评估任务完成: %s (类型 %s, 通过 %d/%d)",
            task_id,
            evaluation_type.value,
            completed,
            total,
        )
        return task

    async def freeze_baseline(
        self,
        task: EvaluationTask,
        description: str = "",
    ) -> Any:
        """将已完成基线评估任务的指标冻结为基线记录。

        前置条件：任务已完成且为基线评估。
        后置条件：返回 BaselineRecord。
        异常映射：EVAL_TASK_INTERRUPTED（任务未完成）。
        """
        if task.state != EvaluationTaskState.COMPLETED or task.report is None:
            raise DomainError(
                EVAL_TASK_INTERRUPTED,
                f"任务 {task.task_id} 未完成，无法冻结基线",
            )
        return await self._baseline_store.create_baseline(
            scenario_set_id=task.scenario_set_id,
            tenant_id=task.tenant_id,
            metrics=task.report.metrics,
            trajectory_scores=task.report.trajectory_scores,
            description=description or f"基线冻结自任务 {task.task_id}",
        )

    async def get_task(self, task_id: str) -> Optional[EvaluationTask]:
        """按标识查询评估任务。"""
        return await self._repository.get_task(task_id)

    async def list_tasks(
        self,
        tenant_id: str,
        scenario_set_id: Optional[str] = None,
    ) -> List[EvaluationTask]:
        """列出评估任务，可按场景集过滤。"""
        return await self._repository.list_tasks(tenant_id, scenario_set_id)

    @staticmethod
    def _aggregate_metrics(
        scenario_set: EvaluationScenarioSet,
        trajectories: List[AgentTrajectory],
        scores: List[TrajectoryScore],
    ) -> EvaluationMetrics:
        """聚合场景集指标。"""
        total = len(scenario_set.scenarios)
        if total == 0:
            return EvaluationMetrics()

        passed = sum(1 for t in trajectories if t.success)
        failed = total - passed
        success_rate = passed / total

        # 平均 CLEAR 分
        if scores:
            avg_cost = sum(s.clear_score.cost_efficiency for s in scores) / len(scores)
            avg_rel = sum(s.clear_score.reliability for s in scores) / len(scores)
            avg_lat = sum(s.clear_score.latency for s in scores) / len(scores)
            avg_pol = sum(s.clear_score.policy_compliance for s in scores) / len(scores)
            avg_overall = sum(s.clear_score.overall for s in scores) / len(scores)
        else:
            avg_cost = avg_rel = avg_lat = avg_pol = avg_overall = 0.0

        total_cost = sum(t.total_cost for t in trajectories)
        avg_latency = (
            sum(t.total_latency_ms for t in trajectories) / len(trajectories)
            if trajectories
            else 0.0
        )

        return EvaluationMetrics(
            total_scenarios=total,
            passed_scenarios=passed,
            failed_scenarios=failed,
            average_clear_score=ClearScore(
                cost_efficiency=avg_cost,
                reliability=avg_rel,
                latency=avg_lat,
                policy_compliance=avg_pol,
                overall=avg_overall,
                rationale=f"基于 {len(scores)} 条轨迹评分的平均",
            ),
            total_cost=total_cost,
            average_latency_ms=avg_latency,
            success_rate=success_rate,
        )
