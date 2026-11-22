# =============================================================================
# 用户自主开发脚手架常量测试（tests/unit/test_dev_scaffold.py）
#
# 覆盖 T6.1 / DC-01：DEV_SCAFFOLDS 存在（≥1）/ 五要素非空 /
# required_files 含 manifest.yaml 与入口文件 /
# required_files 与 L2 必需文件集合对拍一致（AC-DEV-03）/
# 导入期 fail-fast（构造非法脚手架即抛，T1.2）。
# 执行：PYTHONPATH=src python -m pytest tests/unit/test_dev_scaffold.py -q
# 涉及不变式：AC-DEV-01/02/03、INV-DEV-16、DC-01。
# =============================================================================

import pytest
import yaml

import orchestration_platform.market.dev_scaffold as dev_scaffold_module
from orchestration_platform.market.dev_scaffold import (
    DEV_SCAFFOLDS,
    _ENTRY_POINT,
    _MANIFEST_FILE,
    _validate_scaffolds,
    get_scaffold,
    list_scaffolds,
)
from orchestration_platform.plugin_sdk.manifest_validator import ManifestValidator
from orchestration_platform.plugin_sdk.models import PluginManifest
from orchestration_platform.plugin_sdk.package_validator import _REQUIRED_FILES

_FIVE_ELEMENT_FIELDS = (
    "directory_structure",
    "manifest_spec",
    "interface_contract",
    "dependency_and_runtime",
)


def _parse_sample_manifest(scaffold) -> PluginManifest:
    """解析脚手架 manifest.yaml 样例为 PluginManifest（对拍 L1/L2 权威模型）。"""
    manifest_template = next(
        template for template in scaffold.file_templates if template.path == _MANIFEST_FILE
    )
    return PluginManifest(**yaml.safe_load(manifest_template.content))


class TestDevScaffoldsConstant:
    """脚手架常量结构校验（T1.1 / AC-DEV-01/02）。"""

    def test_scaffolds_exist_at_least_one(self):
        assert len(DEV_SCAFFOLDS) >= 1

    def test_scaffold_ids_unique(self):
        scaffold_ids = [scaffold.scaffold_id for scaffold in DEV_SCAFFOLDS]
        assert len(scaffold_ids) == len(set(scaffold_ids))

    def test_five_elements_non_empty(self):
        for scaffold in DEV_SCAFFOLDS:
            for field_name in _FIVE_ELEMENT_FIELDS:
                assert getattr(scaffold, field_name), (
                    f"{scaffold.scaffold_id}.{field_name} 不能为空"
                )
            assert scaffold.file_templates, f"{scaffold.scaffold_id} 缺少文件模板（文件格式要素）"

    def test_file_template_contents_non_empty(self):
        for scaffold in DEV_SCAFFOLDS:
            for template in scaffold.file_templates:
                assert template.content, (
                    f"{scaffold.scaffold_id}:{template.path} content 不能为空"
                )

    def test_required_files_contain_manifest_and_entry(self):
        for scaffold in DEV_SCAFFOLDS:
            assert _MANIFEST_FILE in scaffold.required_files
            assert _ENTRY_POINT in scaffold.required_files

    def test_fetch_hint_non_empty(self):
        for scaffold in DEV_SCAFFOLDS:
            assert scaffold.fetch_hint, f"{scaffold.scaffold_id} fetch_hint 不能为空"


class TestRequiredFilesSameAsL2:
    """required_files 与 L2 权威来源对拍（T1.3 / AC-DEV-03）。"""

    def test_required_files_match_l2_authority(self):
        """L2 判定的必需文件集合 = _REQUIRED_FILES ∪ {manifest.entry_point}。"""
        for scaffold in DEV_SCAFFOLDS:
            manifest = _parse_sample_manifest(scaffold)
            l2_required = set(_REQUIRED_FILES) | {manifest.entry_point}
            assert set(scaffold.required_files) == l2_required

    def test_manifest_sample_passes_l1_validation(self):
        """manifest 样例可直接通过 L1 校验（零 error，引导用户不踩必错样例）。"""
        for scaffold in DEV_SCAFFOLDS:
            manifest = _parse_sample_manifest(scaffold)
            result = ManifestValidator().validate(manifest)
            assert result.errors == []
            # 权限声明至少一项非空（避免「权限声明缺失」error）
            assert manifest.declared_file_paths or manifest.declared_network_targets

    def test_entry_template_path_matches_manifest_entry_point(self):
        for scaffold in DEV_SCAFFOLDS:
            manifest = _parse_sample_manifest(scaffold)
            template_paths = {template.path for template in scaffold.file_templates}
            assert manifest.entry_point in template_paths


