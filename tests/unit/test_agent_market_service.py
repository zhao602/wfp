# =============================================================================
# Agent 市场服务层单测（tests/unit/test_agent_market_service.py）
#
# 覆盖（T17.3 / T7 验收，design §2.2.2 E-1~E-6 / §2.2.4 错误码 / DC-12 降级矩阵）：
#   1. 全量列表：内置模板派生条目 ∪ 仓储条目合并去重（内置优先）；
#   2. 关键字 / 主分类 / 标签 / 状态筛选；
#   3. 分类计数（planning 1 / starter 1 / collaboration 1 / quality 1 / autonomy 1）；
#   4. 注册：created / replaced / 缺字段（MARKET_ENTRY_INVALID）/ 内置不可覆盖
#      （MARKET_ENTRY_BUILTIN_IMMUTABLE）/ 仓储不可写（MARKET_STORAGE_UNAVAILABLE）；
#   5. 仓储异常 → 降级仅内置条目（接口层仍 2xx，服务层断言不抛）；
#   6. 详情：preset / template_ref 附加与 404。
# =============================================================================

from typing import Any, Dict, List, Optional

import pytest

from orchestration_platform.domain.errors import (
    DomainError,
    InstanceStateConflictError,
    ResourceNotFoundError,
    WorkflowValidationError,
)
from orchestration_platform.market.agent_market_service import (
    AgentMarketRegisterResult,
    AgentMarketService,
)
from orchestration_platform.market.agent_templates import AGENT_TEMPLATES
from orchestration_platform.market.models import (
    AgentMarketEntry,
    AgentMarketEntryDetail,
    MarketEntryStatus,
)


class _FakeAgentMarketRepository:
    """内存版 IAgentMarketRepository 测试替身（可注入异常）。"""

    def __init__(self) -> None:
        self.entries: Dict[str, AgentMarketEntry] = {}
        self.list_all_error: Optional[Exception] = None
        self.save_error: Optional[Exception] = None
        self.exists_error: Optional[Exception] = None

    async def save(self, entry: AgentMarketEntry) -> None:
        if self.save_error is not None:
            raise self.save_error
        self.entries[entry.entry_id] = entry

    async def get(self, entry_id: str) -> Optional[AgentMarketEntry]:
        return self.entries.get(entry_id)

    async def list_all(self) -> List[AgentMarketEntry]:
        if self.list_all_error is not None:
            raise self.list_all_error
        return list(self.entries.values())

    async def exists(self, entry_id: str) -> bool:
        if self.exists_error is not None:
            raise self.exists_error
        return entry_id in self.entries


def _make_registered_entry(
    entry_id: str = "team.retrieval_agent",
    name: str = "检索增强 Agent",
    **overrides: Any,
) -> AgentMarketEntry:
    """构造仓储注册条目（对齐 design §2.2.2 E-4 请求样例）。"""
    payload: Dict[str, Any] = dict(
        entry_id=entry_id,
        name=name,
        description="结合知识库检索的问答 Agent",
        category="collaboration",
        tags=["collaboration", "retrieval"],
        status=MarketEntryStatus.DRAFT,
        source_kind="registered",
        preset_ref=None,
        tool_refs=["get_instance"],
    )
    payload.update(overrides)
    return AgentMarketEntry(**payload)


def _builtin_ids() -> List[str]:
    return [f"builtin.{t.template_id}" for t in AGENT_TEMPLATES]


