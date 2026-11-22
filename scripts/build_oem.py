# =============================================================================
# OEM 品牌构建脚本（scripts/build_oem.py，roadmap_b5_oem_whitelabel）
#
# 目标：读取 oem/brands/*.yaml，单次运行产出全部品牌部署产物：
#   oem/dist/<brand>/web/         前端静态产物（vite 构建；品牌色/产品名/
#                                 禁用面板清单经 VITE_OEM_* 构建期烘焙进 bundle）
#   oem/dist/<brand>/backend.env  后端模块开关（与基础 .env 合并部署）
#   oem/dist/manifest.json        构建清单（无时间戳，可复现）
#
# 关键设计（design §0-P2/§8）：
#   - 直调 vite（node node_modules/vite/bin/vite.js build），不经
#     `npm run build`——其 tsc 步骤因 13 个既有测试文件类型错误 exit 2，
#     vite 不可达（T1 实测）；typecheck 门禁维持 13 零新增口径，不在本批次修；
#   - 后端开关写 backend.env（settings 为 pydantic BaseSettings，字段名
#     大写即 env 名；@lru_cache 缓存——改后须重启进程）；
#   - 模块注册表 MODULE_REGISTRY 为 module_id→(menu key, env flag) 单一映射，
#     交叉核对见 tests/unit/test_build_oem.py（防两侧漂移）。
#
# 用法：
#   python scripts/build_oem.py                  # 构建全部品牌
#   python scripts/build_oem.py --brand acmeflow # 仅构建指定品牌
#
# 退出码：0 = 全部品牌构建成功；1 = 配置校验/构建失败。
# =============================================================================

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
BRANDS_DIR = REPO_ROOT / "oem" / "brands"
DIST_DIR = REPO_ROOT / "oem" / "dist"
WEB_DIR = REPO_ROOT / "web"
VITE_JS = WEB_DIR / "node_modules" / "vite" / "bin" / "vite.js"
MENU_CONFIG_TS = WEB_DIR / "src" / "admin" / "menuConfig.ts"

# 模块注册表：module_id → (menu key, env flag)
# （menu key 见 web/src/admin/menuConfig.ts；env flag 见 configuration/settings.py）
MODULE_REGISTRY: dict[str, tuple[str, str]] = {
    "agent_market": ("agent_market", "AGENT_MARKET_ENABLED"),
    "tool_market": ("tool_market", "TOOL_MARKET_ENABLED"),
    "knowledge_base": ("knowledge_base", "VECTOR_KB_MANAGEMENT_ENABLED"),
}

# 10 色阶（design §2；与 web/src/brand/brandConfig.ts 导出一一对应）
COLOR_KEYS: tuple[str, ...] = (
    "primary",
    "primary_hover",
    "primary_active",
    "primary_light",
    "primary_light_hover",
    "primary_soft",
    "primary_tint",
    "primary_tint_hover",
    "primary_dark_tint",
    "primary_dark_tint_hover",
)

HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
BRAND_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
MENU_KEY_UNION_RE = re.compile(r"export\s+type\s+MenuKey\s*=\s*([^;]+);")
TITLE_RE = re.compile(r"<title>[^<]*</title>")


class BrandConfigError(ValueError):
    """品牌配置/构建期校验失败（schema 见 design §2）。"""


@dataclass(frozen=True)
class BrandConfig:
    """品牌配置（load_brand 校验产物；字段语义见 design §2）。"""

    id: str
    product_name: str
    colors: dict[str, str]
    modules: dict[str, bool]

    @property
    def disabled_modules(self) -> list[str]:
        return sorted(mid for mid, enabled in self.modules.items() if not enabled)

    @property
    def disabled_panels(self) -> list[str]:
        return sorted(MODULE_REGISTRY[mid][0] for mid in self.disabled_modules)


