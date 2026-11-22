# =============================================================================
# 编排状态检查点存储（engine/supervisor_orchestration/orchestration_state_store.py）
#
# 职责：实现 IOrchestrationStateStore 三种后端（内存/本地文件/Postgres）（G1/T218）。
# 设计意图（对应 spec 5.1.5、design D1/2.1.3.2）：
#   1. 每个决策点（路由后、聚合上报后）整体序列化落快照；
#   2. 恢复语义：快照有效→续跑（幂等）；快照缺失/损坏→明确终止（SUP_STATE_RESTORE_FAILED）；
#   3. 禁止状态滞留不可中断进程内存产生孤儿执行（A1.5）；
#   4. Postgres 实现延迟导入 asyncpg，连接失败自动降级内存。
#
# 禁止 import langgraph。
# =============================================================================

import json
import os
from typing import Dict, Optional

from orchestration_platform.domain.errors import SUP_STATE_RESTORE_FAILED, DomainError
from orchestration_platform.engine.supervisor_orchestration.models import (
    OrchestrationStatus,
    SupervisorOrchestrationContext,
)


class InMemoryStateStore:
    """内存编排状态存储（默认后端，开发/测试用）。

    进程内 Map 存储，重启后丢失。满足 IOrchestrationStateStore 接口。
    """

    def __init__(self) -> None:
        self._store: Dict[str, SupervisorOrchestrationContext] = {}
        self._terminated: Dict[str, str] = {}

    @property
    def backend_name(self) -> str:
        return "memory"

    async def save(self, context: SupervisorOrchestrationContext) -> None:
        """保存编排上下文快照（内存覆盖写）。"""
        self._store[context.orchestration_id] = context

    async def load(
        self, orchestration_id: str
    ) -> Optional[SupervisorOrchestrationContext]:
        """加载编排上下文快照。不存在返回 None。"""
        return self._store.get(orchestration_id)

    async def mark_terminated(
        self, orchestration_id: str, reason: str
    ) -> None:
        """标记编排为明确终止状态。"""
        self._terminated[orchestration_id] = reason
        ctx = self._store.get(orchestration_id)
        if ctx is not None:
            ctx.status = OrchestrationStatus.TERMINATED
            ctx.touch()

    def is_terminated(self, orchestration_id: str) -> bool:
        """检查是否已被标记终止。"""
        return orchestration_id in self._terminated


class LocalFileStateStore:
    """本地文件编排状态存储。

    快照 JSON 落盘 ./data/supervisor_orchestration/checkpoints/。
    满足 IOrchestrationStateStore 接口。
    """

    def __init__(self, root_dir: str = "./.data/supervisor_orchestration/checkpoints") -> None:
        self._root = root_dir
        os.makedirs(self._root, exist_ok=True)

    @property
    def backend_name(self) -> str:
        return "local_file"

    def _path(self, orchestration_id: str) -> str:
        return os.path.join(self._root, f"{orchestration_id}.json")

    async def save(self, context: SupervisorOrchestrationContext) -> None:
        """保存编排上下文快照（JSON 落盘）。"""
        data = context.model_dump_json(indent=2)
        with open(self._path(context.orchestration_id), "w", encoding="utf-8") as f:
            f.write(data)

    async def load(
        self, orchestration_id: str
    ) -> Optional[SupervisorOrchestrationContext]:
        """加载编排上下文快照。

        文件不存在返回 None；文件损坏抛 DomainError(SUP_STATE_RESTORE_FAILED)。
        """
        path = self._path(orchestration_id)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = f.read()
            return SupervisorOrchestrationContext.model_validate_json(raw)
        except Exception as exc:
            raise DomainError(
                SUP_STATE_RESTORE_FAILED,
                f"编排状态快照损坏，无法恢复：{exc}",
            )

    async def mark_terminated(
        self, orchestration_id: str, reason: str
    ) -> None:
        """标记编排为明确终止状态（落盘终止标记文件）。"""
        marker_path = os.path.join(self._root, f"{orchestration_id}.terminated")
        with open(marker_path, "w", encoding="utf-8") as f:
            json.dump({"reason": reason}, f, ensure_ascii=False)
        ctx = await self.load(orchestration_id)
        if ctx is not None:
            ctx.status = OrchestrationStatus.TERMINATED
            ctx.touch()
            await self.save(ctx)


