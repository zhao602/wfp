# =============================================================================
# 轨迹评分器抽象接口（governance/evaluation/abstractions/trajectory_scorer.py）
#
# 职责：定义轨迹评分的统一契约。
# 设计意图（对应偏差修正 T186）：
#   - score：对单条 Agent 执行轨迹评分，产出 TrajectoryScore（含 CLEAR 四维）；
#   - 规则评分（默认，确定性）与 LLM 评分（可选）实现同一契约；
#   - LLM 评分失败时由调用方回退至规则评分。
# =============================================================================

from typing import Protocol, runtime_checkable

from orchestration_platform.governance.evaluation.models import (
    AgentTrajectory,
    EvaluationScenario,
    TrajectoryScore,
)


@runtime_checkable
class ITrajectoryScorer(Protocol):
    """轨迹评分器抽象接口。"""

    async def score(
        self,
        trajectory: AgentTrajectory,
        scenario: EvaluationScenario,
    ) -> TrajectoryScore:
        """对单条轨迹评分。

        前置条件：轨迹与场景格式合法。
        后置条件：返回 TrajectoryScore，clear_score 各维度在 [0, 1]。
        """
        ...
