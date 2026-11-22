# =============================================================================
# 提交侧防御器（market/dev_package_guard.py）
#
# 职责（agent_market_user_development T9 / CHG-03 / DC-05 / INV-D-17）：
#   1. precheck()：解压前防御——逐成员名规范化（拒绝绝对路径 / `..` 段 /
#      Windows 盘符 / UNC / 空名 / `\0`）、拒绝符号链接成员、声明体积与
#      条目数上限预检（剔除目录项）；
#   2. safe_extract()：不做整体解压（T9.2）——逐成员 open().read(limit)
#      流式写入并实时计数（单文件声明/实际双校验），写前以 resolve() 断言
#      目标路径仍在包根内（path_escape），累计写入超解压总量上限即停；
#   3. 纯标准库实现（zipfile/pathlib/re），可独立单测（test_dev_package_guard.py）。
# 约束：Python 3.10 兼容；零第三方新增依赖；只校验不执行（不 import 用户模块）。
# =============================================================================

import re
import zipfile
from pathlib import Path, PurePosixPath
from typing import List, Optional, Union

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.market.dev_models import DevFinding

_CHUNK_BYTES = 1024 * 1024  # 流式读写块大小（1MB，不整体读入内存）

_DRIVE_LETTER_PATTERN = re.compile(r"^[A-Za-z]:")

_RULE_PACKAGE_TOO_LARGE = "package_too_large"
_RULE_TOO_MANY_FILES = "too_many_files"
_RULE_FILE_TOO_LARGE = "file_too_large"
_RULE_UNPACKED_TOO_LARGE = "unpacked_too_large"
_RULE_MANIFEST_TOO_LARGE = "manifest_too_large"
_RULE_PATH_TRAVERSAL = "path_traversal"
_RULE_PATH_ESCAPE = "path_escape"
_RULE_SYMLINK_MEMBER = "symlink_member"
_RULE_FILE_SIZE_MISMATCH = "file_size_mismatch"


def _finding(
    rule_id: str, message: str, location: Optional[str] = None
) -> DevFinding:
    """构造一条 L2 error 级防御发现项（§5.2 表 Guard 归属规则）。"""
    return DevFinding(
        layer="L2",
        rule_id=rule_id,
        severity="error",
        message=message,
        location=location,
    )


def _normalize_member_name(name: str) -> str:
    r"""成员名规范化为 POSIX 相对路径（`\` 统一为 `/`）。"""
    return name.replace("\\", "/")


def _member_name_finding(name: str) -> Optional[DevFinding]:
    r"""逐成员名校验（DC-05 名称注入表）：命中返回 path_traversal 发现。

    覆盖向量：空名 / 含 `\0` / 以 `/` 开头（含 UNC 规范化形态）/
    Windows 盘符 / `..` 段（路径穿越）。
    """
    normalized = _normalize_member_name(name)
    if not normalized or "\x00" in normalized:
        return _finding(
            _RULE_PATH_TRAVERSAL,
            f"成员名非法（空名或含 \\0）: {name!r}",
            name,
        )
    if normalized.startswith("/"):
        return _finding(
            _RULE_PATH_TRAVERSAL, f"成员名为绝对路径: {name!r}", name
        )
    if _DRIVE_LETTER_PATTERN.match(normalized):
        return _finding(
            _RULE_PATH_TRAVERSAL, f"成员名含 Windows 盘符: {name!r}", name
        )
    if any(part == ".." for part in normalized.split("/")):
        return _finding(
            _RULE_PATH_TRAVERSAL, f"成员名含 `..` 段（路径穿越）: {name!r}", name
        )
    return None


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    """判定符号链接成员。

    注：采用文件类型掩码 `(external_attr >> 16) & 0xF000 == 0xA000`——
    真实符号链接的高 16 位为 0o120777（0xA1FF），若按字面
    `external_attr >> 16 == 0xA000` 判定会漏判真实符号链接。
    """
    return ((info.external_attr >> 16) & 0xF000) == 0xA000


