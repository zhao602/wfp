# =============================================================================
# 离线交付打包器（scripts/ops/package_offline.py）
#
# 目标（roadmap_b15_delivery_hardening S5，design D5；roadmap-v3 :158）：
#   1. docker save 应用镜像 + postgres 基镜像 → images/*.tar；
#   2. pip download manylinux2014_x86_64 wheels（范围 .[postgres,sso]）；
#   3. manifest.json（无时间戳，可复现）+ SHA256SUMS（manifest 自身除外）；
#   4. 生成 install-offline.sh（校验和 → load ×2 → env 提示 → up --no-build
#      --pull never → healthz 轮询）；
#   5. SBOM 拷入（D3 产物）+ compose/.env 模板副本 + README。
#
# 纪律：
#   - 纯函数区（本节「纯函数」分隔线以上）不触 docker/pip/网络——单测面
#     tests/unit/test_offline_package_manifest.py 只 import 该区；
#   - manifest 无时间戳字段（生成时间落 README，不入 manifest）；
#   - HC-06：仅标准库，零新增依赖。
# =============================================================================

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

GENERATOR_NAME = "scripts/ops/package_offline.py"
GENERATOR_VERSION = "1.0.0"
MANIFEST_SCHEMA = "offline-package-manifest/1"
WHEELS_PLATFORM = "manylinux2014_x86_64"
WHEELS_PYTHON = "3.10"
EXTRAS_SCOPE = ".[postgres,sso]"
HEALTHZ_PATH = "/healthz"

# 禁键（manifest 递归自检：可复现性红线——无任何时间戳类字段）
_FORBIDDEN_KEYS = {"time", "timestamp", "created", "created_at", "date", "generated_at", "generated"}

REPO_ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# 纯函数（单测面：不触 docker/pip/网络）
# ---------------------------------------------------------------------------


def compute_file_sha256(path: Path) -> str:
    """文件 sha256（流式读，大 tar 友好）。"""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_sums_entries(root: Path, exclude: set[str]) -> list[tuple[str, str]]:
    """root 下全部文件 → 排序后的 (sha256, 相对路径) 列表。

    exclude 为相对路径集合（正斜杠口径）；相对路径统一正斜杠（sha256sum 跨平台口径）。
    """
    entries: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(root).as_posix()
        if rel in exclude:
            continue
        entries.append((compute_file_sha256(path), rel))
    return entries


def render_sums_content(entries: list[tuple[str, str]]) -> str:
    """sha256sum 兼容格式：<sha256>  <相对路径>（两空格分隔）。"""
    return "".join(f"{sha}  {rel}\n" for sha, rel in entries)


def parse_sums_content(content: str) -> list[tuple[str, str]]:
    """解析 SHA256SUMS 内容 → [(sha256, relpath)]；坏行拒绝。"""
    pairs: list[tuple[str, str]] = []
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        if "  " not in line:
            raise ValueError(f"SHA256SUMS 坏行: {line!r}")
        sha, rel = line.split("  ", 1)
        pairs.append((sha.strip(), rel.strip()))
    return pairs


def verify_sums_content(root: Path, content: str) -> list[str]:
    """按 SHA256SUMS 内容校验 root；返回坏项描述清单（缺失/不匹配）。"""
    problems: list[str] = []
    for sha, rel in parse_sums_content(content):
        target = root / rel
        if not target.is_file():
            problems.append(f"缺失: {rel}")
        elif compute_file_sha256(target) != sha:
            problems.append(f"校验和不符: {rel}")
    return problems


def manifest_has_timestamp(manifest: dict) -> bool:
    """递归检查 manifest 是否含禁键（时间戳类字段）——可复现性自检。"""
    for key, value in manifest.items():
        if key.lower() in _FORBIDDEN_KEYS:
            return True
        if isinstance(value, dict) and manifest_has_timestamp(value):
            return True
        if isinstance(value, list) and any(isinstance(v, dict) and manifest_has_timestamp(v) for v in value):
            return True
    return False


