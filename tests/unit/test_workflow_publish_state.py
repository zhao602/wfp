# =============================================================================
# 定义版本与发布态测试（tests/unit/test_workflow_publish_state.py）
#
# 职责：B11 定义版本与发布态批——domain 发布状态机字段、local_file 仓储
#       三新方法（get_latest_version/get_published/publish）与发布态
#       save 守卫（WF_STATE_CONFLICT 写边界强制）行为锁定。
# 设计意图（B11 design D1-D4）：
#   1. 版本模型（用户裁决）：每次保存递增新版本号；draft 同号幂等覆盖；
#      publish 冻结为 published；published 被 save 改写 → 409 异常语义；
#   2. 发布回滚语义 = 发布回滚（旧内容发新号，端点层 drill 见
#      test_workflow_definition_endpoints.py）；
#   3. 旧数据兼容：缺 status 字段的 JSON（v1_10 前）默认 draft——
#      与迁移 DEFAULT 'draft' 口径一致。
# =============================================================================

import json

import pytest

from orchestration_platform.domain.errors import (
    WF_STATE_CONFLICT,
    InstanceStateConflictError,
)
from orchestration_platform.domain.models.enums import NodeType
from orchestration_platform.domain.models.execution_instance import DefinitionRef
from orchestration_platform.domain.models.node_definition import NodeDefinition
from orchestration_platform.domain.models.workflow_definition import (
    WorkflowDefinition,
)


def _make_definition(
    definition_id: str = "wf-publish-state",
    version: int = 1,
    name: str = "发布态测试",
) -> WorkflowDefinition:
    """构造最小可保存工作流定义（单触发器节点）。"""
    return WorkflowDefinition(
        definition_id=definition_id,
        name=name,
        spec_version="1.0",
        definition_version=version,
        nodes=[
            NodeDefinition(
                node_id="trig",
                node_type=NodeType.TRIGGER,
                configuration={"trigger_type": "manual"},
            )
        ],
    )


# =============================================================================
# Domain 发布状态机字段（design D3）
# =============================================================================

class TestWorkflowDefinitionPublishFields:
    def test_publish_field_defaults(self):
        definition = _make_definition()
        assert definition.status == "draft"
        assert definition.draft_of is None
        assert definition.published_at is None

    def test_legacy_json_without_status_defaults_to_draft(self):
        """v1_10 前落盘的 JSON 缺 status/draft_of/published_at → 默认 draft。"""
        legacy = _make_definition().model_dump(mode="json")
        for key in ("status", "draft_of", "published_at"):
            legacy.pop(key)
        definition = WorkflowDefinition.model_validate(legacy)
        assert definition.status == "draft"
        assert definition.draft_of is None
        assert definition.published_at is None

    def test_published_roundtrip_serialization(self):
        definition = _make_definition()
        definition.status = "published"
        definition.draft_of = 1
        definition.published_at = definition.updated_at
        restored = WorkflowDefinition.model_validate_json(
            definition.model_dump_json()
        )
        assert restored.status == "published"
        assert restored.draft_of == 1
        assert restored.published_at is not None


class TestDefinitionRefEnrichment:
    def test_ref_defaults_backward_compatible(self):
        """既有构造点（不传新字段）零破坏：status/published_at 为 None。"""
        ref = DefinitionRef(definition_id="wf", definition_version=1)
        assert ref.status is None
        assert ref.published_at is None

    def test_ref_enrichment_fields_populated(self):
        ref = DefinitionRef(
            definition_id="wf",
            definition_version=2,
            status="published",
            published_at=None,
        )
        dumped = ref.model_dump(mode="json")
        assert dumped["status"] == "published"
        assert dumped["published_at"] is None


# =============================================================================
# local_file 仓储：版本递增依据 / 发布态读取 / publish 迁移 / 守卫（design D4）
# =============================================================================

class TestLocalFileLatestVersion:
    async def test_unknown_definition_returns_none(self, local_workflow_repository):
        assert await local_workflow_repository.get_latest_version("no-such-wf") is None

    async def test_latest_version_tracks_saves(self, local_workflow_repository):
        repo = local_workflow_repository
        assert await repo.get_latest_version("wf-publish-state") is None
        await repo.save(_make_definition(version=1))
        assert await repo.get_latest_version("wf-publish-state") == 1
        await repo.save(_make_definition(version=2))
        assert await repo.get_latest_version("wf-publish-state") == 2


