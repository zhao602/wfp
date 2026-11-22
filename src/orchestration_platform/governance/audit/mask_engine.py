# =============================================================================
# 脱敏引擎（governance/audit/mask_engine.py）
#
# 职责：按脱敏规则对敏感字段进行遮盖处理。
# 设计意图（对应 spec 5.3、design 2.6、任务 T64.1）：
#   1. 三种掩码方式：full_mask、partial_mask、hash_mask；
#   2. 扩展点：通过 IMaskEngine 抽象接口提供扩展；
#   3. 默认策略：字段无匹配规则但被标记为敏感时，采用默认完全掩码。
# =============================================================================

import hashlib
from typing import Any, Dict, List, Optional

from orchestration_platform.domain.models.enums import MaskMethod
from orchestration_platform.governance.audit.models import MaskRule


class MaskEngine:
    """脱敏引擎。

    按脱敏规则对单字段或整条记录进行掩码处理。
    """

    def __init__(self) -> None:
        """初始化脱敏引擎。"""
        self._rules: List[MaskRule] = []

    def set_rules(self, rules: List[MaskRule]) -> None:
        """设置脱敏规则列表。"""
        self._rules = [r for r in rules if r.is_active]

    def add_rule(self, rule: MaskRule) -> None:
        """添加单条脱敏规则。"""
        if rule.is_active:
            self._rules.append(rule)

    def mask(self, value: Any, rule: MaskRule) -> Any:
        """按脱敏规则对单字段掩码。

        Args:
            value: 原始值。
            rule: 脱敏规则。

        Returns:
            掩码后的值。
        """
        if value is None:
            return None

        str_value = str(value)

        if rule.mask_method == MaskMethod.FULL_MASK:
            return "****"
        elif rule.mask_method == MaskMethod.PARTIAL_MASK:
            return self._partial_mask(
                str_value, rule.keep_prefix, rule.keep_suffix
            )
        elif rule.mask_method == MaskMethod.HASH:
            return hashlib.sha256(str_value.encode("utf-8")).hexdigest()[:8]
        elif rule.mask_method == MaskMethod.REPLACE:
            return rule.replacement or "****"
        else:
            return "****"

    def mask_record(
        self,
        record: Dict[str, Any],
        tenant_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """按规则列表对记录所有字段掩码。

        Args:
            record: 原始记录字典。
            tenant_id: 租户标识（用于过滤租户级规则）。

        Returns:
            掩码后的记录字典。
        """
        if not record:
            return record

        result = dict(record)
        for rule in self._rules:
            if tenant_id and rule.tenant_id and rule.tenant_id != tenant_id:
                continue
            value = self._get_nested_value(result, rule.field_path)
            if value is not None:
                masked = self.mask(value, rule)
                self._set_nested_value(result, rule.field_path, masked)
        return result

    def mask_batch(
        self,
        records: List[Dict[str, Any]],
        tenant_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """批量脱敏记录。"""
        return [self.mask_record(r, tenant_id) for r in records]

    @staticmethod
    def _partial_mask(
        value: str,
        keep_prefix: int,
        keep_suffix: int,
    ) -> str:
        """部分遮盖：保留前 N 后 M 位，中间掩码。"""
        if len(value) <= keep_prefix + keep_suffix:
            return "****"
        prefix = value[:keep_prefix]
        suffix = value[-keep_suffix:] if keep_suffix > 0 else ""
        middle_len = len(value) - keep_prefix - keep_suffix
        return f"{prefix}{'*' * min(middle_len, 4)}{suffix}"

    @staticmethod
    def _get_nested_value(
        data: Dict[str, Any],
        path: str,
    ) -> Any:
        """按点号分隔路径获取嵌套值。"""
        parts = path.split(".")
        current: Any = data
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        return current

    @staticmethod
    def _set_nested_value(
        data: Dict[str, Any],
        path: str,
        value: Any,
    ) -> None:
        """按点号分隔路径设置嵌套值。"""
        parts = path.split(".")
        current: Any = data
        for part in parts[:-1]:
            if part not in current or not isinstance(current[part], dict):
                current[part] = {}
            current = current[part]
        current[parts[-1]] = value
