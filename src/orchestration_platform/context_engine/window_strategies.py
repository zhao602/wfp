# =============================================================================
# 上下文窗口策略（context_engine/window_strategies.py）
#
# 职责：实现上下文窗口裁剪策略，防止超出模型上下文长度限制。
# 设计意图（对应 spec 5.4.24.2、design 2.7.2、任务 T36/T212）：
#   1. 滑动窗口——保留最近 N 轮对话，丢弃最早轮次；
#   2. 摘要压缩——将早期对话摘要为简短描述；
#   3. 重要性排序——按相关性评分保留高价值上下文；
#   4. 结构化无损裁剪（G2/T212 新增）——按结构单元裁剪，语义无损且记录可追溯。
# =============================================================================

import hashlib
from typing import List

from orchestration_platform.context_engine.models import (
    AgentContext,
    ConversationTurn,
)
from orchestration_platform.context_engine.pruning_records import (
    PruningReason,
    PruningRecord,
)


class SlidingWindowStrategy:
    """滑动窗口策略：保留最近 N 轮对话。

    最简单高效的策略：直接截断早期对话，保留最近 window_size 轮。
    """

    def __init__(self, window_size: int = 10) -> None:
        self._window_size = window_size

    def apply(self, context: AgentContext) -> AgentContext:
        """应用滑动窗口裁剪。"""
        if len(context.conversation_history) <= self._window_size:
            return context

        retained = context.conversation_history[-self._window_size:]
        context.conversation_history = retained
        return context


class SummaryCompressionStrategy:
    """摘要压缩策略：将早期对话摘要为简短描述。

    将超出保留窗口的早期对话替换为一条摘要消息，
    摘要内容为"[已压缩 N 轮早期对话]"。
    """

    def __init__(self, max_tokens: int = 500, retain_recent: int = 5) -> None:
        self._max_tokens = max_tokens
        self._retain_recent = retain_recent

    def apply(self, context: AgentContext) -> AgentContext:
        """应用摘要压缩。"""
        history = context.conversation_history
        if len(history) <= self._retain_recent:
            return context

        compressed_count = len(history) - self._retain_recent
        summary_turn = ConversationTurn(
            role="system",
            content=f"[已压缩 {compressed_count} 轮早期对话]",
        )
        retained = history[-self._retain_recent:]
        context.conversation_history = [summary_turn] + retained
        return context


class ImportanceRankingStrategy:
    """重要性排序策略：按相关性评分保留高价值上下文。

    简化实现：按消息长度排序（长消息通常含更多信息），
    保留评分高于阈值的轮次 + 最近 3 轮。
    """

    def __init__(self, threshold: float = 0.5, retain_recent: int = 3) -> None:
        self._threshold = threshold
        self._retain_recent = retain_recent

    def apply(self, context: AgentContext) -> AgentContext:
        """应用重要性排序裁剪。"""
        history = context.conversation_history
        if len(history) <= self._retain_recent:
            return context

        recent = history[-self._retain_recent:]
        candidates = history[:-self._retain_recent]

        if not candidates:
            return context

        # 计算重要性评分（基于内容长度归一化）
        max_length = max(len(turn.content) for turn in candidates)
        if max_length == 0:
            return context

        scored = [
            (turn, len(turn.content) / max_length)
            for turn in candidates
        ]
        retained_important = [
            turn for turn, score in scored if score >= self._threshold
        ]

        context.conversation_history = retained_important + recent
        return context


# =============================================================================
# 第五阶段偏差修正（G2/T212）：结构化无损裁剪策略
# =============================================================================


