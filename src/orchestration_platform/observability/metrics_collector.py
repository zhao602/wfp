# =============================================================================
# 指标采集（observability/metrics_collector.py）
#
# 职责：采集实例执行数、成功数、失败数、平均耗时、节点失败分布等指标。
# 设计意图（对应 spec 4.4.5）：
#   暴露基础运行指标，供 /metrics 接口与监控系统集成。
# =============================================================================

from collections import defaultdict
from threading import Lock
from typing import Dict


class MetricsCollector:
    """指标采集器（线程安全）。"""

    def __init__(self) -> None:
        self._lock = Lock()
        self._instance_count: int = 0
        self._success_count: int = 0
        self._failure_count: int = 0
        self._total_duration_ms: int = 0
        self._node_failures: Dict[str, int] = defaultdict(int)
        # 沙箱指标（偏差修正 T131）
        self._sandbox_executions: int = 0
        self._sandbox_violations: int = 0
        self._sandbox_overhead_samples: list = []
        self._sandbox_degraded_count: int = 0

    def record_instance_started(self) -> None:
        with self._lock:
            self._instance_count += 1

    def record_instance_succeeded(self, duration_ms: int) -> None:
        with self._lock:
            self._success_count += 1
            self._total_duration_ms += duration_ms

    def record_instance_failed(self, duration_ms: int) -> None:
        with self._lock:
            self._failure_count += 1
            self._total_duration_ms += duration_ms

    def record_node_failure(self, node_id: str) -> None:
        with self._lock:
            self._node_failures[node_id] += 1

    def record_sandbox_execution(self) -> None:
        """记录一次沙箱执行。"""
        with self._lock:
            self._sandbox_executions += 1

    def record_sandbox_violation(self) -> None:
        """记录一次沙箱违规。"""
        with self._lock:
            self._sandbox_violations += 1

    def record_sandbox_overhead(self, overhead_ratio: float) -> None:
        """记录沙箱开销比率（隔离耗时/裸执行耗时）。"""
        with self._lock:
            self._sandbox_overhead_samples.append(overhead_ratio)

    def record_sandbox_degraded(self) -> None:
        """记录沙箱降级。"""
        with self._lock:
            self._sandbox_degraded_count += 1

    def snapshot(self) -> dict:
        """返回当前指标快照。"""
        with self._lock:
            total = self._success_count + self._failure_count
            avg_duration = self._total_duration_ms / total if total > 0 else 0
            return {
                "instance_total": self._instance_count,
                "instance_succeeded": self._success_count,
                "instance_failed": self._failure_count,
                "average_duration_ms": round(avg_duration, 2),
                "node_failure_distribution": dict(self._node_failures),
                "sandbox_executions": self._sandbox_executions,
                "sandbox_violations": self._sandbox_violations,
                "sandbox_degraded_count": self._sandbox_degraded_count,
                "sandbox_overhead_avg_ratio": round(
                    sum(self._sandbox_overhead_samples) / len(self._sandbox_overhead_samples), 4
                ) if self._sandbox_overhead_samples else 0.0,
            }
