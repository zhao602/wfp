# =============================================================================
# 向量知识库管理五策略分片器（integrations/vector_kb_management/chunkers.py）
#
# 职责：统一 Chunker 接口 + 工厂分派 + 通用参数校验 + 五策略实现
# （CHG-04 / design §2.7）。
# 设计意图（vector_kb_management design AC-20~AC-28 / INV-D08 / INV-D09）：
#   1. 统一协议（DC-02）：chunk(text, config) -> List[ChunkDraft]，恒等于
#      chunk_detailed(...).chunks；chunk_detailed 额外携带 notices /
#      strategy_effective，承载 structural / semantic 的确定性回退可见性
#      （AC-25 / AC-27：回退必须可见，禁静默）；
#   2. 通用前置校验 validate_chunk_params：max_chunk_length ∈ [50, 2000]；
#      overlap ∈ [0, max_chunk_length-1]；similarity_threshold ∈ [0, 1]
#      （仅 semantic）；非法 → VECTOR_KB_INVALID_CHUNK_PARAMS（附取值域）；
#   3. 空内容（纯空白）→ VECTOR_KB_EMPTY_CONTENT（确定性拒绝，非静默
#      空成功，INV-D09）；
#   4. semantic 无真实来源 → 显式失败 VECTOR_KB_EMBEDDING_SOURCE_MISSING
#      并引导改用 recursive（INV-D08 不伪造语义，AC-27）；
#   5. 策略枚举不含 LLM 项（AC-28）；未知策略 → 确定性拒绝（不静默回退）；
#   6. cosine 相似度与 EmbeddingProvider 抽象复用 local_vector_database
#      既有实现（INV-D02 不重造）。
# =============================================================================

import re
from typing import Dict, List, Optional, Protocol, runtime_checkable

from orchestration_platform.domain.errors import (
    VECTOR_KB_EMPTY_CONTENT,
    VECTOR_KB_EMBEDDING_SOURCE_MISSING,
    VECTOR_KB_INVALID_CHUNK_PARAMS,
    WorkflowValidationError,
)
from orchestration_platform.domain.models.common import JsonValue
from orchestration_platform.integrations.local_vector_database.embedding import (
    EmbeddingProvider,
)
from orchestration_platform.integrations.local_vector_database.vector_math import (
    cosine_score,
)
from orchestration_platform.integrations.vector_kb_management.models import (
    CHUNK_TEXT_LIMIT,
    ChunkDraft,
    ChunkStrategy,
    ChunkStrategyConfig,
    ChunkerOutcome,
)

# 系统默认分片策略（OP-09：recursive）
DEFAULT_CHUNK_STRATEGY: ChunkStrategy = "recursive"

# recursive 默认分隔符优先级（design §2.7.4；末位 "" 为字符级兜底）
DEFAULT_RECURSIVE_SEPARATORS: List[str] = [
    "\n\n",
    "\n",
    "。",
    "！",
    "？",
    ".",
    "!",
    "?",
    " ",
    "",
]

# structural 默认标题行正则（Markdown ^#{1,6}\s，design §2.7.5；可被 hierarchy 覆盖）
DEFAULT_HEADING_PATTERN = r"^#{1,6}\s"

# semantic 默认相似度阈值（DC-10）
DEFAULT_SIMILARITY_THRESHOLD = 0.75

# semantic 句末标点（零宽断言保标点随句，design §2.7.6）
_SENTENCE_BOUNDARY = re.compile(r"(?<=[。！？.!?])")


def _normalize_newlines(text: str) -> str:
    """换行归一（\\r\\n / \\r → \\n）：CRLF 不致盲化 ``\\n\\n`` 边界。"""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _require_content(text: str) -> None:
    """空内容确定性拒绝：纯空白 → VECTOR_KB_EMPTY_CONTENT（非静默空成功）。"""
    if not text or not text.strip():
        raise WorkflowValidationError(
            VECTOR_KB_EMPTY_CONTENT, "文档内容为空（纯空白），无法分片"
        )


