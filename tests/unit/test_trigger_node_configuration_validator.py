# =============================================================================
# 触发器节点配置校验单元测试
# （tests/unit/test_trigger_node_configuration_validator.py）
#
# 覆盖 trigger_input_template_resolution 批次 2（任务 2.2 / CHG-09）：
#   T-04 空键   —— 产出 Severity.ERROR + WF_VALIDATION_FAILED
#   T-05 空值   —— 产出 Severity.ERROR + WF_VALIDATION_FAILED
#   T-06 合法恒等映射 —— 无 issue
#   T-07 空模板 —— 无 issue
#
# 设计依据：spec FR-05 / FR-07，design DC-03；
#   校验级别依据 OQ-1 = ERROR 阻断 + 降级兜底（存量普查未发现非法定义，保持 ERROR）。
# =============================================================================

from orchestration_platform.domain.errors import WF_VALIDATION_FAILED
from orchestration_platform.domain.models.enums import (
    NodeType,
    Severity,
    TriggerType,
)
from orchestration_platform.domain.models.node_configurations import (
    TriggerNodeConfiguration,
)
from orchestration_platform.domain.models.node_definition import NodeDefinition
from orchestration_platform.domain.models.workflow_definition import WorkflowDefinition
from orchestration_platform.domain.validation.node_configuration_validator import (
    NodeConfigurationValidator,
)


def _definition(input_template):
    """构造仅含单个触发器节点的工作流定义。"""
    node = NodeDefinition(
        node_id="trigger_1",
        node_type=NodeType.TRIGGER,
        configuration=TriggerNodeConfiguration(
            trigger_type=TriggerType.MANUAL,
            input_template=input_template,
        ),
    )
    return WorkflowDefinition(
        definition_id="wf-trigger-validation",
        name="触发器校验测试",
        spec_version="1.0",
        nodes=[node],
    )


class TestTriggerInputTemplateValidation:
    """input_template「键非空、值非空」两条结构校验。"""

    def setup_method(self):
        self.validator = NodeConfigurationValidator()

    def _issues(self, input_template):
        return self.validator.validate(_definition(input_template))

    def test_t04_empty_param_name_reports_error(self):
        """T-04 空字符串键：产出 ERROR + WF_VALIDATION_FAILED。"""
        issues = self._issues({"": "topic"})

        assert len(issues) >= 1
        assert all(issue.code == WF_VALIDATION_FAILED for issue in issues)
        assert any(issue.severity == Severity.ERROR for issue in issues)

    def test_t05_empty_reference_reports_error(self):
        """T-05 空字符串值：产出 ERROR + WF_VALIDATION_FAILED。"""
        issues = self._issues({"topic": ""})

        assert len(issues) >= 1
        assert any(
            issue.code == WF_VALIDATION_FAILED
            and issue.severity == Severity.ERROR
            for issue in issues
        )

    def test_t06_valid_identity_mapping_no_issue(self):
        """T-06 合法恒等映射：无 issue。"""
        assert self._issues({"topic": "topic"}) == []

    def test_t07_empty_template_no_issue(self):
        """T-07 空模板：无 issue（空模板合法）。"""
        assert self._issues({}) == []