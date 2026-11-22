# =============================================================================
# 本地向量数据库委托集成测试
# （tests/unit/test_local_vector_database_backend_integration.py）
#
# 覆盖（tasks.md T5.6 / CHG-L13，design DC-04、INV-L04 兼容守护）：
#   1. 开关 False（含 settings 字段不存在容错）→ local_backend 三态行为
#      逐字不变（对齐既有 TestLocalKnowledgeBaseRetriever 6 用例语义）；
#      默认不触达本组件（AC-15/AC-28）；
#   2. 开关 True → 委托本组件：真实来源 → 真实命中；集合不存在/仅哈希
#      兜底 → 空结果 + local_degraded（降级标注语义一致，AC-27/AC-29）；
#      无兜底 → NODE_VECTOR_LOCAL_RUNTIME_MISSING；engine 内部异常 →
#      回落既有三态（不暴露 VECTOR_DB_* 到节点侧）；
#   3. 契约守护：retrieve 五参签名不变、既有符号保留（INV-L04）。
#
# 沙箱验证（B5 批次命令）：
#   PYTHONPATH=src python -m pytest tests/unit/test_local_vector_database_backend_integration.py tests/unit/test_vector_knowledge_base_node_executor.py -q
# =============================================================================

import inspect
import sys

import pytest

from orchestration_platform.configuration import settings as settings_module
from orchestration_platform.domain.errors import NODE_VECTOR_LOCAL_RUNTIME_MISSING
from orchestration_platform.domain.models.node_configurations import ModelLoadingConfig
from orchestration_platform.integrations.local_vector_database import (
    LocalVectorDatabaseService,
    VectorRecord,
)
from orchestration_platform.integrations.vector_knowledge_base.local_backend import (
    _HASH_VECTOR_DIM,
    _LOCAL_RUNTIME_MODULE_CANDIDATES,
    LocalKnowledgeBaseRetriever,
    _hash_vector,
)
from orchestration_platform.integrations.vector_knowledge_base.models import (
    RetrievalOutcome,
    VectorRetrievalError,
)

_LVD_PACKAGE = "orchestration_platform.integrations.local_vector_database"


def _embedding(allow_fallback=False):
    return ModelLoadingConfig.model_validate(
        {
            "mode": "local",
            "model_identifier": "bge-small-zh",
            "allow_degraded_fallback": allow_fallback,
        }
    )


class TestDelegateSwitchOff:
    """开关 False（含字段不存在容错）：既有三态逐字不变（INV-L04/AC-15）。"""

    @staticmethod
    def _settings_without_lvd(monkeypatch):
        class _SettingsNoLvd:
            pass

        monkeypatch.setattr(
            settings_module, "get_settings", lambda: _SettingsNoLvd()
        )

    def test_missing_fail_fast_unchanged(self, monkeypatch):
        # AC-30：依赖缺失且未开兜底 → 确定性失败，不崩溃
        self._settings_without_lvd(monkeypatch)
        retriever = LocalKnowledgeBaseRetriever()
        retriever._local_runtime_ready = False
        with pytest.raises(VectorRetrievalError) as exc_info:
            retriever.retrieve("kb-1", "查询", 5, _embedding(), None)
        assert exc_info.value.error_code == NODE_VECTOR_LOCAL_RUNTIME_MISSING
        assert "bge-small-zh" in exc_info.value.error_message
        assert "allow_degraded_fallback" in exc_info.value.error_message

    def test_missing_fallback_degrades_unchanged(self, monkeypatch):
        # FR-17/AC-31：兜底降级 → 空片段 + 显式标注，不伪造语义
        self._settings_without_lvd(monkeypatch)
        retriever = LocalKnowledgeBaseRetriever()
        retriever._local_runtime_ready = False
        outcome = retriever.retrieve("kb-1", "查询", 5, _embedding(True), None)
        assert outcome.chunks == []
        assert outcome.local_degraded is True
        assert "哈希伪向量" in (outcome.local_degraded_reason or "")

    def test_ready_degrades_empty_unchanged(self, monkeypatch):
        # OP-10：运行时就绪但本期未实现推理 → 空结果降级，静默伪造 = 违规
        self._settings_without_lvd(monkeypatch)
        retriever = LocalKnowledgeBaseRetriever()
        retriever._local_runtime_ready = True
        outcome = retriever.retrieve("kb-1", "查询", 5, _embedding(), None)
        assert outcome.chunks == []
        assert outcome.local_degraded is True
        assert "未实现真实本地推理" in (outcome.local_degraded_reason or "")

    def test_switch_off_zero_component_touch(self, monkeypatch):
        # AC-28：开关 False → retrieve 全程不触达本组件（不导入子包）
        self._settings_without_lvd(monkeypatch)
        monkeypatch.delitem(sys.modules, _LVD_PACKAGE, raising=False)
        retriever = LocalKnowledgeBaseRetriever()
        retriever._local_runtime_ready = False
        outcome = retriever.retrieve("kb-1", "查询", 5, _embedding(True), None)
        assert outcome.local_degraded is True
        assert _LVD_PACKAGE not in sys.modules

    def test_attach_returns_none_when_settings_field_missing(self, monkeypatch):
        # B6 落地前 settings 无 local_vector_database_enabled → 视为 False
        self._settings_without_lvd(monkeypatch)
        assert LocalKnowledgeBaseRetriever()._try_attach_local_engine() is None

    def test_attach_fail_safe_on_settings_error(self, monkeypatch):
        def _boom():
            raise RuntimeError("settings unavailable")

        monkeypatch.setattr(settings_module, "get_settings", _boom)
        assert LocalKnowledgeBaseRetriever()._try_attach_local_engine() is None