class TestBuiltinDerivedEntries:
    """内置模板派生条目（design E-1 样例对齐）。"""

    async def test_list_entries_returns_five_builtin_entries_with_empty_repo(self) -> None:
        service = AgentMarketService(repository=_FakeAgentMarketRepository())
        entries = await service.list_entries()
        assert len(entries) == 5
        assert sorted(e.entry_id for e in entries) == sorted(_builtin_ids())

    async def test_builtin_entry_fields_match_design_example(self) -> None:
        service = AgentMarketService(repository=_FakeAgentMarketRepository())
        entries = {e.entry_id: e for e in await service.list_entries()}
        entry = entries["builtin.plan_and_execute"]
        assert entry.category == "planning"
        assert entry.tags == ["planning", "recipe", "human_review"]
        assert entry.description == "规划产出经人工评审门审阅后逐步执行的配方型 Agent 配置预设"
        assert entry.status == MarketEntryStatus.BUILTIN
        assert entry.source_kind == "builtin"
        assert entry.preset_ref == "plan_and_execute"
        assert entry.tool_refs == []  # recipe 化后顶层 allowed_tools 为空（各节点独立工具集）
        assert entry.created_at == entry.updated_at

    async def test_builtin_entry_ids_cover_all_templates(self) -> None:
        service = AgentMarketService(repository=_FakeAgentMarketRepository())
        entries = await service.list_entries()
        assert {e.entry_id for e in entries} == set(_builtin_ids())

    async def test_builtin_tool_refs_match_template_allowed_tools(self) -> None:
        service = AgentMarketService(repository=_FakeAgentMarketRepository())
        entries = {e.entry_id: e for e in await service.list_entries()}
        for template in AGENT_TEMPLATES:
            entry = entries[f"builtin.{template.template_id}"]
            assert entry.tool_refs == template.allowed_tools
            assert entry.name == template.name


class TestMergedEntries:
    """内置 ∪ 仓储合并去重（T7.1：内置优先、entry_id 去重）。"""

    async def test_repository_entries_merged_after_builtin(self) -> None:
        repository = _FakeAgentMarketRepository()
        repository.entries["team.retrieval_agent"] = _make_registered_entry()
        service = AgentMarketService(repository=repository)
        entries = await service.list_entries()
        assert len(entries) == 6
        assert "team.retrieval_agent" in {e.entry_id for e in entries}

    async def test_repository_entry_with_builtin_id_shadowed_by_builtin(self) -> None:
        repository = _FakeAgentMarketRepository()
        repository.entries["builtin.plan_and_execute"] = _make_registered_entry(
            entry_id="builtin.plan_and_execute",
            name="伪造的内置条目",
            source_kind="registered",
        )
        service = AgentMarketService(repository=repository)
        entries = {e.entry_id: e for e in await service.list_entries()}
        assert len(entries) == 5
        assert entries["builtin.plan_and_execute"].source_kind == "builtin"
        assert entries["builtin.plan_and_execute"].name == "Plan-and-Execute"


class TestListEntriesFilters:
    """关键字 / 主分类 / 标签 / 状态筛选（design E-1 Query 参数）。"""

    async def test_query_filter_matches_name(self) -> None:
        service = AgentMarketService(repository=_FakeAgentMarketRepository())
        entries = await service.list_entries(query="ReAct")
        assert {e.entry_id for e in entries} == {"builtin.basic_react"}

    async def test_query_filter_matches_entry_id_and_description(self) -> None:
        service = AgentMarketService(repository=_FakeAgentMarketRepository())
        by_id = await service.list_entries(query="builtin.plan_and_execute")
        assert {e.entry_id for e in by_id} == {"builtin.plan_and_execute"}
        by_description = await service.list_entries(query="批判节点")
        assert {e.entry_id for e in by_description} == {"builtin.reflexive_agent"}

    async def test_category_filter_matches_primary_category(self) -> None:
        service = AgentMarketService(repository=_FakeAgentMarketRepository())
        entries = await service.list_entries(category="planning")
        assert {e.entry_id for e in entries} == {"builtin.plan_and_execute"}

    async def test_tag_filter_matches_tags_membership(self) -> None:
        service = AgentMarketService(repository=_FakeAgentMarketRepository())
        # 差异化定位后仅 multi_agent / autonomous_loop 保留通用 "template" 标签
        entries = await service.list_entries(tag="template")
        assert {e.entry_id for e in entries} == {"builtin.multi_agent", "builtin.autonomous_loop"}
        knowledge_base = await service.list_entries(tag="knowledge_base")
        assert {e.entry_id for e in knowledge_base} == {"builtin.multi_agent"}

    async def test_status_filter_exact_match(self) -> None:
        repository = _FakeAgentMarketRepository()
        repository.entries["team.retrieval_agent"] = _make_registered_entry()
        service = AgentMarketService(repository=repository)
        builtin_only = await service.list_entries(status=MarketEntryStatus.BUILTIN)
        assert len(builtin_only) == 5
        draft_only = await service.list_entries(status=MarketEntryStatus.DRAFT)
        assert {e.entry_id for e in draft_only} == {"team.retrieval_agent"}

    async def test_combined_filters_are_conjunctive(self) -> None:
        service = AgentMarketService(repository=_FakeAgentMarketRepository())
        entries = await service.list_entries(category="starter", tag="react")
        assert {e.entry_id for e in entries} == {"builtin.basic_react"}


