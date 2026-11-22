# =============================================================================
# 本地文件用户开发提交仓储（persistence/local_file/local_file_dev_submission_repository.py）
#
# 职责（agent_market_user_development T10.2 / CHG-09 / DC-03）：
#   1. 存储布局 {agent_market_dev_storage_root}/submissions/{submission_id}/：
#      submission.json（主体，同标识幂等覆盖）+ reports.jsonl /
#      state_history.jsonl（仅追加、不覆盖，行数单调递增，AC-DEV-16）；
#   2. 构造即 makedirs；list_all 内损坏 JSON 跳过不抛错
#      （复用 LocalFileAgentMarketRepository 范式）。
# 约束：Python 3.10 兼容；零第三方新增依赖。
# =============================================================================

import logging
from pathlib import Path
from typing import Dict, List, Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.models.enums import ContributionState
from orchestration_platform.market.dev_models import DevSubmission

_logger = logging.getLogger(__name__)

_MAIN_FILE = "submission.json"
_REPORTS_FILE = "reports.jsonl"
_HISTORY_FILE = "state_history.jsonl"


def _default_storage_root() -> str:
    """解析缺省存储根目录（settings.agent_market_dev_storage_root，T4.1 字段）。"""
    settings = get_settings()
    return getattr(
        settings, "agent_market_dev_storage_root", "./.data/agent_market_dev"
    )


class LocalFileDevSubmissionRepository:
    """用户开发提交仓储——本地文件实现（IDevSubmissionRepository，DC-03）。"""

    def __init__(self, storage_root: Optional[str] = None) -> None:
        """构造仓储实例；构造即确保目录存在。"""
        root = storage_root or _default_storage_root()
        self._base_dir = Path(root) / "submissions"
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._cache: Dict[str, DevSubmission] = {}

    def _submission_dir(self, submission_id: str) -> Path:
        """生成提交存储目录（submission_id 转义为目录名）。"""
        safe_id = submission_id.replace("/", "_").replace("\\", "_")
        return self._base_dir / safe_id

    @staticmethod
    def _line_count(path: Path) -> int:
        """统计 JSONL 留档文件既有行数（不存在视为 0）。"""
        if not path.exists():
            return 0
        with open(path, "r", encoding="utf-8") as f:
            return sum(1 for _ in f)

    async def save(self, submission: DevSubmission) -> None:
        """保存提交（主体幂等覆盖；报告/历史按既有行数增量追加）。"""
        submission_dir = self._submission_dir(submission.submission_id)
        submission_dir.mkdir(parents=True, exist_ok=True)

        (submission_dir / _MAIN_FILE).write_text(
            submission.model_dump_json(indent=2), encoding="utf-8"
        )

        reports_path = submission_dir / _REPORTS_FILE
        existing_reports = self._line_count(reports_path)
        if len(submission.validation_reports) > existing_reports:
            with open(reports_path, "a", encoding="utf-8") as f:
                for report in submission.validation_reports[existing_reports:]:
                    f.write(report.model_dump_json() + "\n")

        history_path = submission_dir / _HISTORY_FILE
        existing_history = self._line_count(history_path)
        if len(submission.state_history) > existing_history:
            with open(history_path, "a", encoding="utf-8") as f:
                for record in submission.state_history[existing_history:]:
                    f.write(record.model_dump_json() + "\n")

        self._cache[submission.submission_id] = submission

    async def get(self, submission_id: str) -> Optional[DevSubmission]:
        """按标识读取提交；不存在或损坏返回 None。"""
        cached = self._cache.get(submission_id)
        if cached is not None:
            return cached
        main_path = self._submission_dir(submission_id) / _MAIN_FILE
        if not main_path.exists():
            return None
        try:
            submission = DevSubmission.model_validate_json(
                main_path.read_text(encoding="utf-8")
            )
        except Exception as exc:
            _logger.error("加载用户开发提交失败: %s", exc)
            return None
        self._cache[submission_id] = submission
        return submission

    async def list_all(self) -> List[DevSubmission]:
        """列出全部提交（按创建时间有序）；损坏 JSON 跳过不抛错。"""
        submissions: List[DevSubmission] = []
        for submission_dir in sorted(self._base_dir.iterdir()):
            if not submission_dir.is_dir():
                continue
            submission = await self.get(submission_dir.name)
            if submission is not None:
                submissions.append(submission)
        submissions.sort(key=lambda s: (s.created_at, s.submission_id))
        return submissions

    async def list_by_state(
        self, state: ContributionState
    ) -> List[DevSubmission]:
        """按状态列出提交。"""
        return [s for s in await self.list_all() if s.state == state]

    async def exists_by_node_version(
        self, developer_id: str, node_name: str, version: str
    ) -> bool:
        """按开发者 + 节点名 + 版本判重。"""
        for submission in await self.list_all():
            if (
                submission.developer_id == developer_id
                and submission.node_name == node_name
                and submission.version == version
            ):
                return True
        return False
