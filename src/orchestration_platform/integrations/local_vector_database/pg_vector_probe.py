# =============================================================================
# pgvector 扩展探测器（integrations/local_vector_database/pg_vector_probe.py）
#
# 职责：探测 PostgreSQL `vector` 扩展可用性（B2-C5 / design DC-B2-05 ②）。
# 设计意图（对应 arch_review_gap_b2 spec FR-B2-05 / tasks T5.4）：
#   1. 探测语义：查询 pg_extension 系统目录中 extname='vector'——命中即
#      「已安装」；未命中/查询失败 → 不可用（fail-safe，禁伪造可用性）；
#   2. 延迟导入：asyncpg 仅在方法内导入（无依赖环境导入本模块不崩溃，
#      对齐 INV-L02 范式）；构造零副作用（不连库、不探测）；
#   3. 三态（tasks T5.4 验收）：命中且启用 → SQL 算子增强路径（<=>）；
#      未命中 → 应用层距离自动回退；回退必须可辨识登记（禁静默）；
#   4. 结果缓存（探测一次后固化，幂等）；executor 可注入（单测 mock seam）。
# =============================================================================

import importlib
from typing import Any, Callable, Optional


class PgVectorExtensionProbe:
    """pgvector 扩展探测器：结果缓存、executor 可注入、fail-safe 不可用。"""

    def __init__(
        self,
        executor: Optional[Callable[[], Any]] = None,
        enabled: bool = True,
    ) -> None:
        """构造零副作用。

        executor —— 可注入异步探测执行器（返回 True=扩展在位 / False=不在位；
        单测 mock seam；缺省使用内置 pg_extension 查询）。
        enabled —— 总闸（False 时 probe() 恒 False：SQL 算子增强路径不启用，
        应用层距离为唯一权威路径）。
        """
        self._executor = executor
        self._enabled = bool(enabled)
        self._probed = False
        self._result: Optional[bool] = None

    def probe(self) -> bool:
        """探测 vector 扩展是否可用（结果缓存；异常/未命中 → False）。"""
        if not self._probed:
            self._result = self._probe_once()
            self._probed = True
        return bool(self._result)

    def is_acceleration_available(self) -> bool:
        """SQL 算子增强路径是否可用（命中且启用，DC-B2-05 ②）。"""
        return self._enabled and self.probe()

    def invalidate(self) -> None:
        """清除缓存结果（诊断/测试用；下次 probe() 重新探测）。"""
        self._probed = False
        self._result = None

    def _probe_once(self) -> bool:
        if not self._enabled:
            return False
        if self._executor is not None:
            try:
                return bool(self._executor())
            except Exception:
                # 探测失败一律按不可用处理（fail-safe，禁伪造可用性）
                return False
        try:
            import asyncio

            return bool(asyncio.run(self._probe_via_asyncpg()))
        except Exception:
            return False

    async def _probe_via_asyncpg(self) -> bool:
        """内置探测路径：延迟导入 asyncpg + pg_extension 目录查询。"""
        asyncpg = importlib.import_module("asyncpg")
        conn = await self._connect(asyncpg)
        try:
            row = await conn.fetchrow(
                "SELECT 1 FROM pg_extension WHERE extname = 'vector'"
            )
            return row is not None
        finally:
            await conn.close()

    @staticmethod
    async def _connect(asyncpg: Any) -> Any:
        """按运行期 settings 建探测短连接（对齐 persistence/postgres 配置面）。"""
        from orchestration_platform.configuration.settings import get_settings

        settings = get_settings()
        return await asyncpg.connect(
            host=settings.postgres_host,
            port=settings.postgres_port,
            database=settings.postgres_db,
            user=settings.postgres_user,
            password=settings.postgres_password,
        )
