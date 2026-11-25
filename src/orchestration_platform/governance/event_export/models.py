# =============================================================================
# 事件出口领域模型（governance/event_export/models.py）
#
# 职责：订阅/投递记录/死信条目的线级契约模型（roadmap B3）。
# 设计意图：
#   1. dict 序列化即线级契约（契约倒置：B2 SDK 1:1 包装，guide 为准）；
#   2. event_types 匹配语义集中一处：空列表=全部；以 "." 结尾=前缀通配
#      （如 "node."）；否则精确匹配；
#   3. last_error 经 summarize_text 截断（GK-05：异常全文不涌入契约字段）。
# =============================================================================

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from orchestration_platform.domain.models.domain_event import summarize_text


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class WebhookSubscription(BaseModel):
    """Webhook 订阅（注册表条目；subscription_id 由调用方提供，upsert 语义）。

    版本钉住（B16 F5，design D5）：definition_id/definition_version 可选
    钉住——皆空匹配全部（向后兼容）；definition_id 非空时要求事件载荷
    definition_id 相等，definition_version 再要求载荷版本相等；
    definition_version 无 definition_id 即校验拒绝（钉版本必先钉定义）。
    密钥轮换（design D6）：secret_version 自 1 起轮换递增、rotated_at
    记最近轮换时刻（未轮换 None）。
    """

    subscription_id: str = Field(description="订阅标识（调用方提供，upsert 主键）")
    tenant_id: str = Field(description="租户标识（与事件 tenant_id 相等才投递）")
    url: str = Field(description="Webhook 回调地址（仅 http/https）")
    secret: str = Field(description="HMAC-SHA256 签名密钥")
    event_types: List[str] = Field(
        default_factory=list,
        description='事件类型过滤：空=全部；以 "." 结尾=前缀通配（如 "node."）；否则精确匹配',
    )
    definition_id: Optional[str] = Field(
        default=None,
        description="定义钉住（None=匹配全部；非空要求事件载荷 definition_id 相等）",
    )
    definition_version: Optional[int] = Field(
        default=None,
        ge=1,
        description="版本钉住（definition_id 非空时生效；载荷版本相等才投递）",
    )
    enabled: bool = Field(default=True, description="是否启用（停用即不投递）")
    description: str = Field(default="", description="订阅描述")
    secret_version: int = Field(
        default=1, ge=1, description="密钥版本（轮换递增，D6）"
    )
    created_at: datetime = Field(default_factory=_utc_now, description="创建时间")
    rotated_at: Optional[datetime] = Field(
        default=None, description="最近密钥轮换时间（未轮换 None）"
    )

    @field_validator("subscription_id", "tenant_id", "secret")
    @classmethod
    def _validate_non_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("标识/密钥字段不能为空")
        return value.strip()

    @field_validator("definition_id")
    @classmethod
    def _normalize_definition_id(cls, value: Optional[str]) -> Optional[str]:
        """空串归一为 None（未钉住）；非空去空白。"""
        if value is None or not value.strip():
            return None
        return value.strip()

    @model_validator(mode="after")
    def _validate_version_requires_definition(self) -> "WebhookSubscription":
        """钉版本必先钉定义（design D5）；独立防线（路由层 422 映射）。"""
        if self.definition_version is not None and self.definition_id is None:
            raise ValueError("definition_version 需与 definition_id 同时提供")
        return self

    @field_validator("url")
    @classmethod
    def _validate_url_scheme(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError(f"url 仅支持 http/https: {value}")
        return value

    @field_validator("event_types")
    @classmethod
    def _validate_event_types(cls, value: List[str]) -> List[str]:
        for pattern in value:
            if not pattern or not pattern.strip():
                raise ValueError(f"event_types 条目不能为空: {value}")
        return value


def event_type_matches(patterns: List[str], event_type: str) -> bool:
    """事件类型过滤匹配：空 patterns=全部；以 "." 结尾=前缀通配；否则精确。"""
    if not patterns:
        return True
    for pattern in patterns:
        if pattern.endswith("."):
            if event_type.startswith(pattern):
                return True
        elif event_type == pattern:
            return True
    return False


class DeliveryRecord(BaseModel):
    """单事件投递记录（一个投递周期一条：退避重试至终态后登记）。"""

    subscription_id: str = Field(description="订阅标识")
    event_id: str = Field(description="事件信封标识（EventMessage.event_id）")
    event_type: str = Field(description="领域事件类型")
    instance_id: str = Field(description="工作流实例标识")
    attempts: int = Field(ge=1, description="本周期投递尝试次数")
    last_status: Literal["delivered", "failed"] = Field(description="投递终态")
    delivered_at: Optional[datetime] = Field(
        default=None, description="投递成功时间（失败为 None）"
    )
    last_error: str = Field(default="", description="最后一次失败原因（截断）")

    @field_validator("last_error")
    @classmethod
    def _truncate_error(cls, value: str) -> str:
        return summarize_text(value)


class DeadLetterEntry(BaseModel):
    """死信条目（重试耗尽的事件快照；redrive 成功后移出）。"""

    entry_id: str = Field(description="死信条目标识")
    subscription_id: str = Field(description="订阅标识")
    event_id: str = Field(description="事件信封标识（EventMessage.event_id）")
    event_type: str = Field(description="领域事件类型")
    instance_id: str = Field(description="工作流实例标识")
    attempts: int = Field(ge=1, description="累计投递尝试次数")
    failed_at: datetime = Field(default_factory=_utc_now, description="入死信时间")
    last_error: str = Field(default="", description="最后一次失败原因（截断）")
    payload_snapshot: Dict[str, Any] = Field(
        description="领域事件快照（DomainEvent model_dump(mode=json)；redrive 重投依据）"
    )

    @field_validator("last_error")
    @classmethod
    def _truncate_error(cls, value: str) -> str:
        return summarize_text(value)
