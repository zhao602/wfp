# =============================================================================
# Agent 绩效可观测包（observability/agent_performance/__init__.py）
#
# 职责：导出 Agent 绩效记录器、领域模型与仓储（G3/T227）。
# 设计意图（对应 spec 5.3、design D5）：
#   1. 以事件订阅旁路方式建立 Agent 维度绩效明细与四维聚合查询；
#   2. 禁止侵入既有 MetricsCollector/GovernanceMetrics 的 record_* 方法（A3.4）；
#   3. 记录器挂载由 agent_performance_enabled 开关控制，默认 False。
#
# 禁止 import langgraph。
# =============================================================================

from orchestration_platform.observability.agent_performance.models import (
    AgentPerformanceAggregate,
    AgentPerformanceRecord,
)
from orchestration_platform.observability.agent_performance.recorder import (
    AgentPerformanceRecorder,
)

__all__ = [
    "AgentPerformanceAggregate",
    "AgentPerformanceRecord",
    "AgentPerformanceRecorder",
]
