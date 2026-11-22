# =============================================================================
# 本地向量数据库性能实测（tests/unit/test_local_vector_database_perf.py）
#
# 覆盖（CHG-L14 / design §7.1~§7.3 / spec FR-22、AC-31、DFX §4.1）：
#   1. 暴力检索纯计算时延：合成向量（固定种子可复现，N ∈ {1,000/5,000/10,000}，
#      D=128），Top-K（K=10/50/100）各 ≥5 次取样（中位/均值/极值），
#      不含向量化与 I/O；输出附"本机 + 沙箱"环境标注（PERF|env|...）；
#   2. 确定性：同参数重复执行结果完全一致（AC-14/INV-L14）；
#   3. 精确率 = 1.0：与全量线性扫描真值逐条比对（暴力无近似，AC-31）；
#   4. 写入 / 重启恢复时延 + 磁盘占用：真实 store 路径（单条 = 单事务、
#      每操作短连接，AC-34），tmp_path 注入存储根（不触真实 ./.data）；
#   5. 磁盘占用线性：文件大小 vs 条目数 × 维度（design §7.2-4）。
#
# 实测数字经 PERF| 前缀输出行采集，用于回填 design §7.2 / spec DFX §4.1
# 的具体时延上界（上界由实测标定，design 不预设数字）。
# =============================================================================

import os
import platform
import random
import statistics
import sys
import time
from typing import Dict, List, Tuple

import pytest

from orchestration_platform.integrations.local_vector_database.models import (
    VectorRecord,
    VectorSearchHit,
)
from orchestration_platform.integrations.local_vector_database.store import (
    SqliteVectorStore,
)
from orchestration_platform.integrations.local_vector_database.vector_math import (
    metric_score,
    select_top_k,
)

# 环境标注（design §7.2-1：记录"本机 + 沙箱"环境）
PERF_ENV_LABEL = "本机+沙箱"

# 固定随机种子（可复现，AC-31）
SEED = 42
QUERY_SEED = 12345

# 规模档与取样参数（design §7.2-1：N=1k/5k/10k、D=128、K=10/50/100、≥5 次取样）
DIMENSION = 128
TIERS = (1000, 5000, 10000)
TOP_KS = (10, 50, 100)
SAMPLES = 5

# 防悬挂宽裕上界（非性能回归断言：仅保证"无超时无崩溃"，10s 极宽裕）
GENEROUS_TIMEOUT_MS = 10_000.0

_MAX_TOP_K = 100
_COLLECTION_ID = "perf"


def _synthetic_vectors(count: int) -> List[Tuple[str, List[float], str]]:
    """固定种子合成向量集（同种子同前缀，跨档可复现）。"""
    rng = random.Random(SEED)
    records: List[Tuple[str, List[float], str]] = []
    for index in range(count):
        vector = [rng.uniform(-1.0, 1.0) for _ in range(DIMENSION)]
        records.append((f"r-{index:06d}", vector, f"perf chunk {index}"))
    return records


def _query_vector() -> List[float]:
    """固定种子查询向量（D=128）。"""
    rng = random.Random(QUERY_SEED)
    return [rng.uniform(-1.0, 1.0) for _ in range(DIMENSION)]


def _kernel_pure_search(
    records: List[Tuple[str, List[float], str]],
    query: List[float],
    top_k: int,
) -> List[VectorSearchHit]:
    """纯计算路径（对齐 BruteForceIndex.search 去 I/O：打分 + Top-K 截断）。"""
    hits = [
        VectorSearchHit(
            record_id=record_id,
            text=text,
            score=metric_score("cosine", query, vector),
            source=None,
        )
        for record_id, vector, text in records
    ]
    return select_top_k(
        hits,
        top_k,
        _MAX_TOP_K,
        score_of=lambda hit: hit.score,
        id_of=lambda hit: hit.record_id,
    )


def _full_scan_ground_truth(
    records: List[Tuple[str, List[float], str]], query: List[float]
) -> List[Tuple[str, float]]:
    """全量线性扫描真值：全打分 + 全排序（独立于 select_top_k 的参照实现）。"""
    scored = [
        (record_id, metric_score("cosine", query, vector))
        for record_id, vector, _ in records
    ]
    scored.sort(key=lambda item: (-item[1], item[0]))
    return scored


class TestPerformanceEnvironmentAnnotation:
    """环境标注输出（design §7.2-1：记录"本机 + 沙箱"环境）。"""

    def test_environment_banner(self) -> None:
        banner = (
            f"PERF|env|label={PERF_ENV_LABEL}"
            f"|python={sys.version.split()[0]}"
            f"|platform={platform.platform()}"
            f"|machine={platform.machine()}"
        )
        print(banner)
        assert "PERF|env|" in banner
        assert PERF_ENV_LABEL in banner


