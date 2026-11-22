# =============================================================================
# 沙箱领域模型（integrations/sandbox/models.py）
#
# 职责：定义沙箱策略、会话、调用、执行结果、资源用量、违规记录等领域模型。
# 设计意图（对应偏差修正 spec 6.1 / design 2.3.2 类图）：
#   - SandboxPolicy：沙箱执行策略（资源限额+超时+工作目录+网络策略+隔离模式）
#   - SandboxSession：沙箱会话（策略加载后的隔离环境实例）
#   - SandboxInvocation：单次沙箱调用请求
#   - SandboxExecutionResult：沙箱执行结果（含资源用量与违规标记）
#   - ResourceUsage：三维资源用量（CPU/内存/输出）
#   - SandboxViolationRecord：违规记录
#   - ContainerExecutionRequest/Result：容器物理隔离请求与结果
# =============================================================================

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

from orchestration_platform.domain.models.enums import (
    IsolationMode,
    NetworkPolicyMode,
    ViolationType,
)


def _utc_now() -> datetime:
    """UTC 当前时间工厂。"""
    return datetime.now(timezone.utc)


# 平台系统目录黑名单（工作目录禁止声明）
_SYSTEM_DIRS = {
    "/etc", "/var", "/usr", "/bin", "/sbin", "/root", "/boot", "/sys", "/proc",
    "C:\\Windows", "C:\\Program Files", "C:\\Program Files (x86)",
}


class ResourceUsage(BaseModel):
    """三维资源用量。

    CPU 时间/内存峰值/输出尺寸，由 ResourceMeter 周期采样计量。
    """

    cpu_seconds: float = Field(default=0.0, description="CPU 时间（秒）")
    memory_peak_mb: float = Field(default=0.0, description="内存峰值（MB）")
    output_bytes: int = Field(default=0, description="输出尺寸（字节）")


class SandboxViolationRecord(BaseModel):
    """沙箱违规记录。

    执行过程中检测到的路径穿越/网络拒绝/资源超限/超时违规。
    """

    violation_type: ViolationType = Field(description="违规类型")
    detail: str = Field(description="违规详情")
    occurred_at: datetime = Field(default_factory=_utc_now, description="违规时间")
    target: Optional[str] = Field(default=None, description="违规目标（路径/网络地址）")


class SandboxPolicy(BaseModel):
    """沙箱执行策略。

    定义资源限额、执行超时、工作目录、网络策略与隔离模式。
    策略标识在租户内唯一，由系统生成。
    """

    policy_id: str = Field(description="策略唯一标识（租户内唯一）")
    tenant_id: str = Field(description="租户标识")
    name: str = Field(default="", description="策略名称")
    description: str = Field(default="", description="策略描述")

    # 隔离模式
    isolation_mode: IsolationMode = Field(
        default=IsolationMode.LOGICAL, description="隔离模式"
    )
    allow_degrade: bool = Field(
        default=True, description="物理隔离不可用时是否允许降级为逻辑隔离"
    )

    # 资源限额（三项均须为正数）
    cpu_seconds_limit: float = Field(default=10.0, gt=0, description="CPU 时间上限（秒）")
    memory_mb_limit: int = Field(default=512, gt=0, description="内存上限（MB）")
    output_bytes_limit: int = Field(default=1048576, gt=0, description="输出尺寸上限（字节）")

    # 执行超时（正整数秒）
    execution_timeout_seconds: int = Field(
        default=60, gt=0, description="执行超时（秒）"
    )

    # 工作目录（须绝对路径且禁止平台系统目录）
    workdir: str = Field(description="工作目录（绝对路径）")

    # 网络策略
    network_policy: NetworkPolicyMode = Field(
        default=NetworkPolicyMode.DEFAULT_DENY, description="网络策略模式"
    )
    network_whitelist: List[str] = Field(
        default_factory=list, description="网络白名单（域名或 IP 段）"
    )

    # 允许的文件路径前缀（工作目录子路径默认允许）
    allowed_path_prefixes: List[str] = Field(
        default_factory=list, description="额外允许的路径前缀"
    )

    is_active: bool = Field(default=True, description="是否启用")
    created_at: datetime = Field(default_factory=_utc_now, description="创建时间")

    @field_validator("workdir")
    @classmethod
    def _validate_workdir(cls, v: str) -> str:
        """校验工作目录须绝对路径且禁止平台系统目录。"""
        import os
        if not os.path.isabs(v):
            raise ValueError(f"工作目录须为绝对路径: {v}")
        normalized = os.path.normpath(v)
        for sys_dir in _SYSTEM_DIRS:
            if normalized.startswith(sys_dir):
                raise ValueError(f"工作目录禁止声明平台系统目录: {v}")
        return normalized


