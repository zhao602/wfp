# =============================================================================
# 三层校验编排服务（market/dev_validation_service.py）
#
# 职责（agent_market_user_development T7 / CHG-04 / DC-02）：
#   1. L1 元数据校验：直接复用 ManifestValidator.validate(manifest)（不继承、不改类），
#      ValidationResult.errors/warnings 逐条映射为 DevFinding(layer="L1")；
#      manifest.yaml 解析以 NodePackageValidator._load_manifest 的等价逻辑实现
#      （yaml.safe_load → PluginManifest(**data)，不导入其私有常量与私有方法）；
#   2. L2 目录结构校验：包装 NodePackageValidator.validate(dir) 与 compute_digest(dir)，
#      结果逐条映射为 DevFinding(layer="L2")（既有校验器逐字节零改动）；
#   3. L3 静态安全校验：直接复用 StaticSecurityScanner.scan(dir, manifest)，
#      ScanFinding 直接映射为 DevFinding(layer="L3")（含 location，规则表不扩展）；
#   4. 编排顺序 L1→L2→L3，不短路（仅解压/防御预检失败时跳过，由提交侧 Guard 承担）；
#      valid = severity 为 error 的 findings 数为 0 且校验器无自身异常；
#   5. 扩展规则（不改既有校验器）：manifest_unknown_field / entry_point_format（L1，
#      warning）、requirements_absent（L2，warning）、manifest_too_large（L2，error）。
# 约束：Python 3.10 兼容；零第三方新增依赖（yaml/pydantic 均为既有依赖）。
# =============================================================================

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
from pydantic import ValidationError

from orchestration_platform.market.dev_models import DevFinding, DevValidationReport
from orchestration_platform.mcp_tool_market.contribution.static_security_scanner import (
    StaticSecurityScanner,
)
from orchestration_platform.plugin_sdk.manifest_validator import ManifestValidator
from orchestration_platform.plugin_sdk.models import PluginManifest, ValidationResult
from orchestration_platform.plugin_sdk.package_validator import NodePackageValidator

# 统一报告版本标识（与 StaticSecurityScanner 默认 scanner_version 对齐）
_SCANNER_VERSION = "1.0.0"

# manifest 文件名（与 L2 判定的权威来源语义一致，不导入其私有常量）
_MANIFEST_FILE = "manifest.yaml"

# 依赖清单文件名（requirements_absent 判定用）
_REQUIREMENTS_FILE = "requirements.txt"

# PluginManifest 已声明字段集合（manifest_unknown_field 判定基准）
_MANIFEST_FIELD_NAMES = frozenset(PluginManifest.model_fields.keys())

# 消息前缀 → rule_id 映射（ManifestValidator/NodePackageValidator 输出为固定文案，
# 各前缀互不为前缀；未命中时回退到层次默认 rule_id）
_RULE_ID_BY_PREFIX: Tuple[Tuple[str, str], ...] = (
    ("节点名称格式非法", "node_name_format"),
    ("节点名称缺失", "node_name_missing"),
    ("版本号格式非法", "version_format"),
    ("版本号缺失", "version_missing"),
    ("输入模式未声明", "input_schema_undeclared"),
    ("输出模式未声明", "output_schema_undeclared"),
    ("权限声明缺失", "permission_undeclared"),
    ("入口文件缺失", "entry_point_missing"),
    ("缺少必需文件", "missing_required_file"),
    ("入口文件不存在", "missing_entry_file"),
    ("包路径非目录", "package_not_dir"),
    ("manifest 加载失败", "manifest_parse_error"),
)


def _rule_id_for(message: str, default_rule_id: str) -> str:
    """按消息前缀推导 rule_id（未命中回退默认值）。"""
    for prefix, rule_id in _RULE_ID_BY_PREFIX:
        if message.startswith(prefix):
            return rule_id
    return default_rule_id