def build_manifest(
    *,
    version: str,
    images: list[dict],
    wheels: list[dict],
    images_skipped: bool,
    sdist_fallback: list[str],
    sbom_present: bool,
) -> dict:
    """构建 manifest dict（无时间戳；键集合稳定）。

    images 项：{role, ref, digest, repo_digests, file, sha256}
    wheels 项：{file, sha256}
    """
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "version": version,
        "generator": {"name": GENERATOR_NAME, "version": GENERATOR_VERSION},
        "platform": {"wheels": WHEELS_PLATFORM, "python": WHEELS_PYTHON, "extras_scope": EXTRAS_SCOPE},
        "images": images,
        "images_skipped": images_skipped,
        "wheels": wheels,
        "sdist_fallback": sdist_fallback,
        "files": {
            "compose": "docker-compose.yml",
            "env_example": ".env.production.example",
            "install_script": "install-offline.sh",
            "sums": "SHA256SUMS",
            "readme": "README.md",
            "sbom": "sbom/sbom-app.spdx.json" if sbom_present else None,
        },
    }
    if manifest_has_timestamp(manifest):  # 自检红线：宁可构建失败也不带时间戳
        raise AssertionError("manifest 含时间戳类禁键（可复现性红线）")
    return manifest


def strip_digest_pins(text: str) -> str:
    """compose 文本中 image: 引用的 @sha256:… digest 后缀剥离（离线语义）。

    D6 drill 实测（2026-11-25）：docker save/load 不恢复 RepoDigests——
    离线包内 compose 保留 `name:tag@sha256:…` 时 `up --pull never` 解析该引用
    必报 `No such image: …@sha256:…`。provenance 不降级：digest 在
    manifest.json（images[].digest / repo_digests）与 SHA256SUMS（tar 内容锁）
    中保留；仓库在线 compose 的 digest 锁不受影响（本函数只作用于包内副本）。
    """
    out_lines = []
    for line in text.splitlines():
        if re.match(r"^\s*image:\s*\S+", line):
            line = re.sub(r"@sha256:[0-9a-f]{64}", "", line)
        out_lines.append(line)
    return "\n".join(out_lines) + "\n"


def render_install_script(*, app_ref: str, postgres_ref: str) -> str:
    """生成 install-offline.sh（bash；离线语义：load + up --no-build --pull never）。"""
    return f"""#!/usr/bin/env bash
# =============================================================================
# 离线安装器（install-offline.sh，B15 S5 生成器产物）
#
# 前置：本目录为完整离线包（manifest.json/SHA256SUMS 校验通过）；
#       目标机需 docker + docker compose（v2，支持 up --pull never）。
# 步骤：校验和 → docker load ×2 → .env.production 检查 → up --no-build
#       --pull never（零拉取）→ healthz 轮询。
# 诚实口径：单机模拟离线验证（design D6）；真实内网隔离部署以交付方实测为准。
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")"

echo "[1/6] 校验和验证（SHA256SUMS）……"
sha256sum -c SHA256SUMS

echo "[2/6] 加载镜像（app={app_ref} / postgres={postgres_ref}）……"
docker load -i images/app.tar
docker load -i images/postgres.tar

echo "[3/6] 检查 .env.production ……"
if [ ! -f .env.production ]; then
    echo "缺少 .env.production：请先 cp .env.production.example .env.production"
    echo "并填入真实值（红线见模板头：弱密钥 fail-fast，占位值起不来是特性）。"
    exit 1
fi

echo "[4/6] 起栈（离线：--no-build --pull never）……"
docker compose --env-file .env.production up -d --no-build --pull never

PORT=$(grep -E '^API_PORT=' .env.production | tail -1 | cut -d= -f2 || true)
PORT=${{PORT:-8000}}
echo "[5/6] 健康探活 http://127.0.0.1:${{PORT}}/healthz ……"
ok=0
for i in $(seq 1 60); do
    if curl --noproxy '*' -fsS "http://127.0.0.1:${{PORT}}/healthz" >/dev/null 2>&1; then
        echo "healthz OK（第 ${{i}} 次探测）"
        ok=1
        break
    fi
    sleep 2
done
if [ "$ok" -ne 1 ]; then
    echo "healthz 120 秒超时——查 docker compose logs api 排障"
    exit 1
fi

echo "[6/6] 完成。冒烟与运维步骤见 README.md；备份恢复见 docs/deploy.md §11。"
"""


