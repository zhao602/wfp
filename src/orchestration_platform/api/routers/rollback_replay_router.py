# =============================================================================
# 回滚重放与审核路由（api/routers/rollback_replay_router.py）
#
# 职责：实例回滚/重放路由（arch_review_v2_tasks_wave2 G4/F4，CHG-63/CHG-50/
#      DC-30，发布点 P9；任务 5.1/5.3/5.4）+ 人工审核决策路由（G2，
#      CHG-63/DC-27，发布点 P7）。
# 路由层适配约定（DC-30 规则 1）：
#   1. 服务层 RollbackReplayService/ReviewDecisionService 既有方法签名不变
#      （HC-01）；本路由只做参数适配（InstanceRuntime/检查点仓储 →
#      ExecutionInstance/ReplayRequest 域模型装配）、状态回写与响应扩展；
#   2. ExecutionInstance.definition_version 占位 1——服务回滚/重放路径不
#      消费该字段（InstanceRuntime 无定义版本源，E-W2-11）；
#   3. strict 的 compared_outputs 摘要与 re_execute 的差异对比均取检查点
#      状态快照 node_outputs——正常执行链路不向事件仓储写 NODE_SUCCEEDED
#      （注册表内存事件不携带输出值），检查点快照是唯一权威输出存储
#      （INV-08 式适配，batch-records §P5 留痕）。
# =============================================================================

import uuid
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from orchestration_platform.domain.errors import (
    REPLAY_ALREADY_IN_PROGRESS,
    STATE_CHECKPOINT_NOT_FOUND,
    DomainError,
    InstanceStateConflictError,
)
from orchestration_platform.domain.models.enums import EventType

router = APIRouter(tags=["回滚/重放/审核"])


class RollbackBody(BaseModel):
    target_checkpoint_id: str


class ReplayBody(BaseModel):
    """重放请求体（DC-30 规则 4：可选幂等键，缺省由 checkpoint_id+mode 派生）。"""

    checkpoint_id: str
    mode: str = "strict_replay"
    replay_key: Optional[str] = Field(
        default=None, description="可选重放幂等键（缺省由 checkpoint_id+mode 派生）"
    )


class ReviewBody(BaseModel):
    """审核决策请求体（design §4.3 ReviewDecision 契约，spec §6.4 规则 1）。"""

    decision: Literal["approve", "reject", "timeout_action"] = Field(
        description="审核决策（三值取值域）"
    )
    comment: str = ""
    actor: str = Field(description="决策人标识（鉴权链注入的 user_id）")


# =============================================================================
# 回滚/重放路由层适配助手（DC-30 规则 1/3/4；任务 5.1/5.3）
# =============================================================================


def _build_execution_instance(rt):
    """从 InstanceRuntime 装配 ExecutionInstance 域模型（DC-30 规则 1）。

    definition_version 占位 1：服务回滚/重放路径不消费该字段
    （InstanceRuntime 无定义版本源，E-W2-11，见模块头注 2）；
    注册表状态为自由字符串，未知值回退 PENDING（防御，正常不出现）。
    """
    from orchestration_platform.domain.models.enums import InstanceStatus
    from orchestration_platform.domain.models.execution_instance import ExecutionInstance

    try:
        status = InstanceStatus(rt.status)
    except ValueError:
        status = InstanceStatus.PENDING
    return ExecutionInstance(
        instance_id=rt.instance_id,
        definition_id=rt.definition_id,
        definition_version=1,
        status=status,
        trace_id=rt.trace_id or "",
    )


def _next_replay_instance_id(origin_instance_id: str, registry) -> str:
    """派生下一个重放实例标识：{origin}-replay-{max+1}（DC-30 规则 3 ②）。

    注册表无公开列举 API（范围守恒禁改注册表类）——私有 _instances
    只读扫描仅限本处序号派生与幂等护栏两用。
    """
    prefix = f"{origin_instance_id}-replay-"
    max_seq = 0
    for key in registry._instances:
        if key.startswith(prefix) and key[len(prefix):].isdigit():
            max_seq = max(max_seq, int(key[len(prefix):]))
    return f"{origin_instance_id}-replay-{max_seq + 1}"


