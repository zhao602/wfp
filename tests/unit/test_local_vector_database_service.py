# =============================================================================
# 本地向量数据库 service 层单元测试（tests/unit/test_local_vector_database_service.py）
#
# 按 tasks.md 批次增量编写：
#   批次 B1（CHG-L02/L03/L18）：内部模型组 + 配置派生组——
#     - 模型可实例化/可序列化（含 JSON 往返）；metric 取值域被 Literal 约束；
#     - score 归一 [0,1]（INV-L11）；维度声明上界（FR-29）；
#     - 开关默认 False（INV-L05）、懒初始化零副作用（AC-28）、
#       容错读取（字段不存在 → 缺省值，不崩溃）；
#   批次 B5（CHG-L12）补全：facade 契约 / 导入导出 / I-O-AC 对照 / 开关默认行为。
#
# 沙箱验证：PYTHONPATH=src python -m pytest tests/unit/test_local_vector_database_service.py -q
# =============================================================================

import json
import sys

import pytest
from pydantic import ValidationError

from orchestration_platform.configuration import settings as settings_module
from orchestration_platform.domain.errors import (
    VECTOR_DB_DIMENSION_MISMATCH,
    VECTOR_DB_IMPORT_FILE_UNREADABLE,
    VECTOR_DB_INVALID_COLLECTION_ID,
)
from orchestration_platform.integrations.local_vector_database import (
    CollectionMeta,
    DeleteReport,
    DiagnosticReport,
    ExportReport,
    ImportReport,
    LocalVectorDatabaseConfig,
    LocalVectorDatabaseService,
    MetadataFilter,
    RecordFailure,
    UpsertReport,
    VectorRecord,
    VectorSearchHit,
)
from orchestration_platform.integrations.local_vector_database import config as lvd_config_module
from orchestration_platform.integrations.vector_knowledge_base.models import (
    Chunk,
    RetrievalOutcome,
    VectorRetrievalError,
)


def _make_meta(**overrides):
    payload = {
        "collection_id": "kb-1",
        "declared_dimension": 128,
        "metric": "cosine",
        "format_version": "1",
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
    }
    payload.update(overrides)
    return CollectionMeta(**payload)


class TestLocalVectorDatabaseModels:
    """内部领域模型组（CHG-L02）：可实例化、可序列化、取值域约束。"""

    def test_vector_record_roundtrip(self):
        record = VectorRecord(
            record_id="doc-1",
            vector=[0.1, 0.2, 0.3],
            text="报销流程说明",
            source={"type": "hr"},
        )
        data = record.model_dump()
        assert data["record_id"] == "doc-1"
        assert data["vector"] == [0.1, 0.2, 0.3]
        assert data["source"] == {"type": "hr"}
        # JSON 往返（可序列化）
        assert VectorRecord.model_validate_json(record.model_dump_json()) == record

    def test_vector_record_source_defaults_none(self):
        record = VectorRecord(record_id="r-1", vector=[1.0], text="t")
        assert record.source is None

    def test_vector_record_rejects_empty_record_id(self):
        with pytest.raises(ValidationError):
            VectorRecord(record_id="", vector=[1.0], text="t")

    def test_collection_meta_metric_literal_domain(self):
        # INV-L12：度量取值域被 Literal 约束（禁 StrEnum）
        for metric in ("cosine", "dot", "euclidean"):
            assert _make_meta(metric=metric).metric == metric
        with pytest.raises(ValidationError):
            _make_meta(metric="manhattan")

    def test_collection_meta_dimension_bounds(self):
        # FR-29/AC-38：维度声明上界（1..4096）
        assert _make_meta(declared_dimension=4096).declared_dimension == 4096
        with pytest.raises(ValidationError):
            _make_meta(declared_dimension=0)
        with pytest.raises(ValidationError):
            _make_meta(declared_dimension=4097)

    def test_vector_search_hit_score_bounds(self):
        # INV-L11：score 归一到 [0,1]（模型级约束）
        hit = VectorSearchHit(record_id="r-1", text="t", score=0.5)
        assert 0.0 <= hit.score <= 1.0
        assert VectorSearchHit(record_id="r-1", text="t", score=0.0).score == 0.0
        assert VectorSearchHit(record_id="r-1", text="t", score=1.0).score == 1.0
        with pytest.raises(ValidationError):
            VectorSearchHit(record_id="r-1", text="t", score=1.5)
        with pytest.raises(ValidationError):
            VectorSearchHit(record_id="r-1", text="t", score=-0.1)

    def test_metadata_filter_defaults_empty(self):
        assert MetadataFilter().equals == {}
        assert MetadataFilter(equals={"type": "hr"}).equals == {"type": "hr"}

    def test_reports_instantiable_and_serializable(self):
        failure = RecordFailure(record_id="bad-1", reason="维度不一致")
        reports = (
            UpsertReport(upserted_count=2, failed=[failure]),
            DeleteReport(deleted_count=1, missing_ids=["missing-1"]),
            ImportReport(imported_count=97, failed=[failure]),
            ExportReport(exported_count=97),
            DiagnosticReport(
                collection_id="kb-1",
                ok=True,
                integrity_check="ok",
                dimension_consistent=True,
                declared_dimension=128,
                record_count=3,
                format_version="1",
                issues=[],
            ),
        )
        for report in reports:
            assert isinstance(report.model_dump(), dict)
            assert report.model_dump_json()

    def test_diagnostic_report_defaults(self):
        report = DiagnosticReport(collection_id="kb-1", ok=False, integrity_check="ok")
        assert report.dimension_consistent is False
        assert report.declared_dimension is None
        assert report.record_count == 0
        assert report.format_version == ""
        assert report.issues == []


