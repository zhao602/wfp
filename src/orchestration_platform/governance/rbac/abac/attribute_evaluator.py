# =============================================================================
# 属性求值器实现（governance/rbac/abac/attribute_evaluator.py）
#
# 职责：在 ABAC 策略决策时对动态属性进行实时求值。
# 设计意图（对应偏差修正 spec 5.2.1.2 / design 模块B AttributeEvaluator）：
#   - 主体属性：从请求上下文获取用户标识、租户标识、用户元数据
#   - 资源属性：从请求上下文获取资源类型、资源标识、资源元数据
#   - 环境属性：实时求值（当前时间、请求 IP、租户标签）
# =============================================================================

import logging
from datetime import datetime, timezone
from typing import Any

from orchestration_platform.domain.models.enums import AttributeSource
from orchestration_platform.governance.interceptors.base import GovernanceRequest
from orchestration_platform.governance.rbac.abac.abstractions.attribute_evaluator import (
    IAttributeEvaluator,
)

_logger = logging.getLogger(__name__)


class AttributeEvaluator:
    """ABAC 属性求值器。

    支持主体、资源、环境三类属性的实时求值。
    """

    async def evaluate(
        self,
        attribute_name: str,
        attribute_source: AttributeSource,
        request: GovernanceRequest,
    ) -> str:
        """求值单个属性。

        异常映射：抛出 ValueError（含 ATTR_EVAL_FAILED 语义）当属性不可求值。
        """
        try:
            if attribute_source == AttributeSource.SUBJECT:
                return self._evaluate_subject_attribute(attribute_name, request)
            elif attribute_source == AttributeSource.RESOURCE:
                return self._evaluate_resource_attribute(attribute_name, request)
            elif attribute_source == AttributeSource.ENVIRONMENT:
                return self._evaluate_environment_attribute(attribute_name, request)
            else:
                raise ValueError(f"ATTR_EVAL_FAILED: 未知属性来源 {attribute_source}")
        except Exception as exc:
            _logger.error("属性求值失败: %s.%s - %s", attribute_source, attribute_name, exc)
            raise ValueError(f"ATTR_EVAL_FAILED: {attribute_source}.{attribute_name} - {exc}") from exc

    def _evaluate_subject_attribute(
        self,
        attribute_name: str,
        request: GovernanceRequest,
    ) -> str:
        """求值主体属性。"""
        if attribute_name == "user_id":
            return request.user_id or ""
        if attribute_name == "tenant_id":
            return request.tenant_id or ""
        if attribute_name == "role":
            return str(request.metadata.get("role", ""))
        if attribute_name == "department":
            return str(request.metadata.get("department", ""))
        # 从元数据中获取自定义主体属性
        if attribute_name in request.metadata:
            return str(request.metadata[attribute_name])
        raise ValueError(f"未知主体属性: {attribute_name}")

    def _evaluate_resource_attribute(
        self,
        attribute_name: str,
        request: GovernanceRequest,
    ) -> str:
        """求值资源属性。"""
        if attribute_name == "resource_type":
            return request.resource_type.value
        if attribute_name == "resource_id":
            return request.instance_id or ""
        if attribute_name == "operation":
            return request.operation.value
        if attribute_name == "sensitivity_level":
            return str(request.metadata.get("sensitivity_level", "normal"))
        # 从载荷中获取自定义资源属性
        if attribute_name in request.payload:
            return str(request.payload[attribute_name])
        raise ValueError(f"未知资源属性: {attribute_name}")

    def _evaluate_environment_attribute(
        self,
        attribute_name: str,
        request: GovernanceRequest,
    ) -> str:
        """求值环境属性（实时计算）。"""
        if attribute_name == "current_time":
            return datetime.now(timezone.utc).isoformat()
        if attribute_name == "current_hour":
            return str(datetime.now(timezone.utc).hour)
        if attribute_name == "request_ip":
            return str(request.metadata.get("client_ip", "0.0.0.0"))
        if attribute_name == "is_weekend":
            return str(datetime.now(timezone.utc).weekday() >= 5)
        if attribute_name == "tenant_label":
            return str(request.metadata.get("tenant_label", ""))
        raise ValueError(f"未知环境属性: {attribute_name}")
