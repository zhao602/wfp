# =============================================================================
# LLM/Code 节点 YAML 加载与往返单元测试（llm_code_nodes_v2 任务 8.1 / T1）
# 执行命令：PYTHONPATH=src python -m pytest tests/unit/test_llm_code_node_loading.py -q
#
# 覆盖（spec FR-01/FR-20 / AC-01/AC-02/AC-38 / G-08；design :750 T1 口径）：
#   - 含 llm 节点 YAML 经 YamlWorkflowParser 加载成功且逐字段往返一致，
#     None（未配置）与显式值（含 0 / 空数组）区分保真（D-01/DC-03/DC-14）；
#   - 缺 model_identifier → 解析层拒绝，消息含定位与字段名；
#     空白 model_identifier → 加载期校验兜底拒绝，issue 含 node_id 与字段名；
#   - 含 code 节点 YAML 加载成功（多行块标量脚本逐字符保真）；
#     language=javascript → 解析层放行（历史非法值可打开修正）+
#     加载期校验拒绝「本期仅支持 python」（AC-28 分层口径）；
#   - 旧定义（五份既有 fixture，不含新节点）加载/导出零变更回归（AC-38③）；
#   - output_schema 往返原样透传，不重写不消费（AC-01④ / DC-09/DC-14-3）；
#   - trigger → llm → code 混合链路加载期校验零问题（8.2/8.3 场景前置）。
#
# 分层语义（沿用 v1 任务 2.5/3.1/3.2 已确立口径，本文件补 YAML 文本级缺口）：
#   解析层（pydantic 判别联合）拒绝「字段缺失」——loc 含节点序与字段名；
#   加载期校验层（NodeConfigurationValidator）兜底「取值非法」——
#   issue.node_id 定位 + 语义错误码前缀（D-04）。
#
# 往返对照说明：解析器重建模型不携带 created_at/updated_at（构造期元数据，
# yaml_workflow_parser.py :120-:132 仅传七个承载字段），故对照以承载字段为准，
# 承载字段逐键相等 = 加载/导出零变更。
# =============================================================================

import pathlib

import pytest
import yaml

from orchestration_platform.domain.errors import (
    CODE_SCRIPT_INVALID,
    WF_VALIDATION_FAILED,
    WorkflowValidationError,
)
from orchestration_platform.domain.models.enums import NodeType, Severity
from orchestration_platform.domain.models.node_configurations import (
    CodeNodeConfiguration,
    LlmNodeConfiguration,
)
from orchestration_platform.domain.validation.node_configuration_validator import (
    NodeConfigurationValidator,
)

# 解析器实际承载的字段（往返零变更对照范围；created_at/updated_at 不承载）
CARRIED_FIELDS = (
    "definition_id",
    "name",
    "spec_version",
    "definition_version",
    "nodes",
    "edges",
    "inputs",
    "outputs",
)

# 旧定义回归样本：五份既有合法 fixture（均不含 llm/code 节点）
FIXTURES_DIR = (
    pathlib.Path(__file__).resolve().parents[1]
    / "fixtures"
    / "valid_workflow_definitions"
)
LEGACY_FIXTURE_NAMES = sorted(path.name for path in FIXTURES_DIR.glob("*.yaml"))


def _dump_definition(definition) -> str:
    """模型 → JSON 安全字典 → YAML 文本（后端往返的导出侧）。"""
    return yaml.safe_dump(
        definition.model_dump(mode="json"), allow_unicode=True, sort_keys=False
    )


def _carried_dump(definition) -> dict:
    """返回解析器承载字段的 JSON 安全字典（排除构造期时间戳元数据）。"""
    document = definition.model_dump(mode="json")
    return {key: document[key] for key in CARRIED_FIELDS}


def _llm_workflow_yaml(llm_configuration_block: str, definition_id: str) -> str:
    """构造「trigger → llm」两节点合法工作流 YAML（llm 配置块由用例给定）。"""
    return (
        'spec_version: "1.0"\n'
        f"definition_id: {definition_id}\n"
        "name: 大模型节点加载校验\n"
        "definition_version: 1\n"
        "inputs: []\n"
        "outputs: []\n"
        "nodes:\n"
        "  - node_id: trigger_start\n"
        "    name: 手动触发\n"
        "    node_type: trigger\n"
        "    configuration:\n"
        "      trigger_type: manual\n"
        "  - node_id: llm_generate\n"
        "    name: 生成摘要\n"
        "    node_type: llm\n"
        "    output_name: summary\n"
        "    configuration:\n"
        f"{llm_configuration_block}"
        "edges:\n"
        "  - edge_id: e1\n"
        "    source_node_id: trigger_start\n"
        "    target_node_id: llm_generate\n"
    )


