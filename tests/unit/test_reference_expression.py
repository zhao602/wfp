# =============================================================================
# 引用表达式解析器单元测试（tests/unit/test_reference_expression.py）
#
# 覆盖 unified_node_input 批次 1（任务 T1.3 / CHG-T01）：
#   - 基线形态（无点号，0 段）/ 扩展形态（1 段、多段嵌套）
#   - trim 净化（首尾空白）
#   - 边界用例：空串 → None / 总长 257 → None / 9 段 → None /
#     段含空白或空段 → None / 首段非小写字母开头 → None / 首段超 64 字符 → None
#   - 纯函数性质（同输入两次调用结果相等，INV-D01）
#   + upstream_input_ref 批次 1（任务 T1.3 / CHG-T01，DC-01 形态 B 扩展）：
#     - 既有表达式零回归对照集（INV-E01 阻断级，逐字段等价断言）
#     - 形态 B 合法/非法/边界（`NODE_ID::POSITION_NAME[.字段路径]`）
#     - 消歧关键用例单列（`result.a::b` → 形态 A，只看首段）
#     - 形态 B 文法常量（INPUT_POSITION_SEPARATOR / NODE_ID_PATTERN）
#
# 设计依据：spec FR-03 / §9.1，design DC-02 / INV-D01 / INV-09；
#           upstream_input_ref spec §9.1 / design DC-01 / §2.2 / INV-E01。
# =============================================================================

from orchestration_platform.domain.models.enums import NodeType
from orchestration_platform.domain.values.node_output_categories import (
    NODE_OUTPUT_CATEGORIES,
    resolve_output_category,
)
from orchestration_platform.domain.values.reference_expression import (
    INPUT_POSITION_SEPARATOR,
    MAX_EXPRESSION_LENGTH,
    MAX_PATH_SEGMENTS,
    NODE_ID_PATTERN,
    OUTPUT_NAME_PATTERN,
    ParsedReference,
    parse_reference,
)


class TestParseReferenceBaseline:
    """基线形态：命名输出名顶格键（无点号，0 段）。"""

    def test_baseline_returns_empty_segments(self):
        parsed = parse_reference("result")
        assert parsed is not None
        assert parsed.output_name == "result"
        assert parsed.path_segments == ()

    def test_baseline_with_underscore_and_digits(self):
        parsed = parse_reference("raw_data_2")
        assert parsed is not None
        assert parsed.output_name == "raw_data_2"
        assert parsed.path_segments == ()


class TestParseReferenceExtended:
    """扩展形态：命名输出名 + 字段路径。"""

    def test_single_segment(self):
        parsed = parse_reference("result.summary")
        assert parsed is not None
        assert parsed.output_name == "result"
        assert parsed.path_segments == ("summary",)

    def test_multiple_nested_segments(self):
        parsed = parse_reference("result.summary.deep")
        assert parsed is not None
        assert parsed.output_name == "result"
        assert parsed.path_segments == ("summary", "deep")

    def test_max_segments_boundary(self):
        reference = "a." + ".".join(f"s{i}" for i in range(MAX_PATH_SEGMENTS))
        parsed = parse_reference(reference)
        assert parsed is not None
        assert parsed.output_name == "a"
        assert len(parsed.path_segments) == MAX_PATH_SEGMENTS


class TestParseReferenceTrim:
    """trim 净化：首尾空白。"""

    def test_leading_and_trailing_whitespace_stripped(self):
        parsed = parse_reference("  result  ")
        assert parsed is not None
        assert parsed.output_name == "result"
        assert parsed.path_segments == ()

    def test_extended_form_with_surrounding_whitespace(self):
        parsed = parse_reference("\tresult.summary\n")
        assert parsed is not None
        assert parsed.output_name == "result"
        assert parsed.path_segments == ("summary",)

    def test_whitespace_only_returns_none(self):
        assert parse_reference("   ") is None


