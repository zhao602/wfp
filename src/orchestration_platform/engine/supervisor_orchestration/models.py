# =============================================================================
# 监督者编排领域模型（engine/supervisor_orchestration/models.py）
#
# 职责：定义监督者编排器的领域模型（G1/T215）。
# 设计意图（对应 spec 5.1、design D1/D2）：
#   1. 顶层 Supervisor 在业务逻辑级运行，子编排器管理领域专家 Agent 集群；
#   2. 编排状态检查点化，可中断可恢复；
#   3. 发现结果逐级聚合，防止平方级噪声增长。
# =============================================================================

import hashlib
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


def _utc_now() -> datetime:
    """返回当前 UTC 时间。"""
    return datetime.now(timezone.utc)


class OrchestrationStatus(str, Enum):
    """编排状态五态（G1/T215）。

    RUNNING      —— 编排进行中
    AWAITING_SUB —— 等待子编排返回
    COMPLETED    —— 编排完成
    FAILED       —— 编排失败（子任务失败且重试耗尽）
    TERMINATED   —— 明确终止（状态恢复失败或外部终止）
    """

    RUNNING = "running"
    AWAITING_SUB = "awaiting_sub"
    COMPLETED = "completed"
    FAILED = "failed"
    TERMINATED = "terminated"


class FindingItem(BaseModel):
    """单条发现项：子编排产出的浓缩结果。

    属性：
        content_summary  —— 内容摘要（浓缩结果，非全量透传）
        content_hash     —— 内容哈希（用于去重）
        source_sub_orchestrator_id —— 来源子编排器标识（禁止匿名汇聚）
    """

    content_summary: str = Field(description="内容摘要（浓缩结果）")
    content_hash: str = Field(description="内容哈希（去重依据）")
    source_sub_orchestrator_id: str = Field(description="来源子编排器标识")

    @classmethod
    def from_summary(cls, summary: str, source: str) -> "FindingItem":
        """从摘要构造发现项（自动计算哈希）。"""
        return cls(
            content_summary=summary,
            content_hash=hashlib.sha256(summary.encode("utf-8")).hexdigest(),
            source_sub_orchestrator_id=source,
        )


class AggregatedFinding(BaseModel):
    """聚合发现：子编排器向 Supervisor 上报的聚合结果。

    携带来源 sub_orchestration_id，禁止匿名汇聚（A1.3.a）。
    """

    sub_orchestration_id: str = Field(description="子编排实例标识")
    sub_orchestrator_id: str = Field(description="子编排器标识")
    items: List[FindingItem] = Field(
        default_factory=list, description="发现项列表（已限量去重）"
    )
    success: bool = Field(default=True, description="子编排是否成功")
    failure_reason: Optional[str] = Field(default=None, description="失败原因")


class SubOrchestrationRef(BaseModel):
    """子编排引用：Supervisor 分派的子任务跟踪记录。"""

    sub_orchestration_id: str = Field(description="子编排实例标识")
    sub_orchestrator_id: str = Field(description="子编排器标识")
    task_description: str = Field(description="任务描述")
    status: OrchestrationStatus = Field(
        default=OrchestrationStatus.RUNNING, description="子编排状态"
    )
    failure_reason: Optional[str] = Field(default=None, description="失败原因")
    retry_count: int = Field(default=0, ge=0, description="已重试次数")


class RouteDecision(BaseModel):
    """路由决策：确定性路由的输出。

    属性：
        sub_orchestrator_id —— 目标子编排器标识
        matched_keywords    —— 匹配的关键词列表
        decision_index      —— 决策表中的行索引（可追溯）；-1 表示 default 兜底路由（未命中任何声明行）
    """

    sub_orchestrator_id: str = Field(description="目标子编排器标识")
    matched_keywords: List[str] = Field(
        default_factory=list, description="匹配的关键词"
    )
    decision_index: int = Field(
        ge=-1, description="决策表行索引；-1 表示 default 兜底路由"
    )


class SupervisorOrchestrationContext(BaseModel):
    """监督者编排上下文：单次编排的全局状态。

    属性：
        orchestration_id       —— 全局唯一编排标识（sup_<uuid12>）
        status                 —— 编排状态
        sub_orchestration_refs —— 子编排引用列表
        aggregated_findings    —— 已聚合的发现列表
        route_decision_index   —— 路由决策序号（可追溯）
        created_at / updated_at —— 时间戳
    """

    orchestration_id: str = Field(description="编排全局唯一标识")
    status: OrchestrationStatus = Field(
        default=OrchestrationStatus.RUNNING, description="编排状态"
    )
    sub_orchestration_refs: List[SubOrchestrationRef] = Field(
        default_factory=list, description="子编排引用列表"
    )
    aggregated_findings: List[AggregatedFinding] = Field(
        default_factory=list, description="已聚合发现列表"
    )
    route_decision_index: int = Field(default=0, ge=0, description="路由决策序号")
    created_at: datetime = Field(default_factory=_utc_now, description="创建时间")
    updated_at: datetime = Field(default_factory=_utc_now, description="更新时间")

    def touch(self) -> None:
        """更新 updated_at 时间戳。"""
        self.updated_at = _utc_now()


class SupervisorOrchestrationResult(BaseModel):
    """监督者编排结果。

    属性：
        orchestration_id —— 编排标识
        success          —— 整体是否成功
        findings         —— 最终聚合发现列表
        failed_sub_tasks —— 失败子任务列表（含失败原因）
        status           —— 最终状态
    """

    orchestration_id: str = Field(description="编排标识")
    success: bool = Field(description="整体是否成功")
    findings: List[AggregatedFinding] = Field(
        default_factory=list, description="最终聚合发现"
    )
    failed_sub_tasks: List[SubOrchestrationRef] = Field(
        default_factory=list, description="失败子任务列表"
    )
    status: OrchestrationStatus = Field(description="最终状态")
