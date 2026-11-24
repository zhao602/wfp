# =============================================================================
# 向量知识库节点单元测试（tests/unit/test_vector_knowledge_base_node_executor.py）
#
# 按 tasks.md 批次增量编写（design §4.1.1 用例组）：
#   批次 1（CHG-01/CHG-02）：配置模型解析组
#     - 最小合法配置解析（判别联合命中、默认值 top_k=5 / query_mode=manual / rerank=None）
#     - top_k 越界（0/1000）/ 非整数 / 边界值（1/100）
#     - embedding_model 缺失、query_value 长度上限、timeout_seconds 下界
#     - ModelLoadingConfig 无 api_key 字段（INV-D04 / SV-3）
#     - 既有配置零漂移锚点（INV-D03）
#   批次 2 追加（CHG-03/CHG-04/CHG-08 提前）：执行器行为组 + 检索集成层组
#     - 执行器：query 互斥分支（AC-13/14/15/20）、top_k 截断（AC-21/24）、
#       空结果合法成功（AC-39）、降级可见（AC-27/31）、检索失败判失败
#       （INV-D07）、零直写（INV-D08）、local 确定性（AC-30/31）
#     - 集成层：净化确定性（截断/夹取/丢弃）、HTTP 三段流（传输/非 2xx/
#       rerank 降级）、凭据隔离（SV-3）
#   批次 3 追加（CHG-05/06/07/09）：引用登记组（T3.1）/ 加载期校验组（T3.2，AC-17 三元组）/
#     引用环组（AC-18，拓扑校验器零改动）/ 注册表接入组（T3.4，AC-34）；
#   批次 5 补全：YAML 回归 / langgraph 隔离 / 凭据隔离。
#
# 沙箱验证：PYTHONPATH=src python -m pytest tests/unit/test_vector_knowledge_base_node_executor.py -q
# =============================================================================

import asyncio
import copy

import httpx
import pytest
from pydantic import TypeAdapter, ValidationError

from orchestration_platform.configuration import settings as settings_module
from orchestration_platform.domain.errors import (
    NODE_INPUT_REFERENCE_MISSING,
    NODE_VECTOR_EMBEDDING_UNAVAILABLE,
    NODE_VECTOR_LOCAL_RUNTIME_MISSING,
    NODE_VECTOR_RETRIEVAL_UNAVAILABLE,
    WF_CYCLIC_GRAPH,
    WF_VALIDATION_FAILED,
)
from orchestration_platform.domain.models.edge_definition import EdgeDefinition
from orchestration_platform.domain.models.enums import NodeType, Severity
from orchestration_platform.domain.models.node_configurations import (
    ModelLoadingConfig,
    NodeConfiguration,
    OutputDisplayNodeConfiguration,
    VectorKnowledgeBaseNodeConfiguration,
)
from orchestration_platform.domain.models.node_definition import NodeDefinition
from orchestration_platform.domain.models.shared_state import SharedState
from orchestration_platform.domain.models.workflow_definition import WorkflowDefinition
from orchestration_platform.domain.validation.graph_topology_validator import (
    GraphTopologyValidator,
)
from orchestration_platform.domain.validation.node_configuration_validator import (
    NodeConfigurationValidator,
    collect_input_references,
)
from orchestration_platform.engine.nodes.base import (
    NodeExecutionContext,
)
from orchestration_platform.engine.nodes.vector_knowledge_base_node_executor import (
    VectorKnowledgeBaseNodeExecutor,
)
from orchestration_platform.integrations.vector_knowledge_base import local_backend as local_backend_module
from orchestration_platform.integrations.vector_knowledge_base import retriever as retriever_module
from orchestration_platform.integrations.vector_knowledge_base.local_backend import (
    LocalKnowledgeBaseRetriever,
)
from orchestration_platform.integrations.vector_knowledge_base.models import (
    CHUNK_TEXT_MAX_LENGTH,
    Chunk,
    RetrievalOutcome,
    VectorRetrievalError,
    sanitize_chunk_payload,
)
from orchestration_platform.integrations.vector_knowledge_base.retriever import (
    DEFAULT_TIMEOUT_SECONDS,
    HttpKnowledgeBaseRetriever,
    VectorKnowledgeBaseRetriever,
)

# 判别联合适配器：与 NodeDefinition.configuration 同一解析路径
_UNION_ADAPTER: TypeAdapter = TypeAdapter(NodeConfiguration)

# 最小合法 embedding 模型配置（api 模式）
_MINIMAL_EMBEDDING = {
    "mode": "api",
    "model_identifier": "text-embedding-v3",
}


# ---------------------------------------------------------------------------
# 批次 1：ModelLoadingConfig（embedding/rerank 同构子结构）
# ---------------------------------------------------------------------------

class TestModelLoadingConfig:
    """ModelLoadingConfig 字段约束验证（design §2.2.1）。"""

    def test_minimal_api_mode_defaults(self):
        config = ModelLoadingConfig.model_validate(_MINIMAL_EMBEDDING)
        assert config.mode == "api"
        assert config.model_identifier == "text-embedding-v3"
        assert config.base_url is None
        assert config.timeout_seconds is None
        assert config.model_path is None
        assert config.device == "cpu"
        assert config.allow_degraded_fallback is False

    def test_local_mode_fields(self):
        config = ModelLoadingConfig.model_validate(
            {
                "mode": "local",
                "model_identifier": "bge-small-zh",
                "model_path": "./models/bge-small-zh",
                "device": "gpu",
                "allow_degraded_fallback": True,
            }
        )
        assert config.mode == "local"
        assert config.device == "gpu"
        assert config.allow_degraded_fallback is True

    def test_no_api_key_field(self):
        # INV-D04 / SV-3：配置模型严禁 api_key 字段（密钥来源为运行期 settings）
        assert "api_key" not in ModelLoadingConfig.model_fields

    def test_mode_invalid_fails(self):
        with pytest.raises(ValidationError):
            ModelLoadingConfig.model_validate({**_MINIMAL_EMBEDDING, "mode": "remote"})

    def test_model_identifier_missing_fails(self):
        with pytest.raises(ValidationError):
            ModelLoadingConfig.model_validate({"mode": "api"})

    def test_timeout_seconds_zero_fails(self):
        with pytest.raises(ValidationError):
            ModelLoadingConfig.model_validate({**_MINIMAL_EMBEDDING, "timeout_seconds": 0})

    def test_device_invalid_fails(self):
        with pytest.raises(ValidationError):
            ModelLoadingConfig.model_validate({**_MINIMAL_EMBEDDING, "device": "tpu"})


# ---------------------------------------------------------------------------
# 批次 1：VectorKnowledgeBaseNodeConfiguration 配置解析
# ---------------------------------------------------------------------------

