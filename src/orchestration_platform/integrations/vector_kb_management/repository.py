# =============================================================================
# 知识库元数据仓储（integrations/vector_kb_management/repository.py）
#
# 职责：独立 Protocol + sqlite3 单文件落点（CHG-06 / design DC-03）。
# 设计意图（vector_kb_management design INV-D11 / INV-D23 / DC-03）：
#   1. 独立落点：{storage_root}/knowledge_bases.sqlite3（落点经
#      config.storage_root 派生）；不并入 RepositoryFactory、不改
#      persistence/abstractions/repositories.py（DC-03；PG 双实现登记 P2）；
#   2. 四表（design §2.6.1 CHG-06）：knowledge_base / knowledge_base_document
#      / chunk_draft / ingestion_job；ON CONFLICT 幂等（同键覆盖，
#      created_at 首写固化不回改）；
#   3. 每操作短连接（打开 → 执行 → 关闭）+ WAL + synchronous=NORMAL
#      （对齐 local_vector_database/store.py 范式）；单进程串行化写；
#   4. 懒初始化（INV-D23）：构造零副作用；读操作不建库不建目录，
#      首次写入才 makedirs（仅经 storage_root 派生）+ 建表；
#   5. 损坏 JSON 自愈：行级载荷 JSON 损坏/不合法 → 跳过该行（get → None /
#      list 过滤），不抛错；库文件级损坏 → VECTOR_KB_STORAGE_UNAVAILABLE
#      （确定性可诊断，非静默错读）；
#   6. 路径安全：knowledge_base_id 白名单校验（双保险，design §2.3.3；
#      禁路径穿越，INV-D11）；sqlite3/os 失败 → 固定文本 + 零凭据。
# =============================================================================

import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any, List, Optional, Protocol, Tuple, runtime_checkable

from pydantic import ValidationError

from orchestration_platform.domain.errors import (
    VECTOR_KB_INVALID_KNOWLEDGE_BASE_ID,
    VECTOR_KB_STORAGE_UNAVAILABLE,
    DomainError,
    WorkflowValidationError,
)
from orchestration_platform.integrations.vector_kb_management.models import (
    KNOWLEDGE_BASE_ID_PATTERN,
    AnnotationAuditRecord,
    ChunkDraftState,
    ChunkRecord,
    ChunkStrategyConfig,
    IngestionJob,
    IngestionResult,
    KBEmbeddingBinding,
    KnowledgeBase,
    KnowledgeBaseDocument,
)

# 标识白名单（复用 models.KNOWLEDGE_BASE_ID_PATTERN；禁路径穿越，INV-D11）
_IDENTIFIER_PATTERN = re.compile(KNOWLEDGE_BASE_ID_PATTERN)


def _utc_now_iso() -> str:
    """UTC 时间戳（ISO-8601，技术列 created_at/updated_at）。"""
    return datetime.now(timezone.utc).isoformat()


def _validate_knowledge_base_id(knowledge_base_id: str) -> None:
    """知识库标识白名单校验（双保险，design §2.3.3；禁路径穿越）。"""
    if not isinstance(knowledge_base_id, str) or not _IDENTIFIER_PATTERN.match(
        knowledge_base_id or ""
    ):
        raise WorkflowValidationError(
            VECTOR_KB_INVALID_KNOWLEDGE_BASE_ID,
            "知识库标识非法（仅允许小写字母、数字、连字符、下划线，"
            "长度 1..128；禁路径穿越）",
        )


