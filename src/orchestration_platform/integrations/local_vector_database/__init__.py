# =============================================================================
# 本地向量数据库子包（integrations/local_vector_database/__init__.py）
#
# 职责：包声明与公共导出（CHG-L01 / design DC-01）。
# 契约复用说明（INV-L03）：本组件出口唯一形态为既有
# vector_knowledge_base.models 的 Chunk/RetrievalOutcome（经 service 组装
# 并导出 LocalVectorDatabaseService）；本子包不新造平行契约，
# 内部模型仅用于存储与计算。
# 约束（INV-L02）：无重依赖顶层导入——faiss/qdrant_client/
# sentence_transformers 仅方法内延迟导入。
# =============================================================================

from orchestration_platform.integrations.local_vector_database.config import (
    LocalVectorDatabaseConfig,
)
from orchestration_platform.integrations.local_vector_database.config import (
    from_settings as config_from_settings,
)
from orchestration_platform.integrations.local_vector_database.models import (
    CollectionMeta,
    DeleteReport,
    DiagnosticReport,
    ExportReport,
    ImportReport,
    MetadataFilter,
    RecordFailure,
    UpsertReport,
    VectorRecord,
    VectorSearchHit,
)
from orchestration_platform.integrations.local_vector_database.service import (
    LocalVectorDatabaseService,
)
from orchestration_platform.integrations.local_vector_database.pg_array_store import (
    PgArrayVectorStore,
)
from orchestration_platform.integrations.local_vector_database.pg_vector_probe import (
    PgVectorExtensionProbe,
)

__all__ = [
    "CollectionMeta",
    "DeleteReport",
    "DiagnosticReport",
    "ExportReport",
    "ImportReport",
    "LocalVectorDatabaseConfig",
    "LocalVectorDatabaseService",
    "MetadataFilter",
    "PgArrayVectorStore",
    "PgVectorExtensionProbe",
    "RecordFailure",
    "UpsertReport",
    "VectorRecord",
    "VectorSearchHit",
    "config_from_settings",
]