def _has_active_replay(origin_instance_id: str, registry) -> bool:
    """是否存在活跃重放实例：{origin}-replay-N 在册且任务未完成且状态在途。

    活跃 =（task 未完成）且（status ∈ pending/running）；重放完成
    （终态）不算活跃——允许同键再次发起（序号递增，DC-30 规则 4）。
    """
    prefix = f"{origin_instance_id}-replay-"
    for key, candidate in registry._instances.items():
        if not key.startswith(prefix):
            continue
        task_active = candidate.task is not None and not candidate.task.done()
        status_active = candidate.status in ("pending", "running")
        if task_active and status_active:
            return True
    return False


async def _reject_if_replay_in_progress(
    origin_instance_id: str,
    checkpoint_id: str,
    mode_value: str,
    event_repo,
    registry,
) -> None:
    """重放幂等护栏（DC-30 规则 4，任务 5.3；errors.py G4 留痕）。

    同（mode, checkpoint_id）REPLAY 留痕（服务写入 origin 持久化事件流，
    未作废）且活跃重放实例在途 → 409 REPLAY_ALREADY_IN_PROGRESS；
    strict 无重放实例永不触发；幂等键默认由 checkpoint_id+mode 派生
    （body.replay_key 为契约完整性的调用方侧键，服务端以持久化留痕的
    （mode, checkpoint_id）为同键判定依据，自定义键不构成旁路）。
    """
    events = await event_repo.list_for_instance(origin_instance_id)
    has_same_replay = any(
        e.event_type == EventType.REPLAY
        and not getattr(e, "voided", False)
        and e.payload.get("mode") == mode_value
        and e.payload.get("checkpoint_id") == checkpoint_id
        for e in events
    )
    if not has_same_replay:
        return
    if _has_active_replay(origin_instance_id, registry):
        raise HTTPException(
            status_code=409,
            detail={
                "code": REPLAY_ALREADY_IN_PROGRESS,
                "message": (
                    f"实例 {origin_instance_id} 的重放"
                    f"（mode={mode_value}, checkpoint_id={checkpoint_id}）"
                    "仍在进行，拒绝重复发起"
                ),
            },
        )


@router.post("/instances/{instance_id}/rollback")
async def rollback_instance(instance_id: str, body: RollbackBody) -> dict:
    """回滚实例到指定检查点（CHG-63/DC-30 规则 1；发布点 P9）。

    服务既有语义（HC-01 零改动）：RUNNING 拒绝 → 检查点校验 → 恢复
    SharedState 位点 → void_after 批量作废后续事件（不可变原则，绝不
    物理删除）→ ROLLBACK 事件留痕 → 实例置 PENDING（rollback ≠ replay，
    回滚后再执行走正常启动）；路由层补：注册表状态回写 +
    instance.rolled_back 发布（P9，载荷按 design 附录 A.1）。
    """
    # 延迟导入：路由模块保持轻装载（对齐 review 路由范式）
    from orchestration_platform.api.routers.execution_instance_router import (
        _acquire_checkpoint_repository,
        _instance_registry,
        _publish_instance_domain_event,
    )
    from orchestration_platform.application.rollback_replay_service import (
        RollbackReplayService,
    )
    from orchestration_platform.persistence.repository_factory import RepositoryFactory

    rt = _instance_registry.get(instance_id)
    if rt.task is not None and not rt.task.done():
        raise HTTPException(
            status_code=409,
            detail=f"实例 {instance_id} 仍在运行，禁止回滚",
        )

    checkpoint_repo = _acquire_checkpoint_repository()
    if checkpoint_repo is None:
        raise HTTPException(
            status_code=500, detail="检查点仓储不可用，无法回滚",
        )
    event_repo = RepositoryFactory.create_event_repository()

    instance = _build_execution_instance(rt)
    service = RollbackReplayService(checkpoint_repo, event_repo)
    try:
        result = await service.rollback(instance, body.target_checkpoint_id)
    except DomainError as exc:
        if exc.code == STATE_CHECKPOINT_NOT_FOUND:
            raise HTTPException(status_code=404, detail=str(exc))
        raise HTTPException(status_code=409, detail=str(exc))

    # 服务就地改 instance.status（PENDING）→ 回写注册表（状态机唯一真源）
    _instance_registry.update_status(instance_id, instance.status.value)

    # P9：instance.rolled_back 发布（附录 A.1 载荷，审计留痕）
    await _publish_instance_domain_event(
        rt,
        rt.trace_id or "",
        "instance.rolled_back",
        {
            "target_checkpoint_id": result.restored_checkpoint_id,
            "restored_round_index": result.restored_round_index,
            "voided_event_count": result.voided_event_count,
        },
    )

    return {
        "instance_id": result.instance_id,
        "restored_checkpoint_id": result.restored_checkpoint_id,
        "restored_round_index": result.restored_round_index,
        "voided_event_count": result.voided_event_count,
    }


