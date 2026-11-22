# =============================================================================
# 范例库服务（application/example_library_service.py）
#
# 职责：加载预置范例工作流定义，检查范例运行所需环境变量，
#       并以统一结构返回范例列表（含可用性标记与缺失依赖说明）。
# 设计意图（对应需求规格 5.6、任务 T16.5）：
#   1. 启动前检查——缺失关键环境变量时标记范例"不可用"并附原因，
#      避免用户运行到一半才发现配置缺失；
#   2. 加载容错——单个范例 YAML 解析失败不影响其余范例列出，
#      失败范例在列表中标记"不可用"并附解析错误原因；
#   3. 依赖声明——每个范例声明所需环境变量集合，由服务统一校验。
# =============================================================================

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.errors import (
    ExampleDependencyMissingError,
    WorkflowValidationError,
)
from orchestration_platform.workflow.yaml_workflow_parser import YamlWorkflowParser

# 范例根目录：本文件位于 application/，范例位于项目根 examples/
# 路径：example_library_service.py -> application -> orchestration_platform -> src -> 项目根
_EXAMPLES_ROOT = Path(__file__).resolve().parent.parent.parent.parent / "examples"

# 范例注册表：目录名 -> (YAML 文件名, 所需环境变量列表)
# 所需环境变量以 settings 中对应字段为依据：
#   - 含 Agent 节点的范例需要主模型配置（base_url / api_key / model）
#   - 含数据源节点的范例需要数据源连接串
_EXAMPLE_REGISTRY: Dict[str, tuple] = {
    "sequential_pipeline": (
        "sequential_pipeline.yaml",
        ["LLM_PRIMARY_BASE_URL", "LLM_PRIMARY_API_KEY", "LLM_PRIMARY_MODEL",
         "DATA_SOURCE_CONNECTION_STRING"],
    ),
    "supervisor_routing": (
        "supervisor_routing.yaml",
        ["LLM_PRIMARY_BASE_URL", "LLM_PRIMARY_API_KEY", "LLM_PRIMARY_MODEL"],
    ),
    "conditional_loop_pipeline": (
        "conditional_loop_pipeline.yaml",
        ["LLM_PRIMARY_BASE_URL", "LLM_PRIMARY_API_KEY", "LLM_PRIMARY_MODEL"],
    ),
    # 第五阶段偏差修正（G4/T233）：数据科学范例追加登记
    # 环境变量与既有含 Agent 范例一致（LLM 主模型三变量）
    "feature_engineering": (
        "feature_engineering.yaml",
        ["LLM_PRIMARY_BASE_URL", "LLM_PRIMARY_API_KEY", "LLM_PRIMARY_MODEL"],
    ),
    "drift_detection": (
        "drift_detection.yaml",
        ["LLM_PRIMARY_BASE_URL", "LLM_PRIMARY_API_KEY", "LLM_PRIMARY_MODEL"],
    ),
    # 范例库补齐（T16.5 全量登记）：examples/ 目录 12 个范例全部登记，
    # 按目录既有约定以 dict 追加方式登记、不修改既有条目。
    # 依赖声明口径：
    #   - 含 Agent 节点 → LLM 主模型三变量；
    #   - 数据源以查询语句取数（与 sequential_pipeline 同款 demo_warehouse 模式）
    #     → 追加 DATA_SOURCE_CONNECTION_STRING；
    #   - 子工作流嵌套按传递依赖声明（subworkflow_nesting 调用 sequential_pipeline）。
    "parallel_execution": (
        "parallel_execution.yaml",
        ["LLM_PRIMARY_BASE_URL", "LLM_PRIMARY_API_KEY", "LLM_PRIMARY_MODEL"],
    ),
    "hierarchical_delegation": (
        "hierarchical_delegation.yaml",
        ["LLM_PRIMARY_BASE_URL", "LLM_PRIMARY_API_KEY", "LLM_PRIMARY_MODEL"],
    ),
    "reflection_iteration": (
        "reflection_iteration.yaml",
        ["LLM_PRIMARY_BASE_URL", "LLM_PRIMARY_API_KEY", "LLM_PRIMARY_MODEL"],
    ),
    "data_engineering": (
        "data_engineering_agent.yaml",
        ["LLM_PRIMARY_BASE_URL", "LLM_PRIMARY_API_KEY", "LLM_PRIMARY_MODEL",
         "DATA_SOURCE_CONNECTION_STRING"],
    ),
    "data_transform_pipeline": (
        "data_transform_pipeline.yaml",
        ["DATA_SOURCE_CONNECTION_STRING"],
    ),
    "subworkflow_nesting": (
        "subworkflow_nesting.yaml",
        ["LLM_PRIMARY_BASE_URL", "LLM_PRIMARY_API_KEY", "LLM_PRIMARY_MODEL",
         "DATA_SOURCE_CONNECTION_STRING"],
    ),
    "realtime_subscription": (
        "realtime_subscription_workflow.yaml",
        ["LLM_PRIMARY_BASE_URL", "LLM_PRIMARY_API_KEY", "LLM_PRIMARY_MODEL"],
    ),
}


@dataclass
class ExampleEntry:
    """范例条目：描述单个预置范例的元信息与可用性状态。"""

    example_id: str
    name: str
    yaml_path: str
    available: bool
    unavailable_reason: Optional[str] = None
    missing_dependencies: List[str] = field(default_factory=list)


