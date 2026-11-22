# =============================================================================
# OEM 构建脚本单测（tests/unit/test_build_oem.py，roadmap_b5_oem_whitelabel）
#
# 覆盖（design §8/§9 测试矩阵；vite subprocess 隔离在 main 路径，不在单测触发）：
#   1. load_brand：两份真实品牌配置合法（default=现状值/acmeflow=紫系全关）；
#   2. load_brand：未知 module_id/未知顶层键/缺失颜色键/非法色值/非布尔模块值/
#      非字符串产品名 → BrandConfigError（构建期强校验）；
#   3. render_frontend_env：12 个 VITE_OEM_*；acmeflow DISABLED_PANELS=
#      三禁用面板 JSON；default 为 "[]"；
#   4. render_backend_env：注册表开关显式 true/false + 合并/重启头注释；
#      双品牌开关互补（验收①产物断言的数据源）；
#   5. 注册表交叉核对：menu key ⊆ menuConfig.ts MenuKey（18 项）；
#      env flag ⊆ Settings 字段（pydantic model_fields 小写化比对）；
#   6. validate_brand_menu：真实品牌通过；注册表 menu key 漂移 → 报错；
#   7. rewrite_title：<title> 改写 + 幂等 + 缺标签报错。
# =============================================================================

import json
from pathlib import Path

import pytest

