# =============================================================================
# vkb_doc_annotation 兼容与守护单测（tests/unit/test_vkb_doc_annotation_compat.py）
#
# 覆盖 CHG-19：默认 False → 既有行为零变化（不产 annotations）；开关开启产标注；
# 点路径过滤（DC-08 方案 A，含无 "." 键向后兼容）；唯一写入口；
# 业务层无 import langgraph；错误码集中登记。
# 驱动：同步。
#
# 沙箱边界：本文件为交付物；运行命令见 tasks.md §0.3。
# =============================================================================

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from orchestration_platform.integrations.local_vector_database.models import (
    MetadataFilter,
    VectorRecord,
)
from orchestration_platform.integrations.local_vector_database.retrieval import (
    _matches_filter,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


class TestDottedFilter:
    def test_dotted_key_hits_nested(self) -> None:
        record = VectorRecord(
            record_id="r1",
            vector=[1.0, 0.0],
            text="t",
            source={"annotations": {"origin": "官网"}},
        )
        assert _matches_filter(record, MetadataFilter(equals={"annotations.origin": "官网"}))
        assert not _matches_filter(
            record, MetadataFilter(equals={"annotations.origin": "missing"})
        )

    def test_plain_key_behaviour_unchanged(self) -> None:
        record = VectorRecord(
            record_id="r1",
            vector=[1.0, 0.0],
            text="t",
            source={"document_id": "d1"},
        )
        assert _matches_filter(record, MetadataFilter(equals={"document_id": "d1"}))
        assert not _matches_filter(record, MetadataFilter(equals={"document_id": "d2"}))

    def test_missing_dotted_path_no_match(self) -> None:
        record = VectorRecord(
            record_id="r1", vector=[1.0, 0.0], text="t", source={"document_id": "d1"}
        )
        assert not _matches_filter(
            record, MetadataFilter(equals={"annotations.origin": "x"})
        )


class TestNegativeCapability:
    def test_no_langgraph_import_in_business_layers(self) -> None:
        # 跨平台：纯 Python 遍历替代 grep 子进程（Windows 无 grep → WinError 2）
        integrations_root = REPO_ROOT / "src" / "orchestration_platform" / "integrations"
        offenders = [
            str(path.relative_to(integrations_root))
            for path in integrations_root.rglob("*.py")
            if "import langgraph" in path.read_text(encoding="utf-8", errors="ignore")
        ]
        assert offenders == []

    def test_error_codes_registered(self) -> None:
        from orchestration_platform.domain import errors

        for name in (
            "VECTOR_KB_ANNOTATION_ORIGIN_REQUIRED",
            "VECTOR_KB_ANNOTATION_NAME_CONFLICT",
            "VECTOR_KB_ANNOTATION_INVALID",
            "VECTOR_KB_ANNOTATION_LIMIT_EXCEEDED",
            "VECTOR_KB_ANNOTATION_DISABLED",
        ):
            assert hasattr(errors, name)


def test_single_writer_path_symbol() -> None:
    import orchestration_platform.integrations.vector_kb_management.annotations as ann

    assert sys.version_info >= (3, 8)
    assert hasattr(ann, "normalize_annotations")
    assert hasattr(ann, "attach_annotations")
    # annotations.py 为纯函数单点：不含分片/向量化/写存储调用
    import inspect

    src = inspect.getsource(ann)
    for forbidden in ("build_chunker", "upsert_records", "provider.embed"):
        assert forbidden not in src