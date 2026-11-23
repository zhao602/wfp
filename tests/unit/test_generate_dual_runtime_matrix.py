# =============================================================================
# 矩阵生成一致性测试（tests/unit/test_generate_dual_runtime_matrix.py，B8e · A5）
#
# 覆盖验收：roadmap-v3 A5「能力表由测试生成，文档与代码一致」——
#   生成区块 ↔ 提交版 docs/langgraph-equivalence-matrix.md 逐字节一致，
#   测试数据（13 例）或文档任一侧漂移即红。
# 口径：
#   - importlib 按路径载入 scripts/generate_dual_runtime_matrix.py
#     （main 有 __main__ 守卫，import 零副作用）；
#   - 脚本不依赖 langgraph（仅 GraphCompiler 编译推导拓扑），本测试
#     在 langgraph 缺位环境同样成立。
# =============================================================================

import importlib.util
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GENERATOR_PATH = _REPO_ROOT / "scripts" / "generate_dual_runtime_matrix.py"


def _load_generator():
    spec = importlib.util.spec_from_file_location(
        "generate_dual_runtime_matrix", _GENERATOR_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_matrix_table_matches_committed_doc():
    """生成区块与提交版矩阵文档逐字节一致（表与测试一致门禁）。"""
    generator = _load_generator()
    block = generator.build_generated_block()
    doc_text = generator.DOC_PATH.read_text(encoding="utf-8")
    segment = f"{generator.BEGIN_MARKER}\n{block}\n{generator.END_MARKER}"
    assert segment in doc_text, (
        "docs/langgraph-equivalence-matrix.md 生成区块与测试数据不一致："
        "重跑 scripts/generate_dual_runtime_matrix.py 刷新后提交"
    )
