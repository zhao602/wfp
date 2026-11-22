# =============================================================================
# 网络过滤器（integrations/sandbox/network_filter.py）
#
# 职责：校验工具调用的网络访问目标是否在策略白名单内。
# 设计意图（对应偏差修正 spec 5.1.1.4 / design 2.2.2.3）：
#   - 默认拒绝模式（DEFAULT_DENY）：不提供任何出网通道
#   - 白名单模式（WHITELIST）：目标匹配才放行
#   - 白名单条目校验：域名或 IP 段可解析
#   - O(whitelist) 级检查
# =============================================================================

import fnmatch
import ipaddress
import logging
from typing import List, Optional

from orchestration_platform.domain.models.enums import (
    NetworkPolicyMode,
    ViolationType,
)
from orchestration_platform.integrations.sandbox.models import (
    SandboxPolicy,
    SandboxViolationRecord,
)

_logger = logging.getLogger(__name__)


class NetworkFilter:
    """网络过滤器：校验网络访问目标。

    根据策略网络模式（默认拒绝/白名单）校验目标地址。
    """

    def check(
        self,
        target: str,
        policy: SandboxPolicy,
    ) -> Optional[SandboxViolationRecord]:
        """校验网络访问目标。

        参数：
            target：目标地址（域名或 IP:端口）
            policy：沙箱策略

        返回：
            None 表示放行；SandboxViolationRecord 表示拒绝
        """
        if policy.network_policy == NetworkPolicyMode.DEFAULT_DENY:
            return SandboxViolationRecord(
                violation_type=ViolationType.NETWORK_DENIED,
                detail=f"网络访问拒绝（默认拒绝模式）: target={target}",
                target=target,
            )

        if policy.network_policy == NetworkPolicyMode.WHITELIST:
            if not policy.network_whitelist:
                return SandboxViolationRecord(
                    violation_type=ViolationType.NETWORK_DENIED,
                    detail=f"网络访问拒绝（白名单为空）: target={target}",
                    target=target,
                )

            if self._match_whitelist(target, policy.network_whitelist):
                return None

            return SandboxViolationRecord(
                violation_type=ViolationType.NETWORK_DENIED,
                detail=f"网络访问拒绝（不在白名单）: target={target}",
                target=target,
            )

        return None

    def _match_whitelist(self, target: str, whitelist: List[str]) -> bool:
        """检查目标是否匹配白名单。

        支持域名通配符（*.example.com）、IP 段（192.168.1.0/24）、精确匹配。
        """
        target_host = self._extract_host(target)

        for entry in whitelist:
            if self._match_entry(target_host, entry):
                return True

        return False

    @staticmethod
    def _extract_host(target: str) -> str:
        """从目标地址提取主机部分（去除端口/协议）。"""
        host = target
        if "://" in host:
            host = host.split("://", 1)[1]
        if ":" in host and not host.startswith("["):
            host = host.rsplit(":", 1)[0]
        if host.startswith("[") and host.endswith("]"):
            host = host[1:-1]
        return host

    @staticmethod
    def _match_entry(host: str, entry: str) -> bool:
        """检查单个主机是否匹配单个白名单条目。"""
        # CIDR 网段匹配
        if "/" in entry:
            try:
                ip = ipaddress.ip_address(host)
                network = ipaddress.ip_network(entry, strict=False)
                return ip in network
            except (ValueError, TypeError):
                return False

        # 通配符域名匹配
        if "*" in entry or "?" in entry:
            return fnmatch.fnmatch(host, entry)

        # 精确匹配
        return host == entry
