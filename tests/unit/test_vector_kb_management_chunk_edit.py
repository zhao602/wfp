# =============================================================================
# 向量知识库管理分片编辑一致性测试（tests/unit/test_vector_kb_management_chunk_edit.py）
#
# 覆盖（CHG-20 / design T5.4、AC-32~AC-38、INV-D19/D20/D21）：
#   - 草稿不改存储（apply 后集合零变化，AC-38 / INV-D19）；
#   - 合并（相邻 + 确定性拼接，AC-32）/ 拆分（合计一致，AC-33）/
#     编辑（AC-34）/ 非法拦截（AC-35：空文本/超长/非相邻/非法拆分点）；
#   - 仅重算受影响分片（embed 调用计数佐证，AC-36）；
#   - 合并/拆分后依调整结果更新存储记录集合（AC-37）；
#   - 幂等重复确认（不产生重复，INV-D20）；
#   - 失败保留旧记录 + 可重确认恢复（INV-D21）；
#   - 乐观并发冲突确定性拒绝（DC-05/DC-06）；
#   - 多轮未确认编辑跨版本累积（基线 = 最近成功确认快照）。
# 测试纪律：PYTHONPATH=src python -m pytest 本文件 -q；临时 storage_root
# 注入；禁 asyncio.run（全同步 API）；委托调用以真实
# LocalVectorDatabaseService 验证。
# =============================================================================

from typing import List, Optional

import pytest

from orchestration_platform.domain.errors import (
    VECTOR_KB_CHUNK_NOT_FOUND,
    VECTOR_KB_DOCUMENT_NOT_FOUND,
    VECTOR_KB_DRAFT_VERSION_CONFLICT,
    VECTOR_KB_EMBEDDING_UNAVAILABLE,
    VECTOR_KB_INVALID_CHUNK_OPERATION,
    InstanceStateConflictError,
    ResourceNotFoundError,
    WorkflowValidationError,
)
from orchestration_platform.integrations.local_vector_database.config import (
    LocalVectorDatabaseConfig,
)
from orchestration_platform.integrations.local_vector_database.embedding import (
    EmbeddingProvider,
    PROVENANCE_CALLER_SUPPLIED,
)
from orchestration_platform.integrations.local_vector_database.service import (
    LocalVectorDatabaseService,
)
from orchestration_platform.integrations.vector_kb_management import (
    ChunkOperation,
    IngestionPipeline,
    KBEmbeddingBinding,
    KnowledgeBase,
    SqliteKnowledgeBaseRepository,
    VectorKbManagementConfig,
)

CONTENT = "第一段介绍产品定位。\n\n第二段说明使用方法。\n\n第三段列出注意事项。"
PARAGRAPH_1 = "第一段介绍产品定位。"
PARAGRAPH_2 = "第二段说明使用方法。"
PARAGRAPH_3 = "第三段列出注意事项。"
MERGED_12 = PARAGRAPH_1 + "\n\n" + PARAGRAPH_2


class _RecordingProvider(EmbeddingProvider):
    """确定性测试来源（向量由文本校验和派生；记录 embed 调用次数与文本）。"""

    provenance = PROVENANCE_CALLER_SUPPLIED
    dimension = 8

    def __init__(self) -> None:
        self.embedded: List[str] = []

    def embed(self, text: str) -> List[float]:
        self.embedded.append(text)
        seed = sum(ord(character) for character in text)
        # 首维随文本变化 + 常量尾维：不同文本向量不平行（cosine < 1），
        # 相同文本向量一致（cosine = 1），保证 retrieve 排序可断言
        return [float(seed % 97 + 1), 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]


class _SwitchableProvider(EmbeddingProvider):
    """可切换失败来源（模拟来源中途不可达，验证失败不破坏 + 可恢复）。"""

    provenance = PROVENANCE_CALLER_SUPPLIED
    dimension = 8

    def __init__(self) -> None:
        self.fail = False

    def embed(self, text: str) -> List[float]:
        if self.fail:
            raise RuntimeError("模拟 embedding 来源不可达")
        return [1.0] * 8


class _PoisonProvider(EmbeddingProvider):
    """含异常分片来源（POISON 文本返回空向量 → 委托层单条失败 → partial）。"""

    provenance = PROVENANCE_CALLER_SUPPLIED
    dimension = 8

    def embed(self, text: str) -> List[float]:
        if "POISON" in text:
            return []  # 空向量 → VECTOR_DB_INVALID_VECTOR → RecordFailure
        return [1.0] * 8


