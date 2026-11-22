# =============================================================================
# 本地向量数据库检索内核单元测试（tests/unit/test_local_vector_database_retrieval.py）
#
# 覆盖（CHG-L10 / design §9.1）：
#   - vector_math：度量定义/归一/夹取（AC-09/AC-21/AC-22）、
#     Top-K tie-break 确定性（AC-14）
#   - BruteForceIndex：检索精度与全量线性扫描真值逐条比对
#     （精确率 = 1.0，AC-14/AC-31）/ 过滤（AC-10）/ 集合隔离（AC-11）/
#     归一与边界（空库/维度不符/非法查询 → 确定性错误码，AC-08/AC-15）/
#     top_k 夹取 / 重复执行结果完全一致（INV-L14）
#   - OptionalBackendProbe：无依赖不崩溃、延迟导入、结果缓存、可诊断
#     （AC-19/AC-23，INV-L02）
#
# 沙箱验证：PYTHONPATH=src python -m pytest tests/unit/test_local_vector_database_retrieval.py -q
# （同步 API 驱动，禁 asyncio.run；合成向量固定种子可复现；
#   真值以独立实现逐条比对，不调用被测 vector_math）
# =============================================================================

import importlib
import math
import random
from typing import Callable, List, Optional

import pytest

from orchestration_platform.domain.errors import (
    VECTOR_DB_DIMENSION_MISMATCH,
    VECTOR_DB_INVALID_VECTOR,
)
from orchestration_platform.integrations.local_vector_database.models import (
    DiagnosticReport,
    MetadataFilter,
    VectorRecord,
)
from orchestration_platform.integrations.local_vector_database.retrieval import (
    BruteForceIndex,
    OptionalBackendProbe,
    SearchBackend,
)
from orchestration_platform.integrations.local_vector_database.store import (
    SqliteVectorStore,
)
from orchestration_platform.integrations.local_vector_database.vector_math import (
    clamp_score,
    cosine_score,
    dot_score,
    euclidean_score,
    l2_normalize,
    metric_score,
    select_top_k,
    supported_metrics,
)
from orchestration_platform.integrations.vector_knowledge_base.models import (
    VectorRetrievalError,
)


def _make_index(tmp_path, **store_kwargs):
    store = SqliteVectorStore(storage_root=str(tmp_path / "vkb"), **store_kwargs)
    index = BruteForceIndex(store=store, max_top_k=100)
    return store, index


def _query_vector(dim: int, seed: int) -> List[float]:
    rng = random.Random(seed)
    return [round(rng.random(), 6) for _ in range(dim)]


def _seed_records(
    count: int,
    dim: int = 16,
    source_fn: Optional[Callable[[int], dict]] = None,
    seed_prefix: str = "r",
) -> List[VectorRecord]:
    records: List[VectorRecord] = []
    for i in range(count):
        record_id = f"{seed_prefix}-{i:04d}"
        rng = random.Random(record_id)
        records.append(
            VectorRecord(
                record_id=record_id,
                vector=[round(rng.random(), 6) for _ in range(dim)],
                text=f"文档 {i}",
                source=source_fn(i) if source_fn is not None else None,
            )
        )
    return records


def _truth_top_k(metric: str, query: List[float], records: List[VectorRecord], k: int):
    """独立实现的全量线性扫描真值（不调用被测 vector_math）。"""

    def score(vec: List[float]) -> float:
        if metric == "euclidean":
            distance = math.sqrt(
                sum((a - b) ** 2 for a, b in zip(query, vec))
            )
            return 1.0 / (1.0 + distance)
        query_norm = math.sqrt(sum(x * x for x in query))
        candidate_norm = math.sqrt(sum(x * x for x in vec))
        if query_norm == 0.0 or candidate_norm == 0.0:
            return 0.0
        dot = sum(a * b for a, b in zip(query, vec))
        return max(0.0, min(1.0, dot / (query_norm * candidate_norm)))

    scored = [(score(r.vector), r.record_id) for r in records]
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    return scored[:k]


def _assert_code(code: str, fn, *args, **kwargs) -> VectorRetrievalError:
    with pytest.raises(VectorRetrievalError) as exc_info:
        fn(*args, **kwargs)
    assert exc_info.value.error_code == code
    return exc_info.value