def load_brand(path: Path) -> BrandConfig:
    """加载并校验品牌配置；任何 schema 违规抛 BrandConfigError（构建期强校验）。"""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise BrandConfigError(f"{path.name}: 顶层必须是映射，实际 {type(raw).__name__}")

    allowed = {"id", "product_name", "colors", "modules"}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise BrandConfigError(f"{path.name}: 未知顶层键 {unknown}（合法：{sorted(allowed)}）")
    missing = sorted(allowed - set(raw))
    if missing:
        raise BrandConfigError(f"{path.name}: 缺少必填顶层键 {missing}")

    brand_id = raw["id"]
    if not isinstance(brand_id, str) or not BRAND_ID_RE.match(brand_id):
        raise BrandConfigError(f"{path.name}: id 必须是合法目录名（字母开头，仅字母/数字/_/-）")

    product_name = raw["product_name"]
    if not isinstance(product_name, str) or not product_name.strip():
        raise BrandConfigError(f"{path.name}: product_name 必须是非空字符串")

    colors = raw["colors"]
    if not isinstance(colors, dict):
        raise BrandConfigError(f"{path.name}: colors 必须是映射")
    unknown_colors = sorted(set(colors) - set(COLOR_KEYS))
    if unknown_colors:
        raise BrandConfigError(f"{path.name}: 未知颜色键 {unknown_colors}（合法：{list(COLOR_KEYS)}）")
    missing_colors = sorted(set(COLOR_KEYS) - set(colors))
    if missing_colors:
        raise BrandConfigError(f"{path.name}: 缺少颜色键 {missing_colors}")
    for key, value in colors.items():
        if not isinstance(value, str) or not HEX_COLOR_RE.match(value):
            raise BrandConfigError(f"{path.name}: colors.{key} 必须是 #rrggbb 十六进制色，实际 {value!r}")

    modules = raw["modules"]
    if not isinstance(modules, dict):
        raise BrandConfigError(f"{path.name}: modules 必须是映射")
    unknown_modules = sorted(set(modules) - set(MODULE_REGISTRY))
    if unknown_modules:
        raise BrandConfigError(
            f"{path.name}: 未知 module_id {unknown_modules}（合法：{sorted(MODULE_REGISTRY)}）"
        )
    for mid, enabled in modules.items():
        if not isinstance(enabled, bool):
            raise BrandConfigError(f"{path.name}: modules.{mid} 必须是布尔值，实际 {enabled!r}")

    return BrandConfig(id=brand_id, product_name=product_name, colors=dict(colors), modules=dict(modules))


def read_menu_keys(menu_config_path: Path = MENU_CONFIG_TS) -> set[str]:
    """从 menuConfig.ts 的 MenuKey 联合类型提取全部合法面板 key（构建期校验用）。"""
    text = menu_config_path.read_text(encoding="utf-8")
    match = MENU_KEY_UNION_RE.search(text)
    if match is None:
        raise BrandConfigError(f"{menu_config_path}: 未找到 MenuKey 联合类型定义（文件漂移）")
    keys = set(re.findall(r"'([A-Za-z0-9_]+)'", match.group(1)))
    if not keys:
        raise BrandConfigError(f"{menu_config_path}: MenuKey 联合类型提取结果为空（文件漂移）")
    return keys


def validate_brand_menu(brand: BrandConfig, menu_keys: set[str]) -> None:
    """构建期校验：注册表 menu key 存在于 menuConfig.ts；禁用后剩余菜单 ≥1。

    剩余 ≥1 保护 hashRouter 的 DEFAULT_ADMIN_PANEL = MENU_ITEMS[0].key
    （menuConfig.ts 过滤出口为空时回落全量，此处为构建期同口径双保险）。
    """
    for mid in brand.disabled_modules:
        menu_key = MODULE_REGISTRY[mid][0]
        if menu_key not in menu_keys:
            raise BrandConfigError(
                f"品牌 {brand.id}: 模块 {mid} 的 menu key {menu_key!r} 不在 menuConfig.ts "
                f"MenuKey 中（注册表与前端菜单漂移）"
            )
    remaining = menu_keys - set(brand.disabled_panels)
    if not remaining:
        raise BrandConfigError(f"品牌 {brand.id}: 禁用面板后菜单为空（剩余必须 ≥1）")


def render_frontend_env(brand: BrandConfig) -> dict[str, str]:
    """渲染前端构建注入的 12 个 VITE_OEM_* 环境变量（brandConfig.ts 消费）。"""
    return {
        "VITE_OEM_PRODUCT_NAME": brand.product_name,
        "VITE_OEM_PRIMARY_COLOR": brand.colors["primary"],
        "VITE_OEM_PRIMARY_COLOR_HOVER": brand.colors["primary_hover"],
        "VITE_OEM_PRIMARY_COLOR_ACTIVE": brand.colors["primary_active"],
        "VITE_OEM_PRIMARY_COLOR_LIGHT": brand.colors["primary_light"],
        "VITE_OEM_PRIMARY_COLOR_LIGHT_HOVER": brand.colors["primary_light_hover"],
        "VITE_OEM_PRIMARY_COLOR_SOFT": brand.colors["primary_soft"],
        "VITE_OEM_PRIMARY_COLOR_TINT": brand.colors["primary_tint"],
        "VITE_OEM_PRIMARY_COLOR_TINT_HOVER": brand.colors["primary_tint_hover"],
        "VITE_OEM_PRIMARY_COLOR_DARK_TINT": brand.colors["primary_dark_tint"],
        "VITE_OEM_PRIMARY_COLOR_DARK_TINT_HOVER": brand.colors["primary_dark_tint_hover"],
        "VITE_OEM_DISABLED_PANELS": json.dumps(brand.disabled_panels, separators=(",", ":")),
    }


