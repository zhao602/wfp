# =============================================================================
# 清洗治理操作矩阵单测（tests/unit/test_data_transform_ops_cleaning.py）
#
# 覆盖验收：expand_data_transform_ops T4.8（CHG-B04/B08/B09，design §2.4.2-11~15）
# 覆盖模块：regex_safety.py（正则三层安全）/ masking.py（脱敏规则集）/
#           handlers_cleaning.py（handle_nulls/regex_extract/regex_replace/
#           validate/masking 五操作）
# 覆盖维度：输入/输出/边界/错误码/降级 + 高危正则拒绝 + 超长拦截 +
#           脱敏不可逆 + 缺口告警
# 覆盖不变式：INV-07（如实声明残余风险）/ INV-15（正则长度与输入长度上限）
# =============================================================================

from typing import Any, Dict, List, Optional

import pytest

from orchestration_platform.domain.errors import (
    DATA_TRANSFORM_INVALID_STEP_PARAMS,
    DATA_TRANSFORM_REGEX_INPUT_TOO_LONG,
    DATA_TRANSFORM_REGEX_UNSAFE_PATTERN,
)
from orchestration_platform.domain.models.enums import (
    DataTransformOperation,
    NodeType,
)
from orchestration_platform.domain.models.node_configurations import (
    DataTransformNodeConfiguration,
    MaskingCustomPattern,
)
from orchestration_platform.domain.models.node_definition import NodeDefinition
from orchestration_platform.domain.models.shared_state import SharedState
from orchestration_platform.engine.nodes.base import (
    NodeExecutionContext,
    NodeExecutionResult,
)
from orchestration_platform.engine.nodes.data_transform_node_executor import (
    DataTransformNodeExecutor,
)
from orchestration_platform.engine.nodes.data_transform_ops.masking import (
    MASKING_IRREVERSIBLE_NOTE,
    MaskingRuleSet,
)
from orchestration_platform.engine.nodes.data_transform_ops.regex_safety import (
    check_regex_input_length,
    check_regex_safety,
)


# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------


def _make_context(
    config: DataTransformNodeConfiguration,
    outputs: Optional[Dict[str, Any]] = None,
) -> NodeExecutionContext:
    node_def = NodeDefinition(
        node_id="node-transform-1",
        name="数据转换",
        node_type=NodeType.DATA_TRANSFORM,
        configuration=config,
        output_name="transformed_data",
    )
    shared_state = SharedState(
        instance_id="inst-test",
        node_outputs=outputs if outputs is not None else {"raw_data": []},
    )
    return NodeExecutionContext(
        node_definition=node_def,
        shared_state=shared_state,
        instance_id="inst-test",
        trace_id="trace-test",
    )


def _config(operation: str, params: Dict[str, Any]) -> DataTransformNodeConfiguration:
    return DataTransformNodeConfiguration(
        operation=DataTransformOperation(operation),
        input_dataset_reference="raw_data",
        operations=[{"operation": operation, "params": params}],
    )


async def _run_op(
    operation: str,
    params: Dict[str, Any],
    rows: List[Dict[str, Any]],
) -> NodeExecutionResult:
    outputs: Dict[str, Any] = {"raw_data": rows}
    executor = DataTransformNodeExecutor()
    return await executor.execute(_make_context(_config(operation, params), outputs))


def _warnings(result: NodeExecutionResult) -> List[str]:
    warnings = result.metadata.get("warnings")
    return warnings if isinstance(warnings, list) else []


# ---------------------------------------------------------------------------
# 正则三层安全（T4.1 / DC-07 / AC-11 / INV-15）
# ---------------------------------------------------------------------------


