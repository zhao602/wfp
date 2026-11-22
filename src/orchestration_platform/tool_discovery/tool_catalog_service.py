# =============================================================================
# 工具目录聚合服务（tool_discovery/tool_catalog_service.py）
#
# 职责：三源（平台声明 / MCP 工具市场仓储 / 本地注册表）聚合为统一工具清单。
# 设计意图（merge_tool_into_agent_node DC-A / FR-1 / CHG-3）：
#   1. platform 源同步读静态声明（9 个）；
#   2. mcp 源对三种 ToolPermission 各调一次 list_by_permission 取并集
#      （仓储无 list_all，零接口改动满足 INV-1），同标识多版本取 version 字符序最大；
#   3. local 源读注册表元数据；
#   4. 跨源冲突按 platform > mcp > local 优先保留，被丢弃项记 warning；
#   5. 单源异常 → logging.warning 跳过该源、不向调用方抛出（恒可用；
#      .data/mcp_tool_market/ 为空是生产常态，空分组正常返回）；
#   6. 严禁调用仓储 save / market register（只读，INV-10）。
# =============================================================================

import logging
from typing import Callable, List, Optional

from orchestration_platform.domain.models.enums import ToolPermission
from orchestration_platform.integrations.tools.local_function_tool_registry import (
    LocalFunctionToolRegistry,
)
from orchestration_platform.mcp_tool_market.models import McpToolRegistration
from orchestration_platform.mcp_tool_market.persistence.abstractions.mcp_tool_market_repository import (
    IMcpToolMarketRepository,
)
from orchestration_platform.integrations.mcp_server.tool_declarations import (
    get_platform_tool_declarations,
)
from orchestration_platform.tool_discovery.models import (
    ToolCatalogItem,
    ToolSource,
)

_logger = logging.getLogger(__name__)


class ToolCatalogService:
    """工具目录聚合服务：三源合并为只读清单。"""

    def __init__(
        self,
        platform_declarations_provider: Callable[
            ..., List
        ] = get_platform_tool_declarations,
        market_repository: Optional[IMcpToolMarketRepository] = None,
        local_registry: Optional[LocalFunctionToolRegistry] = None,
    ) -> None:
        # 仓储注入式：便于测试替身与生产装配解耦
        self._platform_declarations_provider = platform_declarations_provider
        self._market_repository = market_repository
        self._local_registry = local_registry

    async def list_tools(self) -> List[ToolCatalogItem]:
        """返回三源聚合后的工具清单（单源异常跳过，恒可用）。"""
        platform_items = self._load_platform_items()
        mcp_items = await self._load_mcp_items()
        local_items = self._load_local_items()

        # 跨源冲突按 platform > mcp > local 优先保留
        merged: dict = {}
        for source_label, items in (
            ("mcp", mcp_items),
            ("local", local_items),
        ):
            for item in items:
                existing = merged.get(item.tool_identifier)
                if existing is not None:
                    _logger.warning(
                        "工具标识 %s 跨源冲突：保留 %s 源（%s），丢弃 %s 源",
                        item.tool_identifier,
                        existing.source.value,
                        existing.name,
                        source_label,
                    )
                    continue
                merged[item.tool_identifier] = item
        for item in platform_items:
            existing = merged.get(item.tool_identifier)
            if existing is not None:
                _logger.warning(
                    "工具标识 %s 跨源冲突：保留 platform 源（%s），丢弃 %s 源",
                    item.tool_identifier,
                    item.name,
                    existing.source.value,
                )
            merged[item.tool_identifier] = item

        return list(merged.values())

    # ------------------------------------------------------------------
    # 私有方法：各源加载（单源异常跳过 + warning，不向调用方抛出）
    # ------------------------------------------------------------------

    def _load_platform_items(self) -> List[ToolCatalogItem]:
        """加载平台声明源（同步静态读取）。"""
        try:
            declarations = self._platform_declarations_provider()
        except Exception as e:  # 单源降级：跳过该源
            _logger.warning("平台工具声明源加载失败，已跳过：%s", e)
            return []
        return [
            ToolCatalogItem(
                tool_identifier=d.tool_identifier,
                name=d.name,
                description=d.description,
                source=ToolSource.PLATFORM,
                permission=d.permission,
                version=d.version,
            )
            for d in declarations
        ]

    async def _load_mcp_items(self) -> List[ToolCatalogItem]:
        """加载 MCP 工具市场源（三权限并集 + 同标识取最大版本）。"""
        if self._market_repository is None:
            return []
        try:
            registrations: List[McpToolRegistration] = []
            seen_registrations: dict = {}
            for permission in (
                ToolPermission.READONLY,
                ToolPermission.READWRITE,
                ToolPermission.ADMIN,
            ):
                for reg in await self._market_repository.list_by_permission(
                    permission
                ):
                    # 三权限并集可能重复返回同一条注册，按标识+版本去重
                    key = (reg.descriptor.tool_identifier, reg.descriptor.version)
                    if key in seen_registrations:
                        continue
                    seen_registrations[key] = reg
                    registrations.append(reg)

            # 同标识多版本取 version 字符序最大
            latest: dict = {}
            for reg in registrations:
                identifier = reg.descriptor.tool_identifier
                current = latest.get(identifier)
                if current is None or reg.descriptor.version > current.descriptor.version:
                    latest[identifier] = reg

            return [
                ToolCatalogItem(
                    tool_identifier=reg.descriptor.tool_identifier,
                    name=reg.descriptor.name,
                    description=reg.descriptor.description,
                    source=ToolSource.MCP,
                    permission=reg.descriptor.permission,
                    version=reg.descriptor.version,
                )
                for reg in latest.values()
            ]
        except Exception as e:  # 单源降级：跳过该源
            _logger.warning("MCP 工具市场源加载失败，已跳过：%s", e)
            return []

    def _load_local_items(self) -> List[ToolCatalogItem]:
        """加载本地注册表源。"""
        if self._local_registry is None:
            return []
        try:
            invokers = self._local_registry.list_invokers()
        except Exception as e:  # 单源降级：跳过该源
            _logger.warning("本地工具注册表源加载失败，已跳过：%s", e)
            return []
        return [
            ToolCatalogItem(
                tool_identifier=invoker.tool_identifier,
                name=invoker.tool_identifier,
                description="本地注册工具",
                source=ToolSource.LOCAL,
                permission=ToolPermission.READWRITE,
                version="1.0.0",
            )
            for invoker in invokers.values()
        ]
