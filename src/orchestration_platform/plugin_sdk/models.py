# =============================================================================
# 插件 SDK 领域模型（plugin_sdk/models.py）
#
# 职责：定义插件节点 manifest 与注册信息等领域模型。
# =============================================================================

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PluginManifest(BaseModel):
    """插件节点 manifest（节点元数据声明）。

    第三方开发者通过 manifest 声明节点名称、版本、输入输出模式、
    依赖清单与权限声明（文件路径范围+网络目标）。
    """

    node_name: str = Field(description="节点名称（小写字母/数字/连字符，全局唯一）")
    version: str = Field(description="版本号（语义化格式 主.次.修订）")
    description: str = Field(default="", description="节点说明")
    input_schema: Dict[str, str] = Field(
        default_factory=dict, description="输入模式（字段名→类型）"
    )
    output_schema: Dict[str, str] = Field(
        default_factory=dict, description="输出模式（字段名→类型）"
    )
    dependencies: List[str] = Field(
        default_factory=list, description="依赖清单（Python 包名）"
    )
    declared_file_paths: List[str] = Field(
        default_factory=list, description="声明的文件路径访问范围"
    )
    declared_network_targets: List[str] = Field(
        default_factory=list, description="声明的网络访问目标"
    )
    entry_point: str = Field(
        default="node.py", description="节点实现入口文件"
    )


class PluginNodeRegistration(BaseModel):
    """插件节点注册信息（平台侧管理）。"""

    node_name: str = Field(description="节点名称")
    version: str = Field(description="版本号")
    manifest: PluginManifest = Field(description="manifest")
    healthy: bool = Field(default=True, description="是否健康")
    registered_at: datetime = Field(default_factory=_utc_now, description="注册时间")
    instance_count: int = Field(default=0, description="实例池数量")


class ValidationResult(BaseModel):
    """校验结果。"""

    valid: bool = Field(description="是否通过")
    errors: List[str] = Field(default_factory=list, description="错误列表")
    warnings: List[str] = Field(default_factory=list, description="警告列表")


class LifecycleLog(BaseModel):
    """生命周期日志条目。"""

    phase: str = Field(description="阶段名（setup/execute/teardown）")
    duration_seconds: float = Field(description="耗时（秒）")
    success: bool = Field(description="是否成功")
    error: Optional[str] = Field(default=None, description="异常摘要")
