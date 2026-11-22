# =============================================================================
# 数据源连接器模型（integrations/data_source_connectors/models.py）
#
# 职责：定义数据源连接配置、分页参数、Schema 推断结果与读取请求/响应的领域模型。
# 设计意图（对应 spec 5.2、design 2.5）：
#   1. 四类数据源（CSV/JSON/REST API/数据库）共用统一配置结构；
#   2. 分页参数支持 offset/limit 与游标两种模式；
#   3. Schema 推断返回字段名与类型映射。
# =============================================================================

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from orchestration_platform.domain.models.enums import DataSourceType


def _utc_now() -> datetime:
    """返回当前 UTC 时间。"""
    return datetime.now(timezone.utc)


class PaginationParams(BaseModel):
    """分页参数。"""

    offset: int = Field(default=0, ge=0, description="偏移量（offset 模式）")
    limit: int = Field(default=100, ge=1, description="每页条数")
    cursor: Optional[str] = Field(default=None, description="游标（游标模式）")


class SchemaField(BaseModel):
    """Schema 单字段描述。"""

    name: str = Field(description="字段名")
    type: str = Field(description="字段类型：string/integer/float/boolean/object/array")
    nullable: bool = Field(default=True, description="是否允许空值")


class DataSchema(BaseModel):
    """数据集 Schema 推断结果。"""

    fields: List[SchemaField] = Field(default_factory=list, description="字段列表")
    inferred_at: datetime = Field(default_factory=_utc_now, description="推断时间")


class DataSourceConnectionConfig(BaseModel):
    """数据源连接配置。"""

    source_type: DataSourceType = Field(description="数据源类型")
    connection_reference: str = Field(description="连接引用标识")
    # CSV / JSON
    file_path: Optional[str] = Field(default=None, description="文件路径")
    # REST API
    url: Optional[str] = Field(default=None, description="REST API 端点 URL")
    method: str = Field(default="GET", description="HTTP 方法")
    headers: Dict[str, str] = Field(default_factory=dict, description="请求头")
    # 数据库
    connection_string: Optional[str] = Field(default=None, description="数据库连接串")
    query: Optional[str] = Field(default=None, description="SQL 查询语句")
    # 通用
    encoding: str = Field(default="utf-8", description="文件编码")
    # Hologres —— 实时数仓（PG 协议兼容）
    hologres_endpoint: Optional[str] = Field(default=None, description="Hologres 端点地址")
    hologres_port: int = Field(default=80, description="Hologres 端口")
    hologres_database: Optional[str] = Field(default=None, description="Hologres 数据库名")
    hologres_access_key: Optional[str] = Field(default=None, description="Hologres AccessKey（凭据，不出现在日志）")
    # Kafka —— 消息流
    kafka_brokers: Optional[str] = Field(default=None, description="Kafka broker 列表（逗号分隔）")
    kafka_topic: Optional[str] = Field(default=None, description="Kafka topic 名称")
    kafka_group_id: Optional[str] = Field(default=None, description="Kafka 消费组 ID")
    kafka_offset: str = Field(default="earliest", description="Kafka 消费起始位置: earliest/latest")
    # Flink —— 流计算引擎
    flink_jobmanager_url: Optional[str] = Field(default=None, description="Flink JobManager REST URL")
    flink_jar_id: Optional[str] = Field(default=None, description="Flink JAR ID")
    flink_program_args: Optional[str] = Field(default=None, description="Flink 作业参数")
    # Spark —— 批计算引擎
    spark_master_url: Optional[str] = Field(default=None, description="Spark Master URL（spark://host:port 或 yarn）")
    spark_submit_args: Optional[str] = Field(default=None, description="spark-submit 额外参数")
    spark_app_name: Optional[str] = Field(default=None, description="Spark 应用名")


class DataSourceReadRequest(BaseModel):
    """数据源读取请求。"""

    connection_config: DataSourceConnectionConfig = Field(description="连接配置")
    pagination: PaginationParams = Field(
        default_factory=PaginationParams, description="分页参数"
    )
    streaming: bool = Field(default=False, description="是否流式读取")


class DataSourceReadResult(BaseModel):
    """数据源读取结果。"""

    data: List[Dict[str, Any]] = Field(default_factory=list, description="数据行列表")
    data_schema: Optional[DataSchema] = Field(default=None, alias="schema", description="数据 Schema")
    total_count: Optional[int] = Field(default=None, ge=0, description="总条数（若可知）")
    has_more: bool = Field(default=False, description="是否还有更多数据")
    next_cursor: Optional[str] = Field(default=None, description="下一页游标")


class RetryPolicy(BaseModel):
    """重试策略。"""

    max_retries: int = Field(default=3, ge=0, description="最大重试次数")
    retry_delay_seconds: float = Field(default=1.0, ge=0, description="重试间隔（秒）")
    timeout_seconds: int = Field(default=30, ge=1, description="超时时间（秒）")
