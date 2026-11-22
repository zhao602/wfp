# =============================================================================
# NL 编排服务实现（governance/nl_orchestration/nl_orchestration_service.py）
#
# 职责：编排注入防护、意图解析、YAML 生成、修改应用与拓扑校验的完整流程。
# 设计意图（对应偏差修正 spec 5.1.1 / design 模块A NLOrchestrationService）：
#   - create_from_nl：注入防护 → 意图解析 → YAML 生成 → 拓扑校验 → 返回结果
#   - modify_from_nl：注入防护 → 意图解析 → 修改应用 → 拓扑校验 → 返回结果
#   - 拓扑校验失败时返回 TOPO_INVALID 及校验错误清单，不交付编排引擎
#   - 自纠错循环（DC-04）：解析失败 / YAML 反解失败 / 拓扑校验失败时，把
#     问题清单回灌给意图解析器重新生成，最多 nl_orchestration_max_correction_rounds
#     轮；实际轮次如实回填 correction_rounds。LLM 不可用等不可修复错误不重试。
# =============================================================================

import logging
from typing import Any, List, Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.errors import WorkflowValidationError
from orchestration_platform.domain.models.enums import IntentType, NLErrorCode
from orchestration_platform.domain.models.workflow_definition import WorkflowDefinition
from orchestration_platform.engine.graph_compiler import GraphCompiler
from orchestration_platform.governance.nl_orchestration.abstractions.nl_intent_parser import (
    INLIntentParser,
)
from orchestration_platform.governance.nl_orchestration.injection_guard import InjectionGuard
from orchestration_platform.governance.nl_orchestration.models import NLOrchestrationResult
from orchestration_platform.governance.nl_orchestration.workflow_modifier import WorkflowModifier
from orchestration_platform.governance.nl_orchestration.yaml_generator import YamlGenerator
from orchestration_platform.workflow.yaml_workflow_parser import YamlWorkflowParser

_logger = logging.getLogger(__name__)

# 回灌给 LLM 的问题条目上限（避免提示词膨胀；超出部分只报数量）
_MAX_FEEDBACK_ITEMS = 12

# 不可修复错误标记：命中即不重试（重试只会放大延迟，不会改善结果）
# 传输层故障（网络/代理/超时）重试同一请求不会改变通路状态，故一并视为不可修复；
# 服务端 5xx 不在此列——属于偶发上游故障，回灌重试有实际收益。
_NON_REPAIRABLE_MARKERS = (
    "LLM 调用异常",
    "LLM 请求网络错误",
    "LLM 请求超时",
    "主备模型均不可用",
    "LLM 不可用",
    "NODE_LLM_UNAVAILABLE",
)


def _is_repairable(message: str) -> bool:
    """判断错误是否值得回灌 LLM 重试（配置类/连接类错误不重试）。"""
    return not any(marker in message for marker in _NON_REPAIRABLE_MARKERS)


def _format_feedback(messages: List[str]) -> str:
    """把问题清单格式化为回灌提示词（截断超量条目）。"""
    head = messages[:_MAX_FEEDBACK_ITEMS]
    text = "\n".join(f"- {item}" for item in head)
    extra = len(messages) - len(head)
    if extra > 0:
        text += f"\n- 另有 {extra} 条同类问题，请一并检查字段名、枚举值与连线结构"
    return text


def _describe_validation_error(exc: Exception) -> str:
    """从校验异常中提取可回灌的问题清单（优先取结构校验 issues）。"""
    if isinstance(exc, WorkflowValidationError):
        issues = (exc.details or {}).get("issues") if isinstance(exc.details, dict) else None
        if isinstance(issues, list):
            messages = [
                str(issue.get("message", ""))
                for issue in issues
                if isinstance(issue, dict) and issue.get("message")
            ]
            if messages:
                return "\n".join(f"- {item}" for item in messages)
        return str(exc.message)
    return str(exc)


