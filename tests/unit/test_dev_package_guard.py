# =============================================================================
# 提交侧防御器单元测试（tests/unit/test_dev_package_guard.py）
#
# 覆盖（T14.1 / T9 / AC-DEV-19 / INV-D-17 / DC-05）：
#   - precheck：正常包通过；路径穿越（`..` 段 / 绝对路径 / 盘符 / UNC /
#     反斜杠段 / 空名）；`\0` 名称校验（zipfile 构造时即截断，单测驱动）；
#     符号链接成员；非 ZIP（BadZipFile）；超限（包大小 / 文件数 / 单文件 /
#     解压总量声明）；目录项不计文件数
#   - safe_extract：正常包解压内容一致（含嵌套目录与反斜杠名规范化）；
#     路径逃逸 path_escape（包根内预置外指链接目录，不越出包根）；
#     越界成员被拒绝且不落越界文件；流式读取上限 = 声明 + 1 与
#     声明/实际双校验（T9.2，zip 炸弹防御）；本环境 zipfile 对撒谎成员
#     抛 BadZipFile（截断 + CRC 校验）；解压累计超限即停；manifest 超限复核
# =============================================================================

import os
import struct
import sys
import zipfile
from pathlib import Path
from typing import Dict, List

import pytest

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.market.dev_package_guard import (
    DevPackageGuard,
    _member_name_finding,
)


def _make_zip(path: Path, members: Dict[str, bytes]) -> Path:
    """按 {成员名: 内容} 落盘一个 ZIP（`/` 结尾成员即目录项）。"""
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


def _add_symlink_member(path: Path, name: str, target: str) -> None:
    """向既有 ZIP 追加一个 Unix 符号链接成员（external_attr 高位 0o12xxxx）。"""
    with zipfile.ZipFile(path, "a") as zf:
        info = zipfile.ZipInfo(name)
        info.create_system = 3  # Unix
        info.external_attr = 0o120777 << 16  # S_IFLNK | 0777
        zf.writestr(info, target)


def _patch_central_file_size(zip_path: Path, new_size: int) -> None:
    """篡改中央目录的声明解压体积（模拟撒谎成员；本地头与压缩段保持原样）。"""
    data = bytearray(zip_path.read_bytes())
    central_offset = data.find(b"PK\x01\x02")
    assert central_offset != -1
    struct.pack_into("<I", data, central_offset + 24, new_size)  # 中央头 +24 = 解压体积
    zip_path.write_bytes(bytes(data))


def _rule_ids(findings: List[object]) -> List[str]:
    """提取发现项 rule_id 列表。"""
    return [f.rule_id for f in findings]  # type: ignore[attr-defined]


def _link_dir(target: Path, link: Path) -> None:
    """尽力创建目录链接（POSIX symlink；Windows 退回 junction，均免提权路径优先）。"""
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except (OSError, NotImplementedError):
        pass
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
        return
    raise


class _LyingSource:
    """伪成员流：恒可吐 actual 字节（模拟不按声明截断的 zipfile 变体）。"""

    def __init__(self, actual: int) -> None:
        self._remaining = actual

    def read(self, n: int) -> bytes:
        take = min(n, self._remaining)
        self._remaining -= take
        return b"A" * take

    def __enter__(self) -> "_LyingSource":
        return self

    def __exit__(self, *args: object) -> bool:
        return False


class _LyingZipFile:
    """伪 ZipFile：open() 返回伪成员流。"""

    def __init__(self, actual: int) -> None:
        self._actual = actual

    def open(self, info: zipfile.ZipInfo) -> _LyingSource:
        return _LyingSource(self._actual)


