# =============================================================================
# 执行 Worker（worker/execution_worker.py）
#
# 职责：独立 worker 进程——认领执行队列作业、心跳续租、执行工作流、
#       complete/fail 终态流转；启动期接管租约过期作业；SIGTERM 优雅停机。
# 设计意图（roadmap-v3 中期窗口 M2，F1 执行无状态化）：
#   1. 主循环（认领 → 执行 → 终态）与 API 进程完全解耦——API 只入队，
#      执行全部发生在 worker 进程（多副本/滚动升级不丢任务的前提）；
#   2. 接管续跑（at-least-once 诚实口径）：kill -9 无任何清理，租约到期后
#      由存活 worker 启动扫描/轮询 take_over_expired 接管；续跑点 =
#      resume_checkpoint_id（显式恢复流）或 latest_for_instance（被接管，
#      调度器逐节点检查点使重放范围仅被杀时刻执行中单节点）；
#   3. 优雅停机 = 停止认领 + 宽限期（graceful_shutdown_seconds）内等待当前
#      作业；超时停止心跳续租（不 cancel 运行任务——CancelledError 会把实例
#      置 cancelled 与接管续跑冲突），租约到期由他 worker 接管；
#   4. 事件持久化（读穿透数据源）：PersistingInstanceRegistry 注册表 +
#      事件仓储双写，worker 模式下 API 进程 GET/SSE 经事件仓储回读。
# =============================================================================

import asyncio
import logging
import os
import signal
import socket
import time
import uuid
from typing import Any, Dict, Optional, Set

from orchestration_platform.application.execution_runner import (
    InstanceRegistry,
    InstanceRuntime,
    NodeEventCallbackImpl,
    _acquire_checkpoint_repository,
    _build_executor_registry,
    run_and_record,
)
from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.models.enums import EventType
from orchestration_platform.domain.models.execution_event import ExecutionEvent
from orchestration_platform.domain.models.execution_job import ExecutionJob
from orchestration_platform.domain.models.shared_state import SharedState
from orchestration_platform.engine.scheduler import Scheduler
from orchestration_platform.persistence.abstractions.execution_queue import (
    IExecutionQueue,
)

logger = logging.getLogger(__name__)


# =============================================================================
# 持久化注册表：注册表 + 事件仓储双写（读穿透数据源，design D1）
# =============================================================================

class PersistingInstanceRegistry(InstanceRegistry):
    """注册表 + 事件仓储双写：worker 模式事件持久化供 API 读穿透。

    run_and_record / NodeEventCallbackImpl 的全部事件流经
    InstanceRegistry.append_event（实例级直写 + 节点级经回调），子类覆写
    追加点实现双写：super（worker 本地注册表，run_and_record 内部依赖）
    + 事件仓储追加（create_task 异步化 + 实例级锁串行化，保证 sequence
    递增序 = 事件发生序）。持久化失败仅告警不阻断执行（INV-01）。
    """

    # 注册表事件类型（UPPERCASE 字符串）→ EventType 枚举持久化载体；
    # INSTANCE_CANCELLED 不映射：worker 模式不触发取消端点（M2 边界）
    _INSTANCE_EVENT_MAP: Dict[str, EventType] = {
        "INSTANCE_STARTED": EventType.INSTANCE_STARTED,
        "INSTANCE_COMPLETED": EventType.INSTANCE_COMPLETED,
        "INSTANCE_FAILED": EventType.INSTANCE_FAILED,
        "INSTANCE_SUSPENDED": EventType.INSTANCE_SUSPENDED,
    }
    # NODE_COMPLETED（注册表口径）与 NODE_SUCCEEDED（领域枚举口径）同义，
    # 读穿透侧反映射还原注册表形态（design D1）
    _NODE_EVENT_MAP: Dict[str, EventType] = {
        "NODE_STARTED": EventType.NODE_STARTED,
        "NODE_COMPLETED": EventType.NODE_SUCCEEDED,
        "NODE_FAILED": EventType.NODE_FAILED,
    }

    def __init__(self, event_repository) -> None:
        super().__init__()
        self._event_repo = event_repository
        self._persist_lock = asyncio.Lock()
        self._pending_persists: Set[asyncio.Task] = set()

    def append_event(self, instance_id: str, event: Dict[str, Any]) -> None:
        super().append_event(instance_id, event)
        mapped = self._INSTANCE_EVENT_MAP.get(
            str(event.get("event_type", ""))
        ) or self._NODE_EVENT_MAP.get(str(event.get("event_type", "")))
        if mapped is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning(
                "事件持久化跳过（无运行事件循环）: instance_id=%s",
                instance_id,
            )
            return
        task = loop.create_task(self._persist_event(instance_id, event, mapped))
        self._pending_persists.add(task)
        task.add_done_callback(self._pending_persists.discard)

    async def _persist_event(
        self, instance_id: str, event: Dict[str, Any], mapped: EventType
    ) -> None:
        """单事件持久化：锁串行化保证仓储 sequence 与发生序一致。"""
        async with self._persist_lock:
            try:
                payload = dict(event.get("payload") or {})
                # 实例级事件富化 definition_id（读穿透快照字段；PG 读侧
                # payload 既有丢失 → 该字段 local_file 后端可靠，deploy.md 口径）
                if mapped in self._INSTANCE_EVENT_MAP.values():
                    rt = self._instances.get(instance_id)
                    if rt is not None:
                        payload.setdefault(
                            "definition_id", rt.definition_id or ""
                        )
                await self._event_repo.append(
                    ExecutionEvent(
                        event_id=str(
                            event.get("event_id") or uuid.uuid4()
                        ),
                        instance_id=instance_id,
                        event_type=mapped,
                        node_id=event.get("node_id"),
                        sequence=0,
                        payload=payload,
                    )
                )
            except Exception:
                logger.warning(
                    "事件持久化失败（读穿透将缺失该事件）: "
                    "instance_id=%s, event_type=%s",
                    instance_id,
                    mapped.value,
                    exc_info=True,
                )

    async def flush(self) -> None:
        """收口：等待在途持久化任务全部落账（作业 complete/fail 前调用）。"""
        if self._pending_persists:
            await asyncio.gather(
                *list(self._pending_persists), return_exceptions=True
            )


