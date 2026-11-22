# =============================================================================
# 本地向量数据库 sqlite 持久化层（integrations/local_vector_database/store.py）
#
# 职责：一集合一文件的 sqlite3 持久化（CHG-L04 / design DC-02、DC-08、§6.1）。
# 设计意图（对应 local_vector_database design INV-L06/L07/L10/L15、
# spec FR-03/FR-04/FR-08/FR-25/FR-27/FR-28）：
#   1. 落点 {storage_root}/{collection_id}.sqlite3：集合标识白名单校验
#      （禁路径穿越，INV-L10），一集合一文件天然隔离（INV-L06）；
#   2. DDL：collection_meta(key,value) + vector_records(record_id PK, vector BLOB,
#      vector_dim, text, source)；向量 struct 定长编码；source JSON 文本；
#      不依赖 SQL JSON1（过滤在 Python 侧完成）；
#   3. 幂等 upsert（ON CONFLICT(record_id) DO UPDATE，AC-04/AC-05）；
#      幂等 delete（不存在返回“未删除”，AC-06/AC-07）；
#   4. 首写固化维度（AC-20）；format_version 打开判定：同版本直读、
#      跨版本明确拒绝（AC-36，禁静默错读）；
#   5. 单条写/删 = 单事务（原子，AC-34）；WAL + synchronous=NORMAL：
#      进程崩溃后已提交数据可读（AC-37）；每操作短连接；单进程串行化写；
#   6. record_count 计数维护于 collection_meta（限流 O(1) 点查），
#      diagnose 交叉核验计数一致性（AC-35）；
#   7. 错误映射（design §6.3）：sqlite/os 失败 → VECTOR_DB_STORAGE_UNAVAILABLE；
#      消息为固定文本 + 非凭据定位（集合标识/维度/计数，INV-L09）。
# =============================================================================

import json
import math
import os
import re
import sqlite3
import struct
from datetime import datetime, timezone
from typing import Dict, Iterator, List, Optional, Tuple

from orchestration_platform.domain.errors import (
    VECTOR_DB_DIMENSION_MISMATCH,
    VECTOR_DB_FORMAT_VERSION_INCOMPATIBLE,
    VECTOR_DB_INVALID_COLLECTION_ID,
    VECTOR_DB_INVALID_VECTOR,
    VECTOR_DB_RECORD_LIMIT_EXCEEDED,
    VECTOR_DB_STORAGE_UNAVAILABLE,
)
from orchestration_platform.domain.models.common import JsonValue
from orchestration_platform.integrations.local_vector_database.models import (
    FORMAT_VERSION,
    CollectionMeta,
    DiagnosticReport,
    VectorRecord,
    resolve_embedding_kind,
)
from orchestration_platform.integrations.vector_knowledge_base.models import (
    CHUNK_TEXT_MAX_LENGTH,
    VectorRetrievalError,
)

# 集合标识白名单：小写字母/数字开头，仅含小写字母/数字/连字符/下划线，长度 1..128
_COLLECTION_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,127}$")

# 支持的度量取值域（与 models.MetricName 一致）
_ALLOWED_METRICS = ("cosine", "dot", "euclidean")

# 维度声明上界（FR-29/AC-38）
_DIMENSION_MAX = 4096


def _utc_now_iso() -> str:
    """UTC 时间戳（ISO-8601，集合元信息 created_at/updated_at）。"""
    return datetime.now(timezone.utc).isoformat()


def _validate_collection_id(collection_id: str) -> None:
    """集合标识白名单校验（禁路径穿越，INV-L10）。"""
    if not isinstance(collection_id, str) or not _COLLECTION_ID_PATTERN.match(
        collection_id or ""
    ):
        raise VectorRetrievalError(
            VECTOR_DB_INVALID_COLLECTION_ID,
            "集合标识非法（仅允许小写字母、数字、连字符、下划线，长度 1..128；禁路径穿越）",
        )


