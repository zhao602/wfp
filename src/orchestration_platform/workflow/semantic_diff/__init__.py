# =============================================================================
# 语义 diff 包初始化（workflow/semantic_diff/__init__.py）
#
# 职责（arch_review_v2 6.1，CHG-16）：声明包结构并重导出公共契约，
# 供 CLI diff 命令（6.3 串行合入）与单测（6.2）直接引用。
# 落点 workflow/ 使契约域自洽（E13）。
# =============================================================================

from orchestration_platform.workflow.semantic_diff.engine import SemanticDiffEngine
from orchestration_platform.workflow.semantic_diff.models import (
    DiffChange,
    DiffChangeKind,
    SemanticDiffReport,
)

__all__ = [
    "SemanticDiffEngine",
    "SemanticDiffReport",
    "DiffChange",
    "DiffChangeKind",
]
