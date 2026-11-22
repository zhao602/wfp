# =============================================================================
# 场景集服务（governance/evaluation/scenario_set_service.py）
#
# 职责：评估场景集的业务编排——创建、查询、删除、条目管理。
# 设计意图（对应偏差修正 T185）：
#   1. 校验场景集条目数上限（evaluation_max_scenarios）；
#   2. 场景标识与归属校验；
#   3. 委托 IEvaluationRepository 持久化。
# =============================================================================

import logging
import uuid
from typing import List, Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.errors import EVAL_SCENARIO_INVALID, DomainError
from orchestration_platform.governance.evaluation.abstractions.evaluation_repository import (
    IEvaluationRepository,
)
from orchestration_platform.governance.evaluation.models import (
    EvaluationScenario,
    EvaluationScenarioSet,
)

_logger = logging.getLogger(__name__)


class ScenarioSetService:
    """场景集服务。

    封装场景集的业务校验与持久化编排。
    """

    def __init__(self, repository: IEvaluationRepository) -> None:
        """构造服务实例。

        参数：
            repository —— 评估仓储实现。
        """
        self._repository = repository

    @staticmethod
    def generate_set_id() -> str:
        """生成场景集标识。"""
        return f"eval-set-{uuid.uuid4().hex[:12]}"

    @staticmethod
    def generate_scenario_id() -> str:
        """生成场景标识。"""
        return f"eval-scenario-{uuid.uuid4().hex[:12]}"

    async def create_scenario_set(
        self,
        name: str,
        tenant_id: str,
        description: str = "",
        scenarios: Optional[List[EvaluationScenario]] = None,
    ) -> EvaluationScenarioSet:
        """创建场景集。

        前置条件：条目数 <= evaluation_max_scenarios。
        后置条件：场景集持久化并返回。
        异常映射：EVAL_SCENARIO_INVALID（超限或非法）。
        """
        max_scenarios = get_settings().evaluation_max_scenarios
        scenario_list = scenarios or []
        if len(scenario_list) > max_scenarios:
            raise DomainError(
                EVAL_SCENARIO_INVALID,
                f"场景条目数 {len(scenario_list)} 超过上限 {max_scenarios}",
            )

        set_id = self.generate_set_id()
        # 为未带 scenario_set_id 的场景回填归属与标识
        normalized: List[EvaluationScenario] = []
        for sc in scenario_list:
            if not sc.scenario_id:
                sc = sc.model_copy(update={"scenario_id": self.generate_scenario_id()})
            if sc.scenario_set_id != set_id:
                sc = sc.model_copy(update={"scenario_set_id": set_id})
            normalized.append(sc)

        scenario_set = EvaluationScenarioSet(
            set_id=set_id,
            name=name,
            description=description,
            tenant_id=tenant_id,
            scenarios=normalized,
        )
        await self._repository.save_scenario_set(scenario_set)
        _logger.info("场景集已创建: %s (%s 条场景)", set_id, len(normalized))
        return scenario_set

    async def get_scenario_set(self, set_id: str) -> EvaluationScenarioSet:
        """按标识查询场景集。

        异常映射：EVAL_SCENARIO_INVALID（不存在）。
        """
        scenario_set = await self._repository.get_scenario_set(set_id)
        if scenario_set is None:
            raise DomainError(
                EVAL_SCENARIO_INVALID,
                f"场景集 {set_id} 不存在",
            )
        return scenario_set

    async def list_scenario_sets(self, tenant_id: str) -> List[EvaluationScenarioSet]:
        """列出租户全部场景集。"""
        return await self._repository.list_scenario_sets(tenant_id)

    async def delete_scenario_set(self, set_id: str, tenant_id: str) -> bool:
        """删除场景集。"""
        return await self._repository.delete_scenario_set(set_id, tenant_id)

    async def add_scenario(
        self,
        set_id: str,
        tenant_id: str,
        scenario: EvaluationScenario,
    ) -> EvaluationScenarioSet:
        """向已有场景集追加场景。

        前置条件：场景集存在且归属租户；追加后不超上限。
        后置条件：场景集更新并持久化。
        """
        scenario_set = await self.get_scenario_set(set_id)
        if scenario_set.tenant_id != tenant_id:
            raise DomainError(
                EVAL_SCENARIO_INVALID,
                f"场景集 {set_id} 不属于租户 {tenant_id}",
            )

        max_scenarios = get_settings().evaluation_max_scenarios
        if len(scenario_set.scenarios) + 1 > max_scenarios:
            raise DomainError(
                EVAL_SCENARIO_INVALID,
                f"追加后场景条目数超过上限 {max_scenarios}",
            )

        if not scenario.scenario_id:
            scenario = scenario.model_copy(
                update={"scenario_id": self.generate_scenario_id()}
            )
        scenario = scenario.model_copy(update={"scenario_set_id": set_id})

        updated = scenario_set.model_copy(
            update={
                "scenarios": [*scenario_set.scenarios, scenario],
            }
        )
        await self._repository.save_scenario_set(updated)
        return updated
