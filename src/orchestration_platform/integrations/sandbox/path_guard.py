# =============================================================================
# 路径守卫（integrations/sandbox/path_guard.py）
#
# 职责：校验工具调用参数中的文件路径不逃逸沙箱工作目录。
# 设计意图（对应偏差修正 spec 4.3.1 / design 2.2.2.3）：
#   - 对参数中所有文件路径先 realpath 解析（消解符号链接与 .. 穿越）
#   - 校验解析结果严格位于策略工作目录前缀内
#   - 目录外访问返回 FILE_ESCAPE 违规记录
#   - O(1) 级检查（满足 B103 的 20% 开销约束）
# =============================================================================

import logging
import os
from typing import Any, Dict, List

from orchestration_platform.domain.models.enums import ViolationType
from orchestration_platform.integrations.sandbox.models import (
    SandboxPolicy,
    SandboxViolationRecord,
)

_logger = logging.getLogger(__name__)


class PathGuard:
    """路径守卫：防止文件路径穿越逃逸。

    检查参数中所有路径类值是否严格位于策略工作目录前缀内。
    """

    def check(
        self,
        arguments: Dict[str, Any],
        policy: SandboxPolicy,
    ) -> List[SandboxViolationRecord]:
        """校验参数中的文件路径。

        参数：
            arguments：工具调用入参
            policy：沙箱策略（含工作目录与额外允许前缀）

        返回：
            违规记录列表（空列表表示全部通过）
        """
        violations: List[SandboxViolationRecord] = []
        allowed_prefixes = self._build_allowed_prefixes(policy)

        for key, value in arguments.items():
            if not isinstance(value, str):
                continue
            if not self._looks_like_path(value):
                continue

            resolved = os.path.realpath(value)
            if not self._is_within_allowed(resolved, allowed_prefixes):
                violations.append(SandboxViolationRecord(
                    violation_type=ViolationType.FILE_ESCAPE,
                    detail=f"路径穿越违规: 参数 '{key}' 值 '{value}' 解析为 '{resolved}' 不在允许范围内",
                    target=resolved,
                ))
                _logger.warning(
                    "路径守卫拦截: key=%s, value=%s, resolved=%s",
                    key, value, resolved,
                )

        return violations

    def _build_allowed_prefixes(self, policy: SandboxPolicy) -> List[str]:
        """构建允许的路径前缀列表。"""
        prefixes = [os.path.realpath(policy.workdir)]
        for prefix in policy.allowed_path_prefixes:
            prefixes.append(os.path.realpath(prefix))
        return prefixes

    @staticmethod
    def _looks_like_path(value: str) -> bool:
        """判断字符串是否像文件路径。"""
        if not value:
            return False
        # 绝对路径或含路径分隔符或含文件扩展名
        return (
            os.path.isabs(value)
            or "/" in value
            or "\\" in value
            or ("." in value and value.rsplit(".", 1)[-1].isalpha())
        )

    @staticmethod
    def _is_within_allowed(resolved_path: str, allowed_prefixes: List[str]) -> bool:
        """检查解析后的路径是否在允许前缀内。"""
        for prefix in allowed_prefixes:
            if resolved_path == prefix or resolved_path.startswith(prefix + os.sep):
                return True
        return False