class TestVectorMath:
    """度量/归一/夹取/Top-K（T3.1、T3.2，AC-09/AC-14/AC-21/AC-22）。"""

    def test_clamp_score_nan_and_bounds(self):
        # INV-L11：NaN → 0.0、越界夹取到 [0,1]、inf → 1.0
        assert clamp_score(float("nan")) == 0.0
        assert clamp_score(-0.5) == 0.0
        assert clamp_score(0.0) == 0.0
        assert clamp_score(0.5) == 0.5
        assert clamp_score(1.0) == 1.0
        assert clamp_score(1.5) == 1.0
        assert clamp_score(float("inf")) == 1.0

    def test_l2_normalize_scales_to_unit(self):
        normalized = l2_normalize([3.0, 4.0])
        assert normalized == pytest.approx([0.6, 0.8], abs=1e-12)
        norm = math.sqrt(sum(v * v for v in normalized))
        assert norm == pytest.approx(1.0, abs=1e-12)

    def test_l2_normalize_zero_vector_unchanged(self):
        # 零向量原样返回（除零保护）
        assert l2_normalize([0.0, 0.0, 0.0]) == [0.0, 0.0, 0.0]

    def test_cosine_identical_orthogonal_opposite(self):
        # cosine ∈ [0,1]：同向 ≈ 1、正交 ≈ 0、反向夹取到 0（DC-03）
        assert cosine_score([1.0, 0.0], [2.0, 0.0]) == pytest.approx(1.0, abs=1e-12)
        assert cosine_score([1.0, 0.0], [0.0, 3.0]) == pytest.approx(0.0, abs=1e-12)
        assert cosine_score([1.0, 0.0], [-5.0, 0.0]) == 0.0

    def test_cosine_zero_and_nan_no_leak(self):
        # 零向量 / NaN 分量 → 0.0（禁 NaN 外泄，AC-21/AC-22）
        assert cosine_score([0.0, 0.0], [1.0, 2.0]) == 0.0
        assert cosine_score([1.0, float("nan")], [1.0, 2.0]) == 0.0

    def test_dot_equals_cosine_after_normalization(self):
        # DC-03：dot 各自归一后点积 → 与 cosine 同式
        query = _query_vector(6, seed=11)
        candidate = _query_vector(6, seed=12)
        assert dot_score(query, candidate) == pytest.approx(
            cosine_score(query, candidate), abs=1e-12
        )

    def test_euclidean_known_values_and_bounds(self):
        # 1/(1+d)：同点 → 1.0；d=3 → 0.25；单调递减且 ∈ (0,1]
        assert euclidean_score([0.0], [0.0]) == pytest.approx(1.0, abs=1e-12)
        assert euclidean_score([0.0], [3.0]) == pytest.approx(0.25, abs=1e-12)
        assert euclidean_score([0.0], [1.0]) > euclidean_score([0.0], [2.0])

    def test_metric_score_dispatch_and_unknown(self):
        assert supported_metrics() == ("cosine", "dot", "euclidean")
        assert metric_score("cosine", [1.0, 0.0], [1.0, 0.0]) == pytest.approx(
            1.0, abs=1e-12
        )
        with pytest.raises(ValueError):
            metric_score("manhattan", [1.0], [1.0])

    def test_select_top_k_tiebreak_deterministic(self):
        # 排序键 (score 降序, record_id 升序)：同分按 id 升序（INV-L14）
        items = [("c", 0.5), ("a", 0.5), ("b", 0.9), ("d", 0.1)]
        result = select_top_k(
            items,
            top_k=10,
            max_top_k=100,
            score_of=lambda item: item[1],
            id_of=lambda item: item[0],
        )
        assert result == [("b", 0.9), ("a", 0.5), ("c", 0.5), ("d", 0.1)]

    def test_select_top_k_clamp_and_empty(self):
        items = [("a", 0.9), ("b", 0.8)]
        result_all = select_top_k(
            items, top_k=50, max_top_k=100, score_of=lambda i: i[1], id_of=lambda i: i[0]
        )
        assert len(result_all) == 2
        result_clamped = select_top_k(
            items, top_k=50, max_top_k=1, score_of=lambda i: i[1], id_of=lambda i: i[0]
        )
        assert result_clamped == [("a", 0.9)]
        result_zero = select_top_k(
            items, top_k=0, max_top_k=100, score_of=lambda i: i[1], id_of=lambda i: i[0]
        )
        assert result_zero == []
        result_negative = select_top_k(
            items, top_k=-3, max_top_k=100, score_of=lambda i: i[1], id_of=lambda i: i[0]
        )
        assert result_negative == []
        result_empty = select_top_k(
            [], top_k=5, max_top_k=100, score_of=lambda i: i[1], id_of=lambda i: i[0]
        )
        assert result_empty == []