def _validate_dimension(dimension: int, collection_id: str) -> None:
    """维度声明校验：须为 1..4096 的整数（FR-29/AC-38）。"""
    if (
        isinstance(dimension, bool)
        or not isinstance(dimension, int)
        or dimension < 1
        or dimension > _DIMENSION_MAX
    ):
        raise VectorRetrievalError(
            VECTOR_DB_INVALID_VECTOR,
            f"声明维度超界（须为 1..{_DIMENSION_MAX} 的整数，集合：{collection_id}）",
        )


def _validate_vector_components(vector: List[float], collection_id: str) -> None:
    """向量分量校验：非空且全部有限（非 NaN/Inf，AC-20）。"""
    if not vector:
        raise VectorRetrievalError(
            VECTOR_DB_INVALID_VECTOR,
            f"向量分量为空（集合：{collection_id}）",
        )
    for component in vector:
        if isinstance(component, bool) or not isinstance(component, (int, float)):
            raise VectorRetrievalError(
                VECTOR_DB_INVALID_VECTOR,
                f"向量含非数值分量（集合：{collection_id}）",
            )
        if not math.isfinite(float(component)):
            raise VectorRetrievalError(
                VECTOR_DB_INVALID_VECTOR,
                f"向量含非有限分量（NaN/Inf，集合：{collection_id}）",
            )


