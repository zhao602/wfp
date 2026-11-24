# =============================================================================
# NL 编排 REST API 路由（api/routers/nl_orchestration_router.py）
#
# 职责：暴露自然语言编排端点，接收用户指令并返回生成的 YAML 定义。
# 设计意图（对应偏差修正 spec 5.1 / design 2.2.2.1）：
#   - POST /nl_orchestration/create：从自然语言创建工作流
#   - POST /nl_orchestration/modify：从自然语言修改既有工作流
#   - nl_orchestration_enabled=False 时路由不注册（404）
#   - LLM 客户端统一经 integrations/llm/factory.build_llm_client 构造；
#     主模型配置缺失时返回 error_code=llm_not_configured（降级，非 500）
# =============================================================================

import logging
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from orchestration_platform.application.engine_wiring import build_engine_settings
from orchestration_platform.governance.nl_orchestration.models import NLOrchestrationResult

_logger = logging.getLogger(__name__)

router = APIRouter(tags=["自然语言编排"])


# ---------------- 请求/响应模型 ----------------

class CreateFromNlRequest(BaseModel):
    """创建工作流请求。"""

    directive: str = Field(description="自然语言指令文本")
    tenant_id: str = Field(description="租户标识")
    user_id: str = Field(description="用户标识")


class ModifyFromNlRequest(BaseModel):
    """修改工作流请求。"""

    directive: str = Field(description="自然语言修改指令文本")
    target_yaml: str = Field(description="目标工作流 YAML 文本")
    tenant_id: str = Field(description="租户标识")
    user_id: str = Field(description="用户标识")


class NLOrchestrationResponse(BaseModel):
    """NL 编排响应。"""

    success: bool = Field(description="是否成功")
    yaml_text: Optional[str] = Field(default=None, description="生成的 YAML 文本")
    error_code: Optional[str] = Field(default=None, description="错误码")
    error_message: Optional[str] = Field(default=None, description="错误描述")
    validation_issues: list = Field(default_factory=list, description="校验问题清单")


# ---------------- 服务实例延迟初始化 ----------------

_nl_service = None


def _get_nl_service():
    """延迟初始化 NL 编排服务实例。

    LLM 客户端经 integrations/llm/factory.build_llm_client 构造（唯一权威）：
    主模型配置缺失时返回 None（不抛异常），由端点回明确错误码
    LLM_NOT_CONFIGURED，避免 500。
    """
    global _nl_service
    if _nl_service is not None:
        return _nl_service

    from orchestration_platform.engine.graph_compiler import GraphCompiler
    from orchestration_platform.governance.nl_orchestration.injection_guard import (
        InjectionGuard,
    )
    from orchestration_platform.governance.nl_orchestration.nl_intent_parser import (
        NLIntentParser,
    )
    from orchestration_platform.governance.nl_orchestration.nl_orchestration_service import (
        NLOrchestrationService,
    )
    from orchestration_platform.governance.nl_orchestration.workflow_modifier import (
        WorkflowModifier,
    )
    from orchestration_platform.governance.nl_orchestration.yaml_generator import (
        YamlGenerator,
    )
    from orchestration_platform.integrations.llm.factory import build_llm_client
    from orchestration_platform.workflow.yaml_workflow_parser import (
        YamlWorkflowParser,
    )

    # 构造 LLM 客户端（主备）；配置缺失 → None
    llm_client = build_llm_client()
    if llm_client is None:
        _logger.warning(
            "NL 编排: LLM 客户端不可用（LLM_PRIMARY_BASE_URL/API_KEY/MODEL 未配置）"
        )
        return None

    # 构造 NL 编排服务
    _nl_service = NLOrchestrationService(
        intent_parser=NLIntentParser(llm_client),
        yaml_generator=YamlGenerator(),
        workflow_modifier=WorkflowModifier(),
        injection_guard=InjectionGuard(),
        graph_compiler=GraphCompiler(settings=build_engine_settings()),
        yaml_parser=YamlWorkflowParser(),
    )
    return _nl_service


def _llm_not_configured_response() -> NLOrchestrationResponse:
    """LLM 未配置时的降级响应（明确错误码，非 500）。"""
    return NLOrchestrationResponse(
        success=False,
        error_code="llm_not_configured",
        error_message=(
            "自然语言编排依赖的 LLM 未配置：请设置 "
            "LLM_PRIMARY_BASE_URL / LLM_PRIMARY_API_KEY / LLM_PRIMARY_MODEL"
            "（或 NL_ORCHESTRATION_MODEL 指定模型标识）后重试"
        ),
    )


# ---------------- 端点 ----------------

@router.post("/nl_orchestration/create")
async def create_from_nl(request: CreateFromNlRequest) -> NLOrchestrationResponse:
    """从自然语言指令创建工作流定义。

    流程：注入防护 → LLM 意图解析 → YAML 生成 → 拓扑校验 → 返回结果。
    """
    service = _get_nl_service()
    if service is None:
        return _llm_not_configured_response()
    result: NLOrchestrationResult = await service.create_from_nl(
        directive=request.directive,
        tenant_id=request.tenant_id,
        user_id=request.user_id,
    )
    return _to_response(result)


@router.post("/nl_orchestration/modify")
async def modify_from_nl(request: ModifyFromNlRequest) -> NLOrchestrationResponse:
    """从自然语言修改指令更新既有工作流定义。

    流程：注入防护 → LLM 意图解析 → 修改应用 → 拓扑校验 → 返回结果。
    """
    from orchestration_platform.workflow.yaml_workflow_parser import (
        YamlWorkflowParser,
    )

    # 解析目标工作流 YAML
    parser = YamlWorkflowParser()
    try:
        target_definition = parser.parse(request.target_yaml)
    except Exception as exc:
        return NLOrchestrationResponse(
            success=False,
            error_code="definition_not_found",
            error_message=f"目标工作流 YAML 解析失败: {exc}",
        )

    service = _get_nl_service()
    if service is None:
        return _llm_not_configured_response()
    result: NLOrchestrationResult = await service.modify_from_nl(
        directive=request.directive,
        target_definition=target_definition,
        tenant_id=request.tenant_id,
        user_id=request.user_id,
    )
    return _to_response(result)


def _to_response(result: NLOrchestrationResult) -> NLOrchestrationResponse:
    """将服务结果转换为 API 响应。"""
    validation_issues = []
    if result.validation_report is not None:
        validation_issues = [
            issue.model_dump(mode="json")
            for issue in result.validation_report.issues
        ]

    return NLOrchestrationResponse(
        success=result.success,
        yaml_text=result.yaml_text,
        error_code=result.error_code.value if result.error_code else None,
        error_message=result.error_message,
        validation_issues=validation_issues,
    )