class TestLocalVectorDatabaseConfig:
    """配置派生组（CHG-L03）：默认值、注入、懒初始化、容错读取。"""

    def test_config_defaults(self):
        # DC-07：8 字段默认值（开关默认 False，INV-L05）
        config = LocalVectorDatabaseConfig()
        assert config.enabled is False
        assert config.storage_root == "./.data/vector_kb"
        assert config.default_dimension == 128
        assert config.default_metric == "cosine"
        assert config.backend == ""
        assert config.query_timeout_ms == 1000
        assert config.max_records_per_collection == 100000
        assert config.max_top_k == 100

    def test_config_injection_overrides_all_fields(self, tmp_path):
        # AC-28：注入式构造可覆盖全部字段（绕开全局 @lru_cache）
        config = LocalVectorDatabaseConfig(
            enabled=True,
            storage_root=str(tmp_path),
            default_dimension=384,
            default_metric="euclidean",
            backend="faiss",
            query_timeout_ms=500,
            max_records_per_collection=10,
            max_top_k=20,
        )
        assert config.enabled is True
        assert config.storage_root == str(tmp_path)
        assert config.default_dimension == 384
        assert config.default_metric == "euclidean"
        assert config.backend == "faiss"
        assert config.query_timeout_ms == 500
        assert config.max_records_per_collection == 10
        assert config.max_top_k == 20

    def test_config_rejects_invalid_values(self):
        with pytest.raises(ValidationError):
            LocalVectorDatabaseConfig(default_dimension=0)
        with pytest.raises(ValidationError):
            LocalVectorDatabaseConfig(default_dimension=4097)
        with pytest.raises(ValidationError):
            LocalVectorDatabaseConfig(default_metric="manhattan")
        with pytest.raises(ValidationError):
            LocalVectorDatabaseConfig(query_timeout_ms=0)
        with pytest.raises(ValidationError):
            LocalVectorDatabaseConfig(max_top_k=0)

    def test_config_lazy_init_no_disk_side_effects(self, tmp_path):
        # AC-28：开关 False 构造 → 不触盘（不创建存储根目录）
        storage_root = tmp_path / "vkb"
        config = LocalVectorDatabaseConfig(enabled=False, storage_root=str(storage_root))
        assert config.enabled is False
        assert not storage_root.exists()

    def test_from_settings_tolerates_missing_fields(self, monkeypatch):
        # B6 落地前 settings 无 local_vector_database_* 字段 → 缺省值，不崩溃
        class _SettingsWithoutLvdFields:
            vector_kb_search_base_url = ""

        monkeypatch.setattr(
            settings_module, "get_settings", lambda: _SettingsWithoutLvdFields()
        )
        config = lvd_config_module.from_settings()
        assert config.enabled is False
        assert config.storage_root == "./.data/vector_kb"
        assert config.default_dimension == 128
        assert config.default_metric == "cosine"
        assert config.backend == ""
        assert config.query_timeout_ms == 1000
        assert config.max_records_per_collection == 100000
        assert config.max_top_k == 100

    def test_from_settings_reads_declared_fields(self, monkeypatch):
        class _SettingsWithLvdFields:
            local_vector_database_enabled = True
            local_vector_database_storage_root = "./custom/root"
            local_vector_database_default_dimension = 384
            local_vector_database_default_metric = "dot"
            local_vector_database_backend = ""
            local_vector_database_query_timeout_ms = 2000
            local_vector_database_max_records_per_collection = 500
            local_vector_database_max_top_k = 50

        monkeypatch.setattr(
            settings_module, "get_settings", lambda: _SettingsWithLvdFields()
        )
        config = lvd_config_module.from_settings()
        assert config.enabled is True
        assert config.storage_root == "./custom/root"
        assert config.default_dimension == 384
        assert config.default_metric == "dot"
        assert config.query_timeout_ms == 2000
        assert config.max_records_per_collection == 500
        assert config.max_top_k == 50

    def test_from_settings_defaults_when_settings_unavailable(self, monkeypatch):
        # 配置源不可用 → fail-safe 取缺省值（确定性，不崩溃）
        def _boom():
            raise RuntimeError("settings unavailable")

        monkeypatch.setattr(settings_module, "get_settings", _boom)
        config = lvd_config_module.from_settings()
        assert config.enabled is False
        assert config.default_dimension == 128


