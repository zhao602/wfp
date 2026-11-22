# =============================================================================
# 运行日志数据模型与路径解析用例（tests/unit/test_run_log_models_pathing.py）
#
# 覆盖验收（tasks T2.3 / design 2.0.1、2.0.2 / INV-11）：
#   1. to_dict() 键集合与键序、非必填缺省不写键（不发 null）；
#   2. 命名规则/消毒（含 ../、中文、空值，不产生路径穿越）；
#   3. 序号格式（_001 起、3 位零填充、seq<=0 无序号段）；
#   4. 活动文件辨识（无序号/最大序号）。
#   全部写入 tmp_path，不产生仓库 logs/。
# =============================================================================

from dataclasses import FrozenInstanceError
from datetime import datetime
from pathlib import Path

import pytest

from orchestration_platform.observability.run_log import fields
from orchestration_platform.observability.run_log.models import (
    NodeRunLogRecord,
    RunLogContext,
    WorkflowRunLogRecord,
)
from orchestration_platform.observability.run_log.pathing import LogPathResolver

WHEN = datetime(2026, 10, 24, 10, 30, 0)


def _make_node_record(**overrides) -> NodeRunLogRecord:
    base = dict(
        run_id="run-001",
        trace_id="trace-001",
        tenant_id="tenant-a",
        business_category="manual",
        level="INFO",
        timestamp="2026-10-24T10:30:00+08:00",
        status="succeeded",
        started_at="2026-10-24T10:30:00+08:00",
        node_id="node_llm_1",
        node_type="llm",
        round_index=0,
        input={"query": "你好"},
    )
    base.update(overrides)
    return NodeRunLogRecord(**base)


def _make_workflow_record(**overrides) -> WorkflowRunLogRecord:
    base = dict(
        run_id="run-001",
        trace_id="trace-001",
        tenant_id="tenant-a",
        business_category="manual",
        level="INFO",
        timestamp="2026-10-24T10:30:00+08:00",
        status="running",
        started_at="2026-10-24T10:30:00+08:00",
        workflow_id="wf-demo",
    )
    base.update(overrides)
    return WorkflowRunLogRecord(**base)


class TestRunLogContext:
    """RunLogContext 为 frozen 数据类，可选字段缺省 None。"""

    def test_frozen_immutable(self):
        context = RunLogContext(
            run_id="run-001",
            trace_id="trace-001",
            tenant_id="tenant-a",
            business_category="manual",
        )
        with pytest.raises(FrozenInstanceError):
            context.run_id = "changed"

    def test_optional_defaults_are_none(self):
        context = RunLogContext(
            run_id="r", trace_id="t", tenant_id="tenant-a", business_category="manual"
        )
        assert context.workflow_id is None
        assert context.definition_version is None


class TestNodeRunLogRecordToDict:
    """NodeRunLogRecord.to_dict() 键集合/键序与 design 2.0.1 一致，不发 null 键。"""

    def test_minimal_keys_skip_optional(self):
        record = _make_node_record()
        data = record.to_dict()
        assert list(data.keys()) == [
            "schema_version", "record_type", "run_id", "trace_id", "tenant_id",
            "business_category", "level", "timestamp", "status", "started_at",
            "node_id", "node_type", "round_index", "input", "subworkflow_depth",
        ]
        assert data["schema_version"] == "1.0"
        assert data["record_type"] == "node_run"
        assert data["subworkflow_depth"] == 0
        assert data["input"] == {"query": "你好"}
        for key in ("finished_at", "duration_ms", "output", "error", "truncated", "payload_note"):
            assert key not in data

    def test_empty_input_object_still_written(self):
        record = _make_node_record(input={})
        data = record.to_dict()
        assert data["input"] == {}

    def test_full_key_order_and_values(self):
        record = _make_node_record(
            finished_at="2026-10-24T10:30:01+08:00",
            duration_ms=1000,
            output={"answer": "ok"},
            error={
                "error_type": "ValueError",
                "error_code": "WF_NODE_EXECUTION_FAILED",
                "error_message": "boom",
                "error_stack": "Traceback ...",
            },
            truncated=True,
            payload_note="input 截断",
            subworkflow_depth=2,
        )
        data = record.to_dict()
        assert list(data.keys()) == list(fields.COMMON_FIELDS) + list(fields.NODE_RUN_FIELDS)
        assert all(value is not None for value in data.values())
        assert data["truncated"] is True
        assert data["error"]["error_code"] == "WF_NODE_EXECUTION_FAILED"


class TestWorkflowRunLogRecordToDict:
    """WorkflowRunLogRecord.to_dict() 键集合/键序与 design 2.0.1 一致。"""

    def test_running_record_omits_terminal_fields(self):
        record = _make_workflow_record()
        data = record.to_dict()
        assert list(data.keys()) == [
            "schema_version", "record_type", "run_id", "trace_id", "tenant_id",
            "business_category", "level", "timestamp", "status", "started_at",
            "workflow_id",
        ]
        assert data["record_type"] == "workflow_run"
        assert data["status"] == "running"
        for key in (
            "finished_at", "duration_ms", "definition_version",
            "trigger_type", "node_summary",
        ):
            assert key not in data

    def test_full_key_order_and_node_summary(self):
        record = _make_workflow_record(
            finished_at="2026-10-24T10:31:00+08:00",
            duration_ms=60000,
            definition_version=3,
            trigger_type="manual",
            node_summary={"total": 3, "succeeded": 2, "failed": 1, "timeout": 0},
        )
        data = record.to_dict()
        assert list(data.keys()) == list(fields.COMMON_FIELDS) + list(fields.WORKFLOW_RUN_FIELDS)
        assert data["node_summary"]["total"] == 3


