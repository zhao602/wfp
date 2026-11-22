# =============================================================================
# Webhook 投递工作器（governance/event_export/webhook_delivery_worker.py）
#
# 职责：消费领域事件总线，匹配订阅后经 httpx 投递 Webhook（roadmap B3）——
#       HMAC-SHA256 签名 + 指数退避重投 + 内存死信队列（DLQ）。
# 设计意图：
#   1. AuditEventSubscriber 同范式：handle() 纯逻辑核心 + attach() 薄壳
#      （DC-25 规则 3）；消费循环单条失败不中断（DC-31 降级语义）；
#   2. 0 丢失不变式（进程生命周期内）：单订阅视角每事件终态
#      ∈ {delivered, dead_lettered}——_deliver 每次失败必走「重试」或
#      「入死信」二选一，无第三态；
#   3. 重试态/死信为内存态（用户裁决 2026-11-21）：重启清空为已文档化
#      边界（guide）；shutdown 时在途投递任务被取消（stop 语义）；
#   4. 参数全构造注入（测试退避 base=0.001；http_client 注入 MockTransport）；
#   5. httpx 客户端惰性创建（首次投递时建，避免构造期绑定事件循环）。
# =============================================================================

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

import httpx

from orchestration_platform.domain.models.domain_event import DomainEvent
from orchestration_platform.governance.event_bus.abstractions.event_bus import IEventBus
from orchestration_platform.governance.event_bus.models import EventMessage
from orchestration_platform.governance.event_export.models import (
    DeadLetterEntry,
    DeliveryRecord,
    WebhookSubscription,
    event_type_matches,
)
from orchestration_platform.governance.event_export.signature import (
    EVENT_TYPE_HEADER,
    INSTANCE_ID_HEADER,
    SIGNATURE_HEADER,
    canonical_event_body,
    sign_webhook,
)
from orchestration_platform.governance.event_export.subscription_registry import (
    EventSubscriptionRegistry,
)

_logger = logging.getLogger(__name__)

