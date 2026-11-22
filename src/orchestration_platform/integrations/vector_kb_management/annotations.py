# =============================================================================
# 文档标注装配与校验单点（integrations/vector_kb_management/annotations.py）
#
# 职责：向量知识库"切片标注"的**唯一装配/校验路径**（vkb_doc_annotation /
# CHG-01 / design §2.3.2 / INV-19 单点复用）。
# 设计意图（对应 spec FR-04/FR-05/FR-23/FR-24、design DC-03/DC-06/DC-07）：
#   1. 两渠道（后台导入 / 工作流节点导入）**共用本单点**，唯一差异经
#      ``channel`` 参数收敛（后台来源必填拒绝；节点缺省"工作流写入"）；
#   2. 纯函数、零 I/O：**不含**分片 / 向量化 / 写入逻辑（INV-04）；
#   3. 标注承载于 ``source["annotations"]`` 子对象，既有 ``source`` 顶层键
#      零改动（DC-01 / INV-06）；
#   4. ``ingested_at`` 等标注**不参与**幂等键派生（INV-11）。
# 约束：Python 3.10（Literal/Optional/Dict/Mapping，禁 StrEnum/Self/PEP695）。
# =============================================================================

import json
from datetime import datetime
from typing import Callable, Dict, List, Mapping, Optional

from orchestration_platform.domain.errors import (
    VECTOR_KB_ANNOTATION_INVALID,
    VECTOR_KB_ANNOTATION_LIMIT_EXCEEDED,
    VECTOR_KB_ANNOTATION_NAME_CONFLICT,
    VECTOR_KB_ANNOTATION_ORIGIN_REQUIRED,
    WorkflowValidationError,
)
from orchestration_platform.domain.models.common import JsonValue
from orchestration_platform.integrations.vector_kb_management.models import (
    AnnotationChannel,
    AnnotationLimits,
    ChunkRecord,
)

# 标注子对象在 source 中的键名（唯一承载子结构，DC-01）
ANNOTATIONS_KEY = "annotations"

# 既有 source 顶层键（自定义标注名称禁止命中，避免破坏既有语义；FR-23/AC-39）
_SOURCE_TOP_LEVEL_KEYS = frozenset(
    {
        "document_id",
        "index",
        "provenance",
        "local_degraded",
        "truncated",
        "strategy",
    }
)

# annotations 保留子键（自定义标注名称禁止命中；AC-39）
_ANNOTATION_SUB_KEYS = frozenset(
    {"published_at", "ingested_at", "origin", "custom"}
)

# 保留键集合（顶层键 ∪ 保留子键 ∪ 承载键本身）
RESERVED_ANNOTATION_KEYS = frozenset(
    _SOURCE_TOP_LEVEL_KEYS | _ANNOTATION_SUB_KEYS | {ANNOTATIONS_KEY}
)

# 节点渠道来源缺省值（唯一差异的另一半；DC-06/AC-18）
DEFAULT_WORKFLOW_ORIGIN = "工作流写入"

# 自定义标注值 JSON 嵌套深度上界（防超深结构；AC-40）
_MAX_JSON_DEPTH = 8

# 凭据样式名称片段（零凭据校验；INV-09/AC-42）
_CREDENTIAL_NAME_HINTS = (
    "api_key",
    "apikey",
    "secret",
    "token",
    "password",
    "passwd",
    "credential",
    "private_key",
    "access_key",
)

# 路径穿越型字符（自定义标注名称禁用；AC-41）
_FORBIDDEN_NAME_FRAGMENTS = ("/", "\\", "..")


def _is_iso8601(value: str) -> bool:
    """是否为合法 ISO-8601 时间字符串（确定性判定，不做静默归一）。"""
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _json_depth(value: JsonValue) -> int:
    """计算 JSON 值嵌套深度（标量=1）。"""
    if isinstance(value, dict):
        return 1 + max((_json_depth(v) for v in value.values()), default=0)
    if isinstance(value, list):
        return 1 + max((_json_depth(v) for v in value), default=0)
    return 1


def _json_compatible(value: JsonValue) -> bool:
    """值是否 JSON 兼容且嵌套深度受限（否则确定性拒绝，AC-40）。"""
    try:
        json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return False
    return _json_depth(value) <= _MAX_JSON_DEPTH


