# =============================================================================
# 本地向量数据库向量来源单元测试（tests/unit/test_local_vector_database_embedding.py）
#
# 覆盖（CHG-L11 / design §9.1）：
#   - 抽象契约：EmbeddingProvider 不可实例化；构造零副作用（延迟导入，
#     INV-L02 / AC-19）
#   - 四来源 provenance 明确（api/local_runtime/caller_supplied/
#     hash_fallback，AC-32）
#   - api 来源：注入 _post_json 假 seam 验证请求契约（不访问外网）；
#     响应解析/非法响应/维度校验
#   - local_runtime 来源：缺失经 sys.modules None 哨兵强制确定性
#     ImportError；假运行时模块验证懒加载/缓存/校验
#   - hash_fallback：确定性 + 128 维 + L2 归一（降级路径确定性，INV-L16）
#   - “禁止伪造语义”出口守护：hash_fallback 必标降级，未标 → 内部一致性
#     错误（INV-L08 / AC-03 / AC-39）
#
# 沙箱验证：PYTHONPATH=src python -m pytest tests/unit/test_local_vector_database_embedding.py -q
# （同步 API 驱动，禁 asyncio.run；不访问外网；不依赖本机是否安装
#   sentence_transformers——缺失路径以 None 哨兵强制）
# =============================================================================

import math
import sys
import types

import pytest

from orchestration_platform.domain.models.node_configurations import ModelLoadingConfig
from orchestration_platform.integrations.local_vector_database import embedding
from orchestration_platform.integrations.local_vector_database.embedding import (
    PROVENANCE_API,
    PROVENANCE_CALLER_SUPPLIED,
    PROVENANCE_HASH_FALLBACK,
    PROVENANCE_LOCAL_RUNTIME,
    CallerSuppliedVectorProvider,
    EmbeddingProvider,
    ExternalApiEmbeddingProvider,
    HashFallbackProvider,
    InternalConsistencyError,
    LocalRuntimeEmbeddingProvider,
    enforce_degraded_annotation,
)


def _api_config(**overrides) -> ModelLoadingConfig:
    fields: dict = {"mode": "api", "model_identifier": "text-embed-test"}
    fields.update(overrides)
    return ModelLoadingConfig(**fields)


class _FakePostJson:
    """记录调用参数的 _post_json 假 seam（不访问外网）。"""

    def __init__(self, response=None, error=None):
        self.calls = []
        self._response = response
        self._error = error

    def __call__(self, url, payload, headers, timeout_seconds):
        self.calls.append(
            {
                "url": url,
                "payload": payload,
                "headers": dict(headers),
                "timeout": timeout_seconds,
            }
        )
        if self._error is not None:
            raise self._error
        return self._response


class TestEmbeddingProviderContract:
    """抽象契约与延迟导入（T4.1，INV-L02/AC-19）。"""

    def test_abstract_provider_cannot_instantiate(self):
        with pytest.raises(TypeError):
            EmbeddingProvider()  # type: ignore[abstract]

    def test_provider_construction_lazy_no_imports(self):
        # 构造零副作用：不触发 httpx/sentence_transformers 导入（INV-L02）
        names = ("httpx", "sentence_transformers")
        before = {name: name in sys.modules for name in names}
        ExternalApiEmbeddingProvider(
            _api_config(base_url="http://example.invalid"), dimension=3
        )
        LocalRuntimeEmbeddingProvider(model_identifier="mini-model", dimension=3)
        CallerSuppliedVectorProvider(dimension=3)
        HashFallbackProvider()
        after = {name: name in sys.modules for name in names}
        assert before == after


class TestCallerSuppliedVectorProvider:
    """默认来源：组件只存不生成（T4.1，DC-06 ③）。"""

    def test_provenance_and_dimension(self):
        provider = CallerSuppliedVectorProvider()
        assert provider.provenance == PROVENANCE_CALLER_SUPPLIED
        assert provider.dimension == 128
        custom = CallerSuppliedVectorProvider(dimension=384)
        assert custom.dimension == 384

    def test_embed_refuses_generation(self):
        # 只存不生成：embed 明确拒绝（不以伪语义冒充，INV-L08）
        provider = CallerSuppliedVectorProvider(dimension=3)
        with pytest.raises(NotImplementedError):
            provider.embed("任意文本")