class TestVectorKnowledgeBaseConfigParsing:
    """配置模型解析验证（design §2.2.1 / §4.1.1 配置模型解析组）。"""

    def test_minimal_config_defaults(self):
        config = VectorKnowledgeBaseNodeConfiguration.model_validate(
            {
                "knowledge_base_id": "hr-policy-2026",
                "query_value": "如何申请报销",
                "embedding_model": _MINIMAL_EMBEDDING,
            }
        )
        assert config.node_type == NodeType.VECTOR_KNOWLEDGE_BASE
        assert config.node_type.value == "vector_knowledge_base"
        assert config.knowledge_base_id == "hr-policy-2026"
        assert config.query_mode == "manual"
        assert config.query_value == "如何申请报销"
        assert config.query_reference is None
        assert config.top_k == 5
        assert isinstance(config.embedding_model, ModelLoadingConfig)
        assert config.rerank_model is None

    def test_discriminated_union_hits_vector_kb(self):
        config = _UNION_ADAPTER.validate_python(
            {
                "node_type": "vector_knowledge_base",
                "knowledge_base_id": "kb-1",
                "query_value": "查询文本",
                "embedding_model": _MINIMAL_EMBEDDING,
            }
        )
        assert isinstance(config, VectorKnowledgeBaseNodeConfiguration)
        assert config.top_k == 5

    def test_node_definition_injects_node_type(self):
        # NodeDefinition 解析前注入顶层 node_type 到 configuration（真实 YAML 路径）
        node = NodeDefinition.model_validate(
            {
                "node_id": "node-vkb-1",
                "node_type": "vector_knowledge_base",
                "output_name": "检索结果",
                "configuration": {
                    "knowledge_base_id": "kb-1",
                    "query_value": "如何申请报销",
                    "embedding_model": _MINIMAL_EMBEDDING,
                },
            }
        )
        assert isinstance(node.configuration, VectorKnowledgeBaseNodeConfiguration)
        assert node.effective_output_name == "检索结果"

    def test_top_k_zero_fails(self):
        with pytest.raises(ValidationError):
            VectorKnowledgeBaseNodeConfiguration.model_validate(
                {
                    "knowledge_base_id": "kb-1",
                    "query_value": "q",
                    "top_k": 0,
                    "embedding_model": _MINIMAL_EMBEDDING,
                }
            )

    def test_top_k_over_100_fails(self):
        with pytest.raises(ValidationError):
            VectorKnowledgeBaseNodeConfiguration.model_validate(
                {
                    "knowledge_base_id": "kb-1",
                    "query_value": "q",
                    "top_k": 1000,
                    "embedding_model": _MINIMAL_EMBEDDING,
                }
            )

    def test_top_k_non_integer_fails(self):
        with pytest.raises(ValidationError):
            VectorKnowledgeBaseNodeConfiguration.model_validate(
                {
                    "knowledge_base_id": "kb-1",
                    "query_value": "q",
                    "top_k": 5.5,
                    "embedding_model": _MINIMAL_EMBEDDING,
                }
            )

    def test_top_k_boundaries_ok(self):
        for boundary in (1, 100):
            config = VectorKnowledgeBaseNodeConfiguration.model_validate(
                {
                    "knowledge_base_id": "kb-1",
                    "query_value": "q",
                    "top_k": boundary,
                    "embedding_model": _MINIMAL_EMBEDDING,
                }
            )
            assert config.top_k == boundary

    def test_embedding_model_missing_fails(self):
        # CHG-14（AC-50）：embedding_model 由必填改可选（保留字段名/类型）——
        # 缺省解析成功且为 None（旧断言"缺失报错"随必填契约退役，DR-6）。
        config = VectorKnowledgeBaseNodeConfiguration.model_validate(
            {"knowledge_base_id": "kb-1", "query_value": "q"}
        )
        assert config.embedding_model is None

    def test_query_value_over_2000_chars_fails(self):
        with pytest.raises(ValidationError):
            VectorKnowledgeBaseNodeConfiguration.model_validate(
                {
                    "knowledge_base_id": "kb-1",
                    "query_value": "x" * 2001,
                    "embedding_model": _MINIMAL_EMBEDDING,
                }
            )

    def test_query_value_exactly_2000_chars_ok(self):
        config = VectorKnowledgeBaseNodeConfiguration.model_validate(
            {
                "knowledge_base_id": "kb-1",
                "query_value": "x" * 2000,
                "embedding_model": _MINIMAL_EMBEDDING,
            }
        )
        assert len(config.query_value or "") == 2000

    def test_rerank_model_parsed(self):
        config = VectorKnowledgeBaseNodeConfiguration.model_validate(
            {
                "knowledge_base_id": "kb-1",
                "query_value": "q",
                "embedding_model": _MINIMAL_EMBEDDING,
                "rerank_model": {
                    "mode": "api",
                    "model_identifier": "bge-reranker-v2",
                    "timeout_seconds": 30,
                },
            }
        )
        assert isinstance(config.rerank_model, ModelLoadingConfig)
        assert config.rerank_model.model_identifier == "bge-reranker-v2"
        assert config.rerank_model.timeout_seconds == 30

    def test_knowledge_base_id_default_empty_parses(self):
        # 知识库标识非空拦截发生在加载期校验（T3.2 / design §2.2.5），
        # 模型层默认空串可解析（INV-D03：前端默认配置导出后仍可进入加载期校验报告）
        config = VectorKnowledgeBaseNodeConfiguration.model_validate(
            {"knowledge_base_id": "", "query_value": "q", "embedding_model": _MINIMAL_EMBEDDING}
        )
        assert config.knowledge_base_id == ""

    def test_query_reference_mode_parses(self):
        config = VectorKnowledgeBaseNodeConfiguration.model_validate(
            {
                "knowledge_base_id": "kb-1",
                "query_mode": "reference",
                "query_reference": "上游输出.query",
                "embedding_model": _MINIMAL_EMBEDDING,
            }
        )
        assert config.query_mode == "reference"
        assert config.query_reference == "上游输出.query"


# ---------------------------------------------------------------------------
# 批次 1：既有配置零漂移锚点（INV-D03）
# ---------------------------------------------------------------------------

class TestExistingConfigurationRegression:
    """既有配置类解析零漂移锚点。"""

    def test_output_display_defaults_unchanged(self):
        config = OutputDisplayNodeConfiguration()
        assert config.node_type == NodeType.OUTPUT_DISPLAY
        assert config.input_reference is None
        assert config.display_title is None

    def test_existing_union_member_still_hits(self):
        config = _UNION_ADAPTER.validate_python(
            {
                "node_type": "output_display",
                "input_reference": "上游结果",
                "display_title": "展示",
            }
        )
        assert isinstance(config, OutputDisplayNodeConfiguration)


# ---------------------------------------------------------------------------
# 批次 2：测试基建（桩检索器 / 上下文构造 / HTTP 假件）
# ---------------------------------------------------------------------------


def _api_embedding(**overrides):
    """api 模式 embedding 配置（带默认服务地址）。"""
    payload = {
        "mode": "api",
        "model_identifier": "text-embedding-v3",
        "base_url": "http://emb.example",
    }
    payload.update(overrides)
    return ModelLoadingConfig.model_validate(payload)


def _api_rerank(**overrides):
    payload = {
        "mode": "api",
        "model_identifier": "bge-reranker-v2",
        "base_url": "http://rerank.example",
    }
    payload.update(overrides)
    return ModelLoadingConfig.model_validate(payload)


def _make_chunks(count, prefix="片段"):
    return [
        Chunk(text=f"{prefix}{index}", score=round(0.9 - index * 0.01, 4), source={"kb": "hr"})
        for index in range(count)
    ]


class _StubRetriever(VectorKnowledgeBaseRetriever):
    """记录调用参数的桩检索器（可注入结果或异常）。"""

    def __init__(self, outcome=None, error=None):
        self.calls = []
        self._outcome = outcome if outcome is not None else RetrievalOutcome(chunks=[])
        self._error = error

    def retrieve(
        self,
        knowledge_base_id,
        query,
        top_k,
        embedding_model,
        rerank_model,
    ):
        self.calls.append(
            {
                "knowledge_base_id": knowledge_base_id,
                "query": query,
                "top_k": top_k,
                "embedding_model": embedding_model,
                "rerank_model": rerank_model,
            }
        )
        if self._error is not None:
            raise self._error
        return self._outcome


def _manual_config(**overrides):
    """manual 模式节点配置字典。"""
    payload = {
        "knowledge_base_id": "kb-1",
        "query_mode": "manual",
        "query_value": "如何申请报销",
        "top_k": 5,
        "embedding_model": {
            "mode": "api",
            "model_identifier": "text-embedding-v3",
            "base_url": "http://emb.example",
        },
    }
    payload.update(overrides)
    return payload


def _make_context(config_payload, node_outputs=None):
    """构造执行上下文（真实 NodeDefinition 解析路径）。"""
    node = NodeDefinition.model_validate(
        {
            "node_id": "node-vkb-1",
            "node_type": "vector_knowledge_base",
            "output_name": "检索结果",
            "configuration": config_payload,
        }
    )
    return NodeExecutionContext(
        node_definition=node,
        shared_state=SharedState(
            instance_id="inst-1",
            node_outputs=node_outputs if node_outputs is not None else {},
        ),
        instance_id="inst-1",
        trace_id="trace-1",
    )


