# =============================================================================
# 违规事件发布器（integrations/sandbox/violation_publisher.py）
#
# 职责：违规记录三路落点——结果模型携带、事件总线发布、审计留档。
# 设计意图（对应偏差修正 spec 4.3.1 / design 4.1）：
#   - 事件发布失败不阻断沙箱执行（最终一致）
#   - 违规记录持久化至 ./.data/sandbox/violations/
#   - 审计与事件内容不含工具入参敏感明文
# =============================================================================

import json
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.integrations.sandbox.models import (
    SandboxExecutionResult,
    SandboxViolationRecord,
)

_logger = logging.getLogger(__name__)


class ViolationPublisher:
    """违规事件发布器。

    违规记录三路落点：结果模型携带、事件总线发布、审计留档。
    事件发布失败不阻断沙箱执行。
    """

    def __init__(
        self,
        event_bus: Optional[Any] = None,
        audit_logger: Optional[Any] = None,
        storage_root: str = "./.data/sandbox/violations",
    ) -> None:
        """初始化违规发布器。

        参数：
            event_bus：事件总线（IEventBus 实例，None 则跳过事件发布）
            audit_logger：审计日志器（None 则跳过审计留档）
            storage_root：违规记录持久化根目录
        """
        self._event_bus = event_bus
        self._audit_logger = audit_logger
        self._storage_root = Path(storage_root)
        self._storage_root.mkdir(parents=True, exist_ok=True)

    async def publish_violations(
        self,
        result: SandboxExecutionResult,
        session_id: str,
        tenant_id: str,
        policy_id: str,
    ) -> None:
        """发布违规记录。

        三路落点：持久化文件、事件总线、审计日志。
        事件发布失败不阻断执行。

        参数：
            result：沙箱执行结果（含违规记录）
            session_id：会话标识
            tenant_id：租户标识
            policy_id：策略标识
        """
        if not result.violations:
            return

        for violation in result.violations:
            # 1. 持久化违规记录
            self._persist_violation(violation, session_id, tenant_id, policy_id)

            # 2. 事件总线发布（失败不阻断）
            await self._publish_event(violation, session_id, tenant_id, policy_id)

            # 3. 审计留档（失败不阻断）
            await self._record_audit(violation, session_id, tenant_id, policy_id)

    def _persist_violation(
        self,
        violation: SandboxViolationRecord,
        session_id: str,
        tenant_id: str,
        policy_id: str,
    ) -> None:
        """持久化违规记录至本地文件。"""
        tenant_dir = self._storage_root / tenant_id
        tenant_dir.mkdir(parents=True, exist_ok=True)

        record_id = f"viol_{uuid.uuid4().hex[:12]}"
        file_path = tenant_dir / f"{record_id}.json"

        record_data = {
            "record_id": record_id,
            "session_id": session_id,
            "tenant_id": tenant_id,
            "policy_id": policy_id,
            "violation_type": violation.violation_type.value,
            "detail": violation.detail,
            "target": violation.target,
            "occurred_at": violation.occurred_at.isoformat(),
        }

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(record_data, f, ensure_ascii=False, indent=2)
        except OSError as exc:
            _logger.warning("违规记录持久化失败: %s - %s", file_path, exc)

    async def _publish_event(
        self,
        violation: SandboxViolationRecord,
        session_id: str,
        tenant_id: str,
        policy_id: str,
    ) -> None:
        """经事件总线发布违规事件。

        事件内容不含工具入参敏感明文（仅含违规类型/会话标识/策略标识摘要）。
        """
        if self._event_bus is None:
            return

        try:
            from orchestration_platform.domain.models.enums import MessageEventType
            from orchestration_platform.governance.event_bus.models import EventMessage

            settings = get_settings()
            event = EventMessage(
                event_id=f"evt_{uuid.uuid4().hex[:12]}",
                event_type=MessageEventType.WORKFLOW_EXECUTION,
                sequence_number=0,
                payload={
                    "violation_type": violation.violation_type.value,
                    "session_id": session_id,
                    "tenant_id": tenant_id,
                    "policy_id": policy_id,
                    "detail": violation.detail,
                },
                instance_id=session_id,
            )
            await self._event_bus.publish(settings.sandbox_violation_topic, event)
        except Exception as exc:
            _logger.warning("违规事件发布失败（不阻断执行）: %s", exc)

    async def _record_audit(
        self,
        violation: SandboxViolationRecord,
        session_id: str,
        tenant_id: str,
        policy_id: str,
    ) -> None:
        """经审计日志器记录违规。

        审计内容不含工具入参敏感明文。
        """
        if self._audit_logger is None:
            return

        try:
            # 审计记录仅含违规摘要，不含工具入参
            await self._audit_logger.record(
                tenant_id=tenant_id,
                actor_id="sandbox_system",
                action="sandbox_violation",
                resource_type="sandbox_session",
                resource_id=session_id,
                result="violation_detected",
                detail={
                    "violation_type": violation.violation_type.value,
                    "policy_id": policy_id,
                },
            )
        except Exception as exc:
            _logger.warning("违规审计留档失败（不阻断执行）: %s", exc)

    def query_violations(
        self,
        tenant_id: str,
        violation_type: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """查询违规记录。

        参数：
            tenant_id：租户标识
            violation_type：违规类型筛选（None 则全部）
            limit：最大返回条数

        返回：
            违规记录列表
        """
        tenant_dir = self._storage_root / tenant_id
        if not tenant_dir.exists():
            return []

        records = []
        for file_path in sorted(tenant_dir.glob("*.json"), reverse=True):
            if len(records) >= limit:
                break
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    record = json.load(f)
                if violation_type is None or record.get("violation_type") == violation_type:
                    records.append(record)
            except (OSError, json.JSONDecodeError):
                continue

        return records
