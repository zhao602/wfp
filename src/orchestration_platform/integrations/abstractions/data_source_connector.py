# =============================================================================
# 数据源连接器抽象接口（integrations/abstractions/data_source_connector.py）
#
# 职责：定义四类数据源连接器的统一抽象接口。
# 设计意图（对应 spec 5.2、design 2.5）：
#   1. 以 Protocol 定义接口，实现类只需实现 connect/read/infer_schema/close 四个方法；
#   2. 新增数据源类型只需实现接口并登记至注册表，不修改既有代码；
#   3. 凭据保护：连接配置中的密钥/令牌不出现在日志中。
# =============================================================================

from typing import Protocol, runtime_checkable

from orchestration_platform.integrations.data_source_connectors.models import (
    DataSourceConnectionConfig,
    DataSourceReadRequest,
    DataSourceReadResult,
    DataSchema,
)


@runtime_checkable
class IDataSourceConnector(Protocol):
    """数据源连接器抽象接口。

    四类连接器（CSV/JSON/REST API/数据库）均实现此接口。
    """

    async def connect(self, config: DataSourceConnectionConfig) -> None:
        """建立连接。

        Args:
            config: 连接配置。
        """
        ...

    async def probe(self, config: DataSourceConnectionConfig, level: str = "liveness") -> None:
        """只读连通性探测（无副作用）。

        在建立真实连接后立即释放，用于"测试连接"判断真实可达性；
        与 connect()/read() 分离，避免浅校验产生假阳性。

        Args:
            config: 连接配置。
            level: 探测级别——
                - "liveness"：仅验证目标可达（如 broker 连通、HTTP 端点响应）；
                - "readiness"：在 liveness 基础上验证业务可用性（如 topic 存在、查询可执行）。

        Raises:
            DomainError: 探测失败（参数错误 / 连接失败 / 无法探测），
                诊断文本以固定前缀标识失败类别。
        """
        ...

    async def read(self, request: DataSourceReadRequest) -> DataSourceReadResult:
        """读取数据。

        Args:
            request: 读取请求（含分页与流式标志）。

        Returns:
            读取结果（含数据行、Schema、分页信息）。
        """
        ...

    async def infer_schema(self, config: DataSourceConnectionConfig) -> DataSchema:
        """推断数据 Schema。

        Args:
            config: 连接配置。

        Returns:
            推断的 Schema（字段名与类型映射）。
        """
        ...

    async def close(self) -> None:
        """关闭连接，释放资源。"""
        ...
