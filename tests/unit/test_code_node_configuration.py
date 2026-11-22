# =============================================================================
# 代码节点配置单元测试（llm_and_code_nodes 任务 2.4/2.5/2.6/2.8/3.2）
# 执行命令：PYTHONPATH=src python -m pytest tests/unit/test_code_node_configuration.py -q
#
# 覆盖：
#   - 语言白名单 SUPPORTED_SCRIPT_LANGUAGES 仅 python（DC-22 细化）；
#   - CodeNodeConfiguration 字段白名单 / code 必填且无默认值（禁止预置
#     可执行样例，spec §6.2-9）/ language 模型层不收紧（AC-28 消息可控性
#     前提）/ dependencies 仅登记 / sandbox_policy_id 与 agent 同名同义；
#   - 禁止字段不声明：api_key / runtime_image / network_allowlist /
#     install_commands（G-08 红线）；
#   - NodeDefinition 注入解析 + 既有 tool dump 等价（任务 2.5）；
#   - 代码节点 4 个错误码逐字符 + 与 DATA_TRANSFORM_* 可区分（OP-02）；
#   - 加载期校验接入：语言白名单 / 脚本非空与字节上界 / 静态合规守卫（复用
#     validate_custom_code 只调用不修改，G-03）/ 依赖与入口格式 / 输出结构值域，
#     语义码前缀 CODE_SCRIPT_INVALID / CODE_SCRIPT_TOO_LARGE（任务 3.2 /
#     CHG-06 / D-04 / FR-25~FR-28 / AC-28~AC-32）。
# =============================================================================

# 沙箱限制：本机 asyncio 事件循环不可用（socketpair 被网络策略拦截，
# WinError 10051 / _ssock AttributeError），run_coro 采用手动驱动协程
# 方式（send(None) 循环），不创建事件循环，故无需 import asyncio。
# 本轮尚无异步用例，按任务 2.8 要求保持文件结构一致，供后续批次复用。

import pytest
from pydantic import ValidationError
from pydantic_core import PydanticUndefined

from orchestration_platform.domain.errors import (
    CODE_NODE_DISABLED,
    CODE_SCRIPT_INVALID,
    CODE_SCRIPT_NOT_EXECUTABLE,
    CODE_SCRIPT_TOO_LARGE,
    DATA_TRANSFORM_SCRIPT_NOT_EXECUTABLE,
    WF_VALIDATION_FAILED,
)
from orchestration_platform.domain.models.enums import NodeType, Severity
from orchestration_platform.domain.models.node_configurations import (
    SUPPORTED_SCRIPT_LANGUAGES,
    CodeNodeConfiguration,
)
from orchestration_platform.domain.models.node_definition import NodeDefinition
from orchestration_platform.domain.models.workflow_definition import WorkflowDefinition
from orchestration_platform.domain.validation.node_configuration_validator import (
    NodeConfigurationValidator,
)
from orchestration_platform.engine.nodes.data_transform_ops.custom_code_guard import (
    validate_custom_code,
)


def run_coro(coro):
    """手动驱动协程至完成（沙箱事件循环不可用时的 fallback）。

    execute 的 await 链均为 AsyncMock 或纯同步逻辑协程，无真实挂起点，
    故以 send(None) 循环驱动至 StopIteration，环境无关。
    """
    try:
        while True:
            coro.send(None)
    except StopIteration as exc:
        return exc.value


# =============================================================================
# 语言白名单（任务 2.4 / DC-22 细化）
# =============================================================================


class TestSupportedScriptLanguages:
    """脚本语言白名单：本期仅 python，校验器以此生成定制消息。"""

    def test_supported_languages_python_only(self):
        assert SUPPORTED_SCRIPT_LANGUAGES == ("python",)
        assert isinstance(SUPPORTED_SCRIPT_LANGUAGES, tuple)


# =============================================================================
# CodeNodeConfiguration 模型（任务 2.4 / CHG-02 / spec §6.2）
# =============================================================================