class TestSwitchDefaultBehavior:
    """开关默认行为组（INV-L05 / AC-15/AC-16/AC-28）。"""

    def test_switch_default_off(self):
        assert LocalVectorDatabaseConfig().enabled is False

    def test_switch_off_construction_no_optional_dependency_probe(self, tmp_path):
        # AC-28：开关 False 构造 → 不探测依赖（不新增导入 faiss/qdrant/
        # sentence_transformers，INV-L02）
        storage_root = tmp_path / "vkb"
        before = set(sys.modules)
        LocalVectorDatabaseConfig(enabled=False, storage_root=str(storage_root))
        lvd_config_module.from_settings()
        newly_imported = set(sys.modules) - before
        assert not (
            {"faiss", "qdrant_client", "sentence_transformers"} & newly_imported
        )
        assert not storage_root.exists()


# ---------------------------------------------------------------------------
# 批次 B5（CHG-L12）：service facade 契约 / 批量写删 / 导入导出 / 开关默认
# ---------------------------------------------------------------------------


def _service_config(tmp_path, **overrides):
    """注入式配置（T5.5）：tmp_path 存储根，绕开全局 @lru_cache。"""
    payload = {"enabled": True, "storage_root": str(tmp_path / "vkb")}
    payload.update(overrides)
    return LocalVectorDatabaseConfig(**payload)