class NLOrchestrationService:
    """自然语言编排服务。

    编排注入防护、意图解析、YAML 生成、修改应用与拓扑校验的完整流程。
    """

    def __init__(
        self,
        intent_parser: INLIntentParser,
        yaml_generator: YamlGenerator,
        workflow_modifier: WorkflowModifier,
        injection_guard: InjectionGuard,
        graph_compiler: GraphCompiler,
        yaml_parser: Optional[YamlWorkflowParser] = None,
    ) -> None:
        """初始化 NL 编排服务。

        参数：
            intent_parser    —— NL 意图解析器
            yaml_generator   —— YAML 定义生成器
            workflow_modifier—— 既有工作流修改应用器
            injection_guard  —— 注入防护校验器
            graph_compiler   —— 图编译器（用于拓扑校验）
            yaml_parser      —— YAML 解析器（用于反向解析生成的 YAML）
        """
        self._intent_parser = intent_parser
        self._yaml_generator = yaml_generator
        self._workflow_modifier = workflow_modifier
        self._injection_guard = injection_guard
        self._graph_compiler = graph_compiler
        self._yaml_parser = yaml_parser or YamlWorkflowParser()

    # ------------------------------------------------------------------
    # 创建
    # ------------------------------------------------------------------
    async def create_from_nl(
        self,
        directive: str,
        tenant_id: str,
        user_id: str,
    ) -> NLOrchestrationResult:
        """从自然语言指令创建工作流定义。

        流程：注入防护 → 意图解析 → YAML 生成 → 拓扑校验 →（失败则自纠错重试）。
        """
        # 1. 注入防护
        guard_result = self._injection_guard.check(directive)
        if not guard_result.passed:
            _logger.warning("NL 编排注入防护拦截: %s", guard_result.reason)
            return NLOrchestrationResult(
                success=False,
                error_code=NLErrorCode.INJECTION_DETECTED,
                error_message=guard_result.reason,
            )

        max_rounds = self._max_correction_rounds()
        rounds = 0
        feedback: Optional[str] = None

        while True:
            # 2. 意图解析（失败可回灌重试）
            try:
                parsed_intent = await self._intent_parser.parse(
                    directive=directive,
                    intent_type=IntentType.CREATE_WORKFLOW,
                    feedback=feedback,
                )
            except ValueError as exc:
                message = str(exc)
                if _is_repairable(message) and rounds < max_rounds:
                    rounds += 1
                    feedback = message
                    _logger.info("NL 编排自纠错第 %d 轮（意图解析失败）: %s", rounds, message)
                    continue
                return NLOrchestrationResult(
                    success=False,
                    error_code=NLErrorCode.NL_PARSE_FAILED,
                    error_message=message,
                    correction_rounds=rounds,
                )

            # 3. YAML 生成
            yaml_text = self._yaml_generator.generate(parsed_intent)

            # 4. 反向解析为 WorkflowDefinition（结构校验失败可回灌重试）
            try:
                definition = self._yaml_parser.parse(yaml_text)
            except Exception as exc:  # noqa: BLE001 —— 各类校验异常统一降级
                _logger.error("NL 编排 YAML 反向解析失败: %s", exc)
                if rounds < max_rounds:
                    rounds += 1
                    feedback = _describe_validation_error(exc)
                    _logger.info("NL 编排自纠错第 %d 轮（YAML 反解失败）", rounds)
                    continue
                return NLOrchestrationResult(
                    success=False,
                    error_code=NLErrorCode.NL_PARSE_FAILED,
                    error_message=f"生成的 YAML 无法解析: {exc}",
                    correction_rounds=rounds,
                )

            # 5. 拓扑校验（失败可回灌重试）
            validation_report = self._graph_compiler.validate_topology(definition)
            if validation_report.valid:
                return NLOrchestrationResult(
                    success=True,
                    yaml_text=yaml_text,
                    definition=definition,
                    validation_report=validation_report,
                    correction_rounds=rounds,
                )

            error_messages = [issue.message for issue in validation_report.issues]
            _logger.warning("NL 编排拓扑校验失败: %s", error_messages)
            if rounds < max_rounds:
                rounds += 1
                feedback = _format_feedback(error_messages)
                _logger.info("NL 编排自纠错第 %d 轮（拓扑校验失败）", rounds)
                continue
            return NLOrchestrationResult(
                success=False,
                yaml_text=yaml_text,
                definition=definition,
                validation_report=validation_report,
                error_code=NLErrorCode.TOPO_INVALID,
                error_message=f"拓扑校验失败: {'; '.join(error_messages)}",
                correction_rounds=rounds,
            )

    # ------------------------------------------------------------------
    # 修改
    # ------------------------------------------------------------------
    async def modify_from_nl(
        self,
        directive: str,
        target_definition: WorkflowDefinition,
        tenant_id: str,
        user_id: str,
    ) -> NLOrchestrationResult:
        """从自然语言修改指令更新既有工作流定义。

        流程：注入防护 → 意图解析 → 修改应用 → 拓扑校验 →（失败则自纠错重试）。
        """
        # 1. 注入防护
        guard_result = self._injection_guard.check(directive)
        if not guard_result.passed:
            _logger.warning("NL 编排注入防护拦截: %s", guard_result.reason)
            return NLOrchestrationResult(
                success=False,
                error_code=NLErrorCode.INJECTION_DETECTED,
                error_message=guard_result.reason,
            )

        max_rounds = self._max_correction_rounds()
        rounds = 0
        feedback: Optional[str] = None

        while True:
            # 2. 意图解析
            try:
                parsed_intent = await self._intent_parser.parse(
                    directive=directive,
                    intent_type=IntentType.MODIFY_WORKFLOW,
                    target_definition=target_definition,
                    feedback=feedback,
                )
            except ValueError as exc:
                message = str(exc)
                if _is_repairable(message) and rounds < max_rounds:
                    rounds += 1
                    feedback = message
                    _logger.info("NL 编排自纠错第 %d 轮（意图解析失败）: %s", rounds, message)
                    continue
                return NLOrchestrationResult(
                    success=False,
                    error_code=NLErrorCode.NL_PARSE_FAILED,
                    error_message=message,
                    correction_rounds=rounds,
                )

            # 3. 修改应用（节点不存在等可回灌重试）
            try:
                updated_definition = self._workflow_modifier.apply_modification(
                    base_definition=target_definition,
                    parsed_intent=parsed_intent,
                )
            except ValueError as exc:
                message = str(exc)
                if rounds < max_rounds:
                    rounds += 1
                    feedback = message
                    _logger.info("NL 编排自纠错第 %d 轮（修改应用失败）: %s", rounds, message)
                    continue
                error_code = (
                    NLErrorCode.NODE_NOT_FOUND
                    if "NODE_NOT_FOUND" in message
                    else NLErrorCode.NL_PARSE_FAILED
                )
                return NLOrchestrationResult(
                    success=False,
                    error_code=error_code,
                    error_message=message,
                    correction_rounds=rounds,
                )

            # 4. 序列化为 YAML
            import yaml

            yaml_text = yaml.dump(
                updated_definition.model_dump(mode="json"),
                allow_unicode=True,
                default_flow_style=False,
                sort_keys=False,
            )

            # 5. 拓扑校验
            validation_report = self._graph_compiler.validate_topology(updated_definition)
            if validation_report.valid:
                return NLOrchestrationResult(
                    success=True,
                    yaml_text=yaml_text,
                    definition=updated_definition,
                    validation_report=validation_report,
                    correction_rounds=rounds,
                )

            error_messages = [issue.message for issue in validation_report.issues]
            _logger.warning("NL 编排拓扑校验失败（修改）: %s", error_messages)
            if rounds < max_rounds:
                rounds += 1
                feedback = _format_feedback(error_messages)
                _logger.info("NL 编排自纠错第 %d 轮（拓扑校验失败）", rounds)
                continue
            return NLOrchestrationResult(
                success=False,
                yaml_text=yaml_text,
                definition=updated_definition,
                validation_report=validation_report,
                error_code=NLErrorCode.TOPO_INVALID,
                error_message=f"拓扑校验失败: {'; '.join(error_messages)}",
                correction_rounds=rounds,
            )

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    @staticmethod
    def _max_correction_rounds() -> int:
        """读取自纠错轮次上限（缺省 2，负数按 0 处理）。"""
        settings = get_settings()
        value: Any = getattr(settings, "nl_orchestration_max_correction_rounds", 0)
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0
