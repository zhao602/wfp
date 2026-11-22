# TODO(tech-debt): B档候选孤儿（族群 B5），待 APM 链路追踪专项排查确认无调用后清理，见 docs/tech-debt-ledger.md
# =============================================================================
# NL 编排服务抽象接口（governance/nl_orchestration/abstractions/nl_orchestration_service.py）
#
# 职责：定义自然语言编排服务的统一契约。
# 设计意图（对应 design 2.2.2.1 INLOrchestrationService）：
#   - create_from_nl：从自然语言指令创建工作流定义
#   - modify_from_nl：从自然语言修改指令更新既有工作流定义
# =============================================================================

from typing import Optional, Protocol, runtime_checkable

from orchestration_platform.domain.models.workflow_definition import WorkflowDefinition
from orchestration_platform.governance.nl_orchestration.models import NLOrchestrationResult


@runtime_checkable
class INLOrchestrationService(Protocol):
    """自然语言编排服务抽象接口。"""

    async def create_from_nl(
        self,
        directive: str,
        tenant_id: str,
        user_id: str,
    ) -> NLOrchestrationResult:
        """从自然语言指令创建工作流定义。

        前置条件：directive 非空且通过注入防护校验。
        后置条件：返回通过拓扑校验的 YAML 定义，或校验失败提示。
        异常映射：NL_PARSE_FAILED / TOPO_INVALID / INJECTION_DETECTED。
        """
        ...

    async def modify_from_nl(
        self,
        directive: str,
        target_definition: WorkflowDefinition,
        tenant_id: str,
        user_id: str,
    ) -> NLOrchestrationResult:
        """从自然语言修改指令更新既有工作流定义。

        前置条件：target_definition 存在且 directive 通过注入防护。
        后置条件：返回更新后通过拓扑校验的 YAML 定义。
        异常映射：NL_PARSE_FAILED / TOPO_INVALID / NODE_NOT_FOUND。
        """
        ...