class TestLocalVectorDatabaseServiceFacade:
    """facade 契约组（T5.1/CHG-L08）：出口复用既有契约（INV-L03/AC-27）。"""

    @staticmethod
    def _seed(tmp_path):
        service = LocalVectorDatabaseService(config=_service_config(tmp_path))
        report = service.upsert_records(
            "kb-1",
            [
                VectorRecord(
                    record_id="r-1",
                    vector=[1.0, 0.0, 0.0],
                    text="报销流程",
                    source={"type": "hr"},
                ),
                VectorRecord(
                    record_id="r-2",
                    vector=[0.0, 1.0, 0.0],
                    text="考勤制度",
                    source={"type": "hr"},
                ),
                VectorRecord(
                    record_id="r-3",
                    vector=[0.7, 0.7, 0.0],
                    text="差旅标准",
                    source={"type": "finance"},
                ),
            ],
        )
        assert report.upserted_count == 3
        assert report.failed == []
        return service

    def test_retrieve_returns_retrieval_outcome_with_chunks(self, tmp_path):
        service = self._seed(tmp_path)
        outcome = service.retrieve("kb-1", [1.0, 0.0, 0.0], 2)
        assert isinstance(outcome, RetrievalOutcome)
        assert all(isinstance(chunk, Chunk) for chunk in outcome.chunks)
        assert [chunk.text for chunk in outcome.chunks] == ["报销流程", "差旅标准"]
        scores = [chunk.score for chunk in outcome.chunks]
        assert scores == sorted(scores, reverse=True)
        assert outcome.chunks[0].score == pytest.approx(1.0, abs=1e-9)
        # 行为增量登记（B2-C5 / DC-B2-05 ④）：存量缺省 = hash 代际，出口强制
        # 降级标注（不冒充真实语义，INV-B2-05）——原断言 local_degraded is False
        assert outcome.local_degraded is True
        assert outcome.local_degraded_reason

    def test_retrieve_missing_collection_empty_and_no_dir(self, tmp_path):
        # INV-L15：集合不存在 → 空结果成功（不是失败），且不建集合
        service = LocalVectorDatabaseService(config=_service_config(tmp_path))
        outcome = service.retrieve("ghost", [1.0], 5)
        assert isinstance(outcome, RetrievalOutcome)
        assert outcome.chunks == []
        assert not (tmp_path / "vkb").exists()

    def test_retrieve_invalid_collection_id_raises(self, tmp_path):
        # INV-L10：集合标识白名单（禁路径穿越）
        service = LocalVectorDatabaseService(config=_service_config(tmp_path))
        with pytest.raises(VectorRetrievalError) as exc_info:
            service.retrieve("../escape", [1.0], 5)
        assert exc_info.value.error_code == VECTOR_DB_INVALID_COLLECTION_ID

    def test_retrieve_dimension_mismatch_raises(self, tmp_path):
        service = self._seed(tmp_path)
        with pytest.raises(VectorRetrievalError) as exc_info:
            service.retrieve("kb-1", [1.0, 0.0], 5)
        assert exc_info.value.error_code == VECTOR_DB_DIMENSION_MISMATCH

    def test_retrieve_filter_narrows_candidates(self, tmp_path):
        # AC-10：先过滤 → 再度量（K 作用域 = 过滤后候选集）
        service = self._seed(tmp_path)
        outcome = service.retrieve(
            "kb-1",
            [1.0, 0.0, 0.0],
            5,
            filter_=MetadataFilter(equals={"type": "hr"}),
        )
        # 仅 hr 两条入选（finance 的 r-3 被过滤），按得分降序
        assert [chunk.text for chunk in outcome.chunks] == ["报销流程", "考勤制度"]

    def test_retrieve_top_k_clamped_and_zero(self, tmp_path):
        service = self._seed(tmp_path)
        assert len(service.retrieve("kb-1", [1.0, 0.0, 0.0], 99).chunks) == 3
        assert service.retrieve("kb-1", [1.0, 0.0, 0.0], 0).chunks == []

    def test_retrieve_metric_override_changes_scores(self, tmp_path):
        service = self._seed(tmp_path)
        cosine = service.retrieve("kb-1", [1.0, 0.0, 0.0], 3, metric="cosine")
        euclidean = service.retrieve("kb-1", [1.0, 0.0, 0.0], 3, metric="euclidean")
        assert cosine.chunks[0].score == pytest.approx(1.0, abs=1e-9)
        assert euclidean.chunks[0].text == "报销流程"
        assert euclidean.chunks[-1].text == "考勤制度"
        # 度量不同 → 次名得分取值不同（cos ≈0.707 vs 1/(1+d) ≈0.568）
        assert (
            abs(cosine.chunks[1].score - euclidean.chunks[1].score) > 0.05
        )

    def test_retrieve_backend_declared_degrades_annotated(self, tmp_path):
        # DC-05：声明增强后端但本期未接入实现 → 暴力检索 + local_degraded 标注
        service = LocalVectorDatabaseService(
            config=_service_config(tmp_path, backend="faiss")
        )
        service.upsert_records(
            "kb-1",
            [VectorRecord(record_id="r-1", vector=[1.0], text="命中文本")],
        )
        outcome = service.retrieve("kb-1", [1.0], 5)
        assert [chunk.text for chunk in outcome.chunks] == ["命中文本"]
        assert outcome.local_degraded is True
        assert outcome.local_degraded_reason


