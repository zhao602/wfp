# =============================================================================
# Agent 市场服务层（market/agent_market_service.py）
#
# 职责（agent_tool_marketplace T7 / design §2.2.2 E-1~E-5 / DC-12）：
#   1. 条目清单：内置模板派生条目 ∪ 仓储注册条目（内置优先、entry_id 去重）；
#   2. 条目详情：preset_ref 指向内置模板时附加 preset 与 template_ref（不内联五维度全文）；
#   3. 条目注册：元数据校验 → 内置条目不可覆盖（409）→ 已注册条目覆盖语义（created/replaced）；
#   4. 分类计数（按主分类聚合）与模板只读访问（清单恒 5，AC-12）；
#   5. 仓储读取降级：list_all() 异常 → logging.warning + 仓储分支返回 []（接口仍 2xx）。
# 设计意图：
#   1. 错误码按 design §2.2.4（常量已由 T11.2 追加至 domain/errors.py，服务层统一引用常量）；
#   2. 不引入事务管理器（单写操作幂等覆盖，design DC-09）；
#   3. 响应白名单：显式 pydantic 模型逐字段构造，禁止 **dict 透传（INV-D08）。
# =============================================================================

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import Field, ValidationError

from orchestration_platform.domain.errors import (
    DomainError,
    InstanceStateConflictError,
    MARKET_ENTRY_BUILTIN_IMMUTABLE,
    MARKET_ENTRY_INVALID,
    MARKET_ENTRY_NOT_FOUND,
    MARKET_STORAGE_UNAVAILABLE,
    MARKET_TEMPLATE_NOT_FOUND,
    ResourceNotFoundError,
    WorkflowValidationError,
)
from orchestration_platform.market.agent_templates import (
    AGENT_TEMPLATES,
    AgentTemplate,
    get_template as get_builtin_template,
    list_templates as list_builtin_templates,
)
from orchestration_platform.market.models import (
    AgentMarketEntry,
    AgentMarketEntryDetail,
    MarketCategoryCount,
    MarketEntryStatus,
    TemplatePreset,
)
from orchestration_platform.persistence.abstractions.market_repositories import (
    IAgentMarketRepository,
)

_logger = logging.getLogger(__name__)

# 内置派生条目 entry_id 前缀（builtin.{template_id}，design §2.2.2 E-1）
_BUILTIN_ENTRY_ID_PREFIX = "builtin."

# 内置模板派生条目市场元数据（差异化定位：五模板分类互斥；键=template_id）
_BUILTIN_DERIVED_META: Dict[str, Dict[str, Any]] = {
    "plan_and_execute": {
        "description": "规划产出经人工评审门审阅后逐步执行的配方型 Agent 配置预设",
        "category": "planning",
        "tags": ["planning", "recipe", "human_review"],
    },
    "basic_react": {
        "description": "推理与行动交替的最简单环 ReAct Agent 配置预设（入门基线）",
        "category": "starter",
        "tags": ["starter", "react", "quickstart"],
    },
    "multi_agent": {
        "description": "多智能体分工协作（含知识库检索分支）的配方型 Agent 配置预设",
        "category": "collaboration",
        "tags": ["collaboration", "multi_agent", "knowledge_base", "template"],
    },
    "reflexive_agent": {
        "description": "生成后由独立批判节点审稿再修订的配方型 Agent 配置预设",
        "category": "quality",
        "tags": ["quality", "reflexive", "critique"],
    },
    "autonomous_loop": {
        "description": "自主循环推进（跨轮上下文压缩）直至退出条件的配方型 Agent 配置预设",
        "category": "autonomy",
        "tags": ["autonomy", "loop", "memory", "template"],
    },
}


def _now() -> datetime:
    """UTC 当前时间（注册时 created_at/updated_at 共用同一时刻）。"""
    return datetime.now(timezone.utc)


def _payload_value(payload: Dict[str, Any], key: str, default: Any) -> Any:
    """读取注册载荷字段（显式 null 视同缺省，避免 None 穿透校验）。"""
    value = payload.get(key)
    return default if value is None else value


class AgentMarketRegisterResult(AgentMarketEntry):
    """Agent 市场条目注册结果（design §2.2.2 E-4）：注册后条目 + created/replaced 语义。"""

    result: Literal["created", "replaced"] = Field(
        description="注册结果：新建（created）/ 覆盖既有注册条目（replaced）"
    )
    message: str = Field(
        default="", description="注册结果说明（覆盖时提示覆盖语义）"
    )


