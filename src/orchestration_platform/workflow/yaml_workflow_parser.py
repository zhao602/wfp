# =============================================================================
# YAML 工作流解析器（workflow/yaml_workflow_parser.py）
#
# 职责：把 YAML 文本解析为领域对象 WorkflowDefinition。
# 设计意图（对应需求规格 5.1.1.1、5.1.3.1、5.1.3.2、5.1.3.4）：
#   1. 使用安全加载模式（safe_load），禁用自定义构造器，防止 YAML 注入；
#   2. 语法错误捕获后提取行列号，返回 WF_YAML_SYNTAX_ERROR；
#   3. 版本判定先于结构构造，版本不支持时立即终止且不降级解析；
#   4. 解析只做「语法 + 版本 + 结构存在性」，图拓扑校验由后续校验器承担。
# =============================================================================

from typing import Any, Dict, Optional

import yaml

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.errors import (
    WF_UNSUPPORTED_VERSION,
    WF_VALIDATION_FAILED,
    WF_YAML_SYNTAX_ERROR,
    WorkflowValidationError,
)
from orchestration_platform.domain.models.workflow_definition import WorkflowDefinition
from orchestration_platform.domain.validation.definition_structure_validator import (
    DefinitionStructureValidator,
)
from orchestration_platform.workflow.specification_version_registry import (
    SpecificationVersionRegistry,
)


class YamlWorkflowParser:
    """YAML → 领域对象 解析器。"""

    def __init__(
        self,
        version_registry: Optional[SpecificationVersionRegistry] = None,
        structure_validator: Optional[DefinitionStructureValidator] = None,
        max_nodes: Optional[int] = None,
    ) -> None:
        """构造解析器。

        参数：
            version_registry   —— 规范版本注册表；缺省使用默认支持版本集合。
            structure_validator—— 五要素结构校验器；缺省新建。
            max_nodes          —— 节点数量上限；缺省读取全局配置 WORKFLOW_MAX_NODES。
        """
        self._version_registry = version_registry or SpecificationVersionRegistry()
        self._structure_validator = structure_validator or DefinitionStructureValidator()
        self._max_nodes = max_nodes if max_nodes is not None else get_settings().workflow_max_nodes

    # ------------------------------------------------------------------
    # 语法解析
    # ------------------------------------------------------------------
    def parse_document(self, yaml_text: str) -> Dict[str, Any]:
        """安全解析 YAML 文本，返回原始文档树（字典）。

        异常：语法错误或顶层非映射时抛出 WF_YAML_SYNTAX_ERROR（附行列号）。
        """
        try:
            # safe_load 仅支持标准 YAML 类型，不会实例化任意 Python 对象
            document = yaml.safe_load(yaml_text)
        except yaml.YAMLError as error:
            location: Dict[str, Any] = {}
            mark = getattr(error, "problem_mark", None)
            if mark is not None:
                # YAML 行列号从 0 开始，转换为从 1 开始便于用户理解
                location = {"line": mark.line + 1, "column": mark.column + 1}
            raise WorkflowValidationError(
                WF_YAML_SYNTAX_ERROR,
                f"YAML 语法错误：{getattr(error, 'problem', str(error))}",
                location,
            ) from error

        if document is None:
            raise WorkflowValidationError(WF_YAML_SYNTAX_ERROR, "YAML 内容为空，无法解析工作流定义")
        if not isinstance(document, dict):
            raise WorkflowValidationError(
                WF_YAML_SYNTAX_ERROR, "YAML 顶层结构必须为映射（键值对）"
            )
        return document

    # ------------------------------------------------------------------
    # 完整解析（语法 + 版本 + 结构 + 构造）
    # ------------------------------------------------------------------
    def parse(self, yaml_text: str) -> WorkflowDefinition:
        """把 YAML 文本解析为 WorkflowDefinition。

        流程：语法解析 → 版本判定 → 五要素结构校验 → 构造领域对象。
        异常：任一阶段失败均抛出 WorkflowValidationError（携带精确错误码）。
        """
        document = self.parse_document(yaml_text)

        # 1. 版本判定：必须在结构构造之前完成，且不支持时不做降级解析
        spec_version = document.get("spec_version")
        if not spec_version:
            raise WorkflowValidationError(
                WF_VALIDATION_FAILED, "缺少必填要素：规范版本号 spec_version"
            )
        if not self._version_registry.is_supported(str(spec_version)):
            raise WorkflowValidationError(
                WF_UNSUPPORTED_VERSION,
                f"规范版本 {spec_version} 不受支持，当前支持区间：{self._version_registry.supported_range_text()}",
                {"supported_versions": self._version_registry.supported_range_text()},
            )

        # 2. 五要素结构校验（存在性 + 体积上限），一次性收集全部问题
        # v1.1 版本节点上限提升至 200，v1.0 保持配置值（默认 50）
        effective_max_nodes = 200 if str(spec_version) == "1.1" else self._max_nodes
        issues = self._structure_validator.validate(document, effective_max_nodes)
        errors = [issue for issue in issues if issue.severity.value == "error"]
        if errors:
            raise WorkflowValidationError(
                errors[0].code,
                "工作流定义结构校验未通过",
                {"issues": [issue.model_dump(mode="json") for issue in issues]},
            )

        # 3. 构造领域对象（pydantic 负责字段级类型校验）
        try:
            return WorkflowDefinition.model_validate(
                {
                    "definition_id": document["definition_id"],
                    "name": document["name"],
                    "spec_version": str(spec_version),
                    "definition_version": int(document.get("definition_version", 1)),
                    "nodes": document.get("nodes", []),
                    "edges": document.get("edges", []),
                    "inputs": document.get("inputs", []),
                    "outputs": document.get("outputs", []),
                }
            )
        except Exception as error:  # pydantic ValidationError 等
            raise WorkflowValidationError(
                WF_VALIDATION_FAILED,
                f"工作流定义字段校验失败：{error}",
            ) from error
