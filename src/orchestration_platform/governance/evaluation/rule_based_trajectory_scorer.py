# =============================================================================
# 规则轨迹评分器（governance/evaluation/rule_based_trajectory_scorer.py）
#
# 职责：默认轨迹评分器，基于确定性规则产出 TrajectoryScore。
# 设计意图（对应偏差修正 T186）：
#   1. 默认评分器，不依赖 LLM，结果可复现；
#   2. 委托 ClearScorer 计算 CLEAR 四维；
#   3. 评分器类型标记为 rule_based。
# =============================================================================

import logging
from datetime import datetime, timezone
from typing import Optional

from orchestration_platform.governance.evaluation.clear_scorer import ClearScorer
from orchestration_platform.governance.evaluation.models import (
    AgentTrajectory,
    EvaluationScenario,
    TrajectoryScore,
)

_logger = logging.getLogger(__name__)


class RuleBasedTrajectoryScorer:
    """规则轨迹评分器（默认，确定性）。

    委托 ClearScorer 计算 CLEAR 四维，scorer_type=rule_based。
    """

    def __init__(self, clear_scorer: Optional[ClearScorer] = None) -> None:
        """构造评分器。

        参数：
            clear_scorer —— CLEAR 评分器实例；缺省新建。
        """
        self._clear_scorer = clear_scorer or ClearScorer()

    async def score(
        self,
        trajectory: AgentTrajectory,
        scenario: EvaluationScenario,
    ) -> TrajectoryScore:
        """对单条轨迹评分（规则，确定性）。"""
        clear_score = await self._clear_scorer.score_clear(trajectory, scenario)
        return TrajectoryScore(
            trajectory_id=trajectory.trajectory_id,
            scenario_id=scenario.scenario_id,
            clear_score=clear_score,
            scorer_type="rule_based",
            scored_at=datetime.now(timezone.utc),
        )
