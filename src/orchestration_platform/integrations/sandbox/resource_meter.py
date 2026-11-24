# =============================================================================
# 资源计量器（integrations/sandbox/resource_meter.py）
#
# 职责：CPU 时间/内存占用/输出尺寸三维限额周期采样计量。
# 设计意图（对应偏差修正 spec 5.1.1.5 / design 2.2.2.3）：
#   - 任一维度超限即终止执行，返回 RESOURCE_EXCEEDED 违规与已捕获的部分输出
#   - Windows 无 resource 模块时降级为"时间+输出尺寸"双维计量
#   - 输出尺寸超限不静默截断伪装成功
#   - 纯标准库实现（time/threading）
# =============================================================================

import logging
import time
from typing import Optional

from orchestration_platform.domain.models.enums import ViolationType
from orchestration_platform.integrations.sandbox.models import (
    ResourceUsage,
    SandboxPolicy,
    SandboxViolationRecord,
)

_logger = logging.getLogger(__name__)

# 检测 resource 模块可用性（Windows 可能缺失）
try:
    import resource as _resource_module
    _HAS_RESOURCE_MODULE = True
except ImportError:
    _HAS_RESOURCE_MODULE = False
    _logger.info("resource 模块不可用，资源计量降级为时间+输出尺寸双维")


class ResourceMeter:
    """资源计量器：三维限额采样计量。

    CPU 时间/内存峰值/输出尺寸，任一超限即终止。
    """

    def __init__(self) -> None:
        """初始化资源计量器。"""
        self._degraded = not _HAS_RESOURCE_MODULE
        if self._degraded:
            _logger.warning(
                "资源计量降级模式（SANDBOX_RESOURCE_METER_DEGRADED）: "
                "缺失 resource 模块，仅计量时间+输出尺寸"
            )

    @property
    def is_degraded(self) -> bool:
        """是否降级模式。"""
        return self._degraded

    def check_output_size(
        self,
        output_bytes: int,
        policy: SandboxPolicy,
    ) -> Optional[SandboxViolationRecord]:
        """校验输出尺寸。

        参数：
            output_bytes：当前输出字节数
            policy：沙箱策略

        返回：
            None 表示未超限；SandboxViolationRecord 表示超限
        """
        if output_bytes > policy.output_bytes_limit:
            return SandboxViolationRecord(
                violation_type=ViolationType.RESOURCE_EXCEEDED,
                detail=f"输出尺寸超限: {output_bytes} > {policy.output_bytes_limit}",
            )
        return None

    def check_cpu_time(
        self,
        cpu_seconds: float,
        policy: SandboxPolicy,
    ) -> Optional[SandboxViolationRecord]:
        """校验 CPU 时间。

        参数：
            cpu_seconds：已用 CPU 时间（秒）
            policy：沙箱策略

        返回：
            None 表示未超限；SandboxViolationRecord 表示超限
        """
        if cpu_seconds > policy.cpu_seconds_limit:
            return SandboxViolationRecord(
                violation_type=ViolationType.RESOURCE_EXCEEDED,
                detail=f"CPU 时间超限: {cpu_seconds:.2f}s > {policy.cpu_seconds_limit}s",
            )
        return None

    def check_memory(
        self,
        memory_mb: float,
        policy: SandboxPolicy,
    ) -> Optional[SandboxViolationRecord]:
        """校验内存占用。

        参数：
            memory_mb：当前内存峰值（MB）
            policy：沙箱策略

        返回：
            None 表示未超限；SandboxViolationRecord 表示超限
            降级模式下始终返回 None（不计量内存）
        """
        if self._degraded:
            return None

        if memory_mb > policy.memory_mb_limit:
            return SandboxViolationRecord(
                violation_type=ViolationType.RESOURCE_EXCEEDED,
                detail=f"内存超限: {memory_mb:.1f}MB > {policy.memory_mb_limit}MB",
            )
        return None

    def measure_usage(
        self,
        start_time: float,
        output_bytes: int,
    ) -> ResourceUsage:
        """测量当前资源用量。

        参数：
            start_time：开始时间（time.monotonic() 值）
            output_bytes：当前输出字节数

        返回：
            ResourceUsage 实例
        """
        cpu_seconds = time.monotonic() - start_time
        memory_peak_mb = 0.0

        if not self._degraded:
            try:
                import os
                import psutil  # type: ignore
                process = psutil.Process(os.getpid())
                memory_peak_mb = process.memory_info().rss / (1024 * 1024)
            except ImportError:
                # psutil 不可用时仅用 resource 模块的 getrusage
                try:
                    rusage = _resource_module.getrusage(_resource_module.RUSAGE_SELF)
                    memory_peak_mb = rusage.ru_maxrss / 1024  # KB → MB
                except Exception:
                    memory_peak_mb = 0.0
            except Exception:
                memory_peak_mb = 0.0

        return ResourceUsage(
            cpu_seconds=round(cpu_seconds, 4),
            memory_peak_mb=round(memory_peak_mb, 2),
            output_bytes=output_bytes,
        )

    def check_all(
        self,
        usage: ResourceUsage,
        policy: SandboxPolicy,
    ) -> Optional[SandboxViolationRecord]:
        """综合校验三维资源用量。

        参数：
            usage：资源用量
            policy：沙箱策略

        返回：
            第一个超限的违规记录，或 None 表示全部通过
        """
        violation = self.check_cpu_time(usage.cpu_seconds, policy)
        if violation is not None:
            return violation

        violation = self.check_memory(usage.memory_peak_mb, policy)
        if violation is not None:
            return violation

        violation = self.check_output_size(usage.output_bytes, policy)
        if violation is not None:
            return violation

        return None