class TestHashFallbackProvider:
    """哈希兜底（T4.1，DC-06 ④，INV-L16/AC-32）。"""

    def test_provenance_and_fixed_dimension(self):
        provider = HashFallbackProvider()
        assert provider.provenance == PROVENANCE_HASH_FALLBACK
        assert provider.dimension == 128
        assert len(provider.embed("文本 内容")) == 128

    def test_deterministic_same_text_same_vector(self):
        provider = HashFallbackProvider()
        first = provider.embed("报销 流程 说明")
        second = provider.embed("报销 流程 说明")
        assert first == second

    def test_different_text_different_vector(self):
        provider = HashFallbackProvider()
        assert provider.embed("报销 流程") != provider.embed("请假 流程")

    def test_l2_normalized(self):
        provider = HashFallbackProvider()
        vector = provider.embed("任意 文本 内容")
        norm = math.sqrt(sum(v * v for v in vector))
        assert norm == pytest.approx(1.0, abs=1e-9)

    def test_empty_text_zero_vector(self):
        vector = HashFallbackProvider().embed("")
        assert vector == [0.0] * 128


class TestExternalApiEmbeddingProvider:
    """api 来源：假 seam 验证（T4.1，DC-06 ①，不访问外网）。"""

    def test_constructor_validates_mode_and_base_url(self):
        with pytest.raises(ValueError):
            ExternalApiEmbeddingProvider(
                _api_config(mode="local", base_url="http://example.invalid"),
                dimension=3,
            )
        with pytest.raises(ValueError):
            ExternalApiEmbeddingProvider(_api_config(base_url=None), dimension=3)

    def test_embed_via_seam_request_contract(self, monkeypatch):
        # 请求契约：{base_url}/embeddings、model/input 载荷、Bearer 密钥、
        # 超时透传；httpx 导入状态不变（seam 替代真实调用，不访问外网）
        fake = _FakePostJson(response={"data": [{"embedding": [0.1, 0.2, 0.3]}]})
        monkeypatch.setattr(embedding, "_post_json", fake)
        httpx_imported_before = "httpx" in sys.modules
        provider = ExternalApiEmbeddingProvider(
            _api_config(base_url="http://example.invalid/v1", timeout_seconds=7),
            api_key="secret-key",
            dimension=3,
        )
        assert provider.provenance == PROVENANCE_API
        vector = provider.embed("报销流程")
        assert vector == [0.1, 0.2, 0.3]
        assert ("httpx" in sys.modules) == httpx_imported_before
        assert len(fake.calls) == 1
        call = fake.calls[0]
        assert call["url"] == "http://example.invalid/v1/embeddings"
        assert call["payload"] == {"model": "text-embed-test", "input": "报销流程"}
        assert call["headers"]["Content-Type"] == "application/json"
        assert call["headers"]["Authorization"] == "Bearer secret-key"
        assert call["timeout"] == 7.0

    def test_embed_without_api_key_omits_auth_header(self, monkeypatch):
        fake = _FakePostJson(response={"data": [{"embedding": [0.1, 0.2, 0.3]}]})
        monkeypatch.setattr(embedding, "_post_json", fake)
        provider = ExternalApiEmbeddingProvider(
            _api_config(base_url="http://example.invalid"), dimension=3
        )
        assert provider.embed("文本") == [0.1, 0.2, 0.3]
        assert "Authorization" not in fake.calls[0]["headers"]
        # 未配置 timeout_seconds → 默认 30s
        assert fake.calls[0]["timeout"] == 30.0

    @pytest.mark.parametrize(
        "response",
        [
            ["not", "a", "dict"],
            {},
            {"data": []},
            {"data": ["not-a-dict"]},
            {"data": [{}]},
            {"data": [{"embedding": []}]},
            {"data": [{"embedding": [0.1, float("nan")]}]},
            {"data": [{"embedding": [0.1, True]}]},
        ],
    )
    def test_embed_malformed_response_rejected(self, monkeypatch, response):
        monkeypatch.setattr(embedding, "_post_json", _FakePostJson(response=response))
        provider = ExternalApiEmbeddingProvider(
            _api_config(base_url="http://example.invalid"), dimension=2
        )
        with pytest.raises(ValueError):
            provider.embed("文本")

    def test_embed_dimension_mismatch_rejected(self, monkeypatch):
        fake = _FakePostJson(response={"data": [{"embedding": [0.1, 0.2, 0.3, 0.4]}]})
        monkeypatch.setattr(embedding, "_post_json", fake)
        provider = ExternalApiEmbeddingProvider(
            _api_config(base_url="http://example.invalid"), dimension=3
        )
        with pytest.raises(ValueError):
            provider.embed("文本")


