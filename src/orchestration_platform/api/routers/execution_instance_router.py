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
    record_instance_cancelled_event,
    resolve_event_reader,
)
from orchestration_platform.application.engine_wiring import build_engine_settings
from orchestration_platform.application.execution_runner import (
    ExecutionSubmitter,  # noqa: F401  # 再导出：测试/装配经本路由模块消费
    InstanceRegistry,  # noqa: F401  # 再导出：测试/装配经本路由模块消费
    InstanceRuntime,
    NodeEventCallbackImpl,
    _TERMINAL_STATUSES,
    _acquire_checkpoint_repository,
    _build_executor_registry,  # noqa: F401  # 再导出：测试/装配经本路由模块消费
    _build_llm_client,  # noqa: F401  # 再导出：测试/装配经本路由模块消费
    _build_tool_invokers,  # noqa: F401  # 再导出：测试/装配经本路由模块消费
    _execution_submitter,
    _executor_registry,
    _instance_registry,
    _platform_tool_get_instance,  # noqa: F401  # 再导出：测试/装配经本路由模块消费
    _platform_tool_get_workflow,  # noqa: F401  # 再导出：测试/装配经本路由模块消费
    _platform_tool_validate_workflow,  # noqa: F401  # 再导出：测试/装配经本路由模块消费
    _publish_instance_domain_event,  # noqa: F401  # 再导出：测试/装配经本路由模块消费
    _read_mcp_market_registrations,  # noqa: F401  # 再导出：测试/装配经本路由模块消费
    _run_coro_sync_for_assembly,  # noqa: F401  # 再导出：测试/装配经本路由模块消费
    _tool_invokers,  # noqa: F401  # 再导出：测试/装配经本路由模块消费
    bind_domain_event_publisher_source,
    build_execution_queue,
    run_and_record,  # noqa: F401  # 再导出：测试/装配经本路由模块消费
)
from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.errors import (
    INSTANCE_CANCEL_CONFLICT,
    STATE_CHECKPOINT_CORRUPTED,
    DomainError,
    InstanceStateConflictError,
    ResourceNotFoundError,
)
from orchestration_platform.domain.models.enums import ControlFlowKind, NodeType
from orchestration_platform.domain.models.execution_job import ExecutionJob
from orchestration_platform.domain.models.shared_state import SharedState
from orchestration_platform.engine.abstractions.domain_event_publisher import (
    IDomainEventPublisher,
)
from orchestration_platform.governance.metering.usage_recorder import (
    get_usage_recorder,
)
from orchestration_platform.governance.tenant.quota_gate import (
    get_quota_gate,
)
from orchestration_platform.observability.run_log.fields import (
    BUSINESS_CATEGORY_DEFAULT,
)
from orchestration_platform.persistence.abstractions.execution_queue import (
    IExecutionQueue,
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
    """启动工作流实例：解析 YAML → 编译 → 异步调度 → 返回 instance_id。

    执行入口双轨（B11 定义版本与发布态，design D9）：
      显式轨——yaml_text 权威（前端画布路径零变化；同传 definition_id
      仅元数据，不参与解析）；
      引用轨——无 yaml_text 时按 definition_id 仓储解析：显式
      definition_version 精确取（不校验发布态——显式版本号即意图），
      缺省取最新已发布版本（草稿不可上线；无已发布版本 422）。
    """
    if not body.yaml_text and not body.definition_id:
        raise HTTPException(
            status_code=422,
            detail="请提供 yaml_text（工作流 YAML 文本）或 definition_id"
            "（已保存定义标识；缺省解析其最新已发布版本）",
        )

    instance_id = str(uuid.uuid4())
    trace_id = str(uuid.uuid4())

    # 租户快照（T7.3）：启动瞬间取值，后台协程存活期不依赖请求对象（INV-11）
    tenant_id: Optional[str] = getattr(request.state, "tenant_id", None)

    # 并发配额闸门（roadmap B4，DC-B4-02）：并发活跃实例数达配额上限即
    # 429 拒绝（TenantQuotaExceededError → handler 映射）；拒绝路径零副作用：
    # 不注册、不解析、不编译、不执行。tenant_id 为 None（治理降级/cron
    # 无租户）不闸门。in_process 计数与校验间无 await（事件循环单线程原子，
    # 超配额 100% 拒绝）；M5 worker 口径（design D6）：worker 模式 API 注册表
    # 为空（提交不注册），计数走队列落库口径（pending/running 作业数）——
    # count→check→enqueue 跨 await 的准入竞态与既有窗口同类（best-effort
    # 诚实口径，deploy.md 披露）。
    worker_mode = get_settings().execution_mode == "worker"
    quota_gate = get_quota_gate()
    if quota_gate is not None and tenant_id is not None:
        if worker_mode:
            active_count = await _count_active_jobs_by_tenant(tenant_id)
        else:
            active_count = _instance_registry.count_active_by_tenant(tenant_id)
        await quota_gate.ensure_capacity(tenant_id, active_count)

    rt = InstanceRuntime(
        instance_id=instance_id,
        definition_id=body.definition_id,
        tenant_id=tenant_id,
    )
    # M5 陈旧注册表消除（design D2）：worker 模式不注册——API 进程注册表
    # 保持空（M2 design D5 的 404 窗口径成立，读穿透对全端点生效）；
    # rt 仍构造并传给 submit（worker 分支读 tenant_id/definition_id；
    # 提交侧落 INSTANCE_QUEUED 后窗口期 GET 推导 pending，不再 404）。
    if not worker_mode:
        _instance_registry.register(rt)

    try:
        from orchestration_platform.workflow.yaml_workflow_parser import YamlWorkflowParser
        from orchestration_platform.engine.graph_compiler import GraphCompiler

        # 执行入口双轨解析（B11 design D9）：显式轨 yaml_text 权威；
        # 引用轨仓储解析（显式版本精确取 / 缺省最新已发布版本）。
        if body.yaml_text:
            definition = YamlWorkflowParser().parse(body.yaml_text)
        else:
            from orchestration_platform.persistence.repository_factory import (
                RepositoryFactory,
            )

            workflow_repo = RepositoryFactory.create_workflow_repository()
            if body.definition_version is not None:
                definition = await workflow_repo.get(
                    body.definition_id, body.definition_version
                )
            else:
                definition = await workflow_repo.get_published(
                    body.definition_id
                )
                if definition is None:
                    raise HTTPException(
                        status_code=422,
                        detail=f"工作流定义 {body.definition_id} 尚无已发布版本，"
                        "无法启动实例（引用轨缺省解析最新已发布版本；"
                        "可显式指定 definition_version 启动任意既有版本）",
                    )
        compiled = await GraphCompiler(
            settings=build_engine_settings()
        ).compile(definition)
        # 恢复上下文缓存（arch_review_v2 3.4，CHG-07）：编译产物与 trace 落注册表
        rt.compiled = compiled
        rt.trace_id = trace_id
        # 版本追溯通道（B11 design D9）：rt 随行编译产物版本（引用轨 =
        # 仓储解析版本；yaml_text 轨 = YAML definition_version），
        # GET /instances/{id} 注册表路径随行读出
        rt.definition_version = (
            getattr(compiled, "definition_version", None)
            or definition.definition_version
            or body.definition_version
        )
    except HTTPException:
        _instance_registry.remove(instance_id)
        raise
    except ResourceNotFoundError as exc:
        # 引用轨显式版本不存在（B11 design D9）：404（与通用 422 严格
        # 区分——定义缺失 vs 解析/编译失败）
        _instance_registry.remove(instance_id)
        raise HTTPException(status_code=404, detail=exc.to_dict())
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

        # 构造初始共享状态（DC-1，INV-1；B12-D7.1 零拷贝播种——免验证构造
        # 消除 JsonValue 深验证的整份输入复制）
        try:
            initial_state = SharedState.for_seeding(
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
            settings=build_engine_settings(),
            usage_recorder=get_usage_recorder(),
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
    # 版本追溯（B11 design D9）：注册表路径随行版本（worker 读穿透路径
    # 经 read_instance_snapshot 从事件载荷提取，双路径同键）
    if rt.definition_version is not None:
        result["definition_version"] = rt.definition_version
    if rt.finished_at:
        result["finished_at"] = rt.finished_at
    return result


@router.get("/instances/{instance_id}/events")
async def get_instance_events(instance_id: str, include_voided: bool = True) -> list:
    """查询实例事件序列。

    M5 worker 读穿透（design D7）：注册表未命中 → 统一读取器回退事件
    仓储（worker 进程落账的事件流）；均未命中如实 404。include_voided
    两路径一致忽略（历史契约：注册表路径不筛除，读穿透路径沿用
    list_for_instance 缺省全量）。
    """
    try:
        return _instance_registry.get_events(instance_id)
    except HTTPException:
        reader = await resolve_event_reader(
            instance_id, registry=_instance_registry
        )
        return await reader.read_events(instance_id)


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


# =============================================================================
# M3 worker 模式取消（design D5 读穿透三态 + 盲区事件）
# =============================================================================

_execution_queue_instance: Optional[IExecutionQueue] = None
_execution_queue_failed = False


def _get_execution_queue() -> Optional[IExecutionQueue]:
    """惰性构造执行队列（cancel worker 路径；测试经 monkeypatch 注入）。

    经 build_execution_queue 按 storage_backend 构造（延迟导入，零新增
    依赖）；失败降级 None + 告警（worker 取消不可用，404 口径兜底）。
    """
    global _execution_queue_instance, _execution_queue_failed
    if _execution_queue_instance is not None:
        return _execution_queue_instance
    if _execution_queue_failed:
        return None
    try:
        _execution_queue_instance = build_execution_queue()
        return _execution_queue_instance
    except Exception:
        _execution_queue_failed = True
        logging.warning("执行队列构造失败，worker 模式取消不可用", exc_info=True)
        return None


async def _count_active_jobs_by_tenant(tenant_id: str) -> int:
    """worker 模式配额计数：队列落库口径（M5 design D6）。

    队列构造/查询失败降级回注册表计数并告警（可用性优先：worker 模式
    注册表恒空 → 降级计数 ≈ 0，配额近似旁路——deploy.md 诚实披露）。
    """
    queue = _get_execution_queue()
    if queue is None:
        return _instance_registry.count_active_by_tenant(tenant_id)
    try:
        return await queue.count_active_by_tenant(tenant_id)
    except Exception:
        logging.warning(
            "队列配额计数失败，降级注册表计数: tenant_id=%s",
            tenant_id,
            exc_info=True,
        )
        return _instance_registry.count_active_by_tenant(tenant_id)


async def _cancel_worker_instance(instance_id: str) -> Optional[dict]:
    """worker 模式取消：队列标志置位 + 盲区事件落账（design D5 三态）。

    - 活跃作业（pending/running）→ request_cancel 幂等置标志：
      · pending（未认领盲区）→ 端点侧补 INSTANCE_CANCELLED 事件
        （GET/SSE 立即可见 cancelled；worker 认领时标志检查不执行直接
        收口，不重复落事件）；响应为事件推导快照（cancelled）；
      · running → 仅置标志（取消时延上界 = worker 心跳间隔；终态事件
        由 runner CancelledError 分支落账）；响应为事件推导快照；
    - 无活跃作业：事件可见且非可取消态（终态/waiting_review）→ 409
      INSTANCE_CANCEL_CONFLICT（镜像 in_process 语义）；事件不可见 →
      返回 None（调用方维持 404）。
    """
    queue = _get_execution_queue()
    if queue is None:
        return None
    job = await queue.find_active_by_instance(instance_id)
    if job is None:
        snapshot = await read_instance_snapshot(instance_id)
        if snapshot is None:
            return None
        status = str(snapshot.get("status", ""))
        if status in _TERMINAL_STATUSES or status == "waiting_review":
            raise InstanceStateConflictError(
                INSTANCE_CANCEL_CONFLICT,
                f"实例 {instance_id} 当前无可取消的运行任务（状态 {status}）",
            )
        return None

    await queue.request_cancel(job.job_id)
    if job.status == "pending":
        await record_instance_cancelled_event(
            instance_id, definition_id=job.definition_id
        )
        snapshot = await read_instance_snapshot(instance_id)
        if snapshot is not None:
            return snapshot
        return {
            "instance_id": instance_id,
            "definition_id": job.definition_id,
            "status": "cancelled",
            "started_at": None,
        }
    snapshot = await read_instance_snapshot(instance_id)
    if snapshot is not None:
        return snapshot
    return {
        "instance_id": instance_id,
        "definition_id": job.definition_id,
        "status": job.status,
        "started_at": None,
    }


@router.post("/instances/{instance_id}/cancel")
async def cancel_instance(instance_id: str) -> dict:
    """请求取消执行中的实例（B2 嵌入 SDK：取消能力后端落点）。

    契约（docs/sdk-contract.md §1-7）：
    - 未知实例 → 404（注册表/读穿透既有 detail）；已终态或无可取消任务
      （task 缺失/已结束，含 waiting_review 挂起态）→ 409
      INSTANCE_CANCEL_CONFLICT（{code, message, details} 同构错误体）；
    - 运行中 → task.cancel() 投递取消，终态由 run_and_record 的
      CancelledError 分支落定（本响应不等待，返回请求时刻快照）；
    - 竞态容忍：任务恰在校验后完成时 task.cancel() 为 no-op，
      客户端以 GET /instances/{id} 轮询到的终态为准。

    M3 worker 模式扩展（design D5）：注册表未命中（实例执行于独立
    worker 进程）→ 队列标志置位传播取消（取消时延上界 = 心跳间隔）；
    pending 作业盲区取消补 INSTANCE_CANCELLED 事件（GET/SSE 立即可见）。
    in_process 注册表路径零变化（design D6 红线）。
    """
    try:
        rt = _instance_registry.get(instance_id)
    except HTTPException:
        result = await _cancel_worker_instance(instance_id)
        if result is not None:
            return result
        raise

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


async def _read_worker_node_outputs(instance_id: str) -> Dict[str, Any]:
    """worker 模式 succeeded 输出读取：检查点快照 node_outputs（M5 D7）。

    检查点快照是唯一权威输出存储（INV-08 口径——注册表 final_state 为
    进程内存态，worker 执行实例的输出仅存在于检查点快照）；仓储不可达/
    无检查点/读取失败 → {} + 告警（镜像 in_process「重启后老实例 → {}」
    的可用性优先口径）。
    """
    checkpoint_repo = _acquire_checkpoint_repository()
    if checkpoint_repo is None:
        logging.warning(
            "检查点仓储不可用，worker 模式结果输出回退空: instance_id=%s",
            instance_id,
        )
        return {}
    try:
        checkpoint = await checkpoint_repo.latest_for_instance(instance_id)
    except Exception:
        logging.warning(
            "检查点快照读取失败，worker 模式结果输出回退空: instance_id=%s",
            instance_id,
            exc_info=True,
        )
        return {}
    if checkpoint is None:
        logging.warning(
            "实例无可用检查点快照，worker 模式结果输出回退空: instance_id=%s",
            instance_id,
        )
        return {}
    node_outputs = getattr(
        getattr(checkpoint, "state_snapshot", None), "node_outputs", None
    )
    if isinstance(node_outputs, dict):
        return dict(node_outputs)
    return {}


async def _results_worker_instance(instance_id: str) -> Optional[dict]:
    """worker 模式结果读穿透：事件推导快照 + 检查点快照输出（M5 D7）。

    非终态 → {outputs: None}（轮询友好契约）；succeeded → 最新检查点
    state_snapshot.node_outputs；其余终态 → {}（镜像 in_process 仅
    succeeded 携带输出）；finished_at 取快照留痕时刻。快照不可见 →
    None（调用方维持 404）。
    """
    snapshot = await read_instance_snapshot(instance_id)
    if snapshot is None:
        return None
    status = str(snapshot.get("status", ""))
    if status not in _TERMINAL_STATUSES:
        return {
            "instance_id": instance_id,
            "status": status,
            "outputs": None,
        }
    outputs: Dict[str, Any] = {}
    if status == "succeeded":
        outputs = await _read_worker_node_outputs(instance_id)
    return {
        "instance_id": instance_id,
        "status": status,
        "outputs": outputs,
        "finished_at": snapshot.get("finished_at"),
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

    M5 worker 读穿透（design D7）：注册表未命中 → 事件推导快照分支；
    succeeded 输出取检查点仓储最新快照 node_outputs（检查点快照是唯一
    权威输出存储，INV-08 口径），失败回退 {} 告警——与 in_process
    「重启后老实例 → {}」同构。
    """
    try:
        rt = _instance_registry.get(instance_id)
    except HTTPException:
        result = await _results_worker_instance(instance_id)
        if result is not None:
            return result
        raise

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
            settings=build_engine_settings(),
            usage_recorder=get_usage_recorder(),
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


async def _enqueue_worker_resume_job(
    instance_id: str,
    checkpoint_id: str,
    *,
    definition_id: str,
    trigger_source: str,
    idempotency_scope: str,
) -> None:
    """worker 模式续跑作业入队（M5 design D8；/recover 与 /review 共用）。

    直接 ExecutionJob 构造 + queue.enqueue（不经 ExecutionSubmitter.
    submit——不落 INSTANCE_QUEUED：实例已在事件流可见，非新提交窗口）。
    tenant_id/definition_version/yaml_text 取 find_latest_by_instance
    （origin 作业，T7 载荷补齐——worker 重建上下文正源）；无历史作业时
    载荷降级告警（yaml_text=None → worker 侧按 definition_id 工作流
    仓储兜底，版本 None → 最新版本，保真降级入档）。幂等键 =
    {instance_id}:{idempotency_scope}:{checkpoint_id}（确定性新键——
    规避 v1_8 全局唯一索引与原作业 instance_id 键冲突；同键重入队
    幂等：local_file 非终态拦截 / PG ON CONFLICT 返回既有）。
    """
    queue = _get_execution_queue()
    if queue is None:
        raise HTTPException(
            status_code=500, detail="执行队列不可用，无法续跑实例",
        )
    origin = None
    try:
        origin = await queue.find_latest_by_instance(instance_id)
    except Exception:
        logging.warning(
            "origin 作业查询失败（续跑载荷降级）: instance_id=%s",
            instance_id,
            exc_info=True,
        )
    if origin is None:
        logging.warning(
            "实例无历史队列作业，续跑作业不携带 yaml_text/版本"
            "（worker 侧按 definition_id 仓储兜底）: instance_id=%s",
            instance_id,
        )
    job = ExecutionJob(
        job_id=str(uuid.uuid4()),
        instance_id=instance_id,
        tenant_id=getattr(origin, "tenant_id", None),
        definition_id=definition_id,
        definition_version=getattr(origin, "definition_version", None),
        yaml_text=getattr(origin, "yaml_text", None),
        initial_inputs={},
        trigger_source=trigger_source,
        trace_id=str(getattr(origin, "trace_id", "") or ""),
        resume_checkpoint_id=checkpoint_id,
        idempotency_key=f"{instance_id}:{idempotency_scope}:{checkpoint_id}",
    )
    await queue.enqueue(job)


async def _recover_worker_instance(instance_id: str) -> Optional[dict]:
    """worker 模式恢复：快照守卫 + 检查点续跑作业入队（M5 design D7/D8）。

    - 快照不可见 → None（调用方维持 404）；
    - running → 409 仍在运行（作业在途，镜像 in_process 任务在途拒绝）；
    - waiting_review → 409「请提交审核决策」——worker 进程无法代持注册
      表挂起态（与 in_process 的重挂起分支有意分歧，deploy.md §10.3
      披露）；
    - review_timeout → 409（门决策已留痕，从门检查点续跑 = 绕过审核
      决策，安全优先拒绝；分歧同上入档）；
    - pending/终态 → 检查点续跑流：latest_for_instance（None → 500
      「无可用检查点」镜像 in_process；pending 窗口快照必无检查点——
      提交即入队未运行，500 即「无可恢复位点」的如实口径；终态重入队
      同键幂等）。
    """
    snapshot = await read_instance_snapshot(instance_id)
    if snapshot is None:
        return None
    status = str(snapshot.get("status", ""))
    if status == "running":
        raise HTTPException(
            status_code=409,
            detail=f"实例 {instance_id} 仍在运行，禁止恢复",
        )
    if status == "waiting_review":
        raise HTTPException(
            status_code=409,
            detail=f"等待审核中的实例 {instance_id} 请提交审核决策",
        )
    if status == "review_timeout":
        raise HTTPException(
            status_code=409,
            detail=(
                f"实例 {instance_id} 已按超时动作挂起（review_timeout），"
                "无法恢复"
            ),
        )
    checkpoint_repo = _acquire_checkpoint_repository()
    if checkpoint_repo is None:
        raise HTTPException(
            status_code=500, detail="检查点仓储不可用，无法恢复",
        )
    checkpoint = await checkpoint_repo.latest_for_instance(instance_id)
    if checkpoint is None:
        raise HTTPException(
            status_code=500,
            detail=f"实例 {instance_id} 无可用检查点，无法恢复",
        )
    await _enqueue_worker_resume_job(
        instance_id,
        checkpoint.checkpoint_id,
        definition_id=str(snapshot.get("definition_id", "") or ""),
        trigger_source="recover",
        idempotency_scope="recover",
    )
    return {
        "instance_id": instance_id,
        "resumed_from_checkpoint_id": checkpoint.checkpoint_id,
        "status": "running",
    }


@router.post("/instances/{instance_id}/recover")
async def recover_instance(instance_id: str) -> dict:
    """故障恢复：从最近可用检查点续跑实例（CHG-07，卡 A1 契约）。

    契约（卡 A1）：
    - 出参 {instance_id, resumed_from_checkpoint_id, status}；
    - INSTANCE_NOT_FOUND → 404；STATE_CHECKPOINT_CORRUPTED → 500（复用既有错误码）；
    - 不入 rollback_replay_router（Z-4，F4 属 W5–7 桩现状冻结）。

    M5 worker 读穿透（design D7/D8）：注册表未命中 → 快照守卫 + 检查点
    续跑作业入队（worker 认领执行）；waiting_review/review_timeout →
    409（与 in_process 的重挂起分支有意分歧，deploy.md §10.3 披露）。
    """
    try:
        rt = _instance_registry.get(instance_id)
    except HTTPException:
        result = await _recover_worker_instance(instance_id)
        if result is not None:
            return result
        raise

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
        settings=build_engine_settings(),
        usage_recorder=get_usage_recorder(),
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
