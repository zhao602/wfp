# =============================================================================
# 数据源节点执行器（engine/nodes/data_source_node_executor.py）
#
# 职责：只读查询数据源，空结果标记成功并输出空数据集。
# 设计意图（对应 spec 5.3.1.7-9、design 2.5.3）：
#   1. 经 IDataSourceConnector（连接器实现版）执行只读查询；
#   2. 查询无结果 → 标记成功并输出空数据集（不失败）；
#   3. 写操作已在校验阶段拦截（node_configuration_validator），此处不再重复。
#   4. 构造时注入 DataSourceConnectorRegistry，execute() 内按 source_type 路由连接器（DC-2）。
#   5. 接口适配在执行器内完成（DC-1），不引入独立适配层模块。
# =============================================================================

import logging
from typing import Optional

from orchestration_platform.domain.errors import NODE_DATASOURCE_UNAVAILABLE, DomainError
from orchestration_platform.domain.models.enums import NodeType
from orchestration_platform.domain.models.node_configurations import DataSourceNodeConfiguration
from orchestration_platform.engine.nodes.base import (
    NodeExecutionContext,
    NodeExecutionResult,
)
from orchestration_platform.integrations.data_source_connectors.connector_registry import (
    DataSourceConnectorRegistry,
)
from orchestration_platform.integrations.data_source_connectors.models import (
    DataSourceConnectionConfig,
    DataSourceReadRequest,
    DataSourceReadResult,
    PaginationParams,
)

_logger = logging.getLogger(__name__)

_CREDENTIAL_FIELD_NAMES = frozenset({
    "connection_string",
    "hologres_access_key",
    "authorization",
    "token",
    "password",
    "secret",
    "access_key",
    "api_key",
})


class DataSourceNodeExecutor:
    """数据源节点执行器：只读查询。"""

    def __init__(
        self,
        connector_registry: Optional[DataSourceConnectorRegistry] = None,
    ) -> None:
        """构造数据源执行器。

        参数：
            connector_registry —— 数据源连接器注册表；缺省为 None，需通过
              set_connector_registry 注入（DC-2）。
        """
        self._connector_registry = connector_registry

    def set_connector_registry(
        self, registry: DataSourceConnectorRegistry
    ) -> None:
        """注入数据源连接器注册表。"""
        self._connector_registry = registry

    @property
    def node_type(self) -> NodeType:
        return NodeType.DATA_SOURCE

    def _build_connection_config(
        self, config: DataSourceNodeConfiguration
    ) -> DataSourceConnectionConfig:
        """将节点配置字段映射为 DataSourceConnectionConfig（DC-1）。

        target_endpoint 不映射（DataSourceConnectionConfig 无此字段，Q-2）。
        """
        return DataSourceConnectionConfig(
            source_type=config.source_type,
            connection_reference=config.connection_reference,
            file_path=config.file_path,
            url=config.url,
            method=config.method,
            headers=config.headers,
            connection_string=config.connection_string,
            query=config.query,
            encoding=config.encoding,
            hologres_endpoint=config.hologres_endpoint,
            hologres_port=config.hologres_port,
            hologres_database=config.hologres_database,
            hologres_access_key=config.hologres_access_key,
            kafka_brokers=config.kafka_brokers,
            kafka_topic=config.kafka_topic,
            kafka_group_id=config.kafka_group_id,
            kafka_offset=config.kafka_offset,
            flink_jobmanager_url=config.flink_jobmanager_url,
            flink_jar_id=config.flink_jar_id,
            flink_program_args=config.flink_program_args,
            spark_master_url=config.spark_master_url,
            spark_submit_args=config.spark_submit_args,
            spark_app_name=config.spark_app_name,
        )

    def _build_read_request(
        self, connection_config: DataSourceConnectionConfig
    ) -> DataSourceReadRequest:
        """组装 DataSourceReadRequest（DC-1）。"""
        return DataSourceReadRequest(
            connection_config=connection_config,
            pagination=PaginationParams(),
            streaming=False,
        )

    def _convert_result(
        self,
        read_result: DataSourceReadResult,
        output_name: str,
        connection_reference: str,
    ) -> NodeExecutionResult:
        """转换 DataSourceReadResult 为 NodeExecutionResult（DC-1）。

        空结果 → 成功 + count=0（AC-12）。
        metadata 仅含 connection_reference，不含连接参数明细（INV-5/AC-15）。
        """
        return NodeExecutionResult(
            success=True,
            output_name=output_name,
            output_value={
                "rows": read_result.data,
                "count": len(read_result.data),
            },
            metadata={"connection_reference": connection_reference},
        )

    def _sanitize_error_message(self, error: Exception) -> str:
        """提取诊断文本并去除凭据（DC-1/INV-5）。

        DomainError → 使用其 message，替换凭据字段值为掩码；
        未预期异常 → 返回通用信息，不暴露内部细节。
        """
        if isinstance(error, DomainError):
            msg = error.message
            for field_name in _CREDENTIAL_FIELD_NAMES:
                if field_name in msg:
                    msg = msg.replace(field_name, "***")
            return msg
        return "数据源查询失败"

    async def execute(self, context: NodeExecutionContext) -> NodeExecutionResult:
        """执行数据源节点：只读查询。"""
        config: DataSourceNodeConfiguration = context.node_definition.configuration
        source_type = config.source_type

        # 分支 1：source_type 缺失（AC-3）
        if source_type is None:
            return NodeExecutionResult(
                success=False,
                error_code=NODE_DATASOURCE_UNAVAILABLE,
                error_message="数据源类型未指定，无法获取连接器",
            )

        # 分支 2：注册表未注入（AC-5 防御性）
        if self._connector_registry is None:
            return NodeExecutionResult(
                success=False,
                error_code=NODE_DATASOURCE_UNAVAILABLE,
                error_message="数据源连接器注册表未注入",
            )

        # 分支 3：连接器未注册（AC-2/DC-5）
        try:
            connector = self._connector_registry.get_connector(source_type)
        except DomainError:
            return NodeExecutionResult(
                success=False,
                error_code=NODE_DATASOURCE_UNAVAILABLE,
                error_message=f"数据源类型 {source_type.value} 的连接器未注册",
            )

        # 接口适配（DC-1）
        connection_config = self._build_connection_config(config)
        read_request = self._build_read_request(connection_config)

        # 分支 4/5：查询执行（AC-13）
        try:
            read_result = await connector.read(read_request)
        except DomainError as de:
            return NodeExecutionResult(
                success=False,
                error_code=NODE_DATASOURCE_UNAVAILABLE,
                error_message=self._sanitize_error_message(de),
            )
        except Exception as exc:
            _logger.warning(
                "数据源查询未预期异常: %s", type(exc).__name__
            )
            return NodeExecutionResult(
                success=False,
                error_code=NODE_DATASOURCE_UNAVAILABLE,
                error_message="数据源查询失败",
            )

        # 分支 6：正常返回含空结果（AC-11/AC-12）
        return self._convert_result(
            read_result,
            context.node_definition.effective_output_name,
            config.connection_reference,
        )
