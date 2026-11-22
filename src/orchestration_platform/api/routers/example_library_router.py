# =============================================================================
# 范例库路由（api/routers/example_library_router.py）
#
# 职责：将既有范例库服务（application/example_library_service.py）暴露为只读 REST 接口，
#       供前端范例库面板列出预置范例、按标识加载工作流定义。
# 设计意图（对应 spec EX-*、design 2.3）：
#   1. 只读复用——不新增业务逻辑，不重复实现 YAML 读取/解析/依赖校验；
#   2. 契约对齐——响应字段与前端 orchestrationApiClient 契约逐字段一致
#      （列表 {examples:[...]}；详情响应体即工作流定义对象本身、无外壳）；
#   3. 就近错误映射——异常在路由层映射为统一 {code, message, details} 结构，
#      不改动全局异常处理器；
#   4. 最小侵入——不改既有服务、不改既有错误码集合、不新增第三方依赖。
# 端点：
#   GET /examples         —— 列出全部预置范例（含可用性状态）
#   GET /examples/{id}    —— 加载指定范例的工作流定义
# =============================================================================

from typing import List, Optional, Union

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from orchestration_platform.application.example_library_service import (
    ExampleLibraryService,
)
from orchestration_platform.domain.errors import (
    ExampleDependencyMissingError,
    WorkflowValidationError,
)
from orchestration_platform.domain.models.workflow_definition import (
    WorkflowDefinition,
)

# 模块级路由器：不设自定义 prefix（前缀由应用工厂统一注入 /api/v1）
router = APIRouter(tags=["范例库"])

# 模块级服务单例：复用既有服务缺省构造，无副作用，不在导入期扫描注册表
_service = ExampleLibraryService()

# 未登记标识 / YAML 文件缺失 / 路径穿越被拒 的统一错误码。
# 为满足最小侵入，不写入 errors.py 既有错误码集合（BC-07），在本模块私有承载。
_EXAMPLE_NOT_FOUND = "EXAMPLE_NOT_FOUND"


class ExampleEntryResponse(BaseModel):
    """范例条目响应模型：字段与前端契约逐字段一致（EX-03）。"""

    # from_attributes：直接承接既有服务的 ExampleEntry dataclass
    model_config = ConfigDict(from_attributes=True)

    example_id: str
    name: str
    yaml_path: str
    available: bool
    missing_dependencies: List[str]
    unavailable_reason: Optional[str] = None


class ListExamplesResponse(BaseModel):
    """列表顶层响应：含且仅含 examples 字段（EX-02）。"""

    examples: List[ExampleEntryResponse]


@router.get("/examples", response_model=ListExamplesResponse)
async def list_examples() -> ListExamplesResponse:
    """列出全部预置范例及其可用性状态。

    单次调用既有服务；可用性由服务内部三级检查（文件/环境变量/YAML）兜底，
    路由层不改写、不中断。纯只读，无状态写入。
    """
    entries = _service.list_examples()
    return ListExamplesResponse(
        examples=[ExampleEntryResponse.model_validate(entry) for entry in entries]
    )


@router.get("/examples/{example_id}", response_model=WorkflowDefinition)
async def get_example(
    example_id: str,
) -> Union[WorkflowDefinition, JSONResponse]:
    """加载指定范例的工作流定义。

    成功时直接返回领域对象本身（响应体即 WorkflowDefinition，无 definition 外壳）。
    错误映射（就近，统一 {code, message, details} 结构）：
      · 未登记 / 文件缺失 / 路径穿越被拒 → 404 EXAMPLE_NOT_FOUND
      · 依赖环境变量缺失               → 422 EXAMPLE_DEPENDENCY_MISSING
      · YAML 解析 / 校验失败            → 422（复用 exc.code，WF_*）
    """
    try:
        # 复用既有服务：路径穿越与注册表外取值由 load_example 首行白名单校验兜底
        return _service.load_example(example_id)
    except FileNotFoundError:
        # 不暴露越界文件内容或内部路径细节（SEC-04）
        return JSONResponse(
            status_code=404,
            content={
                "code": _EXAMPLE_NOT_FOUND,
                "message": f"范例不存在: {example_id}",
                "details": {"example_id": example_id},
            },
        )
    except ExampleDependencyMissingError as exc:
        # 必须显式捕获：其继承链 ExampleLibraryError→DomainError 会被全局处理器
        # 映射为 500，与要求的 422 冲突，故在路由层就近映射（design 2.4.2）
        return JSONResponse(status_code=422, content=exc.to_dict())
    except WorkflowValidationError as exc:
        # 复用既有 WF_* 错误码与 details（YAML 解析/校验失败）
        return JSONResponse(status_code=422, content=exc.to_dict())