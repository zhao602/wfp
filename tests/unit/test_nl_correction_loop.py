# =============================================================================
# NL 编排自纠错循环测试（tests/unit/test_nl_correction_loop.py）
#
# 覆盖 DC-04：
#   CL-01 拓扑失败 → 回灌反馈 → 第二轮成功（correction_rounds=1，反馈含问题描述）
#   CL-02 轮次耗尽 → 返回 TOPO_INVALID（correction_rounds=上限，调用次数=上限+1）
#   CL-03 上限为 0 → 只调用一次，不重试
#   CL-04 LLM 调用异常（不可修复）→ 不重试
#   CL-05 JSON 解析失败（可修复）→ 回灌重试后成功
#   CL-06 YAML 反解失败（节点配置字段缺失）→ 回灌重试后成功
#   CL-07 修改路径：NODE_NOT_FOUND → 回灌重试后成功
# =============================================================================

from typing import Any, List, Optional, Union
from unittest.mock import MagicMock, patch

import pytest

from orchestration_platform.domain.models.enums import IntentType, NodeType
from orchestration_platform.engine.graph_compiler import GraphCompiler
from orchestration_platform.governance.nl_orchestration.injection_guard import InjectionGuard
from orchestration_platform.governance.nl_orchestration.models import (
    NodeModification,
    ParsedEdgeSpec,
    ParsedIntent,
    ParsedNodeSpec,
)
from orchestration_platform.governance.nl_orchestration.nl_orchestration_service import (
    NLOrchestrationService,
)
from orchestration_platform.governance.nl_orchestration.workflow_modifier import WorkflowModifier
from orchestration_platform.governance.nl_orchestration.yaml_generator import YamlGenerator
from orchestration_platform.workflow.yaml_workflow_parser import YamlWorkflowParser

_SETTINGS_PATH = (
    "orchestration_platform.governance.nl_orchestration.nl_orchestration_service.get_settings"
)

_BASE_YAML = """definition_id: wf_base
name: 既有工作流
spec_version: '1.0'
definition_version: 1
nodes:
- node_id: t1
  name: 触发器
  node_type: trigger
  configuration:
    trigger_type: manual
- node_id: a1
  name: 智能体
  node_type: agent
  configuration:
    model_identifier: m
    system_prompt: hi
edges:
- edge_id: e0
  source_node_id: t1
  target_node_id: a1
inputs: []
outputs: []
"""


def _settings(max_rounds: int) -> MagicMock:
    settings = MagicMock()
    settings.nl_orchestration_max_correction_rounds = max_rounds
    return settings


def _node(node_id: str, node_type: NodeType, configuration: Optional[dict] = None):
    return ParsedNodeSpec(
        node_id=node_id,
        name=node_id,
        node_type=node_type,
        configuration=configuration or {},
    )


def _valid_intent() -> ParsedIntent:
    return ParsedIntent(
        intent_type=IntentType.CREATE_WORKFLOW,
        workflow_name="合法工作流",
        nodes=[_node("t1", NodeType.TRIGGER), _node("a1", NodeType.AGENT)],
        edges=[ParsedEdgeSpec(source_node_id="t1", target_node_id="a1")],
    )


def _orphan_intent() -> ParsedIntent:
    """含孤立节点：拓扑校验必然失败（不可达 + 到不了终点）。"""
    return ParsedIntent(
        intent_type=IntentType.CREATE_WORKFLOW,
        workflow_name="含孤立节点",
        nodes=[
            _node("t1", NodeType.TRIGGER),
            _node("a1", NodeType.AGENT),
            _node("orphan", NodeType.AGENT),
        ],
        edges=[ParsedEdgeSpec(source_node_id="t1", target_node_id="a1")],
    )


def _broken_config_intent() -> ParsedIntent:
    """code 节点缺 code（必填）→ YAML 反解阶段字段校验失败。"""
    return ParsedIntent(
        intent_type=IntentType.CREATE_WORKFLOW,
        workflow_name="缺字段工作流",
        nodes=[
            _node("t1", NodeType.TRIGGER),
            _node("c1", NodeType.CODE, {"language": "python"}),
        ],
        edges=[ParsedEdgeSpec(source_node_id="t1", target_node_id="c1")],
    )