def _factory_of(provider: Optional[EmbeddingProvider]):
    """固定返回注入 provider 的工厂（None → 模拟无来源）。"""

    def factory(binding, declared_dimension):
        return provider

    return factory


def _make_pipeline(tmp_path, provider=None):
    """构造真实仓储 + 真实 LocalVectorDatabaseService 的流水线（临时落点）。"""
    repository = SqliteKnowledgeBaseRepository(str(tmp_path / "kb_meta"))
    vector_service = LocalVectorDatabaseService(
        config=LocalVectorDatabaseConfig(
            storage_root=str(tmp_path / "vectors")
        )
    )
    pipeline = IngestionPipeline(
        repository=repository,
        vector_service=vector_service,
        config=VectorKbManagementConfig(
            storage_root=str(tmp_path / "kb_meta")
        ),
        provider_factory=_factory_of(provider),
    )
    return pipeline, repository, vector_service


def _create_kb(repository, kb_id="kb_docs", allow_fallback=False):
    knowledge_base = KnowledgeBase(
        knowledge_base_id=kb_id,
        name="测试知识库",
        embedding_model=KBEmbeddingBinding(
            mode="local",
            model_identifier="test-model",
            allow_degraded_fallback=allow_fallback,
        ),
        default_chunk_strategy="paragraph",
    )
    repository.save(knowledge_base)
    return knowledge_base


def _ingest(pipeline, kb_id="kb_docs", content=CONTENT):
    return pipeline.ingest_document(
        knowledge_base_id=kb_id,
        file_name="faq.md",
        data=content.encode("utf-8"),
    )


def _confirmed_document(pipeline, repository, provider):
    """构造已确认导入的三分片文档（返回响应与 chunk_id 前缀工具）。"""
    _create_kb(repository)
    response = _ingest(pipeline)
    pipeline.confirm_import(
        "kb_docs", response.document_id, response.job_id
    )
    return response


def _merge(chunk_ids):
    return ChunkOperation(op="merge", chunk_ids=list(chunk_ids))


def _split(chunk_id, points):
    return ChunkOperation(
        op="split", chunk_id=chunk_id, split_points=list(points)
    )


def _edit(chunk_id, text):
    return ChunkOperation(op="edit", chunk_id=chunk_id, text=text)


# ---------------------------------------------------------------------------
# 草稿不生效（AC-38 / INV-D19）
# ---------------------------------------------------------------------------


def test_draft_edit_does_not_touch_vector_store(tmp_path) -> None:
    """提交调整草稿：存储集合零变化（AC-38 / INV-D19，T5.4「草稿不改存储」）。"""
    provider = _RecordingProvider()
    pipeline, repository, vector_service = _make_pipeline(tmp_path, provider)
    response = _confirmed_document(pipeline, repository, provider)
    before = vector_service.diagnose("kb_docs")
    draft_response = pipeline.apply_chunk_draft(
        "kb_docs",
        response.document_id,
        "v1",
        [_merge([f"{response.document_id}:0001", f"{response.document_id}:0002"])],
    )
    assert draft_response.draft_version == "v2"
    after = vector_service.diagnose("kb_docs")
    assert after.record_count == before.record_count == 3
    assert after.ok is True
    assert len(provider.embedded) == 3  # 草稿阶段零向量化调用


# ---------------------------------------------------------------------------
# 合并 / 拆分 / 编辑（AC-32 / AC-33 / AC-34）与非法拦截（AC-35）
# ---------------------------------------------------------------------------


def test_merge_adjacent_chunks_produces_draft(tmp_path) -> None:
    """合并相邻分片：确定性拼接 "\\n\\n"，保留首分片标识（AC-32）。"""
    provider = _RecordingProvider()
    pipeline, repository, _ = _make_pipeline(tmp_path, provider)
    response = _confirmed_document(pipeline, repository, provider)
    first = f"{response.document_id}:0001"
    second = f"{response.document_id}:0002"
    draft_response = pipeline.apply_chunk_draft(
        "kb_docs", response.document_id, "v1", [_merge([first, second])]
    )
    assert draft_response.draft_version == "v2"
    assert len(draft_response.chunks) == 2
    assert draft_response.chunks[0].chunk_id == first
    assert draft_response.chunks[0].text == MERGED_12
    assert draft_response.chunks[0].index == 0
    assert draft_response.chunks[1].chunk_id == f"{response.document_id}:0003"
    assert draft_response.chunks[1].index == 1


