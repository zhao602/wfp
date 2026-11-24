# =============================================================================
# CLI 命令共享装载层（cli/commands/__init__.py）
#
# 职责（arch_review_v2 5.2/5.5，CHG-18/19；INV-03 零新造校验逻辑）：
#   1. 三层校验链只读复用的统一入口：YamlWorkflowParser →
#      GraphTopologyValidator → NodeConfigurationValidator → GraphCompiler；
#   2. 加载异常（exit 2）与校验失败（exit 1）的语义区分：
#        - WorkflowLoadError：YAML 语法错误 / 文件读取失败（无法装载文档）；
#        - WorkflowInvalidError：已装载但版本/结构/拓扑/配置/编译未通过；
#   3. 范例库伴随 Agent 清单识别（顶层 agents 键且无 spec_version）→
#      validate 目录模式 SKIP（E18：15 个 YAML 中 2 个为伴随清单）。
# =============================================================================

from typing import List, Tuple

import yaml

from orchestration_platform.application.engine_wiring import build_engine_settings
from orchestration_platform.domain.errors import WF_YAML_SYNTAX_ERROR
from orchestration_platform.domain.models.common import ValidationIssue
from orchestration_platform.domain.models.enums import Severity
from orchestration_platform.domain.models.workflow_definition import WorkflowDefinition
from orchestration_platform.domain.validation.graph_topology_validator import (
    GraphTopologyValidator,
)
from orchestration_platform.domain.validation.node_configuration_validator import (
    NodeConfigurationValidator,
)
from orchestration_platform.engine.abstractions.compiled_workflow import CompiledWorkflow
from orchestration_platform.engine.graph_compiler import GraphCompiler
from orchestration_platform.workflow.yaml_workflow_parser import YamlWorkflowParser


class WorkflowLoadError(Exception):
    """加载异常：YAML 语法错误 / 文件不可读 → exit 2。"""


class WorkflowInvalidError(Exception):
    """校验失败：版本/结构/拓扑/节点配置/编译未通过 → exit 1。"""


def is_agent_manifest(text: str) -> bool:
    """识别范例库伴随 Agent 清单（顶层 agents 键且无 spec_version）。

    仅作目录模式 SKIP 分流（呈现层分流，不承担校验职责，INV-03）；
    真正的工作流定义必含 spec_version，二者不会混淆。
    """
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError:
        return False
    if not isinstance(document, dict):
        return False
    return "spec_version" not in document and "agents" in document


def parse_workflow_text(text: str) -> WorkflowDefinition:
    """YAML 文本 → WorkflowDefinition（复用 YamlWorkflowParser，零新造逻辑）。

    异常映射：WF_YAML_SYNTAX_ERROR → WorkflowLoadError（exit 2）；
    其余 WorkflowValidationError（版本/结构/字段）→ WorkflowInvalidError（exit 1）。
    """
    try:
        return YamlWorkflowParser().parse(text)
    except Exception as error:
        # WorkflowValidationError 为 DomainError 子类；code 属性为唯一分流依据
        code = getattr(error, "code", None)
        if code == WF_YAML_SYNTAX_ERROR:
            raise WorkflowLoadError(str(error)) from error
        raise WorkflowInvalidError(str(error)) from error


def validate_definition(definition: WorkflowDefinition) -> List[ValidationIssue]:
    """拓扑校验 + 节点配置校验（只读复用，一次性聚合返回，不因首错终止）。"""
    issues: List[ValidationIssue] = []
    issues.extend(GraphTopologyValidator().validate(definition))
    issues.extend(NodeConfigurationValidator().validate(definition))
    return issues


async def compile_definition(definition: WorkflowDefinition) -> CompiledWorkflow:
    """编译为 CompiledWorkflow（复用 GraphCompiler，拓扑不合法抛 WorkflowInvalidError）。"""
    try:
        return await GraphCompiler(
            settings=build_engine_settings()
        ).compile(definition)
    except Exception as error:
        raise WorkflowInvalidError(str(error)) from error


async def load_workflow_text(
    text: str,
) -> Tuple[WorkflowDefinition, CompiledWorkflow, List[ValidationIssue]]:
    """全链装载：解析 → 拓扑校验 → 节点配置校验 → 编译。

    返回 (definition, compiled, issues)；ERROR 级问题存在时不编译，
    直接以 WorkflowInvalidError 抛出（issues 由调用方在抛出前已可见）。
    """
    definition = parse_workflow_text(text)
    issues = validate_definition(definition)
    if any(issue.severity == Severity.ERROR for issue in issues):
        raise WorkflowInvalidError("工作流定义校验未通过")
    compiled = await compile_definition(definition)
    return definition, compiled, issues


def read_workflow_file(path_str: str) -> str:
    """读取工作流 YAML 文件文本；读取失败按加载异常抛出（exit 2）。"""
    from pathlib import Path

    path = Path(path_str)
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise WorkflowLoadError(f"文件读取失败：{path}：{error}") from error
    except UnicodeDecodeError as error:
        raise WorkflowLoadError(f"文件编码错误（须为 UTF-8）：{path}：{error}") from error


def resolve_target_files(target: str) -> List[str]:
    """解析校验目标：单文件直接返回；目录递归收集 *.yaml（E18 glob 口径）。

    目标不存在 / 目录内无 YAML → WorkflowLoadError（exit 2）。
    """
    from pathlib import Path

    path = Path(target)
    if not path.exists():
        raise WorkflowLoadError(f"目标不存在：{path}")
    if path.is_file():
        return [str(path)]
    files = sorted(str(item) for item in path.rglob("*.yaml") if item.is_file())
    if not files:
        raise WorkflowLoadError(f"目录内未找到 YAML 文件：{path}")
    return files


__all__ = [
    "WorkflowLoadError",
    "WorkflowInvalidError",
    "CompiledWorkflow",
    "is_agent_manifest",
    "parse_workflow_text",
    "validate_definition",
    "compile_definition",
    "load_workflow_text",
    "read_workflow_file",
    "resolve_target_files",
]
