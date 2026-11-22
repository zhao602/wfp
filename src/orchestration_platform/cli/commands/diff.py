# =============================================================================
# diff 命令骨架（cli/commands/diff.py）
#
# 职责（arch_review_v2 5.5，CHG-19；与 G9 并行开发、串行合入 6.3）：
#   双侧解析编译 → 调用语义 diff 引擎（CHG-16 接口预留，G9 产出，
#   落点 workflow/semantic_diff/）→ 三类变更报告（文本/JSON）。
#
# exit code：双侧任一解析失败 → 2（加载异常）；编译/校验失败 → 1；
# 引擎未合入（ImportError）→ 1（执行失败，骨架形态明确提示）；
# 正常产出报告 → 0（diff 有变更也是 0，信息非错误）。
#
# 接口对齐：SemanticDiffReport{changes: [DiffChange{kind, target, detail}],
# summary}（6.1 契约；本骨架按该契约消费，6.3 合入后功能完整）。
# =============================================================================

from typing import Any, Dict, List, Tuple

from orchestration_platform.cli.commands import (
    WorkflowInvalidError,
    WorkflowLoadError,
    compile_definition,
    parse_workflow_text,
    read_workflow_file,
)
from orchestration_platform.cli.output import (
    EXIT_FAIL,
    EXIT_LOAD_ERROR,
    EXIT_OK,
    emit_error,
    emit_json,
    emit_text,
)
from orchestration_platform.engine.abstractions.compiled_workflow import CompiledWorkflow


def _load_compiled_side(path: str) -> Tuple[str, CompiledWorkflow]:
    """装载单侧：读取 → 解析（失败 2）→ 编译（失败 1）。返回 (路径, 编译产物)。"""
    import asyncio

    text = read_workflow_file(path)
    definition = parse_workflow_text(text)
    compiled = asyncio.run(compile_definition(definition))
    return path, compiled


def _import_semantic_diff_engine():
    """懒导入语义 diff 引擎（G9/6.1 落点 workflow/semantic_diff/）。

    未合入 → ImportError，由调用方降级为骨架提示 + exit 1；
    引擎重依赖不在模块顶层引入，保持 CLI 启动轻量。
    """
    from orchestration_platform.workflow.semantic_diff.engine import SemanticDiffEngine

    return SemanticDiffEngine


def _report_to_payload(report: Any) -> Dict[str, Any]:
    """SemanticDiffReport → JSON 可序列化载荷（按 6.1 契约字段消费）。"""
    if hasattr(report, "model_dump"):
        return report.model_dump(mode="json")
    return {"summary": str(report), "changes": []}


def _print_text_report(base_path: str, head_path: str, report: Any) -> None:
    """人读三类变更报告：变更清单 + 汇总。"""
    changes: List[Any] = list(getattr(report, "changes", []) or [])
    summary = getattr(report, "summary", "")
    emit_text(f"对比：{base_path}（基准） vs {head_path}（对比）")
    if not changes:
        emit_text("两份工作流定义语义等价（无三类变更）")
    else:
        for change in changes:
            kind = getattr(change, "kind", "")
            target = getattr(change, "target", "")
            detail = getattr(change, "detail", "")
            emit_text(f"- [{kind}] {target}：{detail}")
    if summary:
        emit_text(f"汇总：{summary}")


def run_diff(args) -> int:
    """diff 命令入口：双侧解析编译 → 语义 diff 引擎 → 三类变更报告。"""
    try:
        base_path, base_compiled = _load_compiled_side(args.base)
        head_path, head_compiled = _load_compiled_side(args.head)
    except WorkflowLoadError as error:
        emit_error(str(error))
        return EXIT_LOAD_ERROR
    except WorkflowInvalidError as error:
        emit_error(str(error))
        return EXIT_FAIL

    # 懒导入引擎：未合入（骨架形态）→ 明确提示 + exit 1（6.3 串行合入）
    try:
        engine_cls = _import_semantic_diff_engine()
    except ImportError:
        emit_error(
            "语义 diff 引擎未合入（G9/任务 6.1 产出、6.3 串行合入后可用），"
            "当前 diff 为骨架模式，仅完成双侧解析与编译校验"
        )
        return EXIT_FAIL

    engine = engine_cls()
    report = engine.diff(base_compiled, head_compiled)

    if getattr(args, "json", False):
        payload = _report_to_payload(report)
        payload["base"] = base_path
        payload["head"] = head_path
        emit_json(payload)
    else:
        _print_text_report(base_path, head_path, report)
    return EXIT_OK