class TestRegexSafetyStaticRejection:
    @pytest.mark.parametrize(
        "pattern",
        [
            r"(a+)+",  # 嵌套量词：+ 外层 + 内层
            r"(a*)*",
            r"(?:a+)+",  # 非捕获组同样拒绝
            r"([a-z]+)*",
            r"(a{2,})+",  # 有界重复外层量词
            r"((ab)+)+",  # 嵌套组量化
            r"(a|b?)+",  # 交替含内部量词
        ],
    )
    def test_nested_quantifier_rejected(self, pattern: str):
        verdict = check_regex_safety(pattern)

        assert verdict.safe is False
        assert verdict.reason is not None

    @pytest.mark.parametrize(
        "pattern",
        [
            r"a{100000}",  # 可疑超大重复计数
            r"a{2,100000}",
            r"(ab){99999}",
        ],
    )
    def test_suspicious_large_repeat_rejected(self, pattern: str):
        verdict = check_regex_safety(pattern)

        assert verdict.safe is False

    def test_pattern_too_long_rejected(self):
        verdict = check_regex_safety("a" * 513)

        assert verdict.safe is False
        assert "512" in (verdict.reason or "")

    def test_pattern_length_boundary_allowed(self):
        verdict = check_regex_safety("a" * 512)

        assert verdict.safe is True

    @pytest.mark.parametrize(
        "pattern",
        [
            r"https?://\S+",
            r"1[3-9]\d{9}",
            r"(ab)+",  # 组内容无量词，安全
            r"(\d{4})-(\d{2})-(\d{2})",
            r"[a-z]+@example\.com",
        ],
    )
    def test_safe_patterns_allowed(self, pattern: str):
        verdict = check_regex_safety(pattern)

        assert verdict.safe is True, verdict.reason

    def test_invalid_regex_rejected(self):
        verdict = check_regex_safety("([unclosed")

        assert verdict.safe is False
        assert "编译失败" in (verdict.reason or "")

    def test_empty_pattern_rejected(self):
        verdict = check_regex_safety("")

        assert verdict.safe is False

    def test_complexity_heuristic_rejects_excess_quantifiers(self):
        """第三层复杂度启发式：量词算子超 50 个拒绝。"""
        verdict = check_regex_safety("a?" * 51)

        assert verdict.safe is False
        assert "复杂度" in (verdict.reason or "")

    def test_complexity_boundary_allowed(self):
        verdict = check_regex_safety("a?" * 50)

        assert verdict.safe is True


class TestRegexInputLengthLimit:
    def test_input_too_long_detected(self):
        assert check_regex_input_length("a" * 8193) is True

    def test_input_boundary_allowed(self):
        assert check_regex_input_length("a" * 8192) is False


# ---------------------------------------------------------------------------
# 脱敏规则集（T4.3 / DC-06 / AC-13）
# ---------------------------------------------------------------------------


class TestMaskingRuleSet:
    def setup_method(self):
        self.rule_set = MaskingRuleSet.builtin()

    @pytest.mark.parametrize(
        ("rule_id", "raw", "expected"),
        [
            ("phone", "13812345678", "138****5678"),
            ("id_card", "11010119900101123X", "110101********123X"),
            ("bank_card", "6222020200012345123", "6222********5123"),
            ("email", "zhangsan@example.com", "z***@example.com"),
            ("name", "张三", "张**"),
            ("name", "欧阳锋", "欧**"),
        ],
    )
    def test_builtin_rules_mask_correctly(self, rule_id: str, raw: str, expected: str):
        result = self.rule_set.mask_field(raw, rule_id)

        assert result.hit is True
        assert result.value == expected

    def test_address_rule_masks_suffix(self):
        result = self.rule_set.mask_field("北京市海淀区中关村大街27号", "address")

        assert result.hit is True
        assert result.value.startswith("北京市海淀区")
        assert "中关村" not in result.value  # 原值信息不可还原

    def test_masked_output_irreversible(self):
        """不可逆：掩码输出无法还原原值（中间 4 位信息不可恢复）。"""
        raw = "13812345678"
        result = self.rule_set.mask_field(raw, "phone")

        assert result.value != raw
        assert "1234" not in result.value  # 中间四位已丢失
        assert result.value.count("*") == 4

    def test_no_match_returns_original_with_no_hit(self):
        result = self.rule_set.mask_field("hello world", "phone")

        assert result.hit is False
        assert result.value == "hello world"

    def test_non_string_value_no_hit(self):
        result = self.rule_set.mask_field(13812345678, "phone")

        assert result.hit is False
        assert result.value == 13812345678

    def test_unknown_rule_raises(self):
        with pytest.raises(ValueError):
            self.rule_set.mask_field("x", "ghost_rule")

    def test_add_rule_duplicate_raises(self):
        with pytest.raises(ValueError):
            self.rule_set.add_rule("phone", r"\d+", "MASK")

    def test_add_rule_invalid_regex_raises(self):
        with pytest.raises(ValueError):
            self.rule_set.add_rule("custom_bad", "([unclosed", "MASK")

    def test_custom_rule_roundtrip(self):
        self.rule_set.add_rule("custom_tail", r"(?<!\d)(\d{3})\d{4}(\d{4})(?!\d)", r"\1####\2")

        result = self.rule_set.mask_field("13812345678", "custom_tail")

        assert result.hit is True
        assert result.value == "138####5678"

    def test_builtin_rule_ids_cover_six(self):
        assert set(self.rule_set.rule_ids()) == {
            "phone", "id_card", "bank_card", "email", "name", "address",
        }