class TestParseReferenceInvalid:
    """语法非法用例：全部返回 None。"""

    def test_empty_string_returns_none(self):
        assert parse_reference("") is None

    def test_length_257_returns_none(self):
        reference = "a." + "b" * (MAX_EXPRESSION_LENGTH - 1)
        assert len(reference) == MAX_EXPRESSION_LENGTH + 1
        assert parse_reference(reference) is None

    def test_length_256_boundary_parses(self):
        reference = "a." + "b" * (MAX_EXPRESSION_LENGTH - 2)
        assert len(reference) == MAX_EXPRESSION_LENGTH
        parsed = parse_reference(reference)
        assert parsed is not None

    def test_nine_segments_returns_none(self):
        reference = "a." + ".".join(f"s{i}" for i in range(MAX_PATH_SEGMENTS + 1))
        assert parse_reference(reference) is None

    def test_segment_with_inner_whitespace_returns_none(self):
        assert parse_reference("result.a b") is None

    def test_empty_path_segment_returns_none(self):
        assert parse_reference("result..summary") is None

    def test_trailing_dot_returns_none(self):
        assert parse_reference("result.") is None

    def test_uppercase_first_char_returns_none(self):
        assert parse_reference("Result") is None

    def test_digit_first_char_returns_none(self):
        assert parse_reference("1result") is None

    def test_underscore_first_char_returns_none(self):
        assert parse_reference("_result") is None

    def test_first_segment_over_64_chars_returns_none(self):
        assert parse_reference("r" * 65) is None

    def test_first_segment_64_chars_boundary_parses(self):
        parsed = parse_reference("r" * 64)
        assert parsed is not None
        assert parsed.output_name == "r" * 64


class TestParseReferencePurity:
    """纯函数性质：同输入两次调用结果相等（INV-D01）。"""

    def test_same_input_yields_equal_result(self):
        first = parse_reference("result.summary.deep")
        second = parse_reference("result.summary.deep")
        assert first == second

    def test_grammar_constants_exposed(self):
        assert OUTPUT_NAME_PATTERN.match("valid_name") is not None
        assert MAX_PATH_SEGMENTS == 8
        assert MAX_EXPRESSION_LENGTH == 256


class TestFormAZeroRegression:
    """既有表达式零回归对照集（upstream_input_ref INV-E01 阻断级）。

    以既有 26 用例覆盖的表达式集为样本，逐条断言「新解析结果与原实现结果
    逐字段相等」（kind / output_name / path_segments），并断言 input_position
    is None——ParsedReference 全字段等价断言（frozen dataclass ==）。
    """

    def test_baseline_expressions_field_equivalent(self):
        samples = [
            ("result", "result", ()),
            ("raw_data_2", "raw_data_2", ()),
        ]
        for expression, output_name, path_segments in samples:
            parsed = parse_reference(expression)
            assert parsed == ParsedReference(
                output_name=output_name, path_segments=path_segments
            )

    def test_extended_expressions_field_equivalent(self):
        samples = [
            ("result.summary", "result", ("summary",)),
            ("result.summary.deep", "result", ("summary", "deep")),
        ]
        for expression, output_name, path_segments in samples:
            parsed = parse_reference(expression)
            assert parsed == ParsedReference(
                output_name=output_name, path_segments=path_segments
            )

    def test_trimmed_expressions_field_equivalent(self):
        samples = [
            ("  result  ", "result", ()),
            ("\tresult.summary\n", "result", ("summary",)),
        ]
        for expression, output_name, path_segments in samples:
            parsed = parse_reference(expression)
            assert parsed == ParsedReference(
                output_name=output_name, path_segments=path_segments
            )

    def test_boundary_expressions_field_equivalent(self):
        max_segments = "a." + ".".join(f"s{i}" for i in range(MAX_PATH_SEGMENTS))
        boundary_length = "a." + "b" * (MAX_EXPRESSION_LENGTH - 2)
        samples = [
            (
                max_segments,
                "a",
                tuple(f"s{i}" for i in range(MAX_PATH_SEGMENTS)),
            ),
            ("r" * 64, "r" * 64, ()),
            (boundary_length, "a", ("b" * (MAX_EXPRESSION_LENGTH - 2),)),
        ]
        for expression, output_name, path_segments in samples:
            parsed = parse_reference(expression)
            assert parsed == ParsedReference(
                output_name=output_name, path_segments=path_segments
            )

    def test_invalid_expressions_still_none(self):
        # 既有非法样本在新解析器下仍为 None（既有非法语义不复活）
        samples = [
            "",
            "   ",
            "Result",
            "1result",
            "_result",
            "result.a b",
            "result..summary",
            "result.",
            "r" * 65,
        ]
        for expression in samples:
            assert parse_reference(expression) is None

    def test_form_a_results_carry_default_kind_and_position(self):
        # 形态 A 解析结果 kind="output"、input_position=None（默认值恒定）
        parsed = parse_reference("result.summary")
        assert parsed is not None
        assert parsed.kind == "output"
        assert parsed.input_position is None


