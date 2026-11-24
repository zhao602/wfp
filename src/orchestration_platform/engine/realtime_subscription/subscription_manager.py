# =============================================================================
# 实时数据订阅管理器（engine/realtime_subscription/subscription_manager.py）
#
# 职责：管理数据流订阅的生命周期，监听事件并自动触发工作流。
# 设计意图（对应项目构建计划书「实时数据订阅触发」要求）：
#   1. 创建订阅后启动异步监听任务，从数据源拉取事件；
#   2. 事件经过滤规则匹配后，调用引擎启动工作流实例；
#   3. 订阅可暂停/恢复/删除，暂停时停止监听但不删除配置；
#   4. 支持最大触发次数限制，达到后自动停止订阅。
# =============================================================================

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from orchestration_platform.engine.realtime_subscription.models import (
    EventFilterRule,
    RealtimeSubscription,
    SubscriptionCreateRequest,
    SubscriptionStatus,
    SubscriptionTriggerEvent,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_subscription_id() -> str:
    return f"sub_{uuid.uuid4().hex[:12]}"


class RealtimeSubscriptionManager:
    """实时数据订阅管理器。

    管理订阅生命周期，监听数据流事件并自动触发目标工作流。
    通过注入的 workflow_trigger 回调启动工作流实例，解耦具体引擎实现。
    """

    def __init__(
        self,
        workflow_trigger: Callable[[str, int, Dict[str, Any]], "asyncio.Future"],
        template_registry: Optional["object"] = None,
    ) -> None:
        """初始化订阅管理器。

        Args:
            workflow_trigger: 工作流触发回调，签名为
                async def trigger(definition_id: str, version: int, inputs: dict) -> str
                返回启动的实例 ID。
            template_registry: 触发器模板注册表（第五阶段 G6/T225，可选）。
                为 None 时不支持模板绑定，行为与修正前一致。
        """
        self._subscriptions: Dict[str, RealtimeSubscription] = {}
        self._listen_tasks: Dict[str, asyncio.Task] = {}
        self._trigger_history: List[SubscriptionTriggerEvent] = []
        self._workflow_trigger = workflow_trigger
        self._event_listeners: Dict[str, Callable] = {}
        # 第五阶段偏差修正（G6/T225）：评估器按订阅维度隔离挂载
        self._template_registry = template_registry
        self._evaluators: Dict[str, "object"] = {}

    def register_event_listener(
        self,
        source_type: str,
        listener: Callable,
    ) -> None:
        """注册数据源事件监听器。

        Args:
            source_type: 数据源类型字符串（如 "kafka"）。
            listener: 事件监听器，签名为
                async def listen(connection_ref: str, event_source: str) -> AsyncIterator[dict]
                产出事件数据字典。
        """
        self._event_listeners[source_type] = listener

    async def create_subscription(
        self,
        request: SubscriptionCreateRequest,
    ) -> RealtimeSubscription:
        """创建并启动数据流订阅。"""
        subscription = RealtimeSubscription(
            subscription_id=_new_subscription_id(),
            workflow_definition_id=request.workflow_definition_id,
            workflow_definition_version=request.workflow_definition_version,
            source_type=request.source_type,
            connection_reference=request.connection_reference,
            event_source=request.event_source,
            filter_rules=request.filter_rules,
            input_mapping=request.input_mapping,
            max_triggers=request.max_triggers,
            template_binding=request.template_binding,
            status=SubscriptionStatus.ACTIVE,
        )
        # 第五阶段偏差修正（G6/T225）：模板绑定校验与展开
        # 参数非法拒绝创建（TRIGGER_TEMPLATE_UNKNOWN/TRIGGER_TEMPLATE_PARAM_INVALID）；
        # 展开规则增广订阅 filter_rules（进入既有 _match_filter 前置粗筛路径），
        # 评估器按订阅维度挂载；无模板绑定时本段零开销跳过。
        if subscription.template_binding is not None:
            if self._template_registry is None:
                raise ValueError(
                    "订阅携带模板绑定但模板注册表未启用（trigger_template_enabled=False）"
                )
            expansion = self._template_registry.expand(subscription.template_binding)
            subscription.filter_rules.extend(expansion.expanded_rules)
            self._evaluators[subscription.subscription_id] = expansion.evaluator

        self._subscriptions[subscription.subscription_id] = subscription
        await self._start_listening(subscription)
        return subscription

    async def pause_subscription(self, subscription_id: str) -> RealtimeSubscription:
        """暂停订阅（停止监听但保留配置）。"""
        sub = self._subscriptions.get(subscription_id)
        if sub is None:
            raise ValueError(f"订阅不存在: {subscription_id}")
        sub.status = SubscriptionStatus.PAUSED
        sub.updated_at = _utc_now()
        await self._stop_listening(subscription_id)
        # 第五阶段偏差修正（G6/T225）：暂停时同步销毁评估器防泄漏
        self._evaluators.pop(subscription_id, None)
        return sub

    async def resume_subscription(self, subscription_id: str) -> RealtimeSubscription:
        """恢复已暂停的订阅。"""
        sub = self._subscriptions.get(subscription_id)
        if sub is None:
            raise ValueError(f"订阅不存在: {subscription_id}")
        if sub.status != SubscriptionStatus.PAUSED:
            raise ValueError(f"订阅非暂停状态: {sub.status}")
        sub.status = SubscriptionStatus.ACTIVE
        sub.updated_at = _utc_now()
        await self._start_listening(sub)
        return sub

    async def delete_subscription(self, subscription_id: str) -> None:
        """删除订阅（停止监听并移除配置）。"""
        await self._stop_listening(subscription_id)
        # 第五阶段偏差修正（G6/T225）：删除时同步销毁评估器防泄漏
        self._evaluators.pop(subscription_id, None)
        self._subscriptions.pop(subscription_id, None)

    def get_subscription(self, subscription_id: str) -> Optional[RealtimeSubscription]:
        """查询订阅。"""
        return self._subscriptions.get(subscription_id)

    def list_subscriptions(
        self,
        status_filter: Optional[str] = None,
    ) -> List[RealtimeSubscription]:
        """列出订阅（可按状态过滤）。"""
        subs = list(self._subscriptions.values())
        if status_filter:
            subs = [s for s in subs if s.status == status_filter]
        return subs

    def get_trigger_history(
        self,
        subscription_id: Optional[str] = None,
    ) -> List[SubscriptionTriggerEvent]:
        """获取触发历史。"""
        if subscription_id:
            return [e for e in self._trigger_history if e.subscription_id == subscription_id]
        return list(self._trigger_history)

    @staticmethod
    def _match_filter(event_data: Dict[str, Any], rules: List[EventFilterRule]) -> bool:
        """检查事件是否满足所有过滤规则（AND 语义）。"""
        for rule in rules:
            field_value = event_data.get(rule.field_name)
            if rule.operator == "eq" and field_value != rule.value:
                return False
            if rule.operator == "ne" and field_value == rule.value:
                return False
            if rule.operator == "gt":
                if not (isinstance(field_value, (int, float)) and field_value > rule.value):
                    return False
            if rule.operator == "lt":
                if not (isinstance(field_value, (int, float)) and field_value < rule.value):
                    return False
            if rule.operator == "contains":
                if not (isinstance(field_value, str) and str(rule.value) in field_value):
                    return False
        return True

    @staticmethod
    def _map_inputs(
        event_data: Dict[str, Any],
        input_mapping: Dict[str, str],
    ) -> Dict[str, Any]:
        """按映射规则从事件数据提取工作流输入参数。

        映射值支持点号分隔的嵌套路径，如 "user.profile.name"。
        """
        inputs: Dict[str, Any] = {}
        for param_name, field_path in input_mapping.items():
            value: Any = event_data
            for key in field_path.split("."):
                if isinstance(value, dict):
                    value = value.get(key)
                else:
                    value = None
                    break
            inputs[param_name] = value
        return inputs

    async def _start_listening(self, subscription: RealtimeSubscription) -> None:
        """启动异步监听任务。"""
        source_type_key = subscription.source_type.value
        listener = self._event_listeners.get(source_type_key)
        if listener is None:
            subscription.status = SubscriptionStatus.ERROR
            subscription.updated_at = _utc_now()
            return

        task = asyncio.create_task(self._listen_loop(subscription, listener))
        self._listen_tasks[subscription.subscription_id] = task

    async def _stop_listening(self, subscription_id: str) -> None:
        """停止异步监听任务。"""
        task = self._listen_tasks.pop(subscription_id, None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _listen_loop(
        self,
        subscription: RealtimeSubscription,
        listener: Callable,
    ) -> None:
        """持续监听数据源事件并触发工作流。"""
        try:
            async for event_data in listener(
                subscription.connection_reference,
                subscription.event_source,
            ):
                # 检查订阅状态
                if subscription.status != SubscriptionStatus.ACTIVE:
                    break
                # 检查最大触发次数
                if (
                    subscription.max_triggers is not None
                    and subscription.trigger_count >= subscription.max_triggers
                ):
                    subscription.status = SubscriptionStatus.STOPPED
                    subscription.updated_at = _utc_now()
                    break
                # 事件过滤（既有 _match_filter 路径零修改，含模板展开的粗筛规则）
                if not self._match_filter(event_data, subscription.filter_rules):
                    continue
                # 第五阶段偏差修正（G6/T225）：模板评估步骤
                # 有模板绑定时调用评估器 update_and_evaluate 判定命中后投递；
                # 无模板绑定时字典 miss 零开销直通，行为与修正前一致。
                evaluator = self._evaluators.get(subscription.subscription_id)
                if evaluator is not None and not evaluator.update_and_evaluate(event_data):
                    continue
                # 映射输入参数
                inputs = self._map_inputs(event_data, subscription.input_mapping)
                # 触发工作流
                try:
                    instance_id = await self._workflow_trigger(
                        subscription.workflow_definition_id,
                        subscription.workflow_definition_version,
                        inputs,
                    )
                except Exception:
                    instance_id = None
                # 记录触发历史
                trigger_event = SubscriptionTriggerEvent(
                    subscription_id=subscription.subscription_id,
                    event_data=event_data,
                    instance_id=instance_id,
                )
                self._trigger_history.append(trigger_event)
                # 更新订阅统计
                subscription.trigger_count += 1
                subscription.last_triggered_at = _utc_now()
                subscription.updated_at = _utc_now()
        except asyncio.CancelledError:
            pass
        except Exception:
            subscription.status = SubscriptionStatus.ERROR
            subscription.updated_at = _utc_now()

    async def shutdown(self) -> None:
        """关闭所有订阅监听（应用关闭时调用）。"""
        sub_ids = list(self._listen_tasks.keys())
        for sub_id in sub_ids:
            await self._stop_listening(sub_id)