class TestAccessFunctions:
    """访问函数测试（DV-1/DV-2 数据源）。"""

    def test_list_scaffolds_returns_all(self):
        scaffolds = list_scaffolds()
        assert len(scaffolds) == len(DEV_SCAFFOLDS)

    def test_get_scaffold_found(self):
        scaffold = get_scaffold(DEV_SCAFFOLDS[0].scaffold_id)
        assert scaffold is not None
        assert scaffold.scaffold_id == DEV_SCAFFOLDS[0].scaffold_id

    def test_get_scaffold_not_found_returns_none(self):
        assert get_scaffold("nonexistent_scaffold") is None


class TestImportTimeValidation:
    """导入期 fail-fast 校验测试（T1.2，必做守护 ⑤）。"""

    def test_import_time_validation_passes(self):
        _validate_scaffolds()

    def test_fail_fast_on_empty_scaffolds(self, monkeypatch):
        monkeypatch.setattr(dev_scaffold_module, "DEV_SCAFFOLDS", ())
        with pytest.raises(RuntimeError):
            _validate_scaffolds()

    def test_fail_fast_on_duplicate_scaffold_id(self, monkeypatch):
        duplicated = (DEV_SCAFFOLDS[0], DEV_SCAFFOLDS[0])
        monkeypatch.setattr(dev_scaffold_module, "DEV_SCAFFOLDS", duplicated)
        with pytest.raises(RuntimeError):
            _validate_scaffolds()

    def test_fail_fast_on_invalid_version(self, monkeypatch):
        bad = DEV_SCAFFOLDS[0].model_copy(update={"version": "1.0"})
        monkeypatch.setattr(dev_scaffold_module, "DEV_SCAFFOLDS", (bad,))
        with pytest.raises(RuntimeError):
            _validate_scaffolds()

    def test_fail_fast_on_missing_manifest_file(self, monkeypatch):
        bad = DEV_SCAFFOLDS[0].model_copy(update={"required_files": [_ENTRY_POINT]})
        monkeypatch.setattr(dev_scaffold_module, "DEV_SCAFFOLDS", (bad,))
        with pytest.raises(RuntimeError):
            _validate_scaffolds()

    def test_fail_fast_on_missing_entry_file(self, monkeypatch):
        bad = DEV_SCAFFOLDS[0].model_copy(update={"required_files": [_MANIFEST_FILE]})
        monkeypatch.setattr(dev_scaffold_module, "DEV_SCAFFOLDS", (bad,))
        with pytest.raises(RuntimeError):
            _validate_scaffolds()

    @pytest.mark.parametrize("field_name", _FIVE_ELEMENT_FIELDS)
    def test_fail_fast_on_empty_five_element(self, monkeypatch, field_name):
        bad = DEV_SCAFFOLDS[0].model_copy(update={field_name: ""})
        monkeypatch.setattr(dev_scaffold_module, "DEV_SCAFFOLDS", (bad,))
        with pytest.raises(RuntimeError):
            _validate_scaffolds()

    def test_fail_fast_on_empty_file_templates(self, monkeypatch):
        bad = DEV_SCAFFOLDS[0].model_copy(update={"file_templates": []})
        monkeypatch.setattr(dev_scaffold_module, "DEV_SCAFFOLDS", (bad,))
        with pytest.raises(RuntimeError):
            _validate_scaffolds()

    def test_fail_fast_on_empty_template_content(self, monkeypatch):
        bad_templates = [
            template.model_copy(update={"content": ""})
            if template.path == _MANIFEST_FILE
            else template
            for template in DEV_SCAFFOLDS[0].file_templates
        ]
        bad = DEV_SCAFFOLDS[0].model_copy(update={"file_templates": bad_templates})
        monkeypatch.setattr(dev_scaffold_module, "DEV_SCAFFOLDS", (bad,))
        with pytest.raises(RuntimeError):
            _validate_scaffolds()

    def test_fail_fast_on_required_flag_mismatch(self, monkeypatch):
        bad_templates = [
            template.model_copy(update={"required": False})
            if template.path == _MANIFEST_FILE
            else template
            for template in DEV_SCAFFOLDS[0].file_templates
        ]
        bad = DEV_SCAFFOLDS[0].model_copy(update={"file_templates": bad_templates})
        monkeypatch.setattr(dev_scaffold_module, "DEV_SCAFFOLDS", (bad,))
        with pytest.raises(RuntimeError):
            _validate_scaffolds()
