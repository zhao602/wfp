# =============================================================================
# vkb_doc_annotation 装配单点单测（tests/unit/test_vkb_doc_annotation_annotations.py）
#
# 覆盖 CHG-14：渠道规则（后台必填拒绝 / 节点默认"工作流写入"/显式覆盖）/
# 发布时间 null 与 ISO-8601 / 非法时间拒绝 / 自定义名称白名单与保留键冲突 /
# JSON 兼容 / 上界 / 零凭据 / 合并（不同名保留、同名覆盖）/ ascii_lowercase 仅 A–Z。
# 驱动：同步（纯函数，无事件循环）。
#
# 沙箱边界：本文件为交付物；运行命令见 tasks.md §0.3。
# =============================================================================

from __future__ import annotations

import pytest

from orchestration_platform.domain.errors import (
    VECTOR_KB_ANNOTATION_INVALID,
    VECTOR_KB_ANNOTATION_LIMIT_EXCEEDED,
    VECTOR_KB_ANNOTATION_NAME_CONFLICT,
    VECTOR_KB_ANNOTATION_ORIGIN_REQUIRED,
    WorkflowValidationError,
)
from orchestration_platform.integrations.vector_kb_management.annotations import (
    RESERVED_ANNOTATION_KEYS,
    ascii_lowercase,
    attach_annotations,
    normalize_annotations,
    validate_custom_annotations,
)
from orchestration_platform.integrations.vector_kb_management.models import (
    AnnotationLimits,
    ChunkRecord,
)


class TestChannelRules:
    def test_backend_missing_origin_rejected(self) -> None:
        with pytest.raises(WorkflowValidationError) as exc:
            normalize_annotations("backend", None, None, None)
        assert exc.value.code == VECTOR_KB_ANNOTATION_ORIGIN_REQUIRED

    def test_backend_blank_origin_rejected(self) -> None:
        with pytest.raises(WorkflowValidationError) as exc:
            normalize_annotations("backend", "   ", None, None)
        assert exc.value.code == VECTOR_KB_ANNOTATION_ORIGIN_REQUIRED

    def test_workflow_missing_origin_defaults(self) -> None:
        result = normalize_annotations("workflow", None, None, None)
        assert result["origin"] == "工作流写入"

    def test_workflow_explicit_origin_wins(self) -> None:
        result = normalize_annotations("workflow", "自定义", None, None)
        assert result["origin"] == "自定义"


class TestPublishedAt:
    def test_unknown_maps_to_none(self) -> None:
        result = normalize_annotations("workflow", "S", "", None)
        assert result["published_at"] is None
        result2 = normalize_annotations("workflow", "S", "unknown", None)
        assert result2["published_at"] is None

    def test_valid_iso8601_kept(self) -> None:
        result = normalize_annotations("workflow", "S", "2024-01-01", None)
        assert result["published_at"] == "2024-01-01"

    def test_invalid_published_at_rejected(self) -> None:
        with pytest.raises(WorkflowValidationError) as exc:
            normalize_annotations("workflow", "S", "not-a-date", None)
        assert exc.value.code == VECTOR_KB_ANNOTATION_INVALID


class TestCustomAnnotations:
    def test_reserved_key_conflict(self) -> None:
        with pytest.raises(WorkflowValidationError) as exc:
            validate_custom_annotations({"origin": "x"})
        assert exc.value.code == VECTOR_KB_ANNOTATION_NAME_CONFLICT

    def test_source_top_level_reserved(self) -> None:
        for name in ("document_id", "index", "provenance"):
            assert name in RESERVED_ANNOTATION_KEYS

    def test_path_traversal_rejected(self) -> None:
        with pytest.raises(WorkflowValidationError) as exc:
            validate_custom_annotations({"bad/name": "x"})
        assert exc.value.code == VECTOR_KB_ANNOTATION_INVALID

    def test_credential_like_name_rejected(self) -> None:
        with pytest.raises(WorkflowValidationError) as exc:
            validate_custom_annotations({"api_key": "x"})
        assert exc.value.code == VECTOR_KB_ANNOTATION_INVALID

    def test_value_length_limit(self) -> None:
        limits = AnnotationLimits(max_value_length=8)
        with pytest.raises(WorkflowValidationError) as exc:
            validate_custom_annotations({"k": "x" * 20}, limits)
        assert exc.value.code == VECTOR_KB_ANNOTATION_LIMIT_EXCEEDED

    def test_items_limit(self) -> None:
        limits = AnnotationLimits(max_items=1)
        with pytest.raises(WorkflowValidationError) as exc:
            validate_custom_annotations({"a": "1", "b": "2"}, limits)
        assert exc.value.code == VECTOR_KB_ANNOTATION_LIMIT_EXCEEDED

    def test_merge_keep_and_override(self) -> None:
        result = normalize_annotations(
            "workflow",
            "S",
            None,
            {"k": "new"},
            existing={"custom": {"k": "old", "keep": "1"}},
        )
        assert result["custom"] == {"k": "new", "keep": "1"}


class TestAsciiLowercase:
    def test_only_ascii_upper_converted(self) -> None:
        assert ascii_lowercase("AbC中文Xyz123") == "abc中文xyz123"

    def test_symbols_untouched(self) -> None:
        assert ascii_lowercase("A-B_C.D") == "a-b_c.d"


class TestAttachAnnotations:
    def test_attaches_without_touching_top_level_keys(self) -> None:
        record = ChunkRecord(
            chunk_id="d:0001",
            index=0,
            text="t",
            source={"document_id": "d", "index": 1, "provenance": "api"},
        )
        out = attach_annotations([record], lambda _r: {"origin": "S"})
        assert out[0].source is not None
        assert out[0].source["document_id"] == "d"
        assert out[0].source["provenance"] == "api"
        assert out[0].source["annotations"] == {"origin": "S"}
        # 不改入参对象（幂等可重入）
        assert record.source is not None
        assert "annotations" not in record.source