class TestLocalFileGetPublished:
    async def test_none_when_no_versions(self, local_workflow_repository):
        assert await local_workflow_repository.get_published("no-such-wf") is None

    async def test_none_when_only_drafts(self, local_workflow_repository):
        repo = local_workflow_repository
        await repo.save(_make_definition(version=1))
        assert await repo.get_published("wf-publish-state") is None

    async def test_returns_latest_published_skipping_drafts(
        self, local_workflow_repository
    ):
        """v1 published、v2 draft → get_published=v1（get 缺省=v2 语义区分）。"""
        repo = local_workflow_repository
        await repo.save(_make_definition(version=1))
        await repo.publish("wf-publish-state", 1)
        await repo.save(_make_definition(version=2))
        latest = await repo.get("wf-publish-state")
        assert latest.definition_version == 2 and latest.status == "draft"
        published = await repo.get_published("wf-publish-state")
        assert published is not None
        assert published.definition_version == 1
        assert published.status == "published"


class TestLocalFilePublish:
    async def test_publish_unknown_version_404(self, local_workflow_repository):
        with pytest.raises(Exception) as exc_info:
            await local_workflow_repository.publish("no-such-wf", 3)
        assert "不存在" in str(exc_info.value)

    async def test_publish_sets_published_state(self, local_workflow_repository):
        repo = local_workflow_repository
        await repo.save(_make_definition(version=1))
        ref = await repo.publish("wf-publish-state", 1)
        assert ref.status == "published"
        assert ref.published_at is not None
        definition = await repo.get("wf-publish-state", 1)
        assert definition.status == "published"
        assert definition.published_at is not None

    async def test_publish_idempotent_preserves_timestamp(
        self, local_workflow_repository
    ):
        """重复发布幂等返回（非 409），published_at 保持首次发布时刻。"""
        repo = local_workflow_repository
        await repo.save(_make_definition(version=1))
        first = await repo.publish("wf-publish-state", 1)
        second = await repo.publish("wf-publish-state", 1)
        assert second.status == "published"
        assert second.published_at == first.published_at


class TestLocalFilePublishedImmutabilityGuard:
    async def test_draft_same_version_overwrite_allowed(
        self, local_workflow_repository
    ):
        """draft 同号重复保存幂等覆盖（协议既有语义保持）。"""
        repo = local_workflow_repository
        await repo.save(_make_definition(version=1, name="第一版"))
        await repo.save(_make_definition(version=1, name="改写草稿"))
        definition = await repo.get("wf-publish-state", 1)
        assert definition.name == "改写草稿"
        assert definition.status == "draft"

    async def test_save_over_published_raises_state_conflict(
        self, local_workflow_repository
    ):
        """发布态不可变守卫（写边界强制）：published 版本被 save 改写 →
        InstanceStateConflictError(WF_STATE_CONFLICT) → API 面 409。"""
        repo = local_workflow_repository
        await repo.save(_make_definition(version=1))
        await repo.publish("wf-publish-state", 1)
        with pytest.raises(InstanceStateConflictError) as exc_info:
            await repo.save(
                _make_definition(version=1, name="改写已发布版本")
            )
        assert exc_info.value.code == WF_STATE_CONFLICT
        assert exc_info.value.details["definition_version"] == 1
        # 落盘内容未被改写（守卫拒绝路径零副作用）
        definition = await repo.get("wf-publish-state", 1)
        assert definition.name == "发布态测试"
        assert definition.status == "published"

    async def test_new_version_save_after_publish_allowed(
        self, local_workflow_repository
    ):
        """published v1 后保存 v2 草稿合法（每次保存递增模型）。"""
        repo = local_workflow_repository
        await repo.save(_make_definition(version=1))
        await repo.publish("wf-publish-state", 1)
        ref = await repo.save(_make_definition(version=2))
        assert ref.definition_version == 2
        assert ref.status == "draft"


class TestLocalFileListVersionsEnrichment:
    async def test_list_versions_carries_status(self, local_workflow_repository):
        repo = local_workflow_repository
        await repo.save(_make_definition(version=1))
        await repo.publish("wf-publish-state", 1)
        await repo.save(_make_definition(version=2))
        refs = await repo.list_versions("wf-publish-state")
        assert [r.definition_version for r in refs] == [2, 1]
        by_version = {r.definition_version: r for r in refs}
        assert by_version[1].status == "published"
        assert by_version[1].published_at is not None
        assert by_version[2].status == "draft"
        assert by_version[2].published_at is None

    async def test_list_versions_legacy_json_backfills_draft(
        self, local_workflow_repository, tmp_storage_root
    ):
        """v1_10 前旧 JSON（缺 status）→ list_versions 回填 draft。"""
        import pathlib

        def_dir = (
            pathlib.Path(tmp_storage_root)
            / "workflow_definitions"
            / "wf-legacy"
        )
        def_dir.mkdir(parents=True)
        legacy = _make_definition(
            definition_id="wf-legacy", version=1
        ).model_dump(mode="json")
        for key in ("status", "draft_of", "published_at"):
            legacy.pop(key)
        (def_dir / "v1.json").write_text(
            json.dumps(legacy, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        refs = await local_workflow_repository.list_versions("wf-legacy")
        assert len(refs) == 1
        assert refs[0].status == "draft"
        assert refs[0].published_at is None
