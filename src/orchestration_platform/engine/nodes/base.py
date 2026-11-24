# =============================================================================
# 节点执行器抽象基类（engine/nodes/base.py）
#
# 职责：定义节点执行器扩展点——NodeExecutor Protocol、执行上下文与执行结果。
# 设计意图（对应 design 2.2.2.5、spec 5.2.1）：
#   1. 新增节点类型只需实现 NodeExecutor 接口并在注册表登记；
#   2. 执行器不直接写检查点——检查点由调度器统一在节点完成后写入（5.2.1.8）；
#   3. 执行上下文提供共享状态只读视图，防止执行器直接修改全局状态。
# =============================================================================

from datetime import datetime, timezone
from typing import Dict, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from orchestration_platform.domain.models.common import JsonValue
from orchestration_platform.domain.models.enums import NodeType
from orchestration_platform.domain.models.node_definition import NodeDefinition
from orchestration_platform.domain.models.shared_state import SharedState


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class NodeExecutionContext(BaseModel):
    """节点执行上下文：向执行器提供执行所需的全部输入。

    字段：
        node_definition   —— 节点定义（含配置）
        shared_state      —— 共享状态只读视图（执行器不应直接修改）
        instance_id       —— 所属实例标识
        trace_id          —— 追踪标识（贯穿日志与事件）
        timeout_seconds   —— 执行超时（秒）
        round_index       —— 当前执行轮次
    """

    node_definition: NodeDefinition = Field(description="节点定义")
    shared_state: SharedState = Field(description="共享状态只读视图")
    instance_id: str = Field(description="所属实例标识")
    trace_id: str = Field(description="追踪标识")
    timeout_seconds: int = Field(default=60, description="执行超时（秒）")
    round_index: int = Field(default=0, ge=0, description="当前执行轮次")
    # ---- 第二阶段新增 ----
    subworkflow_depth: int = Field(default=0, ge=0, description="子工作流嵌套深度（0=顶层）")
    # ---- upstream_input_ref T4.1 新增（CHG-B05 / DC-03 方案 A）----
    # 追加可选字段（INV-D05 显式裁决为扩展）：既有 6 字段与 NodeExecutor
    # Protocol 不动；缺省 None 使既有构造点与既有路径逐字节不变（INV-E09）。
    node_definitions: Optional[Dict[str, NodeDefinition]] = Field(
        default=None,
        description="本工作流全量节点定义索引（形态B 引用解析用；None=未注入）",
    )


class NodeExecutionResult(BaseModel):
    """节点执行结果。

    字段：
        success        —— 是否成功
        output_name    —— 输出命名（成功时有效）
        output_value   —— 输出值（写入共享状态的 node_outputs）
        error_code     —— 失败时的错误码
        error_message  —— 失败时的错误说明
        duration_ms    —— 执行耗时（毫秒）
        metadata       —— 附加元数据（如模型切换标记、工具调用次数）
    """

    success: bool = Field(description="是否成功")
    output_name: Optional[str] = Field(default=None, description="输出命名")
    output_value: Optional[JsonValue] = Field(default=None, description="输出值")
    error_code: Optional[str] = Field(default=None, description="失败错误码")
    error_message: Optional[str] = Field(default=None, description="失败说明")
    duration_ms: int = Field(default=0, ge=0, description="执行耗时（毫秒）")
    metadata: Dict[str, JsonValue] = Field(
        default_factory=dict, description="附加元数据"
    )


@runtime_checkable
class NodeExecutor(Protocol):
    """节点执行器扩展点：新增节点类型只需实现该接口并登记。

    统一约定：
    - 输入 NodeExecutionContext（含节点定义、共享状态只读视图、追踪标识、超时）；
    - 输出 NodeExecutionResult（成功标记、命名输出、耗时、错误）；
    - 执行器不直接写检查点——检查点由调度器统一在节点完成后写入。
    """

    @property
    def node_type(self) -> NodeType:
        """返回该执行器处理的节点类型。"""
        ...

    async def execute(self, context: NodeExecutionContext) -> NodeExecutionResult:
        """执行节点逻辑，返回执行结果。"""
        ...
