# =============================================================================
# 用量对账脚本（scripts/reconcile_usage.py）
#
# 目标（roadmap_b4_metering_quota，DC-B4-05）：
#   比对执行级用量记录（仓储 + 未 flush 缓冲）与运行日志 begin 记录，
#   输出全租户差异视图，供对账验收（零差异 exit 0）与运维巡检。
#
# 前提声明：
#   - metering_enabled=False 时用量侧为空，对账必差异（脚本不替代计量开关语义）；
#   - postgres 模式走仓储 _admin_connection 旁路（app.bypass_rls='on'，
#     B1 运维通道）跨租户管理读；
#   - local_file 模式扫描 <root>/governance/token_usage/ 与缓冲持久化文件。
#
# 用法：
#   python scripts/reconcile_usage.py                        # 缺省后端/路径取 settings
#   python scripts/reconcile_usage.py --storage-backend local_file
#   python scripts/reconcile_usage.py --storage-root .data --log-dir logs
#   python scripts/reconcile_usage.py --json                 # 机器可读输出
#
# 退出码：0 = 全租户零差异；1 = 存在差异；2 = 环境异常（仓储不可达等）。
# =============================================================================

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.governance.metering.reconcile import (
    ReconcileEnvironmentError,
    collect_runlog_begins_by_tenant,
    collect_usage_by_tenant,
    reconcile,
)


def _build_arg_parser() -> argparse.ArgumentParser:
    """构造命令行参数解析器（后端/路径缺省全部取 settings）。"""
    parser = argparse.ArgumentParser(
        prog="reconcile_usage",
        description="执行级用量记录与运行日志 begin 记录对账（roadmap B4）",
    )
    parser.add_argument(
        "--storage-backend",
        choices=["local_file", "postgres"],
        default=None,
        help="用量存储后端（缺省取 settings.storage_backend）",
    )
    parser.add_argument(
        "--storage-root",
        default=None,
        help="本地存储根目录（local_file 模式；缺省取 settings.local_storage_root）",
    )
    parser.add_argument(
        "--log-dir",
        default=None,
        help="运行日志根目录（缺省取 settings.log_dir）",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="机器可读 JSON 输出（默认人类可读文本）",
    )
    return parser


def _entries_to_json(entries: dict) -> str:
    """差异视图序列化为 JSON（机器可读）。"""
    payload = {
        tenant_id: {
            "ok": entry.ok,
            "usage_count": len(entry.usage_instance_ids),
            "runlog_count": len(entry.runlog_run_ids),
            "missing_in_usage": sorted(entry.missing_in_usage),
            "missing_in_runlog": sorted(entry.missing_in_runlog),
        }
        for tenant_id, entry in entries.items()
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _entries_to_text(entries: dict) -> str:
    """差异视图序列化为人类可读文本。"""
    ok_count = sum(1 for entry in entries.values() if entry.ok)
    lines = [
        f"对账租户数: {len(entries)}"
        f"（零差异 {ok_count} / 差异 {len(entries) - ok_count}）"
    ]
    for tenant_id, entry in sorted(entries.items()):
        if entry.ok:
            lines.append(
                f"  [{tenant_id}] 零差异（{len(entry.usage_instance_ids)} 执行）"
            )
            continue
        lines.append(f"  [{tenant_id}] 差异:")
        if entry.missing_in_usage:
            lines.append(
                f"    missing_in_usage  ({len(entry.missing_in_usage)}): "
                f"{sorted(entry.missing_in_usage)}"
            )
        if entry.missing_in_runlog:
            lines.append(
                f"    missing_in_runlog ({len(entry.missing_in_runlog)}): "
                f"{sorted(entry.missing_in_runlog)}"
            )
    return "\n".join(lines)


async def _run_async(args: argparse.Namespace) -> int:
    """对账主流程：采集双源 → 全租户并集对账 → 输出差异视图。"""
    settings = get_settings()
    storage_backend = args.storage_backend or settings.storage_backend
    log_dir = args.log_dir or settings.log_dir

    try:
        usage = await collect_usage_by_tenant(storage_backend, args.storage_root)
    except ReconcileEnvironmentError as exc:
        print(f"[reconcile_usage] 环境异常: {exc}", file=sys.stderr)
        return 2

    runlog = collect_runlog_begins_by_tenant(log_dir)
    entries = reconcile(usage, runlog)

    if args.json:
        print(_entries_to_json(entries))
    else:
        print(_entries_to_text(entries))

    return 0 if all(entry.ok for entry in entries.values()) else 1


def main(argv: list | None = None) -> int:
    """CLI 入口：返回 0（零差异）/ 1（差异）/ 2（环境异常）。"""
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    try:
        return asyncio.run(_run_async(args))
    except ReconcileEnvironmentError as exc:
        print(f"[reconcile_usage] 环境异常: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 —— CLI 兜底口径（exit 2）
        print(f"[reconcile_usage] 未捕获异常: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
