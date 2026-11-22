# =============================================================================
# 绑定与候选单测（tests/unit/test_vector_kb_management_binding.py）
#
# 覆盖（CHG-21 / design §2.6.3、测试覆盖表 1445 行）：
#   - 一库一模型单值必填（创建未绑定 → VECTOR_KB_EMBEDDING_BINDING_REQUIRED）；
#   - 多对一（一模型可绑多库，AC-43）；
#   - 绑定优先于节点字段（AC-51，三路径一致 AC-53/INV-D22）；
#   - 绑定缺失确定性回退 + 提示（AC-52）；
#   - 候选响应零凭据（AC-44/AC-45，白名单结构）；
#   - 改绑维度冲突前置拒绝（INV-D24/AC-41）+ 匹配放行 + 同绑定幂等无探测。
# 测试纪律：全同步用例（禁 asyncio.run）；真实仓储 + 注入式
# dimension_probe / vector_service 桩（NV-02/NV-03 沙箱不可验证项走 seam）。
# =============================================================================

import json
from typing import List, Optional

import pytest

from orchestration_platform.domain.errors import (
    VECTOR_KB_DIMENSION_BINDING_CONFLICT,
    VECTOR_KB_EMBEDDING_BINDING_REQUIRED,
    VECTOR_KB_KNOWLEDGE_BASE_NOT_FOUND,
    DomainError,
)
from orchestration_platform.domain.models.node_configurations import (
    ModelLoadingConfig,
    VectorKnowledgeBaseNodeConfiguration,
)
from orchestration_platform.engine.nodes.vector_knowledge_base_node_executor import (
    VectorKnowledgeBaseNodeExecutor,
)
from orchestration_platform.integrations.vector_kb_management import (
    EmbeddingCandidateItem,
    KBEmbeddingBinding,
    VectorKbManagementConfig,
    VectorKbManagementService,
)
from orchestration_platform.integrations.vector_kb_management.repository import (
    SqliteKnowledgeBaseRepository,
)
from orchestration_platform.integrations.vector_knowledge_base.local_backend import (
    LocalKnowledgeBaseRetriever,
)
from orchestration_platform.integrations.vector_knowledge_base.retriever import (
    HttpKnowledgeBaseRetriever,
)


class _FakeDiagnosis:
    """诊断报告桩：服务层改绑前置校验只读 declared_dimension。"""

    def __init__(self, declared_dimension: Optional[int]) -> None:
        self.declared_dimension = declared_dimension


class _FakeVectorService:
    """vector_service 桩：记录 diagnose 调用（改绑校验触达证据）。"""

    def __init__(self, declared_dimension: Optional[int]) -> None:
        self._declared_dimension = declared_dimension
        self.diagnose_calls: List[str] = []

    def diagnose(self, collection_id: str) -> _FakeDiagnosis:
        self.diagnose_calls.append(collection_id)
        return _FakeDiagnosis(declared_dimension=self._declared_dimension)


class _RecordingRepository(SqliteKnowledgeBaseRepository):
    """真实仓储 + save 计数（resolve_binding 零写入证据）。"""

    def __init__(self, storage_root: str) -> None:
        super().__init__(storage_root)
        self.save_calls: List[str] = []

    def save(self, knowledge_base) -> None:
        self.save_calls.append(knowledge_base.knowledge_base_id)
        super().save(knowledge_base)


def _local_binding(identifier: str = "local-main-model") -> KBEmbeddingBinding:
    return KBEmbeddingBinding(mode="local", model_identifier=identifier)


def _api_binding(
    identifier: str = "api-embed-model",
    base_url: str = "https://embed.example.internal/v1",
) -> KBEmbeddingBinding:
    return KBEmbeddingBinding(
        mode="api", model_identifier=identifier, base_url=base_url
    )


def _make_service(
    tmp_path,
    repository=None,
    vector_service=None,
    dimension_probe=None,
) -> VectorKbManagementService:
    return VectorKbManagementService(
        config=VectorKbManagementConfig(
            storage_root=str(tmp_path / "kb_meta")
        ),
        repository=repository,
        vector_service=vector_service,
        dimension_probe=dimension_probe,
    )


def _node_config(
    embedding_model: Optional[ModelLoadingConfig],
    knowledge_base_id: str = "kb_docs",
) -> VectorKnowledgeBaseNodeConfiguration:
    return VectorKnowledgeBaseNodeConfiguration(
        knowledge_base_id=knowledge_base_id,
        query_value="查询文本",
        embedding_model=embedding_model,
    )


# ---------------------------------------------------------------------------
# 1. 单值必填（AC-40 / INV-D17）
# ---------------------------------------------------------------------------


def test_create_knowledge_base_requires_single_embedding_binding(tmp_path):
    """未绑定 → VECTOR_KB_EMBEDDING_BINDING_REQUIRED（单值必填，AC-40）。"""
    service = _make_service(tmp_path)

    with pytest.raises(DomainError) as exc_info:
        service.create_knowledge_base("kb_docs", "产品文档库", None)

    assert exc_info.value.code == VECTOR_KB_EMBEDDING_BINDING_REQUIRED


