# =============================================================================
# 备份器（scripts/ops/backup.py，B15 S4，design D7）
#
# 单项目一致性备份三件套：
#   1. db.dump：docker exec <project>-postgres-1 pg_dump -U <user> -Fc <db>
#      （custom 格式，pg_restore 配对；二进制走文件句柄，不经 shell 管道）；
#   2. api-data / api-logs 卷 tar.gz（alpine:3.20 digest 锁辅助容器，:ro 挂载）；
#   3. manifest.json（各件 sha256/尺寸、compose 项目名、pg_dump 版本、创建时间）
#      + SHA256SUMS（含 manifest 自身；SHA256SUMS 自身除外）。
#
# 纪律：postgres-data 卷不做物理备份（DB 一致性走逻辑转储）；--keep N 清老备份。
# 前置：目标项目栈在跑（postgres 容器健康）；本脚本不依赖 compose 文件
# （容器名/卷名按 compose v2 命名约定 <project>-<service>-1 / <project>_<volume>）。
# =============================================================================

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ALPINE_REF = "alpine:3.20@sha256:d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc"
BACKUP_ROOT_DEFAULT = Path("backups")
VOLUMES = ["api-data", "api-logs"]


def compute_file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_checked(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    if result.returncode != 0:
        print(f"[backup] 命令失败（exit={result.returncode}）：{' '.join(cmd[:6])}…")
        print((result.stderr or result.stdout or "")[-800:])
        sys.exit(1)
    return result


def postgres_container(project: str) -> str:
    return f"{project}-postgres-1"


def volume_name(project: str, volume: str) -> str:
    return f"{project}_{volume}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="compose 项目一致性备份（design D7）")
    parser.add_argument("--project", required=True, help="compose 项目名（如 b15-s4）")
    parser.add_argument("--output", default=None, help="输出目录（默认 backups/<UTC时间戳>/）")
    parser.add_argument("--db-user", default="orchestration", help="POSTGRES_USER（默认 orchestration）")
    parser.add_argument("--db-name", default="orchestration_platform", help="POSTGRES_DB（默认 orchestration_platform）")
    parser.add_argument("--keep", type=int, default=None, help="保留最近 N 份备份，超出清理（默认不清）")
    args = parser.parse_args(argv)

    # Windows 控制台默认 GBK 等本地代码页：不可编码字符以 ? 替代，防崩溃
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")

    started = time.monotonic()
    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_root = Path(args.output).resolve() if args.output else (BACKUP_ROOT_DEFAULT / ts).resolve()
    if out_root.exists():
        shutil.rmtree(out_root)
    out_root.mkdir(parents=True)
    print(f"[backup] project={args.project} output={out_root}")

    container = postgres_container(args.project)

    # 0. 前置探测：postgres 容器在跑 + 卷存在（早失败）
    run_checked(["docker", "inspect", container, "--format", "{{.Name}}"])
    for volume in VOLUMES:
        run_checked(["docker", "volume", "inspect", volume_name(args.project, volume)])
    print("[backup] 前置探测 OK（postgres 容器 + 2 卷在位）")

    # 1. pg_dump -Fc（二进制走文件句柄，不经 shell 管道）
    t0 = time.monotonic()
    dump_path = out_root / "db.dump"
    with open(dump_path, "wb") as fh:
        result = subprocess.run(
            ["docker", "exec", container, "pg_dump", "-U", args.db_user, "-Fc", args.db_name],
            stdout=fh,
            stderr=subprocess.PIPE,
        )
    if result.returncode != 0:
        print("[backup] pg_dump 失败：" + result.stderr.decode(errors="replace")[-800:])
        sys.exit(1)
    dump_size = dump_path.stat().st_size
    pg_version = subprocess.run(
        ["docker", "exec", container, "pg_dump", "--version"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    print(f"[backup] pg_dump: {dump_size}B（{time.monotonic() - t0:.1f}s）{pg_version}")

    # 2. 卷 tar（alpine digest 锁；:ro 挂载防误写；stdout 二进制管道落盘——
    #    不做宿主目录 bind mount：DOCKER_HOST 裸穿/WSL/npipe/Linux 任意拓扑下
    #    Windows 风格路径均无翻译保障，管道与 pg_dump 同构、拓扑无关）
    items = [{
        "file": "db.dump", "kind": "pg_dump", "sha256": compute_file_sha256(dump_path), "size": dump_size,
    }]
    for volume in VOLUMES:
        t0 = time.monotonic()
        dest = out_root / f"{volume}.tar.gz"
        with open(dest, "wb") as fh:
            result = subprocess.run(
                ["docker", "run", "--rm",
                 "-v", f"{volume_name(args.project, volume)}:/data:ro",
                 ALPINE_REF, "tar", "czf", "-", "-C", "/data", "."],
                stdout=fh, stderr=subprocess.PIPE,
            )
        if result.returncode != 0:
            print("[backup] 卷 tar 失败：" + result.stderr.decode(errors="replace")[-800:])
            sys.exit(1)
        size = dest.stat().st_size
        items.append({
            "file": f"{volume}.tar.gz", "kind": "volume-tar",
            "volume": volume_name(args.project, volume),
            "sha256": compute_file_sha256(dest), "size": size,
        })
        print(f"[backup] tar {volume}: {size}B（{time.monotonic() - t0:.1f}s）")

    # 3. manifest（备份为时点产物，含 created_at；与离线包 manifest 的无时间戳红线不同域）
    manifest = {
        "schema": "backup-manifest/1",
        "project": args.project,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "pg_dump_version": pg_version,
        "postgres_container": container,
        "db": {"user": args.db_user, "name": args.db_name, "format": "custom (-Fc)"},
        "volumes": VOLUMES,
        "items": items,
    }
    manifest_path = out_root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # 4. SHA256SUMS（全部文件；SHA256SUMS 自身除外）
    sums_lines = []
    for path in sorted(out_root.iterdir()):
        if path.name == "SHA256SUMS":
            continue
        sums_lines.append(f"{compute_file_sha256(path)}  {path.name}")
    (out_root / "SHA256SUMS").write_text("\n".join(sums_lines) + "\n", encoding="utf-8")

    # 5. --keep N 清老备份（按目录名 UTC 时间戳排序）
    if args.keep is not None and not args.output:
        backups = sorted(p for p in BACKUP_ROOT_DEFAULT.iterdir() if p.is_dir())
        for old in backups[: max(0, len(backups) - args.keep)]:
            shutil.rmtree(old)
            print(f"[backup] --keep {args.keep}：清理旧备份 {old.name}")

    total = sum(p.stat().st_size for p in out_root.iterdir())
    print(
        f"[backup] 完成：{len(items) + 2} 文件 / {total / 1024 / 1024:.1f} MB / {time.monotonic() - started:.1f}s"
        f"（db.dump {dump_size}B + 2 卷 tar + manifest + SHA256SUMS）"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