class TestParseReferenceFormB:
    """形态 B 合法用例：`NODE_ID::POSITION_NAME[.字段路径]`（DC-01）。"""

    def test_basic_form_b(self):
        parsed = parse_reference("node_a::user_input")
        assert parsed == ParsedReference(
            output_name="node_a",
            path_segments=(),
            kind="input_position",
            input_position="user_input",
        )

    def test_form_b_with_path(self):
        parsed = parse_reference("node_a::user_input.text")
        assert parsed is not None
        assert parsed.kind == "input_position"
        assert parsed.output_name == "node_a"
        assert parsed.input_position == "user_input"
        assert parsed.path_segments == ("text",)

    def test_form_b_uppercase_node_id(self):
        # NODE_ID 允许大写（design §2.2：^[A-Za-z0-9_]{1,64}$）
        parsed = parse_reference("Node_A::x")
        assert parsed is not None
        assert parsed.kind == "input_position"
        assert parsed.output_name == "Node_A"
        assert parsed.input_position == "x"

    def test_form_b_multi_segment_path(self):
        parsed = parse_reference("node_1::p.a.b")
        assert parsed is not None
        assert parsed.kind == "input_position"
        assert parsed.output_name == "node_1"
        assert parsed.input_position == "p"
        assert parsed.path_segments == ("a", "b")

    def test_form_b_trimmed(self):
        parsed = parse_reference("  node_a::x  ")
        assert parsed is not None
        assert parsed.output_name == "node_a"
        assert parsed.input_position == "x"

    def test_form_b_purity_same_input_equal(self):
        assert parse_reference("node_a::x.text") == parse_reference(
            "node_a::x.text"
        )


class TestParseReferenceFormBInvalid:
    """形态 B 非法/边界用例：全部返回 None（DC-01 / design §2.2 文法）。"""

    def test_empty_position_returns_none(self):
        assert parse_reference("node_a::") is None

    def test_double_separator_returns_none(self):
        assert parse_reference("node_a::x::y") is None

    def test_missing_node_id_returns_none(self):
        assert parse_reference("::x") is None

    def test_colon_in_node_id_returns_none(self):
        assert parse_reference("node_a:a::x") is None

    def test_whitespace_in_node_id_returns_none(self):
        assert parse_reference("a b::x") is None

    def test_non_ascii_node_id_returns_none(self):
        # NODE_ID 文法 = ^[A-Za-z0-9_]{1,64}$（design §2.2 权威，沿用 node_id
        # 既有约束），非 ASCII（如中文）不匹配 → None。
        # 注：tasks.md T1.3 清单曾将「中文::输入」列于合法集，与 design §2.2
        # 文法冲突；SDD 裁决以 design 为权威（tasks 笔误），归入非法用例。
        assert parse_reference("中文::输入") is None

    def test_form_a_trailing_dot_still_none(self):
        assert parse_reference("result.items.") is None

    def test_form_b_length_257_returns_none(self):
        prefix = "node_a::x."
        reference = prefix + "b" * (MAX_EXPRESSION_LENGTH + 1 - len(prefix))
        assert len(reference) == MAX_EXPRESSION_LENGTH + 1
        assert parse_reference(reference) is None

    def test_form_b_length_256_boundary_parses(self):
        prefix = "node_a::x."
        reference = prefix + "b" * (MAX_EXPRESSION_LENGTH - len(prefix))
        assert len(reference) == MAX_EXPRESSION_LENGTH
        parsed = parse_reference(reference)
        assert parsed is not None
        assert parsed.kind == "input_position"

    def test_form_b_nine_path_segments_returns_none(self):
        reference = "node_a::x." + ".".join(
            f"s{i}" for i in range(MAX_PATH_SEGMENTS + 1)
        )
        assert parse_reference(reference) is None

    def test_form_b_max_path_segments_boundary_parses(self):
        reference = "node_a::x." + ".".join(
            f"s{i}" for i in range(MAX_PATH_SEGMENTS)
        )
        parsed = parse_reference(reference)
        assert parsed is not None
        assert len(parsed.path_segments) == MAX_PATH_SEGMENTS