class DevValidationService:
    """三层校验编排服务（DC-02：L1 直接复用 / L2 包装扩展 / L3 直接复用）。

    只校验不执行用户代码：全部校验均基于静态读取（YAML/目录/AST 扫描），
    不加载、不导入被校验包内任何模块。
    """

    def __init__(self, max_manifest_kb: int = 64) -> None:
        self._manifest_validator = ManifestValidator()
        self._package_validator = NodePackageValidator()
        self._scanner = StaticSecurityScanner()
        self._max_manifest_kb = max_manifest_kb

    def validate(
        self, package_dir: str, manifest: Optional[PluginManifest] = None
    ) -> DevValidationReport:
        """执行三层校验（L1→L2→L3，不短路），返回统一报告。

        参数：
            package_dir —— 已净化（解压后）的包目录路径
            manifest    —— 可选的已加载 manifest；包内 manifest.yaml 可加载时以
                           包内结果为准（包为被校验对象），包内加载失败时回退使用
        """
        l1_findings: List[DevFinding] = []
        l2_findings: List[DevFinding] = []
        l3_findings: List[DevFinding] = []
        report_error: Optional[str] = None

        # --- L1：manifest 加载（等价逻辑）+ 元数据校验 + L1 扩展规则 ---
        loaded_manifest, raw_data, load_findings = self._load_manifest(package_dir)
        for finding in load_findings:
            if finding.layer == "L2":
                l2_findings.append(finding)
            else:
                l1_findings.append(finding)

        effective_manifest = (
            loaded_manifest if loaded_manifest is not None else manifest
        )

        # L1 扩展：未知字段告警（PluginManifest 默认忽略未知键，此处补告警）
        if raw_data is not None:
            for key in sorted(raw_data.keys()):
                if key not in _MANIFEST_FIELD_NAMES:
                    l1_findings.append(
                        DevFinding(
                            layer="L1",
                            rule_id="manifest_unknown_field",
                            severity="warning",
                            message=f"manifest 含未知字段: {key}",
                        )
                    )

        if effective_manifest is not None:
            # L1 直接复用：ManifestValidator 逐条映射
            manifest_result: ValidationResult = self._manifest_validator.validate(
                effective_manifest
            )
            for message in manifest_result.errors:
                l1_findings.append(
                    DevFinding(
                        layer="L1",
                        severity="error",
                        message=message,
                        rule_id=_rule_id_for(message, "manifest_invalid"),
                    )
                )
            for message in manifest_result.warnings:
                l1_findings.append(
                    DevFinding(
                        layer="L1",
                        severity="warning",
                        message=message,
                        rule_id=_rule_id_for(message, "manifest_invalid"),
                    )
                )

            # L1 扩展：入口文件路径格式（防「声明指向包外」，本期仅告警）
            entry_point = effective_manifest.entry_point
            if entry_point and (
                "/" in entry_point
                or "\\" in entry_point
                or ".." in entry_point
                or not entry_point.endswith(".py")
            ):
                l1_findings.append(
                    DevFinding(
                        layer="L1",
                        rule_id="entry_point_format",
                        severity="warning",
                        message=f"入口文件路径不规范（应为本目录内 .py 文件）: {entry_point}",
                    )
                )

        # --- L2：包装 NodePackageValidator + L2 扩展规则 ---
        package_result: ValidationResult = self._package_validator.validate(package_dir)
        for message in package_result.errors:
            l2_findings.append(
                DevFinding(
                    layer="L2",
                    severity="error",
                    message=message,
                    rule_id=_rule_id_for(message, "package_invalid"),
                )
            )
        for message in package_result.warnings:
            l2_findings.append(
                DevFinding(
                    layer="L2",
                    severity="warning",
                    message=message,
                    rule_id=_rule_id_for(message, "package_invalid"),
                )
            )

        # L2 扩展：声明依赖但缺 requirements.txt（仅告警）
        if (
            effective_manifest is not None
            and effective_manifest.dependencies
            and not (Path(package_dir) / _REQUIREMENTS_FILE).exists()
        ):
            l2_findings.append(
                DevFinding(
                    layer="L2",
                    rule_id="requirements_absent",
                    severity="warning",
                    message="manifest 声明了依赖但缺少 requirements.txt",
                )
            )

        # --- L3：直接复用 StaticSecurityScanner（不短路；manifest 不可用时保守降级） ---
        scan_manifest = (
            effective_manifest
            if effective_manifest is not None
            else PluginManifest(node_name="", version="")
        )
        try:
            scan_report = self._scanner.scan(package_dir, scan_manifest)
        except Exception as exc:
            # 扫描器自身异常 → 报告 error 非空且 valid=False（T7.4），不等同「不安全」
            report_error = f"{type(exc).__name__}: {exc}"
        else:
            for finding in scan_report.findings:
                l3_findings.append(
                    DevFinding(
                        layer="L3",
                        severity=finding.severity,
                        message=finding.message,
                        rule_id=finding.rule_id,
                        location=finding.location,
                    )
                )
            if scan_report.error:
                report_error = scan_report.error

        findings = l1_findings + l2_findings + l3_findings
        has_error = any(finding.severity == "error" for finding in findings)

        return DevValidationReport(
            report_id=str(uuid.uuid4()),
            valid=not has_error and report_error is None,
            layers=["L1", "L2", "L3"],
            findings=findings,
            scanner_version=_SCANNER_VERSION,
            checked_at=datetime.now(timezone.utc),
            error=report_error,
        )

    def load_manifest(self, package_dir: str) -> Optional[PluginManifest]:
        """以等价逻辑加载包内 manifest（yaml.safe_load → PluginManifest(**data)）。

        供调用方在校验前获取 manifest（如重复提交预判）；解析/校验失败返回 None，
        失败发现项由 validate 统一产出（本方法不重复产出报告）。
        """
        manifest, _raw_data, _findings = self._load_manifest(package_dir)
        return manifest

    def compute_digest(self, package_dir: str) -> str:
        """计算包内容 SHA-256 摘要（L2 包装能力；仅可在已净化目录上调用）。"""
        return self._package_validator.compute_digest(package_dir)

    def _load_manifest(
        self, package_dir: str
    ) -> Tuple[Optional[PluginManifest], Optional[Dict[str, Any]], List[DevFinding]]:
        """等价逻辑加载 manifest.yaml（不导入 NodePackageValidator 私有成员）。

        返回 (manifest, 原始键值映射, 加载阶段发现项)：
        - 文件缺失 → L1 manifest_missing（error）；
        - 超过 max_manifest_kb → L2 manifest_too_large（error），跳过解析；
        - YAML 解析失败/非映射 → L1 manifest_parse_error（error）；
        - PluginManifest 字段校验失败 → L1 manifest_schema_error（error）。
        """
        manifest_path = Path(package_dir) / _MANIFEST_FILE
        findings: List[DevFinding] = []

        if not manifest_path.exists():
            findings.append(
                DevFinding(
                    layer="L1",
                    rule_id="manifest_missing",
                    severity="error",
                    message=f"缺少 manifest 文件: {_MANIFEST_FILE}",
                )
            )
            return None, None, findings

        if manifest_path.stat().st_size > self._max_manifest_kb * 1024:
            # 超限属 L2 扩展规则（design §5.2）；同时跳过解析，避免解析超大文件
            findings.append(
                DevFinding(
                    layer="L2",
                    rule_id="manifest_too_large",
                    severity="error",
                    message=f"manifest 文件超过大小上限: {self._max_manifest_kb}KB",
                )
            )
            return None, None, findings

        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                data: Any = yaml.safe_load(f)
        except (yaml.YAMLError, OSError, UnicodeDecodeError) as exc:
            findings.append(
                DevFinding(
                    layer="L1",
                    rule_id="manifest_parse_error",
                    severity="error",
                    message=f"manifest 解析失败: {exc}",
                )
            )
            return None, None, findings

        if not isinstance(data, dict):
            findings.append(
                DevFinding(
                    layer="L1",
                    rule_id="manifest_parse_error",
                    severity="error",
                    message="manifest 内容不是键值映射",
                )
            )
            return None, None, findings

        try:
            manifest = PluginManifest(**data)
        except (ValidationError, TypeError) as exc:
            findings.append(
                DevFinding(
                    layer="L1",
                    rule_id="manifest_schema_error",
                    severity="error",
                    message=f"manifest 字段校验失败: {exc}",
                )
            )
            return None, None, findings

        return manifest, data, findings