def test_merge_non_adjacent_rejected(tmp_path) -> None:
    """非相邻分片合并 → INVALID_CHUNK_OPERATION（AC-35 确定性拦截）。"""
    provider = _RecordingProvider()
    pipeline, repository, _ = _make_pipeline(tmp_path, provider)
    response = _confirmed_document(pipeline, repository, provider)
    with pytest.raises(WorkflowValidationError) as info:
        pipeline.apply_chunk_draft(
            "kb_docs",
            response.document_id,
            "v1",
            [
                _merge(
                    [
                        f"{response.document_id}:0001",
                        f"{response.document_id}:0003",
                    ]
                )
            ],
        )
    assert info.value.code == VECTOR_KB_INVALID_CHUNK_OPERATION


def test_merge_missing_chunk_rejected(tmp_path) -> None:
    """待合并分片不存在 → CHUNK_NOT_FOUND（404）。"""
    provider = _RecordingProvider()
    pipeline, repository, _ = _make_pipeline(tmp_path, provider)
    response = _confirmed_document(pipeline, repository, provider)
    with pytest.raises(ResourceNotFoundError) as info:
        pipeline.apply_chunk_draft(
            "kb_docs",
            response.document_id,
            "v1",
            [
                _merge(
                    [
                        f"{response.document_id}:0001",
                        f"{response.document_id}:9999",
                    ]
                )
            ],
        )
    assert info.value.code == VECTOR_KB_CHUNK_NOT_FOUND


def test_merge_single_chunk_rejected(tmp_path) -> None:
    """合并指令少于 2 个分片 → INVALID_CHUNK_OPERATION。"""
    provider = _RecordingProvider()
    pipeline, repository, _ = _make_pipeline(tmp_path, provider)
    response = _confirmed_document(pipeline, repository, provider)
    with pytest.raises(WorkflowValidationError) as info:
        pipeline.apply_chunk_draft(
            "kb_docs",
            response.document_id,
            "v1",
            [_merge([f"{response.document_id}:0001"])],
        )
    assert info.value.code == VECTOR_KB_INVALID_CHUNK_OPERATION


def test_merge_result_too_long_rejected(tmp_path) -> None:
    """合并后超长（> 2000）→ INVALID_CHUNK_OPERATION（AC-35）。"""
    provider = _RecordingProvider()
    pipeline, repository, _ = _make_pipeline(tmp_path, provider)
    response = _confirmed_document(pipeline, repository, provider)
    pipeline.apply_chunk_draft(
        "kb_docs",
        response.document_id,
        "v1",
        [_edit(f"{response.document_id}:0001", "甲" * 1990)],
    )
    with pytest.raises(WorkflowValidationError) as info:
        pipeline.apply_chunk_draft(
            "kb_docs",
            response.document_id,
            "v2",
            [
                _merge(
                    [
                        f"{response.document_id}:0001",
                        f"{response.document_id}:0002",
                    ]
                )
            ],
        )
    assert info.value.code == VECTOR_KB_INVALID_CHUNK_OPERATION


def test_split_text_total_preserved(tmp_path) -> None:
    """拆分合计一致：子段拼接 == 原文；子标识派生确定（AC-33）。"""
    provider = _RecordingProvider()
    pipeline, repository, _ = _make_pipeline(tmp_path, provider)
    response = _confirmed_document(pipeline, repository, provider)
    second = f"{response.document_id}:0002"
    draft_response = pipeline.apply_chunk_draft(
        "kb_docs", response.document_id, "v1", [_split(second, [3, 8])]
    )
    assert draft_response.draft_version == "v2"
    assert len(draft_response.chunks) == 5
    pieces = draft_response.chunks[1:4]
    assert [piece.chunk_id for piece in pieces] == [
        f"{second}.1",
        f"{second}.2",
        f"{second}.3",
    ]
    assert "".join(piece.text for piece in pieces) == PARAGRAPH_2
    assert draft_response.chunks[0].text == PARAGRAPH_1
    assert draft_response.chunks[4].text == PARAGRAPH_3