# ---------------------------------------------------------------------------
# handle_nulls（T4.2 / FR-12 / AC-09）
# ---------------------------------------------------------------------------


class TestHandleNulls:
    async def test_fill_mean_replaces_null_with_column_mean(self):
        """AC-09：[配置对 年龄 按均值填充] → [空单元格被替换为该列均值]。"""
        rows = [{"年龄": 10}, {"年龄": None}, {"年龄": 30}]
        result = await _run_op(
            "handle_nulls",
            {"null_fields": ["年龄"], "strategy": "fill_mean"},
            rows,
        )

        assert result.success is True
        output = result.output_value
        assert output[0]["年龄"] == 10
        assert output[1]["年龄"] == 20.0  # 均值 (10+30)/2
        assert output[2]["年龄"] == 30

    async def test_fill_median(self):
        rows = [{"v": 10}, {"v": 20}, {"v": None}, {"v": 30}]
        result = await _run_op(
            "handle_nulls",
            {"null_fields": ["v"], "strategy": "fill_median"},
            rows,
        )

        assert result.success is True
        assert result.output_value[2]["v"] == 20  # 中位数

    async def test_fill_constant(self):
        rows = [{"v": 1}, {"v": None}]
        result = await _run_op(
            "handle_nulls",
            {"null_fields": ["v"], "strategy": "fill_constant", "fill_value": 0},
            rows,
        )

        assert result.success is True
        assert result.output_value[1]["v"] == 0

    async def test_fill_constant_missing_fill_value_rejected(self):
        result = await _run_op(
            "handle_nulls",
            {"null_fields": ["v"], "strategy": "fill_constant"},
            [{"v": None}],
        )

        assert result.success is False
        assert result.error_code == DATA_TRANSFORM_INVALID_STEP_PARAMS

    async def test_fill_mean_without_numeric_nulled_with_warning(self):
        """数值缺失无法统计 → 置空 + 告警（degraded），不中断链。"""
        rows = [{"v": "abc"}, {"v": None}]
        result = await _run_op(
            "handle_nulls",
            {"null_fields": ["v"], "strategy": "fill_mean"},
            rows,
        )

        assert result.success is True
        output = result.output_value
        assert output[0]["v"] == "abc"  # 非空值不动
        assert output[1]["v"] is None  # 置空
        assert any("v" in w for w in _warnings(result))

    async def test_drop_rows_by_specified_column(self):
        """AC-09：[配置丢弃 邮箱 为空的行] → [输出不含该列为空的行]。"""
        rows = [
            {"id": 1, "邮箱": "a@b.com"},
            {"id": 2, "邮箱": ""},
            {"id": 3, "邮箱": "c@d.com"},
        ]
        result = await _run_op(
            "handle_nulls",
            {"null_fields": ["邮箱"], "strategy": "drop_row"},
            rows,
        )

        assert result.success is True
        output = result.output_value
        assert [r["id"] for r in output] == [1, 3]

    async def test_drop_scope_field_only_checks_specified_columns(self):
        """drop_scope=field（缺省）：仅指定列空值触发删行。"""
        rows = [
            {"a": 1, "b": None},
            {"a": None, "b": 2},
        ]
        result = await _run_op(
            "handle_nulls",
            {"null_fields": ["a"], "strategy": "drop_row", "drop_scope": "field"},
            rows,
        )

        assert result.success is True
        output = result.output_value
        assert len(output) == 1
        assert output[0]["a"] == 1  # b 空不触发（仅检查 a 列）

    async def test_drop_scope_row_checks_all_columns(self):
        """drop_scope=row：整行任意字段空值触发删行。"""
        rows = [
            {"a": 1, "b": None},
            {"a": 3, "b": 4},
        ]
        result = await _run_op(
            "handle_nulls",
            {"null_fields": ["a"], "strategy": "drop_row", "drop_scope": "row"},
            rows,
        )

        assert result.success is True
        output = result.output_value
        assert len(output) == 1
        assert output[0]["a"] == 3  # 第一行因 b 空被删

    async def test_scope_none_only_excludes_empty_string(self):
        rows = [{"v": ""}, {"v": None}]
        result = await _run_op(
            "handle_nulls",
            {
                "null_fields": ["v"],
                "strategy": "fill_constant",
                "fill_value": "X",
                "null_value_scope": ["none"],
            },
            rows,
        )

        assert result.success is True
        output = result.output_value
        assert output[0]["v"] == ""  # 空串不在 none 口径内
        assert output[1]["v"] == "X"

    async def test_scope_empty_only_excludes_none(self):
        rows = [{"v": ""}, {"v": None}]
        result = await _run_op(
            "handle_nulls",
            {
                "null_fields": ["v"],
                "strategy": "fill_constant",
                "fill_value": "X",
                "null_value_scope": ["empty"],
            },
            rows,
        )

        assert result.success is True
        output = result.output_value
        assert output[0]["v"] == "X"
        assert output[1]["v"] is None  # None 不在 empty 口径内

    async def test_scope_missing_covers_absent_key(self):
        rows = [{"v": 1}, {"other": 2}]
        result = await _run_op(
            "handle_nulls",
            {
                "null_fields": ["v"],
                "strategy": "fill_constant",
                "fill_value": "X",
                "null_value_scope": ["missing"],
            },
            rows,
        )

        assert result.success is True
        assert result.output_value[1]["v"] == "X"  # 缺失键被填充

    async def test_empty_fields_defaults_to_all_columns(self):
        rows = [{"a": 1, "b": None}]
        result = await _run_op(
            "handle_nulls",
            {"strategy": "fill_constant", "fill_value": 0},
            rows,
        )

        assert result.success is True
        assert result.output_value[0]["b"] == 0

    async def test_missing_strategy_passes_through(self):
        rows = [{"v": None}]
        result = await _run_op("handle_nulls", {}, rows)

        assert result.success is True
        assert result.output_value == rows

    async def test_invalid_strategy_rejected(self):
        result = await _run_op(
            "handle_nulls", {"strategy": "fill_magic"}, [{"v": None}]
        )

        assert result.success is False
        assert result.error_code == DATA_TRANSFORM_INVALID_STEP_PARAMS

    async def test_invalid_scope_rejected(self):
        result = await _run_op(
            "handle_nulls",
            {"strategy": "fill_constant", "fill_value": 0, "null_value_scope": ["nil"]},
            [{"v": None}],
        )

        assert result.success is False
        assert result.error_code == DATA_TRANSFORM_INVALID_STEP_PARAMS

    async def test_invalid_drop_scope_rejected(self):
        result = await _run_op(
            "handle_nulls",
            {"strategy": "drop_row", "drop_scope": "column"},
            [{"v": None}],
        )

        assert result.success is False
        assert result.error_code == DATA_TRANSFORM_INVALID_STEP_PARAMS