class TestListCategories:
    """分类计数（差异化定位：planning/starter/collaboration/quality/autonomy 各 1）。"""

    async def test_list_categories_counts_builtin_only(self) -> None:
        service = AgentMarketService(repository=_FakeAgentMarketRepository())
        categories = await service.list_categories()
        assert {c.category: c.count for c in categories} == {
            "planning": 1,
            "starter": 1,
            "collaboration": 1,
            "quality": 1,
            "autonomy": 1,
        }
        assert sum(c.count for c in categories) == 5

    async def test_list_categories_sorted_deterministically(self) -> None:
        service = AgentMarketService(repository=_FakeAgentMarketRepository())
        categories = await service.list_categories()
        names = [c.category for c in categories]
        assert names == sorted(names)

    async def test_list_categories_includes_registered_entries(self) -> None:
        repository = _FakeAgentMarketRepository()
        repository.entries["team.retrieval_agent"] = _make_registered_entry()
        service = AgentMarketService(repository=repository)
        categories = await service.list_categories()
        assert {c.category: c.count for c in categories}["collaboration"] == 2


class TestGetEntry:
    """条目详情（design E-2：preset / template_ref 附加与 404）。"""

    async def test_get_entry_builtin_returns_detail_with_preset_and_template_ref(self) -> None:
        service = AgentMarketService(repository=_FakeAgentMarketRepository())
        detail = await service.get_entry("builtin.plan_and_execute")
        assert isinstance(detail, AgentMarketEntryDetail)
        assert detail.preset is not None
        assert "规划" in detail.preset.system_prompt
        assert detail.preset.available_tools == []  # planner 节点仅产出计划不执行工具
        assert detail.preset.max_tool_rounds == 2
        assert detail.template_ref == "/api/v1/agent-market/templates/plan_and_execute"

    async def test_get_entry_unknown_raises_not_found(self) -> None:
        service = AgentMarketService(repository=_FakeAgentMarketRepository())
        with pytest.raises(ResourceNotFoundError) as exc_info:
            await service.get_entry("missing.entry")
        assert exc_info.value.code == "MARKET_ENTRY_NOT_FOUND"
        assert exc_info.value.details == {"entry_id": "missing.entry"}

    async def test_get_entry_registered_without_preset_ref(self) -> None:
        repository = _FakeAgentMarketRepository()
        repository.entries["team.retrieval_agent"] = _make_registered_entry()
        service = AgentMarketService(repository=repository)
        detail = await service.get_entry("team.retrieval_agent")
        assert detail.preset is None
        assert detail.template_ref is None

    async def test_get_entry_registered_with_unknown_preset_ref_not_resolved(self) -> None:
        repository = _FakeAgentMarketRepository()
        repository.entries["team.custom"] = _make_registered_entry(
            entry_id="team.custom",
            preset_ref="nonexistent_template",
        )
        service = AgentMarketService(repository=repository)
        detail = await service.get_entry("team.custom")
        assert detail.preset is None
        assert detail.template_ref is None


