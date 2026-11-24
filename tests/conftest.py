# =============================================================================
# pytest 共享夹具（tests/conftest.py）
#
# 职责：提供合法/非法定义样本路径、内存桩 MCP/LLM 客户端、临时存储后端等共享夹具。
# =============================================================================

import os
import sys
from pathlib import Path

import asyncio

# Windows 下 ProactorEventLoop 无法创建 socket pair，改用 SelectorEventLoop
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# ---------------------------------------------------------------------------
# 测试环境基线（2026-11-24 CI run 45335295708 定位）
#
# 测试套件此前隐式依赖本机 .env（gitignore 不入库）：CI / 全新克隆无 .env 时
# pydantic-settings 走字段默认值，与已验证的本地环境不一致，导致 42 项失败：
#   - RBAC/租户隔离默认 True → 无令牌请求 401（治理链未降级）
#   - 市场/向量库管理开关默认 False → 路由不注册（405/404 断言失败）
#   - LLM 主模型配置默认空串 → 客户端工厂返回 None → Agent/LLM 执行器跳过注册
#
# 此处显式声明测试套件所需的环境基线（与本地开发 .env 等价、无真实密钥）：
#   - setdefault 语义：CI/开发者已显式导出的环境变量优先，不越权覆盖；
#   - LLM 为占位配置：仅满足客户端构造（不发起网络调用），杜绝密钥入库。
# ---------------------------------------------------------------------------
_TEST_ENV_BASELINE = {
    "RBAC_ENABLED": "false",
    "TENANT_ISOLATION_ENABLED": "false",
    "AGENT_MARKET_ENABLED": "true",
    "TOOL_MARKET_ENABLED": "true",
    "AGENT_MARKET_DEV_ENABLED": "true",
    "VECTOR_KB_MANAGEMENT_ENABLED": "true",
    "LLM_PRIMARY_BASE_URL": "http://127.0.0.1:9/v1",
    "LLM_PRIMARY_API_KEY": "test-placeholder-key",
    "LLM_PRIMARY_MODEL": "test-placeholder-model",
}
for _key, _value in _TEST_ENV_BASELINE.items():
    os.environ.setdefault(_key, _value)

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