class AgentMarketService:
    """Agent 市场服务：内置模板派生条目 ∪ 仓储注册条目的只读目录与注册写入门面。"""

    def __init__(self, repository: IAgentMarketRepository) -> None:
        self._repository = repository

    # ------------------------------------------------------------------
    # 条目清单与详情（E-1 / E-2）
    # ------------------------------------------------------------------

    async def list_entries(
        self,
        query: Optional[str] = None,
        category: Optional[str] = None,
        tag: Optional[str] = None,
        status: Optional[MarketEntryStatus] = None,
    ) -> List[AgentMarketEntry]:
        """列出市场条目（内置 ∪ 仓储，内置优先去重），支持关键字/主分类/标签/状态筛选。"""
        entries = await self._merged_entries()
        if query:
            keyword = query.lower()
            entries = [
                entry
                for entry in entries
                if keyword in entry.name.lower()
                or keyword in entry.entry_id.lower()
                or keyword in entry.description.lower()
            ]
        if category:
            entries = [entry for entry in entries if entry.category == category]
        if tag:
            entries = [entry for entry in entries if tag in entry.tags]
        if status is not None:
            entries = [entry for entry in entries if entry.status == status]
        return entries

    async def get_entry(self, entry_id: str) -> AgentMarketEntryDetail:
        """查询单个市场条目详情。

        preset_ref 指向内置模板时附加 preset 与 template_ref（不内联五维度全文，T7.2）。

        Raises:
            ResourceNotFoundError: entry_id 不存在（MARKET_ENTRY_NOT_FOUND / 404）。
        """
        for entry in await self._merged_entries():
            if entry.entry_id == entry_id:
                return self._to_detail(entry)
        raise ResourceNotFoundError(
            code=MARKET_ENTRY_NOT_FOUND,
            message=f"市场条目不存在：{entry_id}",
            details={"entry_id": entry_id},
        )

    # ------------------------------------------------------------------
    # 条目注册（E-4）
    # ------------------------------------------------------------------

    async def register_entry(self, payload: Dict[str, Any]) -> AgentMarketRegisterResult:
        """注册市场条目元数据（同标识已注册条目按覆盖语义更新，result 区分 created/replaced）。

        Raises:
            WorkflowValidationError: 缺 entry_id/name 或 status 非法（MARKET_ENTRY_INVALID / 422）。
            InstanceStateConflictError: 覆盖内置条目（MARKET_ENTRY_BUILTIN_IMMUTABLE / 409）。
            DomainError: 仓储不可写（MARKET_STORAGE_UNAVAILABLE / 500）。
        """
        raw_entry_id = payload.get("entry_id")
        raw_name = payload.get("name")
        entry_id = raw_entry_id.strip() if isinstance(raw_entry_id, str) else ""
        name = raw_name.strip() if isinstance(raw_name, str) else ""
        if not entry_id or not name:
            raise WorkflowValidationError(
                code=MARKET_ENTRY_INVALID,
                message="条目元数据不完整或非法：entry_id 与 name 均为必填",
                details={"entry_id": raw_entry_id, "name": raw_name},
            )

        raw_status = payload.get("status")
        if raw_status is None:
            status = MarketEntryStatus.DRAFT
        else:
            try:
                status = MarketEntryStatus(raw_status)
            except ValueError:
                raise WorkflowValidationError(
                    code=MARKET_ENTRY_INVALID,
                    message=f"条目元数据不完整或非法：status={raw_status}",
                    details={"status": raw_status},
                ) from None

        if entry_id in self._builtin_entry_ids():
            raise InstanceStateConflictError(
                code=MARKET_ENTRY_BUILTIN_IMMUTABLE,
                message=f"内置条目不可修改：{entry_id}",
                details={"entry_id": entry_id},
            )

        now = _now()
        try:
            entry = AgentMarketEntry(
                entry_id=entry_id,
                name=name,
                description=_payload_value(payload, "description", ""),
                category=_payload_value(payload, "category", "general"),
                tags=_payload_value(payload, "tags", []),
                status=status,
                source_kind="registered",
                preset_ref=_payload_value(payload, "preset_ref", None),
                tool_refs=_payload_value(payload, "tool_refs", []),
                created_at=now,
                updated_at=now,
            )
        except ValidationError as exc:
            raise WorkflowValidationError(
                code=MARKET_ENTRY_INVALID,
                message="条目元数据不完整或非法",
                details={"errors": exc.errors()},
            ) from exc

        try:
            existed = await self._repository.exists(entry.entry_id)
        except Exception as storage_exc:  # 仓储不可用（读探测失败同视为不可写）
            raise DomainError(
                code=MARKET_STORAGE_UNAVAILABLE,
                message="市场存储不可用，请稍后重试",
                details={"reason": str(storage_exc)},
            ) from storage_exc
        result: Literal["created", "replaced"] = "replaced" if existed else "created"
        try:
            await self._repository.save(entry)
        except Exception as storage_exc:
            raise DomainError(
                code=MARKET_STORAGE_UNAVAILABLE,
                message="市场存储不可用，请稍后重试",
                details={"reason": str(storage_exc)},
            ) from storage_exc

        message = (
            "同标识条目已存在，已按覆盖语义更新"
            if result == "replaced"
            else "市场条目注册成功"
        )
        return AgentMarketRegisterResult(
            entry_id=entry.entry_id,
            name=entry.name,
            description=entry.description,
            category=entry.category,
            tags=list(entry.tags),
            status=entry.status,
            source_kind=entry.source_kind,
            preset_ref=entry.preset_ref,
            tool_refs=list(entry.tool_refs),
            created_at=entry.created_at,
            updated_at=entry.updated_at,
            result=result,
            message=message,
        )

    # ------------------------------------------------------------------
    # 分类计数与模板只读访问（E-3 / E-5 / E-6）
    # ------------------------------------------------------------------

    async def list_categories(self) -> List[MarketCategoryCount]:
        """分类计数视图（E-3）：按主分类聚合条目数，按分类名排序保证确定性。"""
        counts: Dict[str, int] = {}
        for entry in await self._merged_entries():
            counts[entry.category] = counts.get(entry.category, 0) + 1
        return [
            MarketCategoryCount(category=category, count=counts[category])
            for category in sorted(counts)
        ]

    def list_templates(self) -> List[AgentTemplate]:
        """按预置顺序返回全部内置模板（长度恒 5，AC-12）。"""
        return list_builtin_templates()

    def get_template(self, template_id: str) -> AgentTemplate:
        """按 template_id 查询内置模板详情。

        Raises:
            ResourceNotFoundError: 未知 template_id（MARKET_TEMPLATE_NOT_FOUND / 404）。
        """
        template = get_builtin_template(template_id)
        if template is None:
            raise ResourceNotFoundError(
                code=MARKET_TEMPLATE_NOT_FOUND,
                message=f"模板不存在：{template_id}",
                details={"template_id": template_id},
            )
        return template

    # ------------------------------------------------------------------
    # 私有方法：内置派生 / 合并去重 / 详情转换
    # ------------------------------------------------------------------

    @staticmethod
    def _builtin_entries() -> List[AgentMarketEntry]:
        """内置模板派生条目（数量恒 5，市场元数据取 _BUILTIN_DERIVED_META）。"""
        now = _now()
        entries: List[AgentMarketEntry] = []
        for template in AGENT_TEMPLATES:
            meta = _BUILTIN_DERIVED_META.get(template.template_id, {})
            entries.append(
                AgentMarketEntry(
                    entry_id=f"{_BUILTIN_ENTRY_ID_PREFIX}{template.template_id}",
                    name=template.name,
                    description=str(meta.get("description", "")),
                    category=str(meta.get("category", "general")),
                    tags=list(meta.get("tags", [])),
                    status=MarketEntryStatus.BUILTIN,
                    source_kind="builtin",
                    preset_ref=template.template_id,
                    tool_refs=list(template.allowed_tools),
                    created_at=now,
                    updated_at=now,
                )
            )
        return entries

    @classmethod
    def _builtin_entry_ids(cls) -> List[str]:
        """内置派生条目 entry_id 集合（注册冲突检查用）。"""
        return [entry.entry_id for entry in cls._builtin_entries()]

    async def _merged_entries(self) -> List[AgentMarketEntry]:
        """内置派生条目 ∪ 仓储条目（内置优先、entry_id 去重；仓储异常降级 []，T7.5/DC-12）。"""
        merged: Dict[str, AgentMarketEntry] = {}
        for entry in self._builtin_entries():
            merged[entry.entry_id] = entry
        try:
            repository_entries = await self._repository.list_all()
        except Exception as exc:  # 仓储读取降级：内置条目仍返回（DC-12）
            _logger.warning("Agent 市场仓储 list_all 失败，仓储分支降级为空清单：%s", exc)
            repository_entries = []
        for entry in repository_entries:
            if entry.entry_id in merged:
                _logger.warning(
                    "仓储条目 %s 与内置条目冲突，保留内置条目", entry.entry_id
                )
                continue
            merged[entry.entry_id] = entry
        return list(merged.values())

    @staticmethod
    def _to_detail(entry: AgentMarketEntry) -> AgentMarketEntryDetail:
        """条目 → 详情（preset_ref 指向内置模板时附加 preset 与 template_ref，T7.2）。"""
        preset: Optional[TemplatePreset] = None
        template_ref: Optional[str] = None
        if entry.preset_ref:
            template = get_builtin_template(entry.preset_ref)
            if template is not None:
                preset = template.preset
                template_ref = (
                    f"/api/v1/agent-market/templates/{template.template_id}"
                )
        return AgentMarketEntryDetail(
            entry_id=entry.entry_id,
            name=entry.name,
            description=entry.description,
            category=entry.category,
            tags=list(entry.tags),
            status=entry.status,
            source_kind=entry.source_kind,
            preset_ref=entry.preset_ref,
            tool_refs=list(entry.tool_refs),
            created_at=entry.created_at,
            updated_at=entry.updated_at,
            preset=preset,
            template_ref=template_ref,
        )
