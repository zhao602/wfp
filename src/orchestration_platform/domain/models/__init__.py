# =============================================================================
# 领域模型包（domain/models）
#
# 对外统一导出全部领域对象、枚举与 DTO，便于其他层以单点导入。
# =============================================================================

from orchestration_platform.domain.models.checkpoint import Checkpoint, CheckpointSummary
from orchestration_platform.domain.models.common import (
    InputParameter,
    JsonValue,
    OutputDeclaration,
    RetryPolicy,
    SourceLocation,
    ValidationIssue,
    ValidationReport,
)
from orchestration_platform.domain.models.edge_definition import EdgeDefinition
from orchestration_platform.domain.models.enums import (
    TERMINAL_INSTANCE_STATUSES,
    ControlFlowKind,
    EventType,
    InstanceStatus,
    NodeExecutionStatus,
    NodeType,
    ParallelFailureStrategy,
    ReplayMode,
    ReviewDecision,
    Severity,
    TriggerType,
)
from orchestration_platform.domain.models.execution_event import ExecutionEvent
from orchestration_platform.domain.models.execution_instance import (
    DefinitionRef,
    ExecutionInstance,
    ReplayRequest,
    ReplayResult,
    ReviewDecisionRequest,
    RollbackResult,
    StartInstanceRequest,
)
from orchestration_platform.domain.models.node_configurations import (
    AgentNodeConfiguration,
    BranchCondition,
    ControlFlowNodeConfiguration,
    DataSourceNodeConfiguration,
    NodeConfiguration,
    ToolNodeConfiguration,
    TriggerNodeConfiguration,
)
from orchestration_platform.domain.models.node_definition import NodeDefinition
from orchestration_platform.domain.models.shared_state import SharedState
from orchestration_platform.domain.models.workflow_definition import WorkflowDefinition

__all__ = [
    # 枚举
    "NodeType",
    "ControlFlowKind",
    "TriggerType",
    "InstanceStatus",
    "EventType",
    "ReplayMode",
    "ParallelFailureStrategy",
    "ReviewDecision",
    "NodeExecutionStatus",
    "Severity",
    "TERMINAL_INSTANCE_STATUSES",
    # 公共类型
    "JsonValue",
    "SourceLocation",
    "RetryPolicy",
    "InputParameter",
    "OutputDeclaration",
    "ValidationIssue",
    "ValidationReport",
    # 核心模型
    "WorkflowDefinition",
    "NodeDefinition",
    "EdgeDefinition",
    "NodeConfiguration",
    "AgentNodeConfiguration",
    "ToolNodeConfiguration",
    "DataSourceNodeConfiguration",
    "TriggerNodeConfiguration",
    "ControlFlowNodeConfiguration",
    "BranchCondition",
    "ExecutionInstance",
    "SharedState",
    "Checkpoint",
    "CheckpointSummary",
    "ExecutionEvent",
    # DTO
    "DefinitionRef",
    "StartInstanceRequest",
    "RollbackResult",
    "ReplayRequest",
    "ReplayResult",
    "ReviewDecisionRequest",
]
