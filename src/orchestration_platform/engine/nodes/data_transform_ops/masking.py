# =============================================================================
# 脱敏规则集模块（engine/nodes/data_transform_ops/masking.py）
#
# 职责：数据集字段值的脱敏规则集（expand_data_transform_ops design DC-06 /
#       CHG-B09 / T4.3，AC-13）。
# 设计口径：
#   - 独立能力：对象为**数据集字段值**（非日志字段名），范式借鉴
#     workflow_run_logging 的「值模式匹配 + 最小暴露 + 漏检告警」，
#     **不复用 LogSanitizer 代码**（对象不同，避免域耦合）；
#   - 内置规则集（值模式正则 + 掩码模板，保留首尾、中间掩码占位）；
#   - 不可逆声明：掩码替换后**不提供反解**，**非加密级保护**；
#   - 缺口可见：未命中规则（hit=False）由调用方按最小暴露处理并告警。
# =============================================================================

import re
from typing import Any, Dict, NamedTuple, Pattern, Tuple

# 内置规则定义（rule_id → (值模式正则, 掩码模板)）：
# 保留首尾、中间掩码占位；掩码为不可逆占位符，不保留原值信息。
_BUILTIN_RULE_DEFS: Dict[str, Tuple[str, str]] = {
    # 手机号：保留前 3 后 4，中间 4 位掩码（138****5678）
    "phone": (r"(?<!\d)(1[3-9]\d)\d{4}(\d{4})(?!\d)", r"\1****\2"),
    # 身份证号（18 位，末位可为 X）：保留前 6 后 4（110101********123X）
    "id_card": (
        r"(?<![0-9Xx])(\d{6})\d{8}(\d{3}[0-9Xx])(?![0-9Xx])",
        r"\1********\2",
    ),
    # 银行卡号（16~19 位数字）：保留前 4 后 4（6222********5123）
    "bank_card": (r"(?<!\d)(\d{4})\d{8,11}(\d{4})(?!\d)", r"\1********\2"),
    # 邮箱：保留本地部分首字符与完整域名（z***@example.com）
    "email": (
        r"(?<![A-Za-z0-9._%+-])([A-Za-z0-9])[A-Za-z0-9._%+-]*(@[A-Za-z0-9.-]+\.[A-Za-z]{2,})",
        r"\1***\2",
    ),
    # 中文姓名：保留姓氏，名字掩码（张三 → 张**）
    "name": (
        r"(?<![\u4e00-\u9fa5])([\u4e00-\u9fa5])[\u4e00-\u9fa5]+(?![\u4e00-\u9fa5])",
        r"\1**",
    ),
    # 地址：保留前缀（省市区，至多 6 字），其余掩码（北京市海淀区****）
    "address": (
        r"(?<![\u4e00-\u9fa5])([\u4e00-\u9fa5]{6})[\u4e00-\u9fa5]{2,}(?![\u4e00-\u9fa5])",
        r"\1****",
    ),
}

# 不可逆声明（UI 与结果说明双重呈现的统一文案）
MASKING_IRREVERSIBLE_NOTE = "masking 为不可逆掩码（非加密级），输出无法还原原值"


class MaskingResult(NamedTuple):
    """单规则脱敏结果（hit=False 表示未命中 → 调用方按缺口处理）。"""

    value: Any
    hit: bool


class MaskingRuleSet:
    """脱敏规则集：内置规则 + 自定义规则，按规则 id 应用值模式掩码。

    不可逆：掩码模板为占位符替换，不保留还原信息（非加密级保护）。
    """

    def __init__(self) -> None:
        self._rules: Dict[str, Tuple[Pattern[str], str]] = {}

    @classmethod
    def builtin(cls) -> "MaskingRuleSet":
        """构造含全部内置规则的规则集。"""
        rule_set = cls()
        for rule_id, (pattern, mask_template) in _BUILTIN_RULE_DEFS.items():
            rule_set.add_rule(rule_id, pattern, mask_template)
        return rule_set

    def add_rule(self, rule_id: str, pattern: str, mask_template: str) -> None:
        """登记自定义规则（值模式正则 + 掩码模板）。

        正则编译失败抛 ValueError（配置期即拒，不静默）。
        """
        if rule_id in self._rules:
            raise ValueError(f"脱敏规则重复登记：{rule_id}")
        try:
            compiled = re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"脱敏规则 {rule_id} 正则编译失败：{exc}") from exc
        self._rules[rule_id] = (compiled, mask_template)

    def has_rule(self, rule_id: str) -> bool:
        return rule_id in self._rules

    def rule_ids(self) -> Tuple[str, ...]:
        return tuple(self._rules.keys())

    def mask_field(self, value: Any, rule_id: str) -> MaskingResult:
        """按规则对单值脱敏（不可逆）。

        - 非字符串值：不适用值模式 → hit=False（原值返回，缺口由调用方处理）；
        - 未命中：hit=False（原值返回）；
        - 命中：返回掩码替换后的新值（hit=True）。
        """
        if rule_id not in self._rules:
            raise ValueError(f"脱敏规则不存在：{rule_id}")
        if not isinstance(value, str):
            return MaskingResult(value, False)

        compiled, mask_template = self._rules[rule_id]
        masked, hits = compiled.subn(mask_template, value)
        if hits == 0:
            return MaskingResult(value, False)
        return MaskingResult(masked, True)
