# =============================================================================
# 执行级用量记录器（governance/metering/usage_recorder.py）
#
# 职责：执行级用量记录（每次工作流执行启动 1 条，roadmap B4）与
#      节点级用量记录（provider 透传真实 token 数，roadmap B7 W1b）。
# 设计意图：
#   1. 记账时点（D2 裁决）：run_and_record 内与 run-log begin_run 同点配对，
#      任意触发源（手动/恢复/重放/审批/cron）每次调用记 1 行；拒绝路径不计；
#   2. 执行级口径：node_id="execution"、model_identifier="workflow_engine"、
#      token 数 0 占位；节点级口径（B7 W1b）：node_id=节点标识、真实
#      token 数——节点级行不入执行级对账（reconcile 仅取执行级行）；
#   3. tenant 兜底：tenant_id 缺失（cron fire 无租户上下文）回退
#      settings.log_tenant_default（与 run_log/recorder.py 兜底同源，对账自洽）；
#   4. 分组 flush：按租户分组后逐租户 append_batch——Postgres append_batch 以
#      首条 tenant_id 建连，混合租户批次会错租户（正确性修复）；
#   5. 失败降级：记账/flush 失败不阻断执行主流程（INV-01）。
# =============================================================================

import logging
import uuid
from typing import Any, Dict, List, Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.governance.metering.models import TokenUsageRecord

logger = logging.getLogger(__name__)

# 执行级记账节点/模型标识（与节点级计量区分的固定口径）
EXECUTION_NODE_ID = "execution"
EXECUTION_MODEL_IDENTIFIER = "workflow_engine"


def _coerce_usage_int(value: Any) -> int:
    """provider usage 条目归一化：bool/异形计 0，数值/数字字符串取整（防御）。"""
    if isinstance(value, bool):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