# ---------------------------------------------------------------------------
# regex_extract（T4.5 / FR-13 / AC-10）
# ---------------------------------------------------------------------------

_DESC_ROWS = [
    {"id": 1, "desc": "访问 https://example.com/x 查看"},
    {"id": 2, "desc": "无链接文本"},
    {"id": 3, "desc": "先 https://a.io 再 https://b.io"},
]


class TestRegexExtract:
    async def test_extract_first_match_to_target_field(self):
        result = await _run_op(
            "regex_extract",
            {
                "regex_field": "desc",
                "pattern": r"https?://\S+",
                "target_field": "url",
            },
            _DESC_ROWS,
        )

        assert result.success is True
        output = result.output_value
        assert output[0]["url"] == "https://example.com/x"
        assert output[1]["url"] is None  # 无匹配 → 置空
        assert output[1]["desc"] == "无链接文本"  # 原字段保留

    async def test_extract_all_mode_returns_list(self):
        result = await _run_op(
            "regex_extract",
            {
                "regex_field": "desc",
                "pattern": r"https?://\S+",
                "extract_mode": "all",
                "target_field": "urls",
            },
            _DESC_ROWS,
        )

        assert result.success is True
        output = result.output_value
        assert output[2]["urls"] == ["https://a.io", "https://b.io"]
        assert output[1]["urls"] is None  # 无匹配 → 置空（非空列表）

    async def test_capture_group_extraction(self):
        result = await _run_op(
            "regex_extract",
            {
                "regex_field": "desc",
                "pattern": r"访问 (https?://\S+) 查看",
                "group": 1,
                "target_field": "url",
            },
            _DESC_ROWS,
        )

        assert result.success is True
        assert result.output_value[0]["url"] == "https://example.com/x"

    async def test_no_match_counted_with_warning(self):
        result = await _run_op(
            "regex_extract",
            {
                "regex_field": "desc",
                "pattern": r"https?://\S+",
                "target_field": "url",
            },
            [{"desc": "无链接"}],
        )

        assert result.success is True
        assert result.output_value[0]["url"] is None
        warnings = _warnings(result)
        assert warnings and "1" in warnings[0]

    async def test_unsafe_pattern_rejected(self):
        result = await _run_op(
            "regex_extract",
            {
                "regex_field": "desc",
                "pattern": r"(a+)+",
                "target_field": "url",
            },
            [{"desc": "aaa"}],
        )

        assert result.success is False
        assert result.error_code == DATA_TRANSFORM_REGEX_UNSAFE_PATTERN

    async def test_input_too_long_fails_fast(self):
        result = await _run_op(
            "regex_extract",
            {
                "regex_field": "desc",
                "pattern": r"\w+",
                "target_field": "token",
            },
            [{"desc": "a" * 8193}],
        )

        assert result.success is False
        assert result.error_code == DATA_TRANSFORM_REGEX_INPUT_TOO_LONG

    async def test_missing_required_params_rejected(self):
        result = await _run_op(
            "regex_extract",
            {"regex_field": "desc", "pattern": r"\w+"},  # 缺 target_field
            [{"desc": "x"}],
        )

        assert result.success is False
        assert result.error_code == DATA_TRANSFORM_INVALID_STEP_PARAMS

    async def test_invalid_extract_mode_rejected(self):
        result = await _run_op(
            "regex_extract",
            {
                "regex_field": "desc",
                "pattern": r"\w+",
                "extract_mode": "last",
                "target_field": "t",
            },
            [{"desc": "x"}],
        )

        assert result.success is False
        assert result.error_code == DATA_TRANSFORM_INVALID_STEP_PARAMS

    async def test_group_out_of_range_treated_as_no_match(self):
        result = await _run_op(
            "regex_extract",
            {
                "regex_field": "desc",
                "pattern": r"\w+",
                "group": 5,
                "target_field": "t",
            },
            [{"desc": "word"}],
        )

        assert result.success is True
        assert result.output_value[0]["t"] is None
        assert _warnings(result)

    async def test_non_string_value_no_match(self):
        result = await _run_op(
            "regex_extract",
            {
                "regex_field": "n",
                "pattern": r"\d+",
                "target_field": "t",
            },
            [{"n": 12345}],
        )

        assert result.success is True
        assert result.output_value[0]["t"] is None
        assert _warnings(result)