def _hard_cut(text: str, max_chunk_length: int) -> List[str]:
    """字符级硬切（不超长、不丢内容；分隔符耗尽 / 超长二次切分兜底）。"""
    return [
        text[start : start + max_chunk_length]
        for start in range(0, len(text), max_chunk_length)
    ]


def _build_drafts(
    pieces: List[str],
    strategy: str,
    extra_sources: Optional[List[Optional[Dict[str, JsonValue]]]] = None,
) -> List[ChunkDraft]:
    """统一组装 ChunkDraft（过滤空白片段 + 超长确定性截断 + source 组装）。

    - 纯空白片段跳过（无有效内容，不计入分片）；
    - text > CHUNK_TEXT_LIMIT → 截断至上限并标注 source["truncated"]=True
      （design §2.7.1：超长确定性截断 + truncated=true）；
    - index 过滤后连续自增（0 起）；source["index"] 同步。
    """
    drafts: List[ChunkDraft] = []
    for position, piece in enumerate(pieces):
        if not piece.strip():
            continue
        text = piece
        source: Dict[str, JsonValue] = {"strategy": strategy, "index": len(drafts)}
        if len(text) > CHUNK_TEXT_LIMIT:
            text = text[:CHUNK_TEXT_LIMIT]
            source["truncated"] = True
        if extra_sources is not None and position < len(extra_sources):
            extra = extra_sources[position]
            if extra:
                source.update(extra)
        drafts.append(ChunkDraft(index=len(drafts), text=text, source=source))
    return drafts


def _outcome(
    drafts: List[ChunkDraft],
    strategy_effective: ChunkStrategy,
    notices: Optional[List[str]] = None,
) -> ChunkerOutcome:
    """简单结果包装（无回退路径：notices 为空，strategy_effective 即请求策略）。"""
    return ChunkerOutcome(
        chunks=drafts,
        notices=list(notices) if notices else [],
        strategy_effective=strategy_effective,
    )


def validate_chunk_params(config: ChunkStrategyConfig) -> None:
    """通用前置校验（design §2.7.1；非法 → VECTOR_KB_INVALID_CHUNK_PARAMS）。

    - max_chunk_length ∈ [50, CHUNK_TEXT_LIMIT]；
    - overlap ∈ [0, max_chunk_length-1]（必须 < 上限，AC-22 防零步长）；
    - similarity_threshold ∈ [0, 1]（仅 semantic 且显式提供时校验）；
    - 全部取值域附于错误消息（可诊断）。
    """
    problems: List[str] = []
    if not 50 <= config.max_chunk_length <= CHUNK_TEXT_LIMIT:
        problems.append(
            f"max_chunk_length 须在 [50, {CHUNK_TEXT_LIMIT}] 内，"
            f"当前为 {config.max_chunk_length}"
        )
    if not 0 <= config.overlap <= config.max_chunk_length - 1:
        problems.append(
            f"overlap 须满足 0 ≤ overlap ≤ max_chunk_length-1"
            f"（当前 max_chunk_length={config.max_chunk_length}，"
            f"overlap={config.overlap}）"
        )
    if (
        config.strategy == "semantic"
        and config.similarity_threshold is not None
        and not 0.0 <= config.similarity_threshold <= 1.0
    ):
        problems.append(
            f"similarity_threshold 须在 [0, 1] 内，"
            f"当前为 {config.similarity_threshold}"
        )
    if problems:
        raise WorkflowValidationError(
            VECTOR_KB_INVALID_CHUNK_PARAMS,
            "分片参数非法：" + "；".join(problems),
        )


@runtime_checkable
class Chunker(Protocol):
    """统一分片协议（DC-02）。

    - chunk          —— 纯分片序列视图（恒等于 chunk_detailed(...).chunks）；
    - chunk_detailed —— 携带 notices / strategy_effective 的完整产出
      （structural / semantic 回退可见性出口，AC-25 / AC-27）。
    """

    def chunk(self, text: str, config: ChunkStrategyConfig) -> List[ChunkDraft]:
        ...

    def chunk_detailed(
        self, text: str, config: ChunkStrategyConfig
    ) -> ChunkerOutcome:
        ...