# =============================================================================
# 作业执行：上下文重建 + run_and_record（design D3 续跑点解析）
# =============================================================================

class WorkerJobReconstructionError(Exception):
    """作业上下文重建失败（定义缺失/解析失败等，作业不可恢复）。"""


async def run_claimed_job(
    job: ExecutionJob,
    *,
    registry: InstanceRegistry,
    executor_registry,
    checkpoint_repository=None,
    workflow_repository=None,
) -> bool:
    """执行单个认领作业：重建上下文 → run_and_record。

    返回 True = 运行完成（工作流任意结局均算作业完成——succeeded/failed/
    waiting_review 由 run_and_record 落注册表与事件仓储）；抛出异常 =
    重建/执行链基础设施失败（调用方按 queue.fail 流转）。
    """
    from orchestration_platform.workflow.yaml_workflow_parser import (
        YamlWorkflowParser,
    )
    from orchestration_platform.engine.graph_compiler import GraphCompiler

    # 1. 定义解析：yaml_text 优先（API 提交携带）；缺省从工作流仓储
    #    按 definition_id + version 取（cron 等仓储定义流）
    if job.yaml_text:
        definition = YamlWorkflowParser().parse(job.yaml_text)
    else:
        if workflow_repository is None:
            from orchestration_platform.persistence.repository_factory import (
                RepositoryFactory,
            )

            workflow_repository = RepositoryFactory.create_workflow_repository()
        try:
            definition = await workflow_repository.get(
                job.definition_id, job.definition_version
            )
        except Exception as exc:
            raise WorkerJobReconstructionError(
                f"工作流定义获取失败: {job.definition_id}"
                f"@{job.definition_version}: {exc}"
            ) from exc
    compiled = await GraphCompiler().compile(definition)

    # 2. 注册表登记（worker 本地 + 事件仓储双写）
    registry.register(
        InstanceRuntime(
            instance_id=job.instance_id,
            definition_id=job.definition_id,
            tenant_id=job.tenant_id,
        )
    )

    # 3. 初始状态 + 续跑点解析（design D3）
    initial_state = SharedState(
        instance_id=job.instance_id,
        node_outputs=dict(job.initial_inputs or {}),
    )
    resume_checkpoint = None
    if checkpoint_repository is not None:
        if job.resume_checkpoint_id:
            resume_checkpoint = await checkpoint_repository.get(
                job.resume_checkpoint_id
            )
            if resume_checkpoint is None:
                logger.warning(
                    "显式恢复检查点不存在，从初始输入重跑（at-least-once）: "
                    "job_id=%s, checkpoint_id=%s",
                    job.job_id,
                    job.resume_checkpoint_id,
                )
        elif job.attempts > 1:
            # 被接管作业：从最新检查点续跑（重放范围仅被杀时刻执行中单节点）
            resume_checkpoint = await checkpoint_repository.latest_for_instance(
                job.instance_id
            )

    # 4. 调度器（worker 侧与 API start_instance 同构装配）
    scheduler = Scheduler(
        executor_registry=executor_registry,
        checkpoint_repository=checkpoint_repository,
        event_callback=NodeEventCallbackImpl(registry),
    )

    # 5. 执行并记录（run_and_record 内部处理 succeeded/failed/waiting_review）
    await run_and_record(
        scheduler,
        compiled,
        initial_state,
        job.instance_id,
        job.trace_id or str(uuid.uuid4()),
        registry,
        resume_checkpoint=resume_checkpoint,
        trigger_source=job.trigger_source or "manual",
    )
    return True


