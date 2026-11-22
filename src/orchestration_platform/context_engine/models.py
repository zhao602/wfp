# =============================================================================
# 上下文引擎模型（context_engine/models.py）
#
# 职责：定义 Agent 执行上下文的结构化模型，供上下文管理器、窗口策略与持久化使用。
# 设计意图（对应 spec 5.4、design 2.7）：
#   1. 上下文由四部分组成：系统提示、历史对话、工具结果、共享状态引用；
#   2. 三种窗口策略对上下文进行裁剪，防止超出模型上下文长度限制；
#   3. 上下文检查点支持持久化与跨节点传递。
# =============================================================================

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from orchestration_platform.domain.models.enums import ContextWindowStrategyType


def _utc_now() -> datetime:
    """返回当前 UTC 时间。"""
    return datetime.now(timezone.utc)


class ConversationTurn(BaseModel):
    """单轮对话：含角色与内容。"""

    role: str = Field(description="角色：system / user / assistant / tool")
    content: str = Field(description="消息内容")
    timestamp: datetime = Field(default_factory=_utc_now, description="时间戳")
    token_count: Optional[int] = Field(default=None, ge=0, description="Token 计数")
    # 第五阶段偏差修正（G2/T213）：已消费标记，无损裁剪策略据此识别可移除区块
    consumed: bool = Field(default=False, description="是否已被消费（无损裁剪依据）")


class ToolResultEntry(BaseModel):
    """工具调用结果条目。"""

    tool_identifier: str = Field(description="工具标识")
    arguments: Dict[str, Any] = Field(default_factory=dict, description="调用参数")
    result: Any = Field(description="返回结果")
    success: bool = Field(description="是否成功")
    timestamp: datetime = Field(default_factory=_utc_now, description="时间戳")
    token_count: Optional[int] = Field(default=None, ge=0, description="结果 Token 计数")
    # 第五阶段偏差修正（G2/T213）：已消费标记
    consumed: bool = Field(default=False, description="是否已被消费（无损裁剪依据）")


class AgentContext(BaseModel):
    """Agent 执行上下文：四部分组成。

    system_prompt     —— 系统提示词
    conversation_history —— 历史对话轮次列表
    tool_results      —— 工具调用结果列表
    state_references  —— 共享状态字段引用（键名 -> 值的快照）
    pruning_records   —— 无损裁剪记录（G2 新增，旁路追溯，不计入 Token 估算）
    """

    instance_id: str = Field(description="实例标识")
    node_id: str = Field(description="节点标识")
    system_prompt: str = Field(default="", description="系统提示词")
    conversation_history: List[ConversationTurn] = Field(
        default_factory=list, description="历史对话轮次"
    )
    tool_results: List[ToolResultEntry] = Field(
        default_factory=list, description="工具调用结果列表"
    )
    state_references: Dict[str, Any] = Field(
        default_factory=dict, description="共享状态字段引用快照"
    )
    total_token_count: int = Field(default=0, ge=0, description="上下文总 Token 计数")
    max_token_limit: int = Field(default=4096, ge=1, description="模型上下文 Token 上限")
    # 第五阶段偏差修正（G2/T213）：无损裁剪记录列表
    # 旁路追溯数据，不参与 Token 估算（避免记录膨胀上下文）；
    # 既有构造调用不传此字段时默认空列表，向后兼容。
    pruning_records: List[Any] = Field(
        default_factory=list, description="无损裁剪记录（旁路追溯，不计入 Token 估算）"
    )


class ContextWindowStrategyConfig(BaseModel):
    """上下文窗口策略配置。"""

    strategy_type: ContextWindowStrategyType = Field(description="策略类型")
    # 滑动窗口
    window_size: Optional[int] = Field(default=None, ge=1, description="保留轮次数（滑动窗口）")
    # 摘要压缩
    summary_max_tokens: Optional[int] = Field(default=None, ge=1, description="摘要最大 Token 数")
    # 重要性排序
    importance_threshold: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, description="重要性阈值（0~1）"
    )
    # 通用
    fallback_strategy: Optional[ContextWindowStrategyType] = Field(
        default=None, description="回退策略（主策略失败时使用）"
    )


class ContextCheckpoint(BaseModel):
    """上下文检查点：持久化 Agent 上下文的快照。"""

    instance_id: str = Field(description="实例标识")
    node_id: str = Field(description="节点标识")
    sequence: int = Field(ge=0, description="检查点序号")
    context: AgentContext = Field(description="上下文快照")
    created_at: datetime = Field(default_factory=_utc_now, description="创建时间")
