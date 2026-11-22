# =============================================================================
# 代码节点执行器（engine/nodes/code_node_executor.py）
#
# 职责：代码节点五段管线真执行器（llm_code_nodes_v2 任务 5.1/5.2 /
# DC-06 / DC-10 / DC-12 / DC-16 / spec §6.2）。
# 设计意图：
#   1. 入口先判能力开关 code_node_enabled（getattr 容错读取，字段缺失
#      视同关闭，FR-03-b），关闭 → CODE_NODE_DISABLED（不读配置与脚本
#      文本、metadata 无指纹，G-03 骨架保留）；
#   2. 段一 静态合规校验：复用既有守卫 validate_custom_code（零修改）+
#      classify_static_verdict 分流（语法失败 → CODE_SYNTAX_ERROR 含行号；
#      其余命中 → CODE_BANNED_CONSTRUCT 含 findings 清单，不含脚本全文）；
#   3. 段二 params 装配：assemble_params 三态语义（None 全量平铺 / []
#      显式空清单 / 非空选择集），失败透传 NODE_INPUT_REFERENCE_MISSING /
#      NODE_INPUT_TYPE_MISMATCH（fail-fast，脚本不执行）；
#   4. 段三 沙箱策略解析与护栏取小（DC-10）：sandbox_policy_id 非空经
#      仓储查询，查询不存在 → CODE_RUNTIME_ERROR（消息含「策略不存在」，
#      不静默降级）；timeout / max_bytes 取 settings 与策略较小值；
#      cpu/memory 上限登记不强制、network_whitelist 零消费（D-09）；
#   5. 段四 通道分派（DC-16）：code_execution_channel 非法值降级
#      inprocess + 告警日志；A1 受限进程内同步执行（默认）/ A2 子进程
#      异步执行（通道自身异常 fail-closed 归 CODE_RUNTIME_ERROR）；
#   6. 段五 result 提取与输出护栏（runner 内聚）：成功输出
#      {"result": <值>}（output_name 缺省回退 node_id）；失败按 DC-16
#      映射总表（同因同码 FR-33）；
#   7. metadata 登记：language / script_length（UTF-8 字节数）/
#      script_fingerprint（SHA-256 前 16 位，替代全文）/ static_check=
#      "pre_exec" / channel / duration；超时附加 abandoned_thread（A1）/
#      killed（A2）；
#   8. 违规发布（DC-10-3）：仅超时与输出超限构造 SandboxViolationRecord
#      经既有发布器异步发布（记录含类型与时长摘要、不含脚本全文）；发布
#      通道自身异常仅 warning 日志，不改变节点失败结果；
#   9. entry_function 零读取零校验（FR-18 展示语义）；dependencies 零
#      消费（FR-19 仅登记）；全部失败路径 output_value=None（INV-05 /
#      FR-34 禁伪成功）。
# 注：「受限进程内执行，非进程级隔离」（INV-06）；静态校验不等于运行期
#     安全性证明（INV-07）；A2 真实子进程运行为 N 级登记不测。
# =============================================================================

import hashlib
import logging
from typing import Dict, Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.errors import (
    CODE_NODE_DISABLED,
    CODE_OUTPUT_TOO_LARGE,
    CODE_RUNTIME_ERROR,
    CODE_TIMEOUT,
)
from orchestration_platform.domain.models.common import JsonValue
from orchestration_platform.domain.models.enums import NodeType, ViolationType
from orchestration_platform.domain.models.node_configurations import CodeNodeConfiguration
from orchestration_platform.engine.input_assembly import (
    AssemblyFailure,
    assemble_params,
)
from orchestration_platform.engine.nodes.base import (
    NodeExecutionContext,
    NodeExecutionResult,
)
from orchestration_platform.engine.nodes.code_execution.runner import (
    CodeRunFailure,
    CodeRunOutcome,
    _run_code_node_script,
    format_script_error,
)
from orchestration_platform.engine.nodes.code_execution.static_check import (
    classify_static_verdict,
)
from orchestration_platform.engine.nodes.code_execution.subprocess_runner import (
    run_script_subprocess,
)
from orchestration_platform.engine.nodes.data_transform_ops.custom_code_guard import (
    validate_custom_code,
)
from orchestration_platform.integrations.sandbox.abstractions.sandbox_policy_repository import (
    ISandboxPolicyRepository,
)
from orchestration_platform.integrations.sandbox.models import (
    SandboxExecutionResult,
    SandboxViolationRecord,
)
from orchestration_platform.integrations.sandbox.violation_publisher import (
    ViolationPublisher,
)

