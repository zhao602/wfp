# =============================================================================
# 领域公共类型（domain/models/common.py）
#
# 职责：定义跨模型复用的基础类型——JSON 值类型别名、源位置、重试策略、
#      输入参数声明、输出声明、校验问题与校验报告。
# 设计意图：
#   1. 用递归联合类型 JsonValue 替代裸 Any，保证事件载荷与节点输出的类型安全；
#   2. ValidationIssue 携带 node_id / edge_id / location，满足「校验结果可定位」要求。
# =============================================================================

from typing import Dict, List, Optional, Union

from pydantic import BaseModel, Field
from typing_extensions import TypeAliasType

from orchestration_platform.domain.models.enums import Severity

# 递归 JSON 值类型：用于事件载荷、节点输出、共享状态值等结构化数据字段。
# 说明（为什么这样写）：Python 3.10 下若使用隐式递归别名
# （JsonValue = Union[..., List["JsonValue"], ...]），pydantic 在构建 schema 时
# 会把它当作无限递归并抛出 RecursionError；改用 typing_extensions.TypeAliasType
# 声明「具名递归类型」（等价 PEP 695 能力），pydantic 可正确生成递归 schema。
JsonValue = TypeAliasType(
    "JsonValue",
    Union[str, int, float, bool, None, List["JsonValue"], Dict[str, "JsonValue"]],
)


class SourceLocation(BaseModel):
    """YAML 源位置：用于语法错误与结构错误的行列定位。"""

    line: int = Field(description="出错行号（从 1 开始）")
    column: int = Field(description="出错列号（从 1 开始）")


class RetryPolicy(BaseModel):
    """节点重试策略（对应需求规格 6.2.6）。未配置时默认不重试。"""

    max_retries: int = Field(default=0, ge=0, description="最大重试次数")
    retry_interval_seconds: int = Field(default=0, ge=0, description="重试间隔（秒）")


class InputParameter(BaseModel):
    """工作流输入参数声明（对应需求规格 6.1.6）。"""

    name: str = Field(description="参数名")
    type: str = Field(default="string", description="参数类型：string/number/boolean/object/array")
    required: bool = Field(default=True, description="是否必填")
    description: str = Field(default="", description="参数说明")
    default: Optional[JsonValue] = Field(default=None, description="默认值（非必填时生效）")


class OutputDeclaration(BaseModel):
    """工作流输出声明（对应需求规格 6.1.7）。"""

    name: str = Field(description="输出字段名")
    type: str = Field(default="object", description="输出类型")
    description: str = Field(default="", description="输出说明")


class ValidationIssue(BaseModel):
    """单条校验问题：错误码 + 严重级别 + 定位信息 + 人类可读说明。

    定位信息用于画布高亮（node_id / edge_id）与 YAML 定位（location）。
    """

    code: str = Field(description="错误码，如 WF_CYCLIC_GRAPH")
    severity: Severity = Field(default=Severity.ERROR, description="严重级别")
    message: str = Field(description="人类可读说明")
    node_id: Optional[str] = Field(default=None, description="关联节点标识（供画布定位）")
    edge_id: Optional[str] = Field(default=None, description="关联连线标识（供画布定位）")
    location: Optional[SourceLocation] = Field(default=None, description="YAML 行列号")
    field: Optional[str] = Field(default=None, description="关联配置字段（字段级定位，B13 U3）")


class ValidationReport(BaseModel):
    """校验报告：一次性聚合全部问题，不因首错终止（对应需求规格 5.1.1.7）。"""

    valid: bool = Field(description="是否整体通过校验")
    issues: List[ValidationIssue] = Field(default_factory=list, description="问题清单")
    definition_id: Optional[str] = Field(default=None, description="被校验定义的标识")
