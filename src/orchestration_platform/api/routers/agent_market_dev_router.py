# =============================================================================
# Agent 市场用户自主开发 API 路由（api/routers/agent_market_dev_router.py）
#
# 职责（agent_market_user_development T3/T12 / design §2.2.2 DV-1~DV-5 / DC-01/DC-03）：
#   1. 开发脚手架只读访问：DV-1 清单 / DV-2 详情
#      （五要素全覆盖 + file_templates[].content 内联 + fetch_hint）；
#   2. 提交受理链路：DV-3 POST /dev-submissions（multipart，流式落盘不整体读入
#      内存）/ DV-4 提交清单（筛选 + 只读降级空清单仍 2xx）/ DV-5 提交详情；
#   3. 审核链路（T16 / design §2.2.2 DV-6~DV-8 / DC-04）：DV-6 状态流转
#      （operator_id 必填；to_state 非法 422；终态/非法流转 409；
#      APPROVED→PUBLISHED 上架失败 500 保持 APPROVED 可重试）/
#      DV-7 重新校验（报告追加不覆盖，AC-DEV-16）/ DV-8 流转历史（有序）；
#   4. 只读端点仅注册 GET，写端点仅注册 POST（其余写方法自动 405，T3.3）；
#   5. 不提供 ZIP 下载端点（DC-01：内容模板内联下发，前端复制/下载文本）；
#   6. 响应白名单：显式 pydantic 模型逐字段构造，禁 **dict 透传（INV-DEV-13）；
#      响应不含 api_key / base_url / 超时类凭据字段。
# 装配：application_factory 按 agent_market_dev_enabled 条件注册（T4.2/DC-07），
#       开关关闭时本模块不加载，端点访问 404。
# 可选依赖：DV-3/DV-6/DV-7 为 Form/File 端点，FastAPI 装饰期硬校验
#       python-multipart；缺失时按 CHG-DEP 范式条件注册（端点不注册 → 404 降级，
#       对齐 vector_kb_management_router.MULTIPART_AVAILABLE），只读端点不受影响。
# =============================================================================

import tempfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, Query, UploadFile

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.errors import (
    MARKET_DEV_LIMIT_EXCEEDED,
    MARKET_DEV_SCAFFOLD_NOT_FOUND,
    MARKET_DEV_SUBMISSION_INVALID,
    ResourceNotFoundError,
    WorkflowValidationError,
)
from orchestration_platform.domain.models.enums import ContributionState
from orchestration_platform.market.agent_market_service import AgentMarketService
from orchestration_platform.market.dev_models import (
    DevHistoryResponse,
    DevScaffoldDetailResponse,
    DevScaffoldListResponse,
    DevScaffoldSummary,
    DevSubmissionDetailResponse,
    DevSubmissionListResponse,
    DevSubmissionResponse,
    DevTransitionResponse,
    DevValidationReport,
)
from orchestration_platform.market.dev_scaffold import (
    get_scaffold,
    list_scaffolds,
)
from orchestration_platform.market.dev_submission_service import (
    DevSubmissionService,
)
from orchestration_platform.persistence.repository_factory import RepositoryFactory

# 模块级路由器：前缀 /agent-market（/api/v1 由应用工厂统一注入）
router = APIRouter(prefix="/agent-market", tags=["Agent 市场·用户自主开发"])

# python-multipart 可用性探测（可选依赖；缺失 → DV-3/DV-6/DV-7 写端点不注册，
# 访问 404 降级；范式对齐 vector_kb_management_router.MULTIPART_AVAILABLE）
try:
    import python_multipart  # noqa: F401

    MULTIPART_AVAILABLE = True
except ImportError:
    MULTIPART_AVAILABLE = False

_UPLOAD_CHUNK_BYTES = 1024 * 1024  # 1MB 分块累计（不整体读入内存，DC-03）


