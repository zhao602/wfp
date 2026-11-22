# =============================================================================
# manifest 校验器（plugin_sdk/manifest_validator.py）
#
# 职责：校验插件节点 manifest 格式与完整性。
# =============================================================================

import re
from typing import List

from orchestration_platform.plugin_sdk.models import (
    PluginManifest,
    ValidationResult,
)

# 版本号语义化格式正则（主.次.修订）
_SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
# 节点名称格式正则（小写字母/数字/连字符）
_NODE_NAME_PATTERN = re.compile(r"^[a-z0-9-]+$")


class ManifestValidator:
    """manifest 校验器。

    校验节点名称规范、版本格式、输入输出模式声明、依赖清单与权限声明。
    """

    def validate(self, manifest: PluginManifest) -> ValidationResult:
        """校验 manifest。

        返回：
            ValidationResult（含缺失字段清单）
        """
        errors: List[str] = []
        warnings: List[str] = []

        # 节点名称
        if not manifest.node_name:
            errors.append("节点名称缺失")
        elif not _NODE_NAME_PATTERN.match(manifest.node_name):
            errors.append(f"节点名称格式非法（须小写字母/数字/连字符）: {manifest.node_name}")

        # 版本号
        if not manifest.version:
            errors.append("版本号缺失")
        elif not _SEMVER_PATTERN.match(manifest.version):
            errors.append(f"版本号格式非法（须语义化格式 主.次.修订）: {manifest.version}")

        # 输入输出模式
        if not manifest.input_schema:
            warnings.append("输入模式未声明")
        if not manifest.output_schema:
            warnings.append("输出模式未声明")

        # 权限声明
        if not manifest.declared_file_paths and not manifest.declared_network_targets:
            errors.append("权限声明缺失（须声明文件路径范围或网络目标）")

        # 入口文件
        if not manifest.entry_point:
            errors.append("入口文件缺失")

        return ValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
        )