class TestParseReferenceDisambiguation:
    """消歧关键用例（必须单列，INV-E01 阻断）：只看首段，字段路径段允许含冒号。"""

    def test_colons_in_path_segment_stay_form_a(self):
        # 消歧关键用例：`result.a::b` → 形态 A、path 含 `a::b`（不可全局搜 `::`）
        parsed = parse_reference("result.a::b")
        assert parsed is not None
        assert parsed.kind == "output"
        assert parsed.output_name == "result"
        assert parsed.path_segments == ("a::b",)
        assert parsed.input_position is None

    def test_single_colon_in_path_segment_stays_form_a(self):
        # 既有合法形态：字段路径段含单冒号（`x.a:b` 合法，design §1.2 约束）
        parsed = parse_reference("result.a:b")
        assert parsed is not None
        assert parsed.kind == "output"
        assert parsed.path_segments == ("a:b",)

    def test_separator_in_later_path_segment_stays_form_a(self):
        # 首段不含 `::`（即使后续段含 `::`）→ 形态 A（零回归边界）
        parsed = parse_reference("result.items.0::deep")
        assert parsed is not None
        assert parsed.kind == "output"
        assert parsed.path_segments == ("items", "0::deep")


class TestFormBGrammarConstants:
    """形态 B 文法常量验收（DC-01 / design §2.2）。"""

    def test_input_position_separator_value(self):
        assert INPUT_POSITION_SEPARATOR == "::"

    def test_node_id_pattern_matches_ascii_word(self):
        assert NODE_ID_PATTERN.match("node_a") is not None
        assert NODE_ID_PATTERN.match("Node_A") is not None
        assert NODE_ID_PATTERN.match("n") is not None
        assert NODE_ID_PATTERN.match("A" * 64) is not None

    def test_node_id_pattern_rejects_invalid(self):
        assert NODE_ID_PATTERN.match("") is None
        assert NODE_ID_PATTERN.match("node-a") is None
        assert NODE_ID_PATTERN.match("node a") is None
        assert NODE_ID_PATTERN.match("node:a") is None
        assert NODE_ID_PATTERN.match("中文") is None
        assert NODE_ID_PATTERN.match("A" * 65) is None


class TestNodeOutputCategories:
    """输出类别登记表验收（任务 T1.2 / CHG-B02，DC-04 取值策略）。"""

    def test_ten_types_registered(self):
        expected = {
            NodeType.AGENT,
            NodeType.DATA_SOURCE,
            NodeType.DATA_TRANSFORM,
            NodeType.TOOL,
            NodeType.TRIGGER,
            NodeType.SUBWORKFLOW,
            NodeType.DYNAMIC_INJECTOR,
            NodeType.CONTROL_FLOW,
            NodeType.LLM,
            NodeType.CODE,
        }
        assert set(NODE_OUTPUT_CATEGORIES.keys()) == expected

    def test_value_kind_assignments(self):
        assert NODE_OUTPUT_CATEGORIES[NodeType.AGENT].value_kind == "object"
        assert NODE_OUTPUT_CATEGORIES[NodeType.DATA_SOURCE].value_kind == "dataset"
        assert NODE_OUTPUT_CATEGORIES[NodeType.DATA_TRANSFORM].value_kind == "dataset"
        assert NODE_OUTPUT_CATEGORIES[NodeType.TOOL].value_kind == "object"
        assert NODE_OUTPUT_CATEGORIES[NodeType.TRIGGER].value_kind == "keyed"
        assert NODE_OUTPUT_CATEGORIES[NodeType.SUBWORKFLOW].value_kind == "keyed"
        assert NODE_OUTPUT_CATEGORIES[NodeType.DYNAMIC_INJECTOR].value_kind == "object"
        assert NODE_OUTPUT_CATEGORIES[NodeType.CONTROL_FLOW].value_kind == "signal"

    def test_control_flow_not_referable(self):
        assert resolve_output_category(NodeType.CONTROL_FLOW).referable is False

    def test_unregistered_types_return_none(self):
        assert resolve_output_category(NodeType.PLUGIN) is None
        assert resolve_output_category(NodeType.SUPERVISOR) is None
        assert resolve_output_category(NodeType.OUTPUT_DISPLAY) is None
