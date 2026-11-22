# =============================================================================
# 向量知识库管理配置派生（integrations/vector_kb_management/config.py）
#
# 职责：从运行期 settings 只读派生本组件配置（CHG-05 / design DC-10）。
# 设计意图（vector_kb_management design INV-D04 / INV-D15 / INV-D23）：
#   1. 懒初始化：开关 enabled=False 时构造零副作用——不触盘、不探测依赖、
#      不 import 可选依赖；本模块自身不执行任何 I/O；
#   2. 容错读取：经 _read_setting(name, default)（对齐 local_vector_database
#      /config.py 范式，独立实现），settings 字段不存在（B7 落地前）→ 缺省值，
#      不崩溃；
#   3. 注入式构造：测试可整体注入 VectorKbManagementConfig，绕开全局
#      @lru_cache（改 .env 须重启进程）。
# =============================================================================

from typing import Any

from pydantic import BaseModel, Field


def _read_setting(name: str, default: Any) -> Any:
    """容错读取运行期配置（字段不存在/配置源不可用时返回缺省值）。"""
    try:
        from orchestration_platform.configuration.settings import get_settings

        return getattr(get_settings(), name, default)
    except Exception:
        return default


class VectorKbManagementConfig(BaseModel):
    """本组件只读配置（settings 只读派生，懒初始化）。

    字段与 settings.vector_kb_management_* 一一对应（B7 落地；落地前经
    容错读取取缺省值，不得因字段未落地崩溃）。
    """

    enabled: bool = Field(default=False, description="功能开关（默认 False，INV-D04）")
    storage_root: str = Field(
        default="./.data/vector_kb_management",
        description="元数据落点根目录（独立 sqlite3 落点，INV-D11）",
    )
    max_file_size_mb: int = Field(
        default=10, ge=1, description="单文件大小上界（MB，超限确定性拒绝）"
    )
    max_chunks_per_kb: int = Field(
        default=100000, ge=1, description="单知识库分片总量上界（与内核条目上限对齐）"
    )
    default_chunk_length: int = Field(
        default=500, ge=50, le=2000, description="默认分片长度上限（50..2000）"
    )
    default_overlap: int = Field(
        default=50, ge=0, description="默认相邻分片重叠长度（必须 < max_chunk_length）"
    )
    default_similarity_threshold: float = Field(
        default=0.75, ge=0.0, le=1.0, description="语义分块默认相似度阈值（仅 semantic）"
    )


def from_settings() -> VectorKbManagementConfig:
    """从运行期 settings 只读派生配置（容错读取，懒初始化零副作用）。"""
    return VectorKbManagementConfig(
        enabled=bool(_read_setting("vector_kb_management_enabled", False)),
        storage_root=str(
            _read_setting(
                "vector_kb_management_storage_root", "./.data/vector_kb_management"
            )
        ),
        max_file_size_mb=int(_read_setting("vector_kb_management_max_file_size_mb", 10)),
        max_chunks_per_kb=int(
            _read_setting("vector_kb_management_max_chunks_per_kb", 100000)
        ),
        default_chunk_length=int(
            _read_setting("vector_kb_management_default_chunk_length", 500)
        ),
        default_overlap=int(_read_setting("vector_kb_management_default_overlap", 50)),
        default_similarity_threshold=float(
            _read_setting("vector_kb_management_default_similarity_threshold", 0.75)
        ),
    )
