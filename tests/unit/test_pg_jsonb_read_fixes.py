# =============================================================================
# M5 T2 PG JSONB 读侧修复单测（tests/unit/test_pg_jsonb_read_fixes.py）
#
# 覆盖（design D4，T2）：
#   A. _decode_stored_event_payload（postgres_event_repository）纯函数语义——
#      asyncpg 未配置 JSONB codec 时 payload 列读侧为 str（全量事件 dump）：
#      str → json.loads 取内层 payload 键；dict 全量形态同源；裸 dict /
#      None / 解码失败 → {} 防御；
#   B. _decode_jsonb_dict（postgres_execution_queue）纯函数语义——str →
#      json.loads；None → {}；非 dict / 解码失败 → {}；
#   C. 读侧接线静态断言——_row_to_event / _row_to_job 消费解码助手
#      （此前直接 dict(str) 必崩 / payload={} 丢失，M5 T2 修复验证）。
# 纯函数 + 静态门禁：无 PG 环境可测（test_pg_takeover_atomicity 先例）。
# 执行命令：PYTHONPATH=src python -m pytest tests/unit/test_pg_jsonb_read_fixes.py -q
# =============================================================================

import json
import re
from pathlib import Path

from orchestration_platform.persistence.postgres.postgres_event_repository import (
    _decode_stored_event_payload,
)
from orchestration_platform.persistence.postgres.postgres_execution_queue import (
    _decode_jsonb_dict,
)

_ROOT = Path(__file__).resolve().parents[2]
_EVENT_REPO_SOURCE = (
    _ROOT
    / "src"
    / "orchestration_platform"
    / "persistence"
    / "postgres"
    / "postgres_event_repository.py"
).read_text(encoding="utf-8")
_QUEUE_SOURCE = (
    _ROOT
    / "src"
    / "orchestration_platform"
    / "persistence"
    / "postgres"
    / "postgres_execution_queue.py"
).read_text(encoding="utf-8")


def _method_body(source: str, name: str) -> str:
    """提取源码方法体（静态断言载体）。"""
    match = re.search(
        rf"def {name}\(.*?(?=\n    (?:async )?def |\n\nclass |\Z)",
        source,
        re.S,
    )
    assert match is not None, f"未找到方法 {name}"
    return match.group(0)


# =============================================================================
# A. _decode_stored_event_payload 纯函数语义
# =============================================================================


class TestDecodeStoredEventPayload:

    def test_none_returns_empty(self):
        assert _decode_stored_event_payload(None) == {}

    def test_full_dump_str_yields_inner_payload(self):
        """asyncpg 无 codec 主场景：str 全量事件 dump → 内层 payload。"""
        raw = json.dumps(
            {
                "event_id": "e1",
                "instance_id": "i1",
                "event_type": "instance_started",
                "payload": {"definition_id": "wf-1", "trace_id": "t-1"},
            },
            ensure_ascii=False,
        )
        assert _decode_stored_event_payload(raw) == {
            "definition_id": "wf-1",
            "trace_id": "t-1",
        }

    def test_full_dump_dict_yields_inner_payload(self):
        """codec 已配置时 dict 全量形态同源（内层 payload 键）。"""
        raw = {"event_id": "e1", "payload": {"decision": "approve"}}
        assert _decode_stored_event_payload(raw) == {"decision": "approve"}

    def test_bare_dict_without_payload_key_passthrough(self):
        """容错非全量形态：列直接存载荷 dict 且无外层键 → 原样返回。"""
        raw = {"definition_id": "wf-2"}
        assert _decode_stored_event_payload(raw) == {"definition_id": "wf-2"}

    def test_empty_dict_returns_empty(self):
        assert _decode_stored_event_payload({}) == {}

    def test_corrupt_str_returns_empty(self):
        assert _decode_stored_event_payload("not-json{") == {}

    def test_non_dict_payload_value_returns_empty(self):
        """内层 payload 非 dict → {}（防御既有脏行）。"""
        assert _decode_stored_event_payload(json.dumps({"payload": "oops"})) == {}


# =============================================================================
# B. _decode_jsonb_dict 纯函数语义
# =============================================================================


class TestDecodeJsonbDict:

    def test_none_returns_empty(self):
        assert _decode_jsonb_dict(None) == {}

    def test_dict_returns_copy(self):
        raw = {"a": 1}
        out = _decode_jsonb_dict(raw)
        assert out == {"a": 1}
        assert out is not raw

    def test_str_dict_decodes(self):
        """asyncpg 无 codec 主场景：str → json.loads（此前 dict(str) 必崩）。"""
        assert _decode_jsonb_dict('{"topic": "队列语义"}') == {"topic": "队列语义"}

    def test_str_non_dict_returns_empty(self):
        assert _decode_jsonb_dict("[1, 2]") == {}
        assert _decode_jsonb_dict('"scalar"') == {}

    def test_corrupt_str_returns_empty(self):
        assert _decode_jsonb_dict("{broken") == {}

    def test_unsupported_type_returns_empty(self):
        assert _decode_jsonb_dict(123) == {}


# =============================================================================
# C. 读侧接线静态断言（修复落点验证）
# =============================================================================


class TestReadSideWiringStatic:

    def test_pg_row_to_event_uses_payload_decoder(self):
        body = _method_body(_EVENT_REPO_SOURCE, "_row_to_event")
        assert '_decode_stored_event_payload(row["payload"])' in body, (
            "事件读侧经解码助手取载荷（不再 payload={} 丢失）"
        )

    def test_pg_row_to_job_uses_jsonb_decoder(self):
        body = _method_body(_QUEUE_SOURCE, "_row_to_job")
        assert "_decode_jsonb_dict(" in body, (
            "作业读侧经解码助手取 JSONB dict（不再 dict(str) 崩溃）"
        )