# =============================================================================
# Worker 主类：认领循环 + 心跳 + 接管扫描 + 优雅停机（design D4）
# =============================================================================

class ExecutionWorker:
    """执行 worker：认领执行队列作业并执行，支持接管与优雅停机。"""

    def __init__(
        self,
        queue: IExecutionQueue,
        *,
        worker_id: Optional[str] = None,
        executor_registry=None,
        checkpoint_repository=None,
        event_repository=None,
        workflow_repository=None,
        registry: Optional[InstanceRegistry] = None,
        lease_ttl_seconds: Optional[int] = None,
        heartbeat_seconds: Optional[float] = None,
        poll_interval_seconds: Optional[float] = None,
        graceful_shutdown_seconds: Optional[float] = None,
    ) -> None:
        settings = get_settings()
        self._queue = queue
        self._worker_id = worker_id or (
            f"worker-{socket.gethostname()}-{os.getpid()}"
        )
        self._executor_registry = executor_registry
        self._checkpoint_repository = checkpoint_repository
        self._event_repository = event_repository
        self._workflow_repository = workflow_repository
        self._registry = registry
        self._lease_ttl = (
            lease_ttl_seconds
            if lease_ttl_seconds is not None
            else settings.execution_queue_lease_seconds
        )
        self._heartbeat_seconds = (
            heartbeat_seconds
            if heartbeat_seconds is not None
            else settings.execution_queue_heartbeat_seconds
        )
        self._poll_interval = (
            poll_interval_seconds
            if poll_interval_seconds is not None
            else settings.execution_queue_poll_interval_seconds
        )
        self._graceful_shutdown_seconds = (
            graceful_shutdown_seconds
            if graceful_shutdown_seconds is not None
            else settings.execution_queue_graceful_shutdown_seconds
        )
        self._stop_requested = False
        self._grace_deadline: Optional[float] = None

    @property
    def worker_id(self) -> str:
        return self._worker_id

    def request_stop(self) -> None:
        """请求停机：停止认领；宽限截止后心跳停续（租约到期由接管兜底）。"""
        self._stop_requested = True
        self._grace_deadline = (
            time.monotonic() + self._graceful_shutdown_seconds
        )

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------
    async def run_forever(self) -> None:
        """主循环：启动接管扫描 → 认领/接管轮询循环。"""
        logger.info(
            "worker 启动: worker_id=%s, lease_ttl=%ss, heartbeat=%ss, "
            "poll=%ss",
            self._worker_id,
            self._lease_ttl,
            self._heartbeat_seconds,
            self._poll_interval,
        )
        # 启动接管扫描（plan M2：take_over_expired 循环至 None）
        while not self._stop_requested:
            job = await self._queue.take_over_expired(
                self._worker_id, lease_ttl_seconds=self._lease_ttl
            )
            if job is None:
                break
            logger.info(
                "启动接管过期作业: job_id=%s, instance_id=%s, attempts=%s",
                job.job_id,
                job.instance_id,
                job.attempts,
            )
            await self.process_job(job)
        # 认领循环：claim 优先（新作业），miss 后尝试接管过期作业
        while not self._stop_requested:
            job = await self._queue.claim(
                self._worker_id, lease_ttl_seconds=self._lease_ttl
            )
            if job is None:
                job = await self._queue.take_over_expired(
                    self._worker_id, lease_ttl_seconds=self._lease_ttl
                )
            if job is None:
                await asyncio.sleep(self._poll_interval)
                continue
            await self.process_job(job)
        logger.info("worker 停机完成: worker_id=%s", self._worker_id)

    async def process_job(self, job: ExecutionJob) -> None:
        """处理单个作业：心跳 + 执行 + 持久化收口 + 终态流转。"""
        heartbeat_task = asyncio.create_task(self._heartbeat_loop(job))
        ok = False
        reason = ""
        try:
            ok = await self._run_job(job)
        except asyncio.CancelledError:
            heartbeat_task.cancel()
            raise
        except Exception as exc:
            ok = False
            reason = f"{type(exc).__name__}: {exc}"
            logger.error(
                "作业执行失败: job_id=%s, instance_id=%s, reason=%s",
                job.job_id,
                job.instance_id,
                reason,
                exc_info=True,
            )
        finally:
            heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass
        # 持久化收口：事件全部落账后再流转终态（读穿透一致性）
        if isinstance(self._registry, PersistingInstanceRegistry):
            await self._registry.flush()
        if ok:
            completed = await self._queue.complete(
                job.job_id, self._worker_id
            )
            if not completed:
                logger.warning(
                    "作业完成流转失败（租约易主或已终态）: job_id=%s",
                    job.job_id,
                )
            else:
                logger.info(
                    "作业完成: job_id=%s, instance_id=%s",
                    job.job_id,
                    job.instance_id,
                )
        else:
            failed = await self._queue.fail(
                job.job_id, self._worker_id, reason
            )
            if not failed:
                logger.warning(
                    "作业失败流转失败（租约易主或已终态）: job_id=%s",
                    job.job_id,
                )
            else:
                logger.info(
                    "作业失败: job_id=%s, instance_id=%s, reason=%s",
                    job.job_id,
                    job.instance_id,
                    reason,
                )

    async def _run_job(self, job: ExecutionJob) -> bool:
        """执行作业：惰性构造执行器注册表/检查点仓储/持久化注册表。"""
        if self._executor_registry is None:
            self._executor_registry = _build_executor_registry()
        if self._checkpoint_repository is None:
            self._checkpoint_repository = _acquire_checkpoint_repository()
        if self._registry is None:
            if self._event_repository is None:
                try:
                    from orchestration_platform.persistence.repository_factory import (
                        RepositoryFactory,
                    )

                    self._event_repository = (
                        RepositoryFactory.create_event_repository()
                    )
                except Exception:
                    logger.warning(
                        "事件仓储获取失败，读穿透事件不持久化（降级）",
                        exc_info=True,
                    )
            if self._event_repository is not None:
                self._registry = PersistingInstanceRegistry(
                    self._event_repository
                )
            else:
                self._registry = InstanceRegistry()
        return await run_claimed_job(
            job,
            registry=self._registry,
            executor_registry=self._executor_registry,
            checkpoint_repository=self._checkpoint_repository,
            workflow_repository=self._workflow_repository,
        )

    async def _heartbeat_loop(self, job: ExecutionJob) -> None:
        """心跳续租：停机宽限超时停止续租（租约到期由接管兜底，design D4）。"""
        while True:
            await asyncio.sleep(self._heartbeat_seconds)
            if (
                self._stop_requested
                and self._grace_deadline is not None
                and time.monotonic() >= self._grace_deadline
            ):
                logger.info(
                    "优雅停机宽限超时，停止续租（租约到期待接管）: job_id=%s",
                    job.job_id,
                )
                return
            renewed = await self._queue.heartbeat(
                job.job_id, self._worker_id
            )
            if not renewed:
                logger.warning(
                    "心跳续租失败（租约易主或作业非 running）: job_id=%s",
                    job.job_id,
                )
                return


# =============================================================================
# 进程入口
# =============================================================================

def _install_signal_handlers(worker: ExecutionWorker) -> None:
    """安装 SIGTERM/SIGINT 优雅停机信号（Windows 降级 signal.signal）。"""
    loop = asyncio.get_running_loop()

    def _on_signal() -> None:
        logger.info("收到停机信号，请求优雅停机")
        worker.request_stop()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _on_signal)
        except (NotImplementedError, RuntimeError, AttributeError):
            # Windows ProactorEventLoop 不支持 add_signal_handler
            try:
                signal.signal(
                    sig,
                    lambda s, f: loop.call_soon_threadsafe(worker.request_stop),
                )
            except (ValueError, OSError):
                logger.warning("信号 %s 安装失败（非主线程？）", sig)


async def _run_worker_forever(worker: ExecutionWorker) -> None:
    _install_signal_handlers(worker)
    await worker.run_forever()


def main() -> None:
    """python -m orchestration_platform.worker 进程入口。"""
    from orchestration_platform.application.execution_runner import (
        build_execution_queue,
    )

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [worker] %(name)s: %(message)s",
    )
    queue = build_execution_queue()
    worker = ExecutionWorker(queue)
    asyncio.run(_run_worker_forever(worker))
