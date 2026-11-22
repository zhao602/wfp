# =============================================================================
# 向量知识库管理五策略分片器单元测试（tests/unit/test_vector_kb_management_chunkers.py）
#
# 覆盖（CHG-17 / design §2.10.1 chunkers 行，AC 对照）：
#   - 五策略正常路径 5（paragraph / fixed_length / recursive / structural / semantic）
#   - 边界：空文档（五策略 ×5）/ 超短段落跳过 / 超长二次切分（段落与章节）
#   - 参数非法拒绝：overlap ≥ max（AC-22）/ 相似度阈值域 / max 边界
#   - structural 无结构回退 + 可见提示（AC-25）/ hierarchy 覆盖 / 单标题单分片
#   - semantic 注入假 provider 切分逻辑（阈值两侧各一例，NV-03）/
#     无来源显式失败（AC-27）/ 无句末标点长文本降级 / 单句单分片 /
#     超长句组二次切分
#   - 未知策略确定性拒绝 / LLM 策略不存在（AC-28）
#   - 超长确定性截断 + source.truncated（design §2.7.1）
#   - 协议一致性：chunk ≡ chunk_detailed().chunks / 工厂五策略协议满足
#
# 沙箱验证：PYTHONPATH=src python -m pytest tests/unit/test_vector_kb_management_chunkers.py -q
# （同步 API 驱动，禁 asyncio.run；真实语义效果不可验证——仅注入假 provider
#   验证切分逻辑，NV-03，不以"语义分片质量"作为验收项）
# =============================================================================

from typing import Dict, List, get_args

import pytest
from pydantic import ValidationError

from orchestration_platform.domain.errors import (
    VECTOR_KB_EMPTY_CONTENT,
    VECTOR_KB_EMBEDDING_SOURCE_MISSING,
    VECTOR_KB_INVALID_CHUNK_PARAMS,
    WorkflowValidationError,
)
from orchestration_platform.integrations.local_vector_database.embedding import (
    EmbeddingProvider,
)
from orchestration_platform.integrations.vector_kb_management import (
    CHUNK_TEXT_LIMIT,
    ChunkDraft,
    ChunkStrategy,
    ChunkStrategyConfig,
    Chunker,
    FixedLengthChunker,
    ParagraphChunker,
    RecursiveChunker,
    SemanticChunker,
    StructuralChunker,
    build_chunker,
    validate_chunk_params,
)
from orchestration_platform.integrations.vector_kb_management.chunkers import (
    _build_drafts,
)

ALL_STRATEGIES = (
    "paragraph",
    "fixed_length",
    "recursive",
    "structural",
    "semantic",
)


class _FakeProvider(EmbeddingProvider):
    """确定性假 provider（NV-03：仅验证切分逻辑，非真实语义）。"""

    provenance = "caller_supplied"
    dimension = 2

    def __init__(self, vectors: Dict[str, List[float]]) -> None:
        self._vectors = vectors

    def embed(self, text: str) -> List[float]:
        return list(self._vectors.get(text, [0.0, 0.0]))


def _config(strategy: str, **overrides) -> ChunkStrategyConfig:
    return ChunkStrategyConfig(strategy=strategy, **overrides)


# ---------------------------------------------------------------------------
# paragraph（AC-20）
# ---------------------------------------------------------------------------


def test_paragraph_splits_on_blank_lines() -> None:
    text = "第一段第一行\n第一段第二行\n\n第二段"
    drafts = ParagraphChunker().chunk(text, _config("paragraph"))
    assert [draft.text for draft in drafts] == [
        "第一段第一行\n第一段第二行",
        "第二段",
    ]
    assert [draft.index for draft in drafts] == [0, 1]
    assert all(draft.source["strategy"] == "paragraph" for draft in drafts)
    assert all(draft.chunk_id == "" for draft in drafts)  # 由流水线赋值


def test_paragraph_line_units_without_blank_lines() -> None:
    text = "行一\n行二\n行三"
    drafts = ParagraphChunker().chunk(text, _config("paragraph"))
    assert [draft.text for draft in drafts] == ["行一", "行二", "行三"]


def test_paragraph_overlong_paragraph_is_split_not_dropped() -> None:
    paragraph = "字" * 1200
    drafts = ParagraphChunker().chunk(
        paragraph, _config("paragraph", max_chunk_length=500)
    )
    assert [len(draft.text) for draft in drafts] == [500, 500, 200]
    assert "".join(draft.text for draft in drafts) == paragraph  # 不丢弃