class TestPureSearchPerformance:
    """暴力检索纯计算时延 / 确定性 / 精确率（design §7.2-1/2/5）。"""

    @pytest.mark.parametrize("n_records", TIERS)
    def test_search_latency_determinism_precision(self, n_records: int) -> None:
        records = _synthetic_vectors(n_records)
        query = _query_vector()
        ground_truth = _full_scan_ground_truth(records, query)
        assert len(ground_truth) == n_records

        for top_k in TOP_KS:
            results: List[List[Tuple[str, float]]] = []
            samples_ms: List[float] = []
            for _ in range(SAMPLES):
                started = time.perf_counter()
                top = _kernel_pure_search(records, query, top_k)
                samples_ms.append((time.perf_counter() - started) * 1000.0)
                results.append([(hit.record_id, hit.score) for hit in top])

            median_ms = statistics.median(samples_ms)
            print(
                f"PERF|search|N={n_records}|K={top_k}"
                f"|median_ms={median_ms:.2f}"
                f"|mean_ms={statistics.mean(samples_ms):.2f}"
                f"|min_ms={min(samples_ms):.2f}"
                f"|max_ms={max(samples_ms):.2f}"
                f"|samples={SAMPLES}"
            )
            # 无超时无崩溃（宽裕防悬挂上界，非性能回归断言）
            assert median_ms < GENEROUS_TIMEOUT_MS
            # 确定性：同参数 ≥5 次重复执行结果完全一致（AC-14/INV-L14）
            for other in results[1:]:
                assert other == results[0]
            assert len(results[0]) == top_k
            # 精确率 = 1.0：与全量线性扫描真值逐条比对（暴力无近似，AC-31）
            expected = ground_truth[:top_k]
            assert [rid for rid, _ in results[0]] == [rid for rid, _ in expected]
            for (_, actual_score), (_, truth_score) in zip(results[0], expected):
                assert actual_score == pytest.approx(truth_score, abs=1e-12)


class TestWriteRecoveryPerformance:
    """写入 / 重启恢复时延 + 磁盘占用（design §7.2-3/4，真实 store 路径）。"""

    # 跨参数化用例共享：各档集合文件大小（供线性占用断言）
    disk_bytes: Dict[int, int] = {}

    @pytest.mark.parametrize("n_records", TIERS)
    def test_write_recovery_and_disk_footprint(
        self, n_records: int, tmp_path
    ) -> None:
        storage_root = str(tmp_path / "store")
        records = [
            VectorRecord(record_id=record_id, vector=vector, text=text)
            for record_id, vector, text in _synthetic_vectors(n_records)
        ]
        store = SqliteVectorStore(
            storage_root=storage_root,
            default_metric="cosine",
            max_records_per_collection=100000,
        )

        # 写入时延（真实路径：单条 = 单事务、每操作短连接，AC-34）
        started = time.perf_counter()
        for record in records:
            store.upsert(_COLLECTION_ID, record)
        write_ms = (time.perf_counter() - started) * 1000.0

        size_bytes = os.path.getsize(
            os.path.join(storage_root, f"{_COLLECTION_ID}.sqlite3")
        )
        self.disk_bytes[n_records] = size_bytes

        # 重启恢复时延：新 store 实例（模拟进程重启）全量载入
        restarted = SqliteVectorStore(
            storage_root=storage_root,
            default_metric="cosine",
            max_records_per_collection=100000,
        )
        started = time.perf_counter()
        loaded = list(restarted.load_records(_COLLECTION_ID))
        recovery_ms = (time.perf_counter() - started) * 1000.0

        print(
            f"PERF|write|N={n_records}|total_ms={write_ms:.1f}"
            f"|per_record_ms={write_ms / n_records:.3f}"
        )
        print(
            f"PERF|recovery|N={n_records}|load_ms={recovery_ms:.1f}"
            f"|per_record_ms={recovery_ms / n_records:.3f}"
        )
        print(
            f"PERF|disk|N={n_records}|bytes={size_bytes}"
            f"|bytes_per_record={size_bytes / n_records:.1f}"
        )

        # 恢复完整性：条目数 / 维度 / 首末采样往返一致（可复现，AC-31）
        assert len(loaded) == n_records
        meta = restarted.read_meta(_COLLECTION_ID)
        assert meta is not None
        assert meta.declared_dimension == DIMENSION
        diagnosis = restarted.diagnose(_COLLECTION_ID)
        assert diagnosis.ok
        assert diagnosis.record_count == n_records
        assert loaded[0].record_id == "r-000000"
        assert loaded[-1].record_id == f"r-{n_records - 1:06d}"
        assert loaded[0].vector == pytest.approx(records[0].vector, abs=1e-6)
        assert loaded[-1].vector == pytest.approx(records[-1].vector, abs=1e-6)
        # 写入/恢复为真实耗时；磁盘占用 ≥ 定长编码下界（D×4B/条）且未病态膨胀
        assert write_ms > 0.0
        assert recovery_ms > 0.0
        assert size_bytes >= n_records * DIMENSION * 4
        assert size_bytes < n_records * 4096

    def test_disk_footprint_linear_growth(self) -> None:
        """磁盘占用线性：文件大小随条目数单调增、单条占用跨档稳定。"""
        if len(self.disk_bytes) < len(TIERS):
            pytest.skip("须先完成三档写入用例（全量运行时生效）")
        size_1k = self.disk_bytes[1000]
        size_5k = self.disk_bytes[5000]
        size_10k = self.disk_bytes[10000]
        per_record = [
            size_1k / 1000,
            size_5k / 5000,
            size_10k / 10000,
        ]
        print(
            "PERF|disk_linear|bytes_per_record="
            + "|".join(f"{value:.1f}" for value in per_record)
        )
        assert size_10k > size_5k > size_1k
        assert max(per_record) / min(per_record) < 2.0