class SandboxSession(BaseModel):
    """沙箱会话。

    策略加载后的隔离环境实例，承载会话标识与执行上下文。
    """

    session_id: str = Field(description="会话唯一标识")
    policy_id: str = Field(description="关联策略标识")
    tenant_id: str = Field(description="租户标识")
    created_at: datetime = Field(default_factory=_utc_now, description="会话创建时间")
    is_active: bool = Field(default=True, description="会话是否活跃")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="会话元数据")


class SandboxInvocation(BaseModel):
    """沙箱调用请求。

    单次工具调用的入参与调用上下文。
    """

    invocation_id: str = Field(description="调用唯一标识")
    session_id: str = Field(description="关联会话标识")
    tool_name: str = Field(description="工具名称")
    arguments: Dict[str, Any] = Field(default_factory=dict, description="工具入参")
    timeout_seconds: Optional[int] = Field(
        default=None, description="调用超时覆盖（None 用策略默认）"
    )


class SandboxExecutionResult(BaseModel):
    """沙箱执行结果。

    携带执行输出、资源用量、违规标记与降级标记。
    """

    success: bool = Field(description="是否成功")
    output: Any = Field(default=None, description="执行输出")
    resource_usage: ResourceUsage = Field(
        default_factory=ResourceUsage, description="资源用量"
    )
    violations: List[SandboxViolationRecord] = Field(
        default_factory=list, description="违规记录列表"
    )
    timed_out: bool = Field(default=False, description="是否超时")
    degraded: bool = Field(default=False, description="是否降级执行")
    degraded_reason: Optional[str] = Field(default=None, description="降级原因")
    error_code: Optional[str] = Field(default=None, description="错误码")
    error_message: Optional[str] = Field(default=None, description="错误消息")
    execution_duration_seconds: float = Field(
        default=0.0, description="执行耗时（秒）"
    )


class ContainerExecutionRequest(BaseModel):
    """容器物理隔离执行请求。

    提交至 IContainerRuntime 的请求载荷。
    """

    request_id: str = Field(description="请求唯一标识")
    session_id: str = Field(description="关联会话标识")
    tool_name: str = Field(description="工具名称")
    arguments: Dict[str, Any] = Field(default_factory=dict, description="工具入参")
    workdir: str = Field(description="容器工作目录")
    cpu_seconds_limit: float = Field(description="CPU 时间上限")
    memory_mb_limit: int = Field(description="内存上限")
    output_bytes_limit: int = Field(description="输出尺寸上限")
    timeout_seconds: int = Field(description="执行超时")
    network_whitelist: List[str] = Field(
        default_factory=list, description="网络白名单"
    )


class ContainerExecutionResult(BaseModel):
    """容器物理隔离执行结果。

    IContainerRuntime 返回的执行结果。
    """

    request_id: str = Field(description="关联请求标识")
    success: bool = Field(description="是否成功")
    output: Any = Field(default=None, description="执行输出")
    resource_usage: ResourceUsage = Field(
        default_factory=ResourceUsage, description="资源用量"
    )
    error_code: Optional[str] = Field(default=None, description="错误码")
    error_message: Optional[str] = Field(default=None, description="错误消息")
    container_id: Optional[str] = Field(default=None, description="容器实例标识")
