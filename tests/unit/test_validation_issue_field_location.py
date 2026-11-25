# =============================================================================
# ValidationIssue.field 字段级定位测试（tests/unit/test_validation_issue_field_location.py）
#
# 覆盖（B13 U3 T4 / design D4）：
#   1. 触发器字段段：scheduled 缺 schedule → field="schedule"；
#      data_event 缺 event_source → field="event_source"；
#      预设 daily hour 非法 → field="hour"
#   2. Agent model_identifier 为空 WARNING → field="model_identifier"
#   3. 未填充站点保持 None：conditional conditions 为空（本批未填充配置类）
#      与结构校验（DefinitionStructureValidator）两类
#   4. SDK 镜像：wfp_sdk.models ValidationIssue.from_dict 补链 field
#      （缺键落默认 None，旧响应兼容）
#   5. 端到端管线：ValidationReportBuilder 聚合后 field 保留 + model_dump 携带
#
# 回退裁决（T8 收口实测发现）：design D4 假设 validator 已有 parallel_join
# 等待字段校验（基线实无）。T4 初版净新增三段 ERROR 镜像校验，与
# GraphCompiler.compile:78 入口全量 validate 冲突——编译期 details["rule"]
# 契约（test_parallel_fanout_compile.py 6a/6b/6c，既有测试零修改红线）被提前
# 拦截为 issues 形态。已回退三段（行为回基线），join 等待字段的 validate 阶段
# field 定位移交 tech-debt ledger（需重构 compile 入口与编译期校验分层）。
#
# 执行命令：python -m pytest tests/unit/test_validation_issue_field_location.py -q
# =============================================================================

from typing import List

from orchestration_platform.domain.models.enums import (
    ControlFlowKind,
    FrequencyPresetEnum,
    Severity,
    TriggerType,
)
from orchestration_platform.domain.models.node_configurations import (
    AgentNodeConfiguration,
    ControlFlowNodeConfiguration,
    PresetConfiguration,
    TriggerNodeConfiguration,
)
from orchestration_platform.domain.models.workflow_definition import (
    NodeDefinition,
    WorkflowDefinition,
)
from orchestration_platform.domain.validation.definition_structure_validator import (
    DefinitionStructureValidator,
)
from orchestration_platform.domain.validation.node_configuration_validator import (
    NodeConfigurationValidator,
)
from orchestration_platform.domain.validation.validation_report_builder import (
    ValidationReportBuilder,
)
from wfp_sdk.models import ValidationIssue as SdkValidationIssue
from wfp_sdk.models import ValidationReport as SdkValidationReport


def _definition(definition_id: str, nodes: List[NodeDefinition]) -> WorkflowDefinition:
    return WorkflowDefinition(
        spec_version="1.1",
        definition_id=definition_id,
        name=definition_id,
        definition_version=1,
        inputs=[],
        outputs=[],
        nodes=nodes,
        edges=[],
    )


def _node(node_id: str, configuration) -> NodeDefinition:
    return NodeDefinition(
        node_id=node_id,
        name=node_id,
        node_type=configuration.node_type,
        configuration=configuration,
    )


def _validate(definition: WorkflowDefinition) -> List:
    return NodeConfigurationValidator().validate(definition)