def _code_workflow_yaml(code_configuration_block: str, definition_id: str) -> str:
    """构造「trigger → code」两节点合法工作流 YAML（code 配置块由用例给定）。"""
    return (
        'spec_version: "1.0"\n'
        f"definition_id: {definition_id}\n"
        "name: 代码节点加载校验\n"
        "definition_version: 1\n"
        "inputs: []\n"
        "outputs: []\n"
        "nodes:\n"
        "  - node_id: trigger_start\n"
        "    name: 手动触发\n"
        "    node_type: trigger\n"
        "    configuration:\n"
        "      trigger_type: manual\n"
        "  - node_id: code_extract\n"
        "    name: 清洗文本\n"
        "    node_type: code\n"
        "    output_name: cleaned\n"
        "    configuration:\n"
        f"{code_configuration_block}"
        "edges:\n"
        "  - edge_id: e1\n"
        "    source_node_id: trigger_start\n"
        "    target_node_id: code_extract\n"
    )


# 显式采样值（含 0 / 空数组——「未配置=None」的对照基准，DC-14）
_EXPLICIT_SAMPLING_BLOCK = (
    "      model_identifier: qwen-max\n"
    "      system_prompt: 你是测试助手。\n"
    "      user_prompt: 请总结输入素材。\n"
    "      temperature: 0.2\n"
    "      top_k: 0\n"
    "      max_tokens: 1024\n"
    "      stop: []\n"
    "      seed: 0\n"
    "      output_schema:\n"
    "        summary: string\n"
)

_MINIMAL_LLM_BLOCK = "      model_identifier: qwen-max\n      system_prompt: 请回答。\n"

# 合法 code 配置块：多行块标量脚本 + 依赖 + 沙箱策略 + 显式入口 + 输出结构声明
_CODE_EXTRACT_BLOCK = (
    "      language: python\n"
    "      code: |\n"
    '        def extract(params):\n'
    '            text = params.get("text", "")\n'
    '            return {"result": text.strip()}\n'
    "      entry_function: extract\n"
    "      dependencies:\n"
    "        - requests\n"
    "      sandbox_policy_id: sp_default\n"
    "      output_schema:\n"
    "        result: string\n"
)

# 块标量脚本的精确期望值（逐字符对照：尾随换行由块标量保留）
_EXPECTED_SCRIPT = (
    "def extract(params):\n"
    '    text = params.get("text", "")\n'
    '    return {"result": text.strip()}\n'
)

# trigger → llm → code 混合链路（output_schema 两类节点各自声明，AC-01④ 载体）
_CHAIN_YAML = (
    'spec_version: "1.0"\n'
    "definition_id: llm_code_chain_check\n"
    "name: 大模型与代码节点链路校验\n"
    "definition_version: 1\n"
    "inputs: []\n"
    "outputs: []\n"
    "nodes:\n"
    "  - node_id: trigger_start\n"
    "    name: 手动触发\n"
    "    node_type: trigger\n"
    "    configuration:\n"
    "      trigger_type: manual\n"
    "  - node_id: llm_generate\n"
    "    name: 生成要点\n"
    "    node_type: llm\n"
    "    output_name: summary\n"
    "    configuration:\n"
    "      model_identifier: qwen-max\n"
    "      system_prompt: 你是测试助手。\n"
    "      user_prompt: 请提炼要点。\n"
    "      output_schema:\n"
    "        summary: string\n"
    "        score: number\n"
    "  - node_id: code_extract\n"
    "    name: 加工结果\n"
    "    node_type: code\n"
    "    output_name: refined\n"
    "    configuration:\n"
    "      language: python\n"
    "      code: |\n"
    '        def extract(params):\n'
    '            return {"result": params}\n'
    "      entry_function: extract\n"
    "      output_schema:\n"
    "        result: object\n"
    "edges:\n"
    "  - edge_id: e1\n"
    "    source_node_id: trigger_start\n"
    "    target_node_id: llm_generate\n"
    "  - edge_id: e2\n"
    "    source_node_id: llm_generate\n"
    "    target_node_id: code_extract\n"
)


