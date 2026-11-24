# =============================================================================
# 贡献本地仓储（mcp_tool_market/contribution/contribution_repository_local.py）
#
# 职责：文件存储布局实现贡献仓储。
# 设计意图（对应偏差修正 T154 / design 2.3.2）：
#   - 存储布局：./.data/contributions/{contribution_id}/
#   - 扫描报告与流转历史留档不可篡改（追加写模式）
#   - PostgreSQL 后端留扩展点（驱动延迟导入，本阶段 local 默认）
# =============================================================================

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.models.enums import ContributionState
from orchestration_platform.mcp_tool_market.contribution.models import (
    NodeContribution,
)

_logger = logging.getLogger(__name__)


class LocalContributionRepository:
    """本地文件贡献仓储。

    存储布局：{storage_root}/{contribution_id}/contribution.json
    扫描报告与流转历史追加写（不可篡改留档）。
    """

    def __init__(self, storage_root: Optional[str] = None) -> None:
        """构造本地仓储。

        参数：
            storage_root —— 存储根目录（缺省从配置读取）
        """
        if storage_root is None:
            settings = get_settings()
            storage_root = settings.node_contribution_storage_root
        self._root = Path(storage_root)
        self._root.mkdir(parents=True, exist_ok=True)
        # 内存索引：contribution_id -> NodeContribution
        self._cache: Dict[str, NodeContribution] = {}

    def save(self, contribution: NodeContribution) -> None:
        """保存贡献。

        扫描报告与流转历史以追加写方式保存（不可篡改留档）。
        """
        contrib_dir = self._root / contribution.contribution_id
        contrib_dir.mkdir(parents=True, exist_ok=True)

        # 保存贡献主体
        data = contribution.model_dump(mode="json")
        with open(contrib_dir / "contribution.json", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        # 扫描报告追加写（不可篡改留档）
        if contribution.scan_reports:
            reports_path = contrib_dir / "scan_reports.jsonl"
            existing_count = 0
            if reports_path.exists():
                with open(reports_path, "r", encoding="utf-8") as f:
                    existing_count = sum(1 for _ in f)
            if len(contribution.scan_reports) > existing_count:
                with open(reports_path, "a", encoding="utf-8") as f:
                    for report in contribution.scan_reports[existing_count:]:
                        f.write(report.model_dump_json() + "\n")

        # 流转历史追加写（不可篡改留档）
        if contribution.state_history:
            history_path = contrib_dir / "state_history.jsonl"
            existing_count = 0
            if history_path.exists():
                with open(history_path, "r", encoding="utf-8") as f:
                    existing_count = sum(1 for _ in f)
            if len(contribution.state_history) > existing_count:
                with open(history_path, "a", encoding="utf-8") as f:
                    for record in contribution.state_history[existing_count:]:
                        f.write(record.model_dump_json() + "\n")

        self._cache[contribution.contribution_id] = contribution

    def get(self, contribution_id: str) -> Optional[NodeContribution]:
        """按标识获取贡献。"""
        if contribution_id in self._cache:
            return self._cache[contribution_id]

        contrib_path = self._root / contribution_id / "contribution.json"
        if not contrib_path.exists():
            return None

        try:
            with open(contrib_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            contribution = NodeContribution(**data)
            self._cache[contribution_id] = contribution
            return contribution
        except Exception as exc:
            _logger.error("加载贡献失败: %s", exc)
            return None

    def list_by_state(self, state: ContributionState) -> List[NodeContribution]:
        """按状态列出贡献。"""
        results: List[NodeContribution] = []
        for contrib_dir in self._root.iterdir():
            if not contrib_dir.is_dir():
                continue
            contribution = self.get(contrib_dir.name)
            if contribution is not None and contribution.state == state:
                results.append(contribution)
        return results

    def list_by_node(self, node_name: str) -> List[NodeContribution]:
        """按节点名称列出贡献。"""
        results: List[NodeContribution] = []
        for contrib_dir in self._root.iterdir():
            if not contrib_dir.is_dir():
                continue
            contribution = self.get(contrib_dir.name)
            if contribution is not None and contribution.node_name == node_name:
                results.append(contribution)
        return results

    def exists_by_node_version(self, node_name: str, version: str) -> bool:
        """按名称+版本判重。"""
        for contrib_dir in self._root.iterdir():
            if not contrib_dir.is_dir():
                continue
            contribution = self.get(contrib_dir.name)
            if (
                contribution is not None
                and contribution.node_name == node_name
                and contribution.version == version
            ):
                return True
        return False