from scripts import build_oem
from scripts.build_oem import (
    BRANDS_DIR,
    MODULE_REGISTRY,
    BrandConfigError,
    load_brand,
    read_menu_keys,
    render_backend_env,
    render_frontend_env,
    rewrite_title,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

_COLORS_BLOCK = """colors:
  primary: "#111111"
  primary_hover: "#222222"
  primary_active: "#333333"
  primary_light: "#444444"
  primary_light_hover: "#555555"
  primary_soft: "#666666"
  primary_tint: "#777777"
  primary_tint_hover: "#888888"
  primary_dark_tint: "#999999"
  primary_dark_tint_hover: "#aaaaaa"
"""

_VALID_BRAND_YAML = (
    "id: testbrand\n"
    "product_name: TestBrand\n"
    + _COLORS_BLOCK
    + "modules:\n"
    "  agent_market: true\n"
    "  tool_market: false\n"
    "  knowledge_base: true\n"
)


def _write_brand(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "brand.yaml"
    path.write_text(content, encoding="utf-8")
    return path


# =============================================================================
# 1. 真实品牌配置合法
# =============================================================================


def test_load_default_brand_matches_current_visual_baseline():
    brand = load_brand(BRANDS_DIR / "default.yaml")
    assert brand.id == "default"
    assert brand.product_name == "工作流编排平台"
    assert brand.colors["primary"] == "#1976d2"
    assert brand.colors["primary_dark_tint_hover"] == "#213045"
    assert brand.modules == {"agent_market": True, "tool_market": True, "knowledge_base": True}
    assert brand.disabled_modules == []
    assert brand.disabled_panels == []


def test_load_acmeflow_brand_all_modules_disabled():
    brand = load_brand(BRANDS_DIR / "acmeflow.yaml")
    assert brand.id == "acmeflow"
    assert brand.product_name == "AcmeFlow"
    assert brand.colors["primary"] == "#7c3aed"
    assert brand.disabled_modules == ["agent_market", "knowledge_base", "tool_market"]
    assert brand.disabled_panels == ["agent_market", "knowledge_base", "tool_market"]


# =============================================================================
# 2. schema 强校验（构建期报错）
# =============================================================================


@pytest.mark.parametrize(
    "content,desc",
    [
        (_VALID_BRAND_YAML + "  ghost_module: true\n", "未知 module_id"),
        (_VALID_BRAND_YAML + "extra_top_key: 1\n", "未知顶层键"),
        (
            _VALID_BRAND_YAML.replace('  primary_tint: "#777777"\n', ""),
            "缺失颜色键",
        ),
        (
            _VALID_BRAND_YAML.replace('primary: "#111111"', 'primary: "111111"'),
            "非法色值（缺 #）",
        ),
        (
            _VALID_BRAND_YAML.replace("  agent_market: true\n", "  agent_market: \"yes\"\n"),
            "非布尔模块值",
        ),
        (_VALID_BRAND_YAML.replace("product_name: TestBrand", "product_name: 123"), "非字符串产品名"),
    ],
    ids=["unknown-module", "unknown-top-key", "missing-color", "bad-hex", "non-bool-module", "non-str-name"],
)
def test_load_brand_schema_violations(tmp_path, content, desc):
    with pytest.raises(BrandConfigError):
        load_brand(_write_brand(tmp_path, content))


# =============================================================================
# 3. 前端 VITE_OEM_* 注入（12 个）
# =============================================================================


def test_render_frontend_env_acmeflow_bakes_disabled_panels():
    env = render_frontend_env(load_brand(BRANDS_DIR / "acmeflow.yaml"))
    assert len(env) == 12
    assert env["VITE_OEM_PRODUCT_NAME"] == "AcmeFlow"
    assert env["VITE_OEM_PRIMARY_COLOR"] == "#7c3aed"
    assert env["VITE_OEM_PRIMARY_COLOR_DARK_TINT_HOVER"] == "#2d2248"
    assert json.loads(env["VITE_OEM_DISABLED_PANELS"]) == ["agent_market", "knowledge_base", "tool_market"]
    assert env["VITE_OEM_DISABLED_PANELS"] == '["agent_market","knowledge_base","tool_market"]'


def test_render_frontend_env_default_disables_nothing():
    env = render_frontend_env(load_brand(BRANDS_DIR / "default.yaml"))
    assert env["VITE_OEM_PRODUCT_NAME"] == "工作流编排平台"
    assert env["VITE_OEM_PRIMARY_COLOR"] == "#1976d2"
    assert env["VITE_OEM_DISABLED_PANELS"] == "[]"


# =============================================================================
# 4. 后端 backend.env（双品牌互补）
# =============================================================================


def test_render_backend_env_complementary_between_brands():
    default_env = render_backend_env(load_brand(BRANDS_DIR / "default.yaml"))
    acme_env = render_backend_env(load_brand(BRANDS_DIR / "acmeflow.yaml"))
    # 头注释：合并语义 + 重启提示（settings @lru_cache）
    assert "合并" in default_env and "重启" in default_env
    # default：三开关全 true
    assert "AGENT_MARKET_ENABLED=true" in default_env
    assert "TOOL_MARKET_ENABLED=true" in default_env
    assert "VECTOR_KB_MANAGEMENT_ENABLED=true" in default_env
    # acmeflow：三开关全 false（与 default 互补 → 验收①产物断言数据源）
    assert "AGENT_MARKET_ENABLED=false" in acme_env
    assert "TOOL_MARKET_ENABLED=false" in acme_env
    assert "VECTOR_KB_MANAGEMENT_ENABLED=false" in acme_env


# =============================================================================
# 5. 注册表交叉核对（防两侧漂移）
# =============================================================================


def test_module_registry_menu_keys_subset_of_menu_config():
    menu_keys = read_menu_keys()
    assert len(menu_keys) == 18
    for menu_key, _flag in MODULE_REGISTRY.values():
        assert menu_key in menu_keys


def test_module_registry_flags_subset_of_settings_fields():
    from orchestration_platform.configuration.settings import Settings

    fields = {name.lower() for name in Settings.model_fields}
    for _menu_key, flag in MODULE_REGISTRY.values():
        assert flag.lower() in fields


# =============================================================================
# 6. 构建期菜单校验
# =============================================================================


def test_validate_brand_menu_accepts_real_brands():
    menu_keys = read_menu_keys()
    for name in ("default.yaml", "acmeflow.yaml"):
        build_oem.validate_brand_menu(load_brand(BRANDS_DIR / name), menu_keys)


def test_validate_brand_menu_rejects_registry_drift(tmp_path, monkeypatch):
    # 注册表 menu key 不在 menuConfig.ts → 报错（防注册表与前端菜单漂移）
    monkeypatch.setattr(
        build_oem,
        "MODULE_REGISTRY",
        {"agent_market": ("nonexistent_panel", "AGENT_MARKET_ENABLED")},
    )
    drift_yaml = (
        "id: drift\n" "product_name: DriftBrand\n" + _COLORS_BLOCK + "modules:\n" "  agent_market: false\n"
    )
    brand = load_brand(_write_brand(tmp_path, drift_yaml))
    with pytest.raises(BrandConfigError, match="nonexistent_panel"):
        build_oem.validate_brand_menu(brand, read_menu_keys())


# =============================================================================
# 7. <title> 改写
# =============================================================================


def test_rewrite_title_replaces_once_and_is_idempotent(tmp_path):
    index = tmp_path / "index.html"
    index.write_text(
        "<html><head><title>工作流编排平台</title></head></html>",
        encoding="utf-8",
    )
    rewrite_title(index, "AcmeFlow")
    text = index.read_text(encoding="utf-8")
    assert "<title>AcmeFlow</title>" in text
    assert "工作流编排平台" not in text
    rewrite_title(index, "AcmeFlow")  # 幂等：不产生第二个 <title>
    assert index.read_text(encoding="utf-8").count("<title>") == 1


def test_rewrite_title_requires_title_tag(tmp_path):
    missing = tmp_path / "no_title.html"
    missing.write_text("<html><head></head></html>", encoding="utf-8")
    with pytest.raises(BrandConfigError):
        rewrite_title(missing, "AcmeFlow")
