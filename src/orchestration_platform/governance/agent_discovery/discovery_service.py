# =============================================================================
# 发现服务（governance/agent_discovery/discovery_service.py）
#
# 职责：编排语义索引仓储、关键词索引、向量索引与混合检索，
#       提供 Agent 语义档案管理与发现查询的领域服务。
# 设计意图（对应偏差修正 T198-T199 / design 2.2.2.5）：
#   - 档案管理：upsert / get / remove / list，描述来源三态独立维护
#   - 索引重建：从仓储全量加载到关键词与向量索引
#   - 发现查询：最短长度校验 → 混合检索 → RBAC 过滤 → 相关度阈值过滤 → 截断
#   - RBAC 过滤：仅返回调用方具备所需权限的 Agent（空 required_permissions 恒放行）
#   - 相关度阈值：低于 agent_discovery_relevance_threshold 的结果过滤
# =============================================================================

import logging
from datetime import datetime, timezone
from typing import List, Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.errors import INTENT_QUERY_INVALID, DomainError
from orchestration_platform.governance.agent_discovery.abstractions.semantic_index_repository import (
    ISemanticIndexRepository,
)
from orchestration_platform.governance.agent_discovery.hybrid_retriever import (
    HybridRetriever,
)
from orchestration_platform.governance.agent_discovery.keyword_index import (
    KeywordInvertedIndex,
)
from orchestration_platform.governance.agent_discovery.models import (
    AgentSemanticProfile,
    DiscoveryQuery,
    DiscoveryResult,
    DiscoveryResultItem,
)
from orchestration_platform.governance.agent_discovery.semantic_index_repository_local import (
    LocalSemanticIndexRepository,
)
from orchestration_platform.governance.agent_discovery.vector_index import (
    VectorIndex,
)

_logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    """获取当前 UTC 时间。"""
    return datetime.now(timezone.utc)


