# =============================================================================
# 注入防护校验器（governance/nl_orchestration/injection_guard.py）
#
# 职责：对自然语言编排指令执行注入防护校验，阻止恶意输入。
# 设计意图（对应偏差修正 spec 4.3.5 / design 模块A InjectionGuard）：
#   - YAML 注入检测：识别 !!python/object、!!exec 等危险 YAML 标签
#   - 提示注入检测：识别 ignore previous instructions、system: 等提示劫持模式
#   - 超长输入检测：超过配置上限的输入直接拒绝
# =============================================================================

import re
from typing import List

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.governance.nl_orchestration.models import InjectionCheckResult


# YAML 注入危险标签模式
_YAML_INJECTION_PATTERNS: List[re.Pattern] = [
    re.compile(r"!!python/object", re.IGNORECASE),
    re.compile(r"!!exec", re.IGNORECASE),
    re.compile(r"!!eval", re.IGNORECASE),
    re.compile(r"!!subprocess", re.IGNORECASE),
    re.compile(r"!!os\.system", re.IGNORECASE),
    re.compile(r"!!globals", re.IGNORECASE),
    re.compile(r"!!locals", re.IGNORECASE),
    re.compile(r"!!apply", re.IGNORECASE),
    re.compile(r"!!map\s+!!python", re.IGNORECASE),
]

# 提示注入模式
_PROMPT_INJECTION_PATTERNS: List[re.Pattern] = [
    re.compile(r"ignore\s+(previous|above|prior)\s+instructions", re.IGNORECASE),
    re.compile(r"disregard\s+(previous|above|prior)\s+instructions", re.IGNORECASE),
    re.compile(r"forget\s+(previous|above|prior)\s+instructions", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+(?:a|an)\s+(?:different|new)\s+(?:assistant|ai|model)", re.IGNORECASE),
    re.compile(r"system\s*:\s*", re.IGNORECASE),
    re.compile(r"<\s*system\s*>", re.IGNORECASE),
    re.compile(r"###\s*system\s*", re.IGNORECASE),
    re.compile(r"override\s+(system|safety|security)\s+(prompt|instructions?)", re.IGNORECASE),
    re.compile(r"reveal\s+(your|the)\s+(system|initial)\s+prompt", re.IGNORECASE),
    re.compile(r"jailbreak", re.IGNORECASE),
]


class InjectionGuard:
    """自然语言编排注入防护校验器。

    在 NL 编排流程的第一步执行，拦截 YAML 注入、提示注入与超长输入。
    """

    def check(self, directive: str) -> InjectionCheckResult:
        """对指令文本执行注入防护校验。

        前置条件：directive 为字符串。
        后置条件：返回 InjectionCheckResult，passed=True 表示安全，passed=False 表示检测到注入。
        """
        detected_patterns: List[str] = []

        # 1. 超长输入检测
        settings = get_settings()
        max_length = settings.nl_orchestration_max_input_length
        if len(directive) > max_length:
            return InjectionCheckResult(
                passed=False,
                reason=f"输入长度 {len(directive)} 超过上限 {max_length}",
                detected_patterns=["input_too_long"],
            )

        # 2. YAML 注入检测
        for pattern in _YAML_INJECTION_PATTERNS:
            match = pattern.search(directive)
            if match:
                detected_patterns.append(f"yaml_injection:{match.group()}")

        # 3. 提示注入检测
        for pattern in _PROMPT_INJECTION_PATTERNS:
            match = pattern.search(directive)
            if match:
                detected_patterns.append(f"prompt_injection:{match.group()}")

        if detected_patterns:
            return InjectionCheckResult(
                passed=False,
                reason=f"检测到注入模式: {', '.join(detected_patterns)}",
                detected_patterns=detected_patterns,
            )

        return InjectionCheckResult(passed=True)