# ---------------------------------------------------------------------------
# 副作用面（docker / pip / 文件系统）
# ---------------------------------------------------------------------------


def read_pyproject_version() -> str:
    """pyproject [project] version（tomllib 优先，regex 兜底）。"""
    pyproject = REPO_ROOT / "pyproject.toml"
    try:
        import tomllib

        with open(pyproject, "rb") as fh:
            data = tomllib.load(fh)
        return str(data["project"]["version"])
    except Exception:
        text = pyproject.read_text(encoding="utf-8")
        match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
        if not match:
            raise
        return match.group(1)


def docker_image_info(ref: str) -> dict:
    """docker image inspect → {id, repo_digests}（provenance 记录）。"""
    result = subprocess.run(
        ["docker", "image", "inspect", ref, "--format", "{{json .}}"],
        capture_output=True,
        text=True,
        check=True,
    )
    info = json.loads(result.stdout)
    return {
        "id": info.get("Id", ""),
        "repo_digests": info.get("RepoDigests", []) or [],
    }


def docker_save(ref: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["docker", "save", "-o", str(dest), ref], check=True)


def pip_download(dest: Path, index_url: str | None) -> tuple[bool, str]:
    """pip download wheels（manylinux2014_x86_64 + py3.10 + only-binary）。

    返回 (成功, 尾部输出)；失败时 sdist-only 依赖由调用方登记裁决（design D5）。
    """
    dest.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "download",
        "--dest",
        str(dest),
        "--platform",
        WHEELS_PLATFORM,
        "--python-version",
        WHEELS_PYTHON,
        "--only-binary",
        ":all:",
    ]
    if index_url:
        cmd += ["--index-url", index_url]
    cmd.append(EXTRAS_SCOPE)
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT)
    tail = "\n".join((result.stderr or "").splitlines()[-15:])
    return result.returncode == 0, tail


