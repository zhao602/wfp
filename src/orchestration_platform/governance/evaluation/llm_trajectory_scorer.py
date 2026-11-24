# =============================================================================
# LLM 轨迹评分器（governance/evaluation/llm_trajectory_scorer.py）
#
# 职责：可选轨迹评分器，调用 LLM 对轨迹进行辅助评分。
# 设计意图（对应偏差修正 T186）：
#   1. 仅在 evaluation_llm_scoring_enabled=True 时启用；
#   2. LLM 评分失败时回退至规则评分（RuleBasedTrajectoryScorer），保证可用性；
#   3. 温度取 evaluation_llm_temperature（默认 0.0 保证确定性）；
#   4. 评分器类型标记为 llm（成功）或 rule_based（回退）。
# =============================================================================

import logging
from datetime import datetime, timezone
from typing import Any, Optional, Protocol, runtime_checkable

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.governance.evaluation.models import (
    AgentTrajectory,
    ClearScore,
    EvaluationScenario,
    TrajectoryScore,
)
from orchestration_platform.governance.evaluation.rule_based_trajectory_scorer import (
    RuleBasedTrajectoryScorer,
)

_logger = logging.getLogger(__name__)


@runtime_checkable
class ILlmClient(Protocol):
    """LLM 客户端抽象（最小契约）。"""

    async def complete(self, prompt: str, temperature: float) -> str:
        """给定提示词与温度，返回补全文本。"""
        ...


class LlmTrajectoryScorer:
    """LLM 轨迹评分器（可选）。

    调用 LLM 对轨迹进行辅助评分；LLM 失败时回退至规则评分。
    需通过构造注入 ILlmClient 实例。
    """

    def __init__(
        self,
        llm_client: Optional[Any] = None,
        fallback: Optional[RuleBasedTrajectoryScorer] = None,
    ) -> None:
        """构造 LLM 评分器。

        参数：
            llm_client —— LLM 客户端（实现 ILlmClient）；None 时评分始终回退。
            fallback   —— 回退规则评分器；缺省新建。
        """
        self._llm_client = llm_client
        self._fallback = fallback or RuleBasedTrajectoryScorer()

    async def score(
        self,
        trajectory: AgentTrajectory,
        scenario: EvaluationScenario,
    ) -> TrajectoryScore:
        """对单条轨迹评分（LLM 辅助，失败回退规则）。"""
        if self._llm_client is None:
            _logger.debug("LLM 客户端未注入，回退规则评分")
            return await self._fallback.score(trajectory, scenario)

        try:
            settings = get_settings()
            prompt = self._build_prompt(trajectory, scenario)
            raw = await self._llm_client.complete(
                prompt, settings.evaluation_llm_temperature
            )
            clear_score = self._parse_llm_response(raw)
            return TrajectoryScore(
                trajectory_id=trajectory.trajectory_id,
                scenario_id=scenario.scenario_id,
                clear_score=clear_score,
                scorer_type="llm",
                scored_at=datetime.now(timezone.utc),
            )
        except Exception as exc:  # noqa: BLE001
            _logger.warning("LLM 评分失败，回退规则评分: %s", exc)
            return await self._fallback.score(trajectory, scenario)

    @staticmethod
    def _build_prompt(trajectory: AgentTrajectory, scenario: EvaluationScenario) -> str:
        """构造 LLM 评分提示词。"""
        return (
            "你是 Agent 效能评估专家。请对以下 Agent 执行轨迹按 CLEAR 四维评分，"
            "每维 [0, 1]，越高越好。\n"
            "CLEAR 维度：\n"
            "  C - Cost-efficiency（成本效率）\n"
            "  R - Reliability（可靠性）\n"
            "  L - Latency（延迟，越低越高）\n"
            "  P - Policy-compliance（策略合规）\n\n"
            f"场景名称：{scenario.name}\n"
            f"输入提示：{scenario.input_prompt}\n"
            f"期望关键词：{', '.join(scenario.expected_keywords)}\n"
            f"Agent 最终输出：{trajectory.final_output}\n"
            f"总成本：{trajectory.total_cost}\n"
            f"总延迟(ms)：{trajectory.total_latency_ms}\n"
            f"工具调用次数：{trajectory.tool_call_count}\n"
            f"是否成功：{trajectory.success}\n\n"
            "请严格按以下 JSON 格式返回（不要附加其他内容）：\n"
            '{"cost_efficiency": 0.0, "reliability": 0.0, '
            '"latency": 0.0, "policy_compliance": 0.0, "rationale": "..."}'
        )

    @staticmethod
    def _parse_llm_response(raw: str) -> ClearScore:
        """解析 LLM 返回的 JSON 评分为 ClearScore。

        异常映射：解析失败抛出 ValueError，由上层捕获回退。
        """
        import json

        # 容忍 LLM 在 JSON 前后附加的文字：提取首个 { ... } 块
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("LLM 响应未包含合法 JSON 对象")
        payload = json.loads(raw[start : end + 1])

        def _clamp(v: float) -> float:
            return max(0.0, min(1.0, float(v)))

        cost = _clamp(payload.get("cost_efficiency", 0.0))
        reliability = _clamp(payload.get("reliability", 0.0))
        latency = _clamp(payload.get("latency", 0.0))
        policy = _clamp(payload.get("policy_compliance", 1.0))
        overall = 0.25 * (cost + reliability + latency + policy)
        rationale = str(payload.get("rationale", "LLM 评分"))

        return ClearScore(
            cost_efficiency=cost,
            reliability=reliability,
            latency=latency,
            policy_compliance=policy,
            overall=_clamp(overall),
            rationale=rationale,
        )
