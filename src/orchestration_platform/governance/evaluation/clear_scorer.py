# =============================================================================
# CLEAR 四维评分器（governance/evaluation/clear_scorer.py）
#
# 职责：依据 Agent 轨迹与场景约束，计算 CLEAR 四维评分。
# 设计意图（对应偏差修正 T187）：
#   CLEAR 四维：
#     C —— Cost-efficiency（成本效率）：成本越低越高，按参考成本归一化；
#     R —— Reliability（可靠性）：成功率与关键词命中综合；
#     L —— Latency（延迟）：延迟越低越高，按参考延迟归一化；
#     P —— Policy-compliance（策略合规）：违规越少越高（轨迹无违规即满分）。
#   各维度归一化至 [0, 1]，overall 为等权加权平均。
#   评分确定性：不依赖随机源，相同输入产出相同评分。
# =============================================================================

import logging
from datetime import datetime, timezone

from orchestration_platform.governance.evaluation.abstractions.clear_scorer import (
    IClearScorer,
)
from orchestration_platform.governance.evaluation.models import (
    AgentTrajectory,
    ClearScore,
    EvaluationScenario,
)

_logger = logging.getLogger(__name__)

# 参考常量：用于归一化。成本与延迟超过参考值时按倒数衰减。
# 这些常量作为评分基准，可按场景 metadata 覆盖。
_DEFAULT_REFERENCE_COST = 0.10       # 参考成本（货币单位）
_DEFAULT_REFERENCE_LATENCY_MS = 5000  # 参考延迟（毫秒）

# overall 加权权重（四维等权）
_WEIGHT_COST = 0.25
_WEIGHT_RELIABILITY = 0.25
_WEIGHT_LATENCY = 0.25
_WEIGHT_POLICY = 0.25


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    """将值裁剪至 [low, high] 区间。"""
    return max(low, min(high, value))


class ClearScorer:
    """CLEAR 四维评分器（规则实现，确定性）。

    评分完全由轨迹与场景约束决定，无随机性、无外部依赖。
    """

    async def score_clear(
        self,
        trajectory: AgentTrajectory,
        scenario: EvaluationScenario,
    ) -> ClearScore:
        """计算 CLEAR 四维评分。"""
        metadata = scenario.metadata or {}
        ref_cost = float(metadata.get("reference_cost", _DEFAULT_REFERENCE_COST))
        ref_latency = float(
            metadata.get("reference_latency_ms", _DEFAULT_REFERENCE_LATENCY_MS)
        )

        # ---- C: 成本效率 ----
        # 成本越低越高；成本为 0 时满分；超过参考值按参考/实际衰减。
        if trajectory.total_cost <= 0.0:
            cost_efficiency = 1.0
        else:
            cost_efficiency = _clamp(ref_cost / max(trajectory.total_cost, 1e-9))

        # ---- R: 可靠性 ----
        # 综合 success 与关键词命中率。
        reliability_components = []
        if trajectory.success:
            reliability_components.append(1.0)
        else:
            reliability_components.append(0.0)

        if scenario.expected_keywords:
            output_lower = trajectory.final_output.lower()
            hit_count = sum(
                1 for kw in scenario.expected_keywords if kw.lower() in output_lower
            )
            keyword_rate = hit_count / len(scenario.expected_keywords)
            reliability_components.append(keyword_rate)
        elif scenario.expected_output:
            # 有期望输出但无关键词：精确包含判定
            if scenario.expected_output.strip() in trajectory.final_output:
                reliability_components.append(1.0)
            else:
                reliability_components.append(0.0)
        else:
            # 无期望约束：仅以 success 计
            reliability_components.append(1.0 if trajectory.success else 0.0)

        reliability = _clamp(sum(reliability_components) / len(reliability_components))

        # ---- L: 延迟 ----
        # 延迟越低越高；延迟为 0 时满分；超过参考值按参考/实际衰减。
        if trajectory.total_latency_ms <= 0:
            latency_score = 1.0
        else:
            latency_score = _clamp(
                ref_latency / max(trajectory.total_latency_ms, 1)
            )

        # ---- P: 策略合规 ----
        # 轨迹无违规记录即满分；steps 中含 policy_violation 标记则扣分。
        violation_count = 0
        for step in trajectory.steps:
            if isinstance(step, dict) and step.get("policy_violation"):
                violation_count += 1
        if violation_count == 0:
            policy_compliance = 1.0
        else:
            policy_compliance = _clamp(1.0 / (1.0 + violation_count))

        # ---- overall: 等权加权 ----
        overall = (
            _WEIGHT_COST * cost_efficiency
            + _WEIGHT_RELIABILITY * reliability
            + _WEIGHT_LATENCY * latency_score
            + _WEIGHT_POLICY * policy_compliance
        )

        rationale = (
            f"成本效率={cost_efficiency:.3f}(成本{trajectory.total_cost}/参考{ref_cost}), "
            f"可靠性={reliability:.3f}(success={trajectory.success}), "
            f"延迟={latency_score:.3f}(延迟{trajectory.total_latency_ms}ms/参考{ref_latency}ms), "
            f"策略合规={policy_compliance:.3f}(违规{violation_count}次)"
        )

        return ClearScore(
            cost_efficiency=cost_efficiency,
            reliability=reliability,
            latency=latency_score,
            policy_compliance=policy_compliance,
            overall=_clamp(overall),
            rationale=rationale,
        )