class _FakeResponse:
    """httpx.Response 假件（仅覆盖本模块用到的接口）。"""

    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body if body is not None else {}

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def _install_fake_httpx(monkeypatch, responder):
    """替换 httpx.post 为路由假件，返回调用记录列表。"""
    calls = []

    def fake_post(url, json=None, timeout=None, headers=None):
        calls.append({"url": url, "json": json, "timeout": timeout, "headers": headers})
        result = responder(url, json)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(httpx, "post", fake_post)
    return calls


def _route_responder(routes):
    """按 URL 后缀路由响应：{后缀: _FakeResponse 或 Exception}。"""

    def responder(url, payload):
        for suffix, result in routes.items():
            if url.endswith(suffix):
                return result
        raise AssertionError(f"意外请求地址：{url}")

    return responder


def _patch_settings(monkeypatch, values):
    """容错读取假件：按字典返回 settings 值（缺省回退 default）。"""

    def fake_read_setting(name, default):
        return values.get(name, default)

    monkeypatch.setattr(retriever_module, "_read_setting", fake_read_setting)


def _settings_without_local_engine(monkeypatch):
    """密封化：屏蔽根 .env 的 LOCAL_VECTOR_DATABASE_ENABLED=true 泄漏。

    引擎委托路径（B5）由 test_local_vector_database_backend_integration.py
    专项覆盖；本文件守护三态语义逐字不变（INV-L04/AC-30/AC-31/OP-10）。
    """

    class _SettingsNoLocalEngine:
        pass

    monkeypatch.setattr(
        settings_module, "get_settings", lambda: _SettingsNoLocalEngine()
    )


# ---------------------------------------------------------------------------
# 批次 2：sanitize_chunk_payload 净化确定性（design §2.2.4 / OP-08）
# ---------------------------------------------------------------------------


class TestSanitizeChunkPayload:
    """外部载荷净化规则：非法结构不抛异常、不伪造语义。"""

    def test_non_list_returns_empty(self):
        for invalid in (None, "text", 42, {"chunks": []}):
            assert sanitize_chunk_payload(invalid) == []

    def test_drops_invalid_items(self):
        items = [None, 42, "text", {}, {"text": ""}, {"text": 123}]
        assert sanitize_chunk_payload(items) == []

    def test_truncates_long_text_and_annotates(self):
        items = [{"text": "x" * (CHUNK_TEXT_MAX_LENGTH + 500), "score": 0.8}]
        chunks = sanitize_chunk_payload(items)
        assert len(chunks) == 1
        assert len(chunks[0].text) == CHUNK_TEXT_MAX_LENGTH
        assert chunks[0].source["truncated"] is True

    def test_truncation_preserves_existing_source(self):
        items = [
            {
                "text": "y" * (CHUNK_TEXT_MAX_LENGTH + 1),
                "score": 0.5,
                "source": {"doc": "policy-01", "truncated": False},
            }
        ]
        chunks = sanitize_chunk_payload(items)
        assert chunks[0].source["doc"] == "policy-01"
        assert chunks[0].source["truncated"] is True

    def test_score_clamp_and_coercion(self):
        items = [
            {"text": "a", "score": 1.7},
            {"text": "b", "score": -0.2},
            {"text": "c", "score": "0.9"},
            {"text": "d", "score": "not-a-number"},
            {"text": "e", "score": None},
            {"text": "f", "score": float("nan")},
        ]
        scores = [chunk.score for chunk in sanitize_chunk_payload(items)]
        assert scores == [1.0, 0.0, 0.9, 0.0, 0.0, 0.0]

    def test_source_non_dict_cleared(self):
        chunks = sanitize_chunk_payload([{"text": "a", "source": "meta"}])
        assert chunks[0].source is None

    def test_valid_chunk_roundtrip(self):
        items = [{"text": "片段", "score": 0.75, "source": {"kb": "hr"}}]
        chunks = sanitize_chunk_payload(items)
        assert chunks[0].text == "片段"
        assert chunks[0].score == 0.75
        assert chunks[0].source == {"kb": "hr"}

    def test_never_raises_on_exotic_payload(self):
        # 契约：任何输入都不抛异常；不可强转的 source 值触发模型校验失败 → 丢弃该条
        result = sanitize_chunk_payload([{"text": "ok", "source": {"weird": object()}}])
        assert result == []
        # score 非法对象 → 宽容归一为 0.0（有效片段不丢弃，见 _coerce_score）
        kept = sanitize_chunk_payload([{"text": "ok2", "score": object()}])
        assert len(kept) == 1
        assert kept[0].score == 0.0
        assert kept[0].source is None


# ---------------------------------------------------------------------------
# 批次 2：HttpKnowledgeBaseRetriever 三段调用流（design §2.2.4 / §2.2.5）
# ---------------------------------------------------------------------------