def _build_service() -> DevSubmissionService:
    """按请求构造受理服务（仓储经工厂按 storage_backend 创建；上架衔接注入市场服务）。"""
    repository = RepositoryFactory.create_dev_submission_repository()
    market_service = AgentMarketService(
        RepositoryFactory.create_agent_market_repository()
    )
    return DevSubmissionService(
        repository=repository, market_service=market_service
    )


@router.get("/dev-templates", response_model=DevScaffoldListResponse)
async def list_dev_templates() -> DevScaffoldListResponse:
    """DV-1：开发脚手架清单（标识/名称/版本/摘要/必需文件，AC-DEV-01）。"""
    scaffolds = list_scaffolds()
    summaries = [
        DevScaffoldSummary(
            scaffold_id=scaffold.scaffold_id,
            name=scaffold.name,
            version=scaffold.version,
            summary=scaffold.summary,
            required_files=list(scaffold.required_files),
        )
        for scaffold in scaffolds
    ]
    return DevScaffoldListResponse(scaffolds=summaries, total=len(summaries))


@router.get("/dev-templates/{scaffold_id}", response_model=DevScaffoldDetailResponse)
async def get_dev_template(scaffold_id: str) -> DevScaffoldDetailResponse:
    """DV-2：开发脚手架详情（五要素 + 文件模板内联 + 获取指引，AC-DEV-02/03）。

    未知 scaffold_id → 404 MARKET_DEV_SCAFFOLD_NOT_FOUND（全局异常处理器映射）。
    """
    scaffold = get_scaffold(scaffold_id)
    if scaffold is None:
        raise ResourceNotFoundError(
            MARKET_DEV_SCAFFOLD_NOT_FOUND,
            f"开发脚手架不存在: {scaffold_id}",
        )
    return DevScaffoldDetailResponse(
        scaffold_id=scaffold.scaffold_id,
        name=scaffold.name,
        version=scaffold.version,
        summary=scaffold.summary,
        directory_structure=scaffold.directory_structure,
        required_files=list(scaffold.required_files),
        optional_files=list(scaffold.optional_files),
        manifest_spec=scaffold.manifest_spec,
        interface_contract=scaffold.interface_contract,
        dependency_and_runtime=scaffold.dependency_and_runtime,
        file_templates=list(scaffold.file_templates),
        fetch_hint=scaffold.fetch_hint,
    )


if MULTIPART_AVAILABLE:

    @router.post("/dev-submissions", response_model=DevSubmissionResponse)
    async def submit_dev_package(
        package: UploadFile = File(...),
        developer_id: str = Form(...),
        node_name: str = Form(...),
        version: str = Form(...),
    ) -> DevSubmissionResponse:
        """DV-3：受理用户开发提交（multipart：package(ZIP) + developer_id + node_name + version）。

        错误映射（DC-03/DV-3 表）：缺字段或非 ZIP → 422 SUBMISSION_INVALID；
        超限/防御预检失败 → 422 LIMIT_EXCEEDED；重复 → 409 DUPLICATE；
        存储不可写 → 500；包内容不合规（校验 error）为 200 业务性结果（valid=false）。
        """
        settings = get_settings()
        max_bytes = settings.agent_market_dev_max_package_mb * 1024 * 1024
        temp_path: Optional[str] = None
        try:
            with tempfile.NamedTemporaryFile(
                delete=False, prefix="dev_submission_", suffix=".zip"
            ) as tmp:
                temp_path = tmp.name
                total = 0
                while True:
                    chunk = await package.read(_UPLOAD_CHUNK_BYTES)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise WorkflowValidationError(
                            code=MARKET_DEV_LIMIT_EXCEEDED,
                            message=(
                                "包体超过大小上限 "
                                f"{settings.agent_market_dev_max_package_mb}MB"
                            ),
                            details={
                                "limit_mb": settings.agent_market_dev_max_package_mb
                            },
                        )
                    tmp.write(chunk)
                if total == 0:
                    raise WorkflowValidationError(
                        code=MARKET_DEV_SUBMISSION_INVALID,
                        message="包体不可读：上传内容为空",
                    )
            return await _build_service().submit(
                developer_id=developer_id,
                node_name=node_name,
                version=version,
                zip_path=Path(temp_path),
            )
        finally:
            if temp_path is not None:
                Path(temp_path).unlink(missing_ok=True)


