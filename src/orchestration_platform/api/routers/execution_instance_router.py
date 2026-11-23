# =============================================================================
# 执行实例路由（api/routers/execution_instance_router.py）
#
# 职责：启动工作流执行实例、查询实例状态与事件、SSE 事件流推送、取消与恢复。
# 设计意图：接入真实执行引擎（YamlWorkflowParser → GraphCompiler → Scheduler），
#           以内存注册表暂存实例运行时状态，通过 SSE 实时推送执行事件。
# M1 薄化（roadmap-v3 中期窗口，F1 执行队列与提交路径统一）：
#   1. 执行运行时主体（InstanceRuntime/InstanceRegistry/run_and_record/
#      NodeEventCallbackImpl/执行器注册表构建器）迁入
#      application/execution_runner.py——本模块经顶部 from-import 转发全部
#      原符号，既有导入路径、身份断言与 monkeypatch 语义零变化；
#   2. 领域事件发布器状态保留在本模块（_domain_event_publisher 全局 +
#      set_domain_event_publisher，历史契约），execution_runner 经
#      bind_domain_event_publisher_source 注入读取源调用期回读本模块属性；
#   3. spawn 收口：start_instance / recover_instance 经统一提交器
#      ExecutionSubmitter 提交（in_process 默认 = create_task 等价路径）。
# =============================================================================

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from orchestration_platform.api.instance_read_through import (
    read_instance_snapshot,
    resolve_event_reader,
)
from orchestration_platform.application.execution_runner import (
    ExecutionSubmitter,
    InstanceRegistry,
    InstanceRuntime,
    NodeEventCallbackImpl,
    _TERMINAL_STATUSES,
    _acquire_checkpoint_repository,
    _build_executor_registry,
    _build_llm_client,
    _build_tool_invokers,
    _execution_submitter,
    _executor_registry,
    _instance_registry,
    _platform_tool_get_instance,
    _platform_tool_get_workflow,
    _platform_tool_validate_workflow,
    _publish_instance_domain_event,
    _read_mcp_market_registrations,
    _run_coro_sync_for_assembly,
    _tool_invokers,
    bind_domain_event_publisher_source,
    build_execution_queue,
    run_and_record,
)
from orchestration_platform.domain.errors import (
    INSTANCE_CANCEL_CONFLICT,
    STATE_CHECKPOINT_CORRUPTED,
    DomainError,
    InstanceStateConflictError,
    ResourceNotFoundError,
)
from orchestration_platform.domain.models.enums import ControlFlowKind, NodeType
from orchestration_platform.domain.models.shared_state import SharedState
from orchestration_platform.engine.abstractions.domain_event_publisher import (
    IDomainEventPublisher,
)
from orchestration_platform.governance.tenant.quota_gate import (
    get_quota_gate,
)
from orchestration_platform.observability.run_log.fields import (
    BUSINESS_CATEGORY_DEFAULT,
)

router = APIRouter(tags=["执行实例"])


# =============================================================================
# 请求体模型
# =============================================================================

class StartInstanceBody(BaseModel):
    definition_id: str
    definition_version: Optional[int] = None
    inputs: dict = Field(default_factory=dict)
    yaml_text: Optional[str] = None
    trigger_type: Optional[str] = None


# =============================================================================
# 领域事件发布器状态（CHG-62/DC-23 发布点 P1~P3）：由 application_factory
# 在装配期注入（G1 开关开启时）；None 表示发布旁路，基线行为零变化。
# M1：状态留守本模块（历史契约），execution_runner 经绑定读取源调用期回读。
# =============================================================================

_domain_event_publisher: Optional[IDomainEventPublisher] = None


def set_domain_event_publisher(publisher: Optional[IDomainEventPublisher]) -> None:
    """注入领域事件发布器（应用装配期调用；None 表示关闭发布）。"""
    global _domain_event_publisher
    _domain_event_publisher = publisher


# 执行运行器发布源绑定：run_and_record 内的 instance.* 领域事件发布
# 经此读取源在调用期回读本模块全局（monkeypatch 语义保持，零测试修改）
bind_domain_event_publisher_source(lambda: _domain_event_publisher)


# =============================================================================
# SSE 事件生成器
# =============================================================================

