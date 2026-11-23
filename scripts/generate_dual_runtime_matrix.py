# =============================================================================
# 双运行时等价矩阵生成器（scripts/generate_dual_runtime_matrix.py，B8e · A5）
#
# 职责：由 tests/integration/test_langgraph_equivalence_matrix.py（13 例）
#      生成 docs/langgraph-equivalence-matrix.md §1 主表（生成区块）并回写。
# 设计意图（roadmap-v3 A5「能力表由测试生成，文档与代码一致」）：
#   1. 单一事实源 = 矩阵测试：MATRIX_IN（8 例）+ _NA_CASES（4 例）+
#      E4 专项用例（源码正则提取，未命中即报错，不静默兜底）；
#   2. 拓扑自范例 YAML 推导（YamlWorkflowParser → GraphCompiler 编译后按
#      节点类型 + control_flow_kind 机械推导，不手填）；执行步数 =
#      len(expected_sequence)（触发序列长度，非节点数）；
#   3. 幂等：区块内容确定性（无时间戳/无环境信息）；--check 只比对不写
#      （一致性测试与 CI 口径）；未变化不写盘；
#   4. 首跑引导：文档无标记时定位手写表（「| # | 范例」表头行至
#      「① 错误传播链」脚注行，含）整段替换为标记区块。
# 纪律：
#   1. 脚本不 import langgraph、不执行 LangGraphAdapter（仅 GraphCompiler
#      编译推导拓扑，langgraph 在位与否不影响生成）；
#   2. 既有测试零修改——本脚本只读取测试模块。
# =============================================================================

import argparse
import asyncio
import importlib.util
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_DIR = _REPO_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from orchestration_platform.domain.models.enums import (  # noqa: E402
    ControlFlowKind,
    NodeType,
)
from orchestration_platform.engine.graph_compiler import GraphCompiler  # noqa: E402
from orchestration_platform.workflow.yaml_workflow_parser import (  # noqa: E402
    YamlWorkflowParser,
)

MATRIX_TEST_PATH = (
    _REPO_ROOT / "tests" / "integration" / "test_langgraph_equivalence_matrix.py"
)
DOC_PATH = _REPO_ROOT / "docs" / "langgraph-equivalence-matrix.md"

BEGIN_MARKER = (
    "<!-- 生成区块起：scripts/generate_dual_runtime_matrix.py 生成"
    "（源 tests/integration/test_langgraph_equivalence_matrix.py），"
    "勿手改；重跑幂等 -->"
)
END_MARKER = "<!-- 生成区块止 -->"

# IN 例备注文案（脚本所有，沿 B7 手写备注；随测试语义演进同步维护）
_IN_NOTES: Dict[str, str] = {
    "sequential_pipeline": "成功收敛",
    "conditional_loop_pipeline": "M2 默认回退 + M3 回边计数 + 终止退出",
    "reflection_iteration": (
        "失败态等价——双引擎同抛 `WF_LOOP_NOT_CONVERGED`（§3）"
    ),
    "drift_detection": (
        "受限求值器不支持 `is not` → 条件求值抛错落默认分支（双引擎同径）"
    ),
    "realtime_subscription_workflow": "事件分类路由（高优先级路径）",
    "feature_engineering": "data_transform 操作链（handle_nulls 确定性剔除）",
    "data_engineering_agent": "同上",
    "data_transform_pipeline": "同上",
}

# N/A 例补充文案（reason/硬边界子句自动组合，此处仅放补充段）
_NA_EXTRA_NOTES: Dict[str, str] = {
    "parallel_execution": (
        "B6 注记（2026-11-22）：该范例为串行化兼容形态（非严格扇出，编译警告 + "
        "单游标语义不变），严格扇出激活形态见 "
        "`examples/parallel_execution/parallel_fanout.yaml`"
        "（编译 14 例 + 执行 9 例覆盖），LangGraph 侧 N/A 结论不变"
    ),
    "supervisor_routing": "静态标注，不经矩阵执行",
}

_HARD_BOUNDARY_CLAUSE = "硬边界：`_validate_subset` 运行期拒绝 `WF_VALIDATION_FAILED`"