def _looks_like_credential_name(name: str) -> bool:
    """名称是否疑似凭据字段（零凭据校验；AC-42/INV-09）。"""
    low = name.lower()
    return any(hint in low for hint in _CREDENTIAL_NAME_HINTS)


def validate_custom_annotations(
    custom: Optional[Mapping[str, JsonValue]],
    limits: Optional[AnnotationLimits] = None,
) -> Dict[str, JsonValue]:
    """校验并归一用户自定义标注（FR-23/FR-24；AC-38~AC-42/AC-45）。

    校验项（任一不通过 → 确定性拒绝，返回 0 项）：
    - 名称非空、≤ ``max_name_length``、不命中 ``RESERVED_ANNOTATION_KEYS``、
      无路径穿越型字符、非凭据样式（零凭据）；
    - 值 JSON 兼容且嵌套深度 ≤ ``_MAX_JSON_DEPTH``、序列化长度 ≤
      ``max_value_length``；
    - 项数 ≤ ``max_items``。
    """
    effective_limits = limits if limits is not None else AnnotationLimits()
    if custom is None:
        return {}
    items = dict(custom)
    if len(items) > effective_limits.max_items:
        raise WorkflowValidationError(
            VECTOR_KB_ANNOTATION_LIMIT_EXCEEDED,
            f"自定义标注项数超上界：{len(items)} > {effective_limits.max_items}",
        )
    result: Dict[str, JsonValue] = {}
    for name, value in items.items():
        if not isinstance(name, str) or not name.strip():
            raise WorkflowValidationError(
                VECTOR_KB_ANNOTATION_INVALID, "自定义标注名称不能为空"
            )
        if len(name) > effective_limits.max_name_length:
            raise WorkflowValidationError(
                VECTOR_KB_ANNOTATION_LIMIT_EXCEEDED,
                f"自定义标注名称超上界：{name!r} 长度 {len(name)} > "
                f"{effective_limits.max_name_length}",
            )
        if name in RESERVED_ANNOTATION_KEYS:
            raise WorkflowValidationError(
                VECTOR_KB_ANNOTATION_NAME_CONFLICT,
                f"自定义标注名称命中保留键：{name!r}",
            )
        if any(fragment in name for fragment in _FORBIDDEN_NAME_FRAGMENTS):
            raise WorkflowValidationError(
                VECTOR_KB_ANNOTATION_INVALID,
                f"自定义标注名称含路径穿越型字符：{name!r}",
            )
        if _looks_like_credential_name(name):
            raise WorkflowValidationError(
                VECTOR_KB_ANNOTATION_INVALID,
                f"自定义标注名称疑似凭据字段（零凭据约束）：{name!r}",
            )
        if not _json_compatible(value):
            raise WorkflowValidationError(
                VECTOR_KB_ANNOTATION_INVALID,
                f"自定义标注值非 JSON 兼容或嵌套过深：{name!r}",
            )
        serialized = json.dumps(value, ensure_ascii=False)
        if len(serialized) > effective_limits.max_value_length:
            raise WorkflowValidationError(
                VECTOR_KB_ANNOTATION_LIMIT_EXCEEDED,
                f"自定义标注值超上界：{name!r} 长度 {len(serialized)} > "
                f"{effective_limits.max_value_length}",
            )
        result[name] = value
    return result


def ascii_lowercase(value: str) -> str:
    """仅把 ASCII A–Z→a–z，不动机器可读性外字符（DC-09/AC-33）。"""
    if not isinstance(value, str):
        return value
    return "".join(
        chr(ord(char) + 32) if "A" <= char <= "Z" else char for char in value
    )


def _normalize_published_at(
    published_at: Optional[str], existing_ann: Mapping[str, JsonValue]
) -> Optional[str]:
    """发布时间归一：合法 ISO-8601 原样；"未知"/空 → None；非法 → 拒绝（DC-05）。"""
    if published_at is None:
        existing_value = existing_ann.get("published_at")
        return existing_value if isinstance(existing_value, str) else None
    if not isinstance(published_at, str):
        raise WorkflowValidationError(
            VECTOR_KB_ANNOTATION_INVALID,
            '文档发布时间须为 ISO-8601 字符串或"未知"(null)',
        )
    candidate = published_at.strip()
    if candidate == "" or candidate.lower() == "unknown":
        return None
    if not _is_iso8601(candidate):
        raise WorkflowValidationError(
            VECTOR_KB_ANNOTATION_INVALID,
            f"文档发布时间非合法 ISO-8601：{published_at!r}（不做静默归一）",
        )
    return candidate


