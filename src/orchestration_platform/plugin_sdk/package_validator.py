# =============================================================================
# 打包规范校验器（plugin_sdk/package_validator.py）
#
# 职责：校验插件节点包目录结构与内容一致性。
# =============================================================================

import hashlib
import logging
from pathlib import Path
from typing import List, Optional

from orchestration_platform.plugin_sdk.manifest_validator import (
    ManifestValidator,
)
from orchestration_platform.plugin_sdk.models import (
    PluginManifest,
    ValidationResult,
)

_logger = logging.getLogger(__name__)

# 固定目录结构要求
_REQUIRED_FILES = ["manifest.yaml"]
_OPTIONAL_FILES = ["README.md", "requirements.txt"]


class NodePackageValidator:
    """节点包规范校验器。

    校验固定目录结构、manifest 与实现文件一致性。
    """

    def __init__(self) -> None:
        self._manifest_validator = ManifestValidator()

    def validate(self, package_path: str) -> ValidationResult:
        """校验节点包。

        参数：
            package_path：包目录路径

        返回：
            ValidationResult（含规范差异说明）
        """
        errors: List[str] = []
        warnings: List[str] = []
        pkg_path = Path(package_path)

        if not pkg_path.exists():
            return ValidationResult(valid=False, errors=[f"包路径不存在: {package_path}"])

        if not pkg_path.is_dir():
            return ValidationResult(valid=False, errors=[f"包路径非目录: {package_path}"])

        # 检查固定文件
        for req_file in _REQUIRED_FILES:
            if not (pkg_path / req_file).exists():
                errors.append(f"缺少必需文件: {req_file}")

        # 加载 manifest
        manifest = self._load_manifest(pkg_path)
        if manifest is None:
            errors.append("manifest 加载失败")
            return ValidationResult(valid=False, errors=errors)

        # manifest 格式校验
        manifest_result = self._manifest_validator.validate(manifest)
        errors.extend(manifest_result.errors)
        warnings.extend(manifest_result.warnings)

        # 实现文件一致性
        entry_path = pkg_path / manifest.entry_point
        if not entry_path.exists():
            errors.append(f"入口文件不存在: {manifest.entry_point}")

        return ValidationResult(valid=len(errors) == 0, errors=errors, warnings=warnings)

    def compute_digest(self, package_path: str) -> str:
        """计算包内容 SHA-256 摘要。

        参数：
            package_path：包目录路径

        返回：
            十六进制摘要字符串
        """
        hasher = hashlib.sha256()
        pkg_path = Path(package_path)

        for file_path in sorted(pkg_path.rglob("*")):
            if file_path.is_file():
                hasher.update(str(file_path.relative_to(pkg_path)).encode("utf-8"))
                with open(file_path, "rb") as f:
                    hasher.update(f.read())

        return hasher.hexdigest()

    def _load_manifest(self, pkg_path: Path) -> Optional[PluginManifest]:
        """从包目录加载 manifest。"""
        manifest_path = pkg_path / "manifest.yaml"
        if not manifest_path.exists():
            return None

        try:
            import yaml
            with open(manifest_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            return PluginManifest(**data)
        except Exception as exc:
            _logger.error("manifest 加载失败: %s", exc)
            return None
