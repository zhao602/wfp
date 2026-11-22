# =============================================================================
# 三层校验编排服务单元测试（tests/unit/test_dev_validation_service.py）
#
# 覆盖（T8.1 / AC-DEV-04~08/16/17 / DC-02）：
#   - L1 各规则（缺节点名/版本/入口、格式非法、权限双空 → error；
#     input_schema/output_schema 空 → warning；YAML 解析失败/非映射/字段校验失败 → error）
#   - L2（缺 manifest.yaml / 缺入口文件 → error）
#   - L3（危险 API / 未声明外联 / 语法错误 → error 含定位；动态导入 → warning）
#   - valid 判定（error 数为 0；warning 不阻断）；报告字段（layers/scanner_version/
#     checked_at/report_id/error）；层次顺序 L1→L2→L3 不短路
#   - 扫描器异常（抛异常或报告携带 error）→ error 非空且 valid=False
#   - 扩展规则（manifest_unknown_field / entry_point_format / requirements_absent /
#     manifest_too_large）
#   - L2 包装能力（compute_digest 与既有校验器一致）；等价逻辑加载 load_manifest
# =============================================================================

from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from orchestration_platform.market.dev_models import DevFinding, DevValidationReport
from orchestration_platform.market.dev_validation_service import DevValidationService
from orchestration_platform.mcp_tool_market.contribution.models import ScanReport
from orchestration_platform.plugin_sdk.models import PluginManifest
from orchestration_platform.plugin_sdk.package_validator import NodePackageValidator

_VALID_NODE_PY = "def run(query: str) -> str:\n    return query\n"


def _base_manifest() -> Dict[str, object]:
    """构造一份可通过三层校验的 manifest 数据。"""
    return {
        "node_name": "demo-node",
        "version": "1.0.0",
        "description": "demo node",
        "input_schema": {"query": "string"},
        "output_schema": {"result": "string"},
        "dependencies": [],
        "declared_file_paths": ["./data/"],
        "declared_network_targets": [],
        "entry_point": "node.py",
    }


