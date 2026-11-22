# =============================================================================
# 向量来源抽象与四来源实现（integrations/local_vector_database/embedding.py）
#
# 职责：EmbeddingProvider 抽象 + api/local_runtime/caller_supplied/
# hash_fallback 四来源 + “禁止伪造语义”出口守护（CHG-L07 / design DC-06、
# §2.3.2 组 3）。
# 设计意图（对应 local_vector_database design INV-L02 / INV-L08 / INV-L16、
# spec FR-30 / AC-03 / AC-32 / AC-39、OP-04）：
#   1. httpx/sentence_transformers 一律方法内延迟导入（无依赖环境导入
#      本模块不崩溃，INV-L02）；构造零副作用（不探测、不联网）；
#   2. api 来源复用既有 embedding_model.mode=api 契约（base_url/
#      model_identifier/timeout_seconds；密钥仅运行期注入，不落配置模型，
#      INV-D04）；POST {base_url}/embeddings，不新增服务端约定；
#   3. caller_supplied 为默认来源（组件只存不生成：embed 不伪造向量，
#      调用方须直供向量）；
#   4. hash_fallback 仅降级路径：128 维 SHA-256 确定性伪向量（借鉴
#      vector_index/_hash_vector 范式，独立实现、不跨层引用，INV-L16）；
#   5. 出口守护 enforce_degraded_annotation：provenance == hash_fallback
#      而未标降级 → InternalConsistencyError（哈希结果永不以真实语义
#      结果外泄，INV-L08 核心守护；非对外错误码契约）。
# =============================================================================

import hashlib
import importlib
import math
from abc import ABC, abstractmethod
from typing import Dict, List, Optional

from orchestration_platform.domain.models.common import JsonValue
from orchestration_platform.domain.models.node_configurations import ModelLoadingConfig

# provenance 取值域（DC-06；出口守护按 hash_fallback 判定）
PROVENANCE_API = "api"
PROVENANCE_LOCAL_RUNTIME = "local_runtime"
PROVENANCE_CALLER_SUPPLIED = "caller_supplied"
PROVENANCE_HASH_FALLBACK = "hash_fallback"

# 哈希兜底伪向量维度（spec §6.1：128 维）
_HASH_FALLBACK_DIM = 128


class EmbeddingProvider(ABC):
    """向量来源抽象（DC-06）：provenance / dimension / embed。"""

    provenance: str
    dimension: int

    @abstractmethod
    def embed(self, text: str) -> List[float]:
        """生成文本向量（真实来源或确定性降级；禁伪造语义，INV-L08）。"""


class InternalConsistencyError(Exception):
    """provenance 出口一致性守护异常（INV-L08 防误用；非对外错误码契约）。"""


def enforce_degraded_annotation(
    provenance: str,
    local_degraded: bool,
    local_degraded_reason: Optional[str],
) -> None:
    """“禁止伪造语义”出口守护（INV-L08 / FR-30 / AC-39）。

    provenance == hash_fallback 时出口组装必须 local_degraded=True 且
    reason 非空；违反 → InternalConsistencyError（防误用，保证哈希结果
    永不以真实语义结果外泄）。其他 provenance 不受影响（降级标注另有语义）。
    """
    if provenance != PROVENANCE_HASH_FALLBACK:
        return
    if not local_degraded:
        raise InternalConsistencyError(
            "provenance 为 hash_fallback 但未置 local_degraded=True"
            "（INV-L08：哈希伪向量禁止以真实语义结果外泄）"
        )
    if local_degraded_reason is None or not local_degraded_reason.strip():
        raise InternalConsistencyError(
            "provenance 为 hash_fallback 但 local_degraded_reason 为空"
            "（INV-L08：降级原因必须可诊断）"
        )


def _post_json(
    url: str,
    payload: Dict[str, JsonValue],
    headers: Dict[str, str],
    timeout_seconds: float,
) -> JsonValue:
    """POST JSON（httpx 方法内延迟导入；单测经 monkeypatch 本函数注入 seam）。"""
    httpx = importlib.import_module("httpx")
    response = httpx.post(url, json=payload, headers=headers, timeout=timeout_seconds)
    response.raise_for_status()
    return response.json()


