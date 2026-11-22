# =============================================================================
# 只读语句守卫（integrations/data_source/read_only_statement_guard.py）
#
# 职责：识别并拦截数据写入、删除、结构变更类语句。
# 设计意图（对应 spec 5.3.1.9）：
#   配置包含写操作语句 → 在校验阶段即拒绝。
# =============================================================================

import re

_FORBIDDEN_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|MERGE|GRANT|REVOKE|RENAME)\b",
    re.IGNORECASE,
)


class ReadOnlyStatementGuard:
    """只读语句守卫：检测写/删/结构变更操作。"""

    @staticmethod
    def check(query: str) -> str:
        """检查查询语句，含禁止操作时返回违规描述，否则返回空字符串。"""
        if not query:
            return ""
        match = _FORBIDDEN_KEYWORDS.search(query)
        if match:
            return f"检测到禁止的写/结构变更关键词：{match.group()}"
        return ""
