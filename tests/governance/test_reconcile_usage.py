# =============================================================================
# 用量对账测试（tests/governance/test_reconcile_usage.py，roadmap B4）
#
# 覆盖（design §8 测试矩阵）：
#   t7 对账零差异（三验收之三）：双源同集合 → 全租户 ok + CLI exit 0；
#   t8 差异检测：用量缺 1 条 → missing_in_usage 非空 + CLI exit 1。
# =============================================================================

import asyncio
import importlib.util
import json
from pathlib import Path

from orchestration_platform.governance.metering.models import TokenUsageRecord
from orchestration_platform.governance.metering.reconcile import (
    collect_runlog_begins_by_tenant,
    collect_usage_by_tenant,
    reconcile,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_CLI_PATH = _PROJECT_ROOT / "scripts" / "reconcile_usage.py"


def _load_cli_module():
    """按路径加载 CLI 脚本模块（scripts/ 无包结构，importlib 惯例）。"""
    spec = importlib.util.spec_from_file_location("reconcile_usage_cli", _CLI_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_usage_record(storage_root: Path, tenant: str, instance_id: str) -> str:
    """写入一条用量记录 JSON，返回 record_id。"""
    record_id = f"usage-{instance_id}"
    tenant_dir = storage_root / "governance" / "token_usage" / tenant
    tenant_dir.mkdir(parents=True, exist_ok=True)
    record = TokenUsageRecord(
        record_id=record_id,
        tenant_id=tenant,
        instance_id=instance_id,
        node_id="execution",
        model_identifier="workflow_engine",
    )
    (tenant_dir / f"{record_id}.json").write_text(
        record.model_dump_json(indent=2), encoding="utf-8"
    )
    return record_id


def _write_runlog_begin(log_dir: Path, tenant: str, run_id: str) -> None:
    """写入一条 workflow_run/running 的 begin 记录（JSONL 逐行）。"""
    log_dir.mkdir(parents=True, exist_ok=True)
    line = json.dumps(
        {
            "record_type": "workflow_run",
            "status": "running",
            "tenant_id": tenant,
            "run_id": run_id,
        },
        ensure_ascii=False,
    )
    with (log_dir / "run.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _isolate_buffer_merge(tmp_path, monkeypatch):
    """隔离缓冲合并路径（防本机 .data/usage_buffer 残留串扰对账集合）。"""
    monkeypatch.setattr(
        "orchestration_platform.governance.metering.reconcile.get_settings",
        lambda: type(
            "_IsolatedSettings",
            (),
            {"metering_buffer_persist_path": str(tmp_path / "no-buffer")},
        )(),
    )


def test_t7_reconcile_zero_diff(tmp_path, monkeypatch, capsys):
    """t7 对账零差异：双源同集合 → 全租户 ok；CLI exit 0。"""
    _isolate_buffer_merge(tmp_path, monkeypatch)
    storage_root = tmp_path / "storage"
    log_dir = tmp_path / "logs"

    for i in (1, 2):
        _write_usage_record(storage_root, "tenant-a", f"inst-{i}")
    _write_runlog_begin(log_dir / "tenant-a" / "default", "tenant-a", "inst-1")
    _write_runlog_begin(log_dir / "tenant-a" / "default", "tenant-a", "inst-2")

    usage = asyncio.run(
        collect_usage_by_tenant("local_file", storage_root=str(storage_root))
    )
    runlog = collect_runlog_begins_by_tenant(str(log_dir))
    entries = reconcile(usage, runlog)

    assert set(entries) == {"tenant-a"}
    entry = entries["tenant-a"]
    assert entry.ok
    assert entry.missing_in_usage == frozenset()
    assert entry.missing_in_runlog == frozenset()
    assert entry.usage_instance_ids == frozenset({"inst-1", "inst-2"})

    cli = _load_cli_module()
    exit_code = cli.main(
        [
            "--storage-backend", "local_file",
            "--storage-root", str(storage_root),
            "--log-dir", str(log_dir),
        ]
    )
    assert exit_code == 0
    assert "零差异" in capsys.readouterr().out


def test_t8_reconcile_diff_detection(tmp_path, monkeypatch, capsys):
    """t8 差异检测：用量缺 1 条 → missing_in_usage 非空；CLI exit 1。"""
    _isolate_buffer_merge(tmp_path, monkeypatch)
    storage_root = tmp_path / "storage"
    log_dir = tmp_path / "logs"

    # 用量侧仅 inst-1；runlog 侧 inst-1 + inst-2 → inst-2 记账缺失
    _write_usage_record(storage_root, "tenant-a", "inst-1")
    _write_runlog_begin(log_dir / "tenant-a" / "default", "tenant-a", "inst-1")
    _write_runlog_begin(log_dir / "tenant-a" / "default", "tenant-a", "inst-2")

    usage = asyncio.run(
        collect_usage_by_tenant("local_file", storage_root=str(storage_root))
    )
    runlog = collect_runlog_begins_by_tenant(str(log_dir))
    entries = reconcile(usage, runlog)

    entry = entries["tenant-a"]
    assert not entry.ok
    assert entry.missing_in_usage == frozenset({"inst-2"})
    assert entry.missing_in_runlog == frozenset()

    cli = _load_cli_module()
    exit_code = cli.main(
        [
            "--storage-backend", "local_file",
            "--storage-root", str(storage_root),
            "--log-dir", str(log_dir),
        ]
    )
    assert exit_code == 1
    captured = capsys.readouterr().out
    assert "missing_in_usage" in captured
    assert "inst-2" in captured