class TestSanitizeSegment:
    """消毒规则：转小写、非法字符→_、去首尾 _、空值→default。"""

    def test_lowercase_and_space(self):
        resolver = LogPathResolver("./logs")
        assert resolver.sanitize_segment("Tenant A") == "tenant_a"
        assert resolver.sanitize_segment("Tenant-01") == "tenant-01"

    def test_traversal_and_specials(self):
        resolver = LogPathResolver("./logs")
        assert resolver.sanitize_segment("../etc/passwd") == "etc_passwd"
        assert resolver.sanitize_segment("..\\win\\path") == "win_path"

    def test_chinese_to_underscore(self):
        resolver = LogPathResolver("./logs")
        assert resolver.sanitize_segment("租户A") == "a"
        assert resolver.sanitize_segment("数据事件") == "default"

    def test_empty_and_none_fallback(self):
        resolver = LogPathResolver("./logs")
        assert resolver.sanitize_segment("") == "default"
        assert resolver.sanitize_segment(None) == "default"
        assert resolver.sanitize_segment("___") == "default"
        assert resolver.sanitize_segment("   ") == "default"


class TestResolve:
    """命名模板 {record_type}_{category}_{yyyyMMdd}[_{seq}].jsonl 与租户层级。"""

    def test_basic_layout_and_filename(self, tmp_path):
        resolver = LogPathResolver(str(tmp_path))
        path = resolver.resolve("node_run", "tenant-a", "manual", WHEN)
        assert path == tmp_path / "tenant-a" / "manual" / "node_run_manual_20261024.jsonl"

    def test_seq_zero_padded_and_disabled(self, tmp_path):
        resolver = LogPathResolver(str(tmp_path))
        base = tmp_path / "tenant-a" / "manual"
        assert resolver.resolve("node_run", "tenant-a", "manual", WHEN, seq=1) == (
            base / "node_run_manual_20261024_001.jsonl"
        )
        assert resolver.resolve("node_run", "tenant-a", "manual", WHEN, seq=12) == (
            base / "node_run_manual_20261024_012.jsonl"
        )
        assert resolver.resolve("node_run", "tenant-a", "manual", WHEN, seq=0) == (
            base / "node_run_manual_20261024.jsonl"
        )
        assert resolver.resolve("node_run", "tenant-a", "manual", WHEN, seq=-3) == (
            base / "node_run_manual_20261024.jsonl"
        )

    def test_no_traversal_for_hostile_inputs(self, tmp_path):
        resolver = LogPathResolver(str(tmp_path))
        path = resolver.resolve("node_run", "../../evil", "../escape", WHEN)
        assert path.is_relative_to(tmp_path)
        assert ".." not in path.parts
        hostile_record_type = resolver.resolve("../evil", "tenant-a", "manual", WHEN)
        assert hostile_record_type.is_relative_to(tmp_path)
        assert ".." not in hostile_record_type.parts

    def test_no_io_side_effect(self, tmp_path):
        resolver = LogPathResolver(str(tmp_path))
        resolver.resolve("node_run", "tenant-a", "manual", WHEN)
        assert not (tmp_path / "tenant-a").exists()


class TestActivePath:
    """活动文件辨识：指定日期下最大序号优先，其次无序号文件。"""

    def _touch(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")

    def test_none_when_directory_missing(self, tmp_path):
        resolver = LogPathResolver(str(tmp_path))
        assert resolver.active_path("node_run", "tenant-a", "manual", WHEN) is None

    def test_no_seq_file_is_active(self, tmp_path):
        resolver = LogPathResolver(str(tmp_path))
        no_seq = resolver.resolve("node_run", "tenant-a", "manual", WHEN)
        self._touch(no_seq)
        assert resolver.active_path("node_run", "tenant-a", "manual", WHEN) == no_seq

    def test_no_seq_wins_over_archives(self, tmp_path):
        resolver = LogPathResolver(str(tmp_path))
        no_seq = resolver.resolve("node_run", "tenant-a", "manual", WHEN)
        seq_1 = resolver.resolve("node_run", "tenant-a", "manual", WHEN, seq=1)
        seq_7 = resolver.resolve("node_run", "tenant-a", "manual", WHEN, seq=7)
        for path in (no_seq, seq_1, seq_7):
            self._touch(path)
        assert resolver.active_path("node_run", "tenant-a", "manual", WHEN) == no_seq

    def test_only_seq_file_active(self, tmp_path):
        resolver = LogPathResolver(str(tmp_path))
        seq_2 = resolver.resolve("node_run", "tenant-a", "manual", WHEN, seq=2)
        self._touch(seq_2)
        assert resolver.active_path("node_run", "tenant-a", "manual", WHEN) == seq_2

    def test_ignores_other_dates_and_categories(self, tmp_path):
        resolver = LogPathResolver(str(tmp_path))
        other_day = resolver.resolve("node_run", "tenant-a", "manual", datetime(2026, 10, 23))
        other_category = resolver.resolve("node_run", "tenant-a", "scheduled", WHEN)
        for path in (other_day, other_category):
            self._touch(path)
        assert resolver.active_path("node_run", "tenant-a", "manual", WHEN) is None

    def test_scan_seq_indices_sorted(self, tmp_path):
        resolver = LogPathResolver(str(tmp_path))
        for seq in (3, 1, 10):
            self._touch(resolver.resolve("node_run", "tenant-a", "manual", WHEN, seq=seq))
        self._touch(resolver.resolve("node_run", "tenant-a", "manual", WHEN))
        assert resolver.scan_seq_indices("node_run", "tenant-a", "manual", WHEN) == [1, 3, 10]
