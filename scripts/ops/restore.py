# =============================================================================
# 恢复器（scripts/ops/restore.py，B15 S4，design D7）
#
# 与 backup.py 配对：校验和 → 项目名一致性闸 → compose down（保卷）→
# 卷 tar 解回（清后还原）→ postgres 单服务起 + healthy 闸 →
# pg_restore --clean --if-exists（stdin 文件句柄）→ 全量 up --no-build →
# healthz 探活 → RTO 分项报告。
#
# 纪律：
#   - --confirm 必须：缺省只打印计划即退出（危险操作双闸）；
#   - manifest.project 与 --project 不一致即中止（防跨项目恢复）；
#   - 校验和任一坏行即中止（备份集不可信不进入恢复）；
#   - healthz 探活强制直连（ProxyHandler({})，防目标机 client 代理劫持，
#     参照 _drills/b15_offline_drill.md 缺口②教训）；
#   - postgres-data 卷不做物理还原（DB 走逻辑恢复；fresh initdb 与既有库
#     两条路径均由 pg_restore --clean --if-exists 覆盖）。
# 前置：CWD 为 compose 目录（或 --compose-file/--env-file 指路）；镜像在位。
# =============================================================================

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ALPINE_REF = "alpine:3.20@sha256:d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc"
VOLUMES = ["api-data", "api-logs"]
POSTGRES_HEALTH_TIMEOUT_S = 120
HEALTHZ_TIMEOUT_S = 180
HEALTHZ_INTERVAL_S = 3


def compute_file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_sums(backup_dir: Path) -> list[str]:
    sums_path = backup_dir / "SHA256SUMS"
    if not sums_path.exists():
        print("[restore] 缺 SHA256SUMS，备份集不完整，中止。")
        sys.exit(1)
    bad: list[str] = []
    entries: list[tuple[str, str]] = []
    for line in sums_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        expected, name = line.split("  ", 1)
        entries.append((expected, name))
    for expected, name in entries:
        path = backup_dir / name
        if not path.exists():
            bad.append(f"{name} 缺失")
            continue
        actual = compute_file_sha256(path)
        if actual != expected:
            bad.append(f"{name} 校验和不符（期望 {expected[:12]}… 实得 {actual[:12]}…）")
    if bad:
        for item in bad:
            print(f"[restore] 校验失败：{item}")
        sys.exit(1)
    print(f"[restore] 校验和 {len(entries)} 项全部通过。")
    return [name for _, name in entries]


