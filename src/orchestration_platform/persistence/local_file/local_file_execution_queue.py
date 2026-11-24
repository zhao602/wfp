# =============================================================================
# 本地文件执行队列（persistence/local_file/local_file_execution_queue.py）
#
# 职责：以 JSON 文件持久化执行队列作业，保证认领原子性与租约语义。
# 设计意图（roadmap-v3 中期窗口 M1，plan D2，F1 执行队列与提交路径统一）：
#   1. 目录结构：{execution_queue_dir}/{job_id}.json（单作业单文件，JSON 条目）；
#   2. 认领原子性（两副本不重复执行，plan D3）：O_EXCL 创建
#      {job_id}.claim 认领标记——单副本单赢（Windows 下 os.open 支持
#      O_CREAT|O_EXCL），输者跳过该作业；
#   3. 原子写：tmp 文件 + os.replace（订阅注册表 subscription_registry.py:40-64
#      原子写先例；os.replace 在 Windows 上原子替换目标文件）；
#   4. 租约语义：认领即持租约（默认 30s），heartbeat 续期；
#      take_over_expired 接管租约过期的 running 作业；
#   5. 崩溃残留防御：认领中途崩溃可能残留 {job_id}.claim——后续 claim 遇
#      age 超过租约时长的陈旧标记先清理再竞争（不永久卡死）；
#   6. 测试环境（无活 PG）以本实现证明全部队列语义（plan D2）。
# =============================================================================

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.models.execution_job import (
    JOB_STATUS_PENDING,
    JOB_STATUS_RUNNING,
    ExecutionJob,
)
from orchestration_platform.persistence.abstractions.execution_queue import (
    IExecutionQueue,
)


