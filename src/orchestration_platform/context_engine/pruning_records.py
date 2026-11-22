# =============================================================================
# 无损裁剪记录模型（context_engine/pruning_records.py）
#
# 职责：定义结构化无损裁剪产生的裁剪记录与原因枚举（G2/T211）。
# 设计意图（对应 spec 5.2、design D3）：
#   1. 每次移除一个结构单元（冗余字段/重复段落/已消费区块）生成一条记录；
#   2. 记录携带区块标识、原因、内容摘要哈希与时间戳，支持裁剪可追溯；
#   3. 记录生成后不可变（仅追加语义），满足数据约束 6.2；
#   4. 记录不计入 Token 估算主路径（旁路追溯数据，避免记录膨胀上下文）。
# =============================================================================

import hashlib
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


def _utc_now() -> datetime:
    """返回当前 UTC 时间。"""
    return datetime.now(timezone.utc)


class PruningReason(str, Enum):
    """结构化无损裁剪原因枚举（G2/T211）。

    三类结构单元判定依据（design 2.1.3.2 判定表）：
      REDUNDANT_FIELD    —— 冗余字段：同键重复出现且值一致（保留首次出现）
      DUPLICATE_SEGMENT  —— 重复段落：段落内容规范化哈希已见（保留首次出现）
      CONSUMED_BLOCK     —— 已消费区块：工具结果/状态引用带 consumed 标记
    """

    REDUNDANT_FIELD = "redundant_field"
    DUPLICATE_SEGMENT = "duplicate_segment"
    CONSUMED_BLOCK = "consumed_block"


def _content_digest(content: Any) -> str:
    """计算被裁内容的规范化摘要哈希（SHA-256，用于追溯比对）。

    规则：JSON canonical 化（键排序）后哈希；非可序列化对象退化为 str() 哈希。
    """
    import json

    try:
        text = json.dumps(content, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(content)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class PruningRecord(BaseModel):
    """无损裁剪记录：单次结构单元移除的留痕。

    生成后不可变（仅追加语义，禁止回改），满足数据约束 6.2。

    属性：
        block_id        —— 被裁区块的唯一标识（非空，可定位）
        reason          —— 裁剪原因（PruningReason 枚举）
        content_digest  —— 被裁内容的规范化哈希（SHA-256，用于追溯比对）
        pruned_at       —— 裁剪时间戳（UTC）
        description     —— 人类可读说明（可选，辅助排查）
    """

    block_id: str = Field(description="被裁区块唯一标识（非空可定位）")
    reason: PruningReason = Field(description="裁剪原因")
    content_digest: str = Field(description="被裁内容规范化哈希（SHA-256）")
    pruned_at: datetime = Field(default_factory=_utc_now, description="裁剪时间戳")
    description: Optional[str] = Field(
        default=None, description="人类可读说明（辅助排查）"
    )

    @classmethod
    def from_content(
        cls,
        block_id: str,
        reason: PruningReason,
        content: Any,
        description: Optional[str] = None,
    ) -> "PruningRecord":
        """从被裁内容构造记录（自动计算 content_digest）。

        Args:
            block_id: 被裁区块标识。
            reason: 裁剪原因。
            content: 被裁内容（任意可序列化对象）。
            description: 可选说明。

        Returns:
            构造完成的 PruningRecord 实例。
        """
        return cls(
            block_id=block_id,
            reason=reason,
            content_digest=_content_digest(content),
            description=description,
        )
