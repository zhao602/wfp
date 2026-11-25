# =============================================================================
# 审批队列服务（application/approval_queue_service.py）
#
# 职责：B16 F4——waiting_review 实例的跨实例聚合查询（审批队列数据源）。
# 设计意图（design D1/D2/D3）：
#   1. 双模式数据源：进程内注册表扫描（list_by_status，内存权威）+
#      事件仓储读穿透（list_instance_ids → 逐实例推导，worker 模式权威）；
#      instance_id 去重以注册表优先（resolve_event_reader 同构先例）；
#   2. 快照推导零重复：worker 行经 derive_instance_snapshot_from_events
#      （instance_read_through 提取的纯函数）——状态机单源；
#   3. suspended_at 双源兜底：新事件载荷显式键 → 存量事件 occurred_at；
#   4. 租户：进程内行取 rt.tenant_id；worker 行取 INSTANCE_SUSPENDED 载荷
#      富化 tenant_id（PersistingInstanceRegistry 落账通道；缺失即 None）；
#   5. deadline = suspended_at + review_timeout_seconds（settings 既有键的
#      首个消费者，纯展示参数；≤0 或缺 suspended_at → None）。
# =============================================================================

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from orchestration_platform.api.instance_read_through import (
    _get_event_repository,
    derive_instance_snapshot_from_events,
)
from orchestration_platform.application.execution_runner import InstanceRegistry
from orchestration_platform.configuration.settings import get_settings

logger = logging.getLogger(__name__)

_WAITING_REVIEW = "waiting_review"


def _parse_iso(value: Any) -> Optional[datetime]:
    """解析 ISO 时刻；失败/空值返回 None。"""
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def compute_deadline(
    suspended_at: Optional[str], review_timeout_seconds: Optional[int]
) -> Optional[str]:
    """deadline = suspended_at + review_timeout_seconds（ISO）；不可算返回 None。"""
    suspended = _parse_iso(suspended_at)
    if suspended is None:
        return None
    if review_timeout_seconds is None or review_timeout_seconds <= 0:
        return None
    return (suspended + timedelta(seconds=review_timeout_seconds)).isoformat()