class TestCodeNodeConfigurationModel:
    """代码节点配置：字段白名单 / 必备项 / 缺省 / 语言不收紧。"""

    def test_field_whitelist(self):
        assert set(CodeNodeConfiguration.model_fields.keys()) == {
            "node_type",
            "language",
            "code",
            "entry_function",
            "dependencies",
            "inputs",
            "output_schema",
            "sandbox_policy_id",
        }

    @pytest.mark.parametrize(
        "forbidden",
        ["api_key", "runtime_image", "network_allowlist", "install_commands"],
    )
    def test_forbidden_fields_not_declared(self, forbidden):
        # spec §6.2-9：禁止字段建模时不声明即不可配置（G-08 红线）
        assert forbidden not in CodeNodeConfiguration.model_fields

    def test_code_required_without_default(self):
        # spec §6.2-9：code 必填且无默认值——禁止预置可执行样例（AC-34）
        field = CodeNodeConfiguration.model_fields["code"]
        assert field.is_required() is True
        assert field.default is PydanticUndefined

    def test_missing_code_rejected(self):
        with pytest.raises(ValidationError) as excinfo:
            CodeNodeConfiguration(language="python")
        assert excinfo.value.errors()[0]["type"] == "missing"
        assert excinfo.value.errors()[0]["loc"] == ("code",)

    def test_language_defaults_to_python(self):
        config = CodeNodeConfiguration(code="value = 1")
        assert config.node_type == NodeType.CODE
        assert config.language == "python"

    def test_language_not_restricted_at_model_layer(self):
        # DC-22 细化：language 为 str 不收紧，历史非法值可被打开修正（AC-28 前提）
        config = CodeNodeConfiguration(code="value = 1", language="javascript")
        assert config.language == "javascript"

    def test_entry_function_defaults_to_main(self):
        config = CodeNodeConfiguration(code="value = 1")
        assert config.entry_function == "main"
        config = CodeNodeConfiguration(code="value = 1", entry_function="run")
        assert config.entry_function == "run"

    def test_dependencies_default_none_and_preserved(self):
        config = CodeNodeConfiguration(code="value = 1")
        assert config.dependencies is None
        config = CodeNodeConfiguration(code="value = 1", dependencies=["numpy", "pandas"])
        assert config.dependencies == ["numpy", "pandas"]

    def test_sandbox_policy_id_default_none(self):
        config = CodeNodeConfiguration(code="value = 1")
        assert config.sandbox_policy_id is None
        config = CodeNodeConfiguration(code="value = 1", sandbox_policy_id="policy-1")
        assert config.sandbox_policy_id == "policy-1"

    def test_inputs_and_output_schema_constructed(self):
        config = CodeNodeConfiguration(
            code="value = 1",
            inputs=[{"reference": "upstream.result"}],
            output_schema={"result": "object"},
        )
        assert config.inputs is not None
        assert config.inputs[0].reference == "upstream.result"
        assert config.output_schema == {"result": "object"}

    def test_code_configuration_parsed_via_node_definition(self):
        definition = NodeDefinition(
            node_id="code-1",
            name="etl",
            node_type=NodeType.CODE,
            configuration={"code": "value = 1", "dependencies": ["numpy"]},
        )
        assert isinstance(definition.configuration, CodeNodeConfiguration)
        assert definition.configuration.dependencies == ["numpy"]

    def test_tool_definition_dump_equivalent_to_baseline(self):
        definition = NodeDefinition(
            node_id="tool-1",
            name="call",
            node_type=NodeType.TOOL,
            configuration={"tool_identifier": "search"},
        )
        assert definition.configuration.model_dump() == {
            "node_type": NodeType.TOOL,
            "tool_identifier": "search",
            "tool_source": "local",
            "argument_mapping": {},
            "static_arguments": {},
        }


# =============================================================================
# 代码节点错误码（任务 2.6 / CHG-04 / OP-02）
# =============================================================================


class TestCodeErrorCodes:
    """代码节点 4 个错误码逐字符；与 DATA_TRANSFORM_* 并列不复用。"""

    def test_four_code_error_codes_exact(self):
        assert CODE_NODE_DISABLED == "CODE_NODE_DISABLED"
        assert CODE_SCRIPT_INVALID == "CODE_SCRIPT_INVALID"
        assert CODE_SCRIPT_TOO_LARGE == "CODE_SCRIPT_TOO_LARGE"
        assert CODE_SCRIPT_NOT_EXECUTABLE == "CODE_SCRIPT_NOT_EXECUTABLE"

    def test_code_error_codes_distinct(self):
        assert (
            len(
                {
                    CODE_NODE_DISABLED,
                    CODE_SCRIPT_INVALID,
                    CODE_SCRIPT_TOO_LARGE,
                    CODE_SCRIPT_NOT_EXECUTABLE,
                }
            )
            == 4
        )
        assert CODE_SCRIPT_NOT_EXECUTABLE != DATA_TRANSFORM_SCRIPT_NOT_EXECUTABLE


# =============================================================================
# 加载期校验（任务 3.2 / CHG-06 / D-04 / G-03：守卫只调用不修改）
# =============================================================================