class TestRegisterEntry:
    """条目注册（design E-4：created / replaced / 校验 / 内置不可覆盖 / 存储不可用）。"""

    async def test_register_entry_created(self) -> None:
        repository = _FakeAgentMarketRepository()
        service = AgentMarketService(repository=repository)
        result = await service.register_entry(
            {
                "entry_id": "team.retrieval_agent",
                "name": "检索增强 Agent",
                "description": "结合知识库检索的问答 Agent",
                "category": "collaboration",
                "tags": ["collaboration", "retrieval"],
                "tool_refs": ["get_instance"],
            }
        )
        assert isinstance(result, AgentMarketRegisterResult)
        assert result.result == "created"
        assert result.entry_id == "team.retrieval_agent"
        assert result.source_kind == "registered"
        assert result.status == MarketEntryStatus.DRAFT
        assert result.created_at == result.updated_at
        assert "team.retrieval_agent" in repository.entries

    async def test_register_entry_replaced_overwrites_existing(self) -> None:
        repository = _FakeAgentMarketRepository()
        service = AgentMarketService(repository=repository)
        first = await service.register_entry(
            {"entry_id": "team.retrieval_agent", "name": "检索增强 Agent"}
        )
        assert first.result == "created"
        second = await service.register_entry(
            {"entry_id": "team.retrieval_agent", "name": "检索增强 Agent v2"}
        )
        assert second.result == "replaced"
        assert "覆盖" in second.message
        assert second.name == "检索增强 Agent v2"
        assert len(repository.entries) == 1
        assert repository.entries["team.retrieval_agent"].name == "检索增强 Agent v2"

    async def test_register_entry_missing_entry_id_rejected(self) -> None:
        service = AgentMarketService(repository=_FakeAgentMarketRepository())
        with pytest.raises(WorkflowValidationError) as exc_info:
            await service.register_entry({"name": "检索增强 Agent"})
        assert exc_info.value.code == "MARKET_ENTRY_INVALID"

    async def test_register_entry_missing_name_rejected(self) -> None:
        service = AgentMarketService(repository=_FakeAgentMarketRepository())
        with pytest.raises(WorkflowValidationError) as exc_info:
            await service.register_entry({"entry_id": "team.retrieval_agent"})
        assert exc_info.value.code == "MARKET_ENTRY_INVALID"

    async def test_register_entry_blank_identifiers_rejected(self) -> None:
        service = AgentMarketService(repository=_FakeAgentMarketRepository())
        with pytest.raises(WorkflowValidationError):
            await service.register_entry({"entry_id": "   ", "name": "x"})
        with pytest.raises(WorkflowValidationError):
            await service.register_entry({"entry_id": "x", "name": ""})

    async def test_register_entry_invalid_status_rejected(self) -> None:
        service = AgentMarketService(repository=_FakeAgentMarketRepository())
        with pytest.raises(WorkflowValidationError) as exc_info:
            await service.register_entry(
                {"entry_id": "team.x", "name": "x", "status": "bogus"}
            )
        assert exc_info.value.code == "MARKET_ENTRY_INVALID"

    async def test_register_entry_explicit_status_preserved(self) -> None:
        repository = _FakeAgentMarketRepository()
        service = AgentMarketService(repository=repository)
        result = await service.register_entry(
            {"entry_id": "team.x", "name": "x", "status": "available"}
        )
        assert result.status == MarketEntryStatus.AVAILABLE

    async def test_register_entry_builtin_id_immutable(self) -> None:
        service = AgentMarketService(repository=_FakeAgentMarketRepository())
        with pytest.raises(InstanceStateConflictError) as exc_info:
            await service.register_entry(
                {"entry_id": "builtin.plan_and_execute", "name": "伪造内置条目"}
            )
        assert exc_info.value.code == "MARKET_ENTRY_BUILTIN_IMMUTABLE"
        with pytest.raises(InstanceStateConflictError):
            await service.register_entry(
                {"entry_id": "builtin.basic_react", "name": "伪造内置条目"}
            )

    async def test_register_entry_invalid_tool_refs_wrapped_as_invalid(self) -> None:
        service = AgentMarketService(repository=_FakeAgentMarketRepository())
        with pytest.raises(WorkflowValidationError) as exc_info:
            await service.register_entry(
                {"entry_id": "team.x", "name": "x", "tool_refs": [""]}
            )
        assert exc_info.value.code == "MARKET_ENTRY_INVALID"

    async def test_register_entry_save_error_storage_unavailable(self) -> None:
        repository = _FakeAgentMarketRepository()
        repository.save_error = RuntimeError("disk full")
        service = AgentMarketService(repository=repository)
        with pytest.raises(DomainError) as exc_info:
            await service.register_entry({"entry_id": "team.x", "name": "x"})
        assert exc_info.value.code == "MARKET_STORAGE_UNAVAILABLE"

    async def test_register_entry_exists_error_storage_unavailable(self) -> None:
        repository = _FakeAgentMarketRepository()
        repository.exists_error = RuntimeError("io error")
        service = AgentMarketService(repository=repository)
        with pytest.raises(DomainError) as exc_info:
            await service.register_entry({"entry_id": "team.x", "name": "x"})
        assert exc_info.value.code == "MARKET_STORAGE_UNAVAILABLE"

    async def test_register_entry_explicit_null_fields_use_defaults(self) -> None:
        repository = _FakeAgentMarketRepository()
        service = AgentMarketService(repository=repository)
        result = await service.register_entry(
            {
                "entry_id": "team.x",
                "name": "x",
                "description": None,
                "category": None,
                "tags": None,
                "status": None,
                "preset_ref": None,
                "tool_refs": None,
            }
        )
        assert result.description == ""
        assert result.category == "general"
        assert result.tags == []
        assert result.status == MarketEntryStatus.DRAFT
        assert result.tool_refs == []


