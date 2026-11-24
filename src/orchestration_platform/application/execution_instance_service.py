# TODO(tech-debt): B档候选孤儿（族群 B15），待 APM 链路追踪专项排查确认无调用后清理，见 docs/tech-debt-ledger.md
# =============================================================================
# 执行实例服务（application/execution_instance_service.py）
#
# 职责：管理执行实例的生命周期——创建、启动、检查点生成、故障恢复。
# 设计意图（对应 spec 5.4、design 2.5.4）：
#   1. 事件追加经 log_sanitizer 脱敏后落盘；
#   2. 检查点在节点完成后生成，与事件同事务提交；
#   3. 故障恢复：扫描 RUNNING 实例，取最新检查点续跑。
# =============================================================================

import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.errors import (
    INSTANCE_NOT_FOUND,
    STATE_CHECKPOINT_CORRUPTED,
    ResourceNotFoundError,
    DomainError,
)
from orchestration_platform.domain.models.checkpoint import Checkpoint
from orchestration_platform.domain.models.enums import (
    EventType,
    InstanceStatus,
)
from orchestration_platform.domain.models.execution_event import ExecutionEvent
from orchestration_platform.domain.models.execution_instance import (
    ExecutionInstance,
    StartInstanceRequest,
)
from orchestration_platform.domain.models.shared_state import SharedState
from orchestration_platform.engine.abstractions.compiled_workflow import CompiledWorkflow
from orchestration_platform.engine.scheduler import Scheduler
from orchestration_platform.engine.shared_state_manager import SharedStateManager
from orchestration_platform.persistence.abstractions.repositories import (
    ICheckpointRepository,
    IEventRepository,
    IWorkflowRepository,
)
from orchestration_platform.application.log_sanitizer import LogSanitizer


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class ExecutionInstanceService:
    """执行实例服务：实例生命周期管理 + 事件溯源 + 检查点生成。"""

    def __init__(
        self,
        workflow_repo: IWorkflowRepository,
        checkpoint_repo: ICheckpointRepository,
        event_repo: IEventRepository,
        scheduler: Scheduler,
    ) -> None:
        self._workflow_repo = workflow_repo
        self._checkpoint_repo = checkpoint_repo
        self._event_repo = event_repo
        self._scheduler = scheduler
        self._settings = get_settings()
        # 实例内存索引（生产环境应由仓储持久化）
        self._instances: Dict[str, ExecutionInstance] = {}

    # ------------------------------------------------------------------
    # 实例创建与启动
    # ------------------------------------------------------------------
    async def start_instance(
        self, request: StartInstanceRequest, compiled: CompiledWorkflow
    ) -> ExecutionInstance:
        """启动工作流实例。

        流程：创建实例 → 写 INSTANCE_CREATED 事件 → 执行工作流 → 写检查点。
        """
        instance_id = _new_id("inst")
        trace_id = _new_id("trace")

        instance = ExecutionInstance(
            instance_id=instance_id,
            definition_id=request.definition_id,
            definition_version=compiled.definition_version,
            status=InstanceStatus.RUNNING,
            inputs=request.inputs,
            idempotency_key=request.idempotency_key,
            trigger_type=request.trigger_type,
            trace_id=trace_id,
        )
        self._instances[instance_id] = instance

        # 写入实例创建事件
        await self._append_event(
            instance_id, EventType.INSTANCE_CREATED, None,
            {"definition_id": request.definition_id, "inputs": request.inputs},
        )

        # 运行日志：工作流级采集门面（T7.5，start_instance 签名不变）；
        # business_category 由既有 request.trigger_type 推导，缺失兜底默认值；
        # 构造失败仅告警，本次运行零采集（INV-01）
        run_log_recorder = None
        try:
            from orchestration_platform.observability.run_log.recorder import WorkflowRunLogger

            trigger_type = getattr(request, "trigger_type", None)
            run_log_recorder = WorkflowRunLogger(
                run_id=instance_id,
                trace_id=trace_id,
                business_category=(
                    str(getattr(trigger_type, "value", trigger_type))
                    if trigger_type
                    else None
                ),
                workflow_id=request.definition_id,
                definition_version=getattr(compiled, "definition_version", None),
            )
        except Exception:
            logging.warning(
                "运行日志采集器构造失败，本次运行不采集: instance_id=%s", instance_id,
                exc_info=True,
            )
            run_log_recorder = None

        if run_log_recorder is not None:
            try:
                run_log_recorder.begin_run()
            except Exception:
                logging.warning(
                    "运行日志 begin_run 接线失败: instance_id=%s", instance_id,
                    exc_info=True,
                )

        run_started_at = _utc_now()

        # 构建初始状态并执行
        initial_state = SharedState(
            instance_id=instance_id,
            node_outputs=dict(request.inputs),
            current_node_id=compiled.entry_node_id,
        )

        try:
            final_state = await self._scheduler.execute(
                compiled, initial_state, instance_id, trace_id
            )
            instance.outputs = dict(final_state.node_outputs)
            instance.status = InstanceStatus.SUCCEEDED
            instance.finished_at = _utc_now()
            if run_log_recorder is not None:
                try:
                    run_duration_ms = max(
                        0,
                        int(
                            (instance.finished_at - run_started_at)
                            .total_seconds()
                            * 1000
                        ),
                    )
                    node_summary = getattr(run_log_recorder, "node_summary", None)
                    if isinstance(node_summary, dict) and node_summary.get("total", 0) > 0:
                        run_log_recorder.end_run(
                            "succeeded",
                            finished_at=instance.finished_at,
                            duration_ms=run_duration_ms,
                            node_summary=node_summary,
                        )
                    else:
                        run_log_recorder.end_run(
                            "succeeded",
                            finished_at=instance.finished_at,
                            duration_ms=run_duration_ms,
                        )
                except Exception:
                    logging.warning(
                        "运行日志 end_run 接线失败: instance_id=%s", instance_id,
                        exc_info=True,
                    )
        except DomainError as e:
            instance.status = InstanceStatus.FAILED
            instance.finished_at = _utc_now()
            instance.error = {"code": e.code, "message": e.message}
            await self._append_event(
                instance_id, EventType.NODE_FAILED, None,
                {"error_code": e.code, "error_message": e.message},
            )
            if run_log_recorder is not None:
                try:
                    run_log_recorder.end_run(
                        "failed",
                        finished_at=instance.finished_at,
                        duration_ms=max(
                            0,
                            int(
                                (instance.finished_at - run_started_at)
                                .total_seconds()
                                * 1000
                            ),
                        ),
                        error={"error_code": e.code, "error_message": e.message},
                    )
                except Exception:
                    logging.warning(
                        "运行日志 end_run(failed) 接线失败: instance_id=%s",
                        instance_id, exc_info=True,
                    )
        finally:
            # 采集通道收尾：冲刷落盘；门面自建 sink 时由门面停后台线程（幂等）
            if run_log_recorder is not None:
                try:
                    run_log_recorder.close()
                except Exception:
                    logging.warning(
                        "运行日志 close 接线失败: instance_id=%s", instance_id,
                        exc_info=True,
                    )

        return instance

    # ------------------------------------------------------------------
    # 事件追加（含脱敏）
    # ------------------------------------------------------------------
    async def _append_event(
        self,
        instance_id: str,
        event_type: EventType,
        node_id: Optional[str],
        payload: dict,
    ) -> int:
        """追加执行事件，载荷经脱敏后落盘。"""
        sanitized_payload = LogSanitizer.sanitize(payload)
        event = ExecutionEvent(
            event_id=_new_id("evt"),
            instance_id=instance_id,
            event_type=event_type,
            node_id=node_id,
            sequence=0,  # 仓储自动分配
            payload=sanitized_payload,
        )
        return await self._event_repo.append(event)

    # ------------------------------------------------------------------
    # 检查点生成
    # ------------------------------------------------------------------
    async def create_checkpoint(
        self,
        instance_id: str,
        state_manager: SharedStateManager,
        completed_node_id: str,
        next_node_id: Optional[str],
        round_index: int,
    ) -> str:
        """生成检查点并写入仓储。"""
        checkpoint = Checkpoint(
            checkpoint_id=_new_id("cp"),
            instance_id=instance_id,
            completed_node_id=completed_node_id,
            next_node_id=next_node_id,
            state_snapshot=state_manager.snapshot(),
            round_index=round_index,
        )
        await self._checkpoint_repo.append(checkpoint)

        # 写入检查点创建事件
        await self._append_event(
            instance_id, EventType.CHECKPOINT_CREATED, completed_node_id,
            {"checkpoint_id": checkpoint.checkpoint_id, "round_index": round_index},
        )
        return checkpoint.checkpoint_id

    # ------------------------------------------------------------------
    # 故障恢复
    # ------------------------------------------------------------------
    async def recover_instance(self, instance_id: str) -> ExecutionInstance:
        """故障恢复：从最新检查点恢复实例执行。

        流程：取 latest_for_instance → 反序列化 SharedState → 从 next_node_id 续跑。
        反序列化失败则 mark_unavailable 并回退更早检查点。
        """
        instance = self._instances.get(instance_id)
        if instance is None:
            raise ResourceNotFoundError(INSTANCE_NOT_FOUND, f"实例 {instance_id} 不存在")

        # 尝试获取最新可用检查点
        checkpoint = await self._checkpoint_repo.latest_for_instance(instance_id)
        if checkpoint is None:
            instance.status = InstanceStatus.UNRECOVERABLE
            raise DomainError(
                STATE_CHECKPOINT_CORRUPTED,
                f"实例 {instance_id} 无可用检查点，无法恢复",
            )

        # 检查点损坏降级：反序列化失败则 mark_unavailable 并回退
        if not checkpoint.is_available:
            await self._checkpoint_repo.mark_unavailable(
                checkpoint.checkpoint_id, "检查点不可用"
            )
            checkpoint = await self._checkpoint_repo.latest_for_instance(instance_id)
            if checkpoint is None:
                instance.status = InstanceStatus.UNRECOVERABLE
                raise DomainError(
                    STATE_CHECKPOINT_CORRUPTED,
                    f"实例 {instance_id} 全部检查点不可用",
                )

        # 恢复状态
        instance.status = InstanceStatus.RUNNING
        instance.current_node_id = checkpoint.next_node_id
        return instance

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    async def get_instance(self, instance_id: str) -> ExecutionInstance:
        """查询实例状态。"""
        instance = self._instances.get(instance_id)
        if instance is None:
            raise ResourceNotFoundError(INSTANCE_NOT_FOUND, f"实例 {instance_id} 不存在")
        return instance

    async def list_events(
        self, instance_id: str, include_voided: bool = True
    ) -> List[ExecutionEvent]:
        """查询实例事件序列。"""
        return await self._event_repo.list_for_instance(instance_id, include_voided)
