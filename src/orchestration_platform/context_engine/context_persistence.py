# TODO(tech-debt): B档候选孤儿（族群 B9），待 APM 链路追踪专项排查确认无调用后清理，见 docs/tech-debt-ledger.md
# =============================================================================
# 上下文持久化与传递（context_engine/context_persistence.py）
#
# 职责：上下文检查点的保存、恢复与跨节点传递。
# 设计意图（对应 spec 5.4.3、design 2.7.3、任务 T37）：
#   1. 上下文检查点持久化至本地文件或内存；
#   2. 恢复时还原完整上下文结构；
#   3. 跨节点传递时支持继承与隔离两种模式。
# =============================================================================

import json
from pathlib import Path
from typing import Optional

from orchestration_platform.context_engine.models import (
    AgentContext,
    ContextCheckpoint,
)


class ContextPersistenceService:
    """上下文持久化服务：保存与恢复上下文检查点。

    构造注入：
        storage_root —— 存储根目录（本地文件模式）
    """

    def __init__(self, storage_root: str = "./.data/contexts") -> None:
        self._storage_root = Path(storage_root)
        self._storage_root.mkdir(parents=True, exist_ok=True)

    def save(self, checkpoint: ContextCheckpoint) -> str:
        """保存上下文检查点至本地文件。

        Returns:
            检查点文件路径。
        """
        file_path = self._storage_root / f"{checkpoint.instance_id}_{checkpoint.node_id}_{checkpoint.sequence}.json"
        data = checkpoint.model_dump(mode="json")
        file_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(file_path)

    def load(self, instance_id: str, node_id: str, sequence: int) -> Optional[ContextCheckpoint]:
        """从本地文件加载上下文检查点。"""
        file_path = self._storage_root / f"{instance_id}_{node_id}_{sequence}.json"
        if not file_path.exists():
            return None
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
            return ContextCheckpoint.model_validate(data)
        except Exception:
            return None

    def load_latest(self, instance_id: str, node_id: str) -> Optional[ContextCheckpoint]:
        """加载指定实例与节点的最新上下文检查点。"""
        pattern = f"{instance_id}_{node_id}_*.json"
        files = sorted(self._storage_root.glob(pattern))
        if not files:
            return None
        try:
            data = json.loads(files[-1].read_text(encoding="utf-8"))
            return ContextCheckpoint.model_validate(data)
        except Exception:
            return None


class ContextTransferService:
    """上下文传递服务：管理跨节点的上下文传递。

    支持两种模式：
    - INHERIT：子节点共享父节点上下文的只读视图
    - ISOLATE：深拷贝父上下文作为初始值，修改不影响父上下文
    """

    @staticmethod
    def transfer_inherit(parent_context: AgentContext) -> AgentContext:
        """继承传递：返回父上下文的只读引用。

        子节点可读不可改（调用方应保证不修改返回的上下文）。
        """
        return parent_context

    @staticmethod
    def transfer_isolate(parent_context: AgentContext) -> AgentContext:
        """隔离传递：深拷贝父上下文作为子节点初始值。"""
        return parent_context.model_copy(deep=True)