def test_paragraph_skips_blank_paragraphs() -> None:
    text = "甲\n\n\n\n\n乙"
    drafts = ParagraphChunker().chunk(text, _config("paragraph"))
    assert [draft.text for draft in drafts] == ["甲", "乙"]


# ---------------------------------------------------------------------------
# fixed_length（AC-21 / AC-22）
# ---------------------------------------------------------------------------


def test_fixed_length_windows_with_overlap() -> None:
    text = "".join(str(index % 10) for index in range(120))
    drafts = FixedLengthChunker().chunk(
        text, _config("fixed_length", max_chunk_length=50, overlap=10)
    )
    assert [len(draft.text) for draft in drafts] == [50, 50, 40]
    assert drafts[1].text == text[40:90]
    assert drafts[2].text == text[80:120]


def test_fixed_length_short_text_single_chunk() -> None:
    drafts = FixedLengthChunker().chunk("短文本", _config("fixed_length"))
    assert len(drafts) == 1
    assert drafts[0].text == "短文本"


# ---------------------------------------------------------------------------
# recursive（AC-23；系统默认策略 OP-09）
# ---------------------------------------------------------------------------


def test_recursive_splits_by_paragraph_then_sentence() -> None:
    text = "短段。\n\n" + "句子内容。" * 60
    drafts = RecursiveChunker().chunk(
        text, _config("recursive", max_chunk_length=100)
    )
    assert drafts[0].text == "短段。"
    assert all(len(draft.text) <= 100 for draft in drafts)
    assert drafts[1].text == "句子内容。"


def test_recursive_falls_back_to_char_level_hard_cut() -> None:
    text = "字" * 120  # 无任何分隔符 → 默认清单末位 "" 字符级兜底
    drafts = RecursiveChunker().chunk(
        text, _config("recursive", max_chunk_length=50, overlap=0)
    )
    assert [len(draft.text) for draft in drafts] == [50, 50, 20]
    assert "".join(draft.text for draft in drafts) == text  # 不丢内容


def test_recursive_custom_separators_override() -> None:
    text = "alpha;" * 20  # 120 字符
    drafts = RecursiveChunker().chunk(
        text, _config("recursive", max_chunk_length=100, separators=[";"])
    )
    assert [draft.text for draft in drafts] == ["alpha;"] * 20


def test_recursive_separator_exhaustion_hard_cut() -> None:
    text = "字" * 120
    drafts = RecursiveChunker().chunk(
        text,
        _config("recursive", max_chunk_length=50, overlap=0, separators=["@@"]),
    )
    assert [len(draft.text) for draft in drafts] == [50, 50, 20]


# ---------------------------------------------------------------------------
# structural（AC-24 / AC-25）
# ---------------------------------------------------------------------------


def test_structural_splits_by_headings() -> None:
    text = "# 标题一\n内容甲\n\n## 标题二\n内容乙"
    outcome = StructuralChunker().chunk_detailed(text, _config("structural"))
    assert outcome.strategy_effective == "structural"
    assert outcome.notices == []
    assert [draft.text for draft in outcome.chunks] == [
        "# 标题一\n内容甲",
        "## 标题二\n内容乙",
    ]
    assert outcome.chunks[0].source["heading"] == "# 标题一"
    assert outcome.chunks[1].source["heading"] == "## 标题二"


def test_structural_single_heading_single_chunk() -> None:
    text = "# 唯一标题\n正文内容"
    drafts = StructuralChunker().chunk(text, _config("structural"))
    assert len(drafts) == 1
    assert drafts[0].text == "# 唯一标题\n正文内容"
    assert drafts[0].source["heading"] == "# 唯一标题"


def test_structural_falls_back_to_paragraph_with_notice() -> None:
    text = "无标题的纯文本段落一。\n\n无标题的纯文本段落二。"
    outcome = StructuralChunker().chunk_detailed(text, _config("structural"))
    assert outcome.strategy_effective == "paragraph"
    assert outcome.notices  # 可见提示（AC-25，禁静默回退）
    assert "段落" in outcome.notices[0]
    assert outcome.chunks[0].source["strategy"] == "paragraph"
    assert [draft.text for draft in outcome.chunks] == [
        "无标题的纯文本段落一。",
        "无标题的纯文本段落二。",
    ]


