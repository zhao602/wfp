# =============================================================================
# 执行队列抽象接口（persistence/abstractions/execution_queue.py）
#
# 职责：定义执行队列的抽象接口（Protocol）——入队/认领/心跳/完成/失败/接管。
# 设计意图（roadmap-v3 中期窗口 M1，plan D2，F1 执行队列与提交路径统一）：
#   1. 接口与实现分离（对齐 repositories.py Protocol 范式）——业务层依赖
#      Protocol，由 storage_backend 分支注入 local_file / postgres 实现；
#   2. 认领原子性（两副本不重复执行，plan D3）：postgres 走
#      FOR UPDATE SKIP LOCKED；local_file 走 O_EXCL 认领标记单赢；
#   3. 租约语义：认领即持租约（默认 30s），心跳续期；租约过期的 running
#      作业可被任何 worker 接管（at-least-once，诚实口径不夸大 exactly-once）；
#   4. 已取消作业为终态，不重投（plan D4）。
# =============================================================================

from typing import Optional, Protocol, runtime_checkable

from orchestration_platform.domain.models.execution_job import ExecutionJob


@runtime_checkable
class IExecutionQueue(Protocol):
    """执行队列：作业入队、认领、租约与终态流转。"""

    async def enqueue(self, job: ExecutionJob) -> ExecutionJob:
        """入队作业，返回生效的作业条目。

        幂等语义：同 idempotency_key 的既有非终态作业存在时，
        不重复入队，返回既有作业（提交幂等，plan D3）。
        """
        ...

    async def claim(
        self, worker_id: str, lease_ttl_seconds: Optional[int] = None
    ) -> Optional[ExecutionJob]:
        """原子认领一个 pending 作业并持租约。

        置 status=running、lease_owner=worker_id、lease_expires_at=now+ttl、
        attempts+1；无 pending 作业返回 None。
        认领原子性由实现保证（SKIP LOCKED / O_EXCL 单赢）。
        """
        ...

    async def take_over_expired(
        self, worker_id: str, lease_ttl_seconds: Optional[int] = None
    ) -> Optional[ExecutionJob]:
        """接管一个租约已过期的 running 作业（M2 worker 启动扫描）。

        置 lease_owner=worker_id、lease_expires_at=now+ttl、attempts+1；
        无过期 running 作业返回 None。
        接管续跑从检查点 next_node_id 起（重放范围仅被杀时刻执行中单节点，
        plan D3 at-least-once 口径）。
        """
        ...

    async def heartbeat(self, job_id: str, worker_id: str) -> bool:
        """心跳续租：仅 lease_owner 匹配且 running 时刷新租约到期时刻。

        续租成功返回 True；作业不存在/非本 worker 持有/非 running 返回 False
        （worker 应停止执行并放弃作业，等待接管）。
        """
        ...

    async def complete(self, job_id: str, worker_id: str) -> bool:
        """作业完成流转终态（status=completed，释放租约）。

        仅 lease_owner 匹配且 running 时生效；返回是否流转成功。
        """
        ...

    async def fail(self, job_id: str, worker_id: str, reason: str = "") -> bool:
        """作业失败流转终态（status=failed + last_error，释放租约）。

        仅 lease_owner 匹配且 running 时生效；返回是否流转成功。
        """
        ...

    async def get(self, job_id: str) -> Optional[ExecutionJob]:
        """按标识查询作业；不存在返回 None（提交幂等核对/运维巡检用）。"""
        ...

    async def request_cancel(self, job_id: str) -> bool:
        """置位作业取消请求标志（M3 P5：cancel_requested=True，幂等）。

        任意状态可置位（终态置位为无害幂等）；作业不存在返回 False。
        worker 心跳轮询发现标志后取消执行（取消时延上界 = 心跳间隔）；
        认领后执行前发现的标志使 worker 不执行直接完成（design D5）。
        """
        ...

    async def find_active_by_instance(
        self, instance_id: str
    ) -> Optional[ExecutionJob]:
        """按实例标识查找唯一活跃（pending/running）作业（cancel 端点定位用）。

        无活跃作业返回 None（终态作业不返回——取消语义仅针对在途执行）。
        """
        ...