class TestBruteForceIndexSearch:
    """暴力检索内核（T3.3，AC-08~AC-11/AC-14/AC-15/AC-21~AC-23/AC-31）。"""

    @pytest.mark.parametrize("metric", ["cosine", "dot", "euclidean"])
    def test_search_precision_matches_bruteforce_truth(self, tmp_path, metric):
        # 精确率 = 1.0：与全量线性扫描真值逐条比对（AC-14/AC-31）
        store, index = _make_index(tmp_path)
        for record in _seed_records(count=50, dim=16):
            store.upsert("kb-1", record)
        loaded = list(store.load_records("kb-1"))
        assert len(loaded) == 50
        query = _query_vector(16, seed=42)
        hits = index.search("kb-1", query, 7, None, metric)
        truth = _truth_top_k(metric, query, loaded, 7)
        assert [h.record_id for h in hits] == [rid for _, rid in truth]
        for hit, (score, _) in zip(hits, truth):
            assert hit.score == pytest.approx(score, abs=1e-9)
            assert 0.0 <= hit.score <= 1.0

    def test_search_deterministic_repeat(self, tmp_path):
        # 同集合 + 同查询 + 同参数 → 结果完全一致（AC-14/INV-L14）
        store, index = _make_index(tmp_path)
        for record in _seed_records(count=20, dim=8):
            store.upsert("kb-1", record)
        query = _query_vector(8, seed=7)
        first = index.search("kb-1", query, 5, None, "cosine")
        second = index.search("kb-1", query, 5, None, "cosine")
        assert first == second
        assert len(first) == 5

    def test_search_tiebreak_record_id_ascending(self, tmp_path):
        # 同分 tie-break = record_id 升序（INV-L14）
        store, index = _make_index(tmp_path)
        vector = [0.1, 0.2, 0.3]
        for rid in ("r-c", "r-a", "r-b"):
            store.upsert(
                "kb-1", VectorRecord(record_id=rid, vector=list(vector), text="t")
            )
        hits = index.search("kb-1", list(vector), 3, None, "cosine")
        assert [h.record_id for h in hits] == ["r-a", "r-b", "r-c"]
        assert all(h.score == pytest.approx(1.0, abs=1e-9) for h in hits)

    def test_search_zero_query_vector_deterministic(self, tmp_path):
        # 零查询向量 → 全部 0 分、确定性 id 升序（无 NaN/未定义行为）
        store, index = _make_index(tmp_path)
        records = _seed_records(count=5, dim=3)
        for record in records:
            store.upsert("kb-1", record)
        hits = index.search("kb-1", [0.0, 0.0, 0.0], 3, None, "cosine")
        assert len(hits) == 3
        assert all(h.score == 0.0 for h in hits)
        assert [h.record_id for h in hits] == ["r-0000", "r-0001", "r-0002"]

    def test_search_filter_equals_and_k_semantics(self, tmp_path):
        # 先过滤 → 度量 → 排序 → 截断：K 作用域 = 过滤后候选（AC-10）
        store, index = _make_index(tmp_path)
        source_fn = lambda i: {"type": "A"} if i % 2 == 0 else {"type": "B"}  # noqa: E731
        records = _seed_records(count=20, dim=8, source_fn=source_fn)
        for record in records:
            store.upsert("kb-1", record)
        loaded = list(store.load_records("kb-1"))
        query = _query_vector(8, seed=9)
        hits = index.search(
            "kb-1", query, 5, MetadataFilter(equals={"type": "A"}), "cosine"
        )
        assert len(hits) == 5
        assert all(h.source is not None and h.source.get("type") == "A" for h in hits)
        truth = _truth_top_k(
            "cosine",
            query,
            [r for r in loaded if r.source is not None and r.source.get("type") == "A"],
            5,
        )
        assert [h.record_id for h in hits] == [rid for _, rid in truth]

    def test_search_filter_multiple_fields(self, tmp_path):
        store, index = _make_index(tmp_path)

        def source_fn(i: int) -> dict:
            if i % 2 != 0:
                return {"type": "B"}
            return {"type": "A", "level": "high" if i % 4 == 0 else "low"}

        records = _seed_records(count=20, dim=8, source_fn=source_fn)
        for record in records:
            store.upsert("kb-1", record)
        query = _query_vector(8, seed=13)
        hits = index.search(
            "kb-1",
            query,
            10,
            MetadataFilter(equals={"type": "A", "level": "low"}),
            "cosine",
        )
        # i % 4 == 2 的记录：i=2,6,10,14,18 → 5 条
        assert len(hits) == 5
        assert all(
            h.source is not None
            and h.source.get("type") == "A"
            and h.source.get("level") == "low"
            for h in hits
        )

    def test_search_filter_fewer_candidates_than_k(self, tmp_path):
        # 过滤后候选少于 K → 返回全部满足者（不放大、不失败）
        store, index = _make_index(tmp_path)
        source_fn = (
            lambda i: {"type": "X"} if i in (3, 7, 11) else {"type": "Y"}  # noqa: E731
        )
        records = _seed_records(count=20, dim=6, source_fn=source_fn)
        for record in records:
            store.upsert("kb-1", record)
        hits = index.search(
            "kb-1", _query_vector(6, seed=3), 10, MetadataFilter(equals={"type": "X"}), "cosine"
        )
        assert len(hits) == 3
        assert {h.record_id for h in hits} == {"r-0003", "r-0007", "r-0011"}

    def test_search_filter_skips_source_none_records(self, tmp_path):
        store, index = _make_index(tmp_path)
        source_fn = lambda i: None if i % 3 == 0 else {"type": "A"}  # noqa: E731
        records = _seed_records(count=20, dim=6, source_fn=source_fn)
        for record in records:
            store.upsert("kb-1", record)
        hits = index.search(
            "kb-1", _query_vector(6, seed=4), 20, MetadataFilter(equals={"type": "A"}), "cosine"
        )
        # source 为 None 的记录（i%3==0，共 7 条）不参与匹配
        assert len(hits) == 13
        assert all(h.source is not None and h.source.get("type") == "A" for h in hits)

    def test_search_collection_isolation(self, tmp_path):
        # 集合 A 检索不返回集合 B 条目（AC-11/INV-L06）
        store, index = _make_index(tmp_path)
        records_a = _seed_records(count=10, dim=8, seed_prefix="a")
        records_b = _seed_records(count=10, dim=8, seed_prefix="b")
        for record in records_a:
            store.upsert("kb-a", record)
        for record in records_b:
            store.upsert("kb-b", record)
        hits = index.search("kb-a", _query_vector(8, seed=5), 10, None, "cosine")
        ids_a = {r.record_id for r in records_a}
        ids_b = {r.record_id for r in records_b}
        assert ids_a & ids_b == set()
        assert {h.record_id for h in hits} <= ids_a
        assert not ({h.record_id for h in hits} & ids_b)

    def test_search_empty_collection_returns_empty_success(self, tmp_path):
        # 空库 → 空列表成功（非失败，INV-L15/AC-15）
        store, index = _make_index(tmp_path)
        store.ensure_collection("kb-1", 4, "cosine")
        assert index.search("kb-1", [0.1, 0.2, 0.3, 0.4], 5, None, "cosine") == []

    def test_search_missing_collection_empty_no_side_effect(self, tmp_path):
        # 集合不存在 → 空命中成功；不建目录、零副作用
        store, index = _make_index(tmp_path)
        assert index.search("kb-1", [0.1, 0.2, 0.3], 5, None, "cosine") == []
        assert not (tmp_path / "vkb").exists()

    def test_search_query_dimension_mismatch_rejected(self, tmp_path):
        # 查询维度 ≠ 集合声明维度 → 确定性错误码（不是空结果，AC-08/AC-20）
        store, index = _make_index(tmp_path)
        store.upsert("kb-1", _seed_records(1, dim=3)[0])
        _assert_code(
            VECTOR_DB_DIMENSION_MISMATCH,
            index.search,
            "kb-1",
            [0.1, 0.2, 0.3, 0.4],
            5,
            None,
            "cosine",
        )

    def test_search_invalid_query_vector_rejected(self, tmp_path):
        store, index = _make_index(tmp_path)
        store.upsert("kb-1", _seed_records(1, dim=3)[0])
        _assert_code(
            VECTOR_DB_INVALID_VECTOR, index.search, "kb-1", [], 5, None, "cosine"
        )
        _assert_code(
            VECTOR_DB_INVALID_VECTOR,
            index.search,
            "kb-1",
            [0.1, float("nan"), 0.3],
            5,
            None,
            "cosine",
        )
        _assert_code(
            VECTOR_DB_INVALID_VECTOR,
            index.search,
            "kb-1",
            [float("inf"), 0.2, 0.3],
            5,
            None,
            "cosine",
        )

    def test_search_top_k_clamped_to_max_top_k(self, tmp_path):
        # top_k 超上限 → 夹取到 max_top_k（T3.2）
        store = SqliteVectorStore(storage_root=str(tmp_path / "vkb"))
        index = BruteForceIndex(store=store, max_top_k=5)
        for record in _seed_records(count=12, dim=4):
            store.upsert("kb-1", record)
        hits = index.search("kb-1", _query_vector(4, seed=1), 50, None, "cosine")
        assert len(hits) == 5

    def test_search_top_k_zero_and_negative(self, tmp_path):
        store, index = _make_index(tmp_path)
        for record in _seed_records(count=6, dim=4):
            store.upsert("kb-1", record)
        query = _query_vector(4, seed=2)
        assert index.search("kb-1", query, 0, None, "cosine") == []
        assert index.search("kb-1", query, -3, None, "cosine") == []

    def test_search_unknown_metric_raises(self, tmp_path):
        store, index = _make_index(tmp_path)
        store.upsert("kb-1", _seed_records(1, dim=3)[0])
        with pytest.raises(ValueError):
            index.search("kb-1", [0.1, 0.2, 0.3], 5, None, "manhattan")

    def test_index_constructor_rejects_invalid_max_top_k(self, tmp_path):
        store = SqliteVectorStore(storage_root=str(tmp_path / "vkb"))
        with pytest.raises(ValueError):
            BruteForceIndex(store=store, max_top_k=0)

    def test_rebuild_returns_consistency_report(self, tmp_path):
        # rebuild 本期语义 = 一致性校验（P2 ANN 预留，DC-09/design §2.2）
        store, index = _make_index(tmp_path)
        store.upsert("kb-1", _seed_records(1, dim=3)[0])
        report = index.rebuild("kb-1")
        assert isinstance(report, DiagnosticReport)
        assert report.ok is True
        assert report.record_count == 1
        missing_report = index.rebuild("kb-missing")
        assert missing_report.ok is False

    def test_search_backend_is_abstract(self):
        with pytest.raises(TypeError):
            SearchBackend()  # type: ignore[abstract]


