# =============================================================================
# 实例读穿透（api/instance_read_through.py）
#
# 职责：worker 模式下 API 进程的实例状态/事件读穿透——注册表（in_process
#       语义）未命中时回退事件仓储（worker 进程 PersistingInstanceRegistry
#       双写落账的数据源）。
# 设计意图（roadmap-v3 中期窗口 M2，plan 状态读穿透）：
#   1. InstanceEventReader 协议双实现（Registry / Repository）——SSE
#      event_generator 经统一读取协议轮询，注册表路径行为与历史逐字段
#      等价（events 序、终态集合、STREAM_END 帧、0.1s 轮询）；
#   2. 状态推导依赖 event_type 而非 payload——PG 事件仓储读侧
#      _row_to_event 既有丢弃 payload 行为，枚举载体两后端均可靠（design D2）；
#   3. 解析序（design D5）：注册表命中 → Registry 路径；未命中 → 事件仓储
#      有事件 → Repository 路径；均未命中 → HTTPException 404（与注册表
#      同文案，404 语义不变）。worker 模式实例自 worker 认领写首事件起可见
#      （提交→认领窗口内 404，docs/deploy.md §10 诚实口径）。
# =============================================================================

import logging
import uuid
from typing import Any, Dict, List, Optional, Protocol

from fastapi import HTTPException

from orchestration_platform.application.execution_runner import (
    _TERMINAL_STATUSES,
    InstanceRegistry,
)
from orchestration_platform.domain.models.enums import EventType
from orchestration_platform.domain.models.execution_event import ExecutionEvent

logger = logging.getLogger(__name__)


# =============================================================================
# 事件仓储持有（模块级惰性单例；测试经 monkeypatch 模块属性注入）
# =============================================================================

_event_repository_instance: Optional[Any] = None
_event_repository_failed = False


def _get_event_repository() -> Optional[Any]:
    """惰性构造事件仓储（双后端经仓储工厂；失败降级 None + 告警）。"""
    global _event_repository_instance, _event_repository_failed
    if _event_repository_instance is not None:
        return _event_repository_instance
    if _event_repository_failed:
        return None
    try:
        from orchestration_platform.persistence.repository_factory import (
            RepositoryFactory,
        )

        _event_repository_instance = RepositoryFactory.create_event_repository()
        return _event_repository_instance
    except Exception:
        _event_repository_failed = True
        logger.warning(
            "事件仓储获取失败，读穿透不可用（注册表口径兜底）", exc_info=True
        )
        return None


# =============================================================================
# InstanceEventReader 协议与双实现（plan M2：Registry / Repository）
# =============================================================================

class InstanceEventReader(Protocol):
    """实例事件读取器：SSE 轮询与终态判定的统一读取协议。"""

    async def read_events(self, instance_id: str) -> List[Dict[str, Any]]:
        """读取实例事件序列（注册表形态 dict 列表）。"""
        ...

    async def is_terminal(self, instance_id: str) -> bool:
        """判定实例是否处于 SSE 收尾终态。"""
        ...


class RegistryEventReader:
    """注册表读取器：in_process 语义（行为与历史 event_generator 等价）。"""

    def __init__(self, registry: InstanceRegistry) -> None:
        self._registry = registry

    async def read_events(self, instance_id: str) -> List[Dict[str, Any]]:
        return self._registry.get_events(instance_id)

    async def is_terminal(self, instance_id: str) -> bool:
        rt = self._registry._instances.get(instance_id)
        return rt is not None and rt.status in _TERMINAL_STATUSES


# 仓储事件 → 注册表事件类型反映射（NODE_SUCCEEDED 为持久化载体，
# 注册表口径为 NODE_COMPLETED，design D1）；其余取枚举值大写
_REVERSE_TYPE_MAP: Dict[EventType, str] = {
    EventType.NODE_SUCCEEDED: "NODE_COMPLETED",
}

# 实例级终态事件（SSE 收尾判定；waiting_review 非终态——流保持开启等待恢复；
# M3：INSTANCE_CANCELLED 入终态集合——盲区取消事件即收尾帧）
_REPO_TERMINAL_TYPES = (
    EventType.INSTANCE_COMPLETED,
    EventType.INSTANCE_FAILED,
    EventType.INSTANCE_CANCELLED,
)


