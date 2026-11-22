# =============================================================================
# validate 命令（cli/commands/validate.py）
#
# 职责（arch_review_v2 5.2，CHG-18；AC-04/AC-06；INV-03 零新造校验逻辑）：
#   复用三层校验链全量只读：YamlWorkflowParser → GraphTopologyValidator →
#   NodeConfigurationValidator → GraphCompiler；
#   文件/目录模式（目录递归 *.yaml，E18 口径），逐文件 PASS/FAIL/SKIP/ERROR
#   + issue 列表（文件/节点/消息）。
#
# exit code：任一加载异常 → 2；否则任一校验失败 → 1；否则 0。
# 范例库伴随 Agent 清单（agents 键 + 无 spec_version）→ SKIP，不计失败。
# =============================================================================

from typing import Any, Dict, List

from orchestration_platform.cli.commands import (
    WorkflowInvalidError,
    WorkflowLoadError,
    compile_definition,
    is_agent_manifest,
    parse_workflow_text,
    read_workflow_file,
    resolve_target_files,
    validate_definition,
)
from orchestration_platform.cli.output import (
    EXIT_FAIL,
    EXIT_LOAD_ERROR,
    EXIT_OK,
    emit_error,
    emit_json,
    emit_text,
)
from orchestration_platform.domain.models.common import ValidationIssue
from orchestration_platform.domain.models.enums import Severity

# 逐文件结果状态（JSON/文本共用口径）
_STATUS_PASS = "pass"
_STATUS_FAIL = "fail"
_STATUS_SKIP = "skip"
_STATUS_LOAD_ERROR = "load_error"


def _issue_to_dict(issue: ValidationIssue) -> Dict[str, Any]:
    """ValidationIssue → JSON 可序列化字典（机器读口径）。"""
    payload: Dict[str, Any] = {
        "code": issue.code,
        "severity": issue.severity.value,
        "message": issue.message,
    }
    if issue.node_id:
        payload["node_id"] = issue.node_id
    if issue.edge_id:
        payload["edge_id"] = issue.edge_id
    if issue.location is not None:
        payload["line"] = issue.location.line
        payload["column"] = issue.location.column
    return payload


async def _validate_single_file(path: str) -> Dict[str, Any]:
    """校验单个 YAML 文件，返回统一结果记录（status/message/issues）。"""
    record: Dict[str, Any] = {"path": path, "status": _STATUS_FAIL, "issues": []}

    try:
        text = read_workflow_file(path)
    except WorkflowLoadError as error:
        record["status"] = _STATUS_LOAD_ERROR
        record["message"] = str(error)
        return record

    # 伴随 Agent 清单分流：非工作流定义，SKIP（E18 范例库口径）
    if is_agent_manifest(text):
        record["status"] = _STATUS_SKIP
        record["message"] = "非工作流定义（Agent 清单），跳过"
        return record

    try:
        definition = parse_workflow_text(text)
    except WorkflowLoadError as error:
        record["status"] = _STATUS_LOAD_ERROR
        record["message"] = str(error)
        return record
    except WorkflowInvalidError as error:
        record["message"] = str(error)
        return record

    issues = validate_definition(definition)
    errors = [issue for issue in issues if issue.severity == Severity.ERROR]
    warnings = [issue for issue in issues if issue.severity != Severity.ERROR]
    record["issues"] = [_issue_to_dict(issue) for issue in issues]
    if errors:
        record["message"] = f"校验未通过（{len(errors)} 个 ERROR 级问题）"
        return record

    try:
        await compile_definition(definition)
    except WorkflowInvalidError as error:
        record["message"] = str(error)
        return record

    record["status"] = _STATUS_PASS
    record["message"] = (
        f"校验通过（{len(warnings)} 个非 ERROR 级提示）" if warnings else "校验通过"
    )
    return record


async def _validate_files(paths: List[str]) -> List[Dict[str, Any]]:
    """逐文件串行校验（确定性顺序，保证输出可复现）。"""
    results: List[Dict[str, Any]] = []
    for path in paths:
        results.append(await _validate_single_file(path))
    return results


def _summary_of(results: List[Dict[str, Any]]) -> Dict[str, int]:
    return {
        "total": len(results),
        "passed": sum(1 for r in results if r["status"] == _STATUS_PASS),
        "failed": sum(1 for r in results if r["status"] == _STATUS_FAIL),
        "skipped": sum(1 for r in results if r["status"] == _STATUS_SKIP),
        "load_errors": sum(1 for r in results if r["status"] == _STATUS_LOAD_ERROR),
    }


def _print_text_report(results: List[Dict[str, Any]]) -> None:
    """人读逐文件报告 + 汇总行。"""
    prefix = {
        _STATUS_PASS: "PASS ",
        _STATUS_FAIL: "FAIL ",
        _STATUS_SKIP: "SKIP ",
        _STATUS_LOAD_ERROR: "ERROR",
    }
    for record in results:
        emit_text(f"{prefix[record['status']]} {record['path']}")
        if record["status"] != _STATUS_PASS and record.get("message"):
            emit_text(f"      {record['message']}")
        for issue in record.get("issues", []):
            severity = issue.get("severity", "error")
            location_parts: List[str] = []
            if issue.get("node_id"):
                location_parts.append(f"节点 {issue['node_id']}")
            if issue.get("edge_id"):
                location_parts.append(f"连线 {issue['edge_id']}")
            if issue.get("line") is not None:
                location_parts.append(f"行 {issue['line']} 列 {issue['column']}")
            suffix = f"（{'，'.join(location_parts)}）" if location_parts else ""
            emit_text(
                f"      - [{severity}] {issue.get('code', '')}："
                f"{issue.get('message', '')}{suffix}"
            )
    summary = _summary_of(results)
    emit_text(
        f"汇总：共 {summary['total']} 个文件 —— {summary['passed']} 通过 / "
        f"{summary['failed']} 失败 / {summary['skipped']} 跳过 / "
        f"{summary['load_errors']} 加载异常"
    )


def _exit_code_of(summary: Dict[str, int]) -> int:
    """退出码优先级：加载异常(2) > 校验失败(1) > 成功(0)。"""
    if summary["load_errors"] > 0:
        return EXIT_LOAD_ERROR
    if summary["failed"] > 0:
        return EXIT_FAIL
    return EXIT_OK


def run_validate(args) -> int:
    """validate 命令入口：解析目标 → 逐文件全链校验 → 报告 + exit code。"""
    import asyncio

    try:
        paths = resolve_target_files(args.target)
    except WorkflowLoadError as error:
        emit_error(str(error))
        return EXIT_LOAD_ERROR

    try:
        results = asyncio.run(_validate_files(paths))
    except WorkflowLoadError as error:  # pragma: no cover - 防御路径
        emit_error(str(error))
        return EXIT_LOAD_ERROR

    if getattr(args, "json", False):
        emit_json({"files": results, "summary": _summary_of(results)})
    else:
        _print_text_report(results)

    return _exit_code_of(_summary_of(results))
