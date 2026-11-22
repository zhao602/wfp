# =============================================================================
# 仓储工厂（persistence/repository_factory.py）
#
# 职责：依据 STORAGE_BACKEND 配置项选择本地文件或 PostgreSQL 仓储实现。
# 设计意图（对应需求规格 4.6、design 2.2.2.3）：
#   1. 存储层不写死单一实现——业务层通过工厂获取仓储实例，切换后端仅需改配置；
#   2. PostgreSQL 实现延迟导入——验证期无 psycopg 依赖时不触发 ImportError。
# =============================================================================

from typing import Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.persistence.abstractions.repositories import (
    ICheckpointRepository,
    IEventRepository,
    IWorkflowRepository,
)
from orchestration_platform.persistence.abstractions.dev_submission_repositories import (
    IDevSubmissionRepository,
)
from orchestration_platform.persistence.abstractions.market_repositories import (
    IAgentMarketRepository,
    IToolMarketRepository,
)


class RepositoryFactory:
    """仓储工厂：按配置创建三类仓储实例。"""

    @staticmethod
    def create_workflow_repository(
        storage_backend: Optional[str] = None,
    ) -> IWorkflowRepository:
        """创建工作流定义仓储实例。"""
        backend = storage_backend or get_settings().storage_backend
        if backend == "local_file":
            from orchestration_platform.persistence.local_file.local_file_workflow_repository import (
                LocalFileWorkflowRepository,
            )
            return LocalFileWorkflowRepository()
        if backend == "postgres":
            from orchestration_platform.persistence.postgres.postgres_workflow_repository import (
                PostgresWorkflowRepository,
            )
            return PostgresWorkflowRepository()
        raise ValueError(f"不支持的存储后端：{backend}")

    @staticmethod
    def create_checkpoint_repository(
        storage_backend: Optional[str] = None,
    ) -> ICheckpointRepository:
        """创建检查点仓储实例。"""
        backend = storage_backend or get_settings().storage_backend
        if backend == "local_file":
            from orchestration_platform.persistence.local_file.local_file_checkpoint_repository import (
                LocalFileCheckpointRepository,
            )
            return LocalFileCheckpointRepository()
        if backend == "postgres":
            from orchestration_platform.persistence.postgres.postgres_checkpoint_repository import (
                PostgresCheckpointRepository,
            )
            return PostgresCheckpointRepository()
        raise ValueError(f"不支持的存储后端：{backend}")

    @staticmethod
    def create_event_repository(
        storage_backend: Optional[str] = None,
    ) -> IEventRepository:
        """创建执行事件仓储实例。"""
        backend = storage_backend or get_settings().storage_backend
        if backend == "local_file":
            from orchestration_platform.persistence.local_file.local_file_event_repository import (
                LocalFileEventRepository,
            )
            return LocalFileEventRepository()
        if backend == "postgres":
            from orchestration_platform.persistence.postgres.postgres_event_repository import (
                PostgresEventRepository,
            )
            return PostgresEventRepository()
        raise ValueError(f"不支持的存储后端：{backend}")

    @staticmethod
    def create_agent_market_repository(
        storage_backend: Optional[str] = None,
    ) -> IAgentMarketRepository:
        """创建 Agent 市场条目仓储实例（OP-9：仅追加，对应 CHG-08）。"""
        backend = storage_backend or get_settings().storage_backend
        if backend == "local_file":
            from orchestration_platform.persistence.local_file.local_file_agent_market_repository import (
                LocalFileAgentMarketRepository,
            )
            return LocalFileAgentMarketRepository()
        if backend == "postgres":
            from orchestration_platform.persistence.postgres.postgres_agent_market_repository import (
                PostgresAgentMarketRepository,
            )
            return PostgresAgentMarketRepository()
        raise ValueError(f"不支持的存储后端：{backend}")

    @staticmethod
    def create_tool_market_repository(
        storage_backend: Optional[str] = None,
    ) -> IToolMarketRepository:
        """创建 Tool 市场条目仓储实例（OP-9：仅追加，对应 CHG-08）。"""
        backend = storage_backend or get_settings().storage_backend
        if backend == "local_file":
            from orchestration_platform.persistence.local_file.local_file_tool_market_repository import (
                LocalFileToolMarketRepository,
            )
            return LocalFileToolMarketRepository()
        if backend == "postgres":
            from orchestration_platform.persistence.postgres.postgres_tool_market_repository import (
                PostgresToolMarketRepository,
            )
            return PostgresToolMarketRepository()
        raise ValueError(f"不支持的存储后端：{backend}")

    @staticmethod
    def create_dev_submission_repository(
        storage_backend: Optional[str] = None,
    ) -> IDevSubmissionRepository:
        """创建用户开发提交仓储实例（OP-9：仅追加，对应 CHG-19/DC-03）。"""
        backend = storage_backend or get_settings().storage_backend
        if backend == "local_file":
            from orchestration_platform.persistence.local_file.local_file_dev_submission_repository import (
                LocalFileDevSubmissionRepository,
            )
            return LocalFileDevSubmissionRepository()
        if backend == "postgres":
            from orchestration_platform.persistence.postgres.postgres_dev_submission_repository import (
                PostgresDevSubmissionRepository,
            )
            return PostgresDevSubmissionRepository()
        raise ValueError(f"不支持的存储后端：{backend}")