class RepositoryEventReader:
    """事件仓储读取器：worker 模式回退路径（list_for_instance 轮询）。"""

    def __init__(self, event_repository: Any) -> None:
        self._repo = event_repository

    async def read_events(self, instance_id: str) -> List[Dict[str, Any]]:
        events = await self._repo.list_for_instance(instance_id)
        return [self._normalize(e) for e in events]

    async def is_terminal(self, instance_id: str) -> bool:
        events = await self._repo.list_for_instance(instance_id)
        # 从尾部找最近一条实例级事件判定（节点事件不参与终态判定）
        for event in reversed(events):
            if event.event_type in _REPO_TERMINAL_TYPES:
                return True
            # M5 决策可见性（design D7）：review reject 决策留痕
            # STATUS_CHANGED(to_status=failed/cancelled) 即 SSE 收尾；
            # approve→running / timeout_action→review_timeout 不收流
            # （镜像 in_process _TERMINAL_STATUSES 收尾语义）
            if (
                event.event_type == EventType.STATUS_CHANGED
                and str((event.payload or {}).get("to_status", "") or "")
                in _TERMINAL_STATUSES
            ):
                return True
            if event.event_type in (
                EventType.INSTANCE_STARTED,
                EventType.INSTANCE_SUSPENDED,
            ):
                return False
        return False

    @staticmethod
    def _normalize(event: ExecutionEvent) -> Dict[str, Any]:
        """仓储事件 → 注册表事件形态（event_type 大写、timestamp ISO）。"""
        type_str = _REVERSE_TYPE_MAP.get(
            event.event_type, str(event.event_type.value).upper()
        )
        timestamp = (
            event.occurred_at.isoformat()
            if event.occurred_at is not None
            else None
        )
        return {
            "event_id": event.event_id,
            "instance_id": event.instance_id,
            "event_type": type_str,
            "node_id": event.node_id,
            "timestamp": timestamp,
            "payload": dict(event.payload or {}),
        }


# =============================================================================
# 解析器与快照推导
# =============================================================================

async def resolve_event_reader(
    instance_id: str, *, registry: InstanceRegistry
) -> InstanceEventReader:
    """解析事件读取器：注册表优先，未命中回退事件仓储；均未命中 404。

    registry 由调用方命名空间传入（调用期求值，router 模块 monkeypatch
    语义保持）；404 形状与注册表 get 完全一致（detail 同文案）。
    """
    try:
        registry.get(instance_id)
        return RegistryEventReader(registry)
    except HTTPException:
        repo = _get_event_repository()
        if repo is not None:
            try:
                events = await repo.list_for_instance(instance_id)
            except Exception:
                logger.warning(
                    "读穿透事件仓储查询失败，按未知实例处理: instance_id=%s",
                    instance_id,
                    exc_info=True,
                )
                raise
            if events:
                return RepositoryEventReader(repo)
        raise


