# =============================================================================
# 数据转换操作注册表（engine/nodes/data_transform_ops/registry.py）
#
# 职责：定义操作处理器统一契约与注册表（expand_data_transform_ops DC-02）。
# 设计意图：
#   1. OperationHandler（Protocol）：每步操作为独立可测纯函数边界，
#      apply 不抛异常，失败经 StepOutcome.failure_code（fail-fast 语义）；
#   2. StepOutcome 两级传播：failure_code（失败）/ degraded+warnings（降级告警），
#      消除既有 executor 的静默吞异常不确定态（OP-12）；
#   3. OPERATION_REGISTRY 为唯一分派权威；未注册操作经 get_handler 返回 None，
#      由调用方 fail-fast DATA_TRANSFORM_UNKNOWN_OPERATION（无静默降级）；
#   4. 资源上限常量集中定义（INV-15）。
# 约束：Python 3.10 兼容（禁 StrEnum/Self/TaskGroup/PEP695）。
# =============================================================================

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

from orchestration_platform.domain.models.enums import DataTransformOperation
from orchestration_platform.domain.models.node_configurations import DataTransformStep

# ---------------------------------------------------------------------------
# 资源上限常量（INV-15，design §2.6.4 集中定义）
# ---------------------------------------------------------------------------

MAX_CHAIN_STEPS = 20                 # 操作链步数上限（超限 fail-fast DATA_TRANSFORM_CHAIN_TOO_LONG）
MAX_REGEX_PATTERN_LENGTH = 512       # 正则模式长度上限（字符，超限拒绝）
MAX_REGEX_INPUT_LENGTH = 8192        # 正则输入字段值长度上限（字符，超限拒绝）
MAX_EXPLODE_ROWS = 100000            # explode 展开后总行数上限（超限 fail-fast）
DEFAULT_FLATTEN_MAX_DEPTH = 3        # flatten 打平深度缺省值（超深度截断 + 告警）
MAX_JSON_TEXT_LENGTH = 256 * 1024    # JSON 解析文本长度上限（字节，超限该行置空 + 计数）


# ---------------------------------------------------------------------------
# 步骤上下文与结果（DC-02 第 1 条）
# ---------------------------------------------------------------------------


@dataclass
class StepContext:
    """操作步骤执行上下文。

    step_index              —— 步骤序号（0 起，错误定位用）
    step                    —— 当前步骤（operation + params）
    left_dataset            —— 上一步输出数据集（首步为主输入装配结果）
    shared_state_snapshot   —— 共享状态只读快照（join 副引用装配用）
    """

    step_index: int
    step: DataTransformStep
    left_dataset: List[Dict[str, Any]]
    shared_state_snapshot: Dict[str, Any]


@dataclass
class StepOutcome:
    """操作步骤执行结果（不抛异常，两级传播）。

    value         —— 本步输出数据集
    degraded      —— 是否降级（置空 + 计数，不中断链）
    warnings      —— 降级/缺口告警（累加至节点 metadata.warnings）
    failure_code  —— 非 None 即 fail-fast（映射 DATA_TRANSFORM_* 错误码）
    failure_message —— fail-fast 的人类可读说明（含步骤定位）
    """

    value: List[Dict[str, Any]]
    degraded: bool = False
    warnings: List[str] = field(default_factory=list)
    failure_code: Optional[str] = None
    failure_message: Optional[str] = None

    @staticmethod
    def failed(code: str, message: str) -> "StepOutcome":
        """构造 fail-fast 结果（value 为空占位，调用方不得使用）。"""
        return StepOutcome(value=[], failure_code=code, failure_message=message)


class OperationHandler(Protocol):
    """操作处理器统一契约（DC-02；实现为无状态纯函数集合）。

    operation   —— 本处理器承担的操作类型
    degradable  —— 声明本步是否可降级（无匹配/缺口置空 + 计数、不中断链）；
                   未声明可降级者一律 fail-fast（OP-12）
    """

    operation: DataTransformOperation
    degradable: bool

    def apply(self, ctx: StepContext) -> StepOutcome:
        """执行本步操作；不抛异常，失败经 StepOutcome.failure_code。"""
        ...


# ---------------------------------------------------------------------------
# 注册表（DC-02 第 2 条：唯一分派权威）
# ---------------------------------------------------------------------------

OPERATION_REGISTRY: Dict[DataTransformOperation, OperationHandler] = {}


def register(handler: OperationHandler) -> None:
    """注册操作处理器；重复注册同一操作为编程错误（fail-fast）。"""
    existing = OPERATION_REGISTRY.get(handler.operation)
    if existing is not None and existing is not handler:
        raise RuntimeError(
            f"数据转换操作重复注册：{handler.operation.value}"
        )
    OPERATION_REGISTRY[handler.operation] = handler


def get_handler(operation: DataTransformOperation) -> Optional[OperationHandler]:
    """查询操作处理器；未注册返回 None（调用方 fail-fast DATA_TRANSFORM_UNKNOWN_OPERATION）。"""
    return OPERATION_REGISTRY.get(operation)


def registered_operations() -> List[DataTransformOperation]:
    """返回已注册操作列表（按枚举声明序）。"""
    return [op for op in DataTransformOperation if op in OPERATION_REGISTRY]