class BaseChunker:
    """分片器基类：协议适配（chunk 恒等映射）+ 子类实现 chunk_detailed。"""

    def chunk(self, text: str, config: ChunkStrategyConfig) -> List[ChunkDraft]:
        return self.chunk_detailed(text, config).chunks

    def chunk_detailed(
        self, text: str, config: ChunkStrategyConfig
    ) -> ChunkerOutcome:
        raise NotImplementedError("分片器须实现 chunk_detailed")


class ParagraphChunker(BaseChunker):
    """paragraph（按段落，design §2.7.2）。

    - 空行为主边界；段内单换行保留；
    - 无空行的单换行文本 → 逐行作为段落单元；
    - 超长段落按 max_chunk_length 二次切分（不丢弃，AC-20）；
    - 超短段落（无有效内容）跳过。
    """

    def chunk_detailed(
        self, text: str, config: ChunkStrategyConfig
    ) -> ChunkerOutcome:
        validate_chunk_params(config)
        _require_content(text)
        normalized = _normalize_newlines(text)
        blocks = re.split(r"\n\s*\n", normalized)
        if len(blocks) == 1 and "\n" in blocks[0]:
            blocks = blocks[0].split("\n")
        pieces: List[str] = []
        for block in blocks:
            paragraph = block.strip()
            if not paragraph:
                continue
            if len(paragraph) <= config.max_chunk_length:
                pieces.append(paragraph)
            else:
                pieces.extend(_hard_cut(paragraph, config.max_chunk_length))
        drafts = _build_drafts(pieces, "paragraph")
        if not drafts:
            raise WorkflowValidationError(
                VECTOR_KB_EMPTY_CONTENT, "文档内容为空（纯空白），无法分片"
            )
        return _outcome(drafts, "paragraph")


class FixedLengthChunker(BaseChunker):
    """fixed_length（定长窗口 + 重叠衔接，design §2.7.3）。

    - 窗口 [start, start+max_chunk_length)；步长 max_chunk_length-overlap；
    - 末窗不足仍产出（非空）；文本 ≤ 窗口 → 单分片；
    - 每分片文本自包含（重叠区内容重复但文本独立，AC-21）。
    """

    def chunk_detailed(
        self, text: str, config: ChunkStrategyConfig
    ) -> ChunkerOutcome:
        validate_chunk_params(config)
        _require_content(text)
        normalized = _normalize_newlines(text)
        max_len = config.max_chunk_length
        step = max_len - config.overlap
        pieces: List[str] = []
        start = 0
        total = len(normalized)
        while start < total:
            end = min(start + max_len, total)
            pieces.append(normalized[start:end])
            if end >= total:
                break
            start += step
        drafts = _build_drafts(pieces, "fixed_length")
        if not drafts:
            raise WorkflowValidationError(
                VECTOR_KB_EMPTY_CONTENT, "文档内容为空（纯空白），无法分片"
            )
        return _outcome(drafts, "fixed_length")


def _split_recursive(
    fragment: str, separators: List[str], max_len: int
) -> List[str]:
    """按分隔符优先级递归切分（split-down 语义，design §2.7.4）。

    - 片段 ≤ max_len 直接产出（语义完整性优先）；
    - 超长片段按下一级分隔符递归；分隔符未出现 → 降级继续；
    - 分隔符耗尽（列表为空）→ 字符级硬切（不超长、不丢内容）；
    - ``""`` 分隔符即字符级兜底（默认清单末位）；
    - 分隔符随前段附着（保句末标点语义），片段两缘空白剥离。
    """
    if not fragment:
        return []
    if len(fragment) <= max_len:
        return [fragment]
    if not separators:
        return _hard_cut(fragment, max_len)
    separator = separators[0]
    remaining = separators[1:]
    if separator == "":
        return _hard_cut(fragment, max_len)
    if separator not in fragment:
        return _split_recursive(fragment, remaining, max_len)
    parts = fragment.split(separator)
    pieces: List[str] = []
    for position, part in enumerate(parts):
        piece = (part + (separator if position < len(parts) - 1 else "")).strip()
        if not piece:
            continue
        if len(piece) <= max_len:
            pieces.append(piece)
        else:
            pieces.extend(_split_recursive(piece, remaining, max_len))
    return pieces