def _make_code_definition(*configs: dict) -> WorkflowDefinition:
    """构造仅含代码节点的测试定义（无连线：引用规则零登记，聚焦节点级校验）。"""
    nodes = [
        NodeDefinition(
            node_id=f"code-{index}",
            node_type=NodeType.CODE,
            configuration=config,
        )
        for index, config in enumerate(configs)
    ]
    return WorkflowDefinition(
        definition_id="wf-code-validation",
        name="代码节点校验测试",
        spec_version="1.0",
        nodes=nodes,
        edges=[],
        inputs=[],
    )


# 六项命中脚本（静态守卫 ast.walk 序：3 敏感导入 + eval + exec + open）
_SIX_FINDINGS_SCRIPT = (
    "import os\n"
    "import sys\n"
    "import socket\n"
    "value = eval('1')\n"
    "exec('2')\n"
    "open('f')\n"
)


class TestValidateCodeNode:
    """代码节点加载期校验：语言 / 脚本 / 静态守卫 / 依赖 / 入口 / 输出结构。

    语义错误码作消息前缀（CODE_SCRIPT_INVALID / CODE_SCRIPT_TOO_LARGE，
    D-04），issue.code 统一 WF_VALIDATION_FAILED、severity=ERROR；守卫
    validate_custom_code 只调用不修改（G-03 指纹零变更）。
    """

    def setup_method(self):
        self.validator = NodeConfigurationValidator()

    # ------------------------------------------------------------------
    # 语言取值域（FR-25 / AC-28）
    # ------------------------------------------------------------------
    def test_unsupported_language_rejected_with_tailored_message(self):
        definition = _make_code_definition(
            {"code": "value = 1", "language": "javascript"}
        )
        issues = self.validator.validate(definition)

        assert len(issues) == 1
        issue = issues[0]
        assert issue.code == WF_VALIDATION_FAILED
        assert issue.severity == Severity.ERROR
        assert issue.node_id == "code-0"
        assert issue.message.startswith(f"{CODE_SCRIPT_INVALID}: ")
        assert "脚本语言不支持" in issue.message
        assert "javascript" in issue.message
        assert "本期仅支持 python" in issue.message

    def test_python_language_passes(self):
        definition = _make_code_definition(
            {"code": "value = 1", "language": "python"}
        )
        assert self.validator.validate(definition) == []

    def test_validate_node_dispatches_code(self):
        # 分发接线反证：未接线时 _validate_node 静默返回 []（G-08 红线）
        node = NodeDefinition(
            node_id="code-0",
            node_type=NodeType.CODE,
            configuration={"code": "value = 1", "language": "javascript"},
        )
        issues = self.validator._validate_node(node)
        assert issues
        assert issues[0].message.startswith(f"{CODE_SCRIPT_INVALID}: ")

    # ------------------------------------------------------------------
    # 脚本文本：非空（FR-04b）/ UTF-8 字节上界（FR-26 / AC-29）
    # ------------------------------------------------------------------
    @pytest.mark.parametrize("code", ["", "   \n\t"])
    def test_empty_script_rejected(self, code):
        definition = _make_code_definition({"code": code})
        issues = self.validator.validate(definition)

        assert len(issues) == 1
        issue = issues[0]
        assert issue.severity == Severity.ERROR
        assert issue.node_id == "code-0"
        assert issue.message.startswith(f"{CODE_SCRIPT_INVALID}: ")
        assert "脚本文本为空" in issue.message

    def test_script_over_byte_limit_rejected(self):
        validator = NodeConfigurationValidator(code_node_max_script_bytes=16)
        script = "value = " + "1" * 14  # 8 + 14 = 22 字节
        assert len(script.encode("utf-8")) == 22
        definition = _make_code_definition({"code": script})

        issues = validator.validate(definition)

        assert len(issues) == 1
        issue = issues[0]
        assert issue.code == WF_VALIDATION_FAILED
        assert issue.severity == Severity.ERROR
        assert issue.message.startswith(f"{CODE_SCRIPT_TOO_LARGE}: ")
        assert "脚本长度 22 字节超出上界 16 字节" in issue.message
        # 超界路径跳过静态守卫（尺寸违规已拦截，无静默通过路径）
        assert "静态校验" not in issue.message

    def test_script_within_and_at_byte_limit_passes(self):
        validator = NodeConfigurationValidator(code_node_max_script_bytes=64)
        within = _make_code_definition({"code": "value = " + "1" * 14})  # 22 字节
        assert validator.validate(within) == []

        at_limit_script = "x = " + "1" * 60  # 4 + 60 = 64 字节（边界含）
        assert len(at_limit_script.encode("utf-8")) == 64
        at_limit = _make_code_definition({"code": at_limit_script})
        assert validator.validate(at_limit) == []

    def test_default_byte_limit_rejects_over_64kib(self):
        # 缺省上界 = 65536 字节（与 settings 配置项默认一致，任务 3.2 构造注入）
        script = "value = 1\n" + "#" + "x" * 65530  # 10 + 1 + 65530 = 65541 字节
        assert len(script.encode("utf-8")) == 65541
        definition = _make_code_definition({"code": script})

        issues = self.validator.validate(definition)

        assert len(issues) == 1
        issue = issues[0]
        assert issue.severity == Severity.ERROR
        assert issue.message.startswith(f"{CODE_SCRIPT_TOO_LARGE}: ")
        assert "脚本长度 65541 字节超出上界 65536 字节" in issue.message

    # ------------------------------------------------------------------
    # 静态合规守卫（AC-31 / AC-32 / G-03：validate_custom_code 只调用不修改）
    # ------------------------------------------------------------------
    def test_guard_finding_import_rejected(self):
        definition = _make_code_definition({"code": "import os\nvalue = 1"})
        issues = self.validator.validate(definition)

        assert len(issues) == 1
        issue = issues[0]
        assert issue.severity == Severity.ERROR
        assert issue.message.startswith(f"{CODE_SCRIPT_INVALID}: ")
        assert "脚本静态校验不合规" in issue.message
        assert "共 1 项命中" in issue.message
        assert "敏感模块导入：import os" in issue.message

    def test_guard_compliant_script_passes(self):
        definition = _make_code_definition({"code": "def main():\n    return 1\n"})
        assert self.validator.validate(definition) == []

    def test_guard_findings_display_capped_at_five(self):
        definition = _make_code_definition({"code": _SIX_FINDINGS_SCRIPT})
        issues = self.validator.validate(definition)

        assert len(issues) == 1
        message = issues[0].message
        assert "共 6 项命中" in message
        assert "敏感模块导入：import os" in message
        assert "禁用调用：eval()" in message
        assert "等 1 项" in message
        # 展示截断：第 6 条命中项（open）不进入消息（前 5 条 + 计数）
        assert "禁用调用：open()" not in message

    def test_guard_findings_consistent_with_direct_call(self):
        # G-03 一致性：校验器消费的命中项与守卫直调结果逐条一致
        verdict = validate_custom_code(_SIX_FINDINGS_SCRIPT, "python")
        assert not verdict.compliant
        assert len(verdict.findings) == 6

        definition = _make_code_definition({"code": _SIX_FINDINGS_SCRIPT})
        issues = self.validator.validate(definition)

        assert len(issues) == 1
        message = issues[0].message
        for finding in verdict.findings[:5]:
            assert finding in message

    def test_guard_internal_error_treated_as_non_compliant(self, monkeypatch):
        # spec 5.5.3-4：校验器异常不得放行（按不合规 + 内部错误处理）
        def _raise(code: str, language: str):
            raise RuntimeError("guard boom")

        monkeypatch.setattr(
            "orchestration_platform.engine.nodes.data_transform_ops.custom_code_guard.validate_custom_code",
            _raise,
        )
        definition = _make_code_definition({"code": "value = 1"})
        issues = self.validator.validate(definition)

        assert len(issues) == 1
        issue = issues[0]
        assert issue.severity == Severity.ERROR
        assert issue.message.startswith(f"{CODE_SCRIPT_INVALID}: ")
        assert "内部错误" in issue.message
        assert "不合规" in issue.message

    # ------------------------------------------------------------------
    # 依赖清单（FR-27：条数上界 / 去重保序 / 仅名称）
    # ------------------------------------------------------------------
    def test_dependency_count_over_limit_rejected(self):
        dependencies = [f"dep_{index:02d}" for index in range(33)]
        definition = _make_code_definition(
            {"code": "value = 1", "dependencies": dependencies}
        )
        issues = self.validator.validate(definition)

        assert len(issues) == 1
        issue = issues[0]
        assert issue.severity == Severity.ERROR
        assert issue.message.startswith(f"{CODE_SCRIPT_INVALID}: ")
        assert "依赖条数 33 超出上界 32" in issue.message

    def test_dependency_count_at_limit_passes(self):
        dependencies = [f"dep_{index:02d}" for index in range(32)]
        definition = _make_code_definition(
            {"code": "value = 1", "dependencies": dependencies}
        )
        assert self.validator.validate(definition) == []

    def test_duplicate_dependencies_rejected(self):
        definition = _make_code_definition(
            {
                "code": "value = 1",
                "dependencies": ["numpy", "pandas", "numpy", "numpy", "pandas"],
            }
        )
        issues = self.validator.validate(definition)

        assert len(issues) == 1
        issue = issues[0]
        assert issue.severity == Severity.ERROR
        assert "依赖清单存在重复条目" in issue.message
        assert "'numpy'、'pandas'" in issue.message
        assert "去重保序" in issue.message

    @pytest.mark.parametrize(
        "dependency",
        ["numpy==1.26.0", "pandas>=2.0", "scikit-learn~=1.3", "a;b", "pkg@2.1.0"],
    )
    def test_versioned_dependency_rejected(self, dependency):
        definition = _make_code_definition(
            {"code": "value = 1", "dependencies": [dependency]}
        )
        issues = self.validator.validate(definition)

        assert len(issues) == 1
        issue = issues[0]
        assert issue.severity == Severity.ERROR
        assert "版本信息" in issue.message
        assert "依赖仅登记名称" in issue.message
        assert repr(dependency) in issue.message

    def test_valid_dependencies_pass(self):
        definition = _make_code_definition(
            {
                "code": "value = 1",
                "dependencies": ["numpy", "pandas", "scikit-learn"],
            }
        )
        assert self.validator.validate(definition) == []

    # ------------------------------------------------------------------
    # 入口函数名格式（FR-28：展示语义，本期无执行效果）
    # ------------------------------------------------------------------
    @pytest.mark.parametrize(
        "entry_function",
        ["1main", "main-fn", "main fn", "a" * 65, ""],
    )
    def test_invalid_entry_function_rejected(self, entry_function):
        definition = _make_code_definition(
            {"code": "value = 1", "entry_function": entry_function}
        )
        issues = self.validator.validate(definition)

        assert len(issues) == 1
        issue = issues[0]
        assert issue.severity == Severity.ERROR
        assert "入口函数名" in issue.message
        assert "非法" in issue.message
        assert "64 字符" in issue.message

    @pytest.mark.parametrize("entry_function", ["main", "run_2", "_private"])
    def test_valid_entry_function_passes(self, entry_function):
        definition = _make_code_definition(
            {"code": "value = 1", "entry_function": entry_function}
        )
        assert self.validator.validate(definition) == []

    def test_entry_function_at_64_chars_passes(self):
        definition = _make_code_definition(
            {"code": "value = 1", "entry_function": "a" * 64}
        )
        assert self.validator.validate(definition) == []

    # ------------------------------------------------------------------
    # 输出结构类型名值域（design §2.3.2(2)：声明语义，值域同 llm）
    # ------------------------------------------------------------------
    def test_output_schema_registered_types_pass(self):
        definition = _make_code_definition(
            {
                "code": "value = 1",
                "output_schema": {"result": "string", "count": "int"},
            }
        )
        assert self.validator.validate(definition) == []

    def test_output_schema_unregistered_type_rejected(self):
        definition = _make_code_definition(
            {"code": "value = 1", "output_schema": {"result": "blob"}}
        )
        issues = self.validator.validate(definition)

        assert len(issues) == 1
        issue = issues[0]
        assert issue.severity == Severity.ERROR
        assert issue.message.startswith(f"{CODE_SCRIPT_INVALID}: ")
        assert "'blob'" in issue.message
        assert "未登记" in issue.message

    # ------------------------------------------------------------------
    # 多问题聚合（DC-04：逐项全部返回）+ 合法节点全过
    # ------------------------------------------------------------------
    def test_multiple_code_issues_all_reported(self):
        definition = _make_code_definition(
            {
                "code": "",
                "language": "javascript",
                "entry_function": "1main",
                "dependencies": ["numpy==1.26.0"],
                "output_schema": {"result": "blob"},
            }
        )
        issues = self.validator.validate(definition)

        assert len(issues) == 5
        assert all(issue.severity == Severity.ERROR for issue in issues)
        assert all(issue.node_id == "code-0" for issue in issues)
        messages = [issue.message for issue in issues]
        assert any("脚本语言不支持" in message for message in messages)
        assert any("脚本文本为空" in message for message in messages)
        assert any("依赖条目含版本信息" in message for message in messages)
        assert any("入口函数名" in message for message in messages)
        assert any("未登记" in message for message in messages)

    def test_valid_code_node_passes(self):
        definition = _make_code_definition(
            {
                "code": "def main(input_value):\n    return input_value + 1\n",
                "entry_function": "main",
                "dependencies": ["numpy"],
                "output_schema": {"result": "integer"},
            }
        )
        assert self.validator.validate(definition) == []