@router.post("/instances/{instance_id}/replay")
async def replay_instance(instance_id: str, body: ReplayBody) -> dict:
    """重放实例（CHG-63/DC-30 规则 2/3/4；发布点 P9）。

    strict_replay：复用历史 node_outputs（服务 _strict_replay 零改动、
    零外部调用）+ 路由层补 compared_outputs 逐节点一致摘要（AC-03 前半）；
    re_execute：ReplayExecutor 编排新实例 {origin}-replay-{N} 真跑
    （resume_checkpoint 复用 E-W2-01，原记录零覆盖）+ 双侧检查点快照
    node_outputs 对比（确定性白名单正反例 + 非确定性如实标注，
    AC-03 后半）；完成即发布 instance.replayed（P9）。
    """
    # 延迟导入：路由模块保持轻装载（对齐 review 路由范式）
    from orchestration_platform.api.routers.execution_instance_router import (
        NodeEventCallbackImpl,
        InstanceRuntime,
        _acquire_checkpoint_repository,
        _execution_submitter,
        _executor_registry,
        _instance_registry,
        _publish_instance_domain_event,
    )
    from orchestration_platform.application.replay_executor import ReplayExecutor
    from orchestration_platform.application.rollback_replay_service import (
        RollbackReplayService,
    )
    from orchestration_platform.domain.models.enums import ReplayMode
    from orchestration_platform.domain.models.execution_instance import ReplayRequest
    from orchestration_platform.engine.scheduler import Scheduler
    from orchestration_platform.persistence.repository_factory import RepositoryFactory

    rt = _instance_registry.get(instance_id)
    if rt.task is not None and not rt.task.done():
        raise HTTPException(
            status_code=409,
            detail=f"实例 {instance_id} 仍在运行，禁止重放",
        )

    # 模式值域校验（ReplayMode 两值；值外 → 422）
    try:
        mode = ReplayMode(body.mode)
    except ValueError:
        raise HTTPException(
            status_code=422, detail=f"未知重放模式: {body.mode}",
        )

    checkpoint_repo = _acquire_checkpoint_repository()
    if checkpoint_repo is None:
        raise HTTPException(
            status_code=500, detail="检查点仓储不可用，无法重放",
        )
    event_repo = RepositoryFactory.create_event_repository()

    # 幂等护栏（DC-30 规则 4，任务 5.3）：先于服务 replay()（REPLAY 留痕）
    await _reject_if_replay_in_progress(
        instance_id, body.checkpoint_id, mode.value, event_repo, _instance_registry
    )

    # 步骤②（DC-30 规则 3）：调既有服务 replay() 写 REPLAY 事件留痕
    # （两模式共用；strict 至此语义完成，re_execute 续入编排器）
    instance = _build_execution_instance(rt)
    request = ReplayRequest(
        instance_id=instance_id,
        checkpoint_id=body.checkpoint_id,
        mode=mode,
    )
    service = RollbackReplayService(checkpoint_repo, event_repo)
    try:
        result = await service.replay(instance, request)
    except DomainError as exc:
        if exc.code == STATE_CHECKPOINT_NOT_FOUND:
            raise HTTPException(status_code=404, detail=str(exc))
        raise HTTPException(status_code=409, detail=str(exc))

    workflow_repo = None
    try:
        workflow_repo = RepositoryFactory.create_workflow_repository()
    except Exception:
        workflow_repo = None  # 定义仓储不可用 → 依赖运行时编译产物缓存
    executor = ReplayExecutor(
        checkpoint_repository=checkpoint_repo,
        workflow_repository=workflow_repo,
    )

    # 步骤①（DC-30 规则 3）：编译产物解析——优先运行时缓存（同源保真），
    # 缺失回退定义仓储重取；均无 → 422（重放缺少可执行定义）
    try:
        compiled = await executor.resolve_compiled(rt.definition_id, rt.compiled)
    except DomainError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    if mode == ReplayMode.STRICT_REPLAY:
        # strict：服务已复用历史输出；路由层补逐节点一致摘要（AC-03 前半）
        compared_outputs = await executor.strict_summary(instance_id, compiled)
        divergent_node_ids: list = []
        consistent: bool = result.consistent
        new_event_count: int = result.new_event_count
        replay_instance_id = None
    else:
        # re_execute：步骤③④⑤（DC-30 规则 3）——launch 回调注入
        next_replay_id = _next_replay_instance_id(instance_id, _instance_registry)

        async def _launch(compiled_graph, checkpoint) -> int:
            """launch 回调：新实例注册 + Scheduler 真跑至终态（原记录零覆盖）。

            新 trace_id（每实例独立 trace 语义）；关联经 instance.replayed
            载荷 origin_instance_id/replay_instance_id（附录 A.1）；
            trigger_source="re_execute"（P1 变体，发布链既有自由字符串）。
            """
            new_rt = InstanceRuntime(
                instance_id=next_replay_id,
                definition_id=rt.definition_id,
                tenant_id=rt.tenant_id,
                business_category=rt.business_category,
            )
            new_rt.compiled = compiled_graph
            new_rt.trace_id = str(uuid.uuid4())
            _instance_registry.register(new_rt)

            scheduler = Scheduler(
                executor_registry=_executor_registry,
                checkpoint_repository=checkpoint_repo,
                event_callback=NodeEventCallbackImpl(_instance_registry),
            )
            # M1 spawn 收口：统一提交器提交（in_process 默认等价路径；
            # worker 模式入队返回 None，重放实例由 worker 认领）
            task = await _execution_submitter.submit(
                scheduler,
                compiled_graph,
                checkpoint.state_snapshot,
                next_replay_id,
                new_rt.trace_id,
                _instance_registry,
                resume_checkpoint=checkpoint,
                trigger_source="re_execute",
                rt=new_rt,
            )
            if task is not None:
                await task
            return len(new_rt.events)

        execution = await executor.re_execute(
            instance, request, compiled, next_replay_id, _launch
        )
        divergent_node_ids = execution.divergent_node_ids
        consistent = execution.consistent
        # 新增事件总数 = REPLAY 留痕（服务，origin 流）+ 新实例独立事件流
        new_event_count = result.new_event_count + execution.new_event_count
        replay_instance_id = execution.replay_instance_id
        compared_outputs = execution.compared_outputs

    # P9：instance.replayed 发布（附录 A.1 载荷，审计留痕）
    await _publish_instance_domain_event(
        rt,
        rt.trace_id or "",
        "instance.replayed",
        {
            "mode": mode.value,
            "checkpoint_id": body.checkpoint_id,
            "origin_instance_id": instance_id,
            "replay_instance_id": replay_instance_id,
            "divergent_node_ids": divergent_node_ids,
        },
    )

    return {
        "instance_id": result.instance_id,
        "mode": mode.value,
        "consistent": consistent,
        "divergent_node_ids": divergent_node_ids,
        "new_event_count": new_event_count,
        "replay_instance_id": replay_instance_id,
        "compared_outputs": [c.model_dump() for c in compared_outputs],
    }