# ---------------------------------------------------------------------------
# regex_replace（T4.6 / FR-13 / AC-10）
# ---------------------------------------------------------------------------


class TestRegexReplace:
    async def test_replacement_with_capture_group_backrefs(self):
        rows = [{"d": "2026-01-02"}]
        result = await _run_op(
            "regex_replace",
            {
                "regex_field": "d",
                "pattern": r"(\d{4})-(\d{2})-(\d{2})",
                "replacement": r"\1/\2/\3",
            },
            rows,
        )

        assert result.success is True
        assert result.output_value[0]["d"] == "2026/01/02"  # 缺省原地替换

    async def test_replacement_to_target_field(self):
        rows = [{"d": "2026-01-02"}]
        result = await _run_op(
            "regex_replace",
            {
                "regex_field": "d",
                "pattern": r"(\d{4})-(\d{2})",
                "replacement": r"\1年\2月",
                "target_field": "d_cn",
            },
            rows,
        )

        assert result.success is True
        output = result.output_value[0]
        assert output["d"] == "2026-01-02"  # 原字段保留
        assert output["d_cn"] == "2026年01月-02"  # 仅替换匹配段，未匹配的 "-02" 原样保留

    async def test_no_match_keeps_original_with_count(self):
        rows = [{"d": "无日期"}]
        result = await _run_op(
            "regex_replace",
            {
                "regex_field": "d",
                "pattern": r"\d{4}",
                "replacement": "YYYY",
            },
            rows,
        )

        assert result.success is True
        assert result.output_value[0]["d"] == "无日期"
        warnings = _warnings(result)
        assert warnings and "1" in warnings[0]

    async def test_unsafe_pattern_rejected(self):
        result = await _run_op(
            "regex_replace",
            {
                "regex_field": "d",
                "pattern": r"(a*)*b",
                "replacement": "x",
            },
            [{"d": "aaab"}],
        )

        assert result.success is False
        assert result.error_code == DATA_TRANSFORM_REGEX_UNSAFE_PATTERN

    async def test_invalid_group_reference_in_replacement_rejected(self):
        result = await _run_op(
            "regex_replace",
            {
                "regex_field": "d",
                "pattern": r"(\d{4})",
                "replacement": r"\2",  # 引用不存在的捕获组
            },
            [{"d": "2026"}],
        )

        assert result.success is False
        assert result.error_code == DATA_TRANSFORM_INVALID_STEP_PARAMS

    async def test_missing_required_params_rejected(self):
        result = await _run_op(
            "regex_replace",
            {"regex_field": "d", "pattern": r"\d+"},  # 缺 replacement
            [{"d": "2026"}],
        )

        assert result.success is False
        assert result.error_code == DATA_TRANSFORM_INVALID_STEP_PARAMS


