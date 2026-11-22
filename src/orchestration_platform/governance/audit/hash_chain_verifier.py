# =============================================================================
# 哈希链校验器（governance/audit/hash_chain_verifier.py）
#
# 职责：计算与校验审计日志哈希链。
# 设计意图（对应 spec 5.3、design 2.6、任务 T62.2）：
#   1. 哈希链计算：current_hash = SHA256(previous_hash + log_content)；
#   2. 哈希链校验：按时间范围校验完整性；
#   3. 篡改检测：任何篡改导致链校验失败，报告受影响日志范围。
# =============================================================================

import hashlib
from typing import Any, Dict, List

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.governance.audit.content_hasher import ContentHasher
from orchestration_platform.governance.audit.models import AuditLogEntry


class HashChainVerifier:
    """哈希链校验器。

    计算与校验审计日志的哈希链，保证日志不可篡改。
    """

    def __init__(self) -> None:
        """初始化哈希链校验器。"""
        self._algorithm = get_settings().audit_hash_algorithm
        # ---- 第五阶段偏差修正（G5/T209）：字段级内容哈希器 ----
        # 与写入侧复用同一 ContentHasher 实现，保证哈希口径全链路一致。
        self._content_hasher = ContentHasher()

    def compute_hash(
        self,
        previous_hash: str,
        log_content: str,
    ) -> str:
        """计算哈希链。

        current_hash = SHA256(previous_hash + log_content)

        Args:
            previous_hash: 前一条目的哈希值。
            log_content: 当前条目的内容（序列化后的日志内容）。

        Returns:
            当前条目的哈希值。
        """
        hasher = hashlib.new(self._algorithm)
        hasher.update((previous_hash + log_content).encode("utf-8"))
        return hasher.hexdigest()

    def compute_entry_hash(
        self,
        entry: AuditLogEntry,
        previous_hash: str = "",
    ) -> str:
        """计算审计日志条目的哈希值。

        使用条目的关键字段作为 log_content。
        """
        log_content = self._serialize_entry(entry)
        return self.compute_hash(previous_hash, log_content)

    def verify_chain(
        self,
        entries: List[AuditLogEntry],
    ) -> Dict[str, Any]:
        """校验哈希链完整性。

        Args:
            entries: 按时间顺序排列的审计日志条目列表。

        Returns:
            校验结果：{verified_count, failed_count, failed_entry_ids, is_valid}。
        """
        verified_count = 0
        failed_count = 0
        failed_entry_ids: List[str] = []
        previous_hash = ""

        for entry in entries:
            expected_hash = self.compute_entry_hash(entry, previous_hash)
            if entry.entry_hash == expected_hash:
                verified_count += 1
            else:
                failed_count += 1
                failed_entry_ids.append(entry.entry_id)
            previous_hash = entry.entry_hash

        return {
            "verified_count": verified_count,
            "failed_count": failed_count,
            "failed_entry_ids": failed_entry_ids,
            "is_valid": failed_count == 0,
        }

    @staticmethod
    def _serialize_entry(entry: AuditLogEntry) -> str:
        """序列化审计日志条目为哈希链内容。

        使用关键字段的稳定序列化。
        """
        content_parts = [
            entry.entry_id,
            entry.tenant_id,
            entry.actor_id,
            entry.resource_type.value,
            entry.resource_id,
            entry.operation.value,
            entry.operation_result.value,
            entry.trace_id,
            entry.occurred_at.isoformat(),
        ]
        return "|".join(content_parts)

    # ------------------------------------------------------------------
    # 第五阶段偏差修正（G5/T209）：字段级内容完整性校验
    # ------------------------------------------------------------------
    # 注意：既有 compute_hash / compute_entry_hash / verify_chain /
    # _serialize_entry 四个方法的签名与行为零修改（接口冻结约束 X5）；
    # 本方法为独立追加，input_hash/output_hash 为 None 的旧条目跳过断言。

    def verify_content_integrity(
        self,
        entries: List[AuditLogEntry],
    ) -> Dict[str, Any]:
        """校验审计条目的字段级内容哈希（input_hash/output_hash）。

        与 verify_chain（条目级哈希链校验）相互独立、互补：
          - verify_chain：验证条目序列完整性（防删改链条）；
          - 本方法：验证单条目载荷内容完整性（定位被篡改的字段）。

        兼容语义（A5.2）：
          - input_hash/output_hash 为 None 的条目（修正前旧条目）跳过
            字段级断言并计入 skipped_count，不影响既有链条校验；
          - 字段篡改不抛异常，以结构化报告返回（错误码
            AUDIT_CONTENT_HASH_MISMATCH 记录于报告内）。

        Args:
            entries: 待校验的审计日志条目列表（顺序无关）。

        Returns:
            校验报告：{
                checked_count: 实际执行字段断言的条目数,
                skipped_count: 因哈希字段为 None 而跳过的旧条目数,
                tampered_entry_ids: 被篡改条目 ID 清单,
                tampered_fields: {entry_id: [被篡改字段名]},
                error_code: 校验不一致时的错误码（AUDIT_CONTENT_HASH_MISMATCH）,
                is_valid: 整体是否通过,
            }
        """
        checked_count = 0
        skipped_count = 0
        tampered_entry_ids: List[str] = []
        tampered_fields: Dict[str, List[str]] = {}

        for entry in entries:
            # 旧条目（None）跳过字段级断言——向后兼容核心语义
            if entry.input_hash is None and entry.output_hash is None:
                skipped_count += 1
                continue

            field_issues: List[str] = []

            # input_hash 断言（None 字段单独跳过，允许单侧哈希的条目）
            if entry.input_hash is not None:
                checked_count += 1
                expected_input = self._content_hasher.compute_input_hash(
                    entry.request_payload
                )
                if entry.input_hash != expected_input:
                    field_issues.append("input_hash")

            # output_hash 断言
            if entry.output_hash is not None:
                checked_count += 1
                expected_output = self._content_hasher.compute_output_hash(
                    entry.response_summary
                )
                if entry.output_hash != expected_output:
                    field_issues.append("output_hash")

            if field_issues:
                tampered_entry_ids.append(entry.entry_id)
                tampered_fields[entry.entry_id] = field_issues

        return {
            "checked_count": checked_count,
            "skipped_count": skipped_count,
            "tampered_entry_ids": tampered_entry_ids,
            "tampered_fields": tampered_fields,
            "error_code": (
                "AUDIT_CONTENT_HASH_MISMATCH" if tampered_entry_ids else None
            ),
            "is_valid": len(tampered_entry_ids) == 0,
        }