@router.get("/dev-submissions", response_model=DevSubmissionListResponse)
async def list_dev_submissions(
    state: Optional[str] = Query(
        default=None, description="按状态筛选（ContributionState 七态取值）"
    ),
    node_name: Optional[str] = Query(default=None, description="按节点名筛选"),
) -> DevSubmissionListResponse:
    """DV-4：提交清单（state/node_name 筛选；仓储读取异常降级空清单仍 2xx）。"""
    parsed_state: Optional[ContributionState] = None
    if state is not None and state != "":
        try:
            parsed_state = ContributionState(state)
        except ValueError:
            raise WorkflowValidationError(
                code=MARKET_DEV_SUBMISSION_INVALID,
                message=f"state 参数非法（须为 ContributionState 取值）: {state!r}",
                details={"state": state},
            ) from None
    submissions = await _build_service().list_submissions(parsed_state, node_name)
    return DevSubmissionListResponse(
        submissions=submissions, total=len(submissions)
    )


@router.get(
    "/dev-submissions/{submission_id}",
    response_model=DevSubmissionDetailResponse,
)
async def get_dev_submission(submission_id: str) -> DevSubmissionDetailResponse:
    """DV-5：提交详情（主体 + 最新报告 + 来源标注 user_dev）；未知 → 404。"""
    submission = await _build_service().get_submission(submission_id)
    latest_report = (
        submission.validation_reports[-1]
        if submission.validation_reports
        else None
    )
    return DevSubmissionDetailResponse(
        submission=submission,
        latest_report=latest_report,
        source_kind="user_dev",
    )


if MULTIPART_AVAILABLE:

    @router.post(
        "/dev-submissions/{submission_id}/transition",
        response_model=DevTransitionResponse,
    )
    async def transition_dev_submission(
        submission_id: str,
        to_state: str = Form(..., description="目标状态（ContributionState 取值）"),
        operator_id: str = Form(..., description="操作人标识（必填）"),
        reason: str = Form("", description="流转备注（拒绝原因建议填写）"),
    ) -> DevTransitionResponse:
        """DV-6：审核流转（AC-DEV-11/13/20）。

        to_state 非法 → 422 SUBMISSION_INVALID；operator_id 空 → 422；
        终态/非法流转 → 409 INVALID_TRANSITION；未知提交 → 404；
        APPROVED→PUBLISHED 上架失败 → 500 PUBLISH_FAILED（保持 APPROVED 可重试）。
        """
        try:
            parsed_to_state = ContributionState(to_state)
        except ValueError:
            raise WorkflowValidationError(
                code=MARKET_DEV_SUBMISSION_INVALID,
                message=(
                    f"to_state 参数非法（须为 ContributionState 取值）: {to_state!r}"
                ),
                details={"to_state": to_state},
            ) from None
        return await _build_service().transition(
            submission_id=submission_id,
            to_state=parsed_to_state,
            operator_id=operator_id,
            reason=reason,
        )

    @router.post(
        "/dev-submissions/{submission_id}/rescan",
        response_model=DevValidationReport,
    )
    async def rescan_dev_submission(
        submission_id: str,
        operator_id: str = Form(..., description="操作人标识（必填）"),
    ) -> DevValidationReport:
        """DV-7：重新执行三层校验并追加报告（AC-DEV-16；历史报告不覆盖）。

        operator_id 空 → 422；未知提交 → 404；解压包目录缺失 → 500。
        """
        return await _build_service().rescan(
            submission_id=submission_id,
            operator_id=operator_id,
        )


@router.get(
    "/dev-submissions/{submission_id}/history",
    response_model=DevHistoryResponse,
)
async def get_dev_submission_history(submission_id: str) -> DevHistoryResponse:
    """DV-8：流转历史（StateTransitionRecord 序列，按 occurred_at 有序）。

    未知提交 → 404。
    """
    return await _build_service().get_history(submission_id)