class TestServiceUpsertDelete:
    """批量写/删组（AC-04~AC-07）：幂等、部分成功。"""

    def test_upsert_creates_collection_lazily(self, tmp_path):
        service = LocalVectorDatabaseService(config=_service_config(tmp_path))
        root = tmp_path / "vkb"
        assert not root.exists()
        report = service.upsert_records(
            "kb-1",
            [VectorRecord(record_id="r-1", vector=[1.0, 2.0], text="t")],
        )
        assert report.upserted_count == 1
        assert (root / "kb-1.sqlite3").exists()

    def test_upsert_idempotent_overwrite(self, tmp_path):
        # AC-05：同 record_id 覆盖更新（幂等，不增计数）
        service = LocalVectorDatabaseService(config=_service_config(tmp_path))
        first = VectorRecord(record_id="r-1", vector=[1.0], text="旧文本")
        second = VectorRecord(record_id="r-1", vector=[1.0], text="新文本")
        assert service.upsert_records("kb-1", [first]).upserted_count == 1
        assert service.upsert_records("kb-1", [second]).upserted_count == 1
        assert service.diagnose("kb-1").record_count == 1
        outcome = service.retrieve("kb-1", [1.0], 5)
        assert outcome.chunks[0].text == "新文本"

    def test_upsert_partial_success_on_bad_records(self, tmp_path):
        # AC-04：单条非法不整体失败（部分成功 + 失败清单）
        service = LocalVectorDatabaseService(config=_service_config(tmp_path))
        good_1 = VectorRecord(record_id="r-1", vector=[1.0], text="t1")
        good_2 = VectorRecord(record_id="r-2", vector=[2.0], text="t2")
        bad = VectorRecord(record_id="r-3", vector=[1.0, 2.0], text="维度不符")
        report = service.upsert_records("kb-1", [good_1, bad, good_2])
        assert report.upserted_count == 2
        assert [failure.record_id for failure in report.failed] == ["r-3"]
        assert "维度" in report.failed[0].reason
        assert service.diagnose("kb-1").record_count == 2

    def test_upsert_invalid_collection_id_raises(self, tmp_path):
        service = LocalVectorDatabaseService(config=_service_config(tmp_path))
        with pytest.raises(VectorRetrievalError) as exc_info:
            service.upsert_records(
                "Bad/Id",
                [VectorRecord(record_id="r-1", vector=[1.0], text="t")],
            )
        assert exc_info.value.error_code == VECTOR_DB_INVALID_COLLECTION_ID

    def test_delete_reports_deleted_and_missing(self, tmp_path):
        # AC-06/AC-07：幂等删除，不存在条目返回“未删除”
        service = LocalVectorDatabaseService(config=_service_config(tmp_path))
        service.upsert_records(
            "kb-1",
            [
                VectorRecord(record_id="r-1", vector=[1.0], text="t1"),
                VectorRecord(record_id="r-2", vector=[2.0], text="t2"),
            ],
        )
        report = service.delete_records("kb-1", ["r-1", "ghost-1"])
        assert report.deleted_count == 1
        assert report.missing_ids == ["ghost-1"]

    def test_delete_on_missing_collection_all_missing(self, tmp_path):
        service = LocalVectorDatabaseService(config=_service_config(tmp_path))
        report = service.delete_records("ghost", ["r-1"])
        assert report.deleted_count == 0
        assert report.missing_ids == ["r-1"]
        assert not (tmp_path / "vkb").exists()


