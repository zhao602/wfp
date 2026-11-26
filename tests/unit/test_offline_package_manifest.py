# -*- coding: utf-8 -*-
# =============================================================================
# B15 S5 离线打包器纯函数单测（tests/unit/test_offline_package_manifest.py）
#
# 单一事实源：importlib 复用 scripts/ops/package_offline.py 纯函数区
# （compute_file_sha256 / build_sums_entries / render_sums_content /
# parse_sums_content / verify_sums_content / manifest_has_timestamp /
# build_manifest / render_install_script）；不触 docker/pip/网络
# （副作用面由 S5 drill 实测覆盖，design D5/D6）。
#
# 覆盖：
#   1. sha256 已知向量（"abc" NIST 向量）；
#   2. SHA256SUMS 聚合：排除项/正斜杠相对路径/排序/sha256sum 两空格格式/解析往返；
#   3. 校验复验：全对零坏项、篡改→校验和不符、删除→缺失、坏行→ValueError；
#   4. manifest 无时间戳红线：自检通过 + 检测器灵敏度（嵌套注入禁键必抓）；
#   5. manifest 结构锁定：schema/键集合/files 映射/sdist 回退与 --skip-images 透传；
#   6. install-offline.sh 离线语义：--no-build --pull never 零拉取、
#      sha256sum -c、docker load ×2、healthz 轮询、无 f-string 转义残留。
# =============================================================================
import importlib.util
from pathlib import Path

import pytest

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "ops" / "package_offline.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "package_offline_unit_ref", _SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# 1. sha256 已知向量
# ---------------------------------------------------------------------------


def test_compute_file_sha256_known_vector(tmp_path):
    """sha256("abc") NIST 标准向量（流式实现正确性）。"""
    module = _load_module()
    target = tmp_path / "vector.txt"
    target.write_text("abc", encoding="utf-8")
    assert module.compute_file_sha256(target) == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


# ---------------------------------------------------------------------------
# 2. SHA256SUMS 聚合与往返
# ---------------------------------------------------------------------------