class StructuralLosslessStrategy:
    """结构化无损裁剪策略：按结构单元裁剪，语义无损且记录可追溯。

    三类结构单元确定性判定（单遍扫描 + 哈希集合查找，整体 O(n)）：
      1. 冗余字段（REDUNDANT_FIELD）：conversation_history 中 role+content
         完全相同的重复轮次（保留首次出现）；
      2. 重复段落（DUPLICATE_SEGMENT）：conversation_history 中 content
         规范化哈希已见的重复段落（保留首次出现，role 可不同）；
      3. 已消费区块（CONSUMED_BLOCK）：tool_results 中 consumed=True 的条目
         （工具结果已被后续逻辑消费，不再需要保留）。

    语义无损论证：
      - 冗余字段：同值保留首份，信息集合不变；
      - 重复段落：同内容保留首份，语义集合不变；
      - 已消费区块：consumed 标记由业务逻辑显式设置，表示该结果已不再必要。

    裁剪记录追加至 context.pruning_records（旁路追溯，不计入 Token 估算）。
    """

    def __init__(
        self,
        enable_redundant_field: bool = True,
        enable_duplicate_segment: bool = True,
        enable_consumed_block: bool = True,
        max_pruning_records: int = 100,
    ) -> None:
        """初始化无损裁剪策略。

        Args:
            enable_redundant_field: 是否启用冗余字段裁剪（默认 True）。
            enable_duplicate_segment: 是否启用重复段落裁剪（默认 True）。
            enable_consumed_block: 是否启用已消费区块裁剪（默认 True）。
            max_pruning_records: 最大裁剪记录数（防记录膨胀，默认 100）。
        """
        self._enable_redundant_field = enable_redundant_field
        self._enable_duplicate_segment = enable_duplicate_segment
        self._enable_consumed_block = enable_consumed_block
        self._max_pruning_records = max_pruning_records

    def apply(self, context: AgentContext) -> AgentContext:
        """应用结构化无损裁剪。

        单遍扫描 conversation_history 与 tool_results，移除三类结构单元
        并生成裁剪记录。无候选可裁时零移除并附提示信息。
        """
        pruning_records: List[PruningRecord] = list(
            getattr(context, "pruning_records", []) or []
        )
        pruned_count = 0

        # ---- 1. conversation_history 冗余字段 + 重复段落裁剪 ----
        seen_role_content: set = set()  # (role, content) 去重集合
        seen_content_hash: set = set()  # content 规范化哈希去重集合
        retained_history: List[ConversationTurn] = []

        for idx, turn in enumerate(context.conversation_history):
            content_hash = self._normalize_hash(turn.content)
            role_content_key = (turn.role, turn.content)

            # 冗余字段判定：role+content 完全相同
            if self._enable_redundant_field and role_content_key in seen_role_content:
                if pruned_count < self._max_pruning_records:
                    pruning_records.append(
                        PruningRecord.from_content(
                            block_id=f"turn_{idx}",
                            reason=PruningReason.REDUNDANT_FIELD,
                            content={"role": turn.role, "content": turn.content},
                            description=f"冗余字段：第 {idx} 轮 role+content 与前序重复",
                        )
                    )
                pruned_count += 1
                continue  # 移除（不追加到 retained）

            # 重复段落判定：content 规范化哈希已见
            if self._enable_duplicate_segment and content_hash in seen_content_hash:
                if pruned_count < self._max_pruning_records:
                    pruning_records.append(
                        PruningRecord.from_content(
                            block_id=f"turn_{idx}",
                            reason=PruningReason.DUPLICATE_SEGMENT,
                            content=turn.content,
                            description=f"重复段落：第 {idx} 轮 content 哈希已见",
                        )
                    )
                pruned_count += 1
                continue  # 移除

            # 保留首次出现
            seen_role_content.add(role_content_key)
            seen_content_hash.add(content_hash)
            retained_history.append(turn)

        context.conversation_history = retained_history

        # ---- 2. tool_results 已消费区块裁剪 ----
        if self._enable_consumed_block:
            retained_tools = []
            for idx, result in enumerate(context.tool_results):
                is_consumed = getattr(result, "consumed", False)
                if is_consumed:
                    if pruned_count < self._max_pruning_records:
                        pruning_records.append(
                            PruningRecord.from_content(
                                block_id=f"tool_{idx}",
                                reason=PruningReason.CONSUMED_BLOCK,
                                content={
                                    "tool_identifier": result.tool_identifier,
                                    "success": result.success,
                                },
                                description=f"已消费区块：工具 {result.tool_identifier} 结果已标记 consumed",
                            )
                        )
                    pruned_count += 1
                    continue  # 移除
                retained_tools.append(result)
            context.tool_results = retained_tools

        # ---- 3. 更新裁剪记录到上下文 ----
        context.pruning_records = pruning_records

        return context

    @staticmethod
    def _normalize_hash(content: str) -> str:
        """计算 content 的规范化哈希（用于重复段落判定）。

        规则：strip 后 SHA-256，保证前后空白差异不影响重复判定。
        """
        return hashlib.sha256(content.strip().encode("utf-8")).hexdigest()