class TestServiceImportExport:
    """JSONL 导入导出组（AC-13）：逐行部分成功；不可读整体失败。"""

    @staticmethod
    def _line(record_id):
        payload = {
            "record_id": record_id,
            "vector": [float(len(record_id)), 1.0],
            "text": f"文本-{record_id}",
            "source": {"kind": "seed"},
        }
        return json.dumps(payload, ensure_ascii=False)

    def test_import_partial_success_97_of_100(self, tmp_path):
        # AC-13 完成标准：100 条含 3 条非法 → 97 入库 + 3 条失败原因
        lines = [self._line(f"r-{i:03d}") for i in range(100)]
        lines[10] = "不是合法 JSON{{{"
        lines[50] = json.dumps({"record_id": "no-vector"}, ensure_ascii=False)
        lines[90] = json.dumps([1, 2, 3])
        source = tmp_path / "seed.jsonl"
        source.write_text("\n".join(lines) + "\n", encoding="utf-8")
        service = LocalVectorDatabaseService(config=_service_config(tmp_path))
        report = service.import_jsonl("kb-1", str(source))
        assert report.imported_count == 97
        assert [failure.record_id for failure in report.failed] == [
            "line:11",
            "line:51",
            "line:91",
        ]
        assert all(failure.reason for failure in report.failed)
        assert service.diagnose("kb-1").record_count == 97

    def test_import_file_unreadable_whole_failure(self, tmp_path):
        # 文件不可读 → 整体失败（无部分写入）
        service = LocalVectorDatabaseService(config=_service_config(tmp_path))
        missing = tmp_path / "missing.jsonl"
        with pytest.raises(VectorRetrievalError) as exc_info:
            service.import_jsonl("kb-1", str(missing))
        assert exc_info.value.error_code == VECTOR_DB_IMPORT_FILE_UNREADABLE
        assert not (tmp_path / "vkb").exists()

    def test_import_blank_line_fails_deterministically(self, tmp_path):
        source = tmp_path / "seed.jsonl"
        source.write_text(
            self._line("r-1") + "\n\n" + self._line("r-2") + "\n",
            encoding="utf-8",
        )
        service = LocalVectorDatabaseService(config=_service_config(tmp_path))
        report = service.import_jsonl("kb-1", str(source))
        assert report.imported_count == 2
        assert [failure.record_id for failure in report.failed] == ["line:2"]
        assert all(failure.reason for failure in report.failed)

    def test_export_then_import_roundtrip(self, tmp_path):
        service = LocalVectorDatabaseService(config=_service_config(tmp_path))
        service.upsert_records(
            "kb-1",
            [
                VectorRecord(
                    record_id="r-1",
                    vector=[1.0, 0.0, 0.0],
                    text="报销",
                    source={"t": 1},
                ),
                VectorRecord(record_id="r-2", vector=[0.0, 1.0, 0.0], text="考勤"),
                VectorRecord(
                    record_id="r-3",
                    vector=[0.5, 0.5, 0.5],
                    text="差旅",
                    source={"t": 2},
                ),
            ],
        )
        export_path = tmp_path / "export.jsonl"
        export_report = service.export_jsonl("kb-1", str(export_path))
        assert export_report.exported_count == 3
        import_report = service.import_jsonl("kb-2", str(export_path))
        assert import_report.imported_count == 3
        assert import_report.failed == []
        second_export = tmp_path / "export2.jsonl"
        service.export_jsonl("kb-2", str(second_export))
        assert export_path.read_text(encoding="utf-8") == second_export.read_text(
            encoding="utf-8"
        )

    def test_export_missing_collection_writes_empty_file(self, tmp_path):
        service = LocalVectorDatabaseService(config=_service_config(tmp_path))
        target = tmp_path / "empty.jsonl"
        report = service.export_jsonl("ghost", str(target))
        assert report.exported_count == 0
        assert target.read_text(encoding="utf-8") == ""

    def test_export_write_failure_wrapped(self, tmp_path):
        service = LocalVectorDatabaseService(config=_service_config(tmp_path))
        service.upsert_records(
            "kb-1", [VectorRecord(record_id="r-1", vector=[1.0], text="t")]
        )
        bad_target = tmp_path / "no-such-dir" / "out.jsonl"
        with pytest.raises(VectorRetrievalError) as exc_info:
            service.export_jsonl("kb-1", str(bad_target))
        assert exc_info.value.error_code == "VECTOR_DB_STORAGE_UNAVAILABLE"