def test_create_knowledge_base_rejects_blank_model_identifier(tmp_path):
    """绑定标识空白 → 同码确定性拒绝（spec §5.5.3-2）。"""
    service = _make_service(tmp_path)

    with pytest.raises(DomainError) as exc_info:
        service.create_knowledge_base(
            "kb_docs",
            "产品文档库",
            KBEmbeddingBinding(mode="local", model_identifier="   "),
        )

    assert exc_info.value.code == VECTOR_KB_EMBEDDING_BINDING_REQUIRED


# ---------------------------------------------------------------------------
# 2. 多对一（AC-43）
# ---------------------------------------------------------------------------


def test_one_model_can_bind_multiple_knowledge_bases(tmp_path):
    """同一模型绑定两个知识库均成功（多对一，AC-43，无唯一性约束）。"""
    repository = SqliteKnowledgeBaseRepository(str(tmp_path / "kb_meta"))
    service = _make_service(tmp_path, repository=repository)
    binding = _api_binding()

    first = service.create_knowledge_base(
        "kb_docs", "产品文档库", binding
    )
    second = service.create_knowledge_base(
        "kb_faq", "常见问题库", binding
    )

    assert first.embedding_model.model_identifier == "api-embed-model"
    assert second.embedding_model.model_identifier == "api-embed-model"
    assert (
        service.resolve_binding("kb_docs").model_identifier
        == service.resolve_binding("kb_faq").model_identifier
    )


# ---------------------------------------------------------------------------
# 3. 绑定优先（AC-51）+ 三路径一致（AC-53 / INV-D22）
# ---------------------------------------------------------------------------


def test_binding_takes_priority_over_node_field(tmp_path, monkeypatch):
    """绑定优先：执行器生效模型/检索器以知识库绑定为准（AC-51/AC-53）。

    三路径一致（INV-D22）：后台展示（resolve_binding 摘要）与前台生效
    （执行器绑定解析）同源同读同一仓储实体；节点字段为兼容占位不覆盖。
    """
    repository = SqliteKnowledgeBaseRepository(str(tmp_path / "kb_meta"))
    service = _make_service(tmp_path, repository=repository)
    knowledge_base = service.create_knowledge_base(
        "kb_docs", "产品文档库", _local_binding("bound-main-model")
    )

    # 路径 1：后台展示（去凭据摘要，AC-45）
    summary = service.resolve_binding("kb_docs")
    assert summary.model_identifier == "bound-main-model"
    assert summary == knowledge_base.binding_summary()

    # 路径 2：前台生效（执行器绑定优先，节点字段不覆盖）
    monkeypatch.setattr(
        VectorKnowledgeBaseNodeExecutor,
        "_try_resolve_binding",
        lambda self, knowledge_base_id: knowledge_base.embedding_model,
    )
    executor = VectorKnowledgeBaseNodeExecutor()
    config = _node_config(
        embedding_model=ModelLoadingConfig(
            mode="api",
            model_identifier="node-legacy-model",
            base_url="https://legacy.example.internal/v1",
        )
    )

    effective_model = executor._resolve_effective_model(config)
    assert effective_model is not None
    assert effective_model.model_identifier == "bound-main-model"
    assert effective_model.mode == "local"

    # 检索器选择同样以绑定为准（绑定 local > 字段 api，AC-25/AC-26）
    assert isinstance(executor._resolve_retriever(config), LocalKnowledgeBaseRetriever)


# ---------------------------------------------------------------------------
# 4. 缺失回退 + 提示（AC-52）
# ---------------------------------------------------------------------------


def test_missing_binding_falls_back_to_node_field(tmp_path, monkeypatch):
    """绑定缺失（异常数据）→ 确定性回退节点字段；管理侧可见 404 提示。"""
    repository = SqliteKnowledgeBaseRepository(str(tmp_path / "kb_meta"))
    service = _make_service(tmp_path, repository=repository)
    monkeypatch.setattr(
        VectorKnowledgeBaseNodeExecutor,
        "_try_resolve_binding",
        lambda self, knowledge_base_id: None,
    )
    executor = VectorKnowledgeBaseNodeExecutor()
    legacy_field = ModelLoadingConfig(
        mode="api",
        model_identifier="node-legacy-model",
        base_url="https://legacy.example.internal/v1",
    )
    config = _node_config(
        embedding_model=legacy_field, knowledge_base_id="ghost_kb"
    )

    # 确定性回退：生效模型 = 既有节点字段（AC-52）
    effective_model = executor._resolve_effective_model(config)
    assert effective_model is not None
    assert effective_model.model_identifier == "node-legacy-model"
    assert isinstance(
        executor._resolve_retriever(config), HttpKnowledgeBaseRetriever
    )

    # 可见提示语义：管理侧解析给确定性 404（绑定缺失可诊断，不静默）
    with pytest.raises(DomainError) as exc_info:
        service.resolve_binding("ghost_kb")
    assert exc_info.value.code == VECTOR_KB_KNOWLEDGE_BASE_NOT_FOUND
    assert "ghost_kb" in exc_info.value.message


