# =============================================================================
# 架构规范单测：langgraph 隔离格局（tests/unit/test_langgraph_isolation.py）
#
# 职责：以 AST 语法级扫描固化"业务代码禁止 import langgraph、唯一例外
#       engine/adapter/"隔离约束（C1 裁决保留的物理锚点守护）。
# 覆盖验收：SR-203b / SR-101 / SR-201（接替被删 tests/verify_t8_adapter.py 的守护职责）。
# 三重守护分工：
#   1. 本单测（AST 语法级）——精确解析 import 语句，正确处理多行导入与字符串字面量；
#   2. test_no_direct_framework_dependency.py（文本扫描级）——逐行文本匹配兜底；
#   3. grep 运维命令（SR-303）——CI/运维侧人工复核。
# =============================================================================

import ast
from pathlib import Path
from typing import List

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SRC_ROOT = REPO_ROOT / "src" / "orchestration_platform"
ADAPTER_DIR = SRC_ROOT / "engine" / "adapter"


def _is_in_adapter(py_file: Path) -> bool:
    """判断文件是否位于 engine/adapter/ 隔离层内。"""
    return ADAPTER_DIR in py_file.resolve().parents or py_file.resolve().parent == ADAPTER_DIR


def _collect_source_files() -> List[Path]:
    """收集 adapter 外的全部源码文件。"""
    return [f for f in sorted(SRC_ROOT.rglob("*.py")) if not _is_in_adapter(f)]


class TestLangGraphIsolation:
    """架构规范：langgraph 导入仅允许出现在 engine/adapter/ 隔离层内。"""

    def test_langgraph_imports_only_in_adapter(self):
        """AST 语法级断言：adapter 外零 langgraph 导入（含多行导入精确解析）。"""
        violations = []
        for py_file in _collect_source_files():
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                statements = []
                if isinstance(node, ast.Import):
                    statements = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    statements = [node.module]
                for mod in statements:
                    if mod.split(".")[0] == "langgraph":
                        violations.append(
                            f"{py_file}:{node.lineno}: {'import ' + mod}"
                        )
        assert len(violations) == 0, (
            f"发现 {len(violations)} 处违规 langgraph 导入（仅允许 engine/adapter/）:\n"
            + "\n".join(violations)
        )

    def test_adapter_anchor_files_exist(self):
        """C1 物理锚点守护：adapter 隔离层两个锚点文件必须存在。"""
        anchor_files = [
            ADAPTER_DIR / "langgraph_adapter.py",
            ADAPTER_DIR / "langgraph_state_mapping.py",
        ]
        for anchor in anchor_files:
            assert anchor.exists(), f"langgraph 隔离锚点文件缺失: {anchor}"

    def test_no_langgraph_string_import_in_dynamic_code(self):
        """动态导入防线：adapter 外禁止以字符串形式动态导入 langgraph。"""
        violations = []
        for py_file in _collect_source_files():
            text = py_file.read_text(encoding="utf-8")
            tree = ast.parse(text)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                func_name = ""
                if isinstance(func, ast.Attribute):
                    func_name = func.attr
                elif isinstance(func, ast.Name):
                    func_name = func.id
                if func_name not in ("import_module", "__import__"):
                    continue
                for arg in node.args:
                    if (
                        isinstance(arg, ast.Constant)
                        and isinstance(arg.value, str)
                        and arg.value.split(".")[0] == "langgraph"
                    ):
                        violations.append(
                            f"{py_file}:{node.lineno}: 动态导入 {arg.value!r}"
                        )
            # 文本兜底：捕获非常规写法（如经变量拼接的字符串字面量）
            for line_num, line in enumerate(text.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if "import_module" in stripped and "langgraph" in stripped:
                    violations.append(f"{py_file}:{line_num}: {stripped}")
        assert len(violations) == 0, (
            f"发现 {len(violations)} 处违规动态 langgraph 导入:\n"
            + "\n".join(violations)
        )