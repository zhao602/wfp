# =============================================================================
# pytest 共享夹具（tests/conftest.py）
#
# 职责：提供合法/非法定义样本路径、内存桩 MCP/LLM 客户端、临时存储后端等共享夹具。
# =============================================================================

import sys
from pathlib import Path

import asyncio

# Windows 下 ProactorEventLoop 无法创建 socket pair，改用 SelectorEventLoop
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import pytest

# 将 src 加入路径（与验证脚本一致）
SRC_PATH = Path(__file__).resolve().parent.parent / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

FIXTURES_ROOT = Path(__file__).resolve().parent / "fixtures"
VALID_DEFINITIONS_DIR = FIXTURES_ROOT / "valid_workflow_definitions"
INVALID_DEFINITIONS_DIR = FIXTURES_ROOT / "invalid_workflow_definitions"


@pytest.fixture
def valid_definitions_dir() -> Path:
    """合法工作流定义 fixture 目录。"""
    return VALID_DEFINITIONS_DIR


@pytest.fixture
def invalid_definitions_dir() -> Path:
    """非合法工作流定义 fixture 目录。"""
    return INVALID_DEFINITIONS_DIR


@pytest.fixture
def sequential_definition_path() -> Path:
    """四节点顺序流水线定义路径。"""
    return VALID_DEFINITIONS_DIR / "valid_sequential_four_nodes.yaml"


@pytest.fixture
def loop_definition_path() -> Path:
    """含循环的定义路径。"""
    return VALID_DEFINITIONS_DIR / "valid_with_loop.yaml"


@pytest.fixture
def branch_definition_path() -> Path:
    """含条件分支的定义路径。"""
    return VALID_DEFINITIONS_DIR / "valid_with_branch.yaml"


@pytest.fixture
def tmp_storage_root(tmp_path: Path) -> Path:
    """临时本地文件存储根目录。"""
    root = tmp_path / "storage"
    root.mkdir()
    return root


@pytest.fixture
def yaml_parser():
    """YAML 工作流解析器实例。"""
    from orchestration_platform.workflow.yaml_workflow_parser import YamlWorkflowParser
    return YamlWorkflowParser()


@pytest.fixture
def local_workflow_repository(tmp_storage_root):
    """本地文件工作流仓储实例。"""
    from orchestration_platform.persistence.local_file.local_file_workflow_repository import (
        LocalFileWorkflowRepository,
    )
    return LocalFileWorkflowRepository(storage_root=str(tmp_storage_root))


@pytest.fixture
def local_event_repository(tmp_storage_root):
    """本地文件事件仓储实例。"""
    from orchestration_platform.persistence.local_file.local_file_event_repository import (
        LocalFileEventRepository,
    )
    return LocalFileEventRepository(storage_root=str(tmp_storage_root))


@pytest.fixture
def local_checkpoint_repository(tmp_storage_root):
    """本地文件检查点仓储实例。"""
    from orchestration_platform.persistence.local_file.local_file_checkpoint_repository import (
        LocalFileCheckpointRepository,
    )
    return LocalFileCheckpointRepository(storage_root=str(tmp_storage_root))