class TestOptionalBackendProbe:
    """可选后端探测（T3.3，AC-19/AC-23，INV-L02/DC-05）。"""

    def test_constructor_and_module_import_no_dependency_side_effects(self):
        # 无依赖环境：导入 retrieval + 构造 probe 不崩溃、不引入可选依赖
        import sys

        probe = OptionalBackendProbe()
        for name in OptionalBackendProbe.DEFAULT_CANDIDATES:
            assert name not in sys.modules

    def test_probe_missing_modules_returns_none(self):
        probe = OptionalBackendProbe(
            candidates=("definitely_missing_module_a", "definitely_missing_module_b")
        )
        assert probe.probe() is None
        assert probe.is_ready() is False

    def test_probe_importable_candidate_returns_name(self):
        probe = OptionalBackendProbe(candidates=("json",))
        assert probe.probe() == "json"
        assert probe.is_ready() is True

    def test_probe_default_candidates_diagnosable(self):
        # 默认候选：结果可查询（本环境无 faiss/qdrant/sentence_transformers → None）
        probe = OptionalBackendProbe()
        result = probe.probe()
        assert result is None or result in OptionalBackendProbe.DEFAULT_CANDIDATES
        assert probe.is_ready() == (result is not None)

    def test_probe_result_cached(self, monkeypatch):
        # 结果缓存：重复 probe/is_ready 不重复导入
        calls = []
        original_import = importlib.import_module

        def counting_import(name, *args, **kwargs):
            calls.append(name)
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(importlib, "import_module", counting_import)
        probe = OptionalBackendProbe(
            candidates=("definitely_missing_module_c", "definitely_missing_module_d")
        )
        assert probe.probe() is None
        assert probe.probe() is None
        assert probe.is_ready() is False
        assert calls.count("definitely_missing_module_c") == 1
        assert calls.count("definitely_missing_module_d") == 1