@runtime_checkable
class IKnowledgeBaseRepository(Protocol):
    """知识库元数据仓储协议（DC-03，同步方法）。

    - 知识库 CRUD：save / get / list_all / delete / exists；
    - 技术时间戳：get_timestamps（created_at/updated_at 由落盘层承载，
      不入域模型，design §2.3.2 注）；
    - 文档 / 草稿 / 作业存取（design §2.6.1 CHG-06）；
    - 读取语义：库文件未创建 → 空结果（None / [] / False，零副作用）；
      载荷损坏行 → 跳过（自愈，不抛错）。
    """

    def save(self, knowledge_base: KnowledgeBase) -> None:
        ...

    def get(self, knowledge_base_id: str) -> Optional[KnowledgeBase]:
        ...

    def list_all(self) -> List[KnowledgeBase]:
        ...

    def exists(self, knowledge_base_id: str) -> bool:
        ...

    def delete(self, knowledge_base_id: str) -> bool:
        ...

    def get_timestamps(
        self, knowledge_base_id: str
    ) -> Optional[Tuple[str, str]]:
        ...

    def save_document(
        self,
        knowledge_base_id: str,
        document: KnowledgeBaseDocument,
        text: str = "",
    ) -> None:
        ...

    def get_document(
        self, document_id: str
    ) -> Optional[KnowledgeBaseDocument]:
        ...

    def list_documents(
        self, knowledge_base_id: str
    ) -> List[KnowledgeBaseDocument]:
        ...

    def set_confirmed_version(
        self, document_id: str, confirmed_version: str
    ) -> None:
        ...

    def save_draft(self, draft: ChunkDraftState) -> None:
        ...

    def get_draft(
        self, document_id: str, draft_version: Optional[str] = None
    ) -> Optional[ChunkDraftState]:
        ...

    def save_job(self, job: IngestionJob) -> None:
        ...

    def get_job(self, job_id: str) -> Optional[IngestionJob]:
        ...

    def save_annotation_audit(self, audit: AnnotationAuditRecord) -> None:
        ...

    def list_annotation_audits(
        self, document_id: str
    ) -> List[AnnotationAuditRecord]:
        ...


