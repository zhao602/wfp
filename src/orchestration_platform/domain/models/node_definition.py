# =============================================================================
# 节点定义模型（domain/models/node_definition.py）
#
# 职责：定义工作流中的单个节点——标识、名称、类型、配置、超时、重试、输出命名。
# 设计意图：
#   YAML 中 node_type 声明在节点顶层，而配置采用判别联合；
#   本模型在解析前将顶层 node_type 注入 configuration，使判别联合可正确解析。
# =============================================================================

from typing import Optional

from pydantic import BaseModel, Field, model_validator

from orchestration_platform.domain.models.common import RetryPolicy
from orchestration_platform.domain.models.enums import NodeType
from orchestration_platform.domain.models.node_configurations import NodeConfiguration


class NodeDefinition(BaseModel):
    """节点定义（对应需求规格 6.2）。

    字段：
        node_id         —— 节点标识，同一工作流内唯一，由字母/数字/下划线组成
        name            —— 节点名称，画布展示用，长度 ≤ 100
        node_type       —— 节点类型（五类之一）
        configuration   —— 节点配置（结构随类型不同，须满足该类型必备项）
        timeout_seconds —— 可选执行超时（秒），缺省用引擎默认值
        retry_policy    —— 可选重试策略，缺省不重试
        output_name     —— 节点输出命名（Agent/Tool 节点应声明，供下游引用）
    """

    node_id: str = Field(description="节点标识（同定义内唯一）")
    name: str = Field(default="", max_length=100, description="节点名称（画布展示）")
    node_type: NodeType = Field(description="节点类型")
    configuration: NodeConfiguration = Field(description="节点配置（判别联合）")
    timeout_seconds: Optional[int] = Field(default=None, ge=1, description="执行超时（秒）")
    retry_policy: Optional[RetryPolicy] = Field(default=None, description="重试策略")
    output_name: Optional[str] = Field(default=None, description="节点输出命名（供下游引用）")

    @model_validator(mode="before")
    @classmethod
    def _inject_node_type_into_configuration(cls, data: object) -> object:
        """解析前把节点顶层 node_type 注入 configuration 字典。

        设计意图：YAML 规范中 node_type 声明在节点顶层，而 configuration 采用
        判别联合需要 node_type 字段；此处注入避免要求用户在 configuration 内重复声明。
        """
        if isinstance(data, dict):
            node_type = data.get("node_type")
            configuration = data.get("configuration")
            if isinstance(configuration, dict) and node_type is not None and "node_type" not in configuration:
                new_configuration = {**configuration, "node_type": node_type}
                return {**data, "configuration": new_configuration}
        return data

    @property
    def effective_output_name(self) -> str:
        """返回有效输出名：显式声明的 output_name，否则回退为 node_id。

        设计意图：下游节点引用上游输出时统一使用该名称，避免空值分支处理。
        """
        return self.output_name or self.node_id