async def event_generator(instance_id: str):
    """异步生成器：轮询实例事件，以 SSE 格式推送新事件。

    M2 读穿透：注册表优先（in_process 语义，行为与历史逐字段等价）；
    未命中回退事件仓储轮询（worker 模式，InstanceEventReader 双实现）。
    """
    reader = await resolve_event_reader(
        instance_id, registry=_instance_registry
    )
    index = 0
    while True:
        events = await reader.read_events(instance_id)
        while index < len(events):
            event = events[index]
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            index += 1
        if await reader.is_terminal(instance_id) and index >= len(events):
            yield f"data: {json.dumps({'event_type': 'STREAM_END', 'instance_id': instance_id}, ensure_ascii=False)}\n\n"
            break
        await asyncio.sleep(0.1)


# =============================================================================
# 路由函数
# =============================================================================

def _resolve_business_category(body: StartInstanceBody, definition: Any) -> str:
    """业务类别三级回退：请求可选 trigger_type → TRIGGER 节点配置 → default。

    承载 design 关键风险 ④（路由无 trigger_type 的回退链）；值异常字符
    由 LogPathResolver.sanitize_segment 统一消毒，不在此处校验。
    """
    if body.trigger_type:
        return body.trigger_type
    for node in getattr(definition, "nodes", None) or []:
        if getattr(node, "node_type", None) == NodeType.TRIGGER:
            config = getattr(node, "configuration", None)
            trigger_type = getattr(config, "trigger_type", None)
            if trigger_type is not None:
                return str(getattr(trigger_type, "value", trigger_type))
    return BUSINESS_CATEGORY_DEFAULT


@router.post("/instances")
async def start_instance(body: StartInstanceBody, request: Request) -> dict:
    """启动工作流实例：解析 YAML → 编译 → 异步调度 → 返回 instance_id。"""
    if not body.yaml_text:
        raise HTTPException(status_code=422, detail="请提供 yaml_text（工作流 YAML 文本）")

    instance_id = str(uuid.uuid4())
    trace_id = str(uuid.uuid4())

    # 租户快照（T7.3）：启动瞬间取值，后台协程存活期不依赖请求对象（INV-11）
    tenant_id: Optional[str] = getattr(request.state, "tenant_id", None)

    # 并发配额闸门（roadmap B4，DC-B4-02）：并发活跃实例数达配额上限即
    # 429 拒绝（TenantQuotaExceededError → handler 映射）；唯一 await =
    # get_quota，count→check→raise→register 全同步块（事件循环单线程原子，
    # 超配额 100% 拒绝）；拒绝路径零副作用：不注册、不解析、不编译、不执行。
    # tenant_id 为 None（治理降级/cron 无租户）不闸门。
    quota_gate = get_quota_gate()
    if quota_gate is not None and tenant_id is not None:
        await quota_gate.ensure_capacity(
            tenant_id,
            _instance_registry.count_active_by_tenant(tenant_id),
        )

    rt = InstanceRuntime(
        instance_id=instance_id,
        definition_id=body.definition_id,
        tenant_id=tenant_id,
    )
    _instance_registry.register(rt)

    try:
        from orchestration_platform.workflow.yaml_workflow_parser import YamlWorkflowParser
        from orchestration_platform.engine.graph_compiler import GraphCompiler

        definition = YamlWorkflowParser().parse(body.yaml_text)
        compiled = await GraphCompiler().compile(definition)
        # 恢复上下文缓存（arch_review_v2 3.4，CHG-07）：编译产物与 trace 落注册表
        rt.compiled = compiled
        rt.trace_id = trace_id
    except HTTPException:
        _instance_registry.remove(instance_id)
        raise
    except Exception as exc:
        _instance_registry.remove(instance_id)
        raise HTTPException(status_code=422, detail=f"工作流解析/编译失败: {str(exc)}")

    # 业务类别三级回退（T7.3）：请求 trigger_type → TRIGGER 节点配置 → default
    business_category = _resolve_business_category(body, definition)
    rt.business_category = business_category

    # 运行日志采集门面（T7.3）：构造失败仅告警，本次运行零采集（INV-01）；
    # run_id=instance_id、trace_id 与实例同源（2.0.1 公共字段）
    run_log_recorder = None
    try:
        from orchestration_platform.observability.run_log.recorder import WorkflowRunLogger

        run_log_recorder = WorkflowRunLogger(
            run_id=instance_id,
            trace_id=trace_id,
            tenant_id=tenant_id,
            business_category=business_category,
            workflow_id=body.definition_id,
            definition_version=getattr(compiled, "definition_version", None)
            or body.definition_version,
        )
    except Exception:
        logging.warning(
            "运行日志采集器构造失败，本次运行不采集: instance_id=%s", instance_id,
            exc_info=True,
        )
        run_log_recorder = None

    try:
        from orchestration_platform.engine.scheduler import Scheduler

        # 构造初始共享状态（DC-1，INV-1）
        try:
            initial_state = SharedState(
                instance_id=instance_id,
                node_outputs=body.inputs or {},
            )
        except Exception as exc:
            _instance_registry.remove(instance_id)
            raise HTTPException(
                status_code=422,
                detail=f"初始状态构造失败: {type(exc).__name__}",
            )

        # 构造节点事件回调并注入 Scheduler（DC-3，变更点 C34-5）
        callback = NodeEventCallbackImpl(_instance_registry)
        # 检查点仓储接线（arch_review_v2 3.2，CHG-07/DC-08）：经仓储工厂取双后端
        # 实现（E2/E3 唯一接线点）；失败降级 None + 告警，执行链不中断
        scheduler = Scheduler(
            executor_registry=_executor_registry,
            checkpoint_repository=_acquire_checkpoint_repository(),
            event_callback=callback,
            run_log_recorder=run_log_recorder,
            domain_event_publisher=_domain_event_publisher,
        )

        # M1 spawn 收口：统一提交器提交（in_process 默认 =
        # create_task(run_and_record(...)) 等价路径，rt.task 由提交器落值）；
        # M2：yaml_text 随作业携带（worker 进程重解析编译；in_process 不消费）
        await _execution_submitter.submit(
            scheduler,
            compiled,
            initial_state,
            instance_id,
            trace_id,
            _instance_registry,
            run_log_recorder=run_log_recorder,
            rt=rt,
            yaml_text=body.yaml_text,
        )
    except Exception as exc:
        _instance_registry.remove(instance_id)
        raise HTTPException(status_code=500, detail=f"启动执行引擎失败: {str(exc)}")

    return {
        "instance_id": instance_id,
        "status": "running",
        "started_at": rt.started_at,
    }


