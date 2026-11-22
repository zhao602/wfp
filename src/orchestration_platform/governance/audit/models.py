# =============================================================================
# 审计领域模型（governance/audit/models.py）
#
# 职责：定义审计日志条目与脱敏规则的领域模型。
# 设计意图（对应 spec 5.3、design 2.6）：
#   1. 全操作留痕，审计日志追加写入，不可篡改；
#   2. 哈希链保证日志完整性；
#   3. 脱敏规则对敏感字段进行遮盖处理。
# =============================================================================

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from orchestration_platform.domain.models.enums import (
    AuditOperationResult,
    MaskMethod,
    OperationType,
    ResourceType,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AuditLogEntry(BaseModel):
    """审计日志条目：单次操作的留痕记录。

    追加写入，不可 UPDATE/DELETE。哈希链保证完整性。
    """

    entry_id: str = Field(description="条目唯一标识")
    tenant_id: str = Field(description="所属租户标识")
    actor_id: str = Field(description="操作者标识（用户ID）")
    actor_type: str = Field(default="user", description="操作者类型：user/system/api")
    resource_type: ResourceType = Field(description="操作的资源类型")
    resource_id: str = Field(description="资源标识")
    operation: OperationType = Field(description="操作类型")
    operation_result: AuditOperationResult = Field(
        default=AuditOperationResult.SUCCESS, description="操作结果"
    )
    request_payload: Optional[Dict[str, Any]] = Field(
        default=None, description="请求载荷（已脱敏）"
    )
    response_summary: Optional[str] = Field(
        default=None, description="响应摘要（已脱敏）"
    )
    trace_id: str = Field(description="追踪标识")
    ip_address: Optional[str] = Field(default=None, description="来源 IP")
    occurred_at: datetime = Field(default_factory=_utc_now, description="操作时间")
    # 哈希链字段
    previous_hash: str = Field(default="", description="前一条目的哈希值")
    entry_hash: str = Field(default="", description="本条目的哈希值")
    # ---- 偏差修正：墓碑字段（向后兼容，默认值不影响既有数据）----
    # 墓碑字段不参与哈希链序列化，保证墓碑标记不破坏哈希链连续性
    tombstone: bool = Field(default=False, description="是否标记为墓碑（软删除）")
    deleted_at: Optional[datetime] = Field(default=None, description="墓碑标记时间（仅 tombstone=True 时有值）")
    deleted_by: Optional[str] = Field(default=None, description="执行墓碑标记的操作人标识")
    forget_operation_id: Optional[str] = Field(
        default=None, description="被遗忘权操作批次标识（批量墓碑标记时关联）"
    )
    # ---- 第五阶段偏差修正（G5/T207）：字段级内容哈希（向后兼容，默认 None）----
    # None 表示既有旧条目（修正前写入），校验器对其跳过字段级断言；
    # 新条目由 AuditLogger 在落盘前计算填充（audit_content_hash_enabled=True 时）。
    # 注意：两字段不参与 _serialize_entry 哈希链序列化，保证 entry_hash 链值
    # 与修正前逐字节一致（design D7 红线）。
    input_hash: Optional[str] = Field(
        default=None, description="请求载荷内容哈希（None 表示修正前旧条目）"
    )
    output_hash: Optional[str] = Field(
        default=None, description="响应摘要内容哈希（None 表示修正前旧条目）"
    )


class MaskRule(BaseModel):
    """脱敏规则：对敏感字段进行遮盖处理。

    规则按字段路径匹配，应用指定的脱敏方法。
    """

    rule_id: str = Field(description="规则唯一标识")
    tenant_id: Optional[str] = Field(
        default=None, description="所属租户（None 表示全局规则）"
    )
    field_path: str = Field(description="字段路径（点号分隔，如 user.email）")
    mask_method: MaskMethod = Field(
        default=MaskMethod.FULL_MASK, description="脱敏方法"
    )
    replacement: Optional[str] = Field(
        default=None, description="替换值（mask_method=replace 时使用）"
    )
    keep_prefix: int = Field(default=0, ge=0, description="保留前缀字符数（部分遮盖）")
    keep_suffix: int = Field(default=0, ge=0, description="保留后缀字符数（部分遮盖）")
    is_active: bool = Field(default=True, description="是否启用")