class DiscoveryService:
    """Agent 发现服务。

    协调仓储、关键词索引、向量索引与混合检索。
    单实例线程安全由调用方保证。
    """

    def __init__(
        self,
        repository: Optional[ISemanticIndexRepository] = None,
        keyword_index: Optional[KeywordInvertedIndex] = None,
        vector_index: Optional[VectorIndex] = None,
    ) -> None:
        """构造发现服务。

        repository 为 None 时使用本地内存仓储。
        vector_index 为 None 时按配置构造（空后端即哈希兜底）。
        """
        self._repository = repository or LocalSemanticIndexRepository()
        self._keyword_index = keyword_index or KeywordInvertedIndex()

        if vector_index is not None:
            self._vector_index = vector_index
        else:
            settings = get_settings()
            self._vector_index = VectorIndex(
                backend=settings.agent_discovery_vector_backend,
                endpoint=settings.agent_discovery_vector_endpoint,
                timeout_ms=settings.agent_discovery_vector_timeout_ms,
            )

        self._retriever = HybridRetriever(
            keyword_index=self._keyword_index,
            vector_index=self._vector_index,
        )
        self._index_built = False

    # ---------------- 档案管理 ----------------

    def upsert_profile(self, profile: AgentSemanticProfile) -> AgentSemanticProfile:
        """新增或更新 Agent 语义档案并同步索引。

        描述来源三态由调用方在 profile.description_source 中指定，
        本服务不自动改写来源。
        """
        # 描述长度截断
        settings = get_settings()
        desc_max = settings.agent_discovery_desc_max_length
        if len(profile.description) > desc_max:
            profile = profile.model_copy(
                update={"description": profile.description[:desc_max]}
            )

        self._repository.upsert_profile(profile)
        self._sync_index_single(profile)
        _logger.info(
            "语义档案 upsert: agent_id=%s source=%s",
            profile.agent_id,
            profile.description_source.value,
        )
        return profile

    def get_profile(self, agent_id: str) -> Optional[AgentSemanticProfile]:
        """查询 Agent 语义档案。"""
        return self._repository.get_profile(agent_id)

    def remove_profile(self, agent_id: str) -> bool:
        """移除 Agent 语义档案并同步索引。"""
        removed = self._repository.remove_profile(agent_id)
        if removed:
            self._keyword_index.remove_document(agent_id)
            self._vector_index.remove(agent_id)
            _logger.info("语义档案移除: agent_id=%s", agent_id)
        return removed

    def list_profiles(self) -> List[AgentSemanticProfile]:
        """列出全部 Agent 语义档案。"""
        return self._repository.list_profiles()

    # ---------------- 索引重建 ----------------

    def rebuild_index(self) -> int:
        """从仓储全量重建关键词与向量索引，返回索引文档数。"""
        self._keyword_index.clear()
        self._vector_index.clear()

        profiles = self._repository.list_profiles()
        for profile in profiles:
            self._sync_index_single(profile)

        self._index_built = True
        _logger.info(
            "索引重建完成: profiles=%d keyword=%d vector=%d",
            len(profiles),
            self._keyword_index.document_count(),
            self._vector_index.document_count(),
        )
        return len(profiles)

    def _sync_index_single(self, profile: AgentSemanticProfile) -> None:
        """同步单个档案到索引。"""
        # 索引文本：名称 + 描述 + 能力清单 + 标签
        index_text = " ".join(
            [
                profile.name,
                profile.description,
                " ".join(profile.capabilities),
                " ".join(profile.tags),
            ]
        )
        self._keyword_index.add_document(
            agent_id=profile.agent_id,
            text=index_text,
            tags=profile.tags,
        )
        self._vector_index.upsert(
            agent_id=profile.agent_id,
            text=index_text,
        )

    # ---------------- 发现查询 ----------------

    def discover(self, query: DiscoveryQuery) -> DiscoveryResult:
        """执行 Agent 发现查询。

        流程：
          1. 最短查询长度校验
          2. 首次查询时按需重建索引
          3. 混合检索（RRF 融合）
          4. RBAC 过滤
          5. 相关度阈值过滤
          6. 结果数量截断
        """
        settings = get_settings()

        # 1. 最短查询长度校验
        min_len = settings.agent_discovery_min_query_length
        if len(query.intent_text.strip()) < min_len:
            raise DomainError(
                code=INTENT_QUERY_INVALID,
                message=f"意图查询长度不足，最短要求 {min_len} 字符",
                details={
                    "actual_length": len(query.intent_text.strip()),
                    "min_length": min_len,
                },
            )

        # 2. 首次查询时按需重建索引
        if not self._index_built:
            self.rebuild_index()

        # 3. 确定结果上限
        limit = query.limit or settings.agent_discovery_result_limit

        # 4. 混合检索
        hits, backend = self._retriever.retrieve(
            query=query.intent_text,
            tags_filter=query.tags_filter,
            limit=limit,
        )

        # 5. RBAC 过滤 + 相关度阈值过滤
        threshold = settings.agent_discovery_relevance_threshold
        caller_permissions = set(query.caller.permissions)
        caller_tenant = query.caller.tenant_id

        filtered_items: List[DiscoveryResultItem] = []
        tags_filter_set = {t.lower() for t in query.tags_filter}
        for hit in hits:
            profile = self._repository.get_profile(hit.agent_id)
            if profile is None:
                continue

            # 标签过滤（AND 语义）：向量索引不应用标签过滤，此处补充
            if tags_filter_set:
                profile_tags = {t.lower() for t in profile.tags}
                if not tags_filter_set.issubset(profile_tags):
                    continue

            # RBAC 过滤：所需权限调用方必须全部具备
            if profile.required_permissions:
                if not set(profile.required_permissions).issubset(
                    caller_permissions
                ):
                    continue

            # 租户隔离：非空租户必须匹配
            if caller_tenant and profile.tenant_id and profile.tenant_id != caller_tenant:
                continue

            # 相关度阈值过滤
            if hit.score < threshold:
                continue

            filtered_items.append(
                DiscoveryResultItem(
                    agent_id=profile.agent_id,
                    name=profile.name,
                    description=profile.description,
                    description_source=profile.description_source,
                    tags=profile.tags,
                    capabilities=profile.capabilities,
                    relevance_score=round(hit.score, 6),
                    matched_evidence=[hit.evidence] if hit.evidence else [],
                )
            )

            if len(filtered_items) >= limit:
                break

        return DiscoveryResult(
            items=filtered_items,
            total=len(filtered_items),
            query=query.intent_text,
            backend=backend,
        )
