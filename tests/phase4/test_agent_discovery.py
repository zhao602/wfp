# =============================================================================
# Agent 发现模块测试（tests/phase4/test_agent_discovery.py）
#
# 职责：验证关键词倒排索引、向量索引、RRF 混合检索、RBAC 过滤、
#       相关度阈值过滤、最短查询长度校验、描述来源三态管理。
# 设计意图（对应偏差修正 T202 / 验收 A127-A133）：
#   - 纯标准库关键词索引：分词、倒排、TF-IDF 评分
#   - 向量索引：哈希兜底、余弦相似度
#   - RRF 融合：两路排序融合
#   - 发现服务：全流程编排
#   - 配置默认值：agent_discovery_enabled=False
# =============================================================================

import asyncio
import unittest

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.errors import INTENT_QUERY_INVALID, DomainError
from orchestration_platform.domain.models.enums import DescriptionSource
from orchestration_platform.governance.agent_discovery.discovery_service import (
    DiscoveryService,
)
from orchestration_platform.governance.agent_discovery.hybrid_retriever import (
    HybridRetriever,
)
from orchestration_platform.governance.agent_discovery.keyword_index import (
    KeywordInvertedIndex,
)
from orchestration_platform.governance.agent_discovery.models import (
    AgentSemanticProfile,
    CallerContext,
    DiscoveryQuery,
    DiscoveryResult,
)
from orchestration_platform.governance.agent_discovery.semantic_index_repository_local import (
    LocalSemanticIndexRepository,
)
from orchestration_platform.governance.agent_discovery.vector_index import (
    VectorIndex,
)