def _write_package(
    pkg_dir: Path,
    manifest: Optional[Dict[str, object]] = None,
    node_py: Optional[str] = _VALID_NODE_PY,
    with_manifest: bool = True,
) -> Path:
    """在临时目录落盘一个待校验包（manifest.yaml + node.py）。"""
    pkg_dir.mkdir(parents=True, exist_ok=True)
    if with_manifest:
        data = _base_manifest() if manifest is None else manifest
        with open(pkg_dir / "manifest.yaml", "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True)
    if node_py is not None:
        with open(pkg_dir / "node.py", "w", encoding="utf-8") as f:
            f.write(node_py)
    return pkg_dir


def _by_rule(
    report: DevValidationReport, rule_id: str, layer: Optional[str] = None
) -> List[DevFinding]:
    """按 rule_id（可选按 layer）筛选报告发现项。"""
    return [
        f
        for f in report.findings
        if f.rule_id == rule_id and (layer is None or f.layer == layer)
    ]


class TestL1MetadataRules:
    """L1 元数据校验规则（直接复用 ManifestValidator 的逐条映射）。"""

    def test_node_name_missing_is_error(self, tmp_path: Path) -> None:
        manifest = _base_manifest()
        manifest["node_name"] = ""
        pkg = _write_package(tmp_path, manifest=manifest)
        report = DevValidationService().validate(str(pkg))
        findings = _by_rule(report, "node_name_missing", "L1")
        assert len(findings) == 1
        assert findings[0].severity == "error"
        assert report.valid is False

    def test_node_name_format_is_error(self, tmp_path: Path) -> None:
        manifest = _base_manifest()
        manifest["node_name"] = "Bad_Name"
        pkg = _write_package(tmp_path, manifest=manifest)
        report = DevValidationService().validate(str(pkg))
        findings = _by_rule(report, "node_name_format", "L1")
        assert len(findings) == 1
        assert findings[0].severity == "error"

    def test_version_missing_is_error(self, tmp_path: Path) -> None:
        manifest = _base_manifest()
        manifest["version"] = ""
        pkg = _write_package(tmp_path, manifest=manifest)
        report = DevValidationService().validate(str(pkg))
        findings = _by_rule(report, "version_missing", "L1")
        assert len(findings) == 1
        assert findings[0].severity == "error"

    def test_version_format_is_error(self, tmp_path: Path) -> None:
        manifest = _base_manifest()
        manifest["version"] = "v1"
        pkg = _write_package(tmp_path, manifest=manifest)
        report = DevValidationService().validate(str(pkg))
        findings = _by_rule(report, "version_format", "L1")
        assert len(findings) == 1
        assert findings[0].severity == "error"
        assert report.valid is False

    def test_permission_undeclared_is_error(self, tmp_path: Path) -> None:
        manifest = _base_manifest()
        manifest["declared_file_paths"] = []
        manifest["declared_network_targets"] = []
        pkg = _write_package(tmp_path, manifest=manifest)
        report = DevValidationService().validate(str(pkg))
        findings = _by_rule(report, "permission_undeclared", "L1")
        assert len(findings) == 1
        assert findings[0].severity == "error"

    def test_entry_point_missing_is_error(self, tmp_path: Path) -> None:
        manifest = _base_manifest()
        manifest["entry_point"] = ""
        pkg = _write_package(tmp_path, manifest=manifest)
        report = DevValidationService().validate(str(pkg))
        findings = _by_rule(report, "entry_point_missing", "L1")
        assert len(findings) == 1
        assert findings[0].severity == "error"

    def test_empty_schemas_are_warnings_not_blocking(self, tmp_path: Path) -> None:
        manifest = _base_manifest()
        manifest["input_schema"] = {}
        manifest["output_schema"] = {}
        pkg = _write_package(tmp_path, manifest=manifest)
        report = DevValidationService().validate(str(pkg))
        input_findings = _by_rule(report, "input_schema_undeclared", "L1")
        output_findings = _by_rule(report, "output_schema_undeclared", "L1")
        assert len(input_findings) == 1
        assert input_findings[0].severity == "warning"
        assert len(output_findings) == 1
        assert output_findings[0].severity == "warning"
        # warning 不阻断（FR-8）：无 error 级发现 → valid=True
        assert report.valid is True

    def test_yaml_parse_error_is_error(self, tmp_path: Path) -> None:
        pkg = _write_package(tmp_path, with_manifest=False)
        (pkg / "manifest.yaml").write_text("key: [1, 2", encoding="utf-8")
        report = DevValidationService().validate(str(pkg))
        findings = _by_rule(report, "manifest_parse_error", "L1")
        assert len(findings) == 1
        assert findings[0].severity == "error"
        assert report.valid is False

    def test_non_mapping_manifest_is_parse_error(self, tmp_path: Path) -> None:
        pkg = _write_package(tmp_path, with_manifest=False)
        (pkg / "manifest.yaml").write_text("- a\n- b\n", encoding="utf-8")
        report = DevValidationService().validate(str(pkg))
        findings = _by_rule(report, "manifest_parse_error", "L1")
        assert len(findings) == 1
        assert findings[0].severity == "error"

    def test_schema_error_is_error(self, tmp_path: Path) -> None:
        manifest = _base_manifest()
        manifest["version"] = [1, 0]
        pkg = _write_package(tmp_path, manifest=manifest)
        report = DevValidationService().validate(str(pkg))
        findings = _by_rule(report, "manifest_schema_error", "L1")
        assert len(findings) == 1
        assert findings[0].severity == "error"

    def test_manifest_missing_is_error(self, tmp_path: Path) -> None:
        pkg = _write_package(tmp_path, with_manifest=False)
        report = DevValidationService().validate(str(pkg))
        findings = _by_rule(report, "manifest_missing", "L1")
        assert len(findings) == 1
        assert findings[0].severity == "error"


class TestL2PackageStructure:
    """L2 目录结构校验（包装 NodePackageValidator 的逐条映射）。"""

    def test_missing_required_manifest_file_is_error(self, tmp_path: Path) -> None:
        pkg = _write_package(tmp_path, with_manifest=False)
        report = DevValidationService().validate(str(pkg))
        findings = _by_rule(report, "missing_required_file", "L2")
        assert len(findings) == 1
        assert findings[0].severity == "error"
        assert "manifest.yaml" in findings[0].message

    def test_missing_entry_file_is_error(self, tmp_path: Path) -> None:
        manifest = _base_manifest()
        manifest["entry_point"] = "gone.py"
        pkg = _write_package(tmp_path, manifest=manifest, node_py=None)
        report = DevValidationService().validate(str(pkg))
        findings = _by_rule(report, "missing_entry_file", "L2")
        assert len(findings) == 1
        assert findings[0].severity == "error"
        assert "gone.py" in findings[0].message


class TestL3StaticSecurity:
    """L3 静态安全校验（直接复用 StaticSecurityScanner 的映射）。"""

    def test_dangerous_call_is_error_with_location(self, tmp_path: Path) -> None:
        pkg = _write_package(
            tmp_path, node_py='result = eval("1 + 1")\n'
        )
        report = DevValidationService().validate(str(pkg))
        findings = _by_rule(report, "dangerous_call", "L3")
        assert len(findings) == 1
        assert findings[0].severity == "error"
        assert findings[0].location == "node.py:1"
        assert report.valid is False

    def test_undeclared_network_is_error(self, tmp_path: Path) -> None:
        pkg = _write_package(
            tmp_path,
            node_py='import requests\nrequests.get("https://example.com")\n',
        )
        report = DevValidationService().validate(str(pkg))
        findings = _by_rule(report, "undeclared_network", "L3")
        assert len(findings) == 1
        assert findings[0].severity == "error"
        assert findings[0].location == "node.py:2"

    def test_declared_network_suppresses_finding(self, tmp_path: Path) -> None:
        manifest = _base_manifest()
        manifest["declared_network_targets"] = ["api.example.com"]
        pkg = _write_package(
            tmp_path,
            manifest=manifest,
            node_py='import requests\nrequests.get("https://api.example.com")\n',
        )
        report = DevValidationService().validate(str(pkg))
        assert _by_rule(report, "undeclared_network") == []
        assert report.valid is True

    def test_dynamic_import_is_warning_not_blocking(self, tmp_path: Path) -> None:
        pkg = _write_package(
            tmp_path,
            node_py='import importlib\nimportlib.import_module("json")\n',
        )
        report = DevValidationService().validate(str(pkg))
        findings = _by_rule(report, "dynamic_import", "L3")
        assert len(findings) == 1
        assert findings[0].severity == "warning"
        # 动态导入仅提示不阻断：无 error 级发现 → valid=True
        assert report.valid is True

    def test_syntax_error_is_error(self, tmp_path: Path) -> None:
        pkg = _write_package(tmp_path, node_py="def broken(:\n")
        report = DevValidationService().validate(str(pkg))
        findings = _by_rule(report, "syntax_error", "L3")
        assert len(findings) == 1
        assert findings[0].severity == "error"


class TestOrchestrationAndReport:
    """编排顺序、判定与报告字段（T7.4）。"""

    def test_valid_package_passes(self, tmp_path: Path) -> None:
        pkg = _write_package(tmp_path)
        report = DevValidationService().validate(str(pkg))
        assert report.valid is True
        assert report.findings == []
        assert report.error is None

    def test_no_short_circuit_all_layers_execute(self, tmp_path: Path) -> None:
        manifest = _base_manifest()
        manifest["version"] = "v1"
        pkg = _write_package(tmp_path, manifest=manifest)
        report = DevValidationService().validate(str(pkg))
        # L1 失败不短路：三层均执行且发现合并（AC-DEV-04/08）
        assert report.layers == ["L1", "L2", "L3"]
        assert len(_by_rule(report, "version_format", "L1")) == 1
        assert any(f.layer == "L2" for f in report.findings)
        assert report.valid is False

    def test_report_fields_present(self, tmp_path: Path) -> None:
        pkg = _write_package(tmp_path)
        report = DevValidationService().validate(str(pkg))
        assert report.layers == ["L1", "L2", "L3"]
        assert report.scanner_version == "1.0.0"
        assert report.report_id
        assert report.checked_at.tzinfo is not None
        elapsed = (datetime.now(timezone.utc) - report.checked_at).total_seconds()
        assert -5 <= elapsed < 5
        assert report.error is None

    def test_findings_ordered_by_layer(self, tmp_path: Path) -> None:
        manifest = _base_manifest()
        manifest["version"] = "v1"
        pkg = _write_package(
            tmp_path, manifest=manifest, node_py='result = eval("1 + 1")\n'
        )
        report = DevValidationService().validate(str(pkg))
        layer_sequence = [f.layer for f in report.findings]
        assert layer_sequence.index("L1") < layer_sequence.index("L2")
        assert layer_sequence.index("L2") < layer_sequence.index("L3")

    def test_param_manifest_ignored_when_package_loadable(
        self, tmp_path: Path
    ) -> None:
        pkg = _write_package(tmp_path)
        bad_param = PluginManifest(node_name="Bad", version="9.9.9")
        report = DevValidationService().validate(str(pkg), manifest=bad_param)
        # 包内 manifest 可加载时以包内结果为准（包为被校验对象）
        assert _by_rule(report, "node_name_format") == []
        assert _by_rule(report, "permission_undeclared") == []
        assert report.valid is True

    def test_param_manifest_used_as_fallback(self, tmp_path: Path) -> None:
        pkg = _write_package(tmp_path, with_manifest=False)
        good_param = PluginManifest(
            node_name="demo-node",
            version="1.0.0",
            declared_file_paths=["./data/"],
        )
        report = DevValidationService().validate(str(pkg), manifest=good_param)
        # 包内加载失败：文件缺失仍登记 L1 error，但传入 manifest 作为回退参与校验
        assert len(_by_rule(report, "manifest_missing", "L1")) == 1
        assert _by_rule(report, "manifest_schema_error") == []
        assert report.valid is False


class TestScannerExceptionPaths:
    """扫描器自身异常路径（T7.4：error 非空且 valid=False）。"""

    def test_scanner_exception_sets_error_and_invalid(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        pkg = _write_package(tmp_path)
        service = DevValidationService()

        def _boom(package_dir: str, manifest: PluginManifest) -> ScanReport:
            raise RuntimeError("boom")

        monkeypatch.setattr(service._scanner, "scan", _boom)
        report = service.validate(str(pkg))
        assert report.error is not None
        assert "RuntimeError" in report.error
        assert "boom" in report.error
        assert report.valid is False
        assert report.layers == ["L1", "L2", "L3"]

    def test_scanner_report_error_propagates(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        pkg = _write_package(tmp_path)
        service = DevValidationService()
        broken = ScanReport(
            report_id="r", passed=False, findings=[], error="scanner broke"
        )
        monkeypatch.setattr(
            service._scanner, "scan", lambda package_dir, manifest: broken
        )
        report = service.validate(str(pkg))
        assert report.error == "scanner broke"
        assert report.valid is False


class TestExtensionRules:
    """扩展规则（T7.6，不改既有校验器）。"""

    def test_manifest_unknown_field_is_warning(self, tmp_path: Path) -> None:
        manifest = _base_manifest()
        manifest["foo"] = "bar"
        pkg = _write_package(tmp_path, manifest=manifest)
        report = DevValidationService().validate(str(pkg))
        findings = _by_rule(report, "manifest_unknown_field", "L1")
        assert len(findings) == 1
        assert findings[0].severity == "warning"
        assert "foo" in findings[0].message
        # 未知字段仅告警：valid 不受影响
        assert report.valid is True

    def test_entry_point_separator_is_warning(self, tmp_path: Path) -> None:
        manifest = _base_manifest()
        manifest["entry_point"] = "src/node.py"
        pkg = _write_package(tmp_path, manifest=manifest)
        report = DevValidationService().validate(str(pkg))
        findings = _by_rule(report, "entry_point_format", "L1")
        assert len(findings) == 1
        assert findings[0].severity == "warning"

    def test_entry_point_suffix_is_warning(self, tmp_path: Path) -> None:
        manifest = _base_manifest()
        manifest["entry_point"] = "node.txt"
        pkg = _write_package(tmp_path, manifest=manifest)
        report = DevValidationService().validate(str(pkg))
        findings = _by_rule(report, "entry_point_format", "L1")
        assert len(findings) == 1
        assert findings[0].severity == "warning"

    def test_entry_point_parent_reference_is_warning(self, tmp_path: Path) -> None:
        manifest = _base_manifest()
        manifest["entry_point"] = "../node.py"
        pkg = _write_package(tmp_path, manifest=manifest)
        report = DevValidationService().validate(str(pkg))
        findings = _by_rule(report, "entry_point_format", "L1")
        assert len(findings) == 1
        assert findings[0].severity == "warning"

    def test_requirements_absent_is_warning(self, tmp_path: Path) -> None:
        manifest = _base_manifest()
        manifest["dependencies"] = ["requests"]
        pkg = _write_package(tmp_path, manifest=manifest)
        report = DevValidationService().validate(str(pkg))
        findings = _by_rule(report, "requirements_absent", "L2")
        assert len(findings) == 1
        assert findings[0].severity == "warning"
        # 声明依赖缺清单仅告警：valid 不受影响
        assert report.valid is True

    def test_requirements_present_no_warning(self, tmp_path: Path) -> None:
        manifest = _base_manifest()
        manifest["dependencies"] = ["requests"]
        pkg = _write_package(tmp_path, manifest=manifest)
        (pkg / "requirements.txt").write_text("requests\n", encoding="utf-8")
        report = DevValidationService().validate(str(pkg))
        assert _by_rule(report, "requirements_absent") == []
        assert report.valid is True

    def test_manifest_too_large_is_error(self, tmp_path: Path) -> None:
        pkg = _write_package(tmp_path, with_manifest=False)
        (pkg / "manifest.yaml").write_text("a: 1\n" * 400, encoding="utf-8")
        service = DevValidationService(max_manifest_kb=1)
        report = service.validate(str(pkg))
        findings = _by_rule(report, "manifest_too_large", "L2")
        assert len(findings) == 1
        assert findings[0].severity == "error"
        # 超限跳过解析：不再产生 L1 解析/字段校验发现
        assert _by_rule(report, "manifest_parse_error", "L1") == []
        assert _by_rule(report, "manifest_schema_error", "L1") == []
        assert report.valid is False


class TestDigestAndLoader:
    """L2 包装能力（compute_digest）与等价逻辑加载（load_manifest）。"""

    def test_compute_digest_matches_validator(self, tmp_path: Path) -> None:
        pkg = _write_package(tmp_path)
        digest = DevValidationService().compute_digest(str(pkg))
        expected = NodePackageValidator().compute_digest(str(pkg))
        assert digest == expected
        assert len(digest) == 64
        int(digest, 16)

    def test_load_manifest_valid(self, tmp_path: Path) -> None:
        pkg = _write_package(tmp_path)
        manifest = DevValidationService().load_manifest(str(pkg))
        assert manifest is not None
        assert manifest.node_name == "demo-node"
        assert manifest.version == "1.0.0"

    def test_load_manifest_invalid_yaml_returns_none(self, tmp_path: Path) -> None:
        pkg = _write_package(tmp_path, with_manifest=False)
        (pkg / "manifest.yaml").write_text("key: [1, 2", encoding="utf-8")
        assert DevValidationService().load_manifest(str(pkg)) is None

    def test_load_manifest_missing_returns_none(self, tmp_path: Path) -> None:
        pkg = _write_package(tmp_path, with_manifest=False)
        assert DevValidationService().load_manifest(str(pkg)) is None

    def test_load_manifest_too_large_returns_none(self, tmp_path: Path) -> None:
        pkg = _write_package(tmp_path, with_manifest=False)
        (pkg / "manifest.yaml").write_text("a: 1\n" * 400, encoding="utf-8")
        service = DevValidationService(max_manifest_kb=1)
        assert service.load_manifest(str(pkg)) is None
