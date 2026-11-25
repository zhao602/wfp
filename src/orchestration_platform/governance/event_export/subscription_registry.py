# =============================================================================
# 事件订阅注册表（governance/event_export/subscription_registry.py）
#
# 职责：Webhook 订阅的注册表（roadmap B3；B7 W3 可选持久化）。
# 设计意图：
#   1. TriggerScheduleRegistry 同范式注册表（upsert by subscription_id）；
#   2. 内存态为唯一工作副本；persist_path 给定时任一变更（register/
#      delete/enable/disable）后原子落盘（tmp + os.replace），启动期
#      load_from_disk() 恢复（B7 W3 重启保留，guide §7 边界改写）；
#      未传保持纯内存（既有测试/默认行为零影响）；
#   3. 落盘失败仅告警不阻断变更（内存态为准，降级为 B3 既有
#      「重启丢订阅」边界）；恢复时文件缺失静默空表、JSON 损坏/
#      schema_version 不识别告警空表（不崩溃）；
#   4. 未注册/未找到抛 KeyError（路由层映射 404 EVENT_SUBSCRIPTION_NOT_FOUND）。
# =============================================================================

import json
import logging
import os
from typing import Dict, List, Optional

from orchestration_platform.governance.event_export.models import WebhookSubscription

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = 1


class EventSubscriptionRegistry:
    """Webhook 订阅注册表（upsert 语义，subscription_id 为主键）。

    persist_path（B7 W3）：给定时变更后原子落盘、load_from_disk()
    启动期恢复；None 保持纯内存（B3 既有行为，零行为差）。
    """

    def __init__(self, persist_path: Optional[str] = None) -> None:
        self._subscriptions: Dict[str, WebhookSubscription] = {}
        self._persist_path = persist_path

    def _persist(self) -> None:
        """变更后原子落盘（tmp + os.replace；失败告警不阻断，内存态为准）。"""
        if self._persist_path is None:
            return
        payload = {
            "schema_version": _SCHEMA_VERSION,
            "subscriptions": [
                subscription.model_dump(mode="json")
                for subscription in self._subscriptions.values()
            ],
        }
        try:
            directory = os.path.dirname(self._persist_path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            tmp_path = f"{self._persist_path}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as file_handle:
                json.dump(payload, file_handle, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self._persist_path)
        except Exception:
            logger.warning(
                "订阅持久化落盘失败（降级为内存态）: path=%s",
                self._persist_path,
                exc_info=True,
            )

    def load_from_disk(self) -> None:
        """启动期恢复持久化订阅（全部校验通过后 upsert 入内存表）。

        文件缺失 → 静默空表；JSON 损坏 / schema_version 不识别 /
        条目校验失败 → 告警 + 不恢复（内存表保持原状，不崩溃）。
        """
        if self._persist_path is None:
            return
        if not os.path.exists(self._persist_path):
            return
        try:
            with open(self._persist_path, "r", encoding="utf-8") as file_handle:
                payload = json.load(file_handle)
            if (
                not isinstance(payload, dict)
                or payload.get("schema_version") != _SCHEMA_VERSION
            ):
                raise ValueError(f"schema_version 不识别: {payload!r}")
            restored = [
                WebhookSubscription.model_validate(item)
                for item in payload.get("subscriptions", [])
            ]
        except Exception:
            logger.warning(
                "订阅持久化文件损坏，不恢复: path=%s",
                self._persist_path,
                exc_info=True,
            )
            return
        for subscription in restored:
            self._subscriptions[subscription.subscription_id] = subscription

    def register(self, subscription: WebhookSubscription) -> WebhookSubscription:
        """注册/覆盖订阅（upsert by subscription_id）。"""
        self._subscriptions[subscription.subscription_id] = subscription
        self._persist()
        return subscription

    def get(self, subscription_id: str) -> WebhookSubscription:
        """取订阅；未注册抛 KeyError。"""
        subscription = self._subscriptions.get(subscription_id)
        if subscription is None:
            raise KeyError(subscription_id)
        return subscription

    def list(self, tenant_id: Optional[str] = None) -> List[WebhookSubscription]:
        """列出订阅（tenant_id 给定时按租户过滤）。"""
        subscriptions = list(self._subscriptions.values())
        if tenant_id is None:
            return subscriptions
        return [
            subscription
            for subscription in subscriptions
            if subscription.tenant_id == tenant_id
        ]

    def delete(self, subscription_id: str) -> WebhookSubscription:
        """删除订阅；未注册抛 KeyError。"""
        subscription = self._subscriptions.pop(subscription_id, None)
        if subscription is None:
            raise KeyError(subscription_id)
        self._persist()
        return subscription

    def enable(self, subscription_id: str) -> WebhookSubscription:
        """启用订阅；未注册抛 KeyError。"""
        subscription = self.get(subscription_id)
        self._subscriptions[subscription_id] = subscription.model_copy(
            update={"enabled": True}
        )
        self._persist()
        return self._subscriptions[subscription_id]

    def disable(self, subscription_id: str) -> WebhookSubscription:
        """停用订阅（软停用，不删除注册）；未注册抛 KeyError。"""
        subscription = self.get(subscription_id)
        self._subscriptions[subscription_id] = subscription.model_copy(
            update={"enabled": False}
        )
        self._persist()
        return self._subscriptions[subscription_id]

    def rotate_secret(self, subscription_id: str, new_secret: str) -> WebhookSubscription:
        """轮换订阅密钥（B16 F5，design D6）。

        服务端生成新密钥经调用方传入：secret 原子替换、secret_version+1、
        rotated_at=now（新值即时生效——无双签名宽限，用户裁决 2026-11-25）；
        未注册抛 KeyError（路由层映射 404）。
        """
        from datetime import datetime, timezone

        subscription = self.get(subscription_id)
        rotated = subscription.model_copy(
            update={
                "secret": new_secret,
                "secret_version": subscription.secret_version + 1,
                "rotated_at": datetime.now(timezone.utc),
            }
        )
        self._subscriptions[subscription_id] = rotated
        self._persist()
        return rotated