class LocalFileExecutionQueue:
    """执行队列——本地文件实现（O_EXCL 认领单赢 + 租约时间戳比较）。"""

    def __init__(self, queue_dir: Optional[str] = None) -> None:
        """构造队列实例。

        参数：
            queue_dir —— 队列存储目录；缺省读配置 execution_queue_dir。
        """
        root = queue_dir or get_settings().execution_queue_dir
        self._dir = Path(root)
        self._dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 内部工具：文件读写与时间
    # ------------------------------------------------------------------

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _parse_iso(value: Optional[str]) -> Optional[datetime]:
        """解析 ISO8601 时间戳；空值/非法值返回 None（防御陈旧文件）。"""
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    def _job_file(self, job_id: str) -> Path:
        return self._dir / f"{job_id}.json"

    def _claim_file(self, job_id: str) -> Path:
        return self._dir / f"{job_id}.claim"

    def _load_job(self, job_id: str) -> Optional[ExecutionJob]:
        """读取单个作业文件；不存在/损坏返回 None。"""
        file_path = self._job_file(job_id)
        if not file_path.exists():
            return None
        try:
            return ExecutionJob.model_validate(
                json.loads(file_path.read_text(encoding="utf-8"))
            )
        except Exception:
            return None

    def _save_job(self, job: ExecutionJob) -> None:
        """原子写作业文件（tmp + os.replace，订阅注册表先例）。"""
        file_path = self._job_file(job.job_id)
        tmp_path = file_path.with_name(file_path.name + ".tmp")
        tmp_path.write_text(
            json.dumps(job.model_dump(mode="json"), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(tmp_path, file_path)

    def _list_jobs(self) -> List[ExecutionJob]:
        """列举全部作业（按入队时刻排序，损坏文件跳过）。"""
        jobs: List[ExecutionJob] = []
        for file_path in sorted(self._dir.glob("*.json")):
            try:
                jobs.append(
                    ExecutionJob.model_validate(
                        json.loads(file_path.read_text(encoding="utf-8"))
                    )
                )
            except Exception:
                continue
        jobs.sort(key=lambda j: (j.created_at, j.job_id))
        return jobs

    def _try_acquire_claim(self, job_id: str, worker_id: str) -> bool:
        """O_EXCL 原子创建认领标记；已存在时按陈旧阈值清理后重试一次。

        Windows/Linux 均以 os.open(path, O_CREAT|O_EXCL) 为原子单赢原语；
        陈旧阈值 = 租约时长：认领中途崩溃的残留标记超时后可清理（不永久卡死）。
        """
        claim_path = self._claim_file(job_id)
        try:
            fd = os.open(
                str(claim_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY
            )
        except FileExistsError:
            return False
        except OSError:
            # Windows 偶发共享冲突：退避重试一次
            try:
                fd = os.open(
                    str(claim_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY
                )
            except OSError:
                return False
        try:
            os.write(fd, worker_id.encode("utf-8"))
        except OSError:
            pass
        finally:
            os.close(fd)
        return True

    def _release_claim(self, job_id: str) -> None:
        """释放认领标记（认领写回完成后调用）。"""
        try:
            self._claim_file(job_id).unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass

    def _cleanup_stale_claim(self, job_id: str, stale_seconds: float) -> None:
        """清理超过租约时长的陈旧认领标记（崩溃残留防御）。"""
        claim_path = self._claim_file(job_id)
        try:
            age = self._now().timestamp() - claim_path.stat().st_mtime
            if age > stale_seconds:
                claim_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass

    # ------------------------------------------------------------------
    # IExecutionQueue 实现
    # ------------------------------------------------------------------

    def _default_ttl_seconds(self, lease_ttl_seconds: Optional[int]) -> int:
        if lease_ttl_seconds is not None:
            return lease_ttl_seconds
        return get_settings().execution_queue_lease_seconds

    async def enqueue(self, job: ExecutionJob) -> ExecutionJob:
        """入队作业（提交幂等：同 idempotency_key 非终态既有作业直接返回）。"""
        key = job.idempotency_key
        if key is not None:
            for existing in self._list_jobs():
                if (
                    existing.idempotency_key == key
                    and not existing.is_terminal()
                ):
                    return existing
        self._save_job(job)
        return job

    async def claim(
        self, worker_id: str, lease_ttl_seconds: Optional[int] = None
    ) -> Optional[ExecutionJob]:
        """原子认领一个 pending 作业并持租约（O_EXCL 单赢）。"""
        ttl = self._default_ttl_seconds(lease_ttl_seconds)
        for candidate in self._list_jobs():
            if candidate.status != JOB_STATUS_PENDING:
                continue
            # 陈旧认领标记清理（崩溃残留防御，超过租约时长视为陈旧）
            self._cleanup_stale_claim(candidate.job_id, float(ttl))
            if not self._try_acquire_claim(candidate.job_id, worker_id):
                continue
            try:
                # 双读校验：认领标记单赢后复核状态仍为 pending
                # （窗口期内被前一持有者流转终态的场景回退让出）
                latest = self._load_job(candidate.job_id)
                if latest is None or latest.status != JOB_STATUS_PENDING:
                    continue
                latest.status = JOB_STATUS_RUNNING
                latest.lease_owner = worker_id
                latest.lease_expires_at = (
                    self._now() + timedelta(seconds=ttl)
                ).isoformat()
                latest.attempts += 1
                self._save_job(latest)
                return latest
            finally:
                self._release_claim(candidate.job_id)
        return None

    async def take_over_expired(
        self, worker_id: str, lease_ttl_seconds: Optional[int] = None
    ) -> Optional[ExecutionJob]:
        """接管一个租约已过期的 running 作业（O_EXCL 单赢，attempts+1）。"""
        ttl = self._default_ttl_seconds(lease_ttl_seconds)
        now = self._now()
        for candidate in self._list_jobs():
            if candidate.status != JOB_STATUS_RUNNING:
                continue
            expires_at = self._parse_iso(candidate.lease_expires_at)
            if expires_at is None or expires_at >= now:
                continue
            if not self._try_acquire_claim(candidate.job_id, worker_id):
                continue
            try:
                # 双读校验：接管窗口内被原持有者续租或流转终态则让出
                # （lease_expires_at 变化覆盖续租与接管两种竞态）
                latest = self._load_job(candidate.job_id)
                if (
                    latest is None
                    or latest.status != JOB_STATUS_RUNNING
                    or latest.lease_expires_at != candidate.lease_expires_at
                ):
                    continue
                latest.lease_owner = worker_id
                latest.lease_expires_at = (
                    now + timedelta(seconds=ttl)
                ).isoformat()
                latest.attempts += 1
                self._save_job(latest)
                return latest
            finally:
                self._release_claim(candidate.job_id)
        return None

    async def heartbeat(
        self, job_id: str, worker_id: str, lease_ttl_seconds: Optional[int] = None
    ) -> bool:
        """心跳续租：仅 lease_owner 匹配且 running 时刷新到期时刻（D9 TTL 参）。"""
        job = self._load_job(job_id)
        if (
            job is None
            or job.status != JOB_STATUS_RUNNING
            or job.lease_owner != worker_id
        ):
            return False
        job.lease_expires_at = (
            self._now()
            + timedelta(seconds=self._default_ttl_seconds(lease_ttl_seconds))
        ).isoformat()
        self._save_job(job)
        return True

    async def complete(self, job_id: str, worker_id: str) -> bool:
        """作业完成流转终态（仅本 worker 持有且 running 时生效）。"""
        job = self._load_job(job_id)
        if (
            job is None
            or job.status != JOB_STATUS_RUNNING
            or job.lease_owner != worker_id
        ):
            return False
        job.status = "completed"
        job.lease_owner = None
        job.lease_expires_at = None
        self._save_job(job)
        return True

    async def fail(self, job_id: str, worker_id: str, reason: str = "") -> bool:
        """作业失败流转终态（仅本 worker 持有且 running 时生效）。"""
        job = self._load_job(job_id)
        if (
            job is None
            or job.status != JOB_STATUS_RUNNING
            or job.lease_owner != worker_id
        ):
            return False
        job.status = "failed"
        job.lease_owner = None
        job.lease_expires_at = None
        job.last_error = reason or None
        self._save_job(job)
        return True

    async def get(self, job_id: str) -> Optional[ExecutionJob]:
        """按标识查询作业；不存在返回 None。"""
        return self._load_job(job_id)

    async def request_cancel(self, job_id: str) -> bool:
        """置位取消请求标志（幂等；design D1——worker 心跳轮询发现后取消）。"""
        job = self._load_job(job_id)
        if job is None:
            return False
        if not job.cancel_requested:
            job.cancel_requested = True
            self._save_job(job)
        return True

    async def find_active_by_instance(
        self, instance_id: str
    ) -> Optional[ExecutionJob]:
        """按实例查找活跃（pending/running）作业，最早入队者优先。"""
        active = [
            job
            for job in self._list_jobs()
            if job.instance_id == instance_id and not job.is_terminal()
        ]
        if not active:
            return None
        active.sort(key=lambda j: (j.created_at, j.job_id))
        return active[0]

    async def count_active_by_tenant(self, tenant_id: str) -> int:
        """按租户统计活跃（pending/running）作业数（M5 配额 worker 口径）。"""
        return sum(
            1
            for job in self._list_jobs()
            if job.tenant_id == tenant_id and not job.is_terminal()
        )

    async def find_latest_by_instance(
        self, instance_id: str
    ) -> Optional[ExecutionJob]:
        """按实例查找最新（created_at, job_id 最大）作业（含终态，M5 载荷补齐）。"""
        candidates = [
            job for job in self._list_jobs() if job.instance_id == instance_id
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda j: (j.created_at, j.job_id))
        return candidates[-1]