@router.post("/instances/{instance_id}/review")
async def submit_review(instance_id: str, body: ReviewBody) -> dict:
    """提交人工审核决策（arch_review_v2_tasks_wave2 G2，CHG-63/DC-27，发布点 P7）。

    前置校验：仅 WAITING_REVIEW 实例可提交（INSTANCE_NOT_WAITING_REVIEW → 409）；
    approve → 经 resume_checkpoint 机制续跑；reject → review_timeout_action
    映射终态（不自动重跑）；timeout_action → REVIEW_TIMEOUT；决策经
    STATUS_CHANGED 执行事件 + governance.review_decision 领域事件留痕
    （spec §6.4 规则 3，审批动作全留痕）。
    """
    # 延迟导入：路由模块保持轻装载；执行实例路由内部与服务的依赖按需进入
    from orchestration_platform.api.routers.execution_instance_router import (
        NodeEventCallbackImpl,
        _acquire_checkpoint_repository,
        _domain_event_publisher,
        _execution_submitter,
        _executor_registry,
        _instance_registry,
    )
    from orchestration_platform.application.review_service import ReviewDecisionService
    from orchestration_platform.engine.scheduler import Scheduler
    from orchestration_platform.persistence.repository_factory import RepositoryFactory

    rt = _instance_registry.get(instance_id)

    if rt.compiled is None:
        raise HTTPException(
            status_code=409,
            detail=f"实例 {instance_id} 缺少可决策的执行上下文",
        )
    if rt.task is not None and not rt.task.done():
        raise HTTPException(
            status_code=409,
            detail=f"实例 {instance_id} 仍在运行，禁止提交审核决策",
        )

    checkpoint_repo = _acquire_checkpoint_repository()
    if checkpoint_repo is None:
        raise HTTPException(
            status_code=500, detail="检查点仓储不可用，无法处理审核决策",
        )
    event_repo = RepositoryFactory.create_event_repository()

    def _resume_runner(checkpoint) -> None:
        """approve 唤醒：重建 Scheduler + 经统一提交器 resume 续跑。

        trigger_source=review_resume：续跑 instance.started（P1 变体）即唤醒信号，
        不另发 instance.resumed（拍板项 #6 合并口径，不留双发）。
        M1 spawn 收口：同步回调场景走 submit_sync（in_process 直达
        create_task，rt.task 同步落值，与历史逐拍等价；worker 模式入队）。
        """
        scheduler = Scheduler(
            executor_registry=_executor_registry,
            checkpoint_repository=checkpoint_repo,
            event_callback=NodeEventCallbackImpl(_instance_registry),
        )
        _execution_submitter.submit_sync(
            scheduler,
            rt.compiled,
            checkpoint.state_snapshot,
            instance_id,
            rt.trace_id or "",
            _instance_registry,
            resume_checkpoint=checkpoint,
            trigger_source="review_resume",
            rt=rt,
        )

    service = ReviewDecisionService(
        checkpoint_repo,
        event_repo,
        _domain_event_publisher,
    )
    try:
        return await service.submit_decision(
            instance_id=instance_id,
            decision=body.decision,
            current_status=rt.status,
            actor=body.actor,
            comment=body.comment,
            trace_id=rt.trace_id or "",
            tenant_id=rt.tenant_id or "",
            suspended_checkpoint_id=rt.suspended_checkpoint_id,
            get_gate_config=lambda node_id: getattr(
                rt.compiled.get_node(node_id), "configuration", None
            ),
            set_status=lambda status: _instance_registry.update_status(
                instance_id, status
            ),
            resume_runner=_resume_runner,
        )
    except InstanceStateConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except DomainError as exc:
        if exc.code == STATE_CHECKPOINT_NOT_FOUND:
            raise HTTPException(status_code=404, detail=str(exc))
        raise HTTPException(status_code=409, detail=str(exc))