class SqliteKnowledgeBaseRepository:
    """sqlite3 单文件知识库元数据仓储（CHG-06 / DC-03 唯一实现）。

    约定（对齐 local_vector_database/store.py）：
    - 每操作短连接；WAL + synchronous=NORMAL；单进程串行化写；
    - 幂等保存：ON CONFLICT 同键覆盖（created_at 首写固化，updated_at 推进）；
    - 删除知识库 → 同事务级联清理文档 / 草稿 / 作业（不留孤儿元数据）；
    - 草稿多版本共存：PK(document_id, draft_version)；缺省取最新版本
      （数值型版本串按 长度 + 字典序 取最大，"v9" < "v10"）。
    """

    def __init__(self, storage_root: str) -> None:
        self._storage_root = storage_root
        self._db_path = os.path.join(storage_root, "knowledge_bases.sqlite3")

    # ------------------------------------------------------------------
    # 知识库 CRUD
    # ------------------------------------------------------------------

    def save(self, knowledge_base: KnowledgeBase) -> None:
        """幂等保存知识库（同键覆盖；created_at 固化，updated_at 推进）。"""
        _validate_knowledge_base_id(knowledge_base.knowledge_base_id)
        binding_json = knowledge_base.embedding_model.model_dump_json()
        now = _utc_now_iso()
        conn = self._open_for_write()
        try:
            with conn:
                conn.execute(
                    "INSERT INTO knowledge_base(knowledge_base_id, name, "
                    "embedding_binding_json, default_chunk_strategy, "
                    "created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(knowledge_base_id) DO UPDATE SET "
                    "name=excluded.name, "
                    "embedding_binding_json=excluded.embedding_binding_json, "
                    "default_chunk_strategy=excluded.default_chunk_strategy, "
                    "updated_at=excluded.updated_at",
                    (
                        knowledge_base.knowledge_base_id,
                        knowledge_base.name,
                        binding_json,
                        knowledge_base.default_chunk_strategy,
                        now,
                        now,
                    ),
                )
        except sqlite3.Error:
            raise DomainError(
                VECTOR_KB_STORAGE_UNAVAILABLE,
                "知识库元数据写入失败（knowledge_bases.sqlite3）",
            ) from None
        finally:
            conn.close()

    def get(self, knowledge_base_id: str) -> Optional[KnowledgeBase]:
        """读取知识库（缺失/载荷损坏 → None，不抛错）。"""
        _validate_knowledge_base_id(knowledge_base_id)
        row = self._query_one(
            "SELECT knowledge_base_id, name, embedding_binding_json, "
            "default_chunk_strategy FROM knowledge_base "
            "WHERE knowledge_base_id = ?",
            (knowledge_base_id,),
        )
        if row is None:
            return None
        try:
            return KnowledgeBase(
                knowledge_base_id=row[0],
                name=row[1],
                embedding_model=KBEmbeddingBinding.model_validate_json(row[2]),
                default_chunk_strategy=row[3],
            )
        except (ValidationError, ValueError):
            return None  # 损坏 JSON 自愈（跳过不抛错）

    def list_all(self) -> List[KnowledgeBase]:
        """全量知识库（按标识升序；损坏行跳过）。"""
        rows = self._query_all(
            "SELECT knowledge_base_id, name, embedding_binding_json, "
            "default_chunk_strategy FROM knowledge_base "
            "ORDER BY knowledge_base_id",
            (),
        )
        entities: List[KnowledgeBase] = []
        for row in rows:
            try:
                entities.append(
                    KnowledgeBase(
                        knowledge_base_id=row[0],
                        name=row[1],
                        embedding_model=KBEmbeddingBinding.model_validate_json(
                            row[2]
                        ),
                        default_chunk_strategy=row[3],
                    )
                )
            except (ValidationError, ValueError):
                continue  # 损坏 JSON 自愈（跳过不抛错）
        return entities

    def exists(self, knowledge_base_id: str) -> bool:
        """存在性判定（行存在即 True；载荷损坏不影响存在性）。"""
        _validate_knowledge_base_id(knowledge_base_id)
        return (
            self._query_one(
                "SELECT 1 FROM knowledge_base WHERE knowledge_base_id = ?",
                (knowledge_base_id,),
            )
            is not None
        )

    def delete(self, knowledge_base_id: str) -> bool:
        """删除知识库并同事务级联清理文档/草稿/作业（幂等：未删返回 False）。"""
        _validate_knowledge_base_id(knowledge_base_id)
        if not os.path.exists(self._db_path):
            return False
        try:
            conn = self._connect()
            try:
                with conn:
                    cursor = conn.execute(
                        "DELETE FROM knowledge_base "
                        "WHERE knowledge_base_id = ?",
                        (knowledge_base_id,),
                    )
                    if cursor.rowcount == 0:
                        return False
                    conn.execute(
                        "DELETE FROM knowledge_base_document "
                        "WHERE knowledge_base_id = ?",
                        (knowledge_base_id,),
                    )
                    conn.execute(
                        "DELETE FROM chunk_draft WHERE knowledge_base_id = ?",
                        (knowledge_base_id,),
                    )
                    conn.execute(
                        "DELETE FROM ingestion_job "
                        "WHERE knowledge_base_id = ?",
                        (knowledge_base_id,),
                    )
                    return True
            finally:
                conn.close()
        except sqlite3.Error:
            raise DomainError(
                VECTOR_KB_STORAGE_UNAVAILABLE,
                "知识库元数据删除失败（knowledge_bases.sqlite3）",
            ) from None

    def get_timestamps(
        self, knowledge_base_id: str
    ) -> Optional[Tuple[str, str]]:
        """技术时间戳（created_at, updated_at；缺失 → None）。"""
        _validate_knowledge_base_id(knowledge_base_id)
        row = self._query_one(
            "SELECT created_at, updated_at FROM knowledge_base "
            "WHERE knowledge_base_id = ?",
            (knowledge_base_id,),
        )
        if row is None:
            return None
        return str(row[0]), str(row[1])

    # ------------------------------------------------------------------
    # 文档 / 草稿 / 作业存取
    # ------------------------------------------------------------------

    def save_document(
        self,
        knowledge_base_id: str,
        document: KnowledgeBaseDocument,
        text: str = "",
    ) -> None:
        """幂等保存文档元数据（DC-03 表含 text 列：解析全文随文档持久化）。"""
        _validate_knowledge_base_id(knowledge_base_id)
        strategy_json = document.chunk_strategy.model_dump_json()
        now = _utc_now_iso()
        conn = self._open_for_write()
        try:
            with conn:
                conn.execute(
                    "INSERT INTO knowledge_base_document(document_id, "
                    "knowledge_base_id, file_name, format, parse_status, "
                    "chunk_strategy_json, text, confirmed_version, "
                    "created_at) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(document_id) DO UPDATE SET "
                    "knowledge_base_id=excluded.knowledge_base_id, "
                    "file_name=excluded.file_name, format=excluded.format, "
                    "parse_status=excluded.parse_status, "
                    "chunk_strategy_json=excluded.chunk_strategy_json, "
                    "text=excluded.text, "
                    "confirmed_version=excluded.confirmed_version",
                    (
                        document.document_id,
                        knowledge_base_id,
                        document.file_name,
                        document.format,
                        document.parse_status,
                        strategy_json,
                        text,
                        document.confirmed_version,
                        now,
                    ),
                )
        except sqlite3.Error:
            raise DomainError(
                VECTOR_KB_STORAGE_UNAVAILABLE,
                "文档元数据写入失败（knowledge_bases.sqlite3）",
            ) from None
        finally:
            conn.close()

    def get_document(
        self, document_id: str
    ) -> Optional[KnowledgeBaseDocument]:
        """读取文档元数据（缺失/载荷损坏 → None，不抛错）。"""
        row = self._query_one(
            "SELECT document_id, knowledge_base_id, file_name, format, "
            "parse_status, chunk_strategy_json, confirmed_version "
            "FROM knowledge_base_document WHERE document_id = ?",
            (document_id,),
        )
        if row is None:
            return None
        try:
            return KnowledgeBaseDocument(
                document_id=row[0],
                knowledge_base_id=row[1],
                file_name=row[2],
                format=row[3],
                parse_status=row[4],
                chunk_strategy=ChunkStrategyConfig.model_validate_json(row[5]),
                confirmed_version=row[6],
            )
        except (ValidationError, ValueError):
            return None  # 损坏 JSON 自愈（跳过不抛错）

    def list_documents(
        self, knowledge_base_id: str
    ) -> List[KnowledgeBaseDocument]:
        """列出知识库下全部文档（按标识升序；损坏行跳过）。"""
        _validate_knowledge_base_id(knowledge_base_id)
        rows = self._query_all(
            "SELECT document_id, knowledge_base_id, file_name, format, "
            "parse_status, chunk_strategy_json, confirmed_version "
            "FROM knowledge_base_document "
            "WHERE knowledge_base_id = ? ORDER BY document_id",
            (knowledge_base_id,),
        )
        documents: List[KnowledgeBaseDocument] = []
        for row in rows:
            try:
                documents.append(
                    KnowledgeBaseDocument(
                        document_id=row[0],
                        knowledge_base_id=row[1],
                        file_name=row[2],
                        format=row[3],
                        parse_status=row[4],
                        chunk_strategy=ChunkStrategyConfig.model_validate_json(
                            row[5]
                        ),
                        confirmed_version=row[6],
                    )
                )
            except (ValidationError, ValueError):
                continue  # 损坏 JSON 自愈（跳过不抛错）
        return documents

    def set_confirmed_version(
        self, document_id: str, confirmed_version: str
    ) -> None:
        """回填生效基线版本（B5：最近成功确认的草稿版本；行缺失为幂等 no-op）。"""
        conn = self._open_for_write()
        try:
            with conn:
                conn.execute(
                    "UPDATE knowledge_base_document "
                    "SET confirmed_version = ? WHERE document_id = ?",
                    (confirmed_version, document_id),
                )
        except sqlite3.Error:
            raise DomainError(
                VECTOR_KB_STORAGE_UNAVAILABLE,
                "文档生效基线回填失败（knowledge_bases.sqlite3）",
            ) from None
        finally:
            conn.close()

    def save_draft(self, draft: ChunkDraftState) -> None:
        """幂等保存草稿（PK(document_id, draft_version)；同版本覆盖）。"""
        _validate_knowledge_base_id(draft.knowledge_base_id)
        chunks_json = json.dumps(
            [record.model_dump() for record in draft.chunks],
            ensure_ascii=False,
        )
        notices_json = json.dumps(draft.notices, ensure_ascii=False)
        now = _utc_now_iso()
        conn = self._open_for_write()
        try:
            with conn:
                conn.execute(
                    "INSERT INTO chunk_draft(knowledge_base_id, document_id, "
                    "draft_version, base_version, chunks_json, notices_json, "
                    "strategy_effective, created_at) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(document_id, draft_version) DO UPDATE SET "
                    "knowledge_base_id=excluded.knowledge_base_id, "
                    "base_version=excluded.base_version, "
                    "chunks_json=excluded.chunks_json, "
                    "notices_json=excluded.notices_json, "
                    "strategy_effective=excluded.strategy_effective",
                    (
                        draft.knowledge_base_id,
                        draft.document_id,
                        draft.draft_version,
                        draft.base_version,
                        chunks_json,
                        notices_json,
                        draft.strategy_effective,
                        now,
                    ),
                )
        except sqlite3.Error:
            raise DomainError(
                VECTOR_KB_STORAGE_UNAVAILABLE,
                "草稿写入失败（knowledge_bases.sqlite3）",
            ) from None
        finally:
            conn.close()

    def get_draft(
        self, document_id: str, draft_version: Optional[str] = None
    ) -> Optional[ChunkDraftState]:
        """读取草稿（缺省取最新版本；缺失/载荷损坏 → None，不抛错）。"""
        if draft_version is not None:
            row = self._query_one(
                "SELECT knowledge_base_id, document_id, draft_version, "
                "base_version, chunks_json, notices_json, strategy_effective "
                "FROM chunk_draft WHERE document_id = ? AND draft_version = ?",
                (document_id, draft_version),
            )
        else:
            # 最新草稿：数值型版本串按（长度, 字典序）取最大（"v9" < "v10"）
            row = self._query_one(
                "SELECT knowledge_base_id, document_id, draft_version, "
                "base_version, chunks_json, notices_json, strategy_effective "
                "FROM chunk_draft WHERE document_id = ? "
                "ORDER BY LENGTH(draft_version) DESC, draft_version DESC "
                "LIMIT 1",
                (document_id,),
            )
        if row is None:
            return None
        try:
            chunks = [
                ChunkRecord.model_validate(item) for item in json.loads(row[4])
            ]
            return ChunkDraftState(
                knowledge_base_id=row[0],
                document_id=row[1],
                draft_version=row[2],
                base_version=row[3],
                chunks=chunks,
                notices=json.loads(row[5]),
                strategy_effective=row[6],
            )
        except (ValidationError, ValueError):
            return None  # 损坏 JSON 自愈（跳过不抛错）

    def save_job(self, job: IngestionJob) -> None:
        """幂等保存摄取作业（同 job_id 覆盖；created_at 固化）。"""
        _validate_knowledge_base_id(job.knowledge_base_id)
        result_json = (
            job.result.model_dump_json() if job.result is not None else None
        )
        now = _utc_now_iso()
        conn = self._open_for_write()
        try:
            with conn:
                conn.execute(
                    "INSERT INTO ingestion_job(job_id, knowledge_base_id, "
                    "document_id, status, result_json, provenance, "
                    "local_degraded, created_at, updated_at) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(job_id) DO UPDATE SET "
                    "status=excluded.status, "
                    "result_json=excluded.result_json, "
                    "provenance=excluded.provenance, "
                    "local_degraded=excluded.local_degraded, "
                    "updated_at=excluded.updated_at",
                    (
                        job.job_id,
                        job.knowledge_base_id,
                        job.document_id,
                        job.status,
                        result_json,
                        job.provenance,
                        int(job.local_degraded),
                        now,
                        now,
                    ),
                )
        except sqlite3.Error:
            raise DomainError(
                VECTOR_KB_STORAGE_UNAVAILABLE,
                "摄取作业写入失败（knowledge_bases.sqlite3）",
            ) from None
        finally:
            conn.close()

    def get_job(self, job_id: str) -> Optional[IngestionJob]:
        """读取摄取作业（缺失/载荷损坏 → None，不抛错）。"""
        row = self._query_one(
            "SELECT job_id, knowledge_base_id, document_id, status, "
            "result_json, provenance, local_degraded FROM ingestion_job "
            "WHERE job_id = ?",
            (job_id,),
        )
        if row is None:
            return None
        try:
            result = (
                IngestionResult.model_validate_json(row[4]) if row[4] else None
            )
            return IngestionJob(
                job_id=row[0],
                knowledge_base_id=row[1],
                document_id=row[2],
                status=row[3],
                result=result,
                provenance=row[5],
                local_degraded=bool(row[6]),
            )
        except (ValidationError, ValueError):
            return None  # 损坏 JSON 自愈（跳过不抛错）

    # ------------------------------------------------------------------
    # 标注审计（vkb_doc_annotation / CHG-04 / DC-14；零凭据）
    # ------------------------------------------------------------------

    def save_annotation_audit(self, audit: AnnotationAuditRecord) -> None:
        """保存标注操作审计条目（幂等同键覆盖；零凭据）。"""
        detail_json = json.dumps(audit.detail, ensure_ascii=False)
        conn = self._open_for_write()
        try:
            with conn:
                conn.execute(
                    "INSERT INTO annotation_audit(audit_id, document_id, "
                    "action, scope, affected_count, executed_at, detail_json) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(audit_id) DO UPDATE SET "
                    "document_id=excluded.document_id, "
                    "action=excluded.action, scope=excluded.scope, "
                    "affected_count=excluded.affected_count, "
                    "executed_at=excluded.executed_at, "
                    "detail_json=excluded.detail_json",
                    (
                        audit.audit_id,
                        audit.document_id,
                        audit.action,
                        audit.scope,
                        int(audit.affected_count),
                        audit.executed_at,
                        detail_json,
                    ),
                )
        except sqlite3.Error:
            raise DomainError(
                VECTOR_KB_STORAGE_UNAVAILABLE,
                "标注审计写入失败（knowledge_bases.sqlite3）",
            ) from None
        finally:
            conn.close()

    def list_annotation_audits(
        self, document_id: str
    ) -> List[AnnotationAuditRecord]:
        """读取指定文档的标注审计条目（时间倒序；损坏行跳过自愈）。"""
        rows = self._query_all(
            "SELECT audit_id, document_id, action, scope, affected_count, "
            "executed_at, detail_json FROM annotation_audit "
            "WHERE document_id = ? ORDER BY executed_at DESC",
            (document_id,),
        )
        records: List[AnnotationAuditRecord] = []
        for row in rows:
            try:
                records.append(
                    AnnotationAuditRecord(
                        audit_id=row[0],
                        document_id=row[1],
                        action=row[2],
                        scope=row[3],
                        affected_count=int(row[4] or 0),
                        executed_at=row[5],
                        detail=json.loads(row[6]) if row[6] else {},
                    )
                )
            except (ValidationError, ValueError, TypeError):
                continue  # 损坏 JSON 自愈（跳过不抛错）
        return records

    # ------------------------------------------------------------------
    # 内部：连接 / schema / 查询
    # ------------------------------------------------------------------

    def _open_for_write(self) -> sqlite3.Connection:
        """写路径就绪：makedirs（仅经 storage_root 派生）+ 建库建表。"""
        try:
            os.makedirs(self._storage_root, exist_ok=True)
        except OSError:
            raise DomainError(
                VECTOR_KB_STORAGE_UNAVAILABLE,
                "知识库元数据落点目录不可用（不可创建或不可写）",
            ) from None
        file_existed = os.path.exists(self._db_path)
        conn = self._connect()
        try:
            with conn:
                self._create_schema(conn)
        except sqlite3.Error:
            conn.close()
            if not file_existed:
                # 新建失败清理半初始化文件（不产生半初始化状态；
                # 既有文件保留，避免误删存量数据）
                try:
                    os.remove(self._db_path)
                except OSError:
                    pass
            raise DomainError(
                VECTOR_KB_STORAGE_UNAVAILABLE,
                "知识库元数据库初始化失败（knowledge_bases.sqlite3）",
            ) from None
        return conn

    def _connect(self) -> sqlite3.Connection:
        try:
            conn = sqlite3.connect(str(self._db_path))
        except sqlite3.Error:
            raise DomainError(
                VECTOR_KB_STORAGE_UNAVAILABLE,
                "知识库元数据存储打开失败",
            ) from None
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.Error:
            conn.close()
            raise DomainError(
                VECTOR_KB_STORAGE_UNAVAILABLE,
                "知识库元数据存储初始化失败",
            ) from None
        return conn

    def _query_one(
        self, sql: str, params: Tuple[Any, ...]
    ) -> Optional[Tuple[Any, ...]]:
        """单行查询（库文件未创建 → None；sqlite 失败 → STORAGE_UNAVAILABLE）。"""
        if not os.path.exists(self._db_path):
            return None
        try:
            conn = self._connect()
            try:
                return conn.execute(sql, params).fetchone()
            finally:
                conn.close()
        except sqlite3.Error:
            raise DomainError(
                VECTOR_KB_STORAGE_UNAVAILABLE,
                "知识库元数据读取失败（knowledge_bases.sqlite3）",
            ) from None

    def _query_all(
        self, sql: str, params: Tuple[Any, ...]
    ) -> List[Tuple[Any, ...]]:
        """多行查询（库文件未创建 → []；sqlite 失败 → STORAGE_UNAVAILABLE）。"""
        if not os.path.exists(self._db_path):
            return []
        try:
            conn = self._connect()
            try:
                return conn.execute(sql, params).fetchall()
            finally:
                conn.close()
        except sqlite3.Error:
            raise DomainError(
                VECTOR_KB_STORAGE_UNAVAILABLE,
                "知识库元数据读取失败（knowledge_bases.sqlite3）",
            ) from None

    @staticmethod
    def _create_schema(conn: sqlite3.Connection) -> None:
        """四表 DDL（design §2.6.1 CHG-06；幂等建表）。"""
        conn.execute(
            "CREATE TABLE IF NOT EXISTS knowledge_base ("
            "knowledge_base_id TEXT PRIMARY KEY, name TEXT NOT NULL, "
            "embedding_binding_json TEXT NOT NULL, "
            "default_chunk_strategy TEXT, "
            "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS knowledge_base_document ("
            "document_id TEXT PRIMARY KEY, "
            "knowledge_base_id TEXT NOT NULL, "
            "file_name TEXT NOT NULL, format TEXT NOT NULL, "
            "parse_status TEXT NOT NULL, "
            "chunk_strategy_json TEXT NOT NULL, "
            "text TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, "
            "confirmed_version TEXT)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS chunk_draft ("
            "knowledge_base_id TEXT NOT NULL, "
            "document_id TEXT NOT NULL, draft_version TEXT NOT NULL, "
            "base_version TEXT NOT NULL, chunks_json TEXT NOT NULL, "
            "notices_json TEXT NOT NULL DEFAULT '[]', "
            "strategy_effective TEXT NOT NULL, created_at TEXT NOT NULL, "
            "PRIMARY KEY (document_id, draft_version))"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS ingestion_job ("
            "job_id TEXT PRIMARY KEY, knowledge_base_id TEXT NOT NULL, "
            "document_id TEXT NOT NULL, status TEXT NOT NULL, "
            "result_json TEXT, provenance TEXT, "
            "local_degraded INTEGER NOT NULL DEFAULT 0, "
            "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS annotation_audit ("
            "audit_id TEXT PRIMARY KEY, document_id TEXT NOT NULL, "
            "action TEXT NOT NULL, scope TEXT, "
            "affected_count INTEGER NOT NULL DEFAULT 0, "
            "executed_at TEXT NOT NULL, "
            "detail_json TEXT NOT NULL DEFAULT '{}')"
        )
