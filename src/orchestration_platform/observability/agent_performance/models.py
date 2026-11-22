# =============================================================================
# Agent 绩效领域模型（observability/agent_performance/models.py）
#
# 职责：定义 Agent 绩效明细记录与聚合结果模型（G3/T227）。
# 设计意图（对应 spec 5.3.1、design D5）：
#   1. 明细六字段：agent_id/succeeded/duration_ms/token_count/quality_score/occurred_at；
#   2. 聚合四维指标恒在：success_rate/avg_duration_ms/total_tokens/avg_quality_score，
#      无数据以零值表达不缺失字段（数据约束 6.3.2）；
#   3. 时间字段 UTC，与平台既有时间约定一致。
# =============================================================================

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


def _utc_now() -> datetime:
    """返回当前 UTC 时间。"""
    return datetime.now(timezone.utc)


class AgentPerformanceRecord(BaseModel):
    """Agent 绩效明细记录：单次 Agent 执行的四维度量快照。

    字段：
        agent_id      —— Agent 标识
        succeeded     —— 本次执行是否成功
        duration_ms   —— 执行耗时（毫秒）
        token_count   —— Token 消耗数
        quality_score —— 质量评分（评估框架产出优先，无评分记 0）
        occurred_at   —— 发生时间（UTC）
    """

    agent_id: str = Field(description="Agent 标识")
    succeeded: bool = Field(description="本次执行是否成功")
    duration_ms: int = Field(default=0, ge=0, description="执行耗时（毫秒）")
    token_count: int = Field(default=0, ge=0, description="Token 消耗数")
    quality_score: float = Field(default=0.0, ge=0.0, description="质量评分（无评分记 0）")
    occurred_at: datetime = Field(default_factory=_utc_now, description="发生时间（UTC）")


class AgentPerformanceAggregate(BaseModel):
    """Agent 绩效聚合结果：指定时间范围内的四维聚合。

    四项指标字段恒在（无数据以零值表达，不缺失字段，数据约束 6.3.2）：
        success_rate       —— 成功率 = 成功次数/总次数（无数据 0.0）
        avg_duration_ms    —— 平均耗时 = Σduration/次数（毫秒）
        total_tokens       —— Token 总量 = Σtoken
        avg_quality_score  —— 质量评分均值（无数据 0.0）
    """

    agent_id: str = Field(description="Agent 标识")
    start: datetime = Field(description="统计起始时间（UTC，含）")
    end: datetime = Field(description="统计结束时间（UTC，含）")
    success_rate: float = Field(default=0.0, ge=0.0, le=1.0, description="成功率（无数据 0.0）")
    avg_duration_ms: float = Field(default=0.0, ge=0.0, description="平均耗时（毫秒）")
    total_tokens: int = Field(default=0, ge=0, description="Token 总量")
    avg_quality_score: float = Field(default=0.0, ge=0.0, description="质量评分均值（无数据 0.0）")
    sample_count: int = Field(default=0, ge=0, description="参与聚合的明细样本数")
    data_source: str = Field(default="in_memory_degraded", description="数据来源标识（postgres/in_memory_degraded）")
