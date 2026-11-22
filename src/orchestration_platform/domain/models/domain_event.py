# =============================================================================
# 领域事件模型（domain/models/domain_event.py）
#
# 职责：定义跨治理/引擎边界的统一领域事件契约（CHG-41 / DC-22）。
# 设计意图：
#   1. payload 限定 JSON 安全类型（JsonValue），保证事件可经事件总线
#      （Kafka/内存）序列化传输且可直接落盘；
#   2. 事件类型采用 "instance./node./governance." 三前缀命名
#      （design 附录 A.1/A.2），未登记前缀在构造期拒绝；
#   3. occurred_at 统一 UTC 带时区，避免跨时区歧义；
#   4. summarize_text 提供统一文本摘要能力，供错误消息等字段截断使用，
#      防止异常堆栈全文涌入事件载荷（GK-05）。
# =============================================================================

from datetime import datetime, timezone
from typing import Dict, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from orchestration_platform.domain.models.common import JsonValue

# 默认租户标识：无租户上下文的事件（如引擎内部节点事件）统一归入默认租户。
# B7 W2b（2026-11-22）：值由 "default" 统一为 "tenant_default"——与 RLS 谓词/
# backfill/权限种子口径正向收敛（全仓唯一权威定义，degraded_mode/bootstrap
# 均引用本常量；计量域兜底 log_tenant_default 为独立口径，见
# docs/metering-quota-guide.md）。
DEFAULT_TENANT_ID = "tenant_default"

# 文本摘要默认最大字符数。
DEFAULT_SUMMARY_MAX_CHARS = 500

# 合法的事件类型前缀（design 附录 A.1/A.2）。
_VALID_EVENT_TYPE_PREFIXES = ("instance.", "node.", "governance.")


def _utc_now() -> datetime:
    """返回当前 UTC 时间（带时区）。"""
    return datetime.now(timezone.utc)


def summarize_text(value: Optional[str], max_chars: int = DEFAULT_SUMMARY_MAX_CHARS) -> str:
    """将文本摘要为不超过 max_chars 的字符串（DC-22）。

    None 或空白返回空字符串；超长文本截断并追加截断标记，
    防止异常全文涌入事件载荷。
    """
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "...[truncated]"


class DomainEvent(BaseModel):
    """领域事件：治理与引擎间传递的统一事件模型（DC-22，附录 A 载荷契约）。"""

    event_type: str = Field(description="事件类型，须以 instance./node./governance. 之一开头")
    instance_id: str = Field(description="工作流实例标识")
    tenant_id: str = Field(description="租户标识")
    trace_id: str = Field(description="链路追踪标识")
    node_id: Optional[str] = Field(default=None, description="节点标识（node.* 事件必填）")
    occurred_at: datetime = Field(default_factory=_utc_now, description="事件发生时间（UTC）")
    payload: Dict[str, JsonValue] = Field(default_factory=dict, description="事件载荷（JSON 安全类型）")

    @field_validator("event_type")
    @classmethod
    def _validate_event_type(cls, value: str) -> str:
        """校验事件类型前缀，防止未登记的事件类型流入总线。"""
        if not value.startswith(_VALID_EVENT_TYPE_PREFIXES):
            raise ValueError(f"event_type 必须以 instance./node./governance. 之一开头: {value}")
        return value

    @field_validator("instance_id", "tenant_id", "trace_id")
    @classmethod
    def _validate_non_empty(cls, value: str) -> str:
        """校验标识字段非空。"""
        if not value or not value.strip():
            raise ValueError("标识字段不能为空")
        return value.strip()

    @model_validator(mode="after")
    def _validate_node_event(self) -> "DomainEvent":
        """node.* 事件必须携带 node_id（design 附录 A.2）。"""
        if self.event_type.startswith("node.") and not self.node_id:
            raise ValueError(f"node.* 事件必须携带 node_id: {self.event_type}")
        return self
