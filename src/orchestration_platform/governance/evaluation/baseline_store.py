# =============================================================================
# 基线存储（governance/evaluation/baseline_store.py）
#
# 职责：基线记录的创建、查询与冻结管理。
# 设计意图（对应偏差修正 T188）：
#   1. 基线一经创建即不可变（冻结），作为回归对比基准；
#   2. 基线标识自动生成，关联场景集与租户；
#   3. 委托 IEvaluationRepository 持久化。
# =============================================================================

import logging
import uuid
from typing import List, Optional

from orchestration_platform.governance.evaluation.abstractions.evaluation_repository import (
    IEvaluationRepository,
)
from orchestration_platform.governance.evaluation.models import (
    BaselineRecord,
    EvaluationMetrics,
    TrajectoryScore,
)

_logger = logging.getLogger(__name__)


class BaselineStore:
    """基线存储。

    管理基线记录的创建与查询。基线一经创建不可变。
    """

    def __init__(self, repository: IEvaluationRepository) -> None:
        """构造基线存储实例。

        参数：
            repository —— 评估仓储实现。
        """
        self._repository = repository

    @staticmethod
    def generate_baseline_id() -> str:
        """生成基线标识。"""
        return f"eval-baseline-{uuid.uuid4().hex[:12]}"

    async def create_baseline(
        self,
        scenario_set_id: str,
        tenant_id: str,
        metrics: EvaluationMetrics,
        trajectory_scores: List[TrajectoryScore],
        description: str = "",
    ) -> BaselineRecord:
        """创建并冻结基线记录。

        前置条件：场景集存在（由调用方保证）。
        后置条件：基线持久化，不可变。
        """
        baseline_id = self.generate_baseline_id()
        baseline = BaselineRecord(
            baseline_id=baseline_id,
            scenario_set_id=scenario_set_id,
            tenant_id=tenant_id,
            description=description,
            metrics=metrics,
            trajectory_scores=list(trajectory_scores),
        )
        await self._repository.save_baseline(baseline)
        _logger.info(
            "基线已冻结: %s (场景集 %s, 平均分 %.3f)",
            baseline_id,
            scenario_set_id,
            metrics.average_clear_score.overall,
        )
        return baseline

    async def get_baseline(self, baseline_id: str) -> Optional[BaselineRecord]:
        """按标识查询基线记录。"""
        return await self._repository.get_baseline(baseline_id)

    async def list_baselines(
        self,
        tenant_id: str,
        scenario_set_id: Optional[str] = None,
    ) -> List[BaselineRecord]:
        """列出基线记录，可按场景集过滤。"""
        return await self._repository.list_baselines(tenant_id, scenario_set_id)
