# =============================================================================
# 审计事件订阅器（governance/audit/audit_event_subscriber.py）
#
# 职责：订阅治理/实例领域事件，映射为审计日志并落盘（CHG-44 / DC-25 / DC-22）。
# 设计意图：
#   1. handle(event) 为纯逻辑核心（过滤→映射→脱敏→记录→降级落盘→P10 通知），
#      可独立单测；attach(event_bus, ...) 为薄壳，仅负责把消费循环挂接到
#      运行中的事件循环（DC-25 规则 3），测试永不驱动真实订阅长循环；
#   2. 事件→操作映射见 _EVENT_OPERATION_MAP；actor 取自事件载荷 actor 字段，
#      缺失时记 system（design §4.6）；
#   3. 落盘主/降级双仓储：主失败转降级，双双失败计数丢弃（total_failed），
#      不向事件流传播异常（DC-31 降级语义）；
#   4. governance.audit_written 回环事件被过滤（防自环 P10）；
#      node.* 细粒度事件不落审计（design §4.6 过滤链）；
#   5. resume_audit_hash_chain 启动期恢复哈希链续链（首个可用仓储）；
#      本地文件仓储 get_last_hash 多条目语义见 CHG-68 核对记录（零改动）。
# =============================================================================

import asyncio
import logging
from typing import Dict, List, Optional, Tuple

from orchestration_platform.domain.models.domain_event import DomainEvent
from orchestration_platform.domain.models.enums import (
    AuditOperationResult,
    OperationType,
    ResourceType,
)
from orchestration_platform.engine.abstractions.domain_event_publisher import (
    IDomainEventPublisher,
)
from orchestration_platform.governance.audit.audit_logger import AuditLogger
from orchestration_platform.governance.audit.mask_engine import MaskEngine
from orchestration_platform.governance.audit.models import AuditLogEntry
from orchestration_platform.governance.event_bus.abstractions.event_bus import IEventBus
from orchestration_platform.governance.persistence.abstractions.audit_log_repository import (
    IAuditLogRepository,
)
from orchestration_platform.governance.persistence.local_file.local_file_audit_log_repository import (
    LocalFileAuditLogRepository,
)

_logger = logging.getLogger(__name__)

# 审计已落盘通知事件类型（P10 回环事件；handle 过滤防自环）。
AUDIT_WRITTEN_EVENT_TYPE = "governance.audit_written"

# 未映射事件类型的默认操作（design §4.6 兜底行）。
_DEFAULT_OPERATION: Tuple[OperationType, AuditOperationResult] = (
    OperationType.EXECUTE,
    AuditOperationResult.SUCCESS,
)

# 事件类型 → (操作类型, 操作结果) 映射（design §4.6）。
_EVENT_OPERATION_MAP: Dict[str, Tuple[OperationType, AuditOperationResult]] = {
    "instance.started": (OperationType.CREATE, AuditOperationResult.SUCCESS),
    "instance.succeeded": (OperationType.EXECUTE, AuditOperationResult.SUCCESS),
    "instance.failed": (OperationType.EXECUTE, AuditOperationResult.FAILURE),
    "instance.suspended": (OperationType.EXECUTE, AuditOperationResult.SUCCESS),
    "instance.triggered": (OperationType.CREATE, AuditOperationResult.SUCCESS),
    "instance.created": (OperationType.CREATE, AuditOperationResult.SUCCESS),
    "instance.replayed": (OperationType.EXECUTE, AuditOperationResult.SUCCESS),
    "instance.rolled_back": (OperationType.EXECUTE, AuditOperationResult.SUCCESS),
    "governance.review_decision": (OperationType.UPDATE, AuditOperationResult.SUCCESS),
}


def acquire_audit_repository() -> Tuple[
    Optional[IAuditLogRepository], Optional[IAuditLogRepository]
]:
    """获取审计日志仓储（主/降级双后端，CHG-44）。

    优先 PostgreSQL 实现（未安装时降级），兜底本地文件仓储；
    双双不可用时返回 (None, None)。
    """
    primary: Optional[IAuditLogRepository] = None
    try:
        from orchestration_platform.governance.persistence.postgres.postgres_audit_log_repository import (
            PostgresAuditLogRepository,
        )

        primary = PostgresAuditLogRepository()
    except ImportError:
        _logger.info("PostgreSQL 审计仓储不可用，使用本地文件仓储兜底")
    try:
        fallback: Optional[IAuditLogRepository] = LocalFileAuditLogRepository()
    except Exception:
        _logger.warning("本地文件审计仓储初始化失败", exc_info=True)
        fallback = None
    return primary, fallback