class ExternalApiEmbeddingProvider(EmbeddingProvider):
    """API 来源：复用既有 embedding_model.mode=api 契约（DC-06 ①）。

    POST {base_url}/embeddings（OpenAI 兼容约定，不新增服务端约定）；
    密钥仅运行期注入（配置模型不含 api_key，INV-D04）；错误消息禁凭据。
    """

    provenance = PROVENANCE_API

    def __init__(
        self,
        model_config: ModelLoadingConfig,
        api_key: Optional[str] = None,
        dimension: int = 128,
    ) -> None:
        if model_config.mode != "api":
            raise ValueError("embedding_model.mode 须为 api（复用既有 api 契约）")
        if not model_config.base_url:
            raise ValueError("api 来源须声明 base_url（非凭据，服务地址）")
        self._model_config = model_config
        self._api_key = api_key
        self.dimension = dimension

    def embed(self, text: str) -> List[float]:
        payload: Dict[str, JsonValue] = {
            "model": self._model_config.model_identifier,
            "input": text,
        }
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        timeout = float(self._model_config.timeout_seconds or 30)
        data = _post_json(
            f"{self._model_config.base_url.rstrip('/')}/embeddings",
            payload,
            headers,
            timeout,
        )
        vector = self._extract_vector(data)
        if len(vector) != self.dimension:
            raise ValueError(
                "embedding 服务返回维度与声明不一致"
                f"（声明：{self.dimension}；实际：{len(vector)}）"
            )
        return vector

    @staticmethod
    def _extract_vector(data: JsonValue) -> List[float]:
        """解析 OpenAI 兼容响应 {"data":[{"embedding":[...]}]}；非法 → ValueError。"""
        if not isinstance(data, dict):
            raise ValueError("embedding 服务响应格式非法（非 JSON 对象）")
        items = data.get("data")
        if not isinstance(items, list) or not items:
            raise ValueError("embedding 服务响应缺少 data 数组")
        first = items[0]
        if not isinstance(first, dict):
            raise ValueError("embedding 服务响应 data[0] 非对象")
        raw_vector = first.get("embedding")
        if not isinstance(raw_vector, list) or not raw_vector:
            raise ValueError("embedding 服务响应缺少 embedding 向量")
        vector: List[float] = []
        for component in raw_vector:
            if isinstance(component, bool) or not isinstance(component, (int, float)):
                raise ValueError("embedding 服务响应含非数值分量")
            value = float(component)
            if not math.isfinite(value):
                raise ValueError("embedding 服务响应含非有限分量（NaN/Inf）")
            vector.append(value)
        return vector


class LocalRuntimeEmbeddingProvider(EmbeddingProvider):
    """本地运行时来源：sentence_transformers 方法内延迟导入（DC-06 ②）。

    依赖缺失 → ImportError 确定性抛出（由上层决定降级，不伪造语义）；
    构造零副作用（不导入、不加载模型）；模型实例构造后缓存复用。
    """

    provenance = PROVENANCE_LOCAL_RUNTIME

    def __init__(
        self,
        model_identifier: str,
        dimension: int = 128,
        device: str = "cpu",
    ) -> None:
        if not model_identifier:
            raise ValueError("model_identifier 须非空")
        self._model_identifier = model_identifier
        self.dimension = dimension
        self._device = device
        self._model: Optional[object] = None

    def embed(self, text: str) -> List[float]:
        if self._model is None:
            sentence_transformers = importlib.import_module("sentence_transformers")
            self._model = sentence_transformers.SentenceTransformer(
                self._model_identifier, device=self._device
            )
        raw_vector = self._model.encode(text)
        vector = [float(component) for component in list(raw_vector)]
        for value in vector:
            if not math.isfinite(value):
                raise ValueError("本地运行时向量含非有限分量（NaN/Inf）")
        if len(vector) != self.dimension:
            raise ValueError(
                "本地运行时向量维度与声明不一致"
                f"（声明：{self.dimension}；实际：{len(vector)}）"
            )
        return vector


class CallerSuppliedVectorProvider(EmbeddingProvider):
    """调用方直供来源（默认，DC-06 ③）：组件只存不生成。

    向量由调用方在写入/查询时直供；本来源不提供文本→向量生成
    （embed 明确拒绝，不以伪语义冒充，INV-L08）。
    """

    provenance = PROVENANCE_CALLER_SUPPLIED

    def __init__(self, dimension: int = 128) -> None:
        self.dimension = dimension

    def embed(self, text: str) -> List[float]:
        raise NotImplementedError(
            "caller_supplied 来源不生成向量：调用方须直供向量（组件只存不生成）"
        )


def _hash_embedding(text: str) -> List[float]:
    """确定性哈希伪向量（128 维 + L2 归一；仅降级/可测试性兜底，INV-L08）。"""
    vector = [0.0] * _HASH_FALLBACK_DIM
    if not text:
        return vector
    tokens = text.lower().split()
    if not tokens:
        tokens = [text]
    for token in tokens:
        for salt in range(4):
            digest = hashlib.sha256(f"{salt}:{token}".encode("utf-8")).digest()
            for i in range(0, 16, 2):
                index = (digest[i] + salt) % _HASH_FALLBACK_DIM
                vector[index] += ((digest[i + 1] % 100) / 100.0) - 0.5
    norm = math.sqrt(sum(value * value for value in vector))
    if norm > 0.0:
        vector = [value / norm for value in vector]
    return vector


class HashFallbackProvider(EmbeddingProvider):
    """哈希兜底来源（仅降级路径，DC-06 ④）：128 维确定性伪向量。

    独立实现（借鉴 vector_index/_hash_vector 范式，不跨层引用，INV-L16）；
    出口必须经 enforce_degraded_annotation 强制降级标注（INV-L08）。
    """

    provenance = PROVENANCE_HASH_FALLBACK
    dimension = _HASH_FALLBACK_DIM

    def embed(self, text: str) -> List[float]:
        return _hash_embedding(text)
