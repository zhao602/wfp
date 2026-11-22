# =============================================================================
# 本地向量数据库配置派生（integrations/local_vector_database/config.py）
#
# 职责：从运行期 settings 只读派生本组件配置（CHG-L03 / design DC-07）。
# 设计意图（对应 local_vector_database design INV-L02 / INV-L05 / AC-28）：
#   1. 懒初始化：开关 enabled=False 时构造零副作用——不触盘、不探测依赖、
#      不 import 可选依赖；本模块自身不执行任何 I/O；
#   2. 容错读取：经 _read_setting(name, default)（对齐既有 retriever 范式，
#      独立实现、不跨层引用），settings 字段不存在（B6 落地前）→ 缺省值，
#      不崩溃；
#   3. 注入式构造：测试可整体注入 LocalVectorDatabaseConfig，绕开全局
#      @lru_cache（改 .env 须重启进程，HR-7）。
# =============================================================================

from typing import Any

from pydantic import BaseModel, Field

from orchestration_platform.integrations.local_vector_database.models import MetricName


def _read_setting(name: str, default: Any) -> Any:
    """容错读取运行期配置（字段不存在/配置源不可用时返回缺省值）。"""
    try:
        from orchestration_platform.configuration.settings import get_settings

        return getattr(get_settings(), name, default)
    except Exception:
        return default


class LocalVectorDatabaseConfig(BaseModel):
    """本组件只读配置（settings 只读派生，懒初始化，AC-28）。

    字段与 settings.local_vector_database_* 一一对应（B6 落地；落地前经
    容错读取取缺省值，不得因字段未落地崩溃）。
    """

    enabled: bool = Field(default=False, description="功能开关（默认 False，INV-L05）")
    storage_root: str = Field(
        default="./.data/vector_kb", description="持久化根目录（落点边界，INV-L10）"
    )
    default_dimension: int = Field(default=128, ge=1, le=4096, description="默认向量维度")
    default_metric: MetricName = Field(default="cosine", description="默认相似度度量")
    backend: str = Field(default="", description="可选增强后端声明（空=纯暴力检索）")
    query_timeout_ms: int = Field(default=1000, ge=1, description="检索单次超时（毫秒）")
    max_records_per_collection: int = Field(
        default=100000, ge=1, description="单集合条目上限（FR-29）"
    )
    max_top_k: int = Field(default=100, ge=1, le=1000, description="top_k 上限")
    storage: str = Field(
        default="sqlite",
        description="存储路径（sqlite=存量默认零变化；pg_array=可选 PostgreSQL 原生数组路径，B2-C5/DC-B2-05）",
    )


def from_settings() -> LocalVectorDatabaseConfig:
    """从运行期 settings 只读派生配置（容错读取，懒初始化零副作用）。"""
    return LocalVectorDatabaseConfig(
        enabled=bool(_read_setting("local_vector_database_enabled", False)),
        storage_root=str(_read_setting("local_vector_database_storage_root", "./.data/vector_kb")),
        default_dimension=int(_read_setting("local_vector_database_default_dimension", 128)),
        default_metric=_read_setting("local_vector_database_default_metric", "cosine"),
        backend=str(_read_setting("local_vector_database_backend", "")),
        query_timeout_ms=int(_read_setting("local_vector_database_query_timeout_ms", 1000)),
        max_records_per_collection=int(
            _read_setting("local_vector_database_max_records_per_collection", 100000)
        ),
        max_top_k=int(_read_setting("local_vector_database_max_top_k", 100)),
        storage=str(_read_setting("local_vector_database_storage", "sqlite")),
    )
