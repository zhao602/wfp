# =============================================================================
# 触发器模板展开器（engine/realtime_subscription/trigger_templates/expander.py）
#
# 职责：将触发器模板展开为既有 EventFilterRule 组合 + 有状态评估器（G6/T224）。
# 设计意图（对应 spec 5.6.2、design D6）：
#   1. 展开规则（如阈值模板展开 gt 规则）进入既有 filter_rules 匹配路径做前置粗筛；
#   2. EventFilterRule 三字段与五操作符零修改（X4/X5）——只消费既有结构；
#   3. 评估器承担有状态判定，粗筛规则承担无状态前置过滤。
# =============================================================================

from dataclasses import dataclass, field
from typing import Any, Dict, List

from orchestration_platform.engine.realtime_subscription.models import EventFilterRule
from orchestration_platform.engine.realtime_subscription.trigger_templates.evaluator import (
    ITriggerTemplateEvaluator,
    SlidingWindowEvaluator,
    ThresholdSustainedEvaluator,
    VarianceSpikeEvaluator,
)


@dataclass
class TemplateExpansion:
    """模板展开结果：粗筛规则组合 + 有状态评估器。

    属性：
        expanded_rules —— 展开出的 EventFilterRule 列表（增广进订阅 filter_rules）
        evaluator      —— 有状态评估器实例（按订阅隔离）
    """

    expanded_rules: List[EventFilterRule] = field(default_factory=list)
    evaluator: ITriggerTemplateEvaluator = None  # type: ignore[assignment]


class TemplateExpander:
    """触发器模板展开器：按模板类型构造粗筛规则与评估器。

    三类模板展开策略：
        SLIDING_WINDOW_ANOMALY     —— 无粗筛规则（窗口语义需全量事件），评估器滑动窗口
        THRESHOLD_SUSTAINED_BREACH —— 展开 gt threshold 粗筛规则，评估器防抖计数
        VARIANCE_SPIKE             —— 无粗筛规则（窗口语义需全量事件），评估器方差突变
    """

    def expand_sliding_window(self, params: Dict[str, Any]) -> TemplateExpansion:
        """展开滑动窗口统计异常模板。"""
        evaluator = SlidingWindowEvaluator(
            field_name=str(params["field_name"]),
            window_size=int(params["window_size"]),
            baseline=float(params["baseline"]),
            tolerance=float(params["tolerance"]),
        )
        return TemplateExpansion(expanded_rules=[], evaluator=evaluator)

    def expand_threshold_sustained(self, params: Dict[str, Any]) -> TemplateExpansion:
        """展开阈值持续突破模板（gt 粗筛规则进入既有 filter_rules 路径）。"""
        threshold = float(params["threshold"])
        rules = [
            EventFilterRule(
                field_name=str(params["field_name"]),
                operator="gt",
                value=threshold,
            )
        ]
        evaluator = ThresholdSustainedEvaluator(
            field_name=str(params["field_name"]),
            threshold=threshold,
            sustained_count=int(params["sustained_count"]),
        )
        return TemplateExpansion(expanded_rules=rules, evaluator=evaluator)

    def expand_variance_spike(self, params: Dict[str, Any]) -> TemplateExpansion:
        """展开方差突变模板。"""
        evaluator = VarianceSpikeEvaluator(
            field_name=str(params["field_name"]),
            window_size=int(params["window_size"]),
            variance_ratio=float(params["variance_ratio"]),
        )
        return TemplateExpansion(expanded_rules=[], evaluator=evaluator)
