# =============================================================================
# 实时数据订阅领域模型（engine/realtime_subscription/models.py）
#
# 职责：定义数据流订阅、事件触发规则与订阅状态的领域模型。
# 设计意图（对应项目构建计划书「实时数据订阅触发」要求）：
#   1. Agent 订阅数据流（Kafka topic / 数据变更事件），事件到达时自动触发工作流；
#   2. 订阅可管理：创建 / 暂停 / 恢复 / 删除；
#   3. 事件过滤：支持按条件过滤事件，仅满足条件的事件触发工作流。
# =============================================================================

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from orchestration_platform.domain.models.enums import DataSourceType


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SubscriptionStatus:
    """订阅状态常量。"""

    ACTIVE = "active"
    PAUSED = "paused"
    STOPPED = "stopped"
    ERROR = "error"


class EventFilterRule(BaseModel):
    """事件过滤规则：仅满足条件的事件触发工作流。

    简化实现：按字段值匹配。expression 为空时不过滤（所有事件均触发）。
    """

    field_name: str = Field(description="事件数据中的字段名")
    operator: str = Field(default="eq", description="比较运算符: eq/ne/gt/lt/contains")
    value: Any = Field(description="比较目标值")


class TriggerTemplateBinding(BaseModel):
    """触发器模板绑定（第五阶段偏差修正 T223）。

    作为订阅的可选附加字段（0..1）：模板在订阅创建时展开为
    既有 EventFilterRule 组合 + 有状态评估器，实现三类异常检测语义。
    旧配置（无模板绑定）反序列化与创建行为不变。
    """

    template_type: str = Field(description="模板类型标识（TriggerTemplateType 取值）")
    params: Dict[str, Any] = Field(
        default_factory=dict, description="模板参数（按模板类型校验）"
    )


class RealtimeSubscription(BaseModel):
    """实时数据订阅配置。

    一个订阅绑定：数据源 + 事件过滤 + 目标工作流 + 输入映射。
    当数据源产生满足过滤条件的事件时，自动以映射后的输入启动目标工作流实例。
    """

    subscription_id: str = Field(description="订阅唯一标识")
    workflow_definition_id: str = Field(description="目标工作流定义 ID")
    workflow_definition_version: int = Field(default=1, description="目标工作流版本")
    source_type: DataSourceType = Field(description="数据源类型（Kafka/数据库变更流等）")
    connection_reference: str = Field(description="数据源连接引用")
    event_source: str = Field(description="事件源标识（如 Kafka topic 名）")
    filter_rules: list[EventFilterRule] = Field(
        default_factory=list, description="事件过滤规则列表（AND 语义）"
    )
    input_mapping: Dict[str, str] = Field(
        default_factory=dict,
        description="工作流输入参数映射：参数名 -> 事件数据字段路径（点号分隔）",
    )
    status: str = Field(default=SubscriptionStatus.STOPPED, description="订阅状态")
    created_at: datetime = Field(default_factory=_utc_now, description="创建时间")
    updated_at: datetime = Field(default_factory=_utc_now, description="更新时间")
    last_triggered_at: Optional[datetime] = Field(default=None, description="最近触发时间")
    trigger_count: int = Field(default=0, ge=0, description="累计触发次数")
    max_triggers: Optional[int] = Field(
        default=None, ge=1, description="最大触发次数（None 表示无限制）"
    )
    # 第五阶段偏差修正（G6/T223）：触发器模板绑定（可选，0..1）
    template_binding: Optional[TriggerTemplateBinding] = Field(
        default=None, description="触发器模板绑定（无模板时为 None，行为与既有订阅一致）"
    )


class SubscriptionCreateRequest(BaseModel):
    """创建订阅请求。"""

    workflow_definition_id: str = Field(description="目标工作流定义 ID")
    workflow_definition_version: int = Field(default=1, description="目标工作流版本")
    source_type: DataSourceType = Field(description="数据源类型")
    connection_reference: str = Field(description="数据源连接引用")
    event_source: str = Field(description="事件源标识")
    filter_rules: list[EventFilterRule] = Field(default_factory=list)
    input_mapping: Dict[str, str] = Field(default_factory=dict)
    max_triggers: Optional[int] = Field(default=None, ge=1)
    # 第五阶段偏差修正（G6/T223）：触发器模板绑定（可选，创建时经注册表校验与展开）
    template_binding: Optional[TriggerTemplateBinding] = Field(
        default=None, description="触发器模板绑定（可选）"
    )


class SubscriptionTriggerEvent(BaseModel):
    """订阅触发事件记录。"""

    subscription_id: str = Field(description="订阅 ID")
    event_data: Dict[str, Any] = Field(description="触发事件数据")
    triggered_at: datetime = Field(default_factory=_utc_now, description="触发时间")
    instance_id: Optional[str] = Field(default=None, description="启动的工作流实例 ID")