def _run_async(coro):
    """异步协程同步执行辅助（不使用 asyncio.run，兼容事件循环已存在场景）。"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_profile(
    agent_id: str,
    name: str = "",
    description: str = "",
    tags=None,
    capabilities=None,
    required_permissions=None,
    tenant_id: str = "",
    source: DescriptionSource = DescriptionSource.PROVIDER,
) -> AgentSemanticProfile:
    """构造 Agent 语义档案辅助。"""
    return AgentSemanticProfile(
        agent_id=agent_id,
        name=name or agent_id,
        description=description,
        description_source=source,
        tags=tags or [],
        capabilities=capabilities or [],
        required_permissions=required_permissions or [],
        tenant_id=tenant_id,
    )


def _make_caller(
    user_id: str = "user-1",
    tenant_id: str = "",
    permissions=None,
) -> CallerContext:
    """构造调用方上下文辅助。"""
    return CallerContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=[],
        permissions=permissions or [],
    )


# =============================================================================
# 关键词倒排索引测试
# =============================================================================


class TestKeywordInvertedIndex(unittest.TestCase):
    """关键词倒排索引测试（纯标准库）。"""

    def test_add_and_search_basic(self):
        """基本添加与检索。"""
        index = KeywordInvertedIndex()
        index.add_document(
            agent_id="agent-1",
            text="data cleaning pipeline etl",
            tags=["data", "etl"],
        )
        index.add_document(
            agent_id="agent-2",
            text="image classification model training",
            tags=["ml", "vision"],
        )
        hits = index.search("data cleaning")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].agent_id, "agent-1")
        self.assertGreater(hits[0].score, 0.0)

    def test_search_no_match_returns_empty(self):
        """无命中返回空列表。"""
        index = KeywordInvertedIndex()
        index.add_document(agent_id="agent-1", text="data cleaning")
        hits = index.search("image classification")
        self.assertEqual(len(hits), 0)

    def test_search_empty_index_returns_empty(self):
        """空索引检索返回空。"""
        index = KeywordInvertedIndex()
        self.assertEqual(index.search("anything"), [])

    def test_score_normalized_to_unit_interval(self):
        """评分归一化到 [0, 1]。"""
        index = KeywordInvertedIndex()
        index.add_document(agent_id="a1", text="data cleaning etl")
        index.add_document(agent_id="a2", text="data processing")
        hits = index.search("data cleaning etl")
        for hit in hits:
            self.assertGreaterEqual(hit.score, 0.0)
            self.assertLessEqual(hit.score, 1.0)
        # 最高分应归一化为 1.0
        self.assertAlmostEqual(hits[0].score, 1.0, places=5)

    def test_tags_filter_and_semantics(self):
        """标签过滤为 AND 语义。"""
        index = KeywordInvertedIndex()
        index.add_document(
            agent_id="a1", text="data processing", tags=["data", "etl"]
        )
        index.add_document(
            agent_id="a2", text="data processing", tags=["data"]
        )
        # 要求同时命中 data 和 etl
        hits = index.search("data", tags_filter=["data", "etl"])
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].agent_id, "a1")

    def test_remove_document(self):
        """移除文档。"""
        index = KeywordInvertedIndex()
        index.add_document(agent_id="a1", text="data cleaning")
        self.assertTrue(index.remove_document("a1"))
        self.assertEqual(index.search("data cleaning"), [])
        self.assertFalse(index.remove_document("a1"))

    def test_add_document_idempotent(self):
        """重复添加相同 agent_id 幂等。"""
        index = KeywordInvertedIndex()
        index.add_document(agent_id="a1", text="data cleaning")
        index.add_document(agent_id="a1", text="image classification")
        self.assertEqual(index.document_count(), 1)
        # 应只命中新文本
        hits = index.search("image classification")
        self.assertEqual(len(hits), 1)

    def test_chinese_tokenization(self):
        """中文分词（二元组兜底）。"""
        index = KeywordInvertedIndex()
        index.add_document(agent_id="a1", text="数据清洗管道")
        index.add_document(agent_id="a2", text="图像分类模型")
        hits = index.search("数据清洗")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].agent_id, "a1")

    def test_clear(self):
        """清空索引。"""
        index = KeywordInvertedIndex()
        index.add_document(agent_id="a1", text="data")
        index.clear()
        self.assertEqual(index.document_count(), 0)
        self.assertEqual(index.search("data"), [])


# =============================================================================
# 向量索引测试
# =============================================================================


class TestVectorIndex(unittest.TestCase):
    """向量索引测试（哈希兜底）。"""

    def test_upsert_and_search(self):
        """基本添加与检索。"""
        vi = VectorIndex()
        vi.upsert("a1", "data cleaning pipeline")
        vi.upsert("a2", "image classification model")
        hits = vi.search("data cleaning")
        # 向量索引仅返回正相似度命中；相似文本应排前
        self.assertGreater(len(hits), 0)
        self.assertEqual(hits[0].agent_id, "a1")

    def test_search_empty_returns_empty(self):
        """空索引检索返回空。"""
        vi = VectorIndex()
        self.assertEqual(vi.search("anything"), [])

    def test_remove(self):
        """移除向量。"""
        vi = VectorIndex()
        vi.upsert("a1", "data cleaning")
        self.assertTrue(vi.remove("a1"))
        self.assertEqual(vi.search("data cleaning"), [])
        self.assertFalse(vi.remove("a1"))

    def test_score_in_unit_interval(self):
        """余弦相似度在 [0, 1]（归一化向量非负）。"""
        vi = VectorIndex()
        vi.upsert("a1", "data cleaning etl pipeline")
        hits = vi.search("data cleaning etl pipeline")
        self.assertEqual(len(hits), 1)
        self.assertGreaterEqual(hits[0].score, 0.0)
        self.assertLessEqual(hits[0].score, 1.0)

    def test_unknown_backend_degrades_to_hash(self):
        """未知后端降级哈希兜底。"""
        vi = VectorIndex(backend="nonexistent_backend")
        self.assertFalse(vi.is_external_ready())
        vi.upsert("a1", "data cleaning")
        hits = vi.search("data cleaning")
        self.assertEqual(len(hits), 1)

    def test_empty_backend_uses_hash(self):
        """空后端使用哈希兜底。"""
        vi = VectorIndex(backend="")
        self.assertFalse(vi.is_external_ready())
        vi.upsert("a1", "data cleaning")
        self.assertEqual(vi.document_count(), 1)


# =============================================================================
# RRF 混合检索测试
# =============================================================================


class TestHybridRetriever(unittest.TestCase):
    """RRF 混合检索测试。"""

    def test_pure_keyword_when_no_vector(self):
        """无向量索引时纯关键词检索。"""
        ki = KeywordInvertedIndex()
        ki.add_document(agent_id="a1", text="data cleaning")
        retriever = HybridRetriever(keyword_index=ki, vector_index=None)
        hits, backend = retriever.retrieve("data cleaning")
        self.assertEqual(backend, "keyword")
        self.assertEqual(len(hits), 1)

    def test_hybrid_when_vector_present(self):
        """向量索引有数据时混合检索。"""
        ki = KeywordInvertedIndex()
        ki.add_document(agent_id="a1", text="data cleaning pipeline")
        ki.add_document(agent_id="a2", text="image classification model")
        vi = VectorIndex()
        vi.upsert("a1", "data cleaning pipeline")
        vi.upsert("a2", "image classification model")
        retriever = HybridRetriever(keyword_index=ki, vector_index=vi)
        hits, backend = retriever.retrieve("data cleaning")
        self.assertEqual(backend, "hybrid")
        self.assertGreater(len(hits), 0)
        # a1 应排前（两路都命中）
        self.assertEqual(hits[0].agent_id, "a1")

    def test_rrf_fusion_normalizes_scores(self):
        """RRF 融合后分数归一化。"""
        ki = KeywordInvertedIndex()
        ki.add_document(agent_id="a1", text="data cleaning etl")
        ki.add_document(agent_id="a2", text="data processing")
        vi = VectorIndex()
        vi.upsert("a1", "data cleaning etl")
        vi.upsert("a2", "data processing")
        retriever = HybridRetriever(keyword_index=ki, vector_index=vi)
        hits, _ = retriever.retrieve("data cleaning etl")
        for hit in hits:
            self.assertGreaterEqual(hit.score, 0.0)
            self.assertLessEqual(hit.score, 1.0)
        self.assertAlmostEqual(hits[0].score, 1.0, places=5)

    def test_rrf_fusion_combines_both_channels(self):
        """RRF 融合合并两路命中。"""
        ki = KeywordInvertedIndex()
        ki.add_document(agent_id="a1", text="data cleaning")
        vi = VectorIndex()
        vi.upsert("a1", "data cleaning")
        vi.upsert("a2", "data cleansing")  # 仅向量命中
        retriever = HybridRetriever(keyword_index=ki, vector_index=vi)
        hits, _ = retriever.retrieve("data cleaning")
        agent_ids = {h.agent_id for h in hits}
        self.assertIn("a1", agent_ids)


# =============================================================================
# 本地语义索引仓储测试
# =============================================================================


class TestLocalSemanticIndexRepository(unittest.TestCase):
    """本地语义索引仓储测试。"""

    def test_upsert_and_get(self):
        """新增与查询。"""
        repo = LocalSemanticIndexRepository()
        profile = _make_profile("a1", description="data cleaning")
        repo.upsert_profile(profile)
        got = repo.get_profile("a1")
        self.assertIsNotNone(got)
        self.assertEqual(got.agent_id, "a1")

    def test_get_nonexistent_returns_none(self):
        """查询不存在返回 None。"""
        repo = LocalSemanticIndexRepository()
        self.assertIsNone(repo.get_profile("nonexistent"))

    def test_remove(self):
        """移除。"""
        repo = LocalSemanticIndexRepository()
        repo.upsert_profile(_make_profile("a1"))
        self.assertTrue(repo.remove_profile("a1"))
        self.assertFalse(repo.remove_profile("a1"))
        self.assertIsNone(repo.get_profile("a1"))

    def test_list_profiles(self):
        """列出全部档案。"""
        repo = LocalSemanticIndexRepository()
        repo.upsert_profile(_make_profile("a1"))
        repo.upsert_profile(_make_profile("a2"))
        profiles = repo.list_profiles()
        self.assertEqual(len(profiles), 2)

    def test_upsert_overwrites(self):
        """重复 upsert 覆盖。"""
        repo = LocalSemanticIndexRepository()
        repo.upsert_profile(_make_profile("a1", description="old"))
        repo.upsert_profile(_make_profile("a1", description="new"))
        got = repo.get_profile("a1")
        self.assertEqual(got.description, "new")
        self.assertEqual(repo.count(), 1)


# =============================================================================
# 发现服务测试
# =============================================================================


class TestDiscoveryService(unittest.TestCase):
    """发现服务测试。"""

    def _build_service_with_profiles(self, profiles, with_vector=True):
        """构造带初始档案的服务。

        with_vector=False 时纯关键词模式（无向量索引）。
        """
        repo = LocalSemanticIndexRepository()
        ki = KeywordInvertedIndex()
        vi = VectorIndex() if with_vector else None
        for p in profiles:
            repo.upsert_profile(p)
            text = " ".join([p.name, p.description] + p.capabilities + p.tags)
            ki.add_document(p.agent_id, text, p.tags)
            if vi is not None:
                vi.upsert(p.agent_id, text)
        service = DiscoveryService(
            repository=repo, keyword_index=ki, vector_index=vi
        )
        service._index_built = True
        return service

    def test_discover_basic(self):
        """基本发现查询。"""
        profiles = [
            _make_profile(
                "a1",
                name="数据清洗 Agent",
                description="data cleaning pipeline etl",
                tags=["data", "etl"],
                capabilities=["clean", "transform"],
            ),
            _make_profile(
                "a2",
                name="图像分类 Agent",
                description="image classification model",
                tags=["ml", "vision"],
            ),
        ]
        service = self._build_service_with_profiles(profiles)
        result = service.discover(
            DiscoveryQuery(
                intent_text="data cleaning etl",
                caller=_make_caller(),
            )
        )
        self.assertIsInstance(result, DiscoveryResult)
        self.assertGreater(result.total, 0)
        self.assertEqual(result.items[0].agent_id, "a1")

    def test_discover_min_query_length_rejects(self):
        """最短查询长度校验。"""
        service = self._build_service_with_profiles([])
        with self.assertRaises(DomainError) as ctx:
            service.discover(
                DiscoveryQuery(
                    intent_text="ab",  # 短于默认最短长度 4
                    caller=_make_caller(),
                )
            )
        self.assertEqual(ctx.exception.code, INTENT_QUERY_INVALID)

    def test_discover_rbac_filtering(self):
        """RBAC 过滤：调用方无权限的 Agent 被过滤。"""
        profiles = [
            _make_profile(
                "a1",
                name="公开 Agent",
                description="public data query",
                required_permissions=[],
            ),
            _make_profile(
                "a2",
                name="受限 Agent",
                description="data query admin",
                required_permissions=["admin"],
            ),
        ]
        service = self._build_service_with_profiles(profiles)
        # 调用方无 admin 权限
        result = service.discover(
            DiscoveryQuery(
                intent_text="data query",
                caller=_make_caller(permissions=[]),
            )
        )
        agent_ids = {item.agent_id for item in result.items}
        self.assertIn("a1", agent_ids)
        self.assertNotIn("a2", agent_ids)

    def test_discover_rbac_allows_with_permission(self):
        """RBAC 过滤：调用方具备权限则放行。"""
        profiles = [
            _make_profile(
                "a2",
                name="受限 Agent",
                description="data query admin",
                required_permissions=["admin"],
            ),
        ]
        service = self._build_service_with_profiles(profiles)
        result = service.discover(
            DiscoveryQuery(
                intent_text="data query admin",
                caller=_make_caller(permissions=["admin"]),
            )
        )
        agent_ids = {item.agent_id for item in result.items}
        self.assertIn("a2", agent_ids)

    def test_discover_tenant_isolation(self):
        """租户隔离过滤。"""
        profiles = [
            _make_profile(
                "a1",
                name="租户 A Agent",
                description="data query tenant a",
                tenant_id="tenant-a",
            ),
            _make_profile(
                "a2",
                name="租户 B Agent",
                description="data query tenant b",
                tenant_id="tenant-b",
            ),
        ]
        service = self._build_service_with_profiles(profiles)
        result = service.discover(
            DiscoveryQuery(
                intent_text="data query tenant",
                caller=_make_caller(tenant_id="tenant-a"),
            )
        )
        agent_ids = {item.agent_id for item in result.items}
        self.assertIn("a1", agent_ids)
        self.assertNotIn("a2", agent_ids)

    def test_discover_relevance_threshold_filtering(self):
        """相关度阈值过滤。

        使用纯关键词模式：弱匹配仅共享部分词元，相关度低于阈值被过滤。
        """
        profiles = [
            _make_profile(
                "a1",
                name="精确匹配",
                description="data cleaning pipeline etl transform",
                tags=["data", "etl"],
                capabilities=["clean", "transform", "pipeline"],
            ),
            _make_profile(
                "a2",
                name="弱匹配",
                description="data unrelated mismatch content",
            ),
        ]
        # 纯关键词模式，避免向量哈希兜底噪声
        service = self._build_service_with_profiles(profiles, with_vector=False)
        result = service.discover(
            DiscoveryQuery(
                intent_text="data cleaning pipeline etl transform",
                caller=_make_caller(),
            )
        )
        # 精确匹配应在结果中
        agent_ids = {item.agent_id for item in result.items}
        self.assertIn("a1", agent_ids)
        # 弱匹配仅共享 "data" 一个词元，相关度应低于阈值被过滤
        self.assertNotIn("a2", agent_ids)

    def test_discover_result_limit(self):
        """结果数量上限。"""
        profiles = [
            _make_profile(f"a{i}", name=f"Agent {i}", description="data query")
            for i in range(5)
        ]
        service = self._build_service_with_profiles(profiles)
        result = service.discover(
            DiscoveryQuery(
                intent_text="data query",
                caller=_make_caller(),
                limit=2,
            )
        )
        self.assertLessEqual(result.total, 2)

    def test_discover_tags_filter(self):
        """标签过滤。"""
        profiles = [
            _make_profile(
                "a1",
                name="数据 Agent",
                description="data processing query",
                tags=["data", "etl"],
            ),
            _make_profile(
                "a2",
                name="ML Agent",
                description="data processing query model",
                tags=["ml"],
            ),
        ]
        service = self._build_service_with_profiles(profiles)
        result = service.discover(
            DiscoveryQuery(
                intent_text="data processing query",
                caller=_make_caller(),
                tags_filter=["data"],
            )
        )
        agent_ids = {item.agent_id for item in result.items}
        self.assertIn("a1", agent_ids)
        self.assertNotIn("a2", agent_ids)

    def test_upsert_profile_truncates_long_description(self):
        """描述超长截断。"""
        service = self._build_service_with_profiles([])
        long_desc = "x" * 10000
        profile = _make_profile("a1", description=long_desc)
        saved = service.upsert_profile(profile)
        settings = get_settings()
        self.assertLessEqual(len(saved.description), settings.agent_discovery_desc_max_length)

    def test_remove_profile_syncs_index(self):
        """移除档案同步索引。"""
        profiles = [
            _make_profile("a1", name="数据 Agent", description="data cleaning"),
        ]
        service = self._build_service_with_profiles(profiles)
        self.assertTrue(service.remove_profile("a1"))
        result = service.discover(
            DiscoveryQuery(
                intent_text="data cleaning",
                caller=_make_caller(),
            )
        )
        agent_ids = {item.agent_id for item in result.items}
        self.assertNotIn("a1", agent_ids)

    def test_rebuild_index(self):
        """索引重建。"""
        repo = LocalSemanticIndexRepository()
        repo.upsert_profile(_make_profile("a1", description="data cleaning"))
        repo.upsert_profile(_make_profile("a2", description="image classification"))
        service = DiscoveryService(repository=repo)
        count = service.rebuild_index()
        self.assertEqual(count, 2)
        result = service.discover(
            DiscoveryQuery(
                intent_text="data cleaning",
                caller=_make_caller(),
            )
        )
        self.assertGreater(result.total, 0)


# =============================================================================
# 描述来源三态管理测试
# =============================================================================


class TestDescriptionSourceThreeState(unittest.TestCase):
    """描述来源三态管理测试。"""

    def test_provider_source(self):
        """注册方提供来源。"""
        profile = _make_profile("a1", source=DescriptionSource.PROVIDER)
        self.assertEqual(profile.description_source, DescriptionSource.PROVIDER)
        self.assertEqual(profile.description_source.value, "provider")

    def test_llm_generated_source(self):
        """LLM 生成来源。"""
        profile = _make_profile("a1", source=DescriptionSource.LLM_GENERATED)
        self.assertEqual(profile.description_source, DescriptionSource.LLM_GENERATED)
        self.assertEqual(profile.description_source.value, "llm_generated")

    def test_manual_revision_source(self):
        """人工修订来源。"""
        profile = _make_profile("a1", source=DescriptionSource.MANUAL_REVISION)
        self.assertEqual(profile.description_source, DescriptionSource.MANUAL_REVISION)
        self.assertEqual(profile.description_source.value, "manual_revision")

    def test_upsert_preserves_source(self):
        """upsert 保留描述来源。"""
        repo = LocalSemanticIndexRepository()
        service = DiscoveryService(repository=repo)
        for source in [
            DescriptionSource.PROVIDER,
            DescriptionSource.LLM_GENERATED,
            DescriptionSource.MANUAL_REVISION,
        ]:
            profile = _make_profile(f"agent-{source.value}", source=source)
            service.upsert_profile(profile)
            got = service.get_profile(profile.agent_id)
            self.assertEqual(got.description_source, source)


# =============================================================================
# 配置与兼容性测试
# =============================================================================


class TestAgentDiscoveryConfig(unittest.TestCase):
    """Agent 发现配置测试。"""

    def test_disabled_by_default(self):
        """默认禁用。"""
        settings = get_settings()
        self.assertFalse(settings.agent_discovery_enabled)

    def test_vector_backend_empty_by_default(self):
        """默认向量后端为空（纯关键词）。"""
        settings = get_settings()
        self.assertEqual(settings.agent_discovery_vector_backend, "")

    def test_relevance_threshold_default(self):
        """相关度阈值默认值。"""
        settings = get_settings()
        self.assertGreater(settings.agent_discovery_relevance_threshold, 0.0)
        self.assertLess(settings.agent_discovery_relevance_threshold, 1.0)

    def test_min_query_length_default(self):
        """最短查询长度默认值。"""
        settings = get_settings()
        self.assertGreaterEqual(settings.agent_discovery_min_query_length, 1)

    def test_result_limit_default(self):
        """结果数量上限默认值。"""
        settings = get_settings()
        self.assertGreaterEqual(settings.agent_discovery_result_limit, 1)


# =============================================================================
# 抽象协议兼容性测试
# =============================================================================


class TestRepositoryProtocol(unittest.TestCase):
    """仓储抽象协议兼容性测试。"""

    def test_local_repo_satisfies_protocol(self):
        """本地仓储满足 ISemanticIndexRepository 协议。"""
        from orchestration_platform.governance.agent_discovery.abstractions.semantic_index_repository import (
            ISemanticIndexRepository,
        )

        repo = LocalSemanticIndexRepository()
        self.assertIsInstance(repo, ISemanticIndexRepository)


if __name__ == "__main__":
    unittest.main()