class ApprovalQueueService:
    """审批队列服务：双模式聚合 waiting_review 实例行。"""

    def __init__(
        self,
        registry: InstanceRegistry,
        event_repository: Optional[Any] = None,
    ) -> None:
        """registry 必填（进程内权威源）；event_repository 可注入

        （测试假仓储）；缺省调用期经 instance_read_through 惰性获取
        （读穿透共享同一仓储实例与缓存）。
        """
        self._registry = registry
        self._event_repository = event_repository

    def _resolve_event_repository(self) -> Optional[Any]:
        if self._event_repository is not None:
            return self._event_repository
        return _get_event_repository()

    async def list_waiting_review(
        self, tenant_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """聚合审批队列行（挂起时刻降序——新挂起优先）。

        tenant_id 给定时按租户过滤（进程内行 rt.tenant_id 相等；
        worker 行载荷富化 tenant_id 相等；租户缺失即排除——归属不可证
        不入过滤结果）。
        """
        review_timeout_seconds = get_settings().review_timeout_seconds
        rows: Dict[str, Dict[str, Any]] = {}

        # ---- 源 1：进程内注册表（in_process 权威） ----
        for rt in self._registry.list_by_status(_WAITING_REVIEW):
            if tenant_id is not None and rt.tenant_id != tenant_id:
                continue
            suspended_event = _latest_suspended_event_from_dicts(rt.events)
            payload = dict(suspended_event.get("payload") or {}) if suspended_event else {}
            suspended_at = _suspended_at_from_dict_event(
                suspended_event, payload
            )
            row = self._build_row(
                instance_id=rt.instance_id,
                definition_id=rt.definition_id,
                definition_version=rt.definition_version,
                tenant_id=rt.tenant_id,
                suspended_at=suspended_at,
                checkpoint_id=str(
                    payload.get("checkpoint_id") or rt.suspended_checkpoint_id or ""
                ),
                review_instructions=str(payload.get("review_instructions") or ""),
                timeout_action=str(payload.get("timeout_action") or ""),
                review_timeout_seconds=review_timeout_seconds,
            )
            rows[rt.instance_id] = row

        # ---- 源 2：事件仓储读穿透（worker 权威；注册表未覆盖实例） ----
        repo = self._resolve_event_repository()
        if repo is not None:
            try:
                instance_ids = await repo.list_instance_ids()
            except Exception:
                logger.warning(
                    "审批队列仓储实例列举失败（仅注册表源）",
                    exc_info=True,
                )
                instance_ids = []
            for instance_id in instance_ids:
                if instance_id in rows:
                    continue
                row = await self._worker_row(
                    repo,
                    instance_id,
                    tenant_id,
                    review_timeout_seconds,
                )
                if row is not None:
                    rows[instance_id] = row

        result = list(rows.values())
        result.sort(key=lambda item: item.get("suspended_at") or "", reverse=True)
        return result

    async def _worker_row(
        self,
        repo: Any,
        instance_id: str,
        tenant_id: Optional[str],
        review_timeout_seconds: int,
    ) -> Optional[Dict[str, Any]]:
        """worker 行：单次取事件 → 无挂起事件早跳 → 快照推导过滤。"""
        try:
            events = await repo.list_for_instance(instance_id)
        except Exception:
            logger.warning(
                "审批队列实例事件读取失败，跳过: instance_id=%s",
                instance_id,
                exc_info=True,
            )
            return None
        suspended_event = None
        for event in reversed(events):
            if str(getattr(event.event_type, "value", event.event_type)) == (
                "instance_suspended"
            ):
                suspended_event = event
                break
        if suspended_event is None:
            return None
        snapshot = derive_instance_snapshot_from_events(instance_id, events)
        if snapshot is None or snapshot.get("status") != _WAITING_REVIEW:
            return None
        payload = dict(suspended_event.payload or {})
        event_tenant = payload.get("tenant_id")
        if tenant_id is not None and event_tenant != tenant_id:
            return None
        suspended_at = payload.get("suspended_at") or (
            suspended_event.occurred_at.isoformat()
            if suspended_event.occurred_at is not None
            else None
        )
        return self._build_row(
            instance_id=instance_id,
            definition_id=str(snapshot.get("definition_id") or ""),
            definition_version=snapshot.get("definition_version"),
            tenant_id=str(event_tenant) if event_tenant else None,
            suspended_at=str(suspended_at) if suspended_at else None,
            checkpoint_id=str(payload.get("checkpoint_id") or ""),
            review_instructions=str(payload.get("review_instructions") or ""),
            timeout_action=str(payload.get("timeout_action") or ""),
            review_timeout_seconds=review_timeout_seconds,
        )

    @staticmethod
    def _build_row(
        *,
        instance_id: str,
        definition_id: str,
        definition_version: Optional[int],
        tenant_id: Optional[str],
        suspended_at: Optional[str],
        checkpoint_id: str,
        review_instructions: str,
        timeout_action: str,
        review_timeout_seconds: int,
    ) -> Dict[str, Any]:
        row: Dict[str, Any] = {
            "instance_id": instance_id,
            "definition_id": definition_id,
            "tenant_id": tenant_id,
            "status": _WAITING_REVIEW,
            "checkpoint_id": checkpoint_id,
            "suspended_at": suspended_at,
            "review_timeout_seconds": review_timeout_seconds,
            "deadline": compute_deadline(suspended_at, review_timeout_seconds),
            "timeout_action": timeout_action,
            "review_instructions": review_instructions,
        }
        # 版本随行条件键（B11 先例：非 None 才入行）
        if definition_version is not None:
            row["definition_version"] = definition_version
        return row


def _latest_suspended_event_from_dicts(
    events: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """注册表形态（dict 列表）取最新 INSTANCE_SUSPENDED 事件。"""
    for event in reversed(events):
        if str(event.get("event_type", "")) == "INSTANCE_SUSPENDED":
            return event
    return None


def _suspended_at_from_dict_event(
    event: Optional[Dict[str, Any]], payload: Dict[str, Any]
) -> Optional[str]:
    """注册表形态挂起时刻：载荷显式键 → 事件 timestamp 兜底。"""
    explicit = payload.get("suspended_at")
    if explicit:
        return str(explicit)
    if event is not None:
        fallback = event.get("timestamp")
        if fallback:
            return str(fallback)
    return None
