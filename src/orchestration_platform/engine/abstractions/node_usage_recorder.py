# =============================================================================
# 节点用量计量器协议（engine/abstractions/node_usage_recorder.py）
#
# 职责（roadmap M4 B10 / design D4）：engine-owned 计量缝隙协议。
#   - Scheduler 经 ctor 尾参注入 NodeUsageRecorder 实例（缺省 None 跳过
#     计量），不感知 governance 模块（B10 门禁域约束）；
#   - 生产实现：governance/metering/usage_recorder.UsageRecorder（结构化
#     类型隐式满足本协议；API 进程于 application_factory 装配后由宿主注入）；
#   - 签名对齐 UsageRecorder.record_node_usage 实签名与 scheduler 调用点。
# =============================================================================

from typing import Any, Protocol


class NodeUsageRecorder(Protocol):
    """节点级用量计量器协议（结构化类型，governance 实现隐式满足）。"""

    async def record_node_usage(
        self,
        tenant_id: str | None,
        instance_id: str,
        node_id: str,
        agent_node_id: str | None,
        model_identifier: str,
        token_usage: dict[str, Any],
        workflow_definition_id: str | None = None,
    ) -> str | None:
        """记录单节点 token 用量，返回记录行 id（异常由调用方吞并告警）。"""
        ...
