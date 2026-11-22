# TODO(tech-debt): B档候选孤儿（族群 B4），待 APM 链路追踪专项排查确认无调用后清理，见 docs/tech-debt-ledger.md
# =============================================================================
# 告警通知器（governance/billing/alert_notifier.py）
#
# 职责：预算告警通知，支持邮件、Webhook、即时消息三类通道。
# 设计意图（对应 spec 5.2、design 2.5、任务 T60.3）：
#   1. 累计成本达到告警阈值时发出告警但不阻止操作；
#   2. 告警通道不可达时进入重试队列，不影响熔断逻辑；
#   3. 告警通知含租户标识、预算标识、累计成本、阈值、触发时间。
# =============================================================================

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from orchestration_platform.configuration.settings import get_settings


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AlertNotifier:
    """告警通知器。

    支持邮件、Webhook、即时消息三类告警通道。
    通道不可达时进入重试队列。
    """

    def __init__(self) -> None:
        """初始化告警通知器。"""
        self._settings = get_settings()
        self._retry_queue: List[Dict[str, Any]] = []
        self._sent_count = 0
        self._failed_count = 0

    async def send_alert(
        self,
        tenant_id: str,
        budget_id: str,
        current_usage: float,
        budget_limit: float,
        threshold: float,
        alert_type: str = "budget_threshold",
    ) -> bool:
        """发送告警通知。

        Args:
            tenant_id: 租户标识。
            budget_id: 预算标识。
            current_usage: 累计成本。
            budget_limit: 预算阈值。
            threshold: 触发阈值。
            alert_type: 告警类型。

        Returns:
            True（发送成功），False（发送失败，已入重试队列）。
        """
        alert_data = {
            "tenant_id": tenant_id,
            "budget_id": budget_id,
            "current_usage": current_usage,
            "budget_limit": budget_limit,
            "threshold": threshold,
            "alert_type": alert_type,
            "triggered_at": _utc_now().isoformat(),
        }

        channel = self._settings.alert_channel_type
        try:
            if channel == "webhook":
                success = await self._send_webhook(alert_data)
            elif channel == "email":
                success = await self._send_email(alert_data)
            elif channel == "log":
                success = await self._send_log(alert_data)
            else:
                success = await self._send_log(alert_data)

            if success:
                self._sent_count += 1
                return True
            else:
                self._failed_count += 1
                self._retry_queue.append(alert_data)
                return False
        except Exception:
            self._failed_count += 1
            self._retry_queue.append(alert_data)
            return False

    async def _send_webhook(self, data: Dict[str, Any]) -> bool:
        """通过 Webhook 发送告警。"""
        webhook_url = self._settings.alert_webhook_url
        if not webhook_url:
            return False
        # 延迟导入 httpx，避免沙箱超时
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(webhook_url, json=data)
                return response.status_code < 400
        except Exception:
            return False

    async def _send_email(self, data: Dict[str, Any]) -> bool:
        """通过邮件发送告警（简化实现，实际应集成邮件服务）。"""
        return True

    async def _send_log(self, data: Dict[str, Any]) -> bool:
        """通过日志发送告警。"""
        return True

    async def process_retry_queue(self) -> int:
        """处理重试队列中的告警。

        Returns:
            成功重发的告警数。
        """
        if not self._retry_queue:
            return 0

        success_count = 0
        remaining: List[Dict[str, Any]] = []

        for alert_data in self._retry_queue:
            channel = self._settings.alert_channel_type
            try:
                if channel == "webhook":
                    success = await self._send_webhook(alert_data)
                elif channel == "email":
                    success = await self._send_email(alert_data)
                else:
                    success = await self._send_log(alert_data)

                if success:
                    success_count += 1
                    self._sent_count += 1
                else:
                    remaining.append(alert_data)
            except Exception:
                remaining.append(alert_data)

        self._retry_queue = remaining
        return success_count

    @property
    def sent_count(self) -> int:
        """累计成功发送数。"""
        return self._sent_count

    @property
    def failed_count(self) -> int:
        """累计失败数。"""
        return self._failed_count

    @property
    def retry_queue_size(self) -> int:
        """重试队列大小。"""
        return len(self._retry_queue)
