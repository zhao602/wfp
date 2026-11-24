# =============================================================================
# 架构规范单测：开关矩阵一致性（tests/unit/test_switch_matrix_consistency.py）
#
# 职责（roadmap M4 B10 / design D8）：固化 docs/switch-matrix.md 主表与
# Settings bool 字段集合的一致性——
#   1. 主表行名集合 == Settings.model_fields 全部 bool 字段集合（54 个，
#      全为开关语义；新增 bool 字段必须同步登记矩阵，反之删除字段须删行）；
#   2. 主表「默认」列与字段默认值逐一比对（防文档漂移）；
#   3. OEM 单列节与 scripts/build_oem.py MODULE_REGISTRY 交叉核对
#      （module_id→env flag→Settings 字段三向一致，防两侧漂移）；
#   4. 未接线开关登记节 ⊆ 主表行名集合。
# =============================================================================

import importlib.util
import re
import sys
from pathlib import Path

from orchestration_platform.configuration.settings import Settings

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DOC_PATH = REPO_ROOT / "docs" / "switch-matrix.md"
BUILD_OEM_PATH = REPO_ROOT / "scripts" / "build_oem.py"

MAIN_TABLE_HEADER = "## 54 开关全表"
OEM_HEADER = "## OEM build-time 3 开关（单列）"
UNWIRED_HEADER = "## 未接线开关登记（欠账，出口记录收口）"


def _section(text: str, header: str) -> str:
    """提取 markdown 段落（自 header 之后至下一个二级标题）。"""
    start = text.index(header) + len(header)
    next_header = re.search(r"^## ", text[start:], re.MULTILINE)
    end = start + next_header.start() if next_header else len(text)
    return text[start:end]


def _parse_main_table(doc: str) -> dict:
    """解析主表行：{开关名: (默认值字符串, settings.py 行号字符串)}。"""
    section = _section(doc, MAIN_TABLE_HEADER)
    row_re = re.compile(
        r"^\|\s*([a-z][a-z0-9_]*)\s*\|\s*(True|False)\s*\|\s*(\d+)\s*\|"
    )
    rows = {}
    for line in section.splitlines():
        match = row_re.match(line)
        if match:
            rows[match.group(1)] = (match.group(2), match.group(3))
    return rows


def _parse_oem_table(doc: str) -> dict:
    """解析 OEM 单列表：{Settings 字段名: env flag}。"""
    section = _section(doc, OEM_HEADER)
    rows = {}
    # | field | module_id | menu key | ENV_FLAG | line | gate |
    row_re = re.compile(
        r"^\|\s*([a-z][a-z0-9_]*)\s*\|[^|]+\|[^|]+\|\s*([A-Z_]+)\s*\|"
    )
    for line in section.splitlines():
        match = row_re.match(line)
        if match:
            rows[match.group(1)] = match.group(2)
    return rows


def _load_build_oem_registry() -> dict:
    """按路径加载 scripts/build_oem.py 的 MODULE_REGISTRY（{字段名: env flag}）。"""
    spec = importlib.util.spec_from_file_location("build_oem", BUILD_OEM_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    # module_id → (menu key, env flag)；env flag 大写即 Settings 字段名大写
    return {
        env_flag.lower(): env_flag
        for _mid, (_menu, env_flag) in module.MODULE_REGISTRY.items()
    }


class TestSwitchMatrixConsistency:
    """架构规范：switch-matrix 与 Settings bool 字段集合一致（design D8）。"""

    def test_main_table_rows_match_settings_bool_fields(self):
        """主表行名集合 == Settings.model_fields bool 字段集合（双向零差）。"""
        doc = DOC_PATH.read_text(encoding="utf-8")
        rows = _parse_main_table(doc)
        bool_fields = {
            name for name, field in Settings.model_fields.items()
            if field.annotation is bool
        }
        assert len(rows) == len(bool_fields), (
            f"行数 {len(rows)} != bool 字段数 {len(bool_fields)}"
            f"（若新增 bool 开关须同步登记 docs/switch-matrix.md）"
        )
        only_in_md = set(rows) - bool_fields
        only_in_settings = bool_fields - set(rows)
        assert not only_in_md, f"主表多出已删除字段：{sorted(only_in_md)}"
        assert not only_in_settings, (
            f"Settings bool 字段未登记主表：{sorted(only_in_settings)}"
        )

    def test_main_table_defaults_match_settings(self):
        """主表「默认」列与字段默认值逐一比对（防文档漂移）。"""
        doc = DOC_PATH.read_text(encoding="utf-8")
        rows = _parse_main_table(doc)
        defaults = {
            name: field.default
            for name, field in Settings.model_fields.items()
            if field.annotation is bool
        }
        for name, (md_default, _line) in rows.items():
            assert md_default == str(defaults[name]), (
                f"{name}: 主表默认 {md_default} != Settings 默认 "
                f"{defaults[name]}"
            )

    def test_oem_section_matches_build_oem_registry(self):
        """OEM 单列节与 build_oem.MODULE_REGISTRY 三向一致
        （字段名↔env flag 双射；字段均属 54 开关集合）。"""
        doc = DOC_PATH.read_text(encoding="utf-8")
        oem_rows = _parse_oem_table(doc)
        registry = _load_build_oem_registry()
        assert oem_rows == registry, (
            f"OEM 单列与 MODULE_REGISTRY 漂移：md={oem_rows} "
            f"registry={registry}"
        )
        bool_fields = {
            name for name, field in Settings.model_fields.items()
            if field.annotation is bool
        }
        for field_name in oem_rows:
            assert field_name in bool_fields, (
                f"OEM 字段 {field_name} 不在 Settings bool 开关集合内"
            )

    def test_unwired_registration_subset_of_main_table(self):
        """未接线开关登记节的行名 ⊆ 主表行名集合（登记对象真实存在）。"""
        doc = DOC_PATH.read_text(encoding="utf-8")
        rows = _parse_main_table(doc)
        section = _section(doc, UNWIRED_HEADER)
        registered = re.findall(r"^\|\s*([a-z][a-z0-9_]*)\s*\|", section, re.MULTILINE)
        assert len(registered) > 0, "未接线登记节为空（解析失败或内容缺失）"
        for name in registered:
            assert name in rows, f"未接线登记引用了主表外开关：{name}"