class TestHttpKnowledgeBaseRetriever:
    """API 模式检索器：embedding → 检索 → rerank（失败降级）。"""

    _EMBED_OK = _FakeResponse(body={"data": [{"embedding": [0.1, 0.2, 0.3]}]})
    _SEARCH_OK = _FakeResponse(
        body={"chunks": [{"text": "片段A", "score": 0.9}, {"text": "片段B", "score": 0.7}]}
    )
    _RERANK_OK = _FakeResponse(
        body={"chunks": [{"text": "片段B", "score": 0.95}, {"text": "片段A", "score": 0.9}]}
    )

    def test_embed_base_url_missing_fails(self):
        retriever = HttpKnowledgeBaseRetriever()
        with pytest.raises(VectorRetrievalError) as exc_info:
            retriever.retrieve("kb-1", "查询", 5, _api_embedding(base_url=None), None)
        assert exc_info.value.error_code == NODE_VECTOR_EMBEDDING_UNAVAILABLE
        assert "text-embedding-v3" in exc_info.value.error_message

    def test_embed_transport_error_maps_unavailable(self, monkeypatch):
        monkeypatch.setattr(
            httpx, "post", lambda *args, **kwargs: (_ for _ in ()).throw(httpx.ConnectError("boom"))
        )
        retriever = HttpKnowledgeBaseRetriever()
        with pytest.raises(VectorRetrievalError) as exc_info:
            retriever.retrieve("kb-1", "查询", 5, _api_embedding(), None)
        assert exc_info.value.error_code == NODE_VECTOR_EMBEDDING_UNAVAILABLE

    def test_embed_non_2xx_maps_unavailable(self, monkeypatch):
        calls = _install_fake_httpx(
            monkeypatch, _route_responder({"/embeddings": _FakeResponse(status_code=503)})
        )
        retriever = HttpKnowledgeBaseRetriever()
        with pytest.raises(VectorRetrievalError) as exc_info:
            retriever.retrieve("kb-1", "查询", 5, _api_embedding(), None)
        assert exc_info.value.error_code == NODE_VECTOR_EMBEDDING_UNAVAILABLE
        assert len(calls) == 1

    def test_embed_invalid_response_shape_fails(self, monkeypatch):
        for body in ({"data": []}, {"data": [{"embedding": []}]}, {"unexpected": True}):
            calls = _install_fake_httpx(
                monkeypatch, _route_responder({"/embeddings": _FakeResponse(body=body)})
            )
            retriever = HttpKnowledgeBaseRetriever()
            with pytest.raises(VectorRetrievalError) as exc_info:
                retriever.retrieve("kb-1", "查询", 5, _api_embedding(), None)
            assert exc_info.value.error_code == NODE_VECTOR_EMBEDDING_UNAVAILABLE
            assert len(calls) == 1

    def test_payloads_and_vector_flow(self, monkeypatch):
        _patch_settings(
            monkeypatch,
            {
                "vector_kb_search_base_url": "http://kb.example",
                "vector_kb_search_timeout_seconds": 15,
            },
        )
        calls = _install_fake_httpx(
            monkeypatch,
            _route_responder({"/embeddings": self._EMBED_OK, "/search": self._SEARCH_OK}),
        )
        outcome = HttpKnowledgeBaseRetriever().retrieve(
            "kb-1", "查询文本", 7, _api_embedding(timeout_seconds=12), None
        )
        assert calls[0]["json"] == {"model": "text-embedding-v3", "input": "查询文本"}
        assert calls[0]["timeout"] == 12
        assert calls[1]["json"] == {
            "knowledge_base_id": "kb-1",
            "query": "查询文本",
            "query_vector": [0.1, 0.2, 0.3],
            "top_k": 7,
        }
        assert calls[1]["timeout"] == 15
        assert [chunk.text for chunk in outcome.chunks] == ["片段A", "片段B"]

    def test_search_base_url_missing_fails(self, monkeypatch):
        _patch_settings(monkeypatch, {})
        _install_fake_httpx(
            monkeypatch, _route_responder({"/embeddings": self._EMBED_OK})
        )
        retriever = HttpKnowledgeBaseRetriever()
        with pytest.raises(VectorRetrievalError) as exc_info:
            retriever.retrieve("kb-9", "查询", 5, _api_embedding(), None)
        assert exc_info.value.error_code == NODE_VECTOR_RETRIEVAL_UNAVAILABLE
        assert "kb-9" in exc_info.value.error_message

    def test_search_transport_error_maps_unavailable(self, monkeypatch):
        _patch_settings(monkeypatch, {"vector_kb_search_base_url": "http://kb.example"})
        _install_fake_httpx(
            monkeypatch,
            _route_responder(
                {"/embeddings": self._EMBED_OK, "/search": httpx.ConnectError("boom")}
            ),
        )
        retriever = HttpKnowledgeBaseRetriever()
        with pytest.raises(VectorRetrievalError) as exc_info:
            retriever.retrieve("kb-1", "查询", 5, _api_embedding(), None)
        assert exc_info.value.error_code == NODE_VECTOR_RETRIEVAL_UNAVAILABLE

    def test_full_flow_with_rerank_success(self, monkeypatch):
        _patch_settings(monkeypatch, {"vector_kb_search_base_url": "http://kb.example"})
        calls = _install_fake_httpx(
            monkeypatch,
            _route_responder(
                {
                    "/embeddings": self._EMBED_OK,
                    "/search": self._SEARCH_OK,
                    "/rerank": self._RERANK_OK,
                }
            ),
        )
        outcome = HttpKnowledgeBaseRetriever().retrieve(
            "kb-1", "查询", 5, _api_embedding(), _api_rerank()
        )
        assert [chunk.text for chunk in outcome.chunks] == ["片段B", "片段A"]
        assert outcome.rerank_degraded is False
        assert len(calls) == 3

    def test_rerank_none_skips_rerank_call(self, monkeypatch):
        # AC-28：rerank 未配置即跳过，合法成功、无降级标注
        _patch_settings(monkeypatch, {"vector_kb_search_base_url": "http://kb.example"})
        calls = _install_fake_httpx(
            monkeypatch,
            _route_responder({"/embeddings": self._EMBED_OK, "/search": self._SEARCH_OK}),
        )
        outcome = HttpKnowledgeBaseRetriever().retrieve(
            "kb-1", "查询", 5, _api_embedding(), None
        )
        assert len(calls) == 2
        assert outcome.rerank_degraded is False
        assert outcome.chunks[0].text == "片段A"

    def test_rerank_failure_degrades_to_initial_order(self, monkeypatch):
        # AC-27：rerank 失败降级初排 + 标注，不判失败
        _patch_settings(monkeypatch, {"vector_kb_search_base_url": "http://kb.example"})
        _install_fake_httpx(
            monkeypatch,
            _route_responder(
                {
                    "/embeddings": self._EMBED_OK,
                    "/search": self._SEARCH_OK,
                    "/rerank": httpx.ConnectError("boom"),
                }
            ),
        )
        outcome = HttpKnowledgeBaseRetriever().retrieve(
            "kb-1", "查询", 5, _api_embedding(), _api_rerank()
        )
        assert [chunk.text for chunk in outcome.chunks] == ["片段A", "片段B"]
        assert outcome.rerank_degraded is True
        assert outcome.rerank_degraded_reason

    def test_rerank_base_url_missing_degrades(self, monkeypatch):
        _patch_settings(monkeypatch, {"vector_kb_search_base_url": "http://kb.example"})
        _install_fake_httpx(
            monkeypatch,
            _route_responder({"/embeddings": self._EMBED_OK, "/search": self._SEARCH_OK}),
        )
        outcome = HttpKnowledgeBaseRetriever().retrieve(
            "kb-1", "查询", 5, _api_embedding(), _api_rerank(base_url=None)
        )
        assert outcome.rerank_degraded is True
        assert "未配置" in (outcome.rerank_degraded_reason or "")

    def test_search_auth_header_from_settings(self, monkeypatch):
        # 密钥来自运行期 settings（INV-D04：配置不携带密钥）
        _patch_settings(
            monkeypatch,
            {
                "vector_kb_search_base_url": "http://kb.example",
                "vector_kb_search_api_key": "test-key",
            },
        )
        calls = _install_fake_httpx(
            monkeypatch,
            _route_responder({"/embeddings": self._EMBED_OK, "/search": self._SEARCH_OK}),
        )
        HttpKnowledgeBaseRetriever().retrieve("kb-1", "查询", 5, _api_embedding(), None)
        assert calls[1]["headers"]["Authorization"] == "Bearer test-key"

    def test_search_no_auth_header_without_key(self, monkeypatch):
        _patch_settings(monkeypatch, {"vector_kb_search_base_url": "http://kb.example"})
        calls = _install_fake_httpx(
            monkeypatch,
            _route_responder({"/embeddings": self._EMBED_OK, "/search": self._SEARCH_OK}),
        )
        HttpKnowledgeBaseRetriever().retrieve("kb-1", "查询", 5, _api_embedding(), None)
        assert not calls[1]["headers"]

    def test_default_timeout_applied_when_unset(self, monkeypatch):
        _patch_settings(monkeypatch, {"vector_kb_search_base_url": "http://kb.example"})
        calls = _install_fake_httpx(
            monkeypatch,
            _route_responder({"/embeddings": self._EMBED_OK, "/search": self._SEARCH_OK}),
        )
        HttpKnowledgeBaseRetriever().retrieve(
            "kb-1", "查询", 5, _api_embedding(timeout_seconds=None), None
        )
        assert calls[0]["timeout"] == DEFAULT_TIMEOUT_SECONDS
        assert calls[1]["timeout"] == DEFAULT_TIMEOUT_SECONDS

    def test_error_messages_credential_free(self, monkeypatch):
        # SV-3：错误消息严禁包含服务地址/密钥/原始响应
        _patch_settings(
            monkeypatch,
            {
                "vector_kb_search_base_url": "http://kb-secret-host:9200",
                "vector_kb_search_api_key": "sk-secret-value",
            },
        )
        monkeypatch.setattr(
            httpx, "post", lambda *args, **kwargs: (_ for _ in ()).throw(httpx.ConnectError("boom"))
        )
        retriever = HttpKnowledgeBaseRetriever()
        with pytest.raises(VectorRetrievalError) as exc_info:
            retriever.retrieve(
                "kb-1", "查询", 5, _api_embedding(base_url="http://emb-secret:8000"), None
            )
        message = exc_info.value.error_message
        assert "emb-secret" not in message
        assert "kb-secret-host" not in message
        assert "sk-secret-value" not in message


# ---------------------------------------------------------------------------
# 批次 2：LocalKnowledgeBaseRetriever 确定性降级（design §2.2.5 / FR-17 / OP-10）
# ---------------------------------------------------------------------------