class TestLlmYamlLoading:
    """AC-01①：llm 节点 YAML 加载成功 + 逐字段往返一致（None 与显式值区分）。"""

    def test_llm_definition_loads_and_round_trips_field_by_field(self, yaml_parser):
        definition = yaml_parser.parse(
            _llm_workflow_yaml(_EXPLICIT_SAMPLING_BLOCK, "llm_loading_explicit")
        )
        assert len(definition.nodes) == 2
        llm_node = definition.nodes[1]
        assert isinstance(llm_node.configuration, LlmNodeConfiguration)
        config = llm_node.configuration
        assert config.node_type == NodeType.LLM
        assert config.model_identifier == "qwen-max"
        assert config.system_prompt == "你是测试助手。"
        assert config.user_prompt == "请总结输入素材。"
        assert config.temperature == 0.2
        assert config.top_p is None
        assert config.top_k == 0
        assert config.max_tokens == 1024
        assert config.stop == []
        assert config.seed == 0
        assert config.output_schema == {"summary": "string"}
        assert config.inputs is None

        # 往返：模型 → JSON 字典 → YAML 文本 → 再解析（承载字段零变更）
        reloaded = yaml_parser.parse(_dump_definition(definition))
        assert _carried_dump(reloaded) == _carried_dump(definition)
        reloaded_config = reloaded.nodes[1].configuration
        assert isinstance(reloaded_config, LlmNodeConfiguration)
        assert reloaded_config == config

    def test_llm_unconfigured_sampling_round_trips_as_none(self, yaml_parser):
        # 未配置采样：六键全部 None（D-01「未配置」），与显式 0/[] 值不混淆
        definition = yaml_parser.parse(
            _llm_workflow_yaml(_MINIMAL_LLM_BLOCK, "llm_loading_unconfigured")
        )
        config = definition.nodes[1].configuration
        assert config.temperature is None
        assert config.top_p is None
        assert config.top_k is None
        assert config.max_tokens is None
        assert config.stop is None
        assert config.seed is None

        reloaded = yaml_parser.parse(_dump_definition(definition))
        assert reloaded.nodes[1].configuration == config

        # 与显式值对照：两份重载配置互不相等（None ≠ 0.2），
        # 「None 与显式缺省区分」的机械判据（D-01 / DC-14）
        explicit = yaml_parser.parse(
            _llm_workflow_yaml(_EXPLICIT_SAMPLING_BLOCK, "llm_loading_explicit")
        )
        assert reloaded.nodes[1].configuration != explicit.nodes[1].configuration

    def test_llm_inputs_three_states_preserved(self, yaml_parser):
        # DC-03 三态：None（隐式全量平铺）/ []（显式空清单）/ 非空（显式选择集）
        explicit_inputs_block = (
            _MINIMAL_LLM_BLOCK
            + "      inputs:\n"
            "        - reference: trigger_start.payload\n"
            "          alias: material\n"
        )
        blocks = {
            "none": _MINIMAL_LLM_BLOCK,
            "empty": _MINIMAL_LLM_BLOCK + "      inputs: []\n",
            "explicit": explicit_inputs_block,
        }
        reloaded_inputs = {}
        for label, block in blocks.items():
            definition = yaml_parser.parse(
                _llm_workflow_yaml(block, f"llm_inputs_{label}")
            )
            reloaded = yaml_parser.parse(_dump_definition(definition))
            reloaded_inputs[label] = reloaded.nodes[1].configuration.inputs

        assert reloaded_inputs["none"] is None
        assert reloaded_inputs["empty"] == []
        assert reloaded_inputs["explicit"][0].reference == "trigger_start.payload"
        assert reloaded_inputs["explicit"][0].alias == "material"
        # 三态互异（机械判据：字符串化互不相等）
        assert len({str(reloaded_inputs[label]) for label in blocks}) == 3


class TestLlmModelIdentifierRejection:
    """AC-01①：缺 model_identifier 加载失败——解析层（缺失）/ 校验层（空白）。"""

    def test_missing_model_identifier_rejected_at_parse_with_location(self, yaml_parser):
        # 缺失键 → pydantic 判别联合在解析层拒绝（v1 任务 2.5 口径），
        # 消息含节点序定位与字段名
        yaml_text = _llm_workflow_yaml(
            "      system_prompt: 请回答。\n", "llm_missing_identifier"
        )
        with pytest.raises(WorkflowValidationError) as excinfo:
            yaml_parser.parse(yaml_text)

        exception = excinfo.value
        assert exception.code == WF_VALIDATION_FAILED
        assert "model_identifier" in exception.message
        assert "nodes.1" in exception.message

    def test_blank_model_identifier_rejected_with_node_id(self, yaml_parser):
        # 空白值（""）解析层放行（合法 str）→ 加载期校验兜底拒绝，
        # issue 携带 node_id 定位 + 语义码前缀 + 字段名（v1 任务 3.1 口径）
        yaml_text = _llm_workflow_yaml(
            '      model_identifier: ""\n      system_prompt: 请回答。\n',
            "llm_blank_identifier",
        )
        definition = yaml_parser.parse(yaml_text)
        issues = NodeConfigurationValidator().validate(definition)

        assert len(issues) == 1
        issue = issues[0]
        assert issue.code == WF_VALIDATION_FAILED
        # 跟随全局语义（2026-11-20 修订 FR-04）：留空 = WARNING 提示
        assert issue.severity == Severity.WARNING
        assert issue.node_id == "llm_generate"
        assert "跟随全局主模型" in issue.message
        assert "model_identifier" in issue.message