class ExampleLibraryService:
    """范例库服务：加载、校验依赖、列出预置范例。

    使用方式：
        service = ExampleLibraryService()
        entries = service.list_examples()
        for entry in entries:
            if entry.available:
                definition = service.load_example(entry.example_id)
    """

    def __init__(
        self,
        examples_root: Optional[Path] = None,
        settings_source: Optional[Callable[[], object]] = None,
    ) -> None:
        """初始化范例库服务。

        Args:
            examples_root: 范例根目录，缺省使用模块级常量 _EXAMPLES_ROOT。
            settings_source: 配置读取函数，缺省为 get_settings。
                依赖判定同时看进程环境变量与配置对象（含 .env），
                该参数仅供测试注入空配置以模拟"依赖缺失"。
        """
        self._root = examples_root if examples_root is not None else _EXAMPLES_ROOT
        self._parser = YamlWorkflowParser()
        self._settings_source: Callable[[], object] = settings_source or get_settings

    def list_examples(self) -> List[ExampleEntry]:
        """列出全部预置范例及其可用性状态。

        对每个范例依次检查：
        1. YAML 文件是否存在；
        2. 所需环境变量是否齐全——缺失则标记不可用并列出缺失项；
        3. YAML 是否可解析——解析失败则标记不可用并附错误原因。

        Returns:
            范例条目列表，按注册表声明顺序排列。
        """
        entries: List[ExampleEntry] = []
        for dir_name, (yaml_name, required_envs) in _EXAMPLE_REGISTRY.items():
            entry = self._build_entry(dir_name, yaml_name, required_envs)
            entries.append(entry)
        return entries

    def load_example(self, example_id: str):
        """加载指定范例的工作流定义。

        Args:
            example_id: 范例标识（注册表目录名）。

        Returns:
            WorkflowDefinition 领域对象。

        Raises:
            ExampleDependencyMissingError: 环境变量缺失。
            WorkflowValidationError: YAML 解析失败。
            FileNotFoundError: 范例文件不存在。
        """
        if example_id not in _EXAMPLE_REGISTRY:
            raise FileNotFoundError(f"未知的范例标识: {example_id}")

        yaml_name, required_envs = _EXAMPLE_REGISTRY[example_id]
        yaml_path = self._root / example_id / yaml_name

        missing = self._check_missing_envs(required_envs)
        if missing:
            raise ExampleDependencyMissingError(
                example_id=example_id,
                missing_variables=missing,
            )

        return self._parse_yaml_file(yaml_path)

    def check_dependencies(self, example_id: str) -> List[str]:
        """检查指定范例的环境变量依赖，返回缺失项列表。

        Args:
            example_id: 范例标识。

        Returns:
            缺失的环境变量名列表，空列表表示依赖齐全。
        """
        if example_id not in _EXAMPLE_REGISTRY:
            return []
        _, required_envs = _EXAMPLE_REGISTRY[example_id]
        return self._check_missing_envs(required_envs)

    def _build_entry(
        self,
        dir_name: str,
        yaml_name: str,
        required_envs: List[str],
    ) -> ExampleEntry:
        """构建单个范例条目，含可用性检查。"""
        yaml_path = self._root / dir_name / yaml_name
        entry = ExampleEntry(
            example_id=dir_name,
            name=dir_name,
            yaml_path=str(yaml_path),
            available=True,
        )

        # 检查 1：文件存在性
        if not yaml_path.exists():
            entry.available = False
            entry.unavailable_reason = f"范例文件不存在: {yaml_path}"
            return entry

        # 检查 2：环境变量依赖
        missing = self._check_missing_envs(required_envs)
        if missing:
            entry.available = False
            entry.unavailable_reason = "缺少必要环境变量"
            entry.missing_dependencies = missing
            return entry

        # 检查 3：YAML 可解析性
        try:
            self._parse_yaml_file(yaml_path)
        except WorkflowValidationError as exc:
            entry.available = False
            entry.unavailable_reason = f"YAML 解析失败: {exc}"
        except Exception as exc:  # noqa: BLE001 —— 兜底：任何异常都标记不可用而非中断列出
            entry.available = False
            entry.unavailable_reason = f"加载失败: {exc}"

        return entry

    def _check_missing_envs(self, required_envs: List[str]) -> List[str]:
        """检查依赖配置是否就绪，返回缺失项列表。

        判定标准（两者满足其一即视为已配置）：
          1. 进程环境变量存在且值非空；
          2. 配置对象对应字段非空——.env 由 pydantic-settings 载入 Settings，
             不会写入 os.environ，只看环境变量会把已配置项误判为缺失。
        """
        return [env for env in required_envs if not self._is_configured(env)]

    def _is_configured(self, env_name: str) -> bool:
        """单变量就绪判定：环境变量优先，回落到配置对象同名字段。"""
        if os.environ.get(env_name):
            return True
        try:
            settings = self._settings_source()
        except Exception:  # noqa: BLE001 —— 配置加载失败不应中断范例列举
            return False
        return bool(getattr(settings, env_name.lower(), None))

    def _parse_yaml_file(self, yaml_path: Path):
        """读取 YAML 文件并解析为工作流定义领域对象。"""
        yaml_text = yaml_path.read_text(encoding="utf-8")
        return self._parser.parse(yaml_text)
