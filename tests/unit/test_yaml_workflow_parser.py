# =============================================================================
# YAML 工作流解析器单元测试（tests/unit/test_yaml_workflow_parser.py）
#
# 覆盖验收：A1（YAML 解析与五要素结构校验）
# =============================================================================

import pytest

from orchestration_platform.domain.errors import WorkflowValidationError
from orchestration_platform.domain.models.node_configurations import AgentNodeConfiguration


class TestYamlWorkflowParser:
    """YAML 解析器测试。"""

    def test_parse_valid_sequential(self, yaml_parser, sequential_definition_path):
        """合法四节点顺序定义应解析成功。"""
        yaml_text = sequential_definition_path.read_text(encoding="utf-8")
        definition = yaml_parser.parse(yaml_text)

        assert definition.definition_id == "sequential_four_nodes"
        assert definition.name == "四节点顺序流水线"
        assert len(definition.nodes) == 4
        assert len(definition.edges) == 3

    def test_parse_valid_loop(self, yaml_parser, loop_definition_path):
        """合法含循环定义应解析成功。"""
        yaml_text = loop_definition_path.read_text(encoding="utf-8")
        definition = yaml_parser.parse(yaml_text)

        assert definition.definition_id == "valid_with_loop"
        assert len(definition.nodes) == 5

    def test_parse_all_valid_fixtures(self, yaml_parser, valid_definitions_dir):
        """全部合法 fixture 应解析成功。"""
        valid_files = sorted(valid_definitions_dir.glob("*.yaml"))
        assert len(valid_files) >= 5

        for yaml_file in valid_files:
            yaml_text = yaml_file.read_text(encoding="utf-8")
            definition = yaml_parser.parse(yaml_text)
            assert definition.spec_version == "1.0"
            assert definition.definition_id
            assert definition.nodes

    def test_parse_all_invalid_fixtures_rejected(self, yaml_parser, invalid_definitions_dir):
        """全部非法 fixture 应在解析或编译阶段被拒绝。"""
        import asyncio
        from orchestration_platform.engine.graph_compiler import GraphCompiler

        invalid_files = sorted(invalid_definitions_dir.glob("*.yaml"))
        assert len(invalid_files) >= 5

        for yaml_file in invalid_files:
            yaml_text = yaml_file.read_text(encoding="utf-8")
            try:
                definition = yaml_parser.parse(yaml_text)
            except WorkflowValidationError:
                continue
            compiler = GraphCompiler()
            with pytest.raises(WorkflowValidationError):
                asyncio.run(compiler.compile(definition))

    def test_parse_empty_yaml_raises(self, yaml_parser):
        """空 YAML 应抛出校验异常。"""
        with pytest.raises(WorkflowValidationError):
            yaml_parser.parse("")

    def test_parse_missing_spec_version_raises(self, yaml_parser):
        """缺少 spec_version 应抛出校验异常。"""
        yaml_text = """
definition_id: test
name: 测试
definition_version: 1
nodes: []
edges: []
"""
        with pytest.raises(WorkflowValidationError):
            yaml_parser.parse(yaml_text)

    def test_parse_unsupported_version_raises(self, yaml_parser):
        """不支持的规范版本应抛出校验异常。"""
        yaml_text = """
spec_version: "9.9"
definition_id: test
name: 测试
definition_version: 1
nodes: []
edges: []
"""
        with pytest.raises(WorkflowValidationError):
            yaml_parser.parse(yaml_text)

    def test_node_types_correctly_parsed(self, yaml_parser, sequential_definition_path):
        """节点类型应正确解析为枚举值。"""
        yaml_text = sequential_definition_path.read_text(encoding="utf-8")
        definition = yaml_parser.parse(yaml_text)

        node_types = {node.node_type for node in definition.nodes}
        assert "trigger" in node_types
        assert "data_source" in node_types
        assert "agent" in node_types
        assert "tool" in node_types

    def test_parse_agent_inputs_explicit_selection(self, yaml_parser):
        """含非空 inputs 选择集的 agent YAML 应解析成功且 reference/alias 回读正确。"""
        yaml_text = """
spec_version: "1.0"
definition_id: agent_inputs_explicit
name: Agent选择集用例
definition_version: 1

inputs:
  - name: topic
    type: string
    required: true
    description: 分析主题

outputs:
  - name: analysis
    type: object
    description: 分析结果

nodes:
  - node_id: trigger_manual
    name: 手动触发
    node_type: trigger
    configuration:
      trigger_type: manual

  - node_id: analyze_data
    name: 数据分析
    node_type: agent
    output_name: analysis
    configuration:
      model_identifier: primary-model
      system_prompt: 请分析输入数据。
      inputs:
        - reference: trigger_manual
          alias: 触发上下文
        - reference: trigger_manual.topic

edges:
  - edge_id: e1
    source_node_id: trigger_manual
    target_node_id: analyze_data
"""
        definition = yaml_parser.parse(yaml_text)

        agent_node = next(n for n in definition.nodes if n.node_id == "analyze_data")
        config = agent_node.configuration
        assert isinstance(config, AgentNodeConfiguration)
        assert config.inputs is not None
        assert len(config.inputs) == 2
        assert config.inputs[0].reference == "trigger_manual"
        assert config.inputs[0].alias == "触发上下文"
        assert config.inputs[1].reference == "trigger_manual.topic"
        assert config.inputs[1].alias is None

    def test_parse_agent_inputs_empty_list(self, yaml_parser):
        """含 inputs: [] 的 YAML 应解析为空列表（显式空清单语义，非 None）。"""
        yaml_text = """
spec_version: "1.0"
definition_id: agent_inputs_empty
name: Agent空清单用例
definition_version: 1

inputs:
  - name: topic
    type: string
    required: true
    description: 分析主题

outputs:
  - name: analysis
    type: object
    description: 分析结果

nodes:
  - node_id: trigger_manual
    name: 手动触发
    node_type: trigger
    configuration:
      trigger_type: manual

  - node_id: analyze_data
    name: 数据分析
    node_type: agent
    output_name: analysis
    configuration:
      model_identifier: primary-model
      inputs: []

edges:
  - edge_id: e1
    source_node_id: trigger_manual
    target_node_id: analyze_data
"""
        definition = yaml_parser.parse(yaml_text)

        agent_node = next(n for n in definition.nodes if n.node_id == "analyze_data")
        config = agent_node.configuration
        assert isinstance(config, AgentNodeConfiguration)
        assert config.inputs == []

    def test_parse_legacy_agent_without_inputs_is_none(self, yaml_parser, sequential_definition_path):
        """旧 YAML（agent 节点无 inputs 字段）应解析为 None、零报错（INV-05③）。"""
        yaml_text = sequential_definition_path.read_text(encoding="utf-8")
        definition = yaml_parser.parse(yaml_text)

        agent_node = next(n for n in definition.nodes if n.node_type == "agent")
        config = agent_node.configuration
        assert isinstance(config, AgentNodeConfiguration)
        assert config.inputs is None