class TestLocalKnowledgeBaseRetriever:
    """local 模式：运行时探测 + 确定性降级（不伪造检索语义）。"""

    def test_runtime_missing_fail_fast(self, monkeypatch):
        # AC-30：依赖缺失且未开兜底 → 确定性失败，不崩溃
        _settings_without_local_engine(monkeypatch)
        retriever = LocalKnowledgeBaseRetriever()
        retriever._local_runtime_ready = False
        embedding = ModelLoadingConfig.model_validate(
            {"mode": "local", "model_identifier": "bge-small-zh"}
        )
        with pytest.raises(VectorRetrievalError) as exc_info:
            retriever.retrieve("kb-1", "查询", 5, embedding, None)
        assert exc_info.value.error_code == NODE_VECTOR_LOCAL_RUNTIME_MISSING
        assert "bge-small-zh" in exc_info.value.error_message
        assert "allow_degraded_fallback" in exc_info.value.error_message

    def test_runtime_missing_fallback_degrades(self, monkeypatch):
        # FR-17/AC-31：兜底降级 → 空片段 + 显式标注，不伪造语义
        _settings_without_local_engine(monkeypatch)
        retriever = LocalKnowledgeBaseRetriever()
        retriever._local_runtime_ready = False
        embedding = ModelLoadingConfig.model_validate(
            {"mode": "local", "model_identifier": "bge-small-zh", "allow_degraded_fallback": True}
        )
        outcome = retriever.retrieve("kb-1", "查询", 5, embedding, None)
        assert outcome.chunks == []
        assert outcome.local_degraded is True
        assert "哈希伪向量" in (outcome.local_degraded_reason or "")

    def test_runtime_ready_degrades_empty_no_fabrication(self, monkeypatch):
        # OP-10：运行时就绪但本期未实现推理 → 空结果降级，静默伪造 = 违规
        _settings_without_local_engine(monkeypatch)
        retriever = LocalKnowledgeBaseRetriever()
        retriever._local_runtime_ready = True
        embedding = ModelLoadingConfig.model_validate(
            {"mode": "local", "model_identifier": "bge-small-zh"}
        )
        outcome = retriever.retrieve("kb-1", "查询", 5, embedding, None)
        assert outcome.chunks == []
        assert outcome.local_degraded is True
        assert "未实现真实本地推理" in (outcome.local_degraded_reason or "")

    def test_runtime_probe_import_success(self, monkeypatch):
        imported = []

        def fake_import(name):
            imported.append(name)
            if name == "sentence_transformers":
                return object()
            raise ImportError(name)

        monkeypatch.setattr(local_backend_module.importlib, "import_module", fake_import)
        retriever = LocalKnowledgeBaseRetriever()
        assert retriever._try_load_local_runtime() is True
        assert imported == ["sentence_transformers"]

    def test_runtime_probe_all_missing(self, monkeypatch):
        def fake_import(name):
            raise ImportError(name)

        monkeypatch.setattr(local_backend_module.importlib, "import_module", fake_import)
        retriever = LocalKnowledgeBaseRetriever()
        assert retriever._try_load_local_runtime() is False

    def test_runtime_probe_result_cached(self, monkeypatch):
        calls = []

        def fake_import(name):
            calls.append(name)
            raise ImportError(name)

        monkeypatch.setattr(local_backend_module.importlib, "import_module", fake_import)
        retriever = LocalKnowledgeBaseRetriever()
        assert retriever._try_load_local_runtime() is False
        assert retriever._try_load_local_runtime() is False
        assert len(calls) == len(local_backend_module._LOCAL_RUNTIME_MODULE_CANDIDATES)


# ---------------------------------------------------------------------------
# 批次 2：执行器行为组（design §2.2.2 / §4.1.1）
# ---------------------------------------------------------------------------