@pytest.mark.parametrize(
    "points",
    [[0], [10], [8, 3], []],
)
def test_split_invalid_points_rejected(tmp_path, points) -> None:
    """非法拆分点（越界/非递增/缺失）→ INVALID_CHUNK_OPERATION（AC-35）。"""
    provider = _RecordingProvider()
    pipeline, repository, _ = _make_pipeline(tmp_path, provider)
    response = _confirmed_document(pipeline, repository, provider)
    with pytest.raises(WorkflowValidationError) as info:
        pipeline.apply_chunk_draft(
            "kb_docs",
            response.document_id,
            "v1",
            [_split(f"{response.document_id}:0002", points)],
        )
    assert info.value.code == VECTOR_KB_INVALID_CHUNK_OPERATION


def test_split_missing_chunk_rejected(tmp_path) -> None:
    """待拆分分片不存在 → CHUNK_NOT_FOUND（404）。"""
    provider = _RecordingProvider()
    pipeline, repository, _ = _make_pipeline(tmp_path, provider)
    response = _confirmed_document(pipeline, repository, provider)
    with pytest.raises(ResourceNotFoundError) as info:
        pipeline.apply_chunk_draft(
            "kb_docs",
            response.document_id,
            "v1",
            [_split(f"{response.document_id}:9999", [3])],
        )
    assert info.value.code == VECTOR_KB_CHUNK_NOT_FOUND


def test_edit_chunk_text_produces_draft(tmp_path) -> None:
    """编辑分片文本：同标识草稿更新（AC-34）。"""
    provider = _RecordingProvider()
    pipeline, repository, _ = _make_pipeline(tmp_path, provider)
    response = _confirmed_document(pipeline, repository, provider)
    third = f"{response.document_id}:0003"
    draft_response = pipeline.apply_chunk_draft(
        "kb_docs", response.document_id, "v1", [_edit(third, "全新注意事项文本。")]
    )
    assert draft_response.draft_version == "v2"
    assert len(draft_response.chunks) == 3
    assert draft_response.chunks[2].chunk_id == third
    assert draft_response.chunks[2].text == "全新注意事项文本。"
    assert draft_response.chunks[0].text == PARAGRAPH_1


@pytest.mark.parametrize(
    "text",
    ["", "   ", "乙" * 2001],
)
def test_edit_blank_or_too_long_rejected(tmp_path, text) -> None:
    """编辑空文本/超长 → INVALID_CHUNK_OPERATION（AC-35 不得静默保存）。"""
    provider = _RecordingProvider()
    pipeline, repository, _ = _make_pipeline(tmp_path, provider)
    response = _confirmed_document(pipeline, repository, provider)
    with pytest.raises(WorkflowValidationError) as info:
        pipeline.apply_chunk_draft(
            "kb_docs",
            response.document_id,
            "v1",
            [_edit(f"{response.document_id}:0003", text)],
        )
    assert info.value.code == VECTOR_KB_INVALID_CHUNK_OPERATION


def test_edit_missing_chunk_rejected(tmp_path) -> None:
    """待编辑分片不存在 → CHUNK_NOT_FOUND（404）。"""
    provider = _RecordingProvider()
    pipeline, repository, _ = _make_pipeline(tmp_path, provider)
    response = _confirmed_document(pipeline, repository, provider)
    with pytest.raises(ResourceNotFoundError) as info:
        pipeline.apply_chunk_draft(
            "kb_docs",
            response.document_id,
            "v1",
            [_edit(f"{response.document_id}:9999", "新文本")],
        )
    assert info.value.code == VECTOR_KB_CHUNK_NOT_FOUND


def test_apply_empty_operations_rejected(tmp_path) -> None:
    """空调整指令列表 → INVALID_CHUNK_OPERATION。"""
    provider = _RecordingProvider()
    pipeline, repository, _ = _make_pipeline(tmp_path, provider)
    response = _confirmed_document(pipeline, repository, provider)
    with pytest.raises(WorkflowValidationError) as info:
        pipeline.apply_chunk_draft(
            "kb_docs", response.document_id, "v1", []
        )
    assert info.value.code == VECTOR_KB_INVALID_CHUNK_OPERATION


def test_apply_unknown_document_rejected(tmp_path) -> None:
    """文档不存在/不属于该知识库 → DOCUMENT_NOT_FOUND（404）。"""
    provider = _RecordingProvider()
    pipeline, repository, _ = _make_pipeline(tmp_path, provider)
    _create_kb(repository)
    with pytest.raises(ResourceNotFoundError) as info:
        pipeline.apply_chunk_draft(
            "kb_docs", "doc_missing", "v1", [_edit("doc_missing:0001", "文本")]
        )
    assert info.value.code == VECTOR_KB_DOCUMENT_NOT_FOUND


