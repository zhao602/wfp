# =============================================================================
# 评估仓储抽象接口（governance/evaluation/abstractions/evaluation_repository.py）
#
# 职责：定义评估数据存储的统一契约。
# 设计意图（对应偏差修正 T184 / design 模块B EvaluationRepository）：
#   - 场景集 CRUD（save_scenario_set / get_scenario_set / list_scenario_sets / delete_scenario_set）
#   - 评估任务 CRUD（save_task / get_task / list_tasks）
#   - 基线管理（save_baseline / get_baseline / list_baselines）
#   - 评估数据与生产隔离：eval- 前缀，独立存储路径。
# =============================================================================

from typing import List, Optional, Protocol, runtime_checkable

from orchestration_platform.governance.evaluation.models import (
    BaselineRecord,
    EvaluationScenarioSet,
    EvaluationTask,
)


@runtime_checkable
class IEvaluationRepository(Protocol):
    """评估仓储抽象接口。

    所有方法均为协程，实现可选用本地文件或数据库后端。
    评估数据与生产数据物理隔离（eval- 前缀目录/表）。
    """

    # ---------------- 场景集 ----------------

    async def save_scenario_set(self, scenario_set: EvaluationScenarioSet) -> str:
        """保存场景集。

        前置条件：场景条目数 <= evaluation_max_scenarios。
        后置条件：场景集持久化，返回 set_id。
        异常映射：EVAL_SCENARIO_INVALID（场景非法）。
        """
        ...

    async def get_scenario_set(self, set_id: str) -> Optional[EvaluationScenarioSet]:
        """按标识查询场景集。"""
        ...

    async def list_scenario_sets(self, tenant_id: str) -> List[EvaluationScenarioSet]:
        """列出租户全部场景集。"""
        ...

    async def delete_scenario_set(self, set_id: str, tenant_id: str) -> bool:
        """删除场景集。

        后置条件：返回是否成功。
        """
        ...

    # ---------------- 评估任务 ----------------

    async def save_task(self, task: EvaluationTask) -> str:
        """保存评估任务。

        后置条件：任务持久化，返回 task_id。
        """
        ...

    async def get_task(self, task_id: str) -> Optional[EvaluationTask]:
        """按标识查询评估任务。"""
        ...

    async def list_tasks(
        self,
        tenant_id: str,
        scenario_set_id: Optional[str] = None,
    ) -> List[EvaluationTask]:
        """列出评估任务，可按场景集过滤。"""
        ...

    # ---------------- 基线 ----------------

    async def save_baseline(self, baseline: BaselineRecord) -> str:
        """保存基线记录。

        后置条件：基线持久化，返回 baseline_id。
        """
        ...

    async def get_baseline(self, baseline_id: str) -> Optional[BaselineRecord]:
        """按标识查询基线记录。"""
        ...

    async def list_baselines(
        self,
        tenant_id: str,
        scenario_set_id: Optional[str] = None,
    ) -> List[BaselineRecord]:
        """列出基线记录，可按场景集过滤。"""
        ...
