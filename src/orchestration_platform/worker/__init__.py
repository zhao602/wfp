# =============================================================================
# 执行 Worker 包（worker/）
#
# 职责（roadmap-v3 中期窗口 M2，F1 执行无状态化）：
#   1. 独立 worker 进程：python -m orchestration_platform.worker；
#   2. 认领执行队列作业、心跳续租、complete/fail 终态流转；
#   3. 启动期接管租约过期作业（kill -9 后 at-least-once 续跑）；
#   4. 事件持久化双写（PersistingInstanceRegistry）供 API 读穿透。
# =============================================================================

from orchestration_platform.worker.execution_worker import (
    ExecutionWorker,
    PersistingInstanceRegistry,
    WorkerJobReconstructionError,
    main,
    run_claimed_job,
)

__all__ = [
    "ExecutionWorker",
    "PersistingInstanceRegistry",
    "WorkerJobReconstructionError",
    "main",
    "run_claimed_job",
]