# ---------------------------------------------------------------------------
# 乐观并发（DC-05 / DC-06）
# ---------------------------------------------------------------------------


def test_stale_base_version_rejected(tmp_path) -> None:
    """乐观并发：过期 base_version → DRAFT_VERSION_CONFLICT（409，T5.4「冲突拒绝」）。"""
    provider = _RecordingProvider()
    pipeline, repository, _ = _make_pipeline(tmp_path, provider)
    response = _confirmed_document(pipeline, repository, provider)
    first = f"{response.document_id}:0001"
    pipeline.apply_chunk_draft(
        "kb_docs", response.document_id, "v1", [_edit(first, "第一次编辑文本。")]
    )
    with pytest.raises(InstanceStateConflictError) as info:
        pipeline.apply_chunk_draft(
            "kb_docs",
            response.document_id,
            "v1",  # 重复使用旧基线（草稿已推进至 v2）
            [_edit(first, "第二次编辑文本。")],
        )
    assert info.value.code == VECTOR_KB_DRAFT_VERSION_CONFLICT


def test_confirm_unknown_draft_version_rejected(tmp_path) -> None:
    """确认调整：draft_version 不存在 → DRAFT_VERSION_CONFLICT（409）。"""
    provider = _RecordingProvider()
    pipeline, repository, _ = _make_pipeline(tmp_path, provider)
    response = _confirmed_document(pipeline, repository, provider)
    pipeline.apply_chunk_draft(
        "kb_docs",
        response.document_id,
        "v1",
        [_edit(f"{response.document_id}:0002", "编辑后的第二段。")],
    )
    with pytest.raises(InstanceStateConflictError) as info:
        pipeline.confirm_chunk_draft(
            "kb_docs", response.document_id, "v99"
        )
    assert info.value.code == VECTOR_KB_DRAFT_VERSION_CONFLICT


# ---------------------------------------------------------------------------
# 确认调整：仅重算受影响 / 幂等 / 失败保留 / 降级 / 部分失败（AC-36/AC-37）
# ---------------------------------------------------------------------------


def test_confirm_recomputes_only_affected_chunks(tmp_path) -> None:
    """仅重算受影响分片：编辑单分片 → 仅 1 次 embed，其余记录不动（AC-36）。"""
    provider = _RecordingProvider()
    pipeline, repository, vector_service = _make_pipeline(tmp_path, provider)
    response = _confirmed_document(pipeline, repository, provider)
    assert len(provider.embedded) == 3  # 导入确认 3 次
    second = f"{response.document_id}:0002"
    pipeline.apply_chunk_draft(
        "kb_docs", response.document_id, "v1", [_edit(second, "编辑后的第二段说明。")]
    )
    result = pipeline.confirm_chunk_draft(
        "kb_docs", response.document_id, "v2"
    )
    assert result.status == "succeeded"
    assert result.rewritten == [second]
    assert result.deleted == []
    assert result.local_degraded is False
    assert len(provider.embedded) == 4  # 仅受影响分片重算
    diagnosis = vector_service.diagnose("kb_docs")
    assert diagnosis.record_count == 3
    assert diagnosis.ok is True
    outcome = vector_service.retrieve(
        "kb_docs", provider.embed("编辑后的第二段说明。"), top_k=1
    )
    assert outcome.chunks[0].text == "编辑后的第二段说明。"


def test_confirm_merge_updates_store(tmp_path) -> None:
    """合并确认：覆盖首记录 + 删除被合并记录（AC-37，record_id 幂等）。"""
    provider = _RecordingProvider()
    pipeline, repository, vector_service = _make_pipeline(tmp_path, provider)
    response = _confirmed_document(pipeline, repository, provider)
    first = f"{response.document_id}:0001"
    second = f"{response.document_id}:0002"
    pipeline.apply_chunk_draft(
        "kb_docs", response.document_id, "v1", [_merge([first, second])]
    )
    result = pipeline.confirm_chunk_draft(
        "kb_docs", response.document_id, "v2"
    )
    assert result.status == "succeeded"
    assert result.rewritten == [first]
    assert result.deleted == [second]
    diagnosis = vector_service.diagnose("kb_docs")
    assert diagnosis.record_count == 2
    assert diagnosis.ok is True
    outcome = vector_service.retrieve(
        "kb_docs", provider.embed(MERGED_12), top_k=1
    )
    assert outcome.chunks[0].text == MERGED_12