@router.get("/instances/{instance_id}")
async def get_instance(instance_id: str) -> dict:
    """查询实例状态（worker 模式注册表未命中回退事件仓储读穿透）。"""
    try:
        rt = _instance_registry.get(instance_id)
    except HTTPException:
        snapshot = await read_instance_snapshot(instance_id)
        if snapshot is not None:
            return snapshot
        raise
    result: dict = {
        "instance_id": rt.instance_id,
        "definition_id": rt.definition_id,
        "status": rt.status,
        "started_at": rt.started_at,
    }
    if rt.finished_at:
        result["finished_at"] = rt.finished_at
    return result


@router.get("/instances/{instance_id}/events")
async def get_instance_events(instance_id: str, include_voided: bool = True) -> list:
    """查询实例事件序列。"""
    return _instance_registry.get_events(instance_id)


@router.get("/instances/{instance_id}/events/stream")
async def stream_instance_events(instance_id: str):
    """SSE 事件流：以 text/event-stream 格式实时推送执行事件。

    预检经读穿透解析器（未知实例快速 404 不建流；worker 模式注册表
    未命中但事件仓储有事件 → 建流轮询）。
    """
    await resolve_event_reader(instance_id, registry=_instance_registry)
    return StreamingResponse(
        event_generator(instance_id),
        media_type="text/event-stream",
    )


