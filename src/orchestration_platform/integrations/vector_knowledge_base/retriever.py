# =============================================================================
# 向量知识库检索器（integrations/vector_knowledge_base/retriever.py）
#
# 职责：定义检索器抽象与 API（HTTP）模式实现（CHG-03 / design §2.2.4）。
# 设计意图（对应 vector_knowledge_base_node design §2.2.2 / §2.2.5）：
#   1. 三段调用流：embedding 向量化 → 知识库检索 → rerank 重排序（可选）；
#   2. rerank 失败一律降级为初排结果 + rerank_degraded 标注，不判节点失败
#      （design §2.2.5 降级矩阵；无 rerank 即跳过，AC-28）；
#   3. embedding/检索不可用 → VectorRetrievalError（映射节点失败，INV-D07：
#      检索失败 ≠ 空结果），错误消息为固定文本 + 非凭据定位信息（SV-3）；
#   4. httpx 延迟导入收敛于 _post_json 单一 seam（沙箱环境无网络依赖，
#      单测可注入假 httpx.post）；
#   5. 检索服务地址/密钥/超时来自运行期 settings（CHG-09 字段，缺省容错），
#      配置内不携带密钥（INV-D04）；embedding/rerank 服务地址来自节点配置
#      的 base_url（非凭据）。
# =============================================================================

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from orchestration_platform.domain.errors import (
    NODE_VECTOR_EMBEDDING_UNAVAILABLE,
    NODE_VECTOR_RETRIEVAL_UNAVAILABLE,
)
from orchestration_platform.domain.models.common import JsonValue
from orchestration_platform.domain.models.node_configurations import ModelLoadingConfig
from orchestration_platform.integrations.vector_knowledge_base.models import (
    Chunk,
    RetrievalOutcome,
    VectorRetrievalError,
    sanitize_chunk_payload,
)

# 默认服务超时（秒）：未配置 timeout_seconds 时的兜底
DEFAULT_TIMEOUT_SECONDS = 60

# rerank 层内部错误码：仅作为 VectorRetrievalError 载体，永远被 retrieve()
# 捕获并转为 rerank_degraded 标注，不会以节点失败形态外泄（design §2.2.5）
_RERANK_INTERNAL_ERROR_CODE = "NODE_VECTOR_RERANK_UNAVAILABLE"


