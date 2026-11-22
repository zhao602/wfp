# =============================================================================
# 触发器模板评估器（engine/realtime_subscription/trigger_templates/evaluator.py）
#
# 职责：实现三类有状态触发评估器（G6/T224）。
# 设计意图（对应 spec 5.6.3-5.6.5、design D6）：
#   1. 滑动窗口：窗口满且 |均值-基线|>容差 触发（A6.3）；
#   2. 阈值持续突破：连续 N 次突破才触发一次，计数中断即清零防抖（A6.4）；
#   3. 方差突变：本窗方差 > 前窗方差×ratio 触发（A6.5）；
#   4. 字段缺失按不命中处理并计入诊断计数（不误报，异常场景 2）；
#   5. 评估器实例按 subscription_id 隔离，暂停/删除订阅时同步销毁防泄漏。
# =============================================================================

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


@runtime_checkable
class ITriggerTemplateEvaluator(Protocol):
    """触发器模板评估器抽象接口。

    前置条件：参数已通过注册表 validate_params 校验。
    后置条件：命中时返回 True；字段缺失不命中且计入诊断计数。
    """

    def update_and_evaluate(self, event_data: Dict[str, Any]) -> bool:
        """输入一条事件，更新状态并返回是否触发。

        Returns:
            True 表示本次事件满足模板触发语义。
        """
        ...

    def reset(self) -> None:
        """清空评估器内部状态（订阅恢复时可选重置）。"""
        ...


class _MissingFieldStats:
    """字段缺失诊断计数（不误报，仅记录）。"""

    def __init__(self) -> None:
        self.missing_field_count: int = 0

    def record_missing(self) -> None:
        self.missing_field_count += 1


def _extract_number(event_data: Dict[str, Any], field_name: str, stats: _MissingFieldStats) -> Optional[float]:
    """从事件数据提取数值字段；缺失或非数值返回 None 并计入诊断。"""
    value = event_data.get(field_name)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        stats.record_missing()
        return None
    return float(value)


class SlidingWindowEvaluator:
    """滑动窗口统计异常评估器（A6.3）。

    参数：
        field_name   —— 观测字段名
        window_size  —— 窗口大小（≥1）
        baseline     —— 基线值
        tolerance    —— 容差（>0）

    语义：窗口填满后计算窗口均值，|均值-基线|>tolerance 触发；
    触发后窗口清空重新积累（避免持续触发）。
    """

    def __init__(self, field_name: str, window_size: int, baseline: float, tolerance: float) -> None:
        self._field_name = field_name
        self._window_size = window_size
        self._baseline = baseline
        self._tolerance = tolerance
        self._window: List[float] = []
        self._stats = _MissingFieldStats()

    @property
    def missing_field_count(self) -> int:
        return self._stats.missing_field_count

    def update_and_evaluate(self, event_data: Dict[str, Any]) -> bool:
        value = _extract_number(event_data, self._field_name, self._stats)
        if value is None:
            return False
        self._window.append(value)
        if len(self._window) < self._window_size:
            return False
        mean = sum(self._window) / len(self._window)
        self._window = []
        return abs(mean - self._baseline) > self._tolerance

    def reset(self) -> None:
        self._window = []
        self._stats = _MissingFieldStats()


class ThresholdSustainedEvaluator:
    """阈值持续突破评估器（A6.4，防抖）。

    参数：
        field_name      —— 观测字段名
        threshold       —— 阈值
        sustained_count —— 连续突破次数要求（≥1）

    语义：连续 N 次观测值>阈值才触发一次；计数中断即清零；
    触发后计数清零重新积累（同一持续突破只触发一次）。
    """

    def __init__(self, field_name: str, threshold: float, sustained_count: int) -> None:
        self._field_name = field_name
        self._threshold = threshold
        self._sustained_count = sustained_count
        self._consecutive: int = 0
        self._stats = _MissingFieldStats()

    @property
    def missing_field_count(self) -> int:
        return self._stats.missing_field_count

    def update_and_evaluate(self, event_data: Dict[str, Any]) -> bool:
        value = _extract_number(event_data, self._field_name, self._stats)
        if value is None:
            self._consecutive = 0
            return False
        if value > self._threshold:
            self._consecutive += 1
            if self._consecutive >= self._sustained_count:
                self._consecutive = 0
                return True
            return False
        self._consecutive = 0
        return False

    def reset(self) -> None:
        self._consecutive = 0
        self._stats = _MissingFieldStats()


class VarianceSpikeEvaluator:
    """方差突变评估器（A6.5）。

    参数：
        field_name     —— 观测字段名
        window_size    —— 窗口大小（≥2）
        variance_ratio —— 突变倍数（>1.0）

    语义：窗口填满后计算本窗方差；若存在前窗且
    本窗方差 > 前窗方差×ratio 触发；本窗滚动为前窗继续积累。
    """

    def __init__(self, field_name: str, window_size: int, variance_ratio: float) -> None:
        self._field_name = field_name
        self._window_size = window_size
        self._variance_ratio = variance_ratio
        self._window: List[float] = []
        self._prev_variance: Optional[float] = None
        self._stats = _MissingFieldStats()

    @property
    def missing_field_count(self) -> int:
        return self._stats.missing_field_count

    def update_and_evaluate(self, event_data: Dict[str, Any]) -> bool:
        value = _extract_number(event_data, self._field_name, self._stats)
        if value is None:
            return False
        self._window.append(value)
        if len(self._window) < self._window_size:
            return False
        variance = self._compute_variance(self._window)
        self._window = []
        if self._prev_variance is None:
            self._prev_variance = variance
            return False
        triggered = variance > self._prev_variance * self._variance_ratio
        self._prev_variance = variance
        return triggered

    @staticmethod
    def _compute_variance(values: List[float]) -> float:
        """计算总体方差。"""
        n = len(values)
        if n == 0:
            return 0.0
        mean = sum(values) / n
        return sum((v - mean) ** 2 for v in values) / n

    def reset(self) -> None:
        self._window = []
        self._prev_variance = None
        self._stats = _MissingFieldStats()