def _load_matrix_module() -> Any:
    """按文件路径加载矩阵测试模块（模块级纯数据构造，无测试执行副作用）。"""
    spec = importlib.util.spec_from_file_location(
        "test_langgraph_equivalence_matrix", MATRIX_TEST_PATH
    )
    if spec is None or spec.loader is None:
        raise SystemExit(f"无法加载矩阵测试模块: {MATRIX_TEST_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _extract_source_symbols(source: str) -> Dict[str, str]:
    """自测试源码提取 E4 专项用例与注入错误码（fail loud，不静默兜底）。"""
    symbols: Dict[str, str] = {}
    match = re.search(
        r"def test_e4_error_code_parity[\s\S]*?case_id == \"([^\"]+)\"", source
    )
    if match is None:
        raise SystemExit("E4 专项用例提取失败：test_e4_error_code_parity 内未匹配 case_id")
    symbols["e4_case_id"] = match.group(1)
    match = re.search(r"injected_code = \"([^\"]+)\"", source)
    if match is None:
        raise SystemExit("E4 注入错误码提取失败：未匹配 injected_code 赋值")
    symbols["injected_code"] = match.group(1)
    return symbols


def _derive_topology(examples_root: Path, example_file: str) -> str:
    """自范例 YAML 机械推导子集拓扑标签（编译产物节点类型 + control_flow_kind）。"""
    yaml_path = examples_root / example_file
    definition = YamlWorkflowParser().parse(yaml_path.read_text(encoding="utf-8"))
    compiled = asyncio.run(GraphCompiler().compile(definition))
    has_branch = False
    has_loop = False
    has_transform = False
    for node in compiled.nodes:
        if node.node_type == NodeType.CONTROL_FLOW:
            kind = getattr(node.configuration, "control_flow_kind", None)
            if kind == ControlFlowKind.CONDITIONAL_BRANCH:
                has_branch = True
            elif kind == ControlFlowKind.LOOP:
                has_loop = True
        elif node.node_type == NodeType.DATA_TRANSFORM:
            has_transform = True
    if has_branch and has_loop:
        label = "条件 + 单层循环"
    elif has_branch:
        label = "条件分支"
    elif has_loop:
        label = "单层循环"
    else:
        label = "顺序"
    if has_transform:
        label += " + 数据变换"
    return label


def build_generated_block() -> str:
    """生成矩阵主表区块（标记间内容，不含标记本身；确定性输出）。"""
    module = _load_matrix_module()
    source = MATRIX_TEST_PATH.read_text(encoding="utf-8")
    symbols = _extract_source_symbols(source)
    examples_root: Path = module.EXAMPLES_ROOT
    matrix_in: List[Any] = list(module.MATRIX_IN)
    na_cases: List[Dict[str, Any]] = list(module._NA_CASES)

    lines: List[str] = [
        "| # | 范例 | 拓扑（子集内） | 执行步数 | E1 | E2 | E3 | E4 | E5 | 备注 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for index, case in enumerate(matrix_in, start=1):
        topology = _derive_topology(examples_root, case.example_file)
        e3_cell = (
            "✓（失败态：错误码对比）"
            if case.expected_error_code is not None
            else "✓"
        )
        e4_cell = (
            "✓（专项注入）" if case.case_id == symbols["e4_case_id"] else "✓①"
        )
        note = _IN_NOTES.get(case.case_id, "")
        lines.append(
            f"| {index} | `{case.case_id}` | {topology} "
            f"| {len(case.expected_sequence)} "
            f"| ✓ | ✓ | {e3_cell} | {e4_cell} | ✓ | {note} |"
        )
    for offset, na_case in enumerate(na_cases, start=len(matrix_in) + 1):
        note = str(na_case["reason"])
        if na_case["hard_boundary"]:
            note += f"；{_HARD_BOUNDARY_CLAUSE}"
        extra = _NA_EXTRA_NOTES.get(str(na_case["case_id"]), "")
        if extra:
            note += f"；{extra}"
        lines.append(
            f"| {offset} | `{na_case['case_id']}` | — | — "
            f"| N/A | N/A | N/A | N/A | N/A | {note} |"
        )
    lines.append("")
    lines.append(
        "① 错误传播链为节点类型无关的同一映射（executor 失败 → "
        "`NodeExecutionError` 同码回退链），专项注入测试于 "
        f"`{symbols['e4_case_id']}` 执行（`success=False` + "
        f"`{symbols['injected_code']}`）。"
    )
    return "\n".join(lines)


def _splice(doc_text: str, block: str) -> str:
    """将区块写回文档：有标记按标记替换；无标记走首跑引导（手写表整段替换）。"""
    lines = doc_text.split("\n")
    begin_index = next(
        (i for i, line in enumerate(lines) if line.strip() == BEGIN_MARKER), None
    )
    if begin_index is not None:
        end_index = next(
            (
                i
                for i in range(begin_index + 1, len(lines))
                if lines[i].strip() == END_MARKER
            ),
            None,
        )
        if end_index is None:
            raise SystemExit("生成区块止标记缺失：BEGIN 后未找到 END 标记")
        new_lines = (
            lines[: begin_index + 1] + block.split("\n") + lines[end_index:]
        )
        return "\n".join(new_lines)
    header_index = next(
        (i for i, line in enumerate(lines) if line.startswith("| # | 范例")), None
    )
    if header_index is None:
        raise SystemExit("首跑引导失败：未定位手写表头（「| # | 范例」）")
    footnote_index = next(
        (
            i
            for i in range(header_index, len(lines))
            if lines[i].startswith("① 错误传播链")
        ),
        None,
    )
    if footnote_index is None:
        raise SystemExit("首跑引导失败：未定位手写脚注（「① 错误传播链」）")
    new_lines = (
        lines[:header_index]
        + [BEGIN_MARKER]
        + block.split("\n")
        + [END_MARKER]
        + lines[footnote_index + 1 :]
    )
    return "\n".join(new_lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="生成/校验 docs/langgraph-equivalence-matrix.md §1 矩阵主表"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="只比对不写回（不一致时 exit 1，供一致性测试与 CI 使用）",
    )
    args = parser.parse_args()

    block = build_generated_block()
    doc_text = DOC_PATH.read_text(encoding="utf-8")
    new_text = _splice(doc_text, block)
    if new_text == doc_text:
        print("矩阵主表已一致（零变化）")
        return 0
    if args.check:
        print("矩阵主表与测试不一致（--check 模式不写回，exit 1）")
        return 1
    DOC_PATH.write_text(new_text, encoding="utf-8", newline="\n")
    print(f"矩阵主表已刷新: {DOC_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