def run_checked(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if result.returncode != 0:
        print(f"[restore] 命令失败（exit={result.returncode}）：{' '.join(cmd[:7])}…")
        print((result.stderr or result.stdout or "")[-800:])
        sys.exit(1)
    return result


def read_env_port(env_file: Path, fallback: int) -> int:
    if not env_file.exists():
        return fallback
    for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("API_PORT="):
            try:
                return int(line.split("=", 1)[1].strip().strip('"'))
            except ValueError:
                return fallback
    return fallback


def wait_postgres_healthy(container: str, timeout_s: int) -> float:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout_s:
        result = subprocess.run(
            ["docker", "inspect", container, "--format", "{{.State.Health.Status}}"],
            capture_output=True, text=True,
        )
        if result.returncode == 0 and result.stdout.strip() == "healthy":
            return time.monotonic() - t0
        time.sleep(2)
    print(f"[restore] postgres {timeout_s}s 内未 healthy，中止。")
    sys.exit(1)


def wait_healthz(port: int, timeout_s: int) -> float:
    t0 = time.monotonic()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while time.monotonic() - t0 < timeout_s:
        try:
            with opener.open(f"http://127.0.0.1:{port}/healthz", timeout=5) as resp:
                if resp.status == 200:
                    return time.monotonic() - t0
        except OSError:
            pass
        time.sleep(HEALTHZ_INTERVAL_S)
    print(f"[restore] healthz {timeout_s}s 内未 200（127.0.0.1:{port}），中止。")
    sys.exit(1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从 backup.py 备份集恢复（design D7，需 --confirm）")
    parser.add_argument("--from", dest="backup_dir", required=True, help="备份目录（含 manifest.json/SHA256SUMS）")
    parser.add_argument("--project", required=True, help="compose 项目名（须与 manifest.project 一致）")
    parser.add_argument("--env-file", default=".env.production", help="compose env 文件（默认 .env.production）")
    parser.add_argument("--compose-file", default="docker-compose.yml", help="compose 文件（默认 docker-compose.yml）")
    parser.add_argument("--db-user", default="orchestration")
    parser.add_argument("--db-name", default="orchestration_platform")
    parser.add_argument("--healthz-port", type=int, default=None, help="覆盖探活端口（默认解析 env 文件 API_PORT，回退 8000）")
    parser.add_argument("--confirm", action="store_true", help="确认执行（缺省仅打印计划）")
    args = parser.parse_args(argv)

    # Windows 控制台默认 GBK 等本地代码页：不可编码字符以 ? 替代，防崩溃
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")

    backup_dir = Path(args.backup_dir).resolve()
    compose_file = Path(args.compose_file).resolve()
    env_file = Path(args.env_file).resolve()
    compose_cwd = compose_file.parent

    # 1. 校验和（坏即止）
    verify_sums(backup_dir)

    # 2. manifest + 项目名一致性闸
    manifest_path = backup_dir / "manifest.json"
    if not manifest_path.exists():
        print("[restore] 缺 manifest.json，中止。")
        sys.exit(1)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("project") != args.project:
        print(
            f"[restore] 项目名不一致：manifest={manifest.get('project')} vs --project={args.project}，"
            f"防跨项目恢复，中止。"
        )
        sys.exit(1)
    port = args.healthz_port if args.healthz_port is not None else read_env_port(env_file, 8000)
    db_user = manifest.get("db", {}).get("user", args.db_user)
    db_name = manifest.get("db", {}).get("name", args.db_name)

    plan = [
        f"备份集：{backup_dir}（{manifest.get('created_at', '?')} 创建，pg_dump {manifest.get('pg_dump_version', '?')}）",
        f"项目：{args.project}（compose={compose_file.name}，env={env_file.name}，healthz 端口={port}）",
        "步骤：校验和通过 → compose down（保卷）→ 卷 tar 解回 ×2 → postgres 起+healthy → "
        "pg_restore --clean --if-exists → up --no-build → healthz 200",
    ]
    for line in plan:
        print("[restore] " + line)
    if not args.confirm:
        print("[restore] 未传 --confirm，仅打印计划，未执行。")
        return 0

    rto: dict[str, float] = {}
    total_t0 = time.monotonic()
    container = f"{args.project}-postgres-1"
    compose_cmd = ["docker", "compose", "-p", args.project, "-f", str(compose_file), "--env-file", str(env_file)]

    # 3. compose down（保卷）
    t0 = time.monotonic()
    run_checked(compose_cmd + ["down"], cwd=compose_cwd)
    rto["compose_down"] = time.monotonic() - t0
    print(f"[restore] compose down（保卷）：{rto['compose_down']:.1f}s")

    # 4. 卷 tar 解回（清后还原；stdin 管道供档——与 backup 对偶，无宿主 bind mount）
    t0 = time.monotonic()
    for volume in VOLUMES:
        tar_name = f"{volume}.tar.gz"
        tar_path = backup_dir / tar_name
        if not tar_path.exists():
            print(f"[restore] 备份集缺 {tar_name}，中止。")
            sys.exit(1)
        with open(tar_path, "rb") as fh:
            result = subprocess.run(
                ["docker", "run", "--rm", "-i",
                 "-v", f"{args.project}_{volume}:/target",
                 ALPINE_REF, "sh", "-c",
                 "find /target -mindepth 1 -delete && tar xzf - -C /target"],
                stdin=fh, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
        if result.returncode != 0:
            print(f"[restore] 卷解回失败（{volume}）：" + result.stderr.decode(errors="replace")[-800:])
            sys.exit(1)
    rto["volume_restore"] = time.monotonic() - t0
    print(f"[restore] 卷 tar 解回 ×2：{rto['volume_restore']:.1f}s")

    # 5. postgres 单服务起 + healthy 闸
    t0 = time.monotonic()
    run_checked(compose_cmd + ["up", "-d", "--no-build", "postgres"], cwd=compose_cwd)
    wait_postgres_healthy(container, POSTGRES_HEALTH_TIMEOUT_S)
    rto["postgres_up_healthy"] = time.monotonic() - t0
    print(f"[restore] postgres 起+healthy：{rto['postgres_up_healthy']:.1f}s")

    # 6. pg_restore --clean --if-exists（stdin 文件句柄）
    t0 = time.monotonic()
    dump_path = backup_dir / "db.dump"
    with open(dump_path, "rb") as fh:
        result = subprocess.run(
            ["docker", "exec", "-i", container, "pg_restore",
             "-U", db_user, "--clean", "--if-exists", "-d", db_name],
            stdin=fh, capture_output=True,
        )
    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace")
        print(f"[restore] pg_restore exit={result.returncode}：")
        print(stderr[-1200:])
        sys.exit(1)
    rto["db_restore"] = time.monotonic() - t0
    print(f"[restore] pg_restore --clean --if-exists：{rto['db_restore']:.1f}s")

    # 7. 全量 up + healthz 探活
    t0 = time.monotonic()
    run_checked(compose_cmd + ["up", "-d", "--no-build"], cwd=compose_cwd)
    rto["services_up"] = time.monotonic() - t0
    print(f"[restore] api/worker up --no-build：{rto['services_up']:.1f}s")
    t0 = time.monotonic()
    wait_healthz(port, HEALTHZ_TIMEOUT_S)
    rto["healthz_ready"] = time.monotonic() - t0
    rto["total"] = time.monotonic() - total_t0

    print(
        f"[restore] 完成：healthz 200（127.0.0.1:{port}）\n"
        f"[restore] RTO 分项：compose_down {rto['compose_down']:.1f}s + "
        f"卷恢复 {rto['volume_restore']:.1f}s + postgres起 {rto['postgres_up_healthy']:.1f}s + "
        f"DB恢复 {rto['db_restore']:.1f}s + 服务起 {rto['services_up']:.1f}s + "
        f"healthz {rto['healthz_ready']:.1f}s = 总计 {rto['total']:.1f}s"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
