# TODO(tech-debt): B档候选孤儿（族群 B15），待 APM 链路追踪专项排查确认无调用后清理，见 docs/tech-debt-ledger.md
# =============================================================================
# 只读数据源客户端（integrations/data_source/readonly_data_source_client.py）
#
# 职责：以只读方式读取数据集或数据流，空结果返回空数据集且标记成功。
# 设计意图（对应 spec 5.3.1.7-9）：
#   1. 只读查询——经 read_only_statement_guard 在校验阶段拦截写操作；
#   2. 空结果 → 成功 + 空数据集（不失败）。
# =============================================================================

from typing import List, Dict, Any

from orchestration_platform.integrations.abstractions.external_clients import DataSourceResult
from orchestration_platform.integrations.data_source.read_only_statement_guard import (
    ReadOnlyStatementGuard,
)


class ReadOnlyDataSourceClient:
    """只读数据源客户端。"""

    def __init__(self) -> None:
        self._guard = ReadOnlyStatementGuard()

    async def read(
        self, connection_reference: str, query: str, timeout_seconds: int
    ) -> DataSourceResult:
        """执行只读查询。

        写操作已在校验阶段拦截，此处做二次防御。
        """
        # 二次防御：检查查询语句
        violation = self._guard.check(query)
        if violation:
            return DataSourceResult(
                success=False,
                error_message=f"写操作拦截：{violation}",
            )

        # 实际查询由具体数据源驱动实现（验证期返回空集）
        return DataSourceResult(success=True, rows=[])
