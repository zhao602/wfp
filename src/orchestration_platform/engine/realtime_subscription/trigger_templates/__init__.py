# =============================================================================
# 触发器模板三分层包（engine/realtime_subscription/trigger_templates/__init__.py）
#
# 职责：导出触发器模板注册表/展开器/评估器公共接口（G6/T224）。
# 设计意图（对应 spec 5.6、design D6）：
#   1. 三类预置异常检测触发器模板（注册表/展开器/评估器三分层）；
#   2. 基于既有 EventFilterRule 组合实现，不改写订阅机制（X4/X5）；
#   3. 全部判定逻辑为纯进程内计算，内存降级实现下语义同等可用。
#
# 禁止 import langgraph。
# =============================================================================

from orchestration_platform.engine.realtime_subscription.trigger_templates.evaluator import (
    ITriggerTemplateEvaluator,
    SlidingWindowEvaluator,
    ThresholdSustainedEvaluator,
    VarianceSpikeEvaluator,
)
from orchestration_platform.engine.realtime_subscription.trigger_templates.expander import (
    TemplateExpander,
)
from orchestration_platform.engine.realtime_subscription.trigger_templates.registry import (
    TriggerTemplateRegistry,
)

__all__ = [
    "ITriggerTemplateEvaluator",
    "SlidingWindowEvaluator",
    "ThresholdSustainedEvaluator",
    "VarianceSpikeEvaluator",
    "TemplateExpander",
    "TriggerTemplateRegistry",
]
