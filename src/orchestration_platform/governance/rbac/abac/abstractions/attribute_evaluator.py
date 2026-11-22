# =============================================================================
# 属性求值器抽象接口（governance/rbac/abac/abstractions/attribute_evaluator.py）
#
# 职责：定义 ABAC 属性求值的统一契约。
# 设计意图（对应 design 2.2.2.2 IAttributeEvaluator）：
#   - 输入：属性名 + 属性来源 + 请求上下文
#   - 输出：属性取值
# =============================================================================

from typing import Protocol, runtime_checkable

from orchestration_platform.domain.models.enums import AttributeSource
from orchestration_platform.governance.interceptors.base import GovernanceRequest


@runtime_checkable
class IAttributeEvaluator(Protocol):
    """属性求值器抽象接口。"""

    async def evaluate(
        self,
        attribute_name: str,
        attribute_source: AttributeSource,
        request: GovernanceRequest,
    ) -> str:
        """求值单个属性。

        前置条件：attribute_name 在策略中声明且可求值。
        后置条件：返回属性取值（字符串表示）。
        异常映射：ATTR_EVAL_FAILED（属性数据源不可用）。
        """
        ...