# ---------------------------------------------------------------------------
# validate（T4.7 / FR-14 / AC-12）
# ---------------------------------------------------------------------------


class TestValidate:
    async def test_email_drop_removes_invalid_rows(self):
        rows = [
            {"id": 1, "email": "user@example.com"},
            {"id": 2, "email": "not-an-email"},
            {"id": 3, "email": "a@b"},  # 近似口径：无 TLD 判非法
        ]
        result = await _run_op(
            "validate",
            {"validate_field": "email", "validate_rule": "email"},
            rows,
        )

        assert result.success is True
        output = result.output_value
        assert [r["id"] for r in output] == [1]
        assert any("2" in w for w in _warnings(result))

    async def test_email_mark_adds_flag_column(self):
        rows = [
            {"id": 1, "email": "user@example.com"},
            {"id": 2, "email": "bad"},
        ]
        result = await _run_op(
            "validate",
            {
                "validate_field": "email",
                "validate_rule": "email",
                "on_invalid": "mark",
            },
            rows,
        )

        assert result.success is True
        output = result.output_value
        assert len(output) == 2  # mark 不删行
        assert output[0]["email_invalid"] is False
        assert output[1]["email_invalid"] is True

    async def test_json_drop_removes_invalid_json_rows(self):
        rows = [
            {"id": 1, "payload": '{"a": 1}'},
            {"id": 2, "payload": "{not json"},
            {"id": 3, "payload": "null"},  # JSON null 合法
        ]
        result = await _run_op(
            "validate",
            {"validate_field": "payload", "validate_rule": "json"},
            rows,
        )

        assert result.success is True
        assert [r["id"] for r in result.output_value] == [1, 3]

    async def test_json_parsed_structure_valid(self):
        rows = [{"payload": {"a": 1}}, {"payload": "bad"}]
        result = await _run_op(
            "validate",
            {"validate_field": "payload", "validate_rule": "json"},
            rows,
        )

        assert result.success is True
        assert len(result.output_value) == 1

    async def test_invalid_rule_rejected(self):
        result = await _run_op(
            "validate",
            {"validate_field": "x", "validate_rule": "phone"},
            [{"x": "1"}],
        )

        assert result.success is False
        assert result.error_code == DATA_TRANSFORM_INVALID_STEP_PARAMS

    async def test_missing_field_rejected(self):
        result = await _run_op(
            "validate",
            {"validate_rule": "email"},
            [{"x": "1"}],
        )

        assert result.success is False
        assert result.error_code == DATA_TRANSFORM_INVALID_STEP_PARAMS

    async def test_invalid_on_invalid_rejected(self):
        result = await _run_op(
            "validate",
            {
                "validate_field": "email",
                "validate_rule": "email",
                "on_invalid": "fix",
            },
            [{"email": "a@b.com"}],
        )

        assert result.success is False
        assert result.error_code == DATA_TRANSFORM_INVALID_STEP_PARAMS