class TestTriggerFieldLocation:
    """触发器字段段：schedule / event_source / 预设字段名"""

    def test_scheduled_missing_schedule(self):
        issues = _validate(
            _definition(
                "trig_sched",
                [
                    _node(
                        "t1",
                        TriggerNodeConfiguration(trigger_type=TriggerType.SCHEDULED),
                    )
                ],
            )
        )
        matched = [i for i in issues if "未配置 schedule" in i.message]
        assert len(matched) == 1
        assert matched[0].field == "schedule"
        assert matched[0].node_id == "t1"

    def test_data_event_missing_event_source(self):
        issues = _validate(
            _definition(
                "trig_data_event",
                [
                    _node(
                        "t1",
                        TriggerNodeConfiguration(
                            trigger_type=TriggerType.DATA_EVENT,
                        ),
                    )
                ],
            )
        )
        matched = [i for i in issues if "未配置 event_source" in i.message]
        assert len(matched) == 1
        assert matched[0].field == "event_source"
        assert matched[0].node_id == "t1"

    def test_preset_daily_invalid_hour(self):
        issues = _validate(
            _definition(
                "trig_preset_daily",
                [
                    _node(
                        "t1",
                        TriggerNodeConfiguration(
                            trigger_type=TriggerType.MANUAL,
                            preset_config=PresetConfiguration(
                                preset=FrequencyPresetEnum.DAILY,
                                hour=99,
                                minute=0,
                            ),
                        ),
                    )
                ],
            )
        )
        matched = [i for i in issues if "hour 须为 0–23" in i.message]
        assert len(matched) == 1
        assert matched[0].field == "hour"


class TestAgentFieldLocation:
    def test_empty_model_identifier_warning_field(self):
        issues = _validate(
            _definition(
                "agent_blank_model",
                [_node("a1", AgentNodeConfiguration(model_identifier="  "))],
            )
        )
        matched = [i for i in issues if "model_identifier 为空" in i.message]
        assert len(matched) == 1
        assert matched[0].field == "model_identifier"
        assert matched[0].severity == Severity.WARNING


class TestUnfilledSitesStayNone:
    """本批未填充站点（配置类子集 + 结构类）保持 field=None"""

    def test_conditional_empty_conditions_field_none(self):
        issues = _validate(
            _definition(
                "cond_empty",
                [
                    _node(
                        "c1",
                        ControlFlowNodeConfiguration(
                            control_flow_kind=ControlFlowKind.CONDITIONAL_BRANCH
                        ),
                    )
                ],
            )
        )
        matched = [i for i in issues if "conditions 为空" in i.message]
        assert len(matched) == 1
        assert matched[0].field is None

    def test_structure_issues_field_none(self):
        issues = DefinitionStructureValidator().validate({"definition_id": "x"}, 100)
        assert issues
        assert all(issue.field is None for issue in issues)


class TestSdkMirror:
    """wfp_sdk.models ValidationIssue.field 镜像补链"""

    def test_sdk_issue_field_chain(self):
        issue = SdkValidationIssue.from_dict(
            {
                "code": "WF_VALIDATION_FAILED",
                "severity": "error",
                "message": "触发器节点 t1 类型为 data_event 但未配置 event_source",
                "node_id": "t1",
                "field": "event_source",
            }
        )
        assert issue.field == "event_source"

    def test_sdk_issue_missing_field_defaults_none(self):
        legacy = SdkValidationIssue.from_dict({"code": "X", "message": "m"})
        assert legacy.field is None

    def test_sdk_report_field_chain(self):
        report = SdkValidationReport.from_dict(
            {
                "valid": False,
                "issues": [
                    {"code": "WF_VALIDATION_FAILED", "message": "m", "field": "hour"}
                ],
            }
        )
        assert report.issues[0].field == "hour"


class TestPipelineFieldFlow:
    """ValidationReportBuilder 聚合后 field 保留（响应链路自动携带）"""

    def test_report_builder_keeps_field(self):
        report = ValidationReportBuilder().validate(
            _definition(
                "pipeline_field",
                [
                    _node(
                        "t1",
                        TriggerNodeConfiguration(
                            trigger_type=TriggerType.MANUAL,
                            preset_config=PresetConfiguration(
                                preset=FrequencyPresetEnum.DAILY,
                                hour=99,
                                minute=0,
                            ),
                        ),
                    )
                ],
            )
        )
        matched = [i for i in report.issues if i.field == "hour"]
        assert matched
        assert report.valid is False
        dumped = matched[0].model_dump()
        assert dumped["field"] == "hour"
        assert dumped["node_id"] == "t1"