class _HttpTransportError(Exception):
    """HTTP 传输层内部异常（传输失败/非 2xx/响应非 JSON 对象）。

    仅在 retriever 内部使用：由 _post_json 抛出，调用方将其映射为
    对应的 VectorRetrievalError（消息为固定文本，不含 URL/状态细节）。
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _read_setting(name: str, default: Any) -> Any:
    """容错读取运行期配置（CHG-09 前字段不存在时返回缺省值）。"""
    try:
        from orchestration_platform.configuration.settings import get_settings

        return getattr(get_settings(), name, default)
    except Exception:
        return default


class VectorKnowledgeBaseRetriever(ABC):
    """向量知识库检索器抽象（CHG-03）：执行器只依赖该接口。"""

    @abstractmethod
    def retrieve(
        self,
        knowledge_base_id: str,
        query: str,
        top_k: int,
        embedding_model: ModelLoadingConfig,
        rerank_model: Optional[ModelLoadingConfig],
    ) -> RetrievalOutcome:
        """执行检索，返回净化后的结果（含降级标注）。

        抛出 VectorRetrievalError 表示检索链路不可用（调用方映射节点失败）。
        """


class HttpKnowledgeBaseRetriever(VectorKnowledgeBaseRetriever):
    """API（HTTP）模式检索器：embedding → 检索 → rerank 三段调用流。"""

    def retrieve(
        self,
        knowledge_base_id: str,
        query: str,
        top_k: int,
        embedding_model: ModelLoadingConfig,
        rerank_model: Optional[ModelLoadingConfig],
    ) -> RetrievalOutcome:
        """三段调用流：向量化 → 检索 → 重排序（可选，失败降级初排）。"""
        vector = self._embed_query(query, embedding_model)
        chunks = self._search(knowledge_base_id, query, vector, top_k)
        if rerank_model is None:
            # rerank 未配置即跳过：合法成功，不产生降级标注（AC-28）
            return RetrievalOutcome(chunks=chunks)
        try:
            reranked = self._rerank_chunks(query, chunks, rerank_model)
        except VectorRetrievalError as error:
            # rerank 失败降级：保留初排结果 + 标注原因，不判节点失败
            return RetrievalOutcome(
                chunks=chunks,
                rerank_degraded=True,
                rerank_degraded_reason=error.error_message,
            )
        return RetrievalOutcome(chunks=reranked)

    # ------------------------------------------------------------------
    # embedding 向量化
    # ------------------------------------------------------------------

    def _embed_query(
        self, query: str, embedding_model: ModelLoadingConfig
    ) -> List[float]:
        """调用 embedding 服务将查询文本向量化。

        失败一律抛 NODE_VECTOR_EMBEDDING_UNAVAILABLE（消息含模型标识，
        不含服务地址/密钥/原始响应，SV-3）。
        """
        base_url = (embedding_model.base_url or "").strip()
        model_identifier = embedding_model.model_identifier
        if not base_url:
            raise VectorRetrievalError(
                NODE_VECTOR_EMBEDDING_UNAVAILABLE,
                f"embedding 模型服务地址未配置（模型标识：{model_identifier}）",
            )
        timeout_seconds = embedding_model.timeout_seconds or DEFAULT_TIMEOUT_SECONDS
        try:
            response = self._post_json(
                f"{base_url.rstrip('/')}/embeddings",
                {"model": model_identifier, "input": query},
                timeout_seconds,
            )
        except _HttpTransportError:
            raise VectorRetrievalError(
                NODE_VECTOR_EMBEDDING_UNAVAILABLE,
                f"embedding 向量化服务调用失败（模型标识：{model_identifier}）",
            ) from None
        vector = self._extract_embedding_vector(response)
        if vector is None:
            raise VectorRetrievalError(
                NODE_VECTOR_EMBEDDING_UNAVAILABLE,
                f"embedding 向量化响应结构无效（模型标识：{model_identifier}）",
            )
        return vector

    @staticmethod
    def _extract_embedding_vector(response: Dict[str, JsonValue]) -> Optional[List[float]]:
        """从 embeddings 响应中提取向量：data[0].embedding，结构不符返回 None。"""
        data = response.get("data")
        if not isinstance(data, list) or not data:
            return None
        first = data[0]
        if not isinstance(first, dict):
            return None
        embedding = first.get("embedding")
        if not isinstance(embedding, list) or not embedding:
            return None
        vector: List[float] = []
        for component in embedding:
            if isinstance(component, bool) or not isinstance(component, (int, float)):
                return None
            vector.append(float(component))
        return vector

    # ------------------------------------------------------------------
    # 知识库检索
    # ------------------------------------------------------------------

    def _search(
        self,
        knowledge_base_id: str,
        query: str,
        vector: List[float],
        top_k: int,
    ) -> List[Chunk]:
        """调用检索服务执行向量检索，返回净化后的片段列表。

        失败一律抛 NODE_VECTOR_RETRIEVAL_UNAVAILABLE（消息含知识库标识，
        不含服务地址/密钥/原始响应，SV-3）；不得以空结果冒充成功（INV-D07）。
        """
        search_base_url = str(_read_setting("vector_kb_search_base_url", "") or "").strip()
        if not search_base_url:
            raise VectorRetrievalError(
                NODE_VECTOR_RETRIEVAL_UNAVAILABLE,
                f"向量检索服务地址未配置（知识库：{knowledge_base_id}）",
            )
        timeout_seconds = int(_read_setting("vector_kb_search_timeout_seconds", 0) or 0)
        if timeout_seconds <= 0:
            timeout_seconds = DEFAULT_TIMEOUT_SECONDS
        headers: Dict[str, str] = {}
        api_key = str(_read_setting("vector_kb_search_api_key", "") or "").strip()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        try:
            response = self._post_json(
                f"{search_base_url.rstrip('/')}/search",
                {
                    "knowledge_base_id": knowledge_base_id,
                    "query": query,
                    "query_vector": vector,
                    "top_k": top_k,
                },
                timeout_seconds,
                headers=headers or None,
            )
        except _HttpTransportError:
            raise VectorRetrievalError(
                NODE_VECTOR_RETRIEVAL_UNAVAILABLE,
                f"向量检索服务调用失败（知识库：{knowledge_base_id}）",
            ) from None
        return sanitize_chunk_payload(response.get("chunks"))

    # ------------------------------------------------------------------
    # rerank 重排序
    # ------------------------------------------------------------------

    def _rerank_chunks(
        self,
        query: str,
        chunks: List[Chunk],
        rerank_model: ModelLoadingConfig,
    ) -> List[Chunk]:
        """调用 rerank 服务对初排片段重排序。

        本方法抛出的 VectorRetrievalError 一律由 retrieve() 捕获并转为
        rerank_degraded 降级标注，不会以节点失败形态外泄。
        """
        base_url = (rerank_model.base_url or "").strip()
        model_identifier = rerank_model.model_identifier
        if not base_url:
            raise VectorRetrievalError(
                _RERANK_INTERNAL_ERROR_CODE,
                f"rerank 模型服务地址未配置（模型标识：{model_identifier}），已降级为初排结果",
            )
        timeout_seconds = rerank_model.timeout_seconds or DEFAULT_TIMEOUT_SECONDS
        try:
            response = self._post_json(
                f"{base_url.rstrip('/')}/rerank",
                {
                    "model": model_identifier,
                    "query": query,
                    "chunks": [chunk.model_dump(mode="json") for chunk in chunks],
                },
                timeout_seconds,
            )
        except _HttpTransportError:
            raise VectorRetrievalError(
                _RERANK_INTERNAL_ERROR_CODE,
                f"rerank 重排序服务调用失败（模型标识：{model_identifier}），已降级为初排结果",
            ) from None
        return sanitize_chunk_payload(response.get("chunks"))

    # ------------------------------------------------------------------
    # HTTP seam（httpx 延迟导入单点）
    # ------------------------------------------------------------------

    @staticmethod
    def _post_json(
        url: str,
        payload: Dict[str, Any],
        timeout_seconds: int,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, JsonValue]:
        """POST JSON 并返回 dict 响应（httpx 延迟导入单点，单测注入 seam）。

        传输失败/非 2xx/响应非 JSON 对象 → _HttpTransportError（内部异常，
        不外泄 URL 与状态细节；调用方映射为固定文本的领域错误）。
        """
        import httpx

        try:
            response = httpx.post(
                url, json=payload, timeout=timeout_seconds, headers=headers
            )
        except httpx.HTTPError:
            raise _HttpTransportError("transport_error") from None
        if not 200 <= response.status_code < 300:
            raise _HttpTransportError(f"http_{response.status_code}")
        try:
            body = response.json()
        except ValueError:
            raise _HttpTransportError("invalid_response") from None
        if not isinstance(body, dict):
            raise _HttpTransportError("invalid_response")
        return body
