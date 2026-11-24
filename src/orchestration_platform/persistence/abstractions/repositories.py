# =============================================================================
# 仓储抽象接口（persistence/abstractions/repositories.py）
#
# 职责：定义工作流定义 / 检查点 / 执行事件三类仓储的抽象接口（Protocol）。
# 设计意图（对应需求规格 4.6、4.5.3、design 2.2.2.3）：
#   1. 接口与实现分离——业务层依赖 Protocol，由 repository_factory 按配置注入具体实现；
#   2. 双实现可切换——PostgreSQL（生产）与本地文件（验证降级）行为一致；
#   3. 异步接口——为 PostgreSQL 异步驱动预留，本地文件实现以 async def 对齐。
# =============================================================================

from typing import List, Optional, Protocol, runtime_checkable

from orchestration_platform.domain.models.checkpoint import (
    Checkpoint,
    CheckpointSummary,
)
from orchestration_platform.domain.models.execution_event import ExecutionEvent
from orchestration_platform.domain.models.execution_instance import DefinitionRef
from orchestration_platform.domain.models.workflow_definition import WorkflowDefinition


@runtime_checkable
class IWorkflowRepository(Protocol):
    """工作流定义仓储：支持版本化保存与历史查询。"""

    async def save(self, definition: WorkflowDefinition) -> DefinitionRef:
        """保存工作流定义（按 definition_id + definition_version 唯一存储）。

        返回 DefinitionRef（标识 + 版本 + 创建时间）。
        同一版本重复保存时覆盖（幂等）。
        B11 发布态不可变守卫：目标版本已为 published 时抛
        InstanceStateConflictError(WF_STATE_CONFLICT)（仓储写边界强制，
        双后端一致；API 保存路径每次分配新版本号，结构性不触发）。
        """
        ...

    async def get(
        self, definition_id: str, version: Optional[int] = None
    ) -> WorkflowDefinition:
        """按标识查询定义；version 缺省返回最新版本。

        不存在时抛出 ResourceNotFoundError(WF_NOT_FOUND)。
        """
        ...

    async def list_versions(self, definition_id: str) -> List[DefinitionRef]:
        """列出指定定义的全部历史版本，按版本号降序排列。"""
        ...

    async def get_latest_version(self, definition_id: str) -> Optional[int]:
        """查询指定定义的当前最大版本号（B11 版本递增依据）。

        定义不存在时返回 None（与 get 缺省版抛 404 语义区分——
        版本分配场景「无历史」合法，调用方以 (result or 0) + 1 起版）。
        """
        ...

    async def get_published(
        self, definition_id: str
    ) -> Optional[WorkflowDefinition]:
        """查询指定定义的最新已发布（published）版本（B11）。

        无已发布版本返回 None（执行入口缺省解析口径：无 published 不可上线）；
        语义与 get 缺省「最新版本」严格区分——get 含 draft，本方法仅 published。
        """
        ...

    async def publish(
        self, definition_id: str, version: int
    ) -> DefinitionRef:
        """将指定版本置为发布态（B11：draft → published 状态迁移）。

        目标版本不存在抛 ResourceNotFoundError(WF_NOT_FOUND)；
        已是 published 幂等返回（重复发布非改写，不 409）；
        迁移后 status=published、published_at=now（draft_of 留存为溯源）。
        """
        ...


@runtime_checkable
class ICheckpointRepository(Protocol):
    """检查点仓储：追加写、查询、标记不可用。"""

    async def append(self, checkpoint: Checkpoint) -> str:
        """追加检查点（不可变写入），返回 checkpoint_id。

        同一 checkpoint_id 重复追加时抛出状态冲突错误。
        """
        ...

    async def get(self, checkpoint_id: str) -> Optional[Checkpoint]:
        """按标识查询检查点，不存在返回 None。"""
        ...

    async def latest_for_instance(self, instance_id: str) -> Optional[Checkpoint]:
        """查询指定实例的最新检查点（round_index 最大且 is_available=True）。"""
        ...

    async def list_for_instance(self, instance_id: str) -> List[CheckpointSummary]:
        """列出指定实例的全部检查点摘要，按 round_index 降序排列。"""
        ...

    async def mark_unavailable(self, checkpoint_id: str, reason: str) -> None:
        """将检查点标记为不可用（反序列化失败或损坏时调用）。"""
        ...


@runtime_checkable
class IEventRepository(Protocol):
    """执行事件仓储：不可变追加 + 作废标记。"""

    async def append(self, event: ExecutionEvent) -> int:
        """追加执行事件，返回事件序号（sequence）。

        保证同一实例内 sequence 严格递增；
        若事件已携带 sequence，则校验其等于当前最大序号 + 1。
        """
        ...

    async def list_for_instance(
        self, instance_id: str, include_voided: bool = True
    ) -> List[ExecutionEvent]:
        """列出指定实例的全部事件，按 sequence 升序排列。

        include_voided=False 时排除已作废事件。
        """
        ...

    async def void_after(self, instance_id: str, sequence: int) -> int:
        """将指定实例中 sequence 大于给定值的事件批量标记为 voided=True。

        返回被作废的事件数量。绝不物理删除（事件溯源不可变原则）。
        """
        ...