def test_sums_entries_exclusion_sort_and_roundtrip(tmp_path):
    """排除项不进清单；相对路径正斜杠；排序稳定；渲染→解析往返无损。"""
    module = _load_module()
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    (tmp_path / "a.txt").write_text("alpha", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.bin").write_bytes(b"\x00\x01\x02")

    entries = module.build_sums_entries(tmp_path, exclude={"manifest.json"})

    assert [rel for _, rel in entries] == ["a.txt", "sub/b.bin"], (
        "排序后相对路径应为正斜杠口径且排除 manifest.json"
    )
    assert all(len(sha) == 64 for sha, _ in entries)

    content = module.render_sums_content(entries)
    assert "  a.txt\n" in content and "  sub/b.bin\n" in content, (
        "sha256sum 兼容格式：两空格分隔"
    )
    assert module.parse_sums_content(content) == entries, "渲染→解析往返无损"


# ---------------------------------------------------------------------------
# 3. 校验复验：坏校验和/缺失/坏行
# ---------------------------------------------------------------------------


def test_verify_sums_accepts_intact_tree(tmp_path):
    """全对 → 零坏项。"""
    module = _load_module()
    target = tmp_path / "f.txt"
    target.write_text("good", encoding="utf-8")
    content = f"{module.compute_file_sha256(target)}  f.txt\n"
    assert module.verify_sums_content(tmp_path, content) == []


def test_verify_sums_rejects_tampered_and_missing(tmp_path):
    """篡改 → 校验和不符；删除 → 缺失。"""
    module = _load_module()
    target = tmp_path / "f.txt"
    target.write_text("good", encoding="utf-8")
    content = f"{module.compute_file_sha256(target)}  f.txt\n"

    target.write_text("tampered", encoding="utf-8")
    problems = module.verify_sums_content(tmp_path, content)
    assert len(problems) == 1 and "校验和不符" in problems[0]

    target.unlink()
    problems = module.verify_sums_content(tmp_path, content)
    assert len(problems) == 1 and "缺失" in problems[0]


def test_parse_sums_rejects_malformed_line():
    """无两空格分隔的坏行 → ValueError（拒绝而非静默吞）。"""
    module = _load_module()
    with pytest.raises(ValueError):
        module.parse_sums_content("deadbeef f.txt\n")


# ---------------------------------------------------------------------------
# 4. manifest 无时间戳红线
# ---------------------------------------------------------------------------


def test_manifest_has_no_timestamp_and_detector_sensitivity():
    """合法 manifest 无禁键；检测器对嵌套注入（dict/list）必抓。"""
    module = _load_module()
    manifest = module.build_manifest(
        version="0.3.0",
        images=[],
        wheels=[],
        images_skipped=False,
        sdist_fallback=[],
        sbom_present=True,
    )
    assert module.manifest_has_timestamp(manifest) is False, (
        "合法 manifest 不应含时间戳类字段（可复现性红线）"
    )
    assert module.manifest_has_timestamp({"nested": {"deep": [{"created_at": "2024"}]}}) is True
    assert module.manifest_has_timestamp({"images": [{"time": 1}]}) is True
    assert module.manifest_has_timestamp({"generated": "now"}) is True


# ---------------------------------------------------------------------------
# 5. manifest 结构锁定
# ---------------------------------------------------------------------------


def test_manifest_schema_and_passthrough():
    """schema/键集合/files 映射锁定；sdist 回退与 --skip-images 透传。"""
    module = _load_module()
    manifest = module.build_manifest(
        version="0.3.0",
        images=[{"role": "app", "ref": "r", "digest": "d", "repo_digests": [], "file": "f", "sha256": "s"}],
        wheels=[{"file": "wheels/x.whl", "sha256": "s"}],
        images_skipped=False,
        sdist_fallback=[],
        sbom_present=True,
    )
    assert manifest["schema"] == "offline-package-manifest/1"
    assert manifest["version"] == "0.3.0"
    assert set(manifest.keys()) == {
        "schema", "version", "generator", "platform", "images",
        "images_skipped", "wheels", "sdist_fallback", "files",
    }
    assert manifest["files"]["install_script"] == "install-offline.sh"
    assert manifest["files"]["sbom"] == "sbom/sbom-app.spdx.json"
    assert manifest["platform"]["wheels"] == "manylinux2014_x86_64"
    assert manifest["platform"]["python"] == "3.10"

    skipped = module.build_manifest(
        version="0.3.0",
        images=[],
        wheels=[],
        images_skipped=True,
        sdist_fallback=["some-sdist-only-pkg"],
        sbom_present=False,
    )
    assert skipped["images_skipped"] is True
    assert skipped["sdist_fallback"] == ["some-sdist-only-pkg"]
    assert skipped["files"]["sbom"] is None


# ---------------------------------------------------------------------------
# 6. install-offline.sh 离线语义
# ---------------------------------------------------------------------------


def test_install_script_offline_semantics():
    """零拉取（--no-build --pull never）/ 校验 / load ×2 / healthz / 无转义残留。"""
    module = _load_module()
    script = module.render_install_script(
        app_ref="orchestration-platform:0.3.0", postgres_ref="postgres:16-alpine"
    )
    assert "--no-build --pull never" in script, "up 必须零构建零拉取"
    assert "--noproxy '*'" in script, "环回探活必须代理免疫（目标机 client proxies 劫持 curl）"
    assert "sha256sum -c SHA256SUMS" in script
    assert "docker load -i images/app.tar" in script
    assert "docker load -i images/postgres.tar" in script
    assert "docker compose --env-file .env.production up" in script
    assert "/healthz" in script
    assert "orchestration-platform:0.3.0" in script
    assert "postgres:16-alpine" in script
    assert "{{" not in script and "}}" not in script, "f-string 转义残留（bash 语法破坏）"


# ---------------------------------------------------------------------------
# 7. 离线包 compose 副本 digest 剥离（D6 实测缺口修复）
# ---------------------------------------------------------------------------


def test_strip_digest_pins_only_image_lines():
    """image: 行 digest 后缀剥离（tag 保留）；注释行不剥离；无 digest 行不动。"""
    module = _load_module()
    sample = (
        "services:\n"
        "  postgres:\n"
        "    # 注释 digest 锁 postgres:16-alpine@sha256:f2f98726820d0342055351c6e9d36e705f5bfffb3dd53ff556325114b08afb6d\n"
        "    image: postgres:16-alpine@sha256:f2f98726820d0342055351c6e9d36e705f5bfffb3dd53ff556325114b08afb6d\n"
        "  api:\n"
        "    image: orchestration-platform:0.3.0\n"
    )
    stripped = module.strip_digest_pins(sample)
    assert "image: postgres:16-alpine\n" in stripped, "image: 行 digest 剥离、tag 保留"
    assert "orchestration-platform:0.3.0" in stripped, "无 digest 引用不受影响"
    assert (
        "注释 digest 锁 postgres:16-alpine@sha256:f2f98726820d" in stripped
    ), "注释行不剥离（只动 image: 行）"
    assert stripped.endswith("\n"), "行尾换行保持（read_text→write_text 往返稳定）"