class AuditEventSubscriber:
    """审计事件订阅器：领域事件 → 审计日志落盘（DC-25）。"""

    def __init__(
        self,
        primary_repository: Optional[IAuditLogRepository],
        fallback_repository: Optional[IAuditLogRepository] = None,
        audit_logger: Optional[AuditLogger] = None,
        mask_engine: Optional[MaskEngine] = None,
        domain_event_publisher: Optional[IDomainEventPublisher] = None,
    ) -> None:
        """初始化订阅器。

        Args:
            primary_repository: 主审计仓储。
            fallback_repository: 降级审计仓储（主失败时兜底）。
            audit_logger: 审计日志器（缺省新建；哈希链状态随实例维护）。
            mask_engine: 脱敏引擎（缺省新建）。
            domain_event_publisher: 领域事件发布器（P10 审计已落盘通知；
                None 表示不通知）。
        """
        self._primary_repository = primary_repository
        self._fallback_repository = fallback_repository
        self._audit_logger = audit_logger or AuditLogger()
        self._mask_engine = mask_engine or MaskEngine()
        self._domain_event_publisher = domain_event_publisher
        self._total_written = 0
        self._total_failed = 0
        self._consume_tasks: List[asyncio.Task] = []

    @property
    def total_written(self) -> int:
        """累计落盘条数（主或降级任一成功即计入）。"""
        return self._total_written

    @property
    def total_failed(self) -> int:
        """累计失败条数（主/降级仓储双双失败被丢弃）。"""
        return self._total_failed

    async def handle(self, event: DomainEvent) -> Optional[AuditLogEntry]:
        """处理单个领域事件：映射为审计条目并落盘（DC-25 规则 1/2）。

        过滤链（design §4.6）：node.* 细粒度事件与 governance.audit_written
        回环事件不落审计，返回 None；落盘成功返回条目并发布 P10 通知；
        双仓储失败返回 None（计数丢弃）。
        """
        if event.event_type.startswith("node."):
            return None
        if event.event_type == AUDIT_WRITTEN_EVENT_TYPE:
            return None
        operation, operation_result = _EVENT_OPERATION_MAP.get(
            event.event_type, _DEFAULT_OPERATION
        )
        masked = self._mask_engine.mask_record(
            dict(event.payload), tenant_id=event.tenant_id
        )
        actor = masked.get("actor")
        entry = await self._audit_logger.record(
            tenant_id=event.tenant_id,
            actor_id=str(actor) if actor else "system",
            resource_type=ResourceType.EXECUTION_INSTANCE,
            resource_id=event.instance_id,
            operation=operation,
            trace_id=event.trace_id,
            operation_result=operation_result,
            request_payload=masked,
            actor_type="user" if actor else "system",
        )
        appended = await self._append_with_degradation(entry)
        if not appended:
            return None
        self._total_written += 1
        await self._publish_audit_written(entry)
        return entry

    async def _append_with_degradation(self, entry: AuditLogEntry) -> bool:
        """降级落盘：主失败转降级，双双失败计数丢弃（不抛异常）。"""
        if self._primary_repository is not None:
            try:
                await self._primary_repository.append(entry)
                return True
            except Exception:
                _logger.warning(
                    "审计日志主仓储写入失败，转降级仓储: entry_id=%s",
                    entry.entry_id,
                    exc_info=True,
                )
        if self._fallback_repository is not None:
            try:
                await self._fallback_repository.append(entry)
                return True
            except Exception:
                _logger.warning(
                    "审计日志降级仓储写入失败: entry_id=%s",
                    entry.entry_id,
                    exc_info=True,
                )
        self._total_failed += 1
        _logger.warning(
            "审计日志写入失败（主/降级仓储均不可用），条目丢弃: entry_id=%s",
            entry.entry_id,
        )
        return False

    async def _publish_audit_written(self, entry: AuditLogEntry) -> None:
        """发布「审计已落盘」通知事件（P10）；失败降级吞异常（DC-31）。"""
        if self._domain_event_publisher is None:
            return
        try:
            event = DomainEvent(
                event_type=AUDIT_WRITTEN_EVENT_TYPE,
                instance_id=entry.resource_id,
                tenant_id=entry.tenant_id,
                trace_id=entry.trace_id,
                payload={"entry_id": entry.entry_id, "tenant_id": entry.tenant_id},
            )
            await self._domain_event_publisher.publish_domain_event(event)
        except Exception:
            _logger.warning(
                "审计已落盘通知事件发布失败（降级吞异常）: entry_id=%s",
                entry.entry_id,
                exc_info=True,
            )

    async def resume_audit_hash_chain(self) -> None:
        """从首个可用仓储恢复审计哈希链（启动期调用一次，DC-25）。"""
        for repository in (self._primary_repository, self._fallback_repository):
            if repository is None:
                continue
            try:
                last_hash = await repository.get_last_hash()
            except Exception:
                _logger.warning(
                    "审计哈希链恢复失败（仓储读取异常），空链起步",
                    exc_info=True,
                )
                return
            self._audit_logger.set_last_hash(last_hash)
            _logger.info(
                "审计哈希链已恢复: last_hash=%s...",
                (last_hash or "")[:16],
            )
            return
        _logger.info("审计仓储不可用，哈希链空链起步")

    def attach(
        self,
        event_bus: IEventBus,
        topic: str,
        group_id: str = "audit_event_subscriber",
    ) -> Optional[asyncio.Task]:
        """将消费循环挂接到运行中的事件循环（薄壳，DC-25 规则 3）。

        必须在异步上下文（如 FastAPI startup 事件）中调用；无运行事件
        循环时记录告警并返回 None（不抛异常，保证装配安全）。
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            _logger.warning("审计事件订阅挂接失败：无运行事件循环（需在异步上下文调用）")
            return None
        task = loop.create_task(self._consume_loop(event_bus, topic, group_id))
        self._consume_tasks.append(task)
        _logger.info("审计事件订阅已挂接: topic=%s, group_id=%s", topic, group_id)
        return task

    async def _consume_loop(
        self,
        event_bus: IEventBus,
        topic: str,
        group_id: str,
    ) -> None:
        """消费循环：逐条解析领域事件并落盘；单条失败不中断（DC-25）。"""
        async for message in event_bus.subscribe(topic, group_id):
            try:
                event = DomainEvent.model_validate(message.payload.get("domain_event"))
            except Exception:
                _logger.warning(
                    "审计订阅收到无法解析的领域事件，跳过: event_id=%s",
                    message.event_id,
                    exc_info=True,
                )
                continue
            try:
                await self.handle(event)
            except Exception:
                _logger.warning(
                    "审计事件处理失败，跳过: event_type=%s, instance_id=%s",
                    event.event_type,
                    event.instance_id,
                    exc_info=True,
                )
                continue
