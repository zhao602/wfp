# =============================================================================
# 触发器模板注册表（engine/realtime_subscription/trigger_templates/registry.py）
#
# 职责：模板类型→参数校验+展开器工厂的注册与查询（G6/T224）。
# 设计意图（对应 spec 5.6.1、design D6）：
#   1. 三类预置模板：滑动窗口统计异常/阈值持续突破/方差突变；
#   2. validate_params 按模板类型约束校验，非法抛 TRIGGER_TEMPLATE_PARAM_INVALID；
#   3. list_templates 返回全部预置模板类型与参数中文说明（A6.1）；
#   4. 非法参数拒绝注册/展开，不产生半成品。
# =============================================================================

from typing import Any, Callable, Dict, List, Optional

from orchestration_platform.domain.errors import TRIGGER_TEMPLATE_PARAM_INVALID, DomainError
from orchestration_platform.engine.realtime_subscription.models import TriggerTemplateBinding
from orchestration_platform.engine.realtime_subscription.trigger_templates.expander import (
    TemplateExpansion,
    TemplateExpander,
)

# 参数校验器签名：params -> 校验通过返回规范化后的参数，失败抛 DomainError
_ParamValidator = Callable[[Dict[str, Any]], Dict[str, Any]]


class TriggerTemplateRegistry:
    """触发器模板注册表：按模板类型登记参数校验器与展开器工厂。"""

    def __init__(self, expander: Optional[TemplateExpander] = None) -> None:
        self._validators: Dict[str, _ParamValidator] = {}
        self._expanders: Dict[str, Callable[[Dict[str, Any]], TemplateExpansion]] = {}
        self._descriptions: Dict[str, str] = {}
        self._expander = expander if expander is not None else TemplateExpander()

    # ------------------------------------------------------------------
    # 注册与查询
    # ------------------------------------------------------------------

    def register(
        self,
        template_type: str,
        validator: _ParamValidator,
        expander_factory: Callable[[Dict[str, Any]], TemplateExpansion],
        description: str,
    ) -> None:
        """登记模板类型（校验器 + 展开器工厂 + 中文说明）。"""
        self._validators[template_type] = validator
        self._expanders[template_type] = expander_factory
        self._descriptions[template_type] = description

    def has_template(self, template_type: str) -> bool:
        """检查模板类型是否已登记。"""
        return template_type in self._validators

    def list_templates(self) -> List[Dict[str, str]]:
        """列出全部预置模板类型与参数中文说明（A6.1）。"""
        return [
            {"template_type": t, "description": d}
            for t, d in self._descriptions.items()
        ]

    # ------------------------------------------------------------------
    # 校验与展开
    # ------------------------------------------------------------------

    def validate_params(
        self, template_type: str, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """校验模板参数，通过则返回规范化参数。

        Raises:
            DomainError(TRIGGER_TEMPLATE_UNKNOWN) —— 模板类型未登记
            DomainError(TRIGGER_TEMPLATE_PARAM_INVALID) —— 参数非法
        """
        validator = self._validators.get(template_type)
        if validator is None:
            raise DomainError(
                TRIGGER_TEMPLATE_PARAM_INVALID,
                f"触发器模板类型未知: {template_type}",
            )
        return validator(params)

    def expand(self, binding: TriggerTemplateBinding) -> TemplateExpansion:
        """校验并展开模板绑定为粗筛规则 + 评估器。

        参数非法时抛异常拒绝展开（不注册半成品）。
        """
        normalized = self.validate_params(binding.template_type, binding.params)
        expander_factory = self._expanders.get(binding.template_type)
        if expander_factory is None:
            raise DomainError(
                TRIGGER_TEMPLATE_PARAM_INVALID,
                f"触发器模板类型未知: {binding.template_type}",
            )
        return expander_factory(normalized)

    @classmethod
    def create_default_registry(cls) -> "TriggerTemplateRegistry":
        """创建含三类预置模板的默认注册表。"""
        registry = cls(expander=TemplateExpander())

        registry.register(
            template_type="sliding_window_anomaly",
            validator=_validate_sliding_window,
            expander_factory=registry._expander.expand_sliding_window,
            description=(
                "滑动窗口统计异常：窗口填满后计算均值，|均值-基线|>容差触发。"
                "参数：field_name(字段名)/window_size(窗口大小≥1)/baseline(基线)/tolerance(容差>0)"
            ),
        )
        registry.register(
            template_type="threshold_sustained_breach",
            validator=_validate_threshold_sustained,
            expander_factory=registry._expander.expand_threshold_sustained,
            description=(
                "阈值持续突破：连续 N 次观测值超过阈值才触发一次（防抖），"
                "展开 gt 粗筛规则进入既有过滤路径。"
                "参数：field_name(字段名)/threshold(阈值)/sustained_count(连续次数≥1)"
            ),
        )
        registry.register(
            template_type="variance_spike",
            validator=_validate_variance_spike,
            expander_factory=registry._expander.expand_variance_spike,
            description=(
                "方差突变：本窗方差>前窗方差×倍数触发。"
                "参数：field_name(字段名)/window_size(窗口大小≥2)/variance_ratio(突变倍数>1.0)"
            ),
        )
        return registry


# ----------------------------------------------------------------------
# 三类模板参数校验器（模块级函数，供注册表登记）
# ----------------------------------------------------------------------


def _require_number(params: Dict[str, Any], key: str, desc: str) -> float:
    """提取数值参数；缺失或类型错误抛异常。"""
    value = params.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise DomainError(
            TRIGGER_TEMPLATE_PARAM_INVALID,
            f"模板参数 {key} 非法：{desc}（须数值，实际 {value!r}）",
        )
    return float(value)


def _require_int(params: Dict[str, Any], key: str, desc: str) -> int:
    """提取整数参数；缺失或类型错误抛异常。"""
    value = params.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise DomainError(
            TRIGGER_TEMPLATE_PARAM_INVALID,
            f"模板参数 {key} 非法：{desc}（须整数，实际 {value!r}）",
        )
    return value


def _require_field_name(params: Dict[str, Any]) -> str:
    """提取非空字段名参数。"""
    value = params.get("field_name")
    if not isinstance(value, str) or not value:
        raise DomainError(
            TRIGGER_TEMPLATE_PARAM_INVALID,
            f"模板参数 field_name 非法：须非空字符串（实际 {value!r}）",
        )
    return value


def _validate_sliding_window(params: Dict[str, Any]) -> Dict[str, Any]:
    """滑动窗口模板参数校验：field_name/window_size≥1/baseline/tolerance>0。"""
    field_name = _require_field_name(params)
    window_size = _require_int(params, "window_size", "窗口大小须≥1")
    if window_size < 1:
        raise DomainError(
            TRIGGER_TEMPLATE_PARAM_INVALID,
            f"模板参数 window_size 非法：窗口大小须≥1（实际 {window_size}）",
        )
    baseline = _require_number(params, "baseline", "基线值须数值")
    tolerance = _require_number(params, "tolerance", "容差须数值且>0")
    if tolerance <= 0:
        raise DomainError(
            TRIGGER_TEMPLATE_PARAM_INVALID,
            f"模板参数 tolerance 非法：容差须>0（实际 {tolerance}）",
        )
    return {
        "field_name": field_name,
        "window_size": window_size,
        "baseline": baseline,
        "tolerance": tolerance,
    }


def _validate_threshold_sustained(params: Dict[str, Any]) -> Dict[str, Any]:
    """阈值持续突破模板参数校验：field_name/threshold/sustained_count≥1。"""
    field_name = _require_field_name(params)
    threshold = _require_number(params, "threshold", "阈值须数值")
    sustained_count = _require_int(params, "sustained_count", "连续次数须≥1")
    if sustained_count < 1:
        raise DomainError(
            TRIGGER_TEMPLATE_PARAM_INVALID,
            f"模板参数 sustained_count 非法：连续次数须≥1（实际 {sustained_count}）",
        )
    return {
        "field_name": field_name,
        "threshold": threshold,
        "sustained_count": sustained_count,
    }


def _validate_variance_spike(params: Dict[str, Any]) -> Dict[str, Any]:
    """方差突变模板参数校验：field_name/window_size≥2/variance_ratio>1.0。"""
    field_name = _require_field_name(params)
    window_size = _require_int(params, "window_size", "窗口大小须≥2")
    if window_size < 2:
        raise DomainError(
            TRIGGER_TEMPLATE_PARAM_INVALID,
            f"模板参数 window_size 非法：窗口大小须≥2（实际 {window_size}）",
        )
    variance_ratio = _require_number(params, "variance_ratio", "突变倍数须数值且>1.0")
    if variance_ratio <= 1.0:
        raise DomainError(
            TRIGGER_TEMPLATE_PARAM_INVALID,
            f"模板参数 variance_ratio 非法：突变倍数须>1.0（实际 {variance_ratio}）",
        )
    return {
        "field_name": field_name,
        "window_size": window_size,
        "variance_ratio": variance_ratio,
    }