def test_structural_custom_hierarchy_pattern() -> None:
    text = "Chapter 一\n内容甲\n\nAppendix 甲\n内容乙"
    outcome = StructuralChunker().chunk_detailed(
        text,
        _config("structural", hierarchy=[r"^Chapter\s", r"^Appendix\s"]),
    )
    assert outcome.strategy_effective == "structural"
    assert len(outcome.chunks) == 2
    assert outcome.chunks[0].source["heading"] == "Chapter 一"
    assert outcome.chunks[1].source["heading"] == "Appendix 甲"


def test_structural_invalid_hierarchy_regex_rejected() -> None:
    with pytest.raises(WorkflowValidationError) as exc_info:
        StructuralChunker().chunk_detailed(
            "任意文本", _config("structural", hierarchy=["(未闭合"])
        )
    assert exc_info.value.code == VECTOR_KB_INVALID_CHUNK_PARAMS


def test_structural_overlong_section_is_split() -> None:
    text = "# 标题\n" + "字" * 1200  # 章节 1205 字符
    drafts = StructuralChunker().chunk(
        text, _config("structural", max_chunk_length=500)
    )
    assert [len(draft.text) for draft in drafts] == [500, 500, 205]
    assert drafts[0].source["heading"] == "# 标题"
    assert drafts[1].source.get("heading") is None  # 二次切分不重复挂标题


# ---------------------------------------------------------------------------
# semantic（AC-26 / AC-27 / NV-03）
# ---------------------------------------------------------------------------


def test_semantic_splits_on_low_similarity() -> None:
    text = "猫坐在垫子上。猫在打盹。量子纠缠很复杂。波函数坍缩了。"
    provider = _FakeProvider(
        {
            "猫坐在垫子上。": [1.0, 0.0],
            "猫在打盹。": [1.0, 0.0],
            "量子纠缠很复杂。": [0.0, 1.0],
            "波函数坍缩了。": [0.0, 1.0],
        }
    )
    outcome = SemanticChunker(provider=provider, threshold=0.5).chunk_detailed(
        text, _config("semantic", similarity_threshold=0.5)
    )
    assert outcome.strategy_effective == "semantic"
    assert [draft.text for draft in outcome.chunks] == [
        "猫坐在垫子上。猫在打盹。",
        "量子纠缠很复杂。波函数坍缩了。",
    ]


def test_semantic_keeps_group_when_similar() -> None:
    text = "甲句子。乙句子。丙句子。"
    provider = _FakeProvider(
        {
            "甲句子。": [1.0, 0.0],
            "乙句子。": [1.0, 0.0],
            "丙句子。": [1.0, 0.0],
        }
    )
    outcome = SemanticChunker(provider=provider, threshold=0.5).chunk_detailed(
        text, _config("semantic", similarity_threshold=0.5)
    )
    assert len(outcome.chunks) == 1
    assert outcome.chunks[0].text == text  # 相似度 ≥ 阈值 → 同组不切分


def test_semantic_without_provider_fails_explicitly() -> None:
    chunker = SemanticChunker()  # 未注入来源
    with pytest.raises(WorkflowValidationError) as exc_info:
        chunker.chunk("任意文本内容。", _config("semantic"))
    assert exc_info.value.code == VECTOR_KB_EMBEDDING_SOURCE_MISSING
    assert "recursive" in exc_info.value.message  # 引导改用 recursive


def test_semantic_long_text_without_punctuation_falls_back_to_paragraph() -> None:
    text = "无标点" * 200  # 600 字，无句末标点 → 相似度序列为空
    provider = _FakeProvider({})
    outcome = SemanticChunker(provider=provider).chunk_detailed(
        text, _config("semantic", max_chunk_length=500)
    )
    assert outcome.strategy_effective == "paragraph"
    assert outcome.notices
    assert len(outcome.chunks) == 2  # 600 字 → 二次切分 [500, 100]
    assert [len(draft.text) for draft in outcome.chunks] == [500, 100]


def test_semantic_single_sentence_yields_single_chunk() -> None:
    provider = _FakeProvider({"短句。": [1.0, 0.0]})
    outcome = SemanticChunker(provider=provider).chunk_detailed(
        "短句。", _config("semantic")
    )
    assert len(outcome.chunks) == 1
    assert outcome.chunks[0].text == "短句。"
    assert outcome.strategy_effective == "semantic"


