# =============================================================================
# 校验报告构建器（domain/validation/validation_report_builder.py）
#
# 职责：编排结构校验、拓扑校验、配置校验三类校验器，将全部问题一次性聚合为
#      完整的 ValidationReport。
# 设计意图（对应需求规格 5.1.1.7）：
#   1. 不因首个错误终止——收集全部校验器的问题后统一返回；
#   2. 校验顺序：结构 → 拓扑 → 配置，但即使结构有问题也继续收集拓扑与配置问题
#      （各校验器内部对异常输入做防御性处理），最大化单次校验的信息量；
#   3. 报告的 valid 字段 = 全部 issue 均非 ERROR 级别。
# =============================================================================

from typing import List, Optional

from orchestration_platform.domain.models.common import ValidationIssue, ValidationReport
from orchestration_platform.domain.models.enums import Severity
from orchestration_platform.domain.models.workflow_definition import WorkflowDefinition
from orchestration_platform.domain.validation.definition_structure_validator import (
    DefinitionStructureValidator,
)
from orchestration_platform.domain.validation.graph_topology_validator import (
    GraphTopologyValidator,
)
from orchestration_platform.domain.validation.node_configuration_validator import (
    NodeConfigurationValidator,
)


class ValidationReportBuilder:
    """校验报告构建器：聚合结构 + 拓扑 + 配置三类校验结果。"""

    def __init__(
        self,
        structure_validator: Optional[DefinitionStructureValidator] = None,
        topology_validator: Optional[GraphTopologyValidator] = None,
        configuration_validator: Optional[NodeConfigurationValidator] = None,
    ) -> None:
        """构造校验报告构建器，可注入自定义校验器实例（测试时替换 mock）。"""
        self._structure_validator = structure_validator or DefinitionStructureValidator()
        self._topology_validator = topology_validator or GraphTopologyValidator()
        self._configuration_validator = configuration_validator or NodeConfigurationValidator()

    def validate(self, definition: WorkflowDefinition) -> ValidationReport:
        """对已解析的工作流定义执行全量校验，返回聚合报告。

        流程：拓扑校验 → 配置校验 → 合并问题 → 构建报告。
        （结构校验已在解析阶段完成，此处不重复。）
        """
        issues: List[ValidationIssue] = []

        # 1. 拓扑校验
        issues.extend(self._topology_validator.validate(definition))

        # 2. 配置校验
        issues.extend(self._configuration_validator.validate(definition))

        # 3. 构建报告：valid = 不存在任何 ERROR 级别问题
        has_error = any(issue.severity == Severity.ERROR for issue in issues)
        return ValidationReport(
            valid=not has_error,
            issues=issues,
            definition_id=definition.definition_id,
        )

    def validate_with_structure(
        self, document: dict, max_nodes: int, definition: Optional[WorkflowDefinition] = None
    ) -> ValidationReport:
        """对原始文档树执行结构 + 拓扑 + 配置全量校验。

        当 definition 已构造时跳过结构校验，仅做拓扑 + 配置；
        当 definition 为 None 时仅返回结构校验结果（用于解析失败场景）。
        """
        issues: List[ValidationIssue] = []

        # 结构校验
        issues.extend(self._structure_validator.validate(document, max_nodes))

        # 若已有领域对象，继续拓扑 + 配置校验
        if definition is not None:
            issues.extend(self._topology_validator.validate(definition))
            issues.extend(self._configuration_validator.validate(definition))

        has_error = any(issue.severity == Severity.ERROR for issue in issues)
        return ValidationReport(
            valid=not has_error,
            issues=issues,
            definition_id=document.get("definition_id") if isinstance(document, dict) else None,
        )