class TestCodeYamlLoading:
    """AC-02：code 节点 YAML 加载成功；language=javascript 分层拒绝。"""

    def test_code_definition_loads_and_round_trips_field_by_field(self, yaml_parser):
        definition = yaml_parser.parse(
            _code_workflow_yaml(_CODE_EXTRACT_BLOCK, "code_loading_full")
        )
        assert len(definition.nodes) == 2
        code_node = definition.nodes[1]
        assert isinstance(code_node.configuration, CodeNodeConfiguration)
        config = code_node.configuration
        assert config.node_type == NodeType.CODE
        assert config.language == "python"
        assert config.code == _EXPECTED_SCRIPT  # 多行块标量逐字符保真
        assert config.entry_function == "extract"
        assert config.dependencies == ["requests"]
        assert config.sandbox_policy_id == "sp_default"
        assert config.output_schema == {"result": "string"}
        assert config.inputs is None

        reloaded = yaml_parser.parse(_dump_definition(definition))
        assert _carried_dump(reloaded) == _carried_dump(definition)
        assert reloaded.nodes[1].configuration == config

    def test_javascript_language_parses_but_rejected_by_load_time_validation(
        self, yaml_parser
    ):
        # 解析层不收紧（str 放行——历史非法值可打开修正，design §2.3.2(6)），
        # 加载期校验拒绝并注明「本期仅支持 python」（AC-28）
        javascript_block = _CODE_EXTRACT_BLOCK.replace(
            "      language: python\n", "      language: javascript\n"
        )
        definition = yaml_parser.parse(
            _code_workflow_yaml(javascript_block, "code_javascript_rejected")
        )
        config = definition.nodes[1].configuration
        assert isinstance(config, CodeNodeConfiguration)
        assert config.language == "javascript"

        issues = NodeConfigurationValidator().validate(definition)
        assert len(issues) == 1
        issue = issues[0]
        assert issue.code == WF_VALIDATION_FAILED
        assert issue.severity == Severity.ERROR
        assert issue.node_id == "code_extract"
        assert issue.message.startswith(f"{CODE_SCRIPT_INVALID}: ")
        assert "脚本语言不支持" in issue.message
        assert "javascript" in issue.message
        assert "本期仅支持 python" in issue.message


class TestLlmCodeChainRoundTrip:
    """AC-01④/AC-38：output_schema 透传 + 混合链路加载期校验全通过。"""

    def test_output_schema_round_trips_verbatim_for_both_node_types(self, yaml_parser):
        definition = yaml_parser.parse(_CHAIN_YAML)
        llm_schema = definition.nodes[1].configuration.output_schema
        code_schema = definition.nodes[2].configuration.output_schema
        assert llm_schema == {"summary": "string", "score": "number"}
        assert code_schema == {"result": "object"}

        reloaded = yaml_parser.parse(_dump_definition(definition))
        assert reloaded.nodes[1].configuration.output_schema == {
            "summary": "string",
            "score": "number",
        }
        assert reloaded.nodes[2].configuration.output_schema == {"result": "object"}
        # 文本级透传判据：导出文本原样包含声明键（不重写、不消费，DC-09/DC-14-3）
        dumped = _dump_definition(definition)
        assert "summary: string" in dumped
        assert "score: number" in dumped
        assert "result: object" in dumped

    def test_chain_definition_passes_load_time_validation(self, yaml_parser):
        # trigger → llm → code 混合链路加载期校验零问题（8.2/8.3 场景前置）
        definition = yaml_parser.parse(_CHAIN_YAML)
        assert NodeConfigurationValidator().validate(definition) == []


class TestLegacyDefinitionZeroChange:
    """AC-38③：旧定义（不含新节点）加载/导出零变更回归。"""

    @pytest.mark.parametrize("fixture_name", LEGACY_FIXTURE_NAMES)
    def test_legacy_fixture_load_export_zero_change(
        self, yaml_parser, valid_definitions_dir, fixture_name
    ):
        original_text = (valid_definitions_dir / fixture_name).read_text(
            encoding="utf-8"
        )
        first = yaml_parser.parse(original_text)
        first_carried = _carried_dump(first)

        # 回归前置判据：旧定义不含新节点类型
        assert all(
            node.node_type not in (NodeType.LLM, NodeType.CODE)
            for node in first.nodes
        )

        # 导出（承载字段）→ 再加载 → 承载字段逐键零变更 + 文本级定点
        dumped = yaml.safe_dump(first_carried, allow_unicode=True, sort_keys=False)
        second = yaml_parser.parse(dumped)
        assert _carried_dump(second) == first_carried
        assert (
            yaml.safe_dump(_carried_dump(second), allow_unicode=True, sort_keys=False)
            == dumped
        )