def normalize_annotations(
    channel: AnnotationChannel,
    origin: Optional[str],
    published_at: Optional[str],
    custom: Optional[Mapping[str, JsonValue]],
    ingested_at: Optional[str] = None,
    existing: Optional[Mapping[str, JsonValue]] = None,
    limits: Optional[AnnotationLimits] = None,
) -> Dict[str, JsonValue]:
    """标注装配单点（两渠道共用，``channel`` 区分来源规则；DC-03/DC-06）。

    - 来源规则（唯一渠道差异）：``backend`` 缺来源 → 抛
      ``VECTOR_KB_ANNOTATION_ORIGIN_REQUIRED``；``workflow`` 缺来源 →
      ``DEFAULT_WORKFLOW_ORIGIN``，显式值优先；
    - 发布时间：合法 ISO-8601 或 None（"未知"），非法确定性拒绝（DC-05）；
    - 自定义标注：与 ``existing`` 合并（**不同名保留 / 同名覆盖**，AC-27/AC-28），
      合并后项数仍受上界约束；
    - 入库时间：以本次 ``ingested_at`` 为准，未提供则保留既有（DC-04）。
    """
    effective_limits = limits if limits is not None else AnnotationLimits()
    existing_ann = dict(existing or {})

    # 1. 来源规则（唯一差异单点）
    resolved_origin: Optional[str] = None
    if isinstance(origin, str) and origin.strip():
        resolved_origin = origin.strip()
    if resolved_origin is None:
        if channel == "backend":
            raise WorkflowValidationError(
                VECTOR_KB_ANNOTATION_ORIGIN_REQUIRED,
                "后台导入来源信息必填（受理阶段确定性拒绝，不产草稿/不写入）",
            )
        existing_origin = existing_ann.get("origin")
        resolved_origin = (
            existing_origin
            if isinstance(existing_origin, str) and existing_origin.strip()
            else DEFAULT_WORKFLOW_ORIGIN
        )

    # 2. 发布时间归一
    resolved_published = _normalize_published_at(published_at, existing_ann)

    # 3. 自定义标注合并（不同名保留 / 同名覆盖）
    existing_custom: Dict[str, JsonValue] = {}
    raw_existing_custom = existing_ann.get("custom")
    if isinstance(raw_existing_custom, Mapping):
        existing_custom = {str(k): v for k, v in raw_existing_custom.items()}
    incoming_custom = validate_custom_annotations(custom, effective_limits)
    merged_custom: Dict[str, JsonValue] = {**existing_custom, **incoming_custom}
    if len(merged_custom) > effective_limits.max_items:
        raise WorkflowValidationError(
            VECTOR_KB_ANNOTATION_LIMIT_EXCEEDED,
            f"合并后自定义标注项数超上界：{len(merged_custom)} > "
            f"{effective_limits.max_items}",
        )

    # 4. 入库时间（本次为准，未提供保留既有）
    if ingested_at is not None:
        resolved_ingested: Optional[str] = ingested_at
    else:
        existing_ingested = existing_ann.get("ingested_at")
        resolved_ingested = (
            existing_ingested if isinstance(existing_ingested, str) else None
        )

    return {
        "published_at": resolved_published,
        "ingested_at": resolved_ingested,
        "origin": resolved_origin,
        "custom": merged_custom,
    }


def attach_annotations(
    records: List[ChunkRecord],
    annotations_by_chunk: Callable[[ChunkRecord], Dict[str, JsonValue]],
) -> List[ChunkRecord]:
    """把 ``annotations`` 注入 ``ChunkRecord.source``（写入前；DC-03）。

    - **既有 source 顶层键逐字不变**（``dict(record.source or {})`` 整体保留语义）；
    - 返回新列表（``model_copy``），不改入参对象（幂等可重入）。
    """
    attached: List[ChunkRecord] = []
    for record in records:
        source: Dict[str, JsonValue] = dict(record.source or {})
        source[ANNOTATIONS_KEY] = annotations_by_chunk(record)
        attached.append(record.model_copy(update={"source": source}))
    return attached