class TestVectorKnowledgeBaseExecutorBehavior:
    """执行器：query 互斥分支、委托检索、透出片段集合。"""

    def test_node_type_property(self):
        assert VectorKnowledgeBaseNodeExecutor().node_type == NodeType.VECTOR_KNOWLEDGE_BASE

    # ---- query 解析：manual 分支（AC-13）----

    def test_manual_query_verbatim(self, monkeypatch):
        stub = _StubRetriever()
        monkeypatch.setattr(
            VectorKnowledgeBaseNodeExecutor, "_resolve_retriever", lambda self, config: stub
        )
        context = _make_context(_manual_config(query_value="  如何申请报销  "))
        result = asyncio.run(VectorKnowledgeBaseNodeExecutor().execute(context))
        assert result.success is True
        # 原文逐字符透传，不做 strip/归一化（AC-13）
        assert stub.calls[0]["query"] == "  如何申请报销  "

    def test_manual_query_none_defaults_empty_string(self, monkeypatch):
        stub = _StubRetriever()
        monkeypatch.setattr(
            VectorKnowledgeBaseNodeExecutor, "_resolve_retriever", lambda self, config: stub
        )
        context = _make_context(_manual_config(query_value=None))
        result = asyncio.run(VectorKnowledgeBaseNodeExecutor().execute(context))
        assert result.success is True
        assert stub.calls[0]["query"] == ""

    def test_manual_mode_ignores_reference_field(self, monkeypatch):
        # design §2.2.1 互斥语义：manual 模式仅 query_value 生效
        stub = _StubRetriever()
        monkeypatch.setattr(
            VectorKnowledgeBaseNodeExecutor, "_resolve_retriever", lambda self, config: stub
        )
        context = _make_context(
            _manual_config(query_reference="upstream_output"),
            node_outputs={"upstream_output": "不应被使用"},
        )
        result = asyncio.run(VectorKnowledgeBaseNodeExecutor().execute(context))
        assert result.success is True
        assert stub.calls[0]["query"] == "如何申请报销"

    # ---- query 解析：reference 分支（AC-14/AC-15/AC-20）----

    def test_reference_query_resolved(self, monkeypatch):
        stub = _StubRetriever()
        monkeypatch.setattr(
            VectorKnowledgeBaseNodeExecutor, "_resolve_retriever", lambda self, config: stub
        )
        context = _make_context(
            _manual_config(query_mode="reference", query_reference="upstream_output", query_value=None),
            node_outputs={"upstream_output": "继承查询文本"},
        )
        result = asyncio.run(VectorKnowledgeBaseNodeExecutor().execute(context))
        assert result.success is True
        assert stub.calls[0]["query"] == "继承查询文本"

    def test_reference_field_path(self, monkeypatch):
        stub = _StubRetriever()
        monkeypatch.setattr(
            VectorKnowledgeBaseNodeExecutor, "_resolve_retriever", lambda self, config: stub
        )
        context = _make_context(
            _manual_config(query_mode="reference", query_reference="upstream_output.query", query_value=None),
            node_outputs={"upstream_output": {"query": "字段路径查询"}},
        )
        result = asyncio.run(VectorKnowledgeBaseNodeExecutor().execute(context))
        assert result.success is True
        assert stub.calls[0]["query"] == "字段路径查询"

    def test_reference_not_configured_fails(self, monkeypatch):
        # AC-20：reference 模式未配置引用 → 判 NODE_INPUT_REFERENCE_MISSING 失败
        stub = _StubRetriever()
        monkeypatch.setattr(
            VectorKnowledgeBaseNodeExecutor, "_resolve_retriever", lambda self, config: stub
        )
        context = _make_context(
            _manual_config(query_mode="reference", query_reference=None, query_value=None)
        )
        result = asyncio.run(VectorKnowledgeBaseNodeExecutor().execute(context))
        assert result.success is False
        assert result.error_code == NODE_INPUT_REFERENCE_MISSING
        assert stub.calls == []

    def test_reference_missing_target_fail_fast(self, monkeypatch):
        stub = _StubRetriever()
        monkeypatch.setattr(
            VectorKnowledgeBaseNodeExecutor, "_resolve_retriever", lambda self, config: stub
        )
        context = _make_context(
            _manual_config(query_mode="reference", query_reference="missing_output", query_value=None),
            node_outputs={},
        )
        result = asyncio.run(VectorKnowledgeBaseNodeExecutor().execute(context))
        assert result.success is False
        assert result.error_code == NODE_INPUT_REFERENCE_MISSING

    def test_reference_null_value_coerces_empty_string(self, monkeypatch):
        stub = _StubRetriever()
        monkeypatch.setattr(
            VectorKnowledgeBaseNodeExecutor, "_resolve_retriever", lambda self, config: stub
        )
        context = _make_context(
            _manual_config(query_mode="reference", query_reference="upstream_output", query_value=None),
            node_outputs={"upstream_output": None},
        )
        result = asyncio.run(VectorKnowledgeBaseNodeExecutor().execute(context))
        assert result.success is True
        assert stub.calls[0]["query"] == ""

    def test_reference_non_string_serialized_deterministically(self, monkeypatch):
        stub = _StubRetriever()
        monkeypatch.setattr(
            VectorKnowledgeBaseNodeExecutor, "_resolve_retriever", lambda self, config: stub
        )
        context = _make_context(
            _manual_config(query_mode="reference", query_reference="upstream_output", query_value=None),
            node_outputs={"upstream_output": {"b": 2, "a": "文本"}},
        )
        result = asyncio.run(VectorKnowledgeBaseNodeExecutor().execute(context))
        assert result.success is True
        # 非字符串值确定性 JSON 序列化（键排序 + 非 ASCII 保留）
        assert stub.calls[0]["query"] == '{"a": "文本", "b": 2}'

    # ---- 结果透出与 top_k 截断（AC-21/AC-24/AC-40）----

    def test_output_value_chunk_dicts_in_order(self, monkeypatch):
        chunks = _make_chunks(3)
        stub = _StubRetriever(outcome=RetrievalOutcome(chunks=chunks))
        monkeypatch.setattr(
            VectorKnowledgeBaseNodeExecutor, "_resolve_retriever", lambda self, config: stub
        )
        context = _make_context(_manual_config())
        result = asyncio.run(VectorKnowledgeBaseNodeExecutor().execute(context))
        assert result.success is True
        assert result.output_value == [chunk.model_dump(mode="json") for chunk in chunks]
        assert [item["text"] for item in result.output_value] == ["片段0", "片段1", "片段2"]

    def test_top_k_truncates_output(self, monkeypatch):
        # AC-21：检索返回 20 条，配置 top_k=5 → 输出前 5 条（保序）
        stub = _StubRetriever(outcome=RetrievalOutcome(chunks=_make_chunks(20)))
        monkeypatch.setattr(
            VectorKnowledgeBaseNodeExecutor, "_resolve_retriever", lambda self, config: stub
        )
        context = _make_context(_manual_config(top_k=5))
        result = asyncio.run(VectorKnowledgeBaseNodeExecutor().execute(context))
        assert stub.calls[0]["top_k"] == 5
        assert len(result.output_value) == 5
        assert result.output_value[0]["text"] == "片段0"

    def test_rerank_ordered_result_truncated_to_top_k(self, monkeypatch):
        # AC-24：rerank 后仍按 top_k 截断（重排序结果 8 条 > top_k=5）
        stub = _StubRetriever(outcome=RetrievalOutcome(chunks=_make_chunks(8, prefix="重排")))
        monkeypatch.setattr(
            VectorKnowledgeBaseNodeExecutor, "_resolve_retriever", lambda self, config: stub
        )
        context = _make_context(_manual_config(top_k=5))
        result = asyncio.run(VectorKnowledgeBaseNodeExecutor().execute(context))
        assert len(result.output_value) == 5
        assert result.output_value[0]["text"] == "重排0"

    def test_empty_chunks_is_legal_success(self, monkeypatch):
        # FR-22/AC-39：空片段 = 合法成功（区别于检索失败）
        stub = _StubRetriever(outcome=RetrievalOutcome(chunks=[]))
        monkeypatch.setattr(
            VectorKnowledgeBaseNodeExecutor, "_resolve_retriever", lambda self, config: stub
        )
        context = _make_context(_manual_config())
        result = asyncio.run(VectorKnowledgeBaseNodeExecutor().execute(context))
        assert result.success is True
        assert result.output_value == []
        assert result.error_code is None

    # ---- rerank / local 降级可见性（AC-27/AC-28/AC-31）----

    def test_rerank_not_configured_no_degrade_metadata(self, monkeypatch):
        # AC-28：rerank 未配置 → 跳过成功，无降级标注
        stub = _StubRetriever(outcome=RetrievalOutcome(chunks=_make_chunks(1)))
        monkeypatch.setattr(
            VectorKnowledgeBaseNodeExecutor, "_resolve_retriever", lambda self, config: stub
        )
        context = _make_context(_manual_config())
        result = asyncio.run(VectorKnowledgeBaseNodeExecutor().execute(context))
        assert stub.calls[0]["rerank_model"] is None
        assert "rerank_degraded" not in result.metadata
        assert "local_degraded" not in result.metadata

    def test_rerank_degraded_visible_in_metadata(self, monkeypatch):
        # AC-27：降级必须可见（metadata.rerank_degraded + 原因），禁止静默
        stub = _StubRetriever(
            outcome=RetrievalOutcome(
                chunks=_make_chunks(1),
                rerank_degraded=True,
                rerank_degraded_reason="rerank 重排序服务调用失败，已降级为初排结果",
            )
        )
        monkeypatch.setattr(
            VectorKnowledgeBaseNodeExecutor, "_resolve_retriever", lambda self, config: stub
        )
        context = _make_context(_manual_config())
        result = asyncio.run(VectorKnowledgeBaseNodeExecutor().execute(context))
        assert result.success is True
        assert result.metadata["rerank_degraded"] is True
        assert result.metadata["rerank_degraded_reason"] == "rerank 重排序服务调用失败，已降级为初排结果"

    def test_local_degraded_visible_in_metadata(self, monkeypatch):
        # AC-31：local 降级标注进入 metadata
        stub = _StubRetriever(
            outcome=RetrievalOutcome(
                chunks=[],
                local_degraded=True,
                local_degraded_reason="本地模型运行时不可用，已确定性降级",
            )
        )
        monkeypatch.setattr(
            VectorKnowledgeBaseNodeExecutor, "_resolve_retriever", lambda self, config: stub
        )
        context = _make_context(_manual_config())
        result = asyncio.run(VectorKnowledgeBaseNodeExecutor().execute(context))
        assert result.success is True
        assert result.output_value == []
        assert result.metadata["local_degraded"] is True
        assert result.metadata["local_degraded_reason"] == "本地模型运行时不可用，已确定性降级"

    # ---- 检索链路失败 → 节点失败（INV-D07）----

    def test_retrieval_unavailable_fails_node(self, monkeypatch):
        stub = _StubRetriever(
            error=VectorRetrievalError(
                NODE_VECTOR_RETRIEVAL_UNAVAILABLE, "向量检索服务调用失败（知识库：kb-1）"
            )
        )
        monkeypatch.setattr(
            VectorKnowledgeBaseNodeExecutor, "_resolve_retriever", lambda self, config: stub
        )
        context = _make_context(_manual_config())
        result = asyncio.run(VectorKnowledgeBaseNodeExecutor().execute(context))
        assert result.success is False
        assert result.error_code == NODE_VECTOR_RETRIEVAL_UNAVAILABLE
        assert result.error_message == "向量检索服务调用失败（知识库：kb-1）"
        assert result.output_value is None

    def test_embedding_unavailable_fails_node(self, monkeypatch):
        stub = _StubRetriever(
            error=VectorRetrievalError(
                NODE_VECTOR_EMBEDDING_UNAVAILABLE,
                "embedding 向量化服务调用失败（模型标识：text-embedding-v3）",
            )
        )
        monkeypatch.setattr(
            VectorKnowledgeBaseNodeExecutor, "_resolve_retriever", lambda self, config: stub
        )
        context = _make_context(_manual_config())
        result = asyncio.run(VectorKnowledgeBaseNodeExecutor().execute(context))
        assert result.success is False
        assert result.error_code == NODE_VECTOR_EMBEDDING_UNAVAILABLE

    # ---- local 模式经执行器（AC-30/AC-31）----

    def test_local_mode_runtime_missing_fails(self, monkeypatch):
        # AC-30：local 依赖缺失且未开兜底 → 节点确定性失败
        _settings_without_local_engine(monkeypatch)
        monkeypatch.setattr(
            LocalKnowledgeBaseRetriever, "_try_load_local_runtime", lambda self: False
        )
        context = _make_context(
            _manual_config(
                embedding_model={"mode": "local", "model_identifier": "bge-small-zh"}
            )
        )
        result = asyncio.run(VectorKnowledgeBaseNodeExecutor().execute(context))
        assert result.success is False
        assert result.error_code == NODE_VECTOR_LOCAL_RUNTIME_MISSING
        assert "bge-small-zh" in (result.error_message or "")

    def test_local_mode_fallback_degrades_success(self, monkeypatch):
        # AC-31：开启兜底 → 空结果成功 + local_degraded 可见标注
        # （密封化：防止经引擎委托路径"碰巧通过"，确保验证三态兜底语义）
        _settings_without_local_engine(monkeypatch)
        monkeypatch.setattr(
            LocalKnowledgeBaseRetriever, "_try_load_local_runtime", lambda self: False
        )
        context = _make_context(
            _manual_config(
                embedding_model={
                    "mode": "local",
                    "model_identifier": "bge-small-zh",
                    "allow_degraded_fallback": True,
                }
            )
        )
        result = asyncio.run(VectorKnowledgeBaseNodeExecutor().execute(context))
        assert result.success is True
        assert result.output_value == []
        assert result.metadata["local_degraded"] is True

    # ---- 契约杂项 --------

    def test_output_name_from_node_definition(self, monkeypatch):
        stub = _StubRetriever()
        monkeypatch.setattr(
            VectorKnowledgeBaseNodeExecutor, "_resolve_retriever", lambda self, config: stub
        )
        context = _make_context(_manual_config())
        result = asyncio.run(VectorKnowledgeBaseNodeExecutor().execute(context))
        assert result.output_name == "检索结果"

    def test_duration_ms_non_negative(self, monkeypatch):
        stub = _StubRetriever()
        monkeypatch.setattr(
            VectorKnowledgeBaseNodeExecutor, "_resolve_retriever", lambda self, config: stub
        )
        context = _make_context(_manual_config())
        result = asyncio.run(VectorKnowledgeBaseNodeExecutor().execute(context))
        assert result.duration_ms >= 0

    def test_no_direct_state_write(self, monkeypatch):
        # INV-D08：执行器不直写共享状态/检查点（命名输出写回由调度器统一执行）
        stub = _StubRetriever(outcome=RetrievalOutcome(chunks=_make_chunks(2)))
        monkeypatch.setattr(
            VectorKnowledgeBaseNodeExecutor, "_resolve_retriever", lambda self, config: stub
        )
        outputs = {"上游输出": {"nested": [1, 2]}}
        context = _make_context(_manual_config(), node_outputs=outputs)
        before_outputs = copy.deepcopy(context.shared_state.node_outputs)
        before_pending = copy.deepcopy(context.shared_state.pending_diff)
        result = asyncio.run(VectorKnowledgeBaseNodeExecutor().execute(context))
        assert result.success is True
        assert context.shared_state.node_outputs == before_outputs
        assert context.shared_state.pending_diff == before_pending
        assert context.shared_state.pending_diff == {}