@router.post("/instances/{instance_id}/cancel")
async def cancel_instance(instance_id: str) -> dict:
    """请求取消执行中的实例（B2 嵌入 SDK：取消能力后端落点）。

    契约（docs/sdk-contract.md §1-7）：
    - 未知实例 → 404（注册表既有 detail）；已终态或无可取消任务
      （task 缺失/已结束，含 waiting_review 挂起态）→ 409
      INSTANCE_CANCEL_CONFLICT（{code, message, details} 同构错误体）；
    - 运行中 → task.cancel() 投递取消，终态由 run_and_record 的
      CancelledError 分支落定（本响应不等待，返回请求时刻快照）；
    - 竞态容忍：任务恰在校验后完成时 task.cancel() 为 no-op，
      客户端以 GET /instances/{id} 轮询到的终态为准。
    """
    rt = _instance_registry.get(instance_id)

    if rt.status in _TERMINAL_STATUSES:
        raise InstanceStateConflictError(
            INSTANCE_CANCEL_CONFLICT,
            f"实例 {instance_id} 已处于终态 {rt.status}，无法取消",
        )
    if rt.task is None or rt.task.done():
        raise InstanceStateConflictError(
            INSTANCE_CANCEL_CONFLICT,
            f"实例 {instance_id} 当前无可取消的运行任务（状态 {rt.status}）",
        )

    rt.task.cancel()
    return {
        "instance_id": rt.instance_id,
        "definition_id": rt.definition_id,
        "status": rt.status,
        "started_at": rt.started_at,
    }


@router.get("/instances/{instance_id}/results")
async def get_instance_results(instance_id: str) -> dict:
    """查询实例执行结果（B2 嵌入 SDK：取结果端点）。

    契约（docs/sdk-contract.md §1-8）：
    - 未知实例 → 404；非终态 → 200 {outputs: null}（轮询友好，不设 409）；
    - 终态 → 200 {outputs, status, finished_at}；outputs 取
      run_and_record 缓存的终态共享状态 node_outputs（仅 succeeded
      且 final_state 非 None 有值；failed/cancelled/rolled_back 或
      重启后老实例 → {}）。
    """
    rt = _instance_registry.get(instance_id)

    if rt.status not in _TERMINAL_STATUSES:
        return {
            "instance_id": rt.instance_id,
            "status": rt.status,
            "outputs": None,
        }

    outputs: Dict[str, Any] = {}
    if rt.status == "succeeded" and rt.final_state is not None:
        node_outputs = getattr(rt.final_state, "node_outputs", None)
        if isinstance(node_outputs, dict):
            outputs = dict(node_outputs)
    return {
        "instance_id": rt.instance_id,
        "status": rt.status,
        "outputs": outputs,
        "finished_at": rt.finished_at,
    }


@router.get("/instances/{instance_id}/checkpoints")
async def get_checkpoints(instance_id: str) -> list:
    """查询实例检查点列表。"""
    return []


# =============================================================================
# 故障恢复（arch_review_v2 3.4，CHG-07，卡 A1 契约；损坏降级复用 E4）
# =============================================================================

async def _resolve_recovery_checkpoint(checkpoint_repo, rt: InstanceRuntime):
    """调既有 ExecutionInstanceService.recover_instance 取恢复位点（E4 复用）。

    latest_for_instance → is_available 校验 → 损坏 mark_unavailable →
    回退更早检查点：全部复用服务既有逻辑（application/execution_instance_service.py）。
    服务侧实例对象由路由注册表映射注入服务内存索引（B15 复活接线，
    服务 recover 流程仅消费 instance_id 与状态位）。
    """
    from orchestration_platform.application.execution_instance_service import (
        ExecutionInstanceService,
    )
    from orchestration_platform.domain.models.enums import InstanceStatus
    from orchestration_platform.domain.models.execution_instance import ExecutionInstance
    from orchestration_platform.engine.scheduler import Scheduler
    from orchestration_platform.persistence.repository_factory import RepositoryFactory

    workflow_repo = RepositoryFactory.create_workflow_repository()
    event_repo = RepositoryFactory.create_event_repository()
    service = ExecutionInstanceService(
        workflow_repo,
        checkpoint_repo,
        event_repo,
        Scheduler(
            executor_registry=_executor_registry,
            checkpoint_repository=checkpoint_repo,
        ),
    )
    service._instances[rt.instance_id] = ExecutionInstance(
        instance_id=rt.instance_id,
        definition_id=rt.definition_id,
        definition_version=1,
        status=InstanceStatus.FAILED,
        inputs={},
        trigger_type="manual",
        trace_id=rt.trace_id or "",
    )

    await service.recover_instance(rt.instance_id)
    return await checkpoint_repo.latest_for_instance(rt.instance_id)


