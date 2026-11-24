# =============================================================================
# 评估仓储本地实现（governance/evaluation/evaluation_repository_local.py）
#
# 职责：以 JSON 文件持久化评估数据，按 eval- 前缀与生产数据隔离。
# 设计意图（对应偏差修正 T184 / design 模块B EvaluationRepository）：
#   1. 目录结构：{root}/eval_scenario_sets/{set_id}.json
#                {root}/eval_tasks/{task_id}.json
#                {root}/eval_baselines/{baseline_id}.json
#   2. eval- 前缀保证评估数据与生产工作流数据物理隔离；
#   3. 场景条目数受 evaluation_max_scenarios 约束。
# =============================================================================

import json
import logging
from pathlib import Path
from typing import List, Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.errors import EVAL_SCENARIO_INVALID, DomainError
from orchestration_platform.governance.evaluation.models import (
    BaselineRecord,
    EvaluationScenarioSet,
    EvaluationTask,
)

_logger = logging.getLogger(__name__)


class LocalEvaluationRepository:
    """评估仓储——本地文件实现。

    评估数据存储于 eval_ 前缀子目录，与生产工作流定义/实例隔离。
    """

    def __init__(self, storage_root: Optional[str] = None) -> None:
        """构造仓储实例。

        参数：
            storage_root —— 存储根目录；缺省读取配置 local_storage_root。
        """
        root = storage_root or get_settings().local_storage_root
        base = Path(root)
        self._scenario_set_dir = base / "eval_scenario_sets"
        self._task_dir = base / "eval_tasks"
        self._baseline_dir = base / "eval_baselines"

    # ---------------- 内部工具 ----------------

    def _ensure_dirs(self) -> None:
        """确保全部子目录存在。"""
        self._scenario_set_dir.mkdir(parents=True, exist_ok=True)
        self._task_dir.mkdir(parents=True, exist_ok=True)
        self._baseline_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _read_json(file_path: Path) -> dict:
        """读取 JSON 文件。"""
        return json.loads(file_path.read_text(encoding="utf-8"))

    @staticmethod
    def _write_json(file_path: Path, data: dict) -> None:
        """写入 JSON 文件。"""
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _validate_scenario_set(self, scenario_set: EvaluationScenarioSet) -> None:
        """校验场景集条目数上限。"""
        max_scenarios = get_settings().evaluation_max_scenarios
        if len(scenario_set.scenarios) > max_scenarios:
            raise DomainError(
                EVAL_SCENARIO_INVALID,
                f"场景集 {scenario_set.set_id} 条目数 {len(scenario_set.scenarios)} 超过上限 {max_scenarios}",
            )

    # ---------------- 场景集 ----------------

    async def save_scenario_set(self, scenario_set: EvaluationScenarioSet) -> str:
        """保存场景集。"""
        self._ensure_dirs()
        self._validate_scenario_set(scenario_set)
        file_path = self._scenario_set_dir / f"{scenario_set.set_id}.json"
        self._write_json(file_path, scenario_set.model_dump(mode="json"))
        _logger.info("评估场景集已保存: %s", scenario_set.set_id)
        return scenario_set.set_id

    async def get_scenario_set(self, set_id: str) -> Optional[EvaluationScenarioSet]:
        """按标识查询场景集。"""
        file_path = self._scenario_set_dir / f"{set_id}.json"
        if not file_path.exists():
            return None
        return EvaluationScenarioSet.model_validate(self._read_json(file_path))

    async def list_scenario_sets(self, tenant_id: str) -> List[EvaluationScenarioSet]:
        """列出租户全部场景集。"""
        if not self._scenario_set_dir.exists():
            return []
        result: List[EvaluationScenarioSet] = []
        for file_path in self._scenario_set_dir.glob("*.json"):
            try:
                ss = EvaluationScenarioSet.model_validate(self._read_json(file_path))
                if ss.tenant_id == tenant_id:
                    result.append(ss)
            except Exception:  # noqa: BLE001
                _logger.warning("跳过无法解析的场景集文件: %s", file_path)
        return result

    async def delete_scenario_set(self, set_id: str, tenant_id: str) -> bool:
        """删除场景集。"""
        file_path = self._scenario_set_dir / f"{set_id}.json"
        if not file_path.exists():
            return False
        try:
            ss = EvaluationScenarioSet.model_validate(self._read_json(file_path))
            if ss.tenant_id != tenant_id:
                return False
        except Exception:  # noqa: BLE001
            return False
        file_path.unlink()
        _logger.info("评估场景集已删除: %s", set_id)
        return True

    # ---------------- 评估任务 ----------------

    async def save_task(self, task: EvaluationTask) -> str:
        """保存评估任务。"""
        self._ensure_dirs()
        file_path = self._task_dir / f"{task.task_id}.json"
        self._write_json(file_path, task.model_dump(mode="json"))
        return task.task_id

    async def get_task(self, task_id: str) -> Optional[EvaluationTask]:
        """按标识查询评估任务。"""
        file_path = self._task_dir / f"{task_id}.json"
        if not file_path.exists():
            return None
        return EvaluationTask.model_validate(self._read_json(file_path))

    async def list_tasks(
        self,
        tenant_id: str,
        scenario_set_id: Optional[str] = None,
    ) -> List[EvaluationTask]:
        """列出评估任务，可按场景集过滤。"""
        if not self._task_dir.exists():
            return []
        result: List[EvaluationTask] = []
        for file_path in self._task_dir.glob("*.json"):
            try:
                task = EvaluationTask.model_validate(self._read_json(file_path))
                if task.tenant_id != tenant_id:
                    continue
                if scenario_set_id is not None and task.scenario_set_id != scenario_set_id:
                    continue
                result.append(task)
            except Exception:  # noqa: BLE001
                _logger.warning("跳过无法解析的评估任务文件: %s", file_path)
        return result

    # ---------------- 基线 ----------------

    async def save_baseline(self, baseline: BaselineRecord) -> str:
        """保存基线记录。"""
        self._ensure_dirs()
        file_path = self._baseline_dir / f"{baseline.baseline_id}.json"
        self._write_json(file_path, baseline.model_dump(mode="json"))
        _logger.info("评估基线已保存: %s", baseline.baseline_id)
        return baseline.baseline_id

    async def get_baseline(self, baseline_id: str) -> Optional[BaselineRecord]:
        """按标识查询基线记录。"""
        file_path = self._baseline_dir / f"{baseline_id}.json"
        if not file_path.exists():
            return None
        return BaselineRecord.model_validate(self._read_json(file_path))

    async def list_baselines(
        self,
        tenant_id: str,
        scenario_set_id: Optional[str] = None,
    ) -> List[BaselineRecord]:
        """列出基线记录，可按场景集过滤。"""
        if not self._baseline_dir.exists():
            return []
        result: List[BaselineRecord] = []
        for file_path in self._baseline_dir.glob("*.json"):
            try:
                baseline = BaselineRecord.model_validate(self._read_json(file_path))
                if baseline.tenant_id != tenant_id:
                    continue
                if scenario_set_id is not None and baseline.scenario_set_id != scenario_set_id:
                    continue
                result.append(baseline)
            except Exception:  # noqa: BLE001
                _logger.warning("跳过无法解析的基线文件: %s", file_path)
        return result