# ---------------------------------------------------------------------------
# 批次 3：引用登记（T3.1 / CHG-07-a，AC-17 前置，DC-06 矩阵追加行）
# ---------------------------------------------------------------------------

# 向量知识库节点最小合法配置基线（加载期校验/引用登记测试共用）
_VKB_BASE_CONFIG = {
    "knowledge_base_id": "kb_main",
    "query_mode": "manual",
    "query_value": "默认查询文本",
    "embedding_model": {"mode": "api", "model_identifier": "text-embedding-v3"},
}


def _vkb_config(**overrides) -> dict:
    """构造向量知识库节点配置字典（覆盖式合并基线）。"""
    config = copy.deepcopy(_VKB_BASE_CONFIG)
    config.update(overrides)
    return config


def _make_vkb_node(
    node_id: str,
    output_name: str | None = None,
    **overrides,
) -> NodeDefinition:
    """构造测试用向量知识库节点（configuration 用 dict，由判别联合解析）。"""
    return NodeDefinition(
        node_id=node_id,
        node_type=NodeType.VECTOR_KNOWLEDGE_BASE,
        configuration=_vkb_config(**overrides),
        output_name=output_name,
    )


def _make_trigger_node(node_id: str = "trig") -> NodeDefinition:
    """构造测试用手动触发器节点。"""
    return NodeDefinition(
        node_id=node_id,
        node_type=NodeType.TRIGGER,
        configuration={"trigger_type": "manual"},
    )


def _make_wf_definition(nodes: list, edge_pairs: list) -> WorkflowDefinition:
    """构造测试用工作流定义（nodes + 连线对）。"""
    edges = [
        EdgeDefinition(
            edge_id=f"e{index}",
            source_node_id=source,
            target_node_id=target,
        )
        for index, (source, target) in enumerate(edge_pairs)
    ]
    return WorkflowDefinition(
        definition_id="wf-vector-kb-batch3",
        name="向量知识库批次3测试",
        spec_version="1.0",
        nodes=list(nodes),
        edges=edges,
        inputs=[],
    )


def _errors_only(issues: list) -> list:
    return [i for i in issues if i.severity == Severity.ERROR]


class TestVectorKnowledgeBaseReferenceRegistration:
    """引用提取登记表接入：reference 模式提取 query_reference（T3.1，AC-17 前置）。"""

    def test_reference_mode_non_empty_extracts(self):
        node = _make_vkb_node(
            "vkb_1", query_mode="reference", query_reference="upstream_output"
        )
        assert collect_input_references(node) == [
            ("query_reference", "upstream_output")
        ]

    def test_reference_mode_blank_not_extracted(self):
        # 空白引用不构成依赖边（拓扑环检测不误纳），加载期拦截由 T3.2 承接
        node = _make_vkb_node("vkb_1", query_mode="reference", query_reference="")
        assert collect_input_references(node) == []

    def test_reference_mode_none_not_extracted(self):
        node = _make_vkb_node("vkb_1", query_mode="reference", query_reference=None)
        assert collect_input_references(node) == []

    def test_manual_mode_extracts_nothing(self):
        node = _make_vkb_node("vkb_1", query_mode="manual", query_value="查询文本")
        assert collect_input_references(node) == []

    def test_rerank_present_changes_nothing(self):
        node = _make_vkb_node(
            "vkb_1",
            query_mode="reference",
            query_reference="upstream_output",
            rerank_model={"mode": "api", "model_identifier": "gte-rerank"},
        )
        assert collect_input_references(node) == [
            ("query_reference", "upstream_output")
        ]


# ---------------------------------------------------------------------------
# 批次 3：加载期校验（T3.2 / CHG-07-b，AC-17 三元组 + 互斥生效字段拦截）
# ---------------------------------------------------------------------------

class TestVectorKnowledgeBaseLoadValidation:
    """NodeConfigurationValidator 接入向量知识库节点（design §2.2.5 / FR-03 / FR-12）。"""

    def test_ac17_missing_reference_target_reports_triplet(self):
        # query_reference 指向不存在的命名输出 → ERROR，三元组：节点 + 字段 + 引用目标
        definition = _make_wf_definition(
            [
                _make_trigger_node(),
                _make_vkb_node(
                    "vkb_1", query_mode="reference", query_reference="missing_output"
                ),
            ],
            [("trig", "vkb_1")],
        )
        issues = NodeConfigurationValidator().validate(definition)

        errors = _errors_only(issues)
        assert len(errors) == 1
        issue = errors[0]
        assert issue.code == WF_VALIDATION_FAILED
        assert issue.node_id == "vkb_1"
        assert "query_reference" in issue.message
        assert "missing_output" in issue.message

    def test_blank_knowledge_base_id_reports_error(self):
        definition = _make_wf_definition(
            [
                _make_trigger_node(),
                _make_vkb_node("vkb_1", knowledge_base_id="   "),
            ],
            [("trig", "vkb_1")],
        )
        issues = NodeConfigurationValidator().validate(definition)

        errors = _errors_only(issues)
        assert len(errors) == 1
        assert errors[0].code == WF_VALIDATION_FAILED
        assert errors[0].node_id == "vkb_1"
        assert "knowledge_base_id" in errors[0].message

    def test_manual_blank_query_value_reports_error(self):
        definition = _make_wf_definition(
            [
                _make_trigger_node(),
                _make_vkb_node("vkb_1", query_mode="manual", query_value="   "),
            ],
            [("trig", "vkb_1")],
        )
        issues = NodeConfigurationValidator().validate(definition)

        errors = _errors_only(issues)
        assert len(errors) == 1
        assert errors[0].code == WF_VALIDATION_FAILED
        assert errors[0].node_id == "vkb_1"
        assert "query_value" in errors[0].message

    def test_reference_blank_query_reference_reports_error(self):
        definition = _make_wf_definition(
            [
                _make_trigger_node(),
                _make_vkb_node("vkb_1", query_mode="reference", query_reference=""),
            ],
            [("trig", "vkb_1")],
        )
        issues = NodeConfigurationValidator().validate(definition)

        errors = _errors_only(issues)
        assert len(errors) == 1
        assert errors[0].code == WF_VALIDATION_FAILED
        assert errors[0].node_id == "vkb_1"
        assert "query_reference" in errors[0].message

    def test_ac17_valid_upstream_reference_passes(self):
        # 合法链路：trigger → agent（命名输出）→ 向量知识库（reference 引用）
        definition = _make_wf_definition(
            [
                _make_trigger_node(),
                NodeDefinition(
                    node_id="producer",
                    node_type=NodeType.AGENT,
                    configuration={"model_identifier": "m"},
                    output_name="up_out",
                ),
                _make_vkb_node(
                    "vkb_1", query_mode="reference", query_reference="up_out"
                ),
            ],
            [("trig", "producer"), ("producer", "vkb_1")],
        )
        assert NodeConfigurationValidator().validate(definition) == []