class TestServiceSwitchDefaults:
    """service 开关默认行为组（AC-15/AC-16/AC-28）：懒初始化零副作用。"""

    def test_construction_no_dir_no_probe(self, tmp_path):
        service = LocalVectorDatabaseService(config=_service_config(tmp_path))
        assert not (tmp_path / "vkb").exists()
        assert service._probe._probed is False  # 未探测可选依赖

    def test_construction_no_optional_dependency_import(self, tmp_path):
        before = set(sys.modules)
        LocalVectorDatabaseService(config=_service_config(tmp_path))
        newly_imported = set(sys.modules) - before
        assert not (
            {"faiss", "qdrant_client", "sentence_transformers"} & newly_imported
        )

    def test_retrieve_default_backend_no_annotation_no_probe(self, tmp_path):
        # 默认后端为空 → 纯暴力、零探测副作用、无降级标注（DC-05）
        service = LocalVectorDatabaseService(config=_service_config(tmp_path))
        outcome = service.retrieve("ghost", [1.0], 5)
        assert outcome.chunks == []
        assert outcome.local_degraded is False
        assert service._probe._probed is False

    def test_diagnose_missing_collection_report(self, tmp_path):
        service = LocalVectorDatabaseService(config=_service_config(tmp_path))
        report = service.diagnose("ghost")
        assert report.ok is False
        assert report.integrity_check == "collection_missing"
        assert report.record_count == 0

    def test_diagnose_seeded_collection_ok(self, tmp_path):
        service = LocalVectorDatabaseService(config=_service_config(tmp_path))
        service.upsert_records(
            "kb-1", [VectorRecord(record_id="r-1", vector=[1.0], text="t")]
        )
        report = service.diagnose("kb-1")
        assert report.ok is True
        assert report.record_count == 1
        assert report.format_version == "1"


class TestCredentialAuditBoundary:
    """T8.1 边界加固：凭据审查——错误/诊断/导出产物无凭据字段（INV-L09 / HR-2）。"""

    def test_export_product_contains_no_credential_fields(self, tmp_path):
        service = LocalVectorDatabaseService(config=_service_config(tmp_path))
        report = service.upsert_records(
            "kb-audit",
            [
                VectorRecord(
                    record_id="r-1",
                    vector=[1.0, 0.0],
                    text="审计样本一",
                    source={"type": "hr"},
                ),
                VectorRecord(
                    record_id="r-2",
                    vector=[0.0, 1.0],
                    text="审计样本二",
                    source={"type": "finance"},
                ),
            ],
        )
        assert report.upserted_count == 2
        export_path = tmp_path / "audit_export.jsonl"
        service.export_jsonl("kb-audit", str(export_path))
        content = export_path.read_text(encoding="utf-8")
        assert "api_key" not in content
        assert "vector_kb_search_api_key" not in content
        assert "Bearer" not in content
        lines = content.splitlines()
        assert len(lines) == 2
        for line in lines:
            payload = json.loads(line)
            # 字段集增量登记（B2-C5 / DC-B2-05 ④）：embedding_kind 代际随导出落盘
            assert set(payload.keys()) <= {
                "record_id",
                "vector",
                "text",
                "source",
                "embedding_kind",
            }

    def test_error_and_diagnostic_messages_carry_no_credentials(self, tmp_path):
        service = LocalVectorDatabaseService(config=_service_config(tmp_path))
        with pytest.raises(VectorRetrievalError) as exc_info:
            service.upsert_records(
                "../escape",
                [VectorRecord(record_id="r-1", vector=[1.0], text="t")],
            )
        error = exc_info.value
        assert error.error_code == VECTOR_DB_INVALID_COLLECTION_ID
        assert "api_key" not in error.error_message
        assert "Bearer" not in error.error_message
        report = service.diagnose("kb-audit-missing")
        dumped = report.model_dump_json()
        assert "api_key" not in dumped
        assert "Bearer" not in dumped
        assert report.ok is False
        assert report.integrity_check == "collection_missing"
