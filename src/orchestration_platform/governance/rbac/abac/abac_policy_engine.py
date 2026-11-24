# =============================================================================
# ABAC 策略引擎实现（governance/rbac/abac/abac_policy_engine.py）
#
# 职责：执行 ABAC 策略决策，加载租户策略并逐条求值条件。
# 设计意图（对应偏差修正 spec 5.2.1 / design 2.1.3.1 状态机）：
#   - 加载租户 ABAC 策略→无策略返回 is_no_policy=True
#   - 逐条策略求值条件→合取匹配后返回效果
#   - 属性求值失败按策略 fail_mode 处理
#   - 策略按 priority 排序，首个匹配的策略决定效果
# =============================================================================

import logging
from typing import List

from orchestration_platform.domain.models.enums import (
    ConditionOperator,
    FailMode,
    PolicyEffect,
)
from orchestration_platform.governance.interceptors.base import GovernanceRequest
from orchestration_platform.governance.rbac.abac.abstractions.attribute_evaluator import (
    IAttributeEvaluator,
)
from orchestration_platform.governance.rbac.abac.abstractions.policy_repository import (
    IAbacPolicyRepository,
)
from orchestration_platform.governance.rbac.abac.models import AbacCondition, AbacDecision, AbacPolicy

_logger = logging.getLogger(__name__)


class AbacPolicyEngine:
    """ABAC 策略决策引擎。

    构造注入策略仓储与属性求值器，执行 ABAC 策略决策。
    """

    def __init__(
        self,
        policy_repository: IAbacPolicyRepository,
        attribute_evaluator: IAttributeEvaluator,
    ) -> None:
        """初始化 ABAC 策略引擎。

        参数：
            policy_repository：ABAC 策略仓储
            attribute_evaluator：属性求值器
        """
        self._repository = policy_repository
        self._evaluator = attribute_evaluator

    async def decide(
        self,
        request: GovernanceRequest,
    ) -> AbacDecision:
        """执行 ABAC 策略决策。

        流程：加载租户策略→无策略返回 is_no_policy→逐条求值→首个匹配决定效果。
        """
        tenant_id = request.tenant_id or ""
        policies = await self._repository.load_by_tenant(tenant_id)

        # 无策略：默认允许（保持纯 RBAC 行为一致）
        if not policies:
            return AbacDecision(
                effect=PolicyEffect.ALLOW,
                is_no_policy=True,
            )

        # 逐条策略求值（按 priority 排序，首个匹配的决定效果）
        for policy in policies:
            match_result = await self._evaluate_policy(policy, request)
            if match_result is not None:
                return match_result

        # 全部策略均未匹配：默认允许
        return AbacDecision(
            effect=PolicyEffect.ALLOW,
            is_no_policy=False,
        )

    async def _evaluate_policy(
        self,
        policy: AbacPolicy,
        request: GovernanceRequest,
    ) -> AbacDecision | None:
        """评估单条策略。

        返回 AbacDecision 表示策略匹配，返回 None 表示未匹配。
        属性求值失败按 fail_mode 处理。
        """
        eval_errors: List[str] = []
        all_conditions_match = True

        for condition in policy.conditions:
            try:
                attr_value = await self._evaluator.evaluate(
                    attribute_name=condition.attribute_name,
                    attribute_source=condition.attribute_source,
                    request=request,
                )
                if not self._match_condition(attr_value, condition):
                    all_conditions_match = False
                    break
            except ValueError as exc:
                eval_errors.append(str(exc))
                # 属性求值失败按 fail_mode 处理
                if policy.fail_mode == FailMode.DENY:
                    return AbacDecision(
                        effect=PolicyEffect.DENY,
                        matched_policy_id=policy.policy_id,
                        eval_errors=eval_errors,
                    )
                else:
                    all_conditions_match = False
                    break

        if all_conditions_match:
            return AbacDecision(
                effect=policy.effect,
                matched_policy_id=policy.policy_id,
                eval_errors=eval_errors,
            )

        return None

    def _match_condition(
        self,
        attr_value: str,
        condition: AbacCondition,
    ) -> bool:
        """检查属性值是否匹配条件。"""
        operator = condition.operator
        expected = condition.expected_value

        if operator == ConditionOperator.EQ:
            return attr_value == expected
        elif operator == ConditionOperator.NEQ:
            return attr_value != expected
        elif operator == ConditionOperator.IN:
            expected_set = {s.strip() for s in expected.split(",")}
            return attr_value in expected_set
        elif operator == ConditionOperator.NOT_IN:
            expected_set = {s.strip() for s in expected.split(",")}
            return attr_value not in expected_set
        elif operator == ConditionOperator.RANGE:
            return self._match_range(attr_value, expected)
        elif operator == ConditionOperator.IP_IN_RANGE:
            return self._match_ip_range(attr_value, expected)
        elif operator == ConditionOperator.TIME_IN_RANGE:
            return self._match_time_range(attr_value, expected)
        else:
            _logger.warning("未知操作符: %s", operator)
            return False

    def _match_range(self, value: str, expected: str) -> bool:
        """数值范围匹配：expected 格式 'min,max'。"""
        try:
            parts = expected.split(",")
            if len(parts) != 2:
                return False
            min_val = float(parts[0].strip())
            max_val = float(parts[1].strip())
            return min_val <= float(value) <= max_val
        except (ValueError, TypeError):
            return False

    def _match_ip_range(self, value: str, expected: str) -> bool:
        """IP 网段匹配：expected 格式 'CIDR'（如 '192.168.1.0/24'）。"""
        try:
            import ipaddress
            ip = ipaddress.ip_address(value)
            network = ipaddress.ip_network(expected, strict=False)
            return ip in network
        except (ValueError, TypeError):
            return False

    def _match_time_range(self, value: str, expected: str) -> bool:
        """时间范围匹配：expected 格式 'HH:MM-HH:MM'，value 为当前时间 ISO 格式。"""
        try:
            from datetime import datetime
            parts = expected.split("-")
            if len(parts) != 2:
                return False
            start_parts = parts[0].split(":")
            start_hour = int(start_parts[0])
            start_min = int(start_parts[1]) if len(start_parts) > 1 else 0
            end_parts = parts[1].split(":")
            end_hour = int(end_parts[0])
            end_min = int(end_parts[1]) if len(end_parts) > 1 else 0

            current = datetime.fromisoformat(value)
            current_minutes = current.hour * 60 + current.minute
            start_minutes = start_hour * 60 + start_min
            end_minutes = end_hour * 60 + end_min

            return start_minutes <= current_minutes <= end_minutes
        except (ValueError, TypeError, IndexError):
            return False
