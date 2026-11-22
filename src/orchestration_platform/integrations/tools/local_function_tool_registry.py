# =============================================================================
# 本地函数工具注册表（integrations/tools/local_function_tool_registry.py）
#
# 职责：为本地函数工具（LocalFunctionToolInvoker）提供集中注册点，
# 供工具发现三源聚合与生产装配统一读取。
# 设计意图（merge_tool_into_agent_node DC-A / FR-1 / CHG-4）：
#   1. 同标识重复注册后者覆盖（升级语义）并记 info 日志；
#   2. 空注册表合法——生产首期注册 0 个工具，空态是常态而非异常；
#   3. 模块级惰性单例（进程级，随进程销毁）；
#   4. list_invokers 返回副本，防止外部直接修改内部映射。
# =============================================================================

import logging
from typing import Dict, Optional

from orchestration_platform.integrations.abstractions.external_clients import (
    IToolInvoker,
)

_logger = logging.getLogger(__name__)


class LocalFunctionToolRegistry:
    """本地函数工具注册表：标识 → IToolInvoker 的集中映射。"""

    def __init__(self) -> None:
        self._invokers: Dict[str, IToolInvoker] = {}

    def register(self, invoker: IToolInvoker) -> None:
        """注册本地工具；同标识重复注册后者覆盖并记 info 日志。"""
        identifier = invoker.tool_identifier
        if identifier in self._invokers:
            _logger.info("本地工具 %s 重复注册，采用新实例覆盖", identifier)
        self._invokers[identifier] = invoker

    def list_invokers(self) -> Dict[str, IToolInvoker]:
        """返回注册表副本（外部修改不影响内部状态）。"""
        return dict(self._invokers)


_registry: Optional[LocalFunctionToolRegistry] = None


def get_local_function_tool_registry() -> LocalFunctionToolRegistry:
    """返回模块级惰性单例（进程级，随进程销毁）。"""
    global _registry
    if _registry is None:
        _registry = LocalFunctionToolRegistry()
    return _registry