class _ScriptedParser:
    """按脚本依次返回 ParsedIntent 或抛异常，并记录每次调用参数。"""

    def __init__(self, outcomes: List[Union[ParsedIntent, Exception]]) -> None:
        self._outcomes = outcomes
        self.calls: List[dict] = []

    async def parse(
        self,
        directive: str,
        intent_type: IntentType,
        target_definition: Any = None,
        feedback: Optional[str] = None,
    ) -> ParsedIntent:
        self.calls.append({"directive": directive, "feedback": feedback})
        index = min(len(self.calls) - 1, len(self._outcomes) - 1)
        outcome = self._outcomes[index]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _service(parser: _ScriptedParser) -> NLOrchestrationService:
    return NLOrchestrationService(
        intent_parser=parser,
        yaml_generator=YamlGenerator(),
        workflow_modifier=WorkflowModifier(),
        injection_guard=InjectionGuard(),
        graph_compiler=GraphCompiler(),
        yaml_parser=YamlWorkflowParser(),
    )


class TestCreateCorrectionLoop:
    """CL-01~CL-06：创建路径的自纠错。"""

    @pytest.mark.asyncio
    async def test_cl01_topology_failure_then_success(self):
        parser = _ScriptedParser([_orphan_intent(), _valid_intent()])
        with patch(_SETTINGS_PATH, return_value=_settings(2)):
            result = await _service(parser).create_from_nl("生成工作流", "t", "u")

        assert result.success is True
        assert result.correction_rounds == 1
        assert len(parser.calls) == 2
        # 第二轮必须带上第一轮的问题清单
        assert parser.calls[1]["feedback"]
        assert "orphan" in parser.calls[1]["feedback"]
        assert parser.calls[0]["feedback"] is None

    @pytest.mark.asyncio
    async def test_cl02_rounds_exhausted_returns_topo_invalid(self):
        parser = _ScriptedParser([_orphan_intent()])
        with patch(_SETTINGS_PATH, return_value=_settings(1)):
            result = await _service(parser).create_from_nl("生成工作流", "t", "u")

        assert result.success is False
        assert result.error_code is not None
        assert result.error_code.value == "topo_invalid"
        assert result.correction_rounds == 1
        assert len(parser.calls) == 2  # 首轮 + 1 次纠错

    @pytest.mark.asyncio
    async def test_cl03_zero_rounds_no_retry(self):
        parser = _ScriptedParser([_orphan_intent(), _valid_intent()])
        with patch(_SETTINGS_PATH, return_value=_settings(0)):
            result = await _service(parser).create_from_nl("生成工作流", "t", "u")

        assert result.success is False
        assert result.correction_rounds == 0
        assert len(parser.calls) == 1

    @pytest.mark.asyncio
    async def test_cl04_llm_unavailable_not_retried(self):
        parser = _ScriptedParser(
            [ValueError("NL_PARSE_FAILED: LLM 调用异常 - connection refused")]
        )
        with patch(_SETTINGS_PATH, return_value=_settings(2)):
            result = await _service(parser).create_from_nl("生成工作流", "t", "u")

        assert result.success is False
        assert result.correction_rounds == 0
        assert len(parser.calls) == 1

    @pytest.mark.asyncio
    async def test_cl05_json_parse_failure_retried(self):
        parser = _ScriptedParser(
            [
                ValueError("NL_PARSE_FAILED: LLM 返回无法解析 - Expecting value"),
                _valid_intent(),
            ]
        )
        with patch(_SETTINGS_PATH, return_value=_settings(2)):
            result = await _service(parser).create_from_nl("生成工作流", "t", "u")

        assert result.success is True
        assert result.correction_rounds == 1
        assert len(parser.calls) == 2

    @pytest.mark.asyncio
    async def test_cl06_yaml_field_failure_retried(self):
        parser = _ScriptedParser([_broken_config_intent(), _valid_intent()])
        with patch(_SETTINGS_PATH, return_value=_settings(2)):
            result = await _service(parser).create_from_nl("生成工作流", "t", "u")

        assert result.success is True
        assert result.correction_rounds == 1
        assert parser.calls[1]["feedback"]


class TestModifyCorrectionLoop:
    """CL-07：修改路径的自纠错。"""

    @pytest.mark.asyncio
    async def test_cl07_node_not_found_then_success(self):
        base_definition = YamlWorkflowParser().parse(_BASE_YAML)
        bad_intent = ParsedIntent(
            intent_type=IntentType.MODIFY_WORKFLOW,
            modifications=[
                NodeModification(action="delete", target_node_id="missing_node")
            ],
        )
        noop_intent = ParsedIntent(
            intent_type=IntentType.MODIFY_WORKFLOW,
            modifications=[],
        )
        parser = _ScriptedParser([bad_intent, noop_intent])
        with patch(_SETTINGS_PATH, return_value=_settings(2)):
            result = await _service(parser).modify_from_nl(
                "删除一个节点", base_definition, "t", "u"
            )

        assert result.success is True
        assert result.correction_rounds == 1
        assert "missing_node" in parser.calls[1]["feedback"]