_logger = logging.getLogger(__name__)

# 合法执行通道取值域（settings.code_execution_channel；非法值降级 inprocess）
_VALID_CHANNELS: tuple = ("inprocess", "subprocess")


class CodeNodeExecutor:
    """代码节点执行器：静态校验 → params 装配 → 策略取小 → 通道执行 → 输出。"""

    def __init__(
        self,
        policy_repository: Optional[ISandboxPolicyRepository] = None,
        violation_publisher: Optional[ViolationPublisher] = None,
        tenant_id: str = "default",
    ) -> None:
        """构造代码节点执行器（依赖注入缺省 None → 未引用路径零开销）。"""
        self._settings = get_settings()
        self._policy_repository = policy_repository
        self._violation_publisher = violation_publisher
        self._tenant_id = tenant_id

    @property
    def node_type(self) -> NodeType:
        return NodeType.CODE

    async def execute(self, context: NodeExecutionContext) -> NodeExecutionResult:
        """执行代码节点：开关 → 静态校验 → 装配 → 策略取小 → 通道分派执行。"""
        node_id = context.node_definition.node_id

        # ① 能力开关：关闭 → CODE_NODE_DISABLED（不读配置与脚本、metadata 无指纹）
        if not getattr(self._settings, "code_node_enabled", False):
            return NodeExecutionResult(
                success=False,
                error_code=CODE_NODE_DISABLED,
                error_message=f"代码节点 {node_id} 功能未启用（开关 code_node_enabled 关闭）",
            )

        # ② 静态登记四基键（指纹为 SHA-256 摘要形态，替代全文）
        config: CodeNodeConfiguration = context.node_definition.configuration
        script_bytes = config.code.encode("utf-8")
        metadata: Dict[str, JsonValue] = {
            "language": config.language,
            "script_length": len(script_bytes),
            "script_fingerprint": hashlib.sha256(script_bytes).hexdigest()[:16],
            "static_check": "pre_exec",
        }

        # ③ 段一：静态合规校验（守卫零修改复用 + 分流映射；findings 不含全文）
        static_failure = classify_static_verdict(
            validate_custom_code(config.code, config.language)
        )
        if static_failure is not None:
            error_code, error_message = static_failure
            return NodeExecutionResult(
                success=False,
                error_code=error_code,
                error_message=f"代码节点 {node_id} {error_message}",
                metadata=metadata,
            )

        # ④ 段二：params 装配（三态语义；失败透传既有装配码，脚本不执行）
        params_or_failure = assemble_params(
            config.inputs, dict(context.shared_state.node_outputs)
        )
        if isinstance(params_or_failure, AssemblyFailure):
            return NodeExecutionResult(
                success=False,
                error_code=params_or_failure.error_code,
                error_message=f"代码节点 {node_id} {params_or_failure.error_message}",
                metadata=metadata,
            )
        params = params_or_failure.values

        # ⑤ 段三：沙箱策略解析与护栏取小（DC-10；策略不存在不静默降级）
        timeout_seconds: float = self._settings.code_execution_timeout_seconds
        output_max_bytes: int = self._settings.code_output_max_bytes
        policy_id = config.sandbox_policy_id or ""
        if policy_id:
            policy = None
            if self._policy_repository is not None:
                policy = await self._policy_repository.get(policy_id, self._tenant_id)
            if policy is None:
                return NodeExecutionResult(
                    success=False,
                    error_code=CODE_RUNTIME_ERROR,
                    error_message=(
                        f"代码节点 {node_id} 沙箱策略不存在：{policy_id}（不静默降级）"
                    ),
                    metadata=metadata,
                )
            timeout_seconds = min(timeout_seconds, policy.execution_timeout_seconds)
            output_max_bytes = min(output_max_bytes, policy.output_bytes_limit)

        # ⑥ 段四：通道分派（非法值降级 inprocess + 告警）→ A1/A2 执行
        channel = self._settings.code_execution_channel
        if channel not in _VALID_CHANNELS:
            _logger.warning(
                "代码节点 %s 执行通道配置非法：%r，已降级 inprocess"
                "（合法值：inprocess/subprocess）",
                node_id,
                channel,
            )
            channel = "inprocess"
        metadata["channel"] = channel

        if channel == "subprocess":
            try:
                outcome: CodeRunOutcome = await run_script_subprocess(
                    config.code, params, timeout_seconds, output_max_bytes
                )
            except Exception as exc:  # A2 通道自身异常 fail-closed（DC-16）
                outcome = CodeRunFailure(
                    error_code=CODE_RUNTIME_ERROR,
                    error_message=format_script_error(type(exc).__name__, str(exc)),
                    duration_seconds=0.0,
                )
        else:
            outcome = _run_code_node_script(
                config.code, params, timeout_seconds, output_max_bytes
            )
        metadata["duration"] = round(outcome.duration_seconds, 3)

        # ⑦/⑧ 段五：输出形态与违规发布（全部失败路径 output_value=None）
        if isinstance(outcome, CodeRunFailure):
            if outcome.error_code == CODE_TIMEOUT:
                if outcome.abandoned_thread:
                    metadata["abandoned_thread"] = True
                if outcome.killed:
                    metadata["killed"] = True
            await self._publish_violation_if_needed(outcome, context, policy_id)
            return NodeExecutionResult(
                success=False,
                error_code=outcome.error_code,
                error_message=f"代码节点 {node_id} {outcome.error_message}",
                metadata=metadata,
            )

        return NodeExecutionResult(
            success=True,
            output_name=context.node_definition.effective_output_name,
            output_value={"result": outcome.result},
            metadata=metadata,
        )

    async def _publish_violation_if_needed(
        self,
        failure: CodeRunFailure,
        context: NodeExecutionContext,
        policy_id: str,
    ) -> None:
        """违规发布（DC-10-3）：仅超时与输出超限发布；发布异常不改失败结果。

        记录含违规类型与时长摘要（detail 取失败消息），不含脚本全文；
        发布通道自身异常仅 warning 日志（业务失败结果不被吞）。
        """
        if self._violation_publisher is None:
            return
        if failure.error_code == CODE_TIMEOUT:
            violation_type = ViolationType.TIMEOUT
        elif failure.error_code == CODE_OUTPUT_TOO_LARGE:
            violation_type = ViolationType.RESOURCE_EXCEEDED
        else:
            return

        node_id = context.node_definition.node_id
        record = SandboxViolationRecord(
            violation_type=violation_type,
            detail=f"代码节点 {node_id}：{failure.error_message}",
        )
        sandbox_result = SandboxExecutionResult(
            success=False,
            violations=[record],
            timed_out=failure.error_code == CODE_TIMEOUT,
            error_code=failure.error_code,
            error_message=failure.error_message,
            execution_duration_seconds=failure.duration_seconds,
        )
        try:
            await self._violation_publisher.publish_violations(
                sandbox_result,
                context.instance_id,
                self._tenant_id,
                policy_id,
            )
        except Exception:
            _logger.warning(
                "代码节点 %s 违规记录发布失败（不改变节点失败结果）",
                node_id,
                exc_info=True,
            )