async def read_instance_snapshot(
    instance_id: str, *, event_repository: Optional[Any] = None
) -> Optional[Dict[str, Any]]:
    """从事件仓储推导实例快照（worker 模式 GET /instances/{id} 读穿透）。

    无事件返回 None（调用方维持 404）；状态推导按实例级事件类型
    （INSTANCE_QUEUED→pending（M5 可见窗，提交→认领窗口期不再 404）/
    INSTANCE_STARTED→running / COMPLETED→succeeded / FAILED→failed /
    SUSPENDED→waiting_review / CANCELLED→cancelled（M3 design D5——盲区
    取消事件使 GET 立即可见终态）/ STATUS_CHANGED→payload.to_status
    （M5 决策可见性——review reject/timeout 经决策留痕事件立即可见）；
    definition_id 取 INSTANCE_QUEUED/STARTED/CANCELLED 载荷富化值
    （local_file 后端可靠；PG 读侧 payload 已于 M5 T2 修复）。
    """
    repo = event_repository if event_repository is not None else _get_event_repository()
    if repo is None:
        return None
    try:
        events = await repo.list_for_instance(instance_id)
    except Exception:
        logger.warning(
            "读穿透快照查询失败，按未知实例处理: instance_id=%s",
            instance_id,
            exc_info=True,
        )
        return None
    if not events:
        return None

    status = "running"
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    definition_id = ""
    for event in events:
        if event.event_type == EventType.INSTANCE_QUEUED:
            # M5 可见窗（design D5）：提交即落队列事件——窗口期推导 pending
            # （后续 INSTANCE_STARTED 覆盖 running；仅此事件 = 未认领）
            status = "pending"
            if not definition_id:
                definition_id = str(
                    (event.payload or {}).get("definition_id", "") or ""
                )
        elif event.event_type == EventType.INSTANCE_STARTED:
            status = "running"
            if started_at is None:
                started_at = (
                    event.occurred_at.isoformat()
                    if event.occurred_at is not None
                    else None
                )
                definition_id = str(
                    (event.payload or {}).get("definition_id", "") or ""
                )
        elif event.event_type == EventType.STATUS_CHANGED:
            # M5 决策可见性（design D7）：review 决策留痕事件（服务
            # submit_decision 落账）——to_status 推导快照状态；approve →
            # running（续跑终态由后续 INSTANCE_COMPLETED 收口），reject →
            # failed/cancelled（终态 finished_at 置留痕时刻）
            to_status = str((event.payload or {}).get("to_status", "") or "")
            if to_status:
                status = to_status
                if to_status in _TERMINAL_STATUSES:
                    finished_at = (
                        event.occurred_at.isoformat()
                        if event.occurred_at is not None
                        else None
                    )
        elif event.event_type == EventType.INSTANCE_COMPLETED:
            status = "succeeded"
            finished_at = (
                event.occurred_at.isoformat()
                if event.occurred_at is not None
                else None
            )
        elif event.event_type == EventType.INSTANCE_FAILED:
            status = "failed"
            finished_at = (
                event.occurred_at.isoformat()
                if event.occurred_at is not None
                else None
            )
        elif event.event_type == EventType.INSTANCE_CANCELLED:
            # M3 盲区取消（design D5）：pending 作业取消事件先于
            # INSTANCE_STARTED 存在（worker 未认领），definition_id 取
            # 本事件载荷（cancel 端点富化 job.definition_id）
            status = "cancelled"
            finished_at = (
                event.occurred_at.isoformat()
                if event.occurred_at is not None
                else None
            )
            if not definition_id:
                definition_id = str(
                    (event.payload or {}).get("definition_id", "") or ""
                )
        elif event.event_type == EventType.INSTANCE_SUSPENDED:
            status = "waiting_review"
    result: Dict[str, Any] = {
        "instance_id": instance_id,
        "definition_id": definition_id,
        "status": status,
        "started_at": started_at,
    }
    if finished_at is not None:
        result["finished_at"] = finished_at
    return result


async def record_instance_cancelled_event(
    instance_id: str, *, definition_id: Optional[str] = None
) -> bool:
    """端点侧落 INSTANCE_CANCELLED 事件（M3 盲区可见性，design D5）。

    pending 作业取消（worker 未认领、事件仓储无该实例事件）时由 cancel
    端点调用：GET /instances/{id} 与 SSE 经事件仓储立即可见 cancelled
    终态（worker 认领时标志检查后不执行直接收口，不再补事件——避免双写）。
    仓储不可达或落账失败返回 False（可见性降级仅告警，取消响应契约不变）。
    """
    repo = _get_event_repository()
    if repo is None:
        logger.warning(
            "盲区取消事件落账跳过（事件仓储不可达）: instance_id=%s",
            instance_id,
        )
        return False
    try:
        await repo.append(
            ExecutionEvent(
                event_id=str(uuid.uuid4()),
                instance_id=instance_id,
                event_type=EventType.INSTANCE_CANCELLED,
                node_id=None,
                sequence=0,
                payload={"definition_id": definition_id or ""},
            )
        )
        return True
    except Exception:
        logger.warning(
            "盲区取消事件落账失败: instance_id=%s", instance_id, exc_info=True
        )
        return False