class RecursiveChunker(BaseChunker):
    """recursive（递归分块，design §2.7.4；系统默认策略 OP-09）。"""

    def chunk_detailed(
        self, text: str, config: ChunkStrategyConfig
    ) -> ChunkerOutcome:
        validate_chunk_params(config)
        _require_content(text)
        normalized = _normalize_newlines(text)
        separators = (
            list(config.separators)
            if config.separators
            else list(DEFAULT_RECURSIVE_SEPARATORS)
        )
        pieces = _split_recursive(normalized, separators, config.max_chunk_length)
        drafts = _build_drafts(pieces, "recursive")
        if not drafts:
            raise WorkflowValidationError(
                VECTOR_KB_EMPTY_CONTENT, "文档内容为空（纯空白），无法分片"
            )
        return _outcome(drafts, "recursive")


class StructuralChunker(BaseChunker):
    """structural（按文档结构，design §2.7.5）。

    - 默认 Markdown 标题正则 ^#{1,6}\\s；hierarchy 为正则**清单**，
      任一命中即标题行（可覆盖默认）；
    - 标题行随所属章节进入分片，source["heading"] 记录标题行；
    - 无结构信息 → 确定性回退 paragraph + 可见提示（AC-25）；
    - 仅一个标题 → 单分片（含标题）；超大章节按 max_chunk_length 二次切分。
    """

    def chunk_detailed(
        self, text: str, config: ChunkStrategyConfig
    ) -> ChunkerOutcome:
        validate_chunk_params(config)
        _require_content(text)
        normalized = _normalize_newlines(text)
        patterns = (
            list(config.hierarchy)
            if config.hierarchy
            else [DEFAULT_HEADING_PATTERN]
        )
        try:
            heading_res = [re.compile(pattern) for pattern in patterns]
        except re.error as error:
            raise WorkflowValidationError(
                VECTOR_KB_INVALID_CHUNK_PARAMS,
                f"hierarchy 标题正则非法：{error}",
            ) from None
        lines = normalized.split("\n")
        heading_indices = [
            index
            for index, line in enumerate(lines)
            if any(heading_re.match(line) for heading_re in heading_res)
        ]
        if not heading_indices:
            fallback = ParagraphChunker().chunk_detailed(text, config)
            return ChunkerOutcome(
                chunks=fallback.chunks,
                notices=["未检测到文档结构（标题），已回退为按段落分片"],
                strategy_effective="paragraph",
            )
        pieces: List[str] = []
        extra_sources: List[Optional[Dict[str, JsonValue]]] = []

        def _append_section(heading: Optional[str], section: str) -> None:
            if not section.strip():
                return
            if len(section) <= config.max_chunk_length:
                pieces.append(section)
                extra_sources.append({"heading": heading} if heading else None)
                return
            for offset, piece in enumerate(
                _hard_cut(section, config.max_chunk_length)
            ):
                if not piece.strip():
                    continue
                pieces.append(piece)
                extra_sources.append(
                    {"heading": heading} if (heading and offset == 0) else None
                )

        if heading_indices[0] > 0:
            _append_section(
                None, "\n".join(lines[: heading_indices[0]]).strip()
            )
        for order, start_index in enumerate(heading_indices):
            end = (
                heading_indices[order + 1]
                if order + 1 < len(heading_indices)
                else len(lines)
            )
            _append_section(
                lines[start_index].rstrip(),
                "\n".join(lines[start_index:end]).strip(),
            )
        drafts = _build_drafts(pieces, "structural", extra_sources)
        if not drafts:
            raise WorkflowValidationError(
                VECTOR_KB_EMPTY_CONTENT, "文档内容为空（纯空白），无法分片"
            )
        return _outcome(drafts, "structural")