def render_backend_env(brand: BrandConfig) -> str:
    """渲染后端模块开关 env 文件（注册表开关逐项显式 true/false 行）。"""
    lines = [
        f"# OEM 品牌后端配置（brand: {brand.id}，scripts/build_oem.py 生成，roadmap B5）",
        "# 部署时与基础 .env 合并；修改后须重启进程（settings @lru_cache 缓存）。",
    ]
    for mid in sorted(MODULE_REGISTRY):
        flag = MODULE_REGISTRY[mid][1]
        enabled = brand.modules.get(mid, False)
        lines.append(f"{flag}={'true' if enabled else 'false'}")
    return "\n".join(lines) + "\n"


def rewrite_title(index_html: Path, product_name: str) -> None:
    """改写产物 index.html 的 <title>（源 index.html 零触碰；幂等）。"""
    text = index_html.read_text(encoding="utf-8")
    if TITLE_RE.search(text) is None:
        raise BrandConfigError(f"{index_html}: 未找到 <title> 标签，无法改写")
    new_title = f"<title>{product_name}</title>"
    index_html.write_text(TITLE_RE.sub(lambda _m: new_title, text, count=1), encoding="utf-8")


def build_brand(brand: BrandConfig) -> dict[str, Any]:
    """构建单品牌产物（清空 → vite 直调 → title 改写 → backend.env），返回 manifest 条目。"""
    validate_brand_menu(brand, read_menu_keys())

    out_dir = DIST_DIR / brand.id
    if out_dir.exists():
        shutil.rmtree(out_dir)
    web_out = out_dir / "web"
    web_out.mkdir(parents=True)

    node_exe = shutil.which("node")
    if node_exe is None:
        raise BrandConfigError("未找到 node 可执行文件（PATH）")
    if not VITE_JS.is_file():
        raise BrandConfigError(f"未找到 vite 入口：{VITE_JS}（web/ 依赖未安装？）")

    env = {**os.environ, **render_frontend_env(brand)}
    result = subprocess.run(
        [node_exe, str(VITE_JS), "build", "--outDir", str(web_out), "--emptyOutDir"],
        cwd=str(WEB_DIR),
        env=env,
    )
    if result.returncode != 0:
        raise BrandConfigError(f"品牌 {brand.id}: vite 构建失败（exit {result.returncode}）")

    index_html = web_out / "index.html"
    if not index_html.is_file():
        raise BrandConfigError(f"品牌 {brand.id}: 构建产物缺 index.html（{index_html}）")
    rewrite_title(index_html, brand.product_name)

    (out_dir / "backend.env").write_text(render_backend_env(brand), encoding="utf-8")

    return {
        "id": brand.id,
        "product_name": brand.product_name,
        "colors": dict(brand.colors),
        "modules": dict(brand.modules),
        "disabled_modules": brand.disabled_modules,
        "artifacts": {
            "web": f"oem/dist/{brand.id}/web",
            "backend_env": f"oem/dist/{brand.id}/backend.env",
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="build_oem",
        description="OEM 品牌产物构建（roadmap B5：单次运行产出全部品牌 web 静态产物 + backend.env + manifest）",
    )
    parser.add_argument("--brand", help="仅构建指定品牌 id（缺省构建 oem/brands/ 下全部）")
    args = parser.parse_args(argv)

    if not BRANDS_DIR.is_dir():
        print(f"[build_oem] 品牌配置目录不存在：{BRANDS_DIR}", file=sys.stderr)
        return 1
    brand_paths = sorted(BRANDS_DIR.glob("*.yaml"))
    if not brand_paths:
        print(f"[build_oem] {BRANDS_DIR} 下无 *.yaml 品牌配置", file=sys.stderr)
        return 1

    brands: list[BrandConfig] = []
    seen_ids: set[str] = set()
    try:
        for path in brand_paths:
            brand = load_brand(path)
            if args.brand and brand.id != args.brand:
                continue
            if brand.id in seen_ids:
                raise BrandConfigError(f"品牌 id 重复：{brand.id}（{path.name}）")
            seen_ids.add(brand.id)
            brands.append(brand)
    except BrandConfigError as error:
        print(f"[build_oem] {error}", file=sys.stderr)
        return 1

    if not brands:
        print(f"[build_oem] 未匹配到品牌（--brand {args.brand}）", file=sys.stderr)
        return 1

    entries: list[dict[str, Any]] = []
    for brand in brands:
        print(f"[build_oem] 构建品牌：{brand.id}（{brand.product_name}）")
        try:
            entries.append(build_brand(brand))
        except BrandConfigError as error:
            print(f"[build_oem] {error}", file=sys.stderr)
            return 1

    DIST_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path = DIST_DIR / "manifest.json"
    manifest_path.write_text(
        json.dumps({"brands": entries}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"[build_oem] 完成：{len(entries)} 个品牌 → {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
