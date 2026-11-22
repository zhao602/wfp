# =============================================================================
# 本地数据源连接测试 REST API（api/routers/governance/data_source_router.py）
#
# 职责：为本地数据源（csv/json/rest_api/database/hologres/kafka/flink/spark）
#       提供不依赖企业连接器注册表的连接可达性测试端点（fix_datasource_test_connection）。
# 设计意图：
#   1. INV-1：不按 connector_id 查找企业连接器注册表（_configs）；
#   2. AC-4/5/6：复用 DataSourceConnectorRegistry 与 connect()，按执行结果返回 reachable；
#   3. INV-4：诊断信息不含凭据字段；
#   4. INV-5：不修改既有企业连接器端点行为。
# =============================================================================

import time

from fastapi import APIRouter, HTTPException

from orchestration_platform.domain.errors import DomainError
from orchestration_platform.domain.models.enums import DataSourceType
from orchestration_platform.integrations.data_source_connectors.connector_registry import (
    DataSourceConnectorRegistry,
)
from orchestration_platform.integrations.data_source_connectors.enterprise.models import (
    ConnectionTestResult,
)
from orchestration_platform.integrations.data_source_connectors.models import (
    DataSourceConnectionConfig,
)

router = APIRouter(tags=["数据源"])

_connector_registry = DataSourceConnectorRegistry()

# 企业连接器类型：须走既有 /connectors/enterprise/{connector_id}/test 端点（INV-2）
_ENTERPRISE_SOURCE_TYPES = frozenset(
    {DataSourceType.CRM, DataSourceType.ERP, DataSourceType.OA}
)

# 默认探测级别：readiness（更严格，能提前暴露 topic/参数问题，DEV-1，用户可后续调整为 liveness）
_DEFAULT_PROBE_LEVEL = "readiness"


@router.post("/data-source/test", response_model=ConnectionTestResult)
async def test_data_source_connection(
    config: DataSourceConnectionConfig,
) -> ConnectionTestResult:
    """本地数据源连接测试：按 source_type 取连接器执行 probe() 只读探测（INV-3 无副作用）。"""
    if config.source_type in _ENTERPRISE_SOURCE_TYPES:
        raise HTTPException(
            status_code=422,
            detail="企业连接器类型请使用 /connectors/enterprise/{connector_id}/test 端点",
        )
    try:
        connector = _connector_registry.get_connector(config.source_type)
    except DomainError:
        raise HTTPException(
            status_code=404,
            detail=f"数据源类型 {config.source_type.value} 的连接器未注册",
        )

    start = time.perf_counter()

    def _latency_ms() -> int:
        return int((time.perf_counter() - start) * 1000)

    try:
        await connector.probe(config, _DEFAULT_PROBE_LEVEL)
    except DomainError as exc:
        # 领域异常消息由 probe_support 构造（三类前缀：参数错误/连接失败/无法探测），不含凭据（INV-4）
        return ConnectionTestResult(
            reachable=False,
            response_latency_ms=_latency_ms(),
            diagnosis=exc.message,
        )
    except Exception:
        # 非领域异常：不透出原始消息，避免连接串/AccessKey 等凭据随诊断信息泄漏（INV-4）
        return ConnectionTestResult(
            reachable=False,
            response_latency_ms=_latency_ms(),
            diagnosis="连接失败，请检查连接参数",
        )
    return ConnectionTestResult(reachable=True, response_latency_ms=_latency_ms())
