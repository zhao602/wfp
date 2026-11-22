# =============================================================================
# run --local 命令（cli/commands/run_local.py）
#
# 职责（arch_review_v2 5.4，CHG-21；DC-08 接线互不影响）：
#   GraphCompiler + Scheduler + local_file 仓储单进程本地执行；
#   --checkpoint 可选启用（默认关闭）；不触达 MCP/LLM 外部服务，
#   节点级失败如实报告（NodeExecutionError → exit 1）。
#
# 执行器注册表复用生产装配 _build_executor_registry（INV-03 零新造逻辑，
# 全 12 执行器与 API 路由同源；私有导入为有意复用，6.3/评审口径 E13）。
#
# exit code：成功 0 / 执行失败 1（含校验失败与执行器装配失败）/
# 解析失败 2（任务 5.4 口径）。
# =============================================================================

import time
import uuid
from typing import Any, Dict

from orchestration_platform.cli.commands import (
    WorkflowInvalidError,
    WorkflowLoadError,
    compile_definition,
    parse_workflow_text,
    read_workflow_file,
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
from orchestration_platform.domain.models.enums import Severity


def _build_local_registry():
    """复用生产装配构造执行器注册表（懒导入路由模块，全 12 执行器同源）。"""
    from orchestration_platform.api.routers.execution_instance_router import (
        _build_executor_registry,
    )

    return _build_executor_registry()


async def _compile_and_execute(
    definition,
    registry,
    checkpoint_repository,
    instance_id: str,
    trace_id: str,
):
    """单事件循环内完成编译 + 调度执行（避免多次 asyncio.run 建环）。"""
    compiled = await compile_definition(definition)
    from orchestration_platform.engine.scheduler import Scheduler
    from orchestration_platform.domain.models.shared_state import SharedState

    scheduler = Scheduler(
        executor_registry=registry,
        checkpoint_repository=checkpoint_repository,
    )
    final_state = await scheduler.execute(
        compiled,
        SharedState(instance_id=instance_id),
        instance_id,
        trace_id,
    )
    return compiled, final_state


def _emit_summary(
    args,
    instance_id: str,
    trace_id: str,
    compiled,
    final_state,
    elapsed_seconds: float,
    checkpoint_enabled: bool,
) -> None:
    """输出执行摘要（顺序/状态/耗时/输出键，任务 5.4 口径）。"""
    summary: Dict[str, Any] = {
        "instance_id": instance_id,
        "trace_id": trace_id,
        "definition_id": compiled.definition_id,
        "definition_version": compiled.definition_version,
        "状态": "成功",
        "顺序": list(compiled.node_order),
        "耗时秒": round(elapsed_seconds, 3),
        "输出键": sorted(final_state.node_outputs.keys()),
        "checkpoint_enabled": checkpoint_enabled,
    }
    if getattr(args, "json", False):
        emit_json(summary)
    else:
        emit_text("执行摘要")
        emit_text(f"  实例：{instance_id}")
        emit_text(f"  定义：{compiled.definition_id}（版本 {compiled.definition_version}）")
        emit_text(f"  状态：成功")
        emit_text(f"  顺序：{' -> '.join(compiled.node_order)}")
        emit_text(f"  耗时：{elapsed_seconds:.3f} 秒")
        emit_text(f"  输出键：{'、'.join(sorted(final_state.node_outputs.keys())) or '（无）'}")
        if checkpoint_enabled:
            emit_text("  检查点：已启用（本地文件仓储）")


def run_run_local(args) -> int:
    """run 命令入口：--local 必选（当前唯一模式），本地单进程执行。"""
    import asyncio

    # --local 为当前唯一执行模式（5.4 命令形态：wfp run <file> --local）
    if not getattr(args, "local", False):
        emit_error("当前仅支持本地执行模式：wfp run <工作流 YAML> --local")
        return EXIT_FAIL

    # ① 解析（失败 → 2）
    try:
        text = read_workflow_file(args.target)
        definition = parse_workflow_text(text)
    except WorkflowLoadError as error:
        emit_error(str(error))
        return EXIT_LOAD_ERROR
    except WorkflowInvalidError as error:
        emit_error(str(error))
        return EXIT_FAIL

    # ② 校验（ERROR 级问题 → 1，如实报告不执行）
    issues = validate_definition(definition)
    errors = [issue for issue in issues if issue.severity == Severity.ERROR]
    if errors:
        emit_error(f"工作流定义校验未通过（{len(errors)} 个 ERROR 级问题）：")
        for issue in errors:
            location = f"（节点 {issue.node_id}）" if issue.node_id else ""
            emit_error(f"  - [{issue.code}] {issue.message}{location}")
        return EXIT_FAIL

    # ③ 执行器注册表（复用生产装配；装配失败 → 1）
    registry = _build_local_registry()
    if registry is None:
        emit_error("执行器注册表构造失败，无法执行本地工作流")
        return EXIT_FAIL

    # ④ 检查点仓储（--checkpoint 可选启用，默认关闭，DC-08 互不影响）
    checkpoint_repository = None
    if getattr(args, "checkpoint", False):
        from orchestration_platform.persistence.local_file.local_file_checkpoint_repository import (
            LocalFileCheckpointRepository,
        )

        checkpoint_repository = LocalFileCheckpointRepository()

    instance_id = f"wfp-local-{uuid.uuid4().hex[:12]}"
    trace_id = f"wfp-trace-{uuid.uuid4().hex[:12]}"

    # ⑤ 编译 + 执行（编译失败 → 1；节点级失败 NodeExecutionError → 1）
    started = time.perf_counter()
    try:
        compiled, final_state = asyncio.run(
            _compile_and_execute(
                definition, registry, checkpoint_repository, instance_id, trace_id
            )
        )
    except WorkflowInvalidError as error:
        emit_error(str(error))
        return EXIT_FAIL
    except WorkflowLoadError as error:  # pragma: no cover - 防御路径
        emit_error(str(error))
        return EXIT_LOAD_ERROR
    except Exception as error:
        # DomainError（含 NodeExecutionError/步数护栏等）如实报告 → 1
        emit_error(f"工作流执行失败：{error}")
        return EXIT_FAIL
    elapsed = time.perf_counter() - started

    _emit_summary(args, instance_id, trace_id, compiled, final_state, elapsed, bool(checkpoint_repository))
    return EXIT_OK
