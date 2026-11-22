# =============================================================================
# 本地模式检索后端（integrations/vector_knowledge_base/local_backend.py）
#
# 职责：实现 local 模式检索器——运行时延迟导入探测 + 确定性降级（CHG-04）；
# B5 起叠加独立开关触发的本组件委托（design DC-04/§6.5）。
# 设计意图（对应 vector_knowledge_base_node design §2.2.5 / spec FR-17、
# OP-10；local_vector_database design T5.2~T5.4/AC-29）：
#   1. 本期不实现真实本地推理（OP-10）：_try_load_local_runtime 仅做
#      延迟导入占位探测，为后续接入 sentence-transformers/faiss/qdrant
#      预留扩展点；
#   2. 运行时缺失 + allow_degraded_fallback=False → VectorRetrievalError
#      （NODE_VECTOR_LOCAL_RUNTIME_MISSING，确定性失败不崩溃，AC-30）；
#   3. 运行时缺失 + allow_degraded_fallback=True → 确定性哈希伪向量兜底
#      + 空结果降级 + local_degraded 标注（FR-17/AC-31：静默伪造检索
#      结果 = 违规，故空片段 + 显式降级原因，不伪造语义）；
#   4. 运行时就绪但本期未实现推理 → 同样空结果降级（OP-10，不伪造语义）；
#   5. 无外部依赖、无 I/O：哈希伪向量为本地实现（借鉴
#      governance/agent_discovery/vector_index.py §6.1 兜底，不跨层引用）；
#   6. B5 委托改造：独立开关 local_vector_database_enabled 启用且引擎
#      可用 → 委托本组件 LocalVectorDatabaseService 真实检索；默认开关
#      False → 不触达本组件、既有三态逐字不变（INV-L04/INV-L05 兼容
#      守护；严禁以 _local_runtime_ready 作委托触发条件，DC-04 备选③
#      否决）。
# =============================================================================

import hashlib
import importlib
from typing import List, Optional, Tuple

from orchestration_platform.domain.errors import NODE_VECTOR_LOCAL_RUNTIME_MISSING
from orchestration_platform.domain.models.node_configurations import ModelLoadingConfig
from orchestration_platform.integrations.vector_knowledge_base.models import (
    RetrievalOutcome,
    VectorRetrievalError,
)
from orchestration_platform.integrations.vector_knowledge_base.retriever import (
    VectorKnowledgeBaseRetriever,
)

# 哈希伪向量维度（spec §6.1：128 维 SHA-256 确定性兜底）
_HASH_VECTOR_DIM = 128

# 本地运行时依赖探测清单（延迟导入占位，OP-10：本期未实现真实推理）
_LOCAL_RUNTIME_MODULE_CANDIDATES: tuple = (
    "sentence_transformers",
    "faiss",
    "qdrant_client",
)


def _hash_vector(text: str) -> List[float]:
    """确定性哈希伪向量（spec §6.1 兜底，与 agent_discovery 同构）。

    对相同文本产生相同向量，不同文本产生近似正交向量；
    仅用于 local 降级路径的可测试性，不用于真实检索语义。
    """
    vec = [0.0] * _HASH_VECTOR_DIM
    if not text:
        return vec
    tokens = text.lower().split()
    if not tokens:
        tokens = [text]
    for token in tokens:
        for offset in range(3):
            digest = hashlib.sha256(f"{token}:{offset}".encode("utf-8")).digest()
            for i in range(0, min(len(digest), 16), 2):
                idx = (digest[i] ^ offset) % _HASH_VECTOR_DIM
                vec[idx] += ((digest[i + 1] % 100) / 100.0) - 0.5
    norm = sum(v * v for v in vec) ** 0.5
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


class _EngineFallbackSignal(Exception):
    """引擎委托路径异常回落既有三态的内部信号（模块私有，不外泄）。

    语义（T5.3）：engine 内部异常（含 VECTOR_DB_* 错误）不得暴露到
    节点侧 → 转为本信号由 retrieve() 捕获后回落既有三态逻辑。
    """