def test_semantic_oversized_group_is_hard_cut() -> None:
    sentence_a = "长" * 40 + "。"
    sentence_b = "字" * 40 + "。"
    provider = _FakeProvider(
        {sentence_a: [1.0, 0.0], sentence_b: [1.0, 0.0]}
    )
    outcome = SemanticChunker(provider=provider, threshold=0.5).chunk_detailed(
        sentence_a + sentence_b,
        _config(
            "semantic",
            max_chunk_length=50,
            overlap=0,
            similarity_threshold=0.5,
        ),
    )
    assert [len(draft.text) for draft in outcome.chunks] == [50, 32]
    assert outcome.strategy_effective == "semantic"


# ---------------------------------------------------------------------------
# 空内容确定性拒绝（五策略 ×5，INV-D09）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("strategy", ALL_STRATEGIES)
def test_blank_document_rejected_for_all_strategies(strategy: str) -> None:
    chunker = build_chunker(strategy, provider=_FakeProvider({}))
    with pytest.raises(WorkflowValidationError) as exc_info:
        chunker.chunk("   \n\t  ", _config(strategy))
    assert exc_info.value.code == VECTOR_KB_EMPTY_CONTENT


# ---------------------------------------------------------------------------
# 参数校验（AC-22 / AC-28）
# ---------------------------------------------------------------------------


def test_overlap_not_less_than_max_rejected() -> None:
    config = _config("fixed_length", max_chunk_length=100, overlap=100)
    with pytest.raises(WorkflowValidationError) as exc_info:
        FixedLengthChunker().chunk("文本" * 10, config)
    assert exc_info.value.code == VECTOR_KB_INVALID_CHUNK_PARAMS
    assert "overlap" in exc_info.value.message  # 附取值域


def test_invalid_similarity_threshold_rejected() -> None:
    provider = _FakeProvider({})
    config = _config("semantic", similarity_threshold=1.5)
    with pytest.raises(WorkflowValidationError) as exc_info:
        SemanticChunker(provider=provider).chunk("文本内容。", config)
    assert exc_info.value.code == VECTOR_KB_INVALID_CHUNK_PARAMS


def test_max_chunk_length_bounds_rejected() -> None:
    for invalid in (49, 2001, -1):
        with pytest.raises(WorkflowValidationError) as exc_info:
            validate_chunk_params(
                _config("paragraph", max_chunk_length=invalid)
            )
        assert exc_info.value.code == VECTOR_KB_INVALID_CHUNK_PARAMS
    # 边界值合法（50 / 2000 均为闭区间端点）
    validate_chunk_params(
        _config("paragraph", max_chunk_length=50, overlap=49)
    )
    validate_chunk_params(
        _config("paragraph", max_chunk_length=CHUNK_TEXT_LIMIT, overlap=0)
    )


def test_unknown_strategy_rejected_by_factory() -> None:
    with pytest.raises(WorkflowValidationError) as exc_info:
        build_chunker("llm")  # type: ignore[arg-type]
    assert exc_info.value.code == VECTOR_KB_INVALID_CHUNK_PARAMS
    assert "llm" in exc_info.value.message


def test_llm_strategy_absent_from_enum() -> None:
    assert "llm" not in get_args(ChunkStrategy)
    assert set(get_args(ChunkStrategy)) == set(ALL_STRATEGIES)
    with pytest.raises(ValidationError):  # pydantic Literal 拒绝未知值
        ChunkStrategyConfig(strategy="llm")


# ---------------------------------------------------------------------------
# 协议一致性与截断守护
# ---------------------------------------------------------------------------


def test_all_chunkers_satisfy_protocol() -> None:
    provider = _FakeProvider({})
    for strategy in ALL_STRATEGIES:
        chunker = build_chunker(strategy, provider=provider)
        assert isinstance(chunker, Chunker), strategy


def test_chunk_equals_chunk_detailed_chunks() -> None:
    text = "甲\n\n乙"
    chunker = ParagraphChunker()
    assert chunker.chunk(text, _config("paragraph")) == chunker.chunk_detailed(
        text, _config("paragraph")
    ).chunks


def test_chunk_draft_truncation_guard() -> None:
    long_piece = "字" * (CHUNK_TEXT_LIMIT + 500)
    drafts = _build_drafts([long_piece], "paragraph")
    assert len(drafts) == 1
    assert len(drafts[0].text) == CHUNK_TEXT_LIMIT
    assert drafts[0].source["truncated"] is True
    # 界内不标注
    normal = _build_drafts(["短文本"], "paragraph")
    assert isinstance(normal[0], ChunkDraft)
    assert "truncated" not in (normal[0].source or {})