def test_confirm_split_updates_store(tmp_path) -> None:
    """拆分确认：删除原记录 + 写入子记录（AC-37）。"""
    provider = _RecordingProvider()
    pipeline, repository, vector_service = _make_pipeline(tmp_path, provider)
    response = _confirmed_document(pipeline, repository, provider)
    second = f"{response.document_id}:0002"
    pipeline.apply_chunk_draft(
        "kb_docs", response.document_id, "v1", [_split(second, [5])]
    )
    result = pipeline.confirm_chunk_draft(
        "kb_docs", response.document_id, "v2"
    )
    assert result.status == "succeeded"
    assert result.rewritten == [f"{second}.1", f"{second}.2"]
    assert result.deleted == [second]
    diagnosis = vector_service.diagnose("kb_docs")
    assert diagnosis.record_count == 4
    assert diagnosis.ok is True


def test_confirm_idempotent_no_duplicates(tmp_path) -> None:
    """幂等重复确认：第二次空变更成功 + 零 embed + 记录不增（INV-D20，T5.4）。"""
    provider = _RecordingProvider()
    pipeline, repository, vector_service = _make_pipeline(tmp_path, provider)
    response = _confirmed_document(pipeline, repository, provider)
    second = f"{response.document_id}:0002"
    pipeline.apply_chunk_draft(
        "kb_docs", response.document_id, "v1", [_edit(second, "幂等验证文本。")]
    )
    first_result = pipeline.confirm_chunk_draft(
        "kb_docs", response.document_id, "v2"
    )
    assert first_result.status == "succeeded"
    embed_count = len(provider.embedded)
    second_result = pipeline.confirm_chunk_draft(
        "kb_docs", response.document_id, "v2"
    )
    assert second_result.status == "succeeded"
    assert second_result.rewritten == []
    assert second_result.deleted == []
    assert len(provider.embedded) == embed_count  # 幂等：零重算
    assert vector_service.diagnose("kb_docs").record_count == 3


def test_confirm_failure_preserves_old_records_and_recovers(tmp_path) -> None:
    """重算失败：不删旧记录（in-band failed + failed_at/reason），恢复后可重确认（INV-D21）。"""
    provider = _SwitchableProvider()
    pipeline, repository, vector_service = _make_pipeline(tmp_path, provider)
    response = _confirmed_document(pipeline, repository, provider)
    second = f"{response.document_id}:0002"
    pipeline.apply_chunk_draft(
        "kb_docs", response.document_id, "v1", [_edit(second, "恢复验证文本。")]
    )
    provider.fail = True
    failed_result = pipeline.confirm_chunk_draft(
        "kb_docs", response.document_id, "v2"
    )
    assert failed_result.status == "failed"
    assert failed_result.rewritten == []
    assert failed_result.deleted == []
    assert failed_result.failed_at == second
    assert failed_result.reason is not None
    diagnosis = vector_service.diagnose("kb_docs")
    assert diagnosis.record_count == 3  # 旧记录全保留
    provider.fail = False
    recovered = pipeline.confirm_chunk_draft(
        "kb_docs", response.document_id, "v2"
    )
    assert recovered.status == "succeeded"
    assert recovered.rewritten == [second]
    assert vector_service.diagnose("kb_docs").record_count == 3