class DevPackageGuard:
    """提交侧防御器（DC-05）：解压前 precheck + 流式 safe_extract。

    上限口径（DC-03 表 / T9.3）：
      原始包 ≤ max_package_mb；文件数 ≤ max_files（剔除目录项）；
      单文件 ≤ max_file_kb；manifest.yaml ≤ max_manifest_kb（解压后复核）；
      解压总量 ≤ max_unpacked_mb。
    """

    def __init__(
        self,
        max_package_mb: int = 10,
        max_files: int = 200,
        max_file_kb: int = 2048,
        max_manifest_kb: int = 64,
        max_unpacked_mb: int = 40,
    ) -> None:
        self.max_package_mb = max_package_mb
        self.max_files = max_files
        self.max_file_kb = max_file_kb
        self.max_manifest_kb = max_manifest_kb
        self.max_unpacked_mb = max_unpacked_mb

    @classmethod
    def from_settings(cls) -> "DevPackageGuard":
        """按 settings 的 agent_market_dev_* 上限配置构造（T4.1 字段）。"""
        settings = get_settings()
        return cls(
            max_package_mb=settings.agent_market_dev_max_package_mb,
            max_files=settings.agent_market_dev_max_files,
            max_file_kb=settings.agent_market_dev_max_file_kb,
            max_manifest_kb=settings.agent_market_dev_max_manifest_kb,
            max_unpacked_mb=settings.agent_market_dev_max_unpacked_mb,
        )

    # ------------------------------------------------------------------
    # 解压前预检（声明口径）
    # ------------------------------------------------------------------

    def precheck(self, zip_path: Union[str, Path]) -> List[DevFinding]:
        """解压前防御预检：空清单通过；非 ZIP 抛 zipfile.BadZipFile。"""
        path = Path(zip_path)
        findings: List[DevFinding] = []

        package_size = path.stat().st_size
        if package_size > self.max_package_mb * 1024 * 1024:
            findings.append(
                _finding(
                    _RULE_PACKAGE_TOO_LARGE,
                    f"原始包超过大小上限 {self.max_package_mb}MB"
                    f"（实际 {package_size} 字节）",
                    path.name,
                )
            )

        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()

        file_count = 0
        declared_total = 0
        for info in infos:
            name_finding = _member_name_finding(info.filename)
            if name_finding is not None:
                findings.append(name_finding)
                continue
            if _is_symlink(info):
                findings.append(
                    _finding(
                        _RULE_SYMLINK_MEMBER,
                        f"包含符号链接成员: {info.filename!r}",
                        info.filename,
                    )
                )
                continue
            if info.is_dir():
                continue
            file_count += 1
            declared_total += info.file_size
            if info.file_size > self.max_file_kb * 1024:
                findings.append(
                    _finding(
                        _RULE_FILE_TOO_LARGE,
                        f"单文件超过上限 {self.max_file_kb}KB: "
                        f"{info.filename!r}（声明 {info.file_size} 字节）",
                        info.filename,
                    )
                )

        if file_count > self.max_files:
            findings.append(
                _finding(
                    _RULE_TOO_MANY_FILES,
                    f"文件数超过上限 {self.max_files}: {file_count}",
                )
            )
        if declared_total > self.max_unpacked_mb * 1024 * 1024:
            findings.append(
                _finding(
                    _RULE_UNPACKED_TOO_LARGE,
                    f"解压总量超过上限 {self.max_unpacked_mb}MB"
                    f"（声明合计 {declared_total} 字节）",
                )
            )
        return findings

    # ------------------------------------------------------------------
    # 流式安全解压（实际口径 + 归属复核）
    # ------------------------------------------------------------------

    def safe_extract(
        self, zip_path: Union[str, Path], target_dir: Union[str, Path]
    ) -> List[DevFinding]:
        """逐成员流式解压（整体解压 API 禁用，T9.2）+ 解压后归属/双体积复核。

        空清单通过；非 ZIP 抛 zipfile.BadZipFile；
        单文件读取上限 = 声明体积 + 1（读到声明 +1 字节即成员撒谎，
        天然封顶 zip 炸弹的实际写入量）。
        """
        path = Path(zip_path)
        root = Path(target_dir)
        root.mkdir(parents=True, exist_ok=True)
        root_resolved = root.resolve()
        findings: List[DevFinding] = []

        total_written = 0
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                name_finding = _member_name_finding(info.filename)
                if name_finding is not None:
                    findings.append(name_finding)
                    continue
                dest = root / PurePosixPath(_normalize_member_name(info.filename))
                # 归属复核：目标路径 resolve() 后须仍在包根内（防 `..`/链接逃逸）
                if not dest.resolve().is_relative_to(root_resolved):
                    findings.append(
                        _finding(
                            _RULE_PATH_ESCAPE,
                            f"解压目标越出包根: {info.filename!r}",
                            info.filename,
                        )
                    )
                    continue
                if info.is_dir():
                    dest.mkdir(parents=True, exist_ok=True)
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                written = self._extract_member(zf, info, dest)
                # 单文件声明/实际双校验（T9.2）：实际写入超过声明即拒绝并清半成品
                if written > info.file_size:
                    findings.append(
                        _finding(
                            _RULE_FILE_SIZE_MISMATCH,
                            f"成员实际体积与声明不符（zip 炸弹防御）: "
                            f"{info.filename!r}（声明 {info.file_size}，"
                            f"实际 ≥ {written} 字节）",
                            info.filename,
                        )
                    )
                    dest.unlink(missing_ok=True)
                    continue
                if written > self.max_file_kb * 1024:
                    findings.append(
                        _finding(
                            _RULE_FILE_TOO_LARGE,
                            f"单文件写入超过上限 {self.max_file_kb}KB: "
                            f"{info.filename!r}（实际 {written} 字节）",
                            info.filename,
                        )
                    )
                    dest.unlink(missing_ok=True)
                    continue
                total_written += written
                if total_written > self.max_unpacked_mb * 1024 * 1024:
                    findings.append(
                        _finding(
                            _RULE_UNPACKED_TOO_LARGE,
                            f"解压累计写入超过上限 {self.max_unpacked_mb}MB",
                            info.filename,
                        )
                    )
                    break

        manifest_path = root / "manifest.yaml"
        if manifest_path.is_file():
            manifest_size = manifest_path.stat().st_size
            if manifest_size > self.max_manifest_kb * 1024:
                findings.append(
                    _finding(
                        _RULE_MANIFEST_TOO_LARGE,
                        f"manifest.yaml 超过上限 {self.max_manifest_kb}KB"
                        f"（实际 {manifest_size} 字节）",
                        "manifest.yaml",
                    )
                )
        return findings

    @staticmethod
    def _extract_member(
        zf: zipfile.ZipFile, info: zipfile.ZipInfo, dest: Path
    ) -> int:
        """流式写出单个成员并返回实际写入字节数（读取上限 = 声明体积 + 1）。"""
        written = 0
        remaining = info.file_size + 1
        with zf.open(info) as src, open(dest, "wb") as out:
            while remaining > 0:
                chunk = src.read(min(_CHUNK_BYTES, remaining))
                if not chunk:
                    break
                out.write(chunk)
                written += len(chunk)
                remaining -= len(chunk)
        return written