class TestPrecheck:
    """解压前预检（T9.1 / T9.3，声明口径）。"""

    def test_valid_package_passes(self, tmp_path: Path) -> None:
        zip_path = _make_zip(
            tmp_path / "pkg.zip",
            {
                "manifest.yaml": b"node_name: demo\n",
                "node.py": b"x = 1\n",
                "sub/": b"",
                "sub/util.py": b"y = 2\n",
            },
        )
        assert DevPackageGuard().precheck(zip_path) == []

    def test_parent_traversal_rejected(self, tmp_path: Path) -> None:
        zip_path = _make_zip(tmp_path / "evil.zip", {"../evil.txt": b"x"})
        assert _rule_ids(DevPackageGuard().precheck(zip_path)) == [
            "path_traversal"
        ]

    def test_absolute_path_rejected(self, tmp_path: Path) -> None:
        zip_path = _make_zip(tmp_path / "evil.zip", {"/etc/passwd": b"x"})
        assert "path_traversal" in _rule_ids(DevPackageGuard().precheck(zip_path))

    def test_drive_letter_rejected(self, tmp_path: Path) -> None:
        zip_path = _make_zip(tmp_path / "evil.zip", {"C:/evil.txt": b"x"})
        assert "path_traversal" in _rule_ids(DevPackageGuard().precheck(zip_path))

    def test_unc_path_rejected(self, tmp_path: Path) -> None:
        zip_path = _make_zip(
            tmp_path / "evil.zip", {"\\\\server\\share\\evil.txt": b"x"}
        )
        assert "path_traversal" in _rule_ids(DevPackageGuard().precheck(zip_path))

    def test_backslash_traversal_rejected(self, tmp_path: Path) -> None:
        zip_path = _make_zip(tmp_path / "evil.zip", {"..\\evil.txt": b"x"})
        assert "path_traversal" in _rule_ids(DevPackageGuard().precheck(zip_path))

    def test_nul_name_rejected_at_checker_level(self) -> None:
        # 注：zipfile 在 ZipInfo 构造时即按 `\0` 截断成员名（目录终止符语义），
        # 该向量无法经 zipfile 往返构造；直接单测名称校验函数（spec §5.2 保留口径）。
        finding = _member_name_finding("a\x00b.txt")
        assert finding is not None
        assert finding.rule_id == "path_traversal"

    def test_empty_name_rejected(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "weird.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr(zipfile.ZipInfo(""), b"x")  # 空成员名（ZipInfo 直构）
        assert "path_traversal" in _rule_ids(DevPackageGuard().precheck(zip_path))

    def test_symlink_member_rejected(self, tmp_path: Path) -> None:
        zip_path = _make_zip(tmp_path / "link.zip", {"node.py": b"x = 1\n"})
        _add_symlink_member(zip_path, "link.txt", "/etc/passwd")
        findings = DevPackageGuard().precheck(zip_path)
        assert "symlink_member" in _rule_ids(findings)
        assert "path_traversal" not in _rule_ids(findings)

    def test_not_a_zip_raises_bad_zip_file(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.zip"
        bad.write_text("not a zip", encoding="utf-8")
        with pytest.raises(zipfile.BadZipFile):
            DevPackageGuard().precheck(bad)

    def test_package_too_large(self, tmp_path: Path) -> None:
        # 约 1.2MB 不可压缩随机数据：超 1MB 原始包上限（其余上限不触发）
        zip_path = _make_zip(tmp_path / "big.zip", {"blob.bin": os.urandom(1200 * 1024)})
        guard = DevPackageGuard(max_package_mb=1)
        assert "package_too_large" in _rule_ids(guard.precheck(zip_path))

    def test_too_many_files(self, tmp_path: Path) -> None:
        members = {f"f{i}.txt": b"x" for i in range(3)}
        zip_path = _make_zip(tmp_path / "many.zip", members)
        guard = DevPackageGuard(max_files=2)
        assert "too_many_files" in _rule_ids(guard.precheck(zip_path))

    def test_single_file_too_large(self, tmp_path: Path) -> None:
        zip_path = _make_zip(tmp_path / "one.zip", {"big.txt": b"a" * (3 * 1024)})
        guard = DevPackageGuard(max_file_kb=2)
        assert "file_too_large" in _rule_ids(guard.precheck(zip_path))

    def test_declared_unpacked_too_large(self, tmp_path: Path) -> None:
        # 两个 600KB 成员：单文件不超缺省 2048KB，声明合计 1.2MB 超 1MB 解压上限
        members = {
            "a.bin": b"a" * (600 * 1024),
            "b.bin": b"b" * (600 * 1024),
        }
        zip_path = _make_zip(tmp_path / "bomb.zip", members)
        guard = DevPackageGuard(max_unpacked_mb=1)
        assert "unpacked_too_large" in _rule_ids(guard.precheck(zip_path))

    def test_directory_entries_not_counted_as_files(self, tmp_path: Path) -> None:
        zip_path = _make_zip(
            tmp_path / "dirs.zip", {"a/": b"", "a/b/": b"", "a/b/c.txt": b"x"}
        )
        guard = DevPackageGuard(max_files=1)
        assert guard.precheck(zip_path) == []

    def test_from_settings_uses_configured_limits(self) -> None:
        guard = DevPackageGuard.from_settings()
        settings = get_settings()
        assert guard.max_package_mb == settings.agent_market_dev_max_package_mb
        assert guard.max_files == settings.agent_market_dev_max_files
        assert guard.max_file_kb == settings.agent_market_dev_max_file_kb
        assert guard.max_manifest_kb == settings.agent_market_dev_max_manifest_kb
        assert guard.max_unpacked_mb == settings.agent_market_dev_max_unpacked_mb


class TestSafeExtract:
    """流式安全解压（T9.2，实际口径 + 归属复核）。"""

    def test_valid_package_extracts_with_content(self, tmp_path: Path) -> None:
        zip_path = _make_zip(
            tmp_path / "pkg.zip",
            {
                "manifest.yaml": b"node_name: demo\n",
                "node.py": b"x = 1\n",
                "sub/": b"",
                "sub/util.py": b"y = 2\n",
                "win\\style.txt": b"z = 3\n",  # 反斜杠名规范化为 POSIX 相对路径
            },
        )
        target = tmp_path / "out"
        findings = DevPackageGuard().safe_extract(zip_path, target)
        assert findings == []
        assert (target / "manifest.yaml").read_bytes() == b"node_name: demo\n"
        assert (target / "node.py").read_bytes() == b"x = 1\n"
        assert (target / "sub").is_dir()
        assert (target / "sub" / "util.py").read_bytes() == b"y = 2\n"
        assert (target / "win" / "style.txt").read_bytes() == b"z = 3\n"

    def test_traversal_member_not_written_outside(self, tmp_path: Path) -> None:
        zip_path = _make_zip(tmp_path / "evil.zip", {"../evil.txt": b"x"})
        target = tmp_path / "pkg_root"
        findings = DevPackageGuard().safe_extract(zip_path, target)
        # 名称检查先行命中（path_traversal）；无任何越界/残留文件
        assert "path_traversal" in _rule_ids(findings)
        assert not (tmp_path / "evil.txt").exists()
        assert list(target.iterdir()) == []

    def test_path_escape_via_preexisting_linked_dir(self, tmp_path: Path) -> None:
        # 归属复核（resolve 越界 → path_escape）：包根内预置指向外部的链接目录
        root = tmp_path / "pkg_root"
        root.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        try:
            _link_dir(outside, root / "sub")
        except (OSError, NotImplementedError):
            pytest.skip("当前环境无法创建目录链接（path_escape 为纵深防御分支）")
        zip_path = _make_zip(tmp_path / "evil.zip", {"sub/file.txt": b"x"})
        findings = DevPackageGuard().safe_extract(zip_path, root)
        assert "path_escape" in _rule_ids(findings)
        assert not (outside / "file.txt").exists()

    def test_traversal_name_flagged_independent_of_precheck(
        self, tmp_path: Path
    ) -> None:
        # 成员名含 `..` 段：safe_extract 独立调用时以 path_traversal 拒绝
        zip_path = _make_zip(tmp_path / "evil.zip", {"a/../../evil.txt": b"x"})
        findings = DevPackageGuard().safe_extract(zip_path, tmp_path / "out")
        assert "path_traversal" in _rule_ids(findings)
        assert not (tmp_path / "evil.txt").exists()

    def test_extract_member_read_cap_is_declared_plus_one(
        self, tmp_path: Path
    ) -> None:
        # T9.2 流式读取上限 = 声明体积 + 1：伪成员流恒吐 1000 字节，仅取 11
        info = zipfile.ZipInfo("big.txt")
        info.file_size = 10
        dest = tmp_path / "big.txt"
        written = DevPackageGuard._extract_member(_LyingZipFile(1000), info, dest)
        assert written == 11
        assert dest.read_bytes() == b"A" * 11

    def test_safe_extract_flags_mismatch_when_written_exceeds_declared(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 本环境 zipfile 按声明体积截断读取；以替身 _extract_member 驱动
        # safe_extract 的「实际写入 > 声明」双校验分支（T9.2，zip 炸弹防御）
        zip_path = _make_zip(tmp_path / "pkg.zip", {"big.txt": b"A" * 1000})
        target = tmp_path / "out"

        def _lying_extract(
            zf: zipfile.ZipFile, info: zipfile.ZipInfo, dest: Path
        ) -> int:
            dest.write_bytes(b"B" * (info.file_size + 1))
            return info.file_size + 1

        monkeypatch.setattr(
            DevPackageGuard, "_extract_member", staticmethod(_lying_extract)
        )
        findings = DevPackageGuard().safe_extract(zip_path, target)
        assert "file_size_mismatch" in _rule_ids(findings)
        assert not (target / "big.txt").exists()  # 半成品已删除

    def test_lying_member_rejected_as_bad_zip(self, tmp_path: Path) -> None:
        # 声明 10 字节、实际 1000 字节的 STORED 成员：本环境 zipfile 读取层
        # 按声明截断并在 EOF 处 CRC 校验失败 → BadZipFile（服务层映射 422）
        zip_path = tmp_path / "lie.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("big.txt", b"A" * 1000, compress_type=zipfile.ZIP_STORED)
        _patch_central_file_size(zip_path, 10)
        with pytest.raises(zipfile.BadZipFile):
            DevPackageGuard().safe_extract(zip_path, tmp_path / "out")

    def test_cumulative_unpacked_limit_stops_extraction(
        self, tmp_path: Path
    ) -> None:
        members = {
            "a.bin": b"a" * (600 * 1024),
            "b.bin": b"b" * (600 * 1024),
            "c.txt": b"c",
        }
        zip_path = _make_zip(tmp_path / "bomb.zip", members)
        target = tmp_path / "out"
        guard = DevPackageGuard(max_unpacked_mb=1)
        findings = guard.safe_extract(zip_path, target)
        assert "unpacked_too_large" in _rule_ids(findings)
        assert not (target / "c.txt").exists()  # 超限后停止续写

    def test_manifest_too_large_after_extract(self, tmp_path: Path) -> None:
        zip_path = _make_zip(tmp_path / "pkg.zip", {"manifest.yaml": b"k: v\n" * 500})
        guard = DevPackageGuard(max_manifest_kb=1)
        findings = guard.safe_extract(zip_path, tmp_path / "out")
        assert "manifest_too_large" in _rule_ids(findings)

    def test_extract_creates_target_dir(self, tmp_path: Path) -> None:
        zip_path = _make_zip(tmp_path / "pkg.zip", {"node.py": b"x = 1\n"})
        target = tmp_path / "nested" / "out"
        findings = DevPackageGuard().safe_extract(zip_path, target)
        assert findings == []
        assert (target / "node.py").read_bytes() == b"x = 1\n"
