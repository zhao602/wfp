# =============================================================================
# 外部集成抽象接口（integrations/abstractions/external_clients.py）
#
# 职责：定义 LLM 客户端、工具调用器、数据源连接器的外部集成抽象。
# 设计意图（对应 design 2.2.2.4）：
#   1. 节点执行器依赖抽象接口，不直连具体实现；
#   2. 支持主备模型切换、本地/MCP 工具统一入口、只读数据源连接。
# =============================================================================

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from orchestration_platform.domain.models.common import JsonValue
from orchestration_platform.domain.models.node_configurations import LlmSamplingParameters


class InferenceRequest(BaseModel):
    """LLM 推理请求。"""

    model_identifier: str = Field(description="模型标识")
    system_prompt: str = Field(default="", description="系统提示词")
    user_message: str = Field(default="", description="用户消息")
    available_tools: List[str] = Field(default_factory=list, description="可用工具标识")
    output_schema: Optional[Dict[str, Any]] = Field(default=None, description="输出结构约束")
    # llm_and_code_nodes CHG-11：追加可选字段（既有 5 字段零变更）
    sampling: Optional[LlmSamplingParameters] = Field(
        default=None, description="采样参数（未配置时走既有缺省请求体）"
    )


class InferenceResult(BaseModel):
    """LLM 推理结果。"""

    success: bool = Field(description="是否成功")
    content: str = Field(default="", description="推理内容")
    tool_call_intent: Optional[Dict[str, JsonValue]] = Field(
        default=None, description="工具调用意图（若有）"
    )
    structured_output: Optional[Dict[str, JsonValue]] = Field(
        default=None, description="结构化输出"
    )
    error_message: Optional[str] = Field(default=None, description="错误信息")
    # llm_and_code_nodes CHG-11/CHG-13：追加可选字段（既有 5 字段零变更）
    model_switched: bool = Field(
        default=False, description="主备切换标记（备用通道成功时置位）"
    )
    error_status_code: Optional[int] = Field(
        default=None, description="上游响应状态码（传输失败为 None）"
    )
    error_detail: Optional[str] = Field(
        default=None, description="上游响应体摘要（≤512 字符、去换行、脱敏）"
    )
    # roadmap B7 W1b：追加可选字段（既有字段零变更，CHG-11 追加先例）——
    # provider 返回用量时透传（OpenAI 兼容三键形状；未返回为 None，
    # 执行器按存在性写 metadata["token_usage"]）
    usage: Optional[Dict[str, JsonValue]] = Field(
        default=None, description="Token 用量统计（上游返回时）"
    )


# arch_review_gap_b2_observability_perf B2-C4（T4.3）：流式推理增量块。
# 独立新增类——InferenceRequest/InferenceResult 契约零改动（GK-14）。
class InferenceStreamChunk(BaseModel):
    """LLM 流式推理增量块（infer_stream 产出单位）。"""

    delta: str = Field(default="", description="增量文本")
    finish_reason: Optional[str] = Field(default=None, description="结束原因")
    usage: Optional[Dict[str, JsonValue]] = Field(
        default=None, description="用量统计（上游返回时）"
    )
    is_final: bool = Field(default=False, description="是否结束帧")
    error_message: Optional[str] = Field(default=None, description="错误信息（脱敏）")
    error_status_code: Optional[int] = Field(
        default=None, description="上游响应状态码（传输失败为 None）"
    )
    error_detail: Optional[str] = Field(
        default=None, description="上游响应体摘要（≤512 字符、去换行、脱敏）"
    )


class ToolResult(BaseModel):
    """工具调用结果。"""

    success: bool = Field(description="是否成功")
    output: Optional[JsonValue] = Field(default=None, description="调用输出")
    error_code: Optional[str] = Field(default=None, description="错误码")
    error_message: Optional[str] = Field(default=None, description="错误信息")


class McpToolDescriptor(BaseModel):
    """MCP 工具描述符。"""

    name: str = Field(description="工具名称")
    description: str = Field(default="", description="工具说明")


class DataSourceResult(BaseModel):
    """数据源查询结果。"""

    success: bool = Field(description="是否成功")
    rows: List[Dict[str, JsonValue]] = Field(default_factory=list, description="查询结果行")
    error_message: Optional[str] = Field(default=None, description="错误信息")


@runtime_checkable
class ILlmClient(Protocol):
    """LLM 客户端：支持主备模型切换。"""

    async def infer(self, request: InferenceRequest) -> InferenceResult: ...


@runtime_checkable
class IToolInvoker(Protocol):
    """工具调用抽象：本地函数与 MCP 工具统一入口。"""

    @property
    def tool_identifier(self) -> str: ...

    async def invoke(
        self, arguments: Dict[str, JsonValue], timeout_seconds: int
    ) -> ToolResult: ...


@runtime_checkable
class IDataSourceConnector(Protocol):
    """数据源连接器：只读查询。"""

    async def read(
        self, connection_reference: str, query: str, timeout_seconds: int
    ) -> DataSourceResult: ...