# ---------------------------------------------------------------------------
# 批次 3：引用环检测（AC-18，登记表驱动，GraphTopologyValidator 零改动 P-8）
# ---------------------------------------------------------------------------

class TestVectorKnowledgeBaseReferenceCycle:
    """query_reference 引用依赖边参与环检测：环 → WF_CYCLIC_GRAPH。"""

    def test_mutual_query_reference_cycle_reports_cyclic_graph(self):
        # vkb_a.reference -> b_out 且 vkb_b.reference -> a_out：互引构成引用环
        definition = _make_wf_definition(
            [
                _make_trigger_node(),
                _make_vkb_node(
                    "vkb_a",
                    output_name="a_out",
                    query_mode="reference",
                    query_reference="b_out",
                ),
                _make_vkb_node(
                    "vkb_b",
                    output_name="b_out",
                    query_mode="reference",
                    query_reference="a_out",
                ),
            ],
            [("trig", "vkb_a"), ("trig", "vkb_b")],
        )
        issues = GraphTopologyValidator().validate(definition)

        cyclic = [i for i in issues if i.code == WF_CYCLIC_GRAPH]
        assert len(cyclic) == 1
        assert cyclic[0].severity == Severity.ERROR
        # 引用环专属消息前缀 + 环上节点 + 引用定位
        assert "由输入引用构成" in cyclic[0].message
        assert "vkb_a" in cyclic[0].message
        assert "vkb_b" in cyclic[0].message
        assert "query_reference" in cyclic[0].message

    def test_no_cycle_without_reference_dependency(self):
        # 仅连线边、无引用依赖时不误报环（负例）
        definition = _make_wf_definition(
            [
                _make_trigger_node(),
                _make_vkb_node(
                    "vkb_a", output_name="a_out", query_mode="manual", query_value="查询A"
                ),
                _make_vkb_node(
                    "vkb_b", output_name="b_out", query_mode="manual", query_value="查询B"
                ),
            ],
            [("trig", "vkb_a"), ("trig", "vkb_b")],
        )
        issues = GraphTopologyValidator().validate(definition)
        assert all(i.code != WF_CYCLIC_GRAPH for i in issues)


# ---------------------------------------------------------------------------
# 批次 3：执行器注册接入（T3.4 / CHG-05/CHG-06，AC-34）
# ---------------------------------------------------------------------------

class TestVectorKnowledgeBaseExecutorRegistration:
    """引擎注册表 + 运行时路由双路径登记（既有执行器零变化，P-6）。"""

    def test_register_phase2_registers_vector_knowledge_base(self):
        from orchestration_platform.engine.node_executor_registry import (
            NodeExecutorRegistry,
        )

        registry = NodeExecutorRegistry()
        registry.register_phase2_executors()

        assert registry.has_executor(NodeType.VECTOR_KNOWLEDGE_BASE)
        executor = registry.get_executor(NodeType.VECTOR_KNOWLEDGE_BASE)
        assert isinstance(executor, VectorKnowledgeBaseNodeExecutor)
        # 同批既有类型零变化（P-6）
        assert registry.has_executor(NodeType.SUBWORKFLOW)
        assert registry.has_executor(NodeType.DYNAMIC_INJECTOR)
        assert registry.has_executor(NodeType.DATA_TRANSFORM)
        assert registry.has_executor(NodeType.OUTPUT_DISPLAY)

    def test_runtime_router_registry_includes_vector_knowledge_base(self):
        from orchestration_platform.api.routers.execution_instance_router import (
            _build_executor_registry,
        )

        registry = _build_executor_registry()

        assert registry.has_executor(NodeType.VECTOR_KNOWLEDGE_BASE)
        assert isinstance(
            registry.get_executor(NodeType.VECTOR_KNOWLEDGE_BASE),
            VectorKnowledgeBaseNodeExecutor,
        )
        # 运行时路由既有 8 类无条件执行器零变化（P-6 / AC-34；
        # Agent 执行器按 LLM 客户端可用性条件注册，不在无条件断言内）
        for node_type in (
            NodeType.TRIGGER,
            NodeType.CONTROL_FLOW,
            NodeType.DATA_SOURCE,
            NodeType.DATA_TRANSFORM,
            NodeType.TOOL,
            NodeType.DYNAMIC_INJECTOR,
            NodeType.SUBWORKFLOW,
            NodeType.OUTPUT_DISPLAY,
        ):
            assert registry.has_executor(node_type), f"{node_type} 执行器缺失"


# ---------------------------------------------------------------------------
# 批次 5（T6.1 配置解析组）：最小合法 YAML 文本级加载
# ---------------------------------------------------------------------------

class TestVectorKnowledgeBaseYamlLoading:
    """向量知识库节点 YAML 文本级解析（最小合法 YAML，INV-D03：新节点 YAML 可加载，旧格式零迁移）。"""

    def test_minimal_vkb_workflow_yaml_parses(self):
        from orchestration_platform.workflow.yaml_workflow_parser import (
            YamlWorkflowParser,
        )

        yaml_text = (
            'spec_version: "1.0"\n'
            "definition_id: vkb_minimal_yaml\n"
            "name: 向量知识库最小YAML\n"
            "definition_version: 1\n"
            "inputs: []\n"
            "outputs: []\n"
            "nodes:\n"
            "  - node_id: trigger_manual\n"
            "    name: 手动触发\n"
            "    node_type: trigger\n"
            "    output_name: trigger_output\n"
            "    configuration:\n"
            "      trigger_type: manual\n"
            "  - node_id: vector_search\n"
            "    name: 向量检索\n"
            "    node_type: vector_knowledge_base\n"
            "    output_name: chunks\n"
            "    configuration:\n"
            "      knowledge_base_id: kb_main\n"
            "      query_mode: reference\n"
            "      query_reference: trigger_output\n"
            "      top_k: 5\n"
            "      embedding_model:\n"
            "        mode: api\n"
            "        model_identifier: text-embedding-v3\n"
            "edges:\n"
            "  - edge_id: e1\n"
            "    source_node_id: trigger_manual\n"
            "    target_node_id: vector_search\n"
        )
        definition = YamlWorkflowParser().parse(yaml_text)

        assert definition.definition_id == "vkb_minimal_yaml"
        assert len(definition.nodes) == 2
        assert len(definition.edges) == 1
        vkb_node = definition.nodes[1]
        assert vkb_node.node_type == NodeType.VECTOR_KNOWLEDGE_BASE
        config = vkb_node.configuration
        assert isinstance(config, VectorKnowledgeBaseNodeConfiguration)
        assert config.knowledge_base_id == "kb_main"
        assert config.query_mode == "reference"
        assert config.query_reference == "trigger_output"
        assert config.top_k == 5
        assert config.embedding_model.mode == "api"
        assert config.embedding_model.model_identifier == "text-embedding-v3"
        assert config.rerank_model is None