class SqliteVectorStore:
    """sqlite3 单文件向量存储（一集合一文件，CHG-L04 / DC-02）。

    约定：
    - 集合文件不存在时由首次写入创建（首写固化维度，AC-20）；
    - 每操作使用短连接（打开 → 执行 → 关闭）；WAL + synchronous=NORMAL
      （进程崩溃后已提交数据可读，AC-37）；
    - 单进程串行化写（DC-08），不做多进程共享；
    - record_count 维护于 collection_meta（限流 O(1) 点查），diagnose
      交叉核验计数一致性（AC-35）；meta 缺计数时回退 COUNT(*) 自愈。
    """

    def __init__(
        self,
        storage_root: str,
        default_metric: str = "cosine",
        max_records_per_collection: int = 100000,
    ) -> None:
        if default_metric not in _ALLOWED_METRICS:
            raise ValueError(f"未知度量取值：{default_metric}")
        if max_records_per_collection < 1:
            raise ValueError("max_records_per_collection 须为正整数")
        self._storage_root = storage_root
        self._default_metric = default_metric
        self._max_records_per_collection = max_records_per_collection

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def ensure_collection(
        self, collection_id: str, dimension: int, metric: str
    ) -> CollectionMeta:
        """建集合/读元信息：首写固化维度（AC-20），跨版本明确拒绝（AC-36）。

        - 文件不存在 → 建库 + 建表 + 写 collection_meta（维度固化）；
        - 文件存在 → 读 meta 并校验 format_version；
          传入 dimension != declared_dimension → VECTOR_DB_DIMENSION_MISMATCH；
          已存在集合的度量以存量 meta 为准（返回真实元信息）。
        """
        _validate_collection_id(collection_id)
        _validate_dimension(dimension, collection_id)
        if metric not in _ALLOWED_METRICS:
            raise ValueError(f"未知度量取值：{metric}")
        path = self._collection_path(collection_id)
        if os.path.exists(path):
            meta = self._open_meta(path, collection_id)
            if meta.declared_dimension != dimension:
                raise VectorRetrievalError(
                    VECTOR_DB_DIMENSION_MISMATCH,
                    (
                        f"向量维度与集合声明不一致（集合：{collection_id}；"
                        f"声明维度：{meta.declared_dimension}；传入维度：{dimension}）"
                    ),
                )
            return meta
        self._ensure_storage_root()
        now = _utc_now_iso()
        meta_rows: Dict[str, str] = {
            "collection_id": collection_id,
            "declared_dimension": str(dimension),
            "metric": metric,
            "format_version": FORMAT_VERSION,
            "created_at": now,
            "updated_at": now,
            "record_count": "0",
        }
        try:
            conn = self._connect(path)
            try:
                with conn:
                    self._create_schema(conn)
                    conn.executemany(
                        "INSERT INTO collection_meta(key, value) VALUES(?, ?)",
                        list(meta_rows.items()),
                    )
            finally:
                conn.close()
        except sqlite3.Error:
            # 创建失败清理半初始化文件（不产生半初始化状态）
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass
            raise VectorRetrievalError(
                VECTOR_DB_STORAGE_UNAVAILABLE,
                f"本地向量数据库集合创建失败（集合：{collection_id}）",
            ) from None
        return CollectionMeta(
            collection_id=collection_id,
            declared_dimension=dimension,
            metric=metric,  # type: ignore[arg-type]
            format_version=FORMAT_VERSION,
            created_at=now,
            updated_at=now,
        )

    def upsert(self, collection_id: str, record: VectorRecord) -> bool:
        """幂等写入/更新：同 record_id = 覆盖更新（AC-04/AC-05/AC-20）。

        - 集合不存在 → 按首条记录维度自动建集合（首写固化维度）；
        - 维度不一致 / 非法向量 / 超条目上限 → 确定性拒绝（事务回滚，
          失败不污染集合）；
        - 文本超长 → 截断到 2000 并在 source 标注 truncated=true
          （对齐 sanitize_chunk_payload 净化语义）。
        """
        _validate_vector_components(record.vector, collection_id)
        path = self._collection_path(collection_id)
        if not os.path.exists(path):
            self.ensure_collection(
                collection_id, len(record.vector), self._default_metric
            )
        text, source_json = self._normalize_payload(record)
        blob = struct.pack(
            f"<{len(record.vector)}f", *(float(v) for v in record.vector)
        )
        try:
            conn = self._connect(path)
            try:
                with conn:
                    meta = self._load_meta(conn, collection_id)
                    self._ensure_embedding_kind_column(conn)
                    if len(record.vector) != meta.declared_dimension:
                        raise VectorRetrievalError(
                            VECTOR_DB_DIMENSION_MISMATCH,
                            (
                                f"向量维度与集合声明不一致（集合：{collection_id}；"
                                f"声明维度：{meta.declared_dimension}；"
                                f"实际维度：{len(record.vector)}）"
                            ),
                        )
                    existing = conn.execute(
                        "SELECT 1 FROM vector_records WHERE record_id = ?",
                        (record.record_id,),
                    ).fetchone()
                    new_count: Optional[int] = None
                    if existing is None:
                        count = self._read_record_count(conn)
                        if count + 1 > self._max_records_per_collection:
                            raise VectorRetrievalError(
                                VECTOR_DB_RECORD_LIMIT_EXCEEDED,
                                (
                                    f"单集合条目数超上限（集合：{collection_id}；"
                                    f"上限：{self._max_records_per_collection}）"
                                ),
                            )
                        new_count = count + 1
                    conn.execute(
                        "INSERT INTO vector_records(record_id, vector, vector_dim, "
                        "text, source, embedding_kind) VALUES(?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT(record_id) DO UPDATE SET vector=excluded.vector, "
                        "vector_dim=excluded.vector_dim, text=excluded.text, "
                        "source=excluded.source, embedding_kind=excluded.embedding_kind",
                        (
                            record.record_id,
                            blob,
                            len(record.vector),
                            text,
                            source_json,
                            (
                                record.embedding_kind
                                if record.embedding_kind in ("real", "hash")
                                else None
                            ),
                        ),
                    )
                    if new_count is not None:
                        self._write_record_count(conn, new_count)
                    conn.execute(
                        "UPDATE collection_meta SET value = ? WHERE key = 'updated_at'",
                        (_utc_now_iso(),),
                    )
            finally:
                conn.close()
        except sqlite3.Error:
            raise VectorRetrievalError(
                VECTOR_DB_STORAGE_UNAVAILABLE,
                f"本地向量数据库写入失败（集合：{collection_id}）",
            ) from None
        return True

    def delete(self, collection_id: str, record_id: str) -> bool:
        """幂等删除：返回是否实际删除（不存在 → False，不视为错误，AC-06/AC-07）。"""
        path = self._collection_path(collection_id)
        if not os.path.exists(path):
            # 集合不存在 → 无可删除（幂等成功，不建库、零副作用）
            return False
        deleted = False
        try:
            conn = self._connect(path)
            try:
                with conn:
                    self._load_meta(conn, collection_id)
                    cursor = conn.execute(
                        "DELETE FROM vector_records WHERE record_id = ?",
                        (record_id,),
                    )
                    deleted = cursor.rowcount == 1
                    if deleted:
                        count = self._read_record_count(conn)
                        self._write_record_count(conn, max(0, count - 1))
                        conn.execute(
                            "UPDATE collection_meta SET value = ? "
                            "WHERE key = 'updated_at'",
                            (_utc_now_iso(),),
                        )
            finally:
                conn.close()
        except sqlite3.Error:
            raise VectorRetrievalError(
                VECTOR_DB_STORAGE_UNAVAILABLE,
                f"本地向量数据库删除失败（集合：{collection_id}）",
            ) from None
        return deleted

    def load_records(self, collection_id: str) -> Iterator[VectorRecord]:
        """全量载入记录（按 record_id 升序，供暴力扫描）。

        集合不存在 → 空载入（空结果成功，INV-L15）；跨版本 → 明确拒绝。
        """
        path = self._collection_path(collection_id)
        if not os.path.exists(path):
            return
        conn: Optional[sqlite3.Connection] = None
        try:
            conn = self._connect(path)
            meta = self._load_meta(conn, collection_id)
            has_kind_column = self._has_embedding_kind_column(conn)
            select_sql = (
                "SELECT record_id, vector, vector_dim, text, source, embedding_kind "
                "FROM vector_records ORDER BY record_id"
                if has_kind_column
                else (
                    "SELECT record_id, vector, vector_dim, text, source, NULL "
                    "FROM vector_records ORDER BY record_id"
                )
            )
            cursor = conn.execute(select_sql)
            declared = meta.declared_dimension
            for record_id, blob, vector_dim, text, source_json, kind in cursor:
                if blob is None or vector_dim != declared:
                    # 维度不一致/编码损坏的记录跳过（diagnose 可见，不伪造）
                    continue
                if len(blob) != vector_dim * 4:
                    continue
                vector = list(struct.unpack(f"<{vector_dim}f", blob))
                source = json.loads(source_json) if source_json else None
                # 代际识别（DC-B2-05 ④）：显式字段 > source.provenance 推导 >
                # 存量缺省 hash（无信息记录不冒充真实语义，INV-B2-05）
                kind_resolved = resolve_embedding_kind(kind, source)
                yield VectorRecord(
                    record_id=record_id,
                    vector=vector,
                    text=text,
                    source=source,
                    embedding_kind=kind_resolved,  # type: ignore[arg-type]
                )
        except sqlite3.Error:
            raise VectorRetrievalError(
                VECTOR_DB_STORAGE_UNAVAILABLE,
                f"本地向量数据库读取失败（集合：{collection_id}）",
            ) from None
        finally:
            if conn is not None:
                conn.close()

    def format_version(self, collection_id: str) -> str:
        """读取集合持久化格式版本（集合不存在 → 空串；如实上报，不判定）。"""
        path = self._collection_path(collection_id)
        if not os.path.exists(path):
            return ""
        try:
            conn = self._connect(path)
            try:
                row = conn.execute(
                    "SELECT value FROM collection_meta WHERE key = 'format_version'"
                ).fetchone()
            finally:
                conn.close()
        except sqlite3.Error:
            raise VectorRetrievalError(
                VECTOR_DB_STORAGE_UNAVAILABLE,
                f"本地向量数据库集合文件不可读（集合：{collection_id}）",
            ) from None
        return str(row[0]) if row else ""

    def read_meta(self, collection_id: str) -> Optional[CollectionMeta]:
        """只读集合元信息（不建集合、零副作用；集合不存在 → None）。

        供检索内核获取声明维度（BruteForceIndex 维度校验）；
        打开/版本失败语义同 ensure_collection（STORAGE_UNAVAILABLE /
        FORMAT_VERSION_INCOMPATIBLE），集合缺失 → None（空结果成功，INV-L15）。
        """
        path = self._collection_path(collection_id)
        if not os.path.exists(path):
            return None
        return self._open_meta(path, collection_id)

    def diagnose(self, collection_id: str) -> DiagnosticReport:
        """一致性诊断（AC-35）：integrity_check + 维度一致性 + 计数 + 版本。

        诊断不抛异常：文件不可读/元信息损坏 → ok=False 报告（可诊断）。
        """
        path = self._collection_path(collection_id)
        if not os.path.exists(path):
            return DiagnosticReport(
                collection_id=collection_id,
                ok=False,
                integrity_check="collection_missing",
                dimension_consistent=False,
                declared_dimension=None,
                record_count=0,
                format_version="",
                issues=["集合不存在或尚未创建"],
            )
        try:
            conn = self._connect(path)
            try:
                integrity_rows = [
                    str(row[0])
                    for row in conn.execute("PRAGMA integrity_check").fetchall()
                ]
                integrity = "; ".join(integrity_rows) if integrity_rows else "unknown"
                issues: List[str] = []
                if integrity != "ok":
                    issues.append("完整性校验未通过")
                try:
                    meta = self._load_meta(conn, collection_id)
                except VectorRetrievalError as error:
                    # 元信息不可用（版本不兼容/损坏）→ 仍产出可诊断报告
                    issues.append(error.error_message)
                    count_row = conn.execute(
                        "SELECT COUNT(*) FROM vector_records"
                    ).fetchone()
                    return DiagnosticReport(
                        collection_id=collection_id,
                        ok=False,
                        integrity_check=integrity,
                        dimension_consistent=False,
                        declared_dimension=None,
                        record_count=int(count_row[0]) if count_row else 0,
                        format_version="",
                        issues=issues,
                    )
                declared = meta.declared_dimension
                count = int(
                    conn.execute("SELECT COUNT(*) FROM vector_records").fetchone()[0]
                )
                bad_dim = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM vector_records WHERE vector_dim != ?",
                        (declared,),
                    ).fetchone()[0]
                )
                meta_count = self._read_record_count(conn)
                dimension_consistent = bad_dim == 0
                if not dimension_consistent:
                    issues.append(
                        f"存在维度与声明不一致的记录（数量：{bad_dim}）"
                    )
                if meta_count != count:
                    issues.append(
                        f"元信息条目计数与实际不一致（元信息：{meta_count}；实际：{count}）"
                    )
                return DiagnosticReport(
                    collection_id=collection_id,
                    ok=(integrity == "ok")
                    and dimension_consistent
                    and meta_count == count,
                    integrity_check=integrity,
                    dimension_consistent=dimension_consistent,
                    declared_dimension=declared,
                    record_count=count,
                    format_version=meta.format_version,
                    issues=issues,
                )
            finally:
                conn.close()
        except (sqlite3.Error, VectorRetrievalError):
            return DiagnosticReport(
                collection_id=collection_id,
                ok=False,
                integrity_check="unreadable",
                dimension_consistent=False,
                declared_dimension=None,
                record_count=0,
                format_version="",
                issues=["集合文件不可读或已损坏"],
            )

    # ------------------------------------------------------------------
    # 内部：路径 / 连接 / schema / meta
    # ------------------------------------------------------------------

    def _collection_path(self, collection_id: str) -> str:
        _validate_collection_id(collection_id)
        return os.path.join(self._storage_root, f"{collection_id}.sqlite3")

    def _ensure_storage_root(self) -> None:
        try:
            os.makedirs(self._storage_root, exist_ok=True)
        except OSError:
            raise VectorRetrievalError(
                VECTOR_DB_STORAGE_UNAVAILABLE,
                "本地向量数据库存储根目录不可用（不可创建或不可写）",
            ) from None

    @staticmethod
    def _connect(path: str) -> sqlite3.Connection:
        try:
            conn = sqlite3.connect(str(path))
        except sqlite3.Error:
            raise VectorRetrievalError(
                VECTOR_DB_STORAGE_UNAVAILABLE,
                "本地向量数据库存储打开失败",
            ) from None
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.Error:
            conn.close()
            raise VectorRetrievalError(
                VECTOR_DB_STORAGE_UNAVAILABLE,
                "本地向量数据库存储初始化失败",
            ) from None
        return conn

    @staticmethod
    def _create_schema(conn: sqlite3.Connection) -> None:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS collection_meta ("
            "key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS vector_records ("
            "record_id TEXT PRIMARY KEY, vector BLOB NOT NULL, "
            "vector_dim INTEGER NOT NULL, text TEXT NOT NULL, source TEXT, "
            "embedding_kind TEXT)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_vector_records_dim "
            "ON vector_records(vector_dim)"
        )

    @staticmethod
    def _ensure_embedding_kind_column(conn: sqlite3.Connection) -> None:
        """embedding_kind 列增量迁移（B2-C5/DC-B2-05 ④）。

        存量文件零破坏（HC-05）：列缺失 → ALTER TABLE ADD COLUMN（NULL 默认，
        存量行不动）；已存在 → 零操作。仅附加列，不改既有列语义。
        """
        columns = {
            str(row[1]) for row in conn.execute(
                "PRAGMA table_info(vector_records)"
            ).fetchall()
        }
        if "embedding_kind" not in columns:
            conn.execute(
                "ALTER TABLE vector_records ADD COLUMN embedding_kind TEXT"
            )

    @staticmethod
    def _has_embedding_kind_column(conn: sqlite3.Connection) -> bool:
        columns = {
            str(row[1]) for row in conn.execute(
                "PRAGMA table_info(vector_records)"
            ).fetchall()
        }
        return "embedding_kind" in columns

    @staticmethod
    def _load_meta(conn: sqlite3.Connection, collection_id: str) -> CollectionMeta:
        rows = dict(
            conn.execute("SELECT key, value FROM collection_meta").fetchall()
        )
        return SqliteVectorStore._meta_from_rows(rows, collection_id)

    def _open_meta(self, path: str, collection_id: str) -> CollectionMeta:
        try:
            conn = self._connect(path)
            try:
                return self._load_meta(conn, collection_id)
            finally:
                conn.close()
        except sqlite3.Error:
            raise VectorRetrievalError(
                VECTOR_DB_STORAGE_UNAVAILABLE,
                f"本地向量数据库集合文件不可读（集合：{collection_id}）",
            ) from None

    @staticmethod
    def _meta_from_rows(rows: Dict[str, str], collection_id: str) -> CollectionMeta:
        version = rows.get("format_version", "")
        if version != FORMAT_VERSION:
            raise VectorRetrievalError(
                VECTOR_DB_FORMAT_VERSION_INCOMPATIBLE,
                (
                    f"集合持久化格式版本不兼容（集合：{collection_id}；"
                    f"文件版本：{version or '未知'}；支持版本：{FORMAT_VERSION}）"
                ),
            )
        try:
            return CollectionMeta(
                collection_id=collection_id,
                declared_dimension=int(rows["declared_dimension"]),
                metric=rows["metric"],
                format_version=version,
                created_at=rows["created_at"],
                updated_at=rows["updated_at"],
            )
        except Exception:
            raise VectorRetrievalError(
                VECTOR_DB_STORAGE_UNAVAILABLE,
                f"集合元信息缺失或损坏（集合：{collection_id}）",
            ) from None

    @staticmethod
    def _read_record_count(conn: sqlite3.Connection) -> int:
        row = conn.execute(
            "SELECT value FROM collection_meta WHERE key = 'record_count'"
        ).fetchone()
        if row is not None:
            try:
                return int(row[0])
            except (TypeError, ValueError):
                pass
        # meta 缺计数/计数损坏 → 回退全量计数（自愈路径）
        return int(conn.execute("SELECT COUNT(*) FROM vector_records").fetchone()[0])

    @staticmethod
    def _write_record_count(conn: sqlite3.Connection, count: int) -> None:
        conn.execute(
            "INSERT INTO collection_meta(key, value) VALUES('record_count', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(count),),
        )

    @staticmethod
    def _normalize_payload(record: VectorRecord) -> Tuple[str, Optional[str]]:
        """文本超长截断并标注（对齐 sanitize_chunk_payload 语义）；source 序列化。"""
        text = record.text
        source = record.source
        if len(text) > CHUNK_TEXT_MAX_LENGTH:
            text = text[:CHUNK_TEXT_MAX_LENGTH]
            annotated: Dict[str, JsonValue] = {"truncated": True}
            if source is not None:
                annotated.update(source)
                annotated["truncated"] = True
            source = annotated
        if source is None:
            return text, None
        return text, json.dumps(source, ensure_ascii=False)