class ExecutionUsageRecorder:
    """执行级用量记录器。

    每次执行启动记录一条 TokenUsageRecord（tokens=0 占位），经缓冲队列
    批量写入用量仓储；与 run-log begin 记录构成对账双源。
    """

    def __init__(self, repository, enabled: bool, buffer_queue=None) -> None:
        """构造记录器。

        参数：
            repository   —— 用量仓储（双后端 TokenUsageRepository，append/append_batch）
            enabled      —— 计量开关（settings.metering_enabled）
            buffer_queue —— 用量缓冲队列（可选；None 时直落仓储）
        """
        self._repository = repository
        self._enabled = enabled
        self._buffer_queue = buffer_queue

    async def record_execution_start(
        self,
        tenant_id: Optional[str],
        instance_id: str,
        workflow_definition_id: Optional[str] = None,
    ) -> Optional[str]:
        """执行启动记账：构造执行级记录并入队/落盘，返回 record_id。

        enabled=False 时 no-op 返回 None；tenant_id 缺失回退
        settings.log_tenant_default（与 run-log 兜底同源）。
        """
        if not self._enabled:
            return None

        record = self._build_record(tenant_id, instance_id, workflow_definition_id)
        return await self._persist(record, "执行级")

    async def record_node_usage(
        self,
        tenant_id: Optional[str],
        instance_id: str,
        node_id: str,
        agent_node_id: Optional[str],
        model_identifier: str,
        token_usage: Dict[str, Any],
        workflow_definition_id: Optional[str] = None,
    ) -> Optional[str]:
        """节点级用量记账（roadmap B7 W1b）：真实 token 数入队/落盘。

        与执行级同队列、同按租户分组刷盘；tenant 兜底同执行级口径
        （settings.log_tenant_default）。token_usage 期望
        {"prompt_tokens", "completion_tokens", "total_tokens"}（provider
        OpenAI 兼容形状）；缺失键/异形条目计 0（防御归一化）。
        enabled=False 时 no-op 返回 None。
        """
        if not self._enabled:
            return None

        record = self._build_node_record(
            tenant_id,
            instance_id,
            node_id,
            agent_node_id,
            model_identifier,
            token_usage,
            workflow_definition_id,
        )
        return await self._persist(record, "节点级")

    async def _persist(self, record: TokenUsageRecord, level_label: str) -> str:
        """入队或直落仓储（队列满直落兜底，不丢弃记账行）。"""
        if self._buffer_queue is not None:
            enqueued = await self._buffer_queue.enqueue(record)
            if enqueued:
                return record.record_id
            # 队列已满：直落仓储兜底（不丢弃记账行）
            logger.warning(
                "用量缓冲队列已满，%s记录直落仓储: instance_id=%s",
                level_label,
                record.instance_id,
            )

        await self._repository.append(record)
        return record.record_id

    def _build_record(
        self,
        tenant_id: Optional[str],
        instance_id: str,
        workflow_definition_id: Optional[str],
    ) -> TokenUsageRecord:
        """构造执行级用量记录（tokens=0 占位，node_id/model_identifier 固定口径）。"""
        resolved_tenant = tenant_id or get_settings().log_tenant_default
        return TokenUsageRecord(
            record_id=f"usage_{uuid.uuid4().hex[:16]}",
            tenant_id=resolved_tenant,
            instance_id=instance_id,
            node_id=EXECUTION_NODE_ID,
            model_identifier=EXECUTION_MODEL_IDENTIFIER,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            workflow_definition_id=workflow_definition_id,
        )

    def _build_node_record(
        self,
        tenant_id: Optional[str],
        instance_id: str,
        node_id: str,
        agent_node_id: Optional[str],
        model_identifier: str,
        token_usage: Dict[str, Any],
        workflow_definition_id: Optional[str],
    ) -> TokenUsageRecord:
        """构造节点级用量记录（真实 token 数，roadmap B7 W1b）。"""
        resolved_tenant = tenant_id or get_settings().log_tenant_default
        return TokenUsageRecord(
            record_id=f"usage_{uuid.uuid4().hex[:16]}",
            tenant_id=resolved_tenant,
            instance_id=instance_id,
            node_id=node_id,
            agent_node_id=agent_node_id,
            model_identifier=model_identifier,
            prompt_tokens=_coerce_usage_int(token_usage.get("prompt_tokens")),
            completion_tokens=_coerce_usage_int(token_usage.get("completion_tokens")),
            total_tokens=_coerce_usage_int(token_usage.get("total_tokens")),
            workflow_definition_id=workflow_definition_id,
        )

    async def _flush_to_repository(self, records: List[TokenUsageRecord]) -> None:
        """缓冲队列 flush_callback：按租户分组逐租户批量写入。

        Postgres append_batch 以首条 tenant_id 建连（RLS 上下文），
        混合租户批次会错租户——必须先按租户分组；单组失败吞异常告警，
        不中断其余组（失败组丢失登记告警，不回滚重试避免死循环）。
        """
        if not records:
            return

        grouped: Dict[str, List[TokenUsageRecord]] = {}
        for record in records:
            grouped.setdefault(record.tenant_id, []).append(record)

        for tenant_id, group in grouped.items():
            try:
                await self._repository.append_batch(group)
            except Exception:
                logger.exception(
                    "执行级用量记录批量写入失败: tenant_id=%s count=%d",
                    tenant_id,
                    len(group),
                )

    async def start(self) -> None:
        """启动缓冲队列定时刷新（应用 startup）。"""
        if self._buffer_queue is not None:
            await self._buffer_queue.start_periodic_flush()

    async def stop(self) -> None:
        """停止定时刷新并执行最终刷新（应用 shutdown）。"""
        if self._buffer_queue is not None:
            await self._buffer_queue.stop_periodic_flush()


# ---------------------------------------------------------------------------
# 模块注入点（set_domain_event_publisher 先例：装配期注入，路由期取用）
# ---------------------------------------------------------------------------

_usage_recorder: Optional[ExecutionUsageRecorder] = None


def set_usage_recorder(recorder: Optional[ExecutionUsageRecorder]) -> None:
    """注入执行级用量记录器（application_factory 装配期调用）。"""
    global _usage_recorder
    _usage_recorder = recorder


def get_usage_recorder() -> Optional[ExecutionUsageRecorder]:
    """取用执行级用量记录器；未装配返回 None（记账跳过，治理降级口径）。"""
    return _usage_recorder