def test_confirm_without_source_raises_and_fallback_degrades(tmp_path) -> None:
    """无来源：无兜底 → EMBEDDING_UNAVAILABLE；绑定兜底 → 哈希降级标注（INV-D08）。"""
    # 无兜底：provider 解析为 None → 异常（对齐 T4 语义）
    pipeline, repository, vector_service = _make_pipeline(tmp_path, None)
    _create_kb(repository)
    response = _ingest(pipeline)
    # 导入确认本身需来源：直接走分片编辑路径构造已确认基线不可行，
    # 改为验证「无来源 + 无兜底」在确认调整阶段的确定性失败
    pipeline.apply_chunk_draft(
        "kb_docs",
        response.document_id,
        "v1",
        [_edit(f"{response.document_id}:0002", "无来源验证文本。")],
    )
    with pytest.raises(Exception) as info:
        pipeline.confirm_chunk_draft(
            "kb_docs", response.document_id, "v2"
        )
    assert getattr(info.value, "code", None) == VECTOR_KB_EMBEDDING_UNAVAILABLE
    # 绑定兜底：降级成功 + 标注
    repository2 = SqliteKnowledgeBaseRepository(str(tmp_path / "kb_meta2"))
    vector_service2 = LocalVectorDatabaseService(
        config=LocalVectorDatabaseConfig(
            storage_root=str(tmp_path / "vectors2")
        )
    )
    pipeline2 = IngestionPipeline(
        repository=repository2,
        vector_service=vector_service2,
        config=VectorKbManagementConfig(
            storage_root=str(tmp_path / "kb_meta2")
        ),
        provider_factory=_factory_of(None),
    )
    _create_kb(repository2, allow_fallback=True)
    response2 = _ingest(pipeline2)
    draft_response = pipeline2.apply_chunk_draft(
        "kb_docs",
        response2.document_id,
        "v1",
        [_edit(f"{response2.document_id}:0002", "降级验证文本。")],
    )
    degraded = pipeline2.confirm_chunk_draft(
        "kb_docs", response2.document_id, draft_response.draft_version
    )
    assert degraded.status == "succeeded"
    assert degraded.local_degraded is True
    assert degraded.local_degraded_reason is not None
    assert vector_service2.diagnose("kb_docs").record_count == 3


def test_confirm_partial_upsert_preserves_old_records(tmp_path) -> None:
    """部分失败：单条写入失败 → partial + 不执行删除 + 旧记录保留（INV-D21）。"""
    provider = _PoisonProvider()
    pipeline, repository, vector_service = _make_pipeline(tmp_path, provider)
    response = _confirmed_document(pipeline, repository, provider)
    second = f"{response.document_id}:0002"
    pipeline.apply_chunk_draft(
        "kb_docs", response.document_id, "v1", [_edit(second, "POISON 污染文本。")]
    )
    result = pipeline.confirm_chunk_draft(
        "kb_docs", response.document_id, "v2"
    )
    assert result.status == "partial"
    assert result.rewritten == []
    assert result.deleted == []
    assert result.failed_at == second
    assert result.reason is not None
    assert vector_service.diagnose("kb_docs").record_count == 3


def test_confirm_without_prior_import_writes_all_chunks(tmp_path) -> None:
    """无导入确认基线（confirmed_version=None）：确认调整 → 全量写入生效。"""
    provider = _RecordingProvider()
    pipeline, repository, vector_service = _make_pipeline(tmp_path, provider)
    _create_kb(repository)
    response = _ingest(pipeline)
    pipeline.apply_chunk_draft(
        "kb_docs",
        response.document_id,
        "v1",
        [_edit(f"{response.document_id}:0002", "未先行导入确认的编辑。")],
    )
    result = pipeline.confirm_chunk_draft(
        "kb_docs", response.document_id, "v2"
    )
    assert result.status == "succeeded"
    assert len(result.rewritten) == 3  # 空基线 → 全部重算
    assert result.deleted == []
    assert vector_service.diagnose("kb_docs").record_count == 3


def test_multi_round_edits_accumulate_on_confirm(tmp_path) -> None:
    """多轮未确认编辑跨版本累积：基线为最近成功确认快照（DC-05 最后确认为准）。"""
    provider = _RecordingProvider()
    pipeline, repository, vector_service = _make_pipeline(tmp_path, provider)
    response = _confirmed_document(pipeline, repository, provider)
    first = f"{response.document_id}:0001"
    second = f"{response.document_id}:0002"
    third = f"{response.document_id}:0003"
    # v2：合并前两段；v3：编辑第三段（均未确认）
    pipeline.apply_chunk_draft(
        "kb_docs", response.document_id, "v1", [_merge([first, second])]
    )
    pipeline.apply_chunk_draft(
        "kb_docs", response.document_id, "v2", [_edit(third, "累积编辑的第三段。")]
    )
    result = pipeline.confirm_chunk_draft(
        "kb_docs", response.document_id, "v3"
    )
    assert result.status == "succeeded"
    assert result.rewritten == [first, third]
    assert result.deleted == [second]
    diagnosis = vector_service.diagnose("kb_docs")
    assert diagnosis.record_count == 2
    assert diagnosis.ok is True
    assert len(provider.embedded) == 5  # 导入 3 + 仅受影响 2