# ---------------------------------------------------------------------------
# masking 操作（T4.4 / AC-13）
# ---------------------------------------------------------------------------


class TestMaskingOperation:
    async def test_phone_masking_applies_to_field(self):
        rows = [{"note": "联系 13812345678"}]
        result = await _run_op(
            "masking",
            {"mask_fields": ["note"], "mask_rules": ["phone"]},
            rows,
        )

        assert result.success is True
        assert result.output_value[0]["note"] == "联系 138****5678"

    async def test_gap_masked_with_minimal_exposure_and_warning(self):
        """缺口最小暴露：未命中规则 → 整值替换掩码 + 告警。"""
        rows = [{"note": "客户已联系"}]
        result = await _run_op(
            "masking",
            {"mask_fields": ["note"], "mask_rules": ["id_card"]},
            rows,
        )

        assert result.success is True
        assert result.output_value[0]["note"] == "***"
        warnings = _warnings(result)
        assert any("缺口" in w or "未命中" in w for w in warnings)

    async def test_irreversibility_note_in_result(self):
        """不可逆声明在结果说明（metadata.warnings）中体现。"""
        rows = [{"note": "13812345678"}]
        result = await _run_op(
            "masking",
            {"mask_fields": ["note"], "mask_rules": ["phone"]},
            rows,
        )

        assert result.success is True
        assert MASKING_IRREVERSIBLE_NOTE in _warnings(result)

    async def test_custom_pattern_with_group_template(self):
        rows = [{"note": "13812345678"}]
        result = await _run_op(
            "masking",
            {
                "mask_fields": ["note"],
                "custom_patterns": [
                    MaskingCustomPattern(
                        pattern=r"(?<!\d)(\d{3})\d{4}(\d{4})(?!\d)",
                        mask_template="{1}####{2}",
                    )
                ],
            },
            rows,
        )

        assert result.success is True
        assert result.output_value[0]["note"] == "138####5678"

    async def test_custom_pattern_whole_match_template(self):
        rows = [{"note": "编号 9527"}]
        result = await _run_op(
            "masking",
            {
                "mask_fields": ["note"],
                "mask_rules": ["phone"],
                "custom_patterns": [
                    MaskingCustomPattern(pattern=r"编号 (\d+)", mask_template="编号 MASK")
                ],
            },
            rows,
        )

        assert result.success is True
        assert result.output_value[0]["note"] == "编号 MASK"

    async def test_unknown_builtin_rule_rejected(self):
        result = await _run_op(
            "masking",
            {"mask_fields": ["note"], "mask_rules": ["ghost"]},
            [{"note": "x"}],
        )

        assert result.success is False
        assert result.error_code == DATA_TRANSFORM_INVALID_STEP_PARAMS

    async def test_unsafe_custom_pattern_rejected(self):
        result = await _run_op(
            "masking",
            {
                "mask_fields": ["note"],
                "custom_patterns": [
                    MaskingCustomPattern(pattern=r"(a+)+", mask_template="MASK")
                ],
            },
            [{"note": "aaa"}],
        )

        assert result.success is False
        assert result.error_code == DATA_TRANSFORM_REGEX_UNSAFE_PATTERN

    async def test_empty_fields_or_rules_pass_through(self):
        rows = [{"note": "13812345678"}]
        result = await _run_op("masking", {"mask_fields": [], "mask_rules": ["phone"]}, rows)

        assert result.success is True
        assert result.output_value == rows

    async def test_none_and_empty_values_not_counted_as_gaps(self):
        rows = [{"note": None}, {"note": ""}, {"note": "13812345678"}]
        result = await _run_op(
            "masking",
            {"mask_fields": ["note"], "mask_rules": ["phone"]},
            rows,
        )

        assert result.success is True
        output = result.output_value
        assert output[0]["note"] is None
        assert output[1]["note"] == ""
        assert output[2]["note"] == "138****5678"


