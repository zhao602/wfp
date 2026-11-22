# =============================================================================
# CLEAR 评分器抽象接口（governance/evaluation/abstractions/clear_scorer.py）
#
# 职责：定义 CLEAR 四维评分的统一契约。
# 设计意图（对应偏差修正 T187）：
#   - score_clear：依据轨迹与场景计算 CLEAR 四维评分；
#   - 四维：Cost-efficiency / Reliability / Latency / Policy-compliance；
#   - 各维度归一化至 [0, 1]，overall 为加权综合。
# =============================================================================

from typing import Protocol, runtime_checkable

from orchestration_platform.governance.evaluation.models import (
    AgentTrajectory,
    ClearScore,
    EvaluationScenario,
)


@runtime_checkable
class IClearScorer(Protocol):
    """CLEAR 四维评分器抽象接口。"""

    async def score_clear(
        self,
        trajectory: AgentTrajectory,
        scenario: EvaluationScenario,
    ) -> ClearScore:
        """计算 CLEAR 四维评分。

        后置条件：返回 ClearScore，四维与 overall 均在 [0, 1]。
        """
        ...