class TestRepositoryReadDegradation:
    """仓储读取降级（T7.5 / DC-12：list_all 异常 → warning + 仓储分支 []，不抛）。"""

    async def test_list_all_error_degrades_to_builtin_only(self) -> None:
        repository = _FakeAgentMarketRepository()
        repository.entries["team.retrieval_agent"] = _make_registered_entry()
        repository.list_all_error = RuntimeError("storage unavailable")
        service = AgentMarketService(repository=repository)
        entries = await service.list_entries()
        assert {e.entry_id for e in entries} == set(_builtin_ids())

    async def test_get_entry_still_finds_builtin_under_list_all_error(self) -> None:
        repository = _FakeAgentMarketRepository()
        repository.list_all_error = RuntimeError("storage unavailable")
        service = AgentMarketService(repository=repository)
        detail = await service.get_entry("builtin.basic_react")
        assert detail.preset is not None

    async def test_get_entry_unknown_still_404_under_list_all_error(self) -> None:
        repository = _FakeAgentMarketRepository()
        repository.list_all_error = RuntimeError("storage unavailable")
        service = AgentMarketService(repository=repository)
        with pytest.raises(ResourceNotFoundError):
            await service.get_entry("team.retrieval_agent")

    async def test_list_categories_degrades_to_builtin_counts(self) -> None:
        repository = _FakeAgentMarketRepository()
        repository.list_all_error = RuntimeError("storage unavailable")
        service = AgentMarketService(repository=repository)
        categories = await service.list_categories()
        assert sum(c.count for c in categories) == 5


class TestTemplateAccess:
    """模板只读访问（design E-5 / E-6：清单恒 5；未知 id → 404）。"""

    def test_list_templates_returns_five_in_preset_order(self) -> None:
        service = AgentMarketService(repository=_FakeAgentMarketRepository())
        templates = service.list_templates()
        assert len(templates) == 5
        assert [t.template_id for t in templates] == [
            t.template_id for t in AGENT_TEMPLATES
        ]

    def test_get_template_known(self) -> None:
        service = AgentMarketService(repository=_FakeAgentMarketRepository())
        template = service.get_template("multi_agent")
        assert template.name == "Multi-Agent"
        assert template.kind == "recipe"

    def test_get_template_unknown_raises_not_found(self) -> None:
        service = AgentMarketService(repository=_FakeAgentMarketRepository())
        with pytest.raises(ResourceNotFoundError) as exc_info:
            service.get_template("no_such_template")
        assert exc_info.value.code == "MARKET_TEMPLATE_NOT_FOUND"
