# =============================================================================
# engine 域 import 隔离门禁（scripts/check_engine_imports.py）
#
# 职责（roadmap M4 B10 / design D5）：AST 语法级扫描 src/orchestration_platform/
# engine/ 全部 .py（ast.walk 递归，含函数体内惰性 import），禁止 6 前缀：
#   orchestration_platform.{api, configuration, governance, application,
#   worker, cli}
# 引擎域配置经 EngineSettings 构造注入（engine/abstractions/engine_config.py），
# 宿主侧映射单点 application/engine_wiring.py——引擎组件不得反向感知宿主层。
# 例外登记：persistence 不禁（scheduler 引 persistence.abstractions 为
# 登记接受依赖，design D5）。
# 守护分工：
#   1. 本脚本——CI 独立步（ci.yml backend job，pytest 前插）；
#   2. tests/unit/test_engine_import_isolation.py——pytest 内同逻辑断言
#      （经 importlib 引用本模块，单一逻辑源防漂移）；
#   3. scripts/run_local_ci.py——本地镜像调用。
#
# 用法：python scripts/check_engine_imports.py
# 退出码：0 = 零违规；1 = 存在违规（逐行输出 相对路径:行:模块）。
# =============================================================================

import ast
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src" / "orchestration_platform"
ENGINE_DIR = SRC_ROOT / "engine"

# 禁令面（design D5 定稿）：api/configuration 为 B10 核心禁令；
# governance/application/worker/cli 现零 import，扩禁防回归。
BANNED_PREFIXES: tuple[str, ...] = (
    "orchestration_platform.api",
    "orchestration_platform.configuration",
    "orchestration_platform.governance",
    "orchestration_platform.application",
    "orchestration_platform.worker",
    "orchestration_platform.cli",
)


def is_banned(module: str) -> bool:
    """判断绝对模块名是否落在禁令面前缀下（含子模块）。"""
    for prefix in BANNED_PREFIXES:
        if module == prefix or module.startswith(prefix + "."):
            return True
    return False


def _package_of(file_path: Path) -> tuple[str, ...]:
    """计算文件所属包的绝对点分元组（__init__.py 视为包自身）。"""
    rel_parts = file_path.relative_to(SRC_ROOT).with_suffix("").parts
    if rel_parts and rel_parts[-1] == "__init__":
        rel_parts = rel_parts[:-1]
    return ("orchestration_platform",) + rel_parts[:-1]


def resolve_import_from(file_path: Path, node: ast.ImportFrom) -> str:
    """将 ImportFrom 解析为绝对目标模块（含相对导入 level 解析）。

    level ≥ 1 时按文件所在包逐级上溯；解析越界（病态 level）返回
    "orchestration_platform.__unresolvable__" 以落入禁令面（防御性报错，
    不静默放行）。
    """
    if node.level == 0:
        return node.module or ""
    pkg = _package_of(file_path)
    strip = node.level - 1
    if strip > len(pkg):
        return "orchestration_platform.__unresolvable__"
    base = pkg[: len(pkg) - strip]
    if node.module:
        return ".".join(base + (node.module,))
    return ".".join(base)


def collect_engine_files() -> list[Path]:
    """收集 engine/ 全部 .py（递归，排序稳定输出）。"""
    return sorted(ENGINE_DIR.rglob("*.py"))


def collect_violations() -> tuple[list[Path], list[str]]:
    """扫描 engine/ 全部文件的 import 语句，返回 (文件清单, 违规描述清单)。

    违规描述格式：相对路径:行号: import 语句摘要
    """
    violations: list[str] = []
    files = collect_engine_files()
    for py_file in files:
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if is_banned(alias.name):
                        rel = py_file.relative_to(PROJECT_ROOT)
                        violations.append(
                            f"{rel}:{node.lineno}: import {alias.name}"
                        )
            elif isinstance(node, ast.ImportFrom):
                target = resolve_import_from(py_file, node)
                if target and is_banned(target):
                    rel = py_file.relative_to(PROJECT_ROOT)
                    names = ", ".join(alias.name for alias in node.names)
                    violations.append(
                        f"{rel}:{node.lineno}: from {target} import {names}"
                    )
    return files, violations


def main() -> int:
    files, violations = collect_violations()
    if violations:
        print(f"engine import isolation: FAIL（{len(violations)} 处违规，"
              f"扫描 {len(files)} 文件）")
        for line in violations:
            print(f"  {line}")
        print("禁令面：orchestration_platform.{api, configuration, governance, "
              "application, worker, cli}（design D5；persistence 例外登记）")
        return 1
    print(f"engine import isolation: PASS（扫描 {len(files)} 文件，"
          f"6 前缀零违规）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
