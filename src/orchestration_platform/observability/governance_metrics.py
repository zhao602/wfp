# TODO(tech-debt): B档候选孤儿（族群 B15），待 APM 链路追踪专项排查确认无调用后清理，见 docs/tech-debt-ledger.md
# =============================================================================
# 治理可观测指标（observability/governance_metrics.py）
#
# 职责：采集治理模块的可观测指标。
# 设计意图（对应 spec 5.5、design 2.8、任务 T76）：
#   1. 鉴权指标：鉴权次数、拒绝率、平均鉴权耗时；
#   2. 计量指标：token 用量分布、按模型汇总用量；
#   3. 成本指标：成本趋势、按租户汇总成本；
#   4. 预算指标：预算使用率、熔断次数；
#   5. 审计指标：审计日志写入量、查询量；
#   6. 调度指标：调度公平性、队列长度、调度决策耗时；
#   7. 资源池指标：资源池占用率、抢占次数。
# =============================================================================

import time
from typing import Any, Dict

from orchestration_platform.observability.metrics_collector import MetricsCollector


class GovernanceMetrics:
    """治理可观测指标采集器。

    以追加方式扩展既有指标，不修改既有指标。
    """

    def __init__(self, collector: MetricsCollector) -> None:
        """初始化治理指标采集器。

        Args:
            collector: 既有指标采集器。
        """
        self._collector = collector
        self._auth_total = 0
        self._auth_denied = 0
        self._auth_total_time_ms = 0.0
        self._metering_total = 0
        self._budget_circuit_breaks = 0
        self._audit_writes = 0
        self._audit_queries = 0
        self._scheduler_decisions = 0
        self._scheduler_total_time_ms = 0.0
        self._preemption_count = 0

    def record_auth(
        self,
        allowed: bool,
        duration_ms: float,
    ) -> None:
        """记录鉴权指标。

        Args:
            allowed: 是否放行。
            duration_ms: 鉴权耗时（毫秒）。
        """
        self._auth_total += 1
        if not allowed:
            self._auth_denied += 1
        self._auth_total_time_ms += duration_ms

    def record_metering(self, token_count: int) -> None:
        """记录计量指标。"""
        self._metering_total += token_count

    def record_budget_circuit_break(self) -> None:
        """记录预算熔断。"""
        self._budget_circuit_breaks += 1

    def record_audit_write(self) -> None:
        """记录审计日志写入。"""
        self._audit_writes += 1

    def record_audit_query(self) -> None:
        """记录审计日志查询。"""
        self._audit_queries += 1

    def record_scheduler_decision(
        self,
        duration_ms: float,
    ) -> None:
        """记录调度决策指标。"""
        self._scheduler_decisions += 1
        self._scheduler_total_time_ms += duration_ms

    def record_preemption(self) -> None:
        """记录抢占次数。"""
        self._preemption_count += 1

    def snapshot(self) -> Dict[str, Any]:
        """获取治理指标快照。

        以追加方式扩展既有指标快照。
        """
        auth_avg_time = (
            self._auth_total_time_ms / self._auth_total
            if self._auth_total > 0
            else 0
        )
        auth_deny_rate = (
            self._auth_denied / self._auth_total
            if self._auth_total > 0
            else 0
        )
        scheduler_avg_time = (
            self._scheduler_total_time_ms / self._scheduler_decisions
            if self._scheduler_decisions > 0
            else 0
        )

        return {
            "governance": {
                "auth": {
                    "total": self._auth_total,
                    "denied": self._auth_denied,
                    "deny_rate": round(auth_deny_rate, 4),
                    "avg_time_ms": round(auth_avg_time, 2),
                },
                "metering": {
                    "total_tokens": self._metering_total,
                },
                "budget": {
                    "circuit_breaks": self._budget_circuit_breaks,
                },
                "audit": {
                    "writes": self._audit_writes,
                    "queries": self._audit_queries,
                },
                "scheduler": {
                    "decisions": self._scheduler_decisions,
                    "avg_decision_time_ms": round(scheduler_avg_time, 2),
                },
                "preemption": {
                    "total": self._preemption_count,
                },
            }
        }