class TestDelegateSwitchOn:
    """开关 True → 委托本组件（AC-16/AC-29）：真实命中 + 降级标注语义一致。"""

    @pytest.fixture
    def engine_env(self, monkeypatch, tmp_path):
        storage_root = tmp_path / "vkb"

        class _SettingsEnabled:
            local_vector_database_enabled = True
            local_vector_database_storage_root = str(storage_root)

        monkeypatch.setattr(
            settings_module, "get_settings", lambda: _SettingsEnabled()
        )
        retriever = LocalKnowledgeBaseRetriever()
        engine = retriever._try_attach_local_engine()
        assert engine is not None
        assert isinstance(engine, LocalVectorDatabaseService)
        return retriever, engine

    @staticmethod
    def _seed(engine):
        report = engine.upsert_records(
            "kb-1",
            [
                VectorRecord(
                    record_id="r-1", vector=[1.0, 0.0, 0.0], text="报销流程"
                ),
                VectorRecord(
                    record_id="r-2", vector=[0.0, 1.0, 0.0], text="考勤制度"
                ),
                VectorRecord(
                    record_id="r-3", vector=[0.7, 0.7, 0.0], text="差旅标准"
                ),
            ],
        )
        assert report.upserted_count == 3
        assert report.failed == []

    def test_engine_attached_and_cached(self, engine_env):
        retriever, engine = engine_env
        assert retriever._try_attach_local_engine() is engine

    def test_real_vector_source_real_hits(self, engine_env, monkeypatch):
        # AC-29：集合含真实来源向量 → 委托本组件真实检索命中
        retriever, engine = engine_env
        self._seed(engine)
        monkeypatch.setattr(
            retriever,
            "_resolve_query_vector",
            lambda query, embedding_model, declared_dimension: (
                [1.0, 0.0, 0.0],
                "api",
            ),
        )
        outcome = retriever.retrieve("kb-1", "查询", 2, _embedding(), None)
        assert isinstance(outcome, RetrievalOutcome)
        assert [chunk.text for chunk in outcome.chunks] == ["报销流程", "差旅标准"]
        assert outcome.chunks[0].score == pytest.approx(1.0, abs=1e-9)
        # 行为增量登记（B2-C5 / DC-B2-05 ④）：种子记录为存量缺省 = hash 代际，
        # 出口强制降级标注（不冒充真实语义，INV-B2-05）——原断言 is False
        assert outcome.local_degraded is True
        assert outcome.local_degraded_reason

    def test_missing_collection_degrades_empty(self, engine_env, monkeypatch):
        # 集合不存在 → 空结果 + local_degraded（保持既有降级语义）
        retriever, _engine = engine_env

        def _must_not_resolve(query, embedding_model, declared_dimension):
            raise AssertionError("集合缺失时不应解析查询向量")

        monkeypatch.setattr(retriever, "_resolve_query_vector", _must_not_resolve)
        outcome = retriever.retrieve("ghost-kb", "查询", 5, _embedding(), None)
        assert outcome.chunks == []
        assert outcome.local_degraded is True
        assert "集合不存在或为空" in (outcome.local_degraded_reason or "")

    def test_hash_fallback_only_degrades_empty(self, engine_env, monkeypatch):
        # 仅哈希兜底 → 空结果 + 强制 local_degraded 标注（AC-31）
        retriever, engine = engine_env
        self._seed(engine)

        def _unavailable(query, embedding_model, declared_dimension):
            raise RuntimeError("embedding source unavailable")

        monkeypatch.setattr(retriever, "_resolve_query_vector", _unavailable)
        outcome = retriever.retrieve("kb-1", "查询", 5, _embedding(True), None)
        assert outcome.chunks == []
        assert outcome.local_degraded is True
        assert "哈希伪向量" in (outcome.local_degraded_reason or "")

    def test_no_fallback_raises_runtime_missing(self, engine_env, monkeypatch):
        # 真实来源不可用且未开兜底 → 既有确定性错误码（AC-30 语义一致）
        retriever, engine = engine_env
        self._seed(engine)

        def _unavailable(query, embedding_model, declared_dimension):
            raise RuntimeError("embedding source unavailable")

        monkeypatch.setattr(retriever, "_resolve_query_vector", _unavailable)
        with pytest.raises(VectorRetrievalError) as exc_info:
            retriever.retrieve("kb-1", "查询", 5, _embedding(False), None)
        assert exc_info.value.error_code == NODE_VECTOR_LOCAL_RUNTIME_MISSING
        assert "allow_degraded_fallback" in exc_info.value.error_message

    def test_hash_provenance_never_real_semantics(self, engine_env, monkeypatch):
        # INV-L08 出口守护：哈希来源永不以真实语义结果外泄（即使直供向量）
        retriever, engine = engine_env
        self._seed(engine)
        monkeypatch.setattr(
            retriever,
            "_resolve_query_vector",
            lambda query, embedding_model, declared_dimension: (
                _hash_vector(query),
                "hash_fallback",
            ),
        )
        outcome = retriever.retrieve("kb-1", "查询", 5, _embedding(True), None)
        assert outcome.chunks == []
        assert outcome.local_degraded is True
        assert outcome.local_degraded_reason

    def test_engine_internal_error_falls_back_legacy(self, engine_env, monkeypatch):
        # T5.3：engine 内部异常 → 回落既有三态（不暴露 VECTOR_DB_*）
        retriever, engine = engine_env
        self._seed(engine)
        retriever._local_runtime_ready = False  # 回落路径确定性（禁机器差异）
        monkeypatch.setattr(
            retriever,
            "_resolve_query_vector",
            lambda query, embedding_model, declared_dimension: (
                [1.0, 0.0, 0.0],
                "api",
            ),
        )

        def _engine_broken(collection_id, query_vector, top_k):
            raise VectorRetrievalError(
                "VECTOR_DB_STORAGE_UNAVAILABLE", "engine broken"
            )

        monkeypatch.setattr(engine, "retrieve", _engine_broken)
        outcome = retriever.retrieve("kb-1", "查询", 5, _embedding(True), None)
        assert outcome.chunks == []
        assert outcome.local_degraded is True
        assert "哈希伪向量" in (outcome.local_degraded_reason or "")

    def test_invalid_kb_id_falls_back_legacy(self, engine_env, monkeypatch):
        # 集合标识非法 → engine 诊断异常 → 回落既有三态（不外泄 VECTOR_DB_*）
        retriever, _engine = engine_env
        retriever._local_runtime_ready = False
        monkeypatch.setattr(
            retriever,
            "_resolve_query_vector",
            lambda query, embedding_model, declared_dimension: (
                [1.0, 0.0, 0.0],
                "api",
            ),
        )
        outcome = retriever.retrieve("Invalid_KB", "查询", 5, _embedding(True), None)
        assert outcome.chunks == []
        assert outcome.local_degraded is True
        assert "哈希伪向量" in (outcome.local_degraded_reason or "")