# 出口 worker 默认消费组标识（与审计订阅组不同 → 扇出各得全量）。
DEFAULT_EVENT_EXPORT_GROUP_ID = "event_export_worker"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class WebhookDeliveryWorker:
    """Webhook 投递工作器：匹配订阅 → 签名投递 → 退避重投 → 死信兜底。"""

    def __init__(
        self,
        registry: EventSubscriptionRegistry,
        *,
        max_attempts: int = 5,
        backoff_base_seconds: float = 1.0,
        backoff_cap_seconds: float = 60.0,
        timeout_seconds: float = 5.0,
        delivery_history_max: int = 1000,
        dlq_max_entries: int = 1000,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        """初始化工作器（参数由装配层按 settings 注入）。

        Args:
            registry: 订阅注册表（与路由共享同一内存实例）。
            max_attempts: 单事件（单投递周期）最大尝试次数。
            backoff_base_seconds: 指数退避基数（第 n 次失败后睡 base*2^(n-1)）。
            backoff_cap_seconds: 退避上限。
            timeout_seconds: 单次 HTTP 请求超时（自建客户端时生效）。
            delivery_history_max: 每订阅投递记录环形上限。
            dlq_max_entries: 死信总量上限（超限丢最旧 + 告警）。
            http_client: 注入 httpx 客户端（测试 MockTransport）；None 惰性自建。
        """
        self._registry = registry
        self._max_attempts = max_attempts
        self._backoff_base_seconds = backoff_base_seconds
        self._backoff_cap_seconds = backoff_cap_seconds
        self._timeout_seconds = timeout_seconds
        self._delivery_history_max = delivery_history_max
        self._dlq_max_entries = dlq_max_entries
        self._client = http_client
        self._owns_client = http_client is None
        self._deliveries: Dict[str, List[DeliveryRecord]] = {}
        self._dead_letters: List[DeadLetterEntry] = []
        self._dead_letter_index: Dict[str, DeadLetterEntry] = {}
        self._delivery_tasks: Set[asyncio.Task] = set()
        self._consume_tasks: List[asyncio.Task] = []

    # ---------------- 匹配与投递（纯逻辑核心） ----------------

    def handle(
        self,
        event: DomainEvent,
        *,
        event_id: str = "",
    ) -> List[asyncio.Task]:
        """处理单个领域事件：匹配订阅并为每匹配订阅派发异步投递任务。

        纯逻辑核心（不等待投递完成——出口旁路原则）；event_id 为事件
        信封标识（EventMessage.event_id，用于投递记录/死信溯源），缺省
        回退 trace_id 保证记录标识非空。
        """
        resolved_event_id = event_id or event.trace_id
        tasks: List[asyncio.Task] = []
        for subscription in self._match_subscriptions(event):
            task = asyncio.create_task(
                self._deliver(subscription, event, resolved_event_id)
            )
            self._delivery_tasks.add(task)
            task.add_done_callback(self._delivery_tasks.discard)
            tasks.append(task)
        return tasks

    def _match_subscriptions(self, event: DomainEvent) -> List[WebhookSubscription]:
        """匹配订阅：enabled + tenant_id 相等 + event_types 过滤。"""
        matched: List[WebhookSubscription] = []
        for subscription in self._registry.list():
            if not subscription.enabled:
                continue
            if subscription.tenant_id != event.tenant_id:
                continue
            if not event_type_matches(subscription.event_types, event.event_type):
                continue
            matched.append(subscription)
        return matched

    async def _deliver(
        self,
        subscription: WebhookSubscription,
        event: DomainEvent,
        event_id: str,
        *,
        dead_letter_entry: Optional[DeadLetterEntry] = None,
    ) -> bool:
        """执行一个投递周期（指数退避重试至多 max_attempts 次）。

        返回是否投递成功。dead_letter_entry 非 None 时为 redrive 路径：
        失败更新既有死信条目（不新建）；成功后由调用方移出 DLQ。
        """
        body = canonical_event_body(event)
        attempts = 0
        last_error = ""
        while attempts < self._max_attempts:
            attempts += 1
            timestamp = int(time.time())
            headers = {
                "Content-Type": "application/json",
                SIGNATURE_HEADER: sign_webhook(subscription.secret, timestamp, body),
                EVENT_TYPE_HEADER: event.event_type,
                INSTANCE_ID_HEADER: event.instance_id,
            }
            try:
                client = await self._get_client()
                response = await client.post(
                    subscription.url,
                    content=body.encode("utf-8"),
                    headers=headers,
                )
                if 200 <= response.status_code < 300:
                    if dead_letter_entry is not None:
                        dead_letter_entry.attempts += attempts
                    self._record_delivery(
                        DeliveryRecord(
                            subscription_id=subscription.subscription_id,
                            event_id=event_id,
                            event_type=event.event_type,
                            instance_id=event.instance_id,
                            attempts=attempts,
                            last_status="delivered",
                            delivered_at=_utc_now(),
                        )
                    )
                    return True
                last_error = f"HTTP {response.status_code}"
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            if attempts < self._max_attempts:
                delay = min(
                    self._backoff_base_seconds * (2 ** (attempts - 1)),
                    self._backoff_cap_seconds,
                )
                await asyncio.sleep(delay)
        self._record_delivery(
            DeliveryRecord(
                subscription_id=subscription.subscription_id,
                event_id=event_id,
                event_type=event.event_type,
                instance_id=event.instance_id,
                attempts=attempts,
                last_status="failed",
                last_error=last_error,
            )
        )
        if dead_letter_entry is not None:
            # redrive 失败：更新既有死信条目（attempts 累加），不新建。
            dead_letter_entry.attempts += attempts
            dead_letter_entry.last_error = last_error
            dead_letter_entry.failed_at = _utc_now()
        else:
            self._register_dead_letter(
                DeadLetterEntry(
                    entry_id=f"dlq_{uuid.uuid4().hex[:12]}",
                    subscription_id=subscription.subscription_id,
                    event_id=event_id,
                    event_type=event.event_type,
                    instance_id=event.instance_id,
                    attempts=attempts,
                    failed_at=_utc_now(),
                    last_error=last_error,
                    payload_snapshot=event.model_dump(mode="json"),
                )
            )
        return False

    async def _get_client(self) -> httpx.AsyncClient:
        """取 httpx 客户端（惰性自建，避免构造期绑定事件循环）。"""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout_seconds)
        return self._client

    # ---------------- 投递记录与死信队列 ----------------

    def _record_delivery(self, record: DeliveryRecord) -> None:
        """登记投递记录（每订阅环形上限，超限丢最旧）。"""
        history = self._deliveries.setdefault(record.subscription_id, [])
        history.append(record)
        overflow = len(history) - self._delivery_history_max
        if overflow > 0:
            del history[:overflow]

    def list_deliveries(self, subscription_id: str) -> List[DeliveryRecord]:
        """列出订阅的投递记录（新到旧；无记录返回空列表）。"""
        return list(self._deliveries.get(subscription_id, []))

    def _register_dead_letter(self, entry: DeadLetterEntry) -> None:
        """登记死信（总量上限，超限丢最旧 + 告警）。"""
        self._dead_letters.append(entry)
        self._dead_letter_index[entry.entry_id] = entry
        overflow = len(self._dead_letters) - self._dlq_max_entries
        if overflow > 0:
            evicted = self._dead_letters[:overflow]
            del self._dead_letters[:overflow]
            for item in evicted:
                self._dead_letter_index.pop(item.entry_id, None)
            _logger.warning(
                "死信总量达到上限，丢弃最旧 %d 条", overflow,
            )

    def _remove_dead_letter(self, entry_id: str) -> None:
        """移出死信（redrive 成功路径）。"""
        entry = self._dead_letter_index.pop(entry_id, None)
        if entry is None:
            return
        try:
            self._dead_letters.remove(entry)
        except ValueError:
            pass

    def list_dead_letters(self) -> List[DeadLetterEntry]:
        """列出死信条目（旧到新）。"""
        return list(self._dead_letters)

    async def redrive(self, entry_id: str) -> DeadLetterEntry:
        """死信重投：重建事件快照走完整投递周期；成功移出 DLQ。

        未注册 entry_id 抛 KeyError（路由层映射 404）；订阅不存在/停用或
        快照无法解析时更新条目失败原因并保留在 DLQ（返回条目供调用方检查）。
        """
        entry = self._dead_letter_index.get(entry_id)
        if entry is None:
            raise KeyError(entry_id)
        try:
            subscription = self._registry.get(entry.subscription_id)
        except KeyError:
            entry.last_error = "订阅不存在，无法重投"
            return entry
        if not subscription.enabled:
            entry.last_error = "订阅已停用，无法重投"
            return entry
        try:
            event = DomainEvent.model_validate(entry.payload_snapshot)
        except Exception:
            entry.last_error = "死信载荷无法解析为领域事件，无法重投"
            return entry
        delivered = await self._deliver(
            subscription,
            event,
            entry.event_id,
            dead_letter_entry=entry,
        )
        if delivered:
            self._remove_dead_letter(entry_id)
        return entry

    # ---------------- 消费循环（attach 薄壳） ----------------

    def attach(
        self,
        event_bus: IEventBus,
        topic: str,
        group_id: str = DEFAULT_EVENT_EXPORT_GROUP_ID,
    ) -> Optional[asyncio.Task]:
        """将消费循环挂接到运行中的事件循环（薄壳，DC-25 规则 3）。

        必须在异步上下文（如 FastAPI startup 事件）中调用；无运行事件
        循环时记录告警并返回 None（不抛异常，保证装配安全）。
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            _logger.warning("事件出口订阅挂接失败：无运行事件循环（需在异步上下文调用）")
            return None
        task = loop.create_task(self._consume_loop(event_bus, topic, group_id))
        self._consume_tasks.append(task)
        _logger.info("事件出口订阅已挂接: topic=%s, group_id=%s", topic, group_id)
        return task

    async def _consume_loop(
        self,
        event_bus: IEventBus,
        topic: str,
        group_id: str,
    ) -> None:
        """消费循环：逐条解析领域事件并派发投递；单条失败不中断。"""
        async for message in event_bus.subscribe(topic, group_id):
            try:
                event = DomainEvent.model_validate(message.payload.get("domain_event"))
            except Exception:
                _logger.warning(
                    "事件出口订阅收到无法解析的领域事件，跳过: event_id=%s",
                    message.event_id,
                    exc_info=True,
                )
                continue
            try:
                self.handle(event, event_id=message.event_id)
            except Exception:
                _logger.warning(
                    "事件出口处理失败，跳过: event_type=%s, instance_id=%s",
                    event.event_type,
                    event.instance_id,
                    exc_info=True,
                )
                continue

    def stop(self) -> None:
        """停止消费与在途投递任务并关闭自建客户端（幂等）。

        在途投递被取消（shutdown 语义）：取消时事件既未 delivered 也未
        dead_lettered——进程生命周期边界的已文档化已知行为（guide）。
        """
        for task in list(self._consume_tasks):
            task.cancel()
        self._consume_tasks.clear()
        for task in list(self._delivery_tasks):
            task.cancel()
        self._delivery_tasks.clear()
        if self._owns_client and self._client is not None:
            client, self._client, self._owns_client = self._client, None, False
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop is not None:
                loop.create_task(client.aclose())
            else:
                _logger.warning("事件出口停止时无运行事件循环，跳过客户端关闭")
