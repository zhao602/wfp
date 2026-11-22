# =============================================================================
# 模型候选路由（api/routers/model_catalog_router.py）
#
# 职责：暴露 GET /api/v1/models/available 只读模型候选端点（agent_model_selector）。
# 设计意图（agent_model_selector spec FR-1~FR-5 / design DC-01、DC-02）：
#   1. 候选仅由 settings 即时派生（主/备模型标识），单一来源零重复定义（INV-D11）；
#   2. 非空过滤 + 去重保序（primary 优先）；两来源皆空返回空列表（降级契约，INV-D12）；
#   3. 响应字段白名单：仅 model_identifier / source / label，不读取、不返回
#      任何凭据类配置字段（INV-D08 凭据零泄露）；
#   4. 仅注册 GET（写方法 405）；不实现鉴权——沿用既有 RbacAuthorizationMiddleware
#      鉴权链（与 tool_catalog_router 同构，INV-D02）。
# =============================================================================

from typing import List, Literal, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from orchestration_platform.configuration.settings import Settings, get_settings

router = APIRouter(tags=["模型候选"])


class AvailableModelItem(BaseModel):
    """单条模型候选：标识 + 来源标注（响应字段白名单，无凭据字段）。"""

    model_identifier: str = Field(description="模型标识（单值 string，回填 model_identifier）")
    source: Literal["primary", "fallback"] = Field(description="候选来源标注")
    label: Optional[str] = Field(default=None, description="展示标签（可选，缺省不返回）")


class AvailableModelsResponse(BaseModel):
    """模型候选响应：models + total。"""

    models: List[AvailableModelItem] = Field(default_factory=list, description="模型候选清单")
    total: int = Field(default=0, description="候选总数")


def _derive_available_models(settings: Settings) -> List[AvailableModelItem]:
    """由 settings 派生模型候选（非空过滤 + 去重保序，primary 优先）。

    四种输入（design TC-B1~TC-B4）：
        主备均配置 → 2 项；仅主配置 → 1 项（source=primary）；
        主备同标识 → 去重 1 项（primary 优先）；皆空 → 空列表。
    """
    items: List[AvailableModelItem] = []
    seen: set = set()
    for source, identifier in (
        ("primary", settings.llm_primary_model),
        ("fallback", settings.llm_fallback_model),
    ):
        normalized = (identifier or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        items.append(AvailableModelItem(model_identifier=normalized, source=source))
    return items


@router.get(
    "/models/available",
    response_model=AvailableModelsResponse,
    response_model_exclude_none=True,
)
async def list_available_models() -> AvailableModelsResponse:
    """返回运行环境已配置的模型候选（只读，请求期即时派生）。"""
    items = _derive_available_models(get_settings())
    return AvailableModelsResponse(models=items, total=len(items))
