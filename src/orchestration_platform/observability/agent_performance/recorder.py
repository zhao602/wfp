# =============================================================================
# Agent 绩效事件旁路记录器（observability/agent_performance/recorder.py）
#
# 职责：以事件订阅旁路方式提取 Agent 绩效明细并写入仓储（G3/T227）。
# 设计意图（对应 spec 5.3.1 规则 4、design D5）：
#   1. 订阅式旁路喂数——订阅既有执行事件通道（WORKFLOW_EXECUTION，
#      含 NODE_SUCCEEDED/NODE_FAILED、Token 计量、质量评分）旁路读取，
#      禁止侵入既有 MetricsCollector/GovernanceMetrics 的 record_* 方法（A3.4）；
#   2. 质量评分口径：评估框架产出优先，无评分记录记 0；
#   3. 记录器挂载由开关控制（agent_performance_enabled），默认 False 时不订阅不记录；
#   4. 缺 agent_id 的事件跳过（非 Agent 执行），不产生脏数据。
# =============================================================================

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from orchestration_platform.observability.agent_performance.models import (
    AgentPerformanceRecord,
)

logger = logging.getLogger(__name__)

# 成功状态取值（与既有 publish_execution_event 的 status 约定一致）
_SUCCESS_STATUSES = {"succeeded", "success", "node_succeeded"}


class AgentPerformanceRecorder:
    """Agent 绩效旁路记录器：订阅事件通道提取明细，追加写入仓储。

    构造注入：
        repository —— 绩效仓储（IPerformanceRepository 实现）

    用法：
        1. 旁路事件订阅：调用 start() 消费事件总线迭代器（开关开启时）；
        2. 直接喂数（测试/手动旁路）：调用 ingest_event()。
    """

    def __init__(self, repository: "object") -> None:
        self._repository = repository
        self._started = False

    @property
    def repository(self) -> "object":
        return self._repository

    @property
    def is_started(self) -> bool:
        return self._started

    async def ingest_event(
        self,
        payload: Dict[str, Any],
        occurred_at: Optional[datetime] = None,
    ) -> Optional[AgentPerformanceRecord]:
        """从事件载荷提取 Agent 绩效明细并写入仓储。

        Args:
            payload     —— 事件载荷（含 status/agent_id/duration_ms/token_count/quality_score）
            occurred_at —— 事件发生时间（缺省取当前 UTC）

        Returns:
            写入的明细记录；缺 agent_id 或非 Agent 执行事件时返回 None（跳过）。
        """
        # 缺 agent_id 的事件跳过（非 Agent 执行，不产生脏数据）
        agent_id = payload.get("agent_id")
        if not isinstance(agent_id, str) or not agent_id:
            return None

        status = str(payload.get("status", ""))
        succeeded = status in _SUCCESS_STATUSES

        duration_ms = payload.get("duration_ms", 0)
        if not isinstance(duration_ms, (int, float)) or isinstance(duration_ms, bool) or duration_ms < 0:
            duration_ms = 0

        token_count = payload.get("token_count", 0)
        if not isinstance(token_count, (int, float)) or isinstance(token_count, bool) or token_count < 0:
            token_count = 0

        # 质量评分口径：评估框架产出优先，无评分记录记 0
        quality_score = payload.get("quality_score", 0.0)
        if not isinstance(quality_score, (int, float)) or isinstance(quality_score, bool) or quality_score < 0:
            quality_score = 0.0

        record = AgentPerformanceRecord(
            agent_id=agent_id,
            succeeded=succeeded,
            duration_ms=int(duration_ms),
            token_count=int(token_count),
            quality_score=float(quality_score),
            occurred_at=occurred_at or AgentPerformanceRecord.model_fields["occurred_at"].get_default(),
        )
        await self._repository.append(record)
        return record

    async def start(self, event_bus: "object", topic: str, group_id: str) -> None:
        """订阅事件总线并开始旁路消费（开关开启时由装配层调用）。

        消费 WORKFLOW_EXECUTION 事件流，逐条经 ingest_event 旁路提取；
        本方法阻塞至事件流结束（内部以任务方式运行时可 cancel）。
        """
        self._started = True
        try:
            async for message in event_bus.subscribe(topic, group_id):
                try:
                    await self.ingest_event(
                        payload=message.payload,
                        occurred_at=message.occurred_at,
                    )
                except Exception as exc:
                    # 旁路消费失败不中断事件流（旁路原则：不影响主链路）
                    logger.warning("Agent 绩效旁路消费单条事件失败: %s", exc)
        except Exception as exc:
            logger.warning("Agent 绩效旁路订阅中断: %s", exc)
        finally:
            self._started = False

    def stop(self) -> None:
        """停止旁路消费标记（订阅循环由外层任务 cancel）。"""
        self._started = False