class SemanticChunker(BaseChunker):
    """semantic（按语义，design §2.7.6）。

    - 注入式 EmbeddingProvider（lvd ABC，接口 seam；NV-03 沙箱以假
      provider 验证切分逻辑，真实语义效果不可验证）；
    - 无真实来源 → 显式失败 VECTOR_KB_EMBEDDING_SOURCE_MISSING（引导
      改用 recursive；INV-D08 不伪造语义，AC-27）；
    - 句切 → 相邻 cosine < similarity_threshold 处分片（复用 lvd
      vector_math.cosine_score）；
    - 无句末标点的长文本 → 相似度序列为空 → 降级 paragraph + 提示；
    - 单句文本 → 单分片；超长句组按 max_chunk_length 二次切分。
    """

    def __init__(
        self,
        provider: Optional[EmbeddingProvider] = None,
        threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    ) -> None:
        self._provider = provider
        self._default_threshold = threshold

    def chunk_detailed(
        self, text: str, config: ChunkStrategyConfig
    ) -> ChunkerOutcome:
        validate_chunk_params(config)
        _require_content(text)
        if self._provider is None:
            raise WorkflowValidationError(
                VECTOR_KB_EMBEDDING_SOURCE_MISSING,
                "语义分块需可用 embedding 来源（未注入 provider）；"
                "建议改用 recursive 策略",
            )
        normalized = _normalize_newlines(text)
        sentences = [
            fragment
            for fragment in _SENTENCE_BOUNDARY.split(normalized)
            if fragment.strip()
        ]
        if len(sentences) <= 1:
            if len(normalized) <= config.max_chunk_length:
                drafts = _build_drafts([normalized.strip()], "semantic")
                if not drafts:
                    raise WorkflowValidationError(
                        VECTOR_KB_EMPTY_CONTENT,
                        "文档内容为空（纯空白），无法分片",
                    )
                return _outcome(drafts, "semantic")
            fallback = ParagraphChunker().chunk_detailed(text, config)
            return ChunkerOutcome(
                chunks=fallback.chunks,
                notices=[
                    "未检测到句末标点，语义相似度序列为空，已降级为按段落分片"
                ],
                strategy_effective="paragraph",
            )
        threshold = (
            config.similarity_threshold
            if config.similarity_threshold is not None
            else self._default_threshold
        )
        vectors = [self._provider.embed(sentence) for sentence in sentences]
        groups: List[List[str]] = [[sentences[0]]]
        for position in range(1, len(sentences)):
            similarity = cosine_score(
                vectors[position - 1], vectors[position]
            )
            if similarity < threshold:
                groups.append([sentences[position]])
            else:
                groups[-1].append(sentences[position])
        pieces: List[str] = []
        for group in groups:
            joined = "".join(group)
            if len(joined) <= config.max_chunk_length:
                pieces.append(joined)
            else:
                pieces.extend(_hard_cut(joined, config.max_chunk_length))
        drafts = _build_drafts(pieces, "semantic")
        if not drafts:
            raise WorkflowValidationError(
                VECTOR_KB_EMPTY_CONTENT, "文档内容为空（纯空白），无法分片"
            )
        return _outcome(drafts, "semantic")


def build_chunker(
    strategy: ChunkStrategy,
    provider: Optional[EmbeddingProvider] = None,
    default_similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> Chunker:
    """策略工厂（DC-02）：按策略分派；未知策略 → 确定性拒绝（不静默回退）。

    - semantic 经 provider 注入真实来源（None → 使用时显式失败，
      AC-27）；
    - 其余策略零依赖可达（design §2.7.7）。
    """
    if strategy == "paragraph":
        return ParagraphChunker()
    if strategy == "fixed_length":
        return FixedLengthChunker()
    if strategy == "recursive":
        return RecursiveChunker()
    if strategy == "structural":
        return StructuralChunker()
    if strategy == "semantic":
        return SemanticChunker(
            provider=provider, threshold=default_similarity_threshold
        )
    raise WorkflowValidationError(
        VECTOR_KB_INVALID_CHUNK_PARAMS,
        f"未知分片策略：{strategy!r}"
        "（支持：paragraph/fixed_length/recursive/structural/semantic）",
    )