class PostgresStateStore:
    """Postgres 编排状态存储（延迟导入 asyncpg）。

    连接失败自动降级内存实现并记录降级来源。
    满足 IOrchestrationStateStore 接口。
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 5432,
        db: str = "orchestration_platform",
        user: str = "postgres",
        password: str = "",
    ) -> None:
        self._host = host
        self._port = port
        self._db = db
        self._user = user
        self._password = password
        self._degraded: Optional[InMemoryStateStore] = None
        self._pool = None

    @property
    def backend_name(self) -> str:
        return "postgres" if self._degraded is None else "postgres(degraded->memory)"

    def _ensure_pool(self) -> None:
        """延迟初始化连接池；失败时降级内存。"""
        if self._degraded is not None:
            return
        if self._pool is not None:
            return
        try:
            import asyncpg
        except ImportError:
            self._degraded = InMemoryStateStore()
            return
        # asyncpg 连接池创建在协程中执行，此处仅标记待初始化
        self._pool = None

    async def _get_pool(self):
        """异步获取连接池；失败降级内存。"""
        self._ensure_pool()
        if self._degraded is not None:
            return None
        if self._pool is None:
            try:
                import asyncpg
                self._pool = await asyncpg.create_pool(
                    host=self._host,
                    port=self._port,
                    database=self._db,
                    user=self._user,
                    password=self._password,
                    min_size=1,
                    max_size=5,
                )
            except Exception:
                self._degraded = InMemoryStateStore()
                return None
        return self._pool

    async def save(self, context: SupervisorOrchestrationContext) -> None:
        """保存编排上下文快照。"""
        pool = await self._get_pool()
        if pool is None:
            await self._degraded.save(context)
            return
        data = context.model_dump_json()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS supervisor_orchestration_checkpoints (
                    orchestration_id TEXT PRIMARY KEY,
                    snapshot JSONB NOT NULL,
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
                """,
            )
            await conn.execute(
                """
                INSERT INTO supervisor_orchestration_checkpoints
                    (orchestration_id, snapshot)
                VALUES ($1, $2::jsonb)
                ON CONFLICT (orchestration_id)
                DO UPDATE SET snapshot = EXCLUDED.snapshot, updated_at = NOW()
                """,
                context.orchestration_id,
                data,
            )

    async def load(
        self, orchestration_id: str
    ) -> Optional[SupervisorOrchestrationContext]:
        """加载编排上下文快照。"""
        pool = await self._get_pool()
        if pool is None:
            return await self._degraded.load(orchestration_id)
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT snapshot FROM supervisor_orchestration_checkpoints WHERE orchestration_id = $1",
                orchestration_id,
            )
        if row is None:
            return None
        try:
            return SupervisorOrchestrationContext.model_validate_json(row["snapshot"])
        except Exception as exc:
            raise DomainError(
                SUP_STATE_RESTORE_FAILED,
                f"Postgres 编排状态快照损坏：{exc}",
            )

    async def mark_terminated(
        self, orchestration_id: str, reason: str
    ) -> None:
        """标记编排为明确终止状态。"""
        pool = await self._get_pool()
        if pool is None:
            await self._degraded.mark_terminated(orchestration_id, reason)
            return
        ctx = await self.load(orchestration_id)
        if ctx is not None:
            ctx.status = OrchestrationStatus.TERMINATED
            ctx.touch()
            await self.save(ctx)


def create_state_store(
    backend: str,
    local_storage_root: str = "./.data",
    postgres_host: str = "localhost",
    postgres_port: int = 5432,
    postgres_db: str = "orchestration_platform",
    postgres_user: str = "postgres",
    postgres_password: str = "",
) -> "object":
    """按配置创建编排状态存储实例。

    Args:
        backend —— 后端类型：memory / local_file / postgres
        其余参数为各后端的连接/路径配置

    Returns:
        实现 IOrchestrationStateStore 接口的存储实例
    """
    if backend == "memory":
        return InMemoryStateStore()
    if backend == "local_file":
        return LocalFileStateStore(
            root_dir=os.path.join(local_storage_root, "supervisor_orchestration", "checkpoints")
        )
    if backend == "postgres":
        return PostgresStateStore(
            host=postgres_host,
            port=postgres_port,
            db=postgres_db,
            user=postgres_user,
            password=postgres_password,
        )
    raise DomainError(
        SUP_STATE_RESTORE_FAILED,
        f"未知的编排状态存储后端：{backend}",
    )