@router.post("/instances/{instance_id}/recover")
async def recover_instance(instance_id: str) -> dict:
    """故障恢复：从最近可用检查点续跑实例（CHG-07，卡 A1 契约）。

    契约（卡 A1）：
    - 出参 {instance_id, resumed_from_checkpoint_id, status}；
    - INSTANCE_NOT_FOUND → 404；STATE_CHECKPOINT_CORRUPTED → 500（复用既有错误码）；
    - 不入 rollback_replay_router（Z-4，F4 属 W5–7 桩现状冻结）。
    """
    rt = _instance_registry.get(instance_id)

    if rt.compiled is None:
        raise HTTPException(
            status_code=409,
            detail=f"实例 {instance_id} 缺少可恢复的执行上下文",
        )
    if rt.task is not None and not rt.task.done():
        raise HTTPException(
            status_code=409,
            detail=f"实例 {instance_id} 仍在运行，禁止恢复",
        )

    checkpoint_repo = _acquire_checkpoint_repository()
    if checkpoint_repo is None:
        raise HTTPException(
            status_code=500, detail="检查点仓储不可用，无法恢复",
        )

    # 恢复位点解析（E4 损坏降级复用服务既有逻辑）
    try:
        checkpoint = await _resolve_recovery_checkpoint(checkpoint_repo, rt)
    except ResourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except DomainError as exc:
        if exc.code == STATE_CHECKPOINT_CORRUPTED:
            raise HTTPException(status_code=500, detail=str(exc))
        raise

    if checkpoint is None:
        raise HTTPException(
            status_code=500,
            detail=f"实例 {instance_id} 无可用检查点，无法恢复",
        )

    # HITL 挂起恢复分支（arch_review_v2_tasks_wave2 G2，CHG-62/DC-26 规则 6）：
    # 恢复位点检查点（latest_for_instance，round_index 最大 = 挂起点）的
    # completed_node_id 为人工审核门节点时，实例置 WAITING_REVIEW 而非直接
    # 续跑（交由 review 决策驱动，AC-01 重启可恢复）；非挂起实例零变化
    completed_node = (
        rt.compiled.get_node(checkpoint.completed_node_id)
        if checkpoint.completed_node_id
        else None
    )
    if (
        completed_node is not None
        and completed_node.node_type == NodeType.CONTROL_FLOW
        and getattr(
            getattr(completed_node, "configuration", None),
            "control_flow_kind",
            None,
        )
        == ControlFlowKind.HUMAN_REVIEW_GATE
    ):
        gate_config = getattr(completed_node, "configuration", None)
        _instance_registry.update_status(instance_id, "waiting_review")
        rt.suspended_checkpoint_id = checkpoint.checkpoint_id
        _instance_registry.append_event(instance_id, {
            "event_id": str(uuid.uuid4()),
            "instance_id": instance_id,
            "event_type": "INSTANCE_SUSPENDED",
            "node_id": checkpoint.completed_node_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": {
                "checkpoint_id": checkpoint.checkpoint_id,
                "next_node_id": checkpoint.next_node_id,
                "review_instructions": str(
                    getattr(gate_config, "review_instructions", None) or ""
                ),
                "timeout_action": str(
                    getattr(gate_config, "review_timeout_action", None) or ""
                ),
                "recovered": True,
            },
        })
        return {
            "instance_id": instance_id,
            "resumed_from_checkpoint_id": checkpoint.checkpoint_id,
            "status": "waiting_review",
        }

    # 重建 Scheduler 任务（resume_checkpoint 续跑，CHG-08）
    from orchestration_platform.engine.scheduler import Scheduler

    callback = NodeEventCallbackImpl(_instance_registry)
    scheduler = Scheduler(
        executor_registry=_executor_registry,
        checkpoint_repository=checkpoint_repo,
        event_callback=callback,
    )
    # M1 spawn 收口：统一提交器提交（in_process 默认等价路径）
    await _execution_submitter.submit(
        scheduler,
        rt.compiled,
        checkpoint.state_snapshot,
        instance_id,
        rt.trace_id or "",
        _instance_registry,
        resume_checkpoint=checkpoint,
        rt=rt,
    )

    return {
        "instance_id": instance_id,
        "resumed_from_checkpoint_id": checkpoint.checkpoint_id,
        "status": "running",
    }