# ---------------------------------------------------------------------------
# 注册与链式组合
# ---------------------------------------------------------------------------


class TestCleaningRegistrationAndChain:
    def test_five_cleaning_handlers_registered(self):
        from orchestration_platform.engine.nodes.data_transform_ops import (
            registered_operations,
        )

        registered = {op.value for op in registered_operations()}
        assert {
            "handle_nulls",
            "regex_extract",
            "regex_replace",
            "validate",
            "masking",
        } <= registered

    async def test_handle_nulls_then_masking_chain(self):
        config = DataTransformNodeConfiguration(
            operation=DataTransformOperation.HANDLE_NULLS,
            input_dataset_reference="raw_data",
            operations=[
                {
                    "operation": "handle_nulls",
                    "params": {
                        "null_fields": ["phone"],
                        "strategy": "drop_row",
                        "drop_scope": "field",
                    },
                },
                {
                    "operation": "masking",
                    "params": {"mask_fields": ["phone"], "mask_rules": ["phone"]},
                },
            ],
        )
        rows = [
            {"phone": "13812345678"},
            {"phone": ""},
            {"phone": "13998887777"},
        ]
        outputs: Dict[str, Any] = {"raw_data": rows}
        executor = DataTransformNodeExecutor()
        result = await executor.execute(_make_context(config, outputs))

        assert result.success is True
        output = result.output_value
        assert len(output) == 2  # 空值行先删，再脱敏
        assert output[0]["phone"] == "138****5678"
        assert output[1]["phone"] == "139****7777"