def build_project_wheel(dest: Path) -> Path | None:
    """构建平台自身 wheel（pip wheel --no-deps，本地构建零网络）。

    缺口修复：pip download 对本地目录根包不落盘——wheels/ 缺平台本体则离线
    `pip install --no-index --find-links wheels/ orchestration-platform[postgres,sso]`
    不成立；本地构建 py3-none-any wheel 补齐（纯 Python 包，无平台约束）。
    """
    result = subprocess.run(
        [sys.executable, "-m", "pip", "wheel", "--no-deps", "--wheel-dir", str(dest), "."],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    if result.returncode != 0:
        print("[package_offline] pip wheel（平台本体）失败：")
        print("\n".join((result.stderr or "").splitlines()[-10:]))
        return None
    wheels_built = sorted(dest.glob("orchestration_platform-*.whl"))
    return wheels_built[-1] if wheels_built else None


def write_readme(out_root: Path, *, version: str, stats: dict, generated_at: str, sdist_fallback: list[str]) -> None:
    lines = [
        "# 离线交付包（orchestration-platform）",
        "",
        f"- 版本：{version}（manifest schema {MANIFEST_SCHEMA}）",
        f"- 生成：{GENERATOR_NAME} v{GENERATOR_VERSION}，{generated_at}",
        f"- 平台：wheels={WHEELS_PLATFORM} / python {WHEELS_PYTHON}；镜像 amd64",
        f"- 体积：{stats['total_size_mb']:.1f} MB；文件数：{stats['file_count']}",
        "",
        "## 结构",
        "",
        "```",
        "images/app.tar            应用镜像（docker save）",
        "images/postgres.tar       postgres:16-alpine 基镜像（digest 锁）",
        "wheels/*.whl              平台本体 wheel + main+[postgres]+[sso] 依赖（manylinux2014_x86_64）",
        "manifest.json             交付清单（无时间戳，可复现校验）",
        "SHA256SUMS                全包校验和（manifest 除外）",
        "sbom/sbom-app.spdx.json   镜像 SBOM（SPDX-2.3）",
        "docker-compose.yml        编排副本（与仓库同源）",
        ".env.production.example   生产模板副本",
        "install-offline.sh        一键安装器",
        "```",
        "",
        "## 安装",
        "",
        "1. 校验包完整性：`sha256sum -c SHA256SUMS`",
        "2. 执行 `bash install-offline.sh`（load → env 检查 → up --no-build --pull never → healthz）",
        "3. `.env.production` 必须先按模板填真实值（弱密钥 fail-fast，见模板头红线）",
        "",
        "## 验证口径",
        "",
        "- 镜像 provenance 与 digest 见 manifest.json（images[].digest / repo_digests）",
        "- 漏洞基线：HIGH=61（去重 20）/ CRITICAL=0 / 自研层 0（登记口径非零门禁，",
        "  详见交付方 _drills/b15_s3_scan_baseline.txt）",
        "- 诚实声明：离线语义经单机模拟验证（rmi → load → up --pull never 零拉取），",
        "  真实内网隔离环境以交付方实测为准",
    ]
    if sdist_fallback:
        lines += [
            "",
            "## sdist 回退登记（需裁决）",
            "",
            "以下依赖无 manylinux2014_x86_64 wheel，pip download --only-binary 失败：",
            f"- {', '.join(sdist_fallback)}",
            "离线安装需补 sdist + 目标机构建链，或联系交付方裁决替代方案。",
        ]
    (out_root / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="离线交付打包器（design D5）")
    parser.add_argument("--version", default=None, help="版本号（默认取 pyproject [project] version）")
    parser.add_argument("--output", default=None, help="输出目录（默认 delivery/offline-<version>/）")
    parser.add_argument("--skip-images", action="store_true", help="跳过 docker save（CI 友好；包不完整仅用于校验流程）")
    parser.add_argument("--pip-index-url", default=None, help="pip 镜像源（受限网络注入）")
    args = parser.parse_args(argv)

    started = time.monotonic()
    version = args.version or read_pyproject_version()
    out_root = Path(args.output).resolve() if args.output else REPO_ROOT / "delivery" / f"offline-{version}"
    print(f"[package_offline] version={version} output={out_root}")

    if out_root.exists():
        shutil.rmtree(out_root)
    out_root.mkdir(parents=True)

    app_ref = "orchestration-platform:0.3.0"
    postgres_ref = "postgres:16-alpine"

    # 1. 镜像
    images: list[dict] = []
    if not args.skip_images:
        for role, ref, filename in [
            ("app", app_ref, "images/app.tar"),
            ("postgres", postgres_ref, "images/postgres.tar"),
        ]:
            t0 = time.monotonic()
            info = docker_image_info(ref)
            dest = out_root / filename
            docker_save(ref, dest)
            size_mb = dest.stat().st_size / 1024 / 1024
            images.append(
                {
                    "role": role,
                    "ref": ref,
                    "digest": info["id"],
                    "repo_digests": info["repo_digests"],
                    "file": filename,
                    "sha256": compute_file_sha256(dest),
                }
            )
            print(f"[package_offline] save {ref}: {size_mb:.1f} MB（{time.monotonic() - t0:.1f}s）")
    else:
        print("[package_offline] --skip-images：跳过 docker save（包不完整）")

    # 2. wheels
    t0 = time.monotonic()
    ok, tail = pip_download(out_root / "wheels", args.pip_index_url)
    sdist_fallback: list[str] = []
    if not ok:
        print("[package_offline] pip download 失败（sdist-only 依赖登记口径）：")
        print(tail)
        failing = sorted({p.stem for p in (out_root / "wheels").glob("*.whl")})
        sdist_fallback = failing  # 已下载成功的部分留档；失败项以输出为准人工归因
        print("[package_offline] 登记后终止——请人工裁决（design D5：遇 sdist-only 记录后裁决）")
        return 2
    platform_wheel = build_project_wheel(out_root / "wheels")
    if platform_wheel is None:
        return 2
    print(f"[package_offline] 平台本体 wheel: {platform_wheel.name}")
    wheel_files = sorted((out_root / "wheels").glob("*.whl"))
    wheels = [{"file": f"wheels/{p.name}", "sha256": compute_file_sha256(p)} for p in wheel_files]
    print(f"[package_offline] wheels: {len(wheel_files)} 个（{time.monotonic() - t0:.1f}s）")

    # 3. SBOM / compose / env 模板副本
    sbom_src = REPO_ROOT / "delivery" / "sbom" / "sbom-app.spdx.json"
    sbom_present = sbom_src.is_file()
    if sbom_present:
        (out_root / "sbom").mkdir(exist_ok=True)
        shutil.copy2(sbom_src, out_root / "sbom" / "sbom-app.spdx.json")
    else:
        print("[package_offline] 警告：delivery/sbom/sbom-app.spdx.json 缺位——SBOM 未拷入（先跑 S3 扫描）")
    compose_text = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    offline_note = (
        "# B15 离线包副本：image: 引用的 @sha256 digest 后缀已剥离"
        "（docker save/load 不恢复 RepoDigests，up --pull never 解析 digest 引用"
        "必失败；provenance 见 manifest.json）。\n"
    )
    (out_root / "docker-compose.yml").write_text(
        offline_note + strip_digest_pins(compose_text), encoding="utf-8"
    )
    shutil.copy2(REPO_ROOT / ".env.production.example", out_root / ".env.production.example")

    # 4. manifest（无时间戳）
    manifest = build_manifest(
        version=version,
        images=images,
        wheels=wheels,
        images_skipped=args.skip_images,
        sdist_fallback=sdist_fallback,
        sbom_present=sbom_present,
    )
    manifest_path = out_root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    # 5. install-offline.sh
    script = render_install_script(app_ref=app_ref, postgres_ref=postgres_ref)
    (out_root / "install-offline.sh").write_text(script, encoding="utf-8", newline="\n")

    # 6. SHA256SUMS（manifest 自身除外；含 install-offline.sh 自身）
    entries = build_sums_entries(out_root, exclude={"manifest.json"})
    (out_root / "SHA256SUMS").write_text(render_sums_content(entries), encoding="utf-8", newline="\n")

    # 7. README（生成时间落这里，不入 manifest）；统计 +1 计 README 自身（先算后写）
    import datetime as _dt

    all_files = [p for p in out_root.rglob("*") if p.is_file()]
    readme_projected = 1  # README 即将写入，计入文件数（体积 <2KB，0.1MB 精度不移动）
    stats = {
        "file_count": len(all_files) + readme_projected,
        "total_size_mb": sum(p.stat().st_size for p in all_files) / 1024 / 1024,
    }
    write_readme(
        out_root,
        version=version,
        stats=stats,
        generated_at=_dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z"),
        sdist_fallback=sdist_fallback,
    )

    # 自检：SHA256SUMS 复验（坏校验和零容忍）
    problems = verify_sums_content(out_root, (out_root / "SHA256SUMS").read_text(encoding="utf-8"))
    if problems:
        print("[package_offline] 自检失败：SHA256SUMS 复验发现坏项：")
        for p in problems:
            print(f"  - {p}")
        return 3

    elapsed = time.monotonic() - started
    print(
        f"[package_offline] 完成：{stats['file_count']} 文件 / {stats['total_size_mb']:.1f} MB / {elapsed:.1f}s"
        f"（SBOM {'含' if sbom_present else '缺'}；sdist 回退 {len(sdist_fallback)}）"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