class LocalKnowledgeBaseRetriever(VectorKnowledgeBaseRetriever):
    """local 模式检索器：运行时探测 + 确定性降级 + 可选本组件委托（B5）。"""

    def __init__(self) -> None:
        # 运行时就绪状态缓存：None=未探测；探测一次后固化（幂等）
        self._local_runtime_ready: Optional[bool] = None
        # 本组件引擎缓存（T5.2）：仅开关启用且构造成功时固化；否则保持 None
        self._engine: Optional[object] = None

    def _try_load_local_runtime(self) -> bool:
        """延迟导入探测本地运行时依赖（占位实现，OP-10）。

        任一候选模块可导入即视为就绪；全部缺失视为运行时不可用。
        异常一律按缺失处理（确定性，不崩溃）。
        """
        if self._local_runtime_ready is not None:
            return self._local_runtime_ready
        ready = False
        for module_name in _LOCAL_RUNTIME_MODULE_CANDIDATES:
            try:
                importlib.import_module(module_name)
                ready = True
                break
            except Exception:
                continue
        self._local_runtime_ready = ready
        return ready

    def _try_attach_local_engine(self) -> Optional[object]:
        """构造/获取本组件引擎（T5.2/DC-04）：仅独立开关启用时触达本组件。

        - settings.local_vector_database_enabled 不存在（B6 落地前容错，
          按“视为 False”）/ 为 False → None（不触达本组件，AC-28/INV-L05）；
        - 开关启用 → 延迟构造 LocalVectorDatabaseService（引擎）并缓存；
        - 导入/构造任何异常 → None（fail-safe 确定性回落，绝不悬挂）。
        """
        if self._engine is not None:
            return self._engine
        enabled = False
        try:
            from orchestration_platform.configuration.settings import get_settings

            enabled = bool(
                getattr(get_settings(), "local_vector_database_enabled", False)
            )
        except Exception:
            enabled = False
        if not enabled:
            return None
        try:
            from orchestration_platform.integrations.local_vector_database import (
                LocalVectorDatabaseService,
            )
            from orchestration_platform.integrations.local_vector_database.config import (
                from_settings as lvd_config_from_settings,
            )

            self._engine = LocalVectorDatabaseService(
                config=lvd_config_from_settings()
            )
        except Exception:
            self._engine = None
        return self._engine

    def _retrieve_via_engine(
        self,
        engine: object,
        knowledge_base_id: str,
        query: str,
        top_k: int,
        embedding_model: ModelLoadingConfig,
        rerank_model: Optional[ModelLoadingConfig],
    ) -> RetrievalOutcome:
        """委托本组件检索（T5.3/DC-04）：真实来源 → 真实检索；其余确定性降级。

        - 集合不存在/为空 → 空片段 + local_degraded + reason（保持既有
          降级语义）；
        - 真实向量来源不可用且未开兜底 → NODE_VECTOR_LOCAL_RUNTIME_MISSING
          （与既有三态一致）；开启兜底 → 仅哈希兜底路径（计算即弃 +
          空结果 + 强制降级标注，AC-31/INV-L08）；
        - engine 内部异常 → _EngineFallbackSignal（回落既有三态，不暴露
          VECTOR_DB_* 到节点侧）。
        """
        from orchestration_platform.integrations.local_vector_database.embedding import (
            PROVENANCE_HASH_FALLBACK,
            enforce_degraded_annotation,
        )

        # 集合存在性/规模检查（诊断只读；异常 → 回落既有三态，fail-safe）
        try:
            diagnosis = engine.diagnose(knowledge_base_id)
        except Exception:
            raise _EngineFallbackSignal from None
        if diagnosis.record_count == 0:
            return RetrievalOutcome(
                chunks=[],
                local_degraded=True,
                local_degraded_reason=(
                    "本地向量数据库集合不存在或为空，"
                    "已确定性降级为空结果（不伪造检索语义）"
                ),
            )
        declared_dimension = diagnosis.declared_dimension or _HASH_VECTOR_DIM
        # 查询向量来源解析（真实来源 → 真实检索；不可用 → 既有语义）
        try:
            vector, provenance = self._resolve_query_vector(
                query, embedding_model, declared_dimension
            )
        except Exception:
            if not embedding_model.allow_degraded_fallback:
                raise VectorRetrievalError(
                    NODE_VECTOR_LOCAL_RUNTIME_MISSING,
                    (
                        f"本地 embedding 运行时依赖缺失（模型标识："
                        f"{embedding_model.model_identifier}）；如需确定性兜底降级，"
                        "请在配置中开启 allow_degraded_fallback"
                    ),
                ) from None
            vector, provenance = None, PROVENANCE_HASH_FALLBACK
        if vector is None or provenance == PROVENANCE_HASH_FALLBACK:
            # 仅哈希兜底（AC-31/INV-L08）：哈希来源永不产生真实检索语义
            # （确定性哈希计算即弃 + 空结果 + 强制降级标注）
            _hash_vector(query)
            outcome = RetrievalOutcome(
                chunks=[],
                local_degraded=True,
                local_degraded_reason=(
                    f"查询向量真实来源不可用，已按确定性哈希伪向量兜底降级"
                    f"（模型标识：{embedding_model.model_identifier}；"
                    "不伪造检索语义）"
                ),
            )
            enforce_degraded_annotation(
                provenance, outcome.local_degraded, outcome.local_degraded_reason
            )
            return outcome
        # 真实向量来源 → 委托引擎真实检索；engine 内部异常 → 回落既有三态
        try:
            return engine.retrieve(knowledge_base_id, vector, top_k)
        except Exception:
            raise _EngineFallbackSignal from None

    def _resolve_query_vector(
        self,
        query: str,
        embedding_model: ModelLoadingConfig,
        declared_dimension: int,
    ) -> Tuple[List[float], str]:
        """解析查询向量的真实来源（api/local 运行时；测试可替换 seam）。

        - mode=api → ExternalApiEmbeddingProvider（密钥仅来自 settings，
          INV-D04；POST {base_url}/embeddings，复用既有 api 契约）；
        - mode=local → LocalRuntimeEmbeddingProvider（sentence_transformers
          延迟导入，缺失/加载失败即异常）；
        - 真实来源不可用 → 抛异常（由调用方按 allow_degraded_fallback
          决定既有语义，本方法不伪造向量，INV-L08）。
        """
        from orchestration_platform.integrations.local_vector_database.embedding import (
            ExternalApiEmbeddingProvider,
            LocalRuntimeEmbeddingProvider,
        )

        if embedding_model.mode == "api":
            provider = ExternalApiEmbeddingProvider(
                embedding_model,
                api_key=self._read_embedding_api_key(),
                dimension=declared_dimension,
            )
            return provider.embed(query), provider.provenance
        provider = LocalRuntimeEmbeddingProvider(
            embedding_model.model_identifier,
            dimension=declared_dimension,
            device=embedding_model.device or "cpu",
        )
        return provider.embed(query), provider.provenance

    @staticmethod
    def _read_embedding_api_key() -> Optional[str]:
        """api 模式密钥仅来自运行期 settings（INV-D04；禁写入错误/日志）。"""
        try:
            from orchestration_platform.configuration.settings import get_settings

            return getattr(get_settings(), "vector_kb_search_api_key", None)
        except Exception:
            return None

    def retrieve(
        self,
        knowledge_base_id: str,
        query: str,
        top_k: int,
        embedding_model: ModelLoadingConfig,
        rerank_model: Optional[ModelLoadingConfig],
    ) -> RetrievalOutcome:
        """local 模式检索：独立开关启用且引擎可用时委托本组件，否则确定性降级。

        - 开关启用且引擎可用 → 委托本组件真实检索（DC-04/T5.4）；
        - 运行时缺失且未开启兜底 → NODE_VECTOR_LOCAL_RUNTIME_MISSING 失败；
        - 其余情形 → 空片段 + local_degraded 标注（不伪造检索语义）。
        """
        # 前置委托分支（T5.4/DC-04）：仅由独立开关触发（严禁以
        # _local_runtime_ready 作触发条件，DC-04 备选③否决）；引擎委托
        # 异常 → 回落既有三态（不暴露 VECTOR_DB_*，T5.3）
        engine = self._try_attach_local_engine()
        if engine is not None:
            try:
                return self._retrieve_via_engine(
                    engine,
                    knowledge_base_id,
                    query,
                    top_k,
                    embedding_model,
                    rerank_model,
                )
            except _EngineFallbackSignal:
                pass
        runtime_ready = self._try_load_local_runtime()
        if runtime_ready:
            return RetrievalOutcome(
                chunks=[],
                local_degraded=True,
                local_degraded_reason=(
                    "本地运行时已就绪但本期未实现真实本地推理，"
                    "已确定性降级为空结果（不伪造检索语义）"
                ),
            )
        if not embedding_model.allow_degraded_fallback:
            raise VectorRetrievalError(
                NODE_VECTOR_LOCAL_RUNTIME_MISSING,
                (
                    f"本地 embedding 运行时依赖缺失（模型标识："
                    f"{embedding_model.model_identifier}）；如需确定性兜底降级，"
                    "请在配置中开启 allow_degraded_fallback"
                ),
            )
        # 确定性哈希伪向量兜底（spec §6.1）：保证降级路径可测试，
        # 不产生任何检索语义（空片段 + 显式降级标注，AC-31）
        _hash_vector(query)
        return RetrievalOutcome(
            chunks=[],
            local_degraded=True,
            local_degraded_reason=(
                f"本地模型运行时不可用（延迟导入失败），已按确定性哈希伪向量"
                f"兜底降级（模型标识：{embedding_model.model_identifier}；"
                "不伪造检索语义）"
            ),
        )