def test_resolve_binding_is_read_only_zero_write(tmp_path):
    """resolve_binding 零写入（只读语义：不触发 save）。"""
    repository = _RecordingRepository(str(tmp_path / "kb_meta"))
    service = _make_service(tmp_path, repository=repository)
    service.create_knowledge_base("kb_docs", "产品文档库", _local_binding())
    repository.save_calls.clear()

    summary = service.resolve_binding("kb_docs")

    assert summary.model_identifier == "local-main-model"
    assert repository.save_calls == []


# ---------------------------------------------------------------------------
# 5. 候选零凭据（AC-44 / AC-45）
# ---------------------------------------------------------------------------


def test_embedding_candidates_response_is_zero_credential(tmp_path):
    """候选响应零凭据：字段白名单结构 + 序列化无 base_url/api_key。"""
    service = _make_service(tmp_path)
    response = service.list_embedding_candidates()

    # 字段白名单（结构性质：候选条目仅 model_identifier/mode/label）
    assert set(EmbeddingCandidateItem.model_fields.keys()) == {
        "model_identifier",
        "mode",
        "label",
    }

    serialized = json.dumps(response.model_dump(), ensure_ascii=False)
    assert "base_url" not in serialized
    assert "api_key" not in serialized

    # 候选为空 → allow_manual_input=true（手工填写，spec §5.5.3-1）
    assert response.total == len(response.candidates)
    if not response.candidates:
        assert response.allow_manual_input is True


# ---------------------------------------------------------------------------
# 6. 改绑维度冲突（INV-D24 / AC-41）
# ---------------------------------------------------------------------------


def test_rebinding_dimension_conflict_is_rejected_upfront(tmp_path):
    """新模型维度 ≠ 集合固化维度 → 前置拒绝且原绑定不被改写。"""
    repository = SqliteKnowledgeBaseRepository(str(tmp_path / "kb_meta"))
    vector_service = _FakeVectorService(declared_dimension=64)
    probe_calls: List[str] = []

    def probe(binding: KBEmbeddingBinding) -> Optional[int]:
        probe_calls.append(binding.model_identifier)
        return 32  # 新模型真实维度 32 ≠ 集合固化维度 64

    service = _make_service(
        tmp_path,
        repository=repository,
        vector_service=vector_service,
        dimension_probe=probe,
    )
    service.create_knowledge_base("kb_docs", "产品文档库", _local_binding())

    with pytest.raises(DomainError) as exc_info:
        service.update_knowledge_base(
            "kb_docs", embedding_model=_api_binding("small-embed-model")
        )

    assert exc_info.value.code == VECTOR_KB_DIMENSION_BINDING_CONFLICT
    assert "32" in exc_info.value.message
    assert "64" in exc_info.value.message
    # 前置拒绝证据：诊断/探测触达 + 原绑定未被静默改写
    assert vector_service.diagnose_calls == ["kb_docs"]
    assert probe_calls == ["small-embed-model"]
    stored = repository.get("kb_docs")
    assert stored is not None
    assert stored.embedding_model.model_identifier == "local-main-model"


def test_rebinding_matching_dimension_succeeds(tmp_path):
    """新模型维度 == 集合固化维度 → 改绑成功落盘（INV-D24 通过路径）。"""
    repository = SqliteKnowledgeBaseRepository(str(tmp_path / "kb_meta"))
    vector_service = _FakeVectorService(declared_dimension=64)

    def probe(binding: KBEmbeddingBinding) -> Optional[int]:
        return 64

    service = _make_service(
        tmp_path,
        repository=repository,
        vector_service=vector_service,
        dimension_probe=probe,
    )
    service.create_knowledge_base("kb_docs", "产品文档库", _local_binding())

    updated = service.update_knowledge_base(
        "kb_docs", embedding_model=_api_binding("big-embed-model")
    )

    assert updated.embedding_model.model_identifier == "big-embed-model"
    stored = repository.get("kb_docs")
    assert stored is not None
    assert stored.embedding_model.model_identifier == "big-embed-model"


def test_rebinding_same_binding_skips_dimension_probe(tmp_path):
    """同绑定（mode + 标识一致）→ 幂等保存，无维度探测开销。"""
    repository = SqliteKnowledgeBaseRepository(str(tmp_path / "kb_meta"))
    vector_service = _FakeVectorService(declared_dimension=64)
    probe_calls: List[str] = []

    def probe(binding: KBEmbeddingBinding) -> Optional[int]:
        probe_calls.append(binding.model_identifier)
        return 64

    service = _make_service(
        tmp_path,
        repository=repository,
        vector_service=vector_service,
        dimension_probe=probe,
    )
    service.create_knowledge_base("kb_docs", "产品文档库", _local_binding())

    service.update_knowledge_base(
        "kb_docs", embedding_model=_local_binding()
    )

    assert probe_calls == []
    assert vector_service.diagnose_calls == []
    stored = repository.get("kb_docs")
    assert stored is not None
    assert stored.embedding_model.model_identifier == "local-main-model"