class TestLocalRuntimeEmbeddingProvider:
    """本地运行时来源（T4.1，DC-06 ②）。"""

    def test_embed_missing_dependency_deterministic_import_error(self, monkeypatch):
        # 强制依赖缺失（本机可能装有 sentence_transformers → None 哨兵
        # 阻断导入，ImportError 确定性；由上层决定降级，不伪造语义）
        monkeypatch.setitem(sys.modules, "sentence_transformers", None)
        provider = LocalRuntimeEmbeddingProvider(model_identifier="mini-model", dimension=3)
        assert provider.provenance == PROVENANCE_LOCAL_RUNTIME
        with pytest.raises(ImportError):
            provider.embed("文本")

    def test_embed_with_fake_runtime_module(self, monkeypatch):
        created = []

        class FakeSentenceTransformer:
            def __init__(self, model_name, device=None):
                self.model_name = model_name
                self.device = device
                self.encode_calls = []
                created.append(self)

            def encode(self, text, **kwargs):
                self.encode_calls.append(text)
                return [0.1, 0.2, 0.3]

        fake_module = types.ModuleType("sentence_transformers")
        fake_module.SentenceTransformer = FakeSentenceTransformer
        monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)
        provider = LocalRuntimeEmbeddingProvider(
            model_identifier="mini-model", dimension=3, device="cpu"
        )
        vector = provider.embed("查询文本")
        assert vector == [0.1, 0.2, 0.3]
        # 模型仅构造一次（缓存复用）；标识/设备/原文透传
        assert len(created) == 1
        model = created[0]
        assert model.model_name == "mini-model"
        assert model.device == "cpu"
        assert model.encode_calls == ["查询文本"]
        provider.embed("第二次查询")
        assert len(created) == 1
        assert model.encode_calls == ["查询文本", "第二次查询"]

    def test_embed_fake_runtime_dimension_mismatch_rejected(self, monkeypatch):
        class FakeSentenceTransformer:
            def __init__(self, model_name, device=None):
                pass

            def encode(self, text, **kwargs):
                return [0.1, 0.2, 0.3, 0.4]

        fake_module = types.ModuleType("sentence_transformers")
        fake_module.SentenceTransformer = FakeSentenceTransformer
        monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)
        provider = LocalRuntimeEmbeddingProvider(model_identifier="m", dimension=3)
        with pytest.raises(ValueError):
            provider.embed("文本")

    def test_embed_fake_runtime_non_finite_rejected(self, monkeypatch):
        class FakeSentenceTransformer:
            def __init__(self, model_name, device=None):
                pass

            def encode(self, text, **kwargs):
                return [0.1, float("nan"), 0.3]

        fake_module = types.ModuleType("sentence_transformers")
        fake_module.SentenceTransformer = FakeSentenceTransformer
        monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)
        provider = LocalRuntimeEmbeddingProvider(model_identifier="m", dimension=3)
        with pytest.raises(ValueError):
            provider.embed("文本")


class TestProvenanceEnforcement:
    """“禁止伪造语义”出口守护（T4.2，INV-L08/AC-03/AC-32/AC-39）。"""

    def test_hash_fallback_without_degraded_flag_raises(self):
        with pytest.raises(InternalConsistencyError):
            enforce_degraded_annotation(
                PROVENANCE_HASH_FALLBACK, local_degraded=False, local_degraded_reason="原因"
            )

    @pytest.mark.parametrize("reason", [None, "", "   "])
    def test_hash_fallback_with_empty_reason_raises(self, reason):
        with pytest.raises(InternalConsistencyError):
            enforce_degraded_annotation(
                PROVENANCE_HASH_FALLBACK, local_degraded=True, local_degraded_reason=reason
            )

    def test_hash_fallback_with_annotation_passes(self):
        # 标注齐全 → 守护放行（降级可外泄，真实语义不可伪造）
        enforce_degraded_annotation(
            PROVENANCE_HASH_FALLBACK, local_degraded=True, local_degraded_reason="运行时缺失，哈希兜底"
        )

    @pytest.mark.parametrize(
        "provenance", [PROVENANCE_API, PROVENANCE_LOCAL_RUNTIME, PROVENANCE_CALLER_SUPPLIED]
    )
    def test_non_hash_provenance_passes(self, provenance):
        # 非哈希来源不受强制降级约束（降级标注另有语义）
        enforce_degraded_annotation(
            provenance, local_degraded=False, local_degraded_reason=None
        )
