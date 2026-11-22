# =============================================================================
# 回归报告器（governance/evaluation/regression_reporter.py）
#
# 职责：对比当前评估指标与基线，产出回归对比与退化判定。
# 设计意图（对应偏差修正 T189）：
#   1. CLEAR 各维度差异 = 当前 - 基线（正为提升，负为退化）；
#   2. 任一维度退化幅度超过 evaluation_regression_degrade_threshold 即判定退化；
#   3. 产出退化明细清单，供告警与展示。
# =============================================================================

import logging
import uuid
from datetime import datetime, timezone
from typing import List

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.governance.evaluation.models import (
    BaselineRecord,
    ClearScore,
    EvaluationMetrics,
    RegressionComparison,
)

_logger = logging.getLogger(__name__)

# CLEAR 四维维度名（用于退化明细）
_CLEAR_DIMENSIONS = (
    ("cost_efficiency", "成本效率"),
    ("reliability", "可靠性"),
    ("latency", "延迟"),
    ("policy_compliance", "策略合规"),
    ("overall", "综合"),
)


class RegressionReporter:
    """回归报告器。

    对比当前与基线指标，判定退化并产出 RegressionComparison。
    """

    @staticmethod
    def _compute_delta(current: ClearScore, baseline: ClearScore) -> ClearScore:
        """计算 CLEAR 各维度差异（当前减基线）。"""
        return ClearScore(
            cost_efficiency=current.cost_efficiency - baseline.cost_efficiency,
            reliability=current.reliability - baseline.reliability,
            latency=current.latency - baseline.latency,
            policy_compliance=current.policy_compliance - baseline.policy_compliance,
            overall=current.overall - baseline.overall,
            rationale="各维度差异 = 当前 - 基线（正为提升，负为退化）",
        )

    async def compare(
        self,
        baseline: BaselineRecord,
        current_metrics: EvaluationMetrics,
    ) -> RegressionComparison:
        """对比当前指标与基线，产出回归对比。

        前置条件：基线存在。
        后置条件：返回 RegressionComparison，degraded 反映是否退化。
        """
        threshold = get_settings().evaluation_regression_degrade_threshold
        delta = self._compute_delta(
            current_metrics.average_clear_score,
            baseline.metrics.average_clear_score,
        )

        degrade_details: List[str] = []
        for attr, label in _CLEAR_DIMENSIONS:
            value = getattr(delta, attr)
            if value < -threshold:
                degrade_details.append(
                    f"{label}退化 {value:.3f}（超过阈值 -{threshold:.3f}）"
                )

        degraded = len(degrade_details) > 0
        comparison = RegressionComparison(
            comparison_id=f"eval-comparison-{uuid.uuid4().hex[:12]}",
            baseline_id=baseline.baseline_id,
            current_metrics=current_metrics,
            baseline_metrics=baseline.metrics,
            clear_delta=delta,
            degraded=degraded,
            degrade_details=degrade_details,
            compared_at=datetime.now(timezone.utc),
        )
        if degraded:
            _logger.warning(
                "回归退化告警: 基线 %s, 退化维度 %s",
                baseline.baseline_id,
                degrade_details,
            )
        else:
            _logger.info("回归对比完成: 基线 %s, 无退化", baseline.baseline_id)
        return comparison
