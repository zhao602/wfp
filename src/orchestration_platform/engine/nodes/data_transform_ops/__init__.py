# =============================================================================
# 数据转换操作包入口（engine/nodes/data_transform_ops/__init__.py）
#
# 职责：导出操作协议与注册表（expand_data_transform_ops CHG-B01）。
# 处理器模块（handlers_*）在后续批次落地后由本包统一导入触发注册。
# =============================================================================

from orchestration_platform.engine.nodes.data_transform_ops.registry import (
    DEFAULT_FLATTEN_MAX_DEPTH,
    MAX_CHAIN_STEPS,
    MAX_DATASET_ROWS,
    MAX_EXPLODE_ROWS,
    MAX_JSON_TEXT_LENGTH,
    MAX_REGEX_INPUT_LENGTH,
    MAX_REGEX_PATTERN_LENGTH,
    OPERATION_REGISTRY,
    WARN_DATASET_ROWS,
    OperationHandler,
    StepContext,
    StepOutcome,
    get_handler,
    register,
    registered_operations,
)

# 处理器模块导入即注册（DC-02：模块导入时注册到 OPERATION_REGISTRY）
from orchestration_platform.engine.nodes.data_transform_ops import (  # noqa: F401,E402
    handlers_ai,
    handlers_cleaning,
    handlers_dev,
    handlers_structure,
)

__all__ = [
    "DEFAULT_FLATTEN_MAX_DEPTH",
    "MAX_CHAIN_STEPS",
    "MAX_DATASET_ROWS",
    "MAX_EXPLODE_ROWS",
    "MAX_JSON_TEXT_LENGTH",
    "MAX_REGEX_INPUT_LENGTH",
    "MAX_REGEX_PATTERN_LENGTH",
    "OPERATION_REGISTRY",
    "WARN_DATASET_ROWS",
    "OperationHandler",
    "StepContext",
    "StepOutcome",
    "get_handler",
    "register",
    "registered_operations",
]
