# TODO(tech-debt): B档候选孤儿（族群 B15），待 APM 链路追踪专项排查确认无调用后清理，见 docs/tech-debt-ledger.md
# =============================================================================
# 上下文管理器（context_engine/context_manager.py）
#
# 职责：统一管理 Agent 执行上下文的组装、更新与裁剪。
# 设计意图（对应 spec 5.4、design 2.7、任务 T35）：
#   1. 上下文由四部分组成：系统提示、历史对话、工具结果、共享状态引用；
#   2. 组装上下文时检查总 Token 数，超限时调用窗口策略裁剪；
#   3. 支持跨节点上下文传递与增量更新。
# =============================================================================

from typing import Any, Dict, List, Optional

from orchestration_platform.context_engine.models import (
    AgentContext,
    ConversationTurn,
    ToolResultEntry,
)
from orchestration_platform.domain.models.enums import ContextWindowStrategyType


class ContextManager:
    """上下文管理器：组装、更新与裁剪 Agent 执行上下文。

    构造注入：
        max_token_limit —— 模型上下文 Token 上限（默认 4096）
        window_strategy —— 窗口策略配置（可选，超限时自动应用）
    """

    def __init__(
        self,
        max_token_limit: int = 4096,
        window_strategy_config: Any = None,
    ) -> None:
        self._max_token_limit = max_token_limit
        self._window_strategy_config = window_strategy_config

    def build_context(
        self,
        instance_id: str,
        node_id: str,
        system_prompt: str,
        conversation_history: Optional[List[ConversationTurn]] = None,
        tool_results: Optional[List[ToolResultEntry]] = None,
        state_references: Optional[Dict[str, Any]] = None,
    ) -> AgentContext:
        """组装 Agent 执行上下文。

        Args:
            instance_id: 实例标识
            node_id: 节点标识
            system_prompt: 系统提示词
            conversation_history: 历史对话轮次
            tool_results: 工具调用结果列表
            state_references: 共享状态字段引用快照

        Returns:
            组装完成的 AgentContext（已计算 Token 计数）。
        """
        context = AgentContext(
            instance_id=instance_id,
            node_id=node_id,
            system_prompt=system_prompt,
            conversation_history=conversation_history or [],
            tool_results=tool_results or [],
            state_references=state_references or {},
            max_token_limit=self._max_token_limit,
        )

        # 计算 Token 计数
        context.total_token_count = self._estimate_tokens(context)

        # 超限时应用窗口策略裁剪
        if context.total_token_count > self._max_token_limit:
            context = self._apply_window_strategy(context)

        return context

    def add_conversation_turn(
        self, context: AgentContext, turn: ConversationTurn
    ) -> AgentContext:
        """向上下文追加一轮对话并重新检查 Token 限制。"""
        context.conversation_history.append(turn)
        context.total_token_count = self._estimate_tokens(context)

        if context.total_token_count > context.max_token_limit:
            context = self._apply_window_strategy(context)

        return context

    def add_tool_result(
        self, context: AgentContext, result: ToolResultEntry
    ) -> AgentContext:
        """向上下文追加工具调用结果。"""
        context.tool_results.append(result)
        context.total_token_count = self._estimate_tokens(context)

        if context.total_token_count > context.max_token_limit:
            context = self._apply_window_strategy(context)

        return context

    def _estimate_tokens(self, context: AgentContext) -> int:
        """估算上下文总 Token 数。

        简化估算：中文约 1 字 = 1 Token，英文约 4 字符 = 1 Token。
        """
        tokens = 0
        tokens += len(context.system_prompt)
        for turn in context.conversation_history:
            if turn.token_count:
                tokens += turn.token_count
            else:
                tokens += len(turn.content)
        for result in context.tool_results:
            if result.token_count:
                tokens += result.token_count
            else:
                tokens += len(str(result.result))
        return tokens

    def _apply_window_strategy(self, context: AgentContext) -> AgentContext:
        """应用窗口策略裁剪上下文。

        若未配置窗口策略，默认使用滑动窗口（保留最近 10 轮）。
        """
        from orchestration_platform.context_engine.window_strategies import (
            SlidingWindowStrategy,
            SummaryCompressionStrategy,
            ImportanceRankingStrategy,
            StructuralLosslessStrategy,
        )
        from orchestration_platform.configuration.settings import get_settings

        if self._window_strategy_config is None:
            # 默认滑动窗口
            strategy = SlidingWindowStrategy(window_size=10)
            return strategy.apply(context)

        strategy_type = self._window_strategy_config.strategy_type

        if strategy_type == ContextWindowStrategyType.SLIDING_WINDOW:
            strategy = SlidingWindowStrategy(
                window_size=self._window_strategy_config.window_size or 10
            )
        elif strategy_type == ContextWindowStrategyType.SUMMARY_COMPRESSION:
            strategy = SummaryCompressionStrategy(
                max_tokens=self._window_strategy_config.summary_max_tokens or 500
            )
        elif strategy_type == ContextWindowStrategyType.IMPORTANCE_RANKING:
            strategy = ImportanceRankingStrategy(
                threshold=self._window_strategy_config.importance_threshold or 0.5
            )
        elif strategy_type == ContextWindowStrategyType.STRUCTURAL_LOSSLESS:
            # 第五阶段偏差修正（G2/T213）：结构化无损裁剪分支
            # 开关 structural_lossless_pruning_enabled 默认 False，
            # 关闭时走 else 兜底;滑动窗口，行为与修正前一致。
            if get_settings().structural_lossless_pruning_enabled:
                strategy = StructuralLosslessStrategy()
            else:
                strategy = SlidingWindowStrategy(window_size=10)
        else:
            strategy = SlidingWindowStrategy(window_size=10)
            return strategy.apply(context)

        strategy_type = self._window_strategy_config.strategy_type

        if strategy_type == ContextWindowStrategyType.SLIDING_WINDOW:
            strategy = SlidingWindowStrategy(
                window_size=self._window_strategy_config.window_size or 10
            )
        elif strategy_type == ContextWindowStrategyType.SUMMARY_COMPRESSION:
            strategy = SummaryCompressionStrategy(
                max_tokens=self._window_strategy_config.summary_max_tokens or 500
            )
        elif strategy_type == ContextWindowStrategyType.IMPORTANCE_RANKING:
            strategy = ImportanceRankingStrategy(
                threshold=self._window_strategy_config.importance_threshold or 0.5
            )
        else:
            strategy = SlidingWindowStrategy(window_size=10)

        return strategy.apply(context)