class TestDelegateContract:
    """契约守护组（INV-L04/AC-27/AC-29）：签名、符号、方法面。"""

    def test_retrieve_signature_unchanged(self):
        parameters = list(
            inspect.signature(LocalKnowledgeBaseRetriever.retrieve).parameters
        )
        assert parameters == [
            "self",
            "knowledge_base_id",
            "query",
            "top_k",
            "embedding_model",
            "rerank_model",
        ]

    def test_legacy_symbols_preserved(self):
        # T5.2：既有符号零改动（_HASH_VECTOR_DIM/候选清单/_hash_vector）
        assert _HASH_VECTOR_DIM == 128
        assert "sentence_transformers" in _LOCAL_RUNTIME_MODULE_CANDIDATES
        assert _hash_vector("文本") == _hash_vector("文本")
        assert _hash_vector("") == [0.0] * 128
        retriever = LocalKnowledgeBaseRetriever()
        assert hasattr(retriever, "_try_load_local_runtime")
        assert retriever._local_runtime_ready is None

    def test_delegate_methods_exist(self):
        retriever = LocalKnowledgeBaseRetriever()
        assert callable(retriever._try_attach_local_engine)
        assert callable(retriever._retrieve_via_engine)
        assert callable(retriever._resolve_query_vector)
        assert retriever._engine is None
