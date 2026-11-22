# =============================================================================
# JSON Path 子集提取器单测（tests/unit/test_data_transform_ops_json_path.py）
#
# 覆盖验收：expand_data_transform_ops T5.1/T5.2/T5.6（CHG-B05/B07，DC-05）
# 覆盖模块：json_path.py（tokenizer + 递归求值）/ handlers_ai.py（json_path_extract）
# 覆盖维度：$ / .键名 / [下标] / [*] 提取、缺失置空 + 计数不中断链、
#           语法非法配置期拒绝、JSON 文本解析（长度上限）、
#           与受限求值器隔离性（INV-14，互不调用）
# 覆盖不变式：INV-07（仅数据定位）/ INV-14（求值器零改动零复用）
# =============================================================================

import ast
import inspect
import json
from typing import Any, Dict, List, Optional

import pytest

from orchestration_platform.domain.errors import DATA_TRANSFORM_INVALID_STEP_PARAMS
from orchestration_platform.domain.models.enums import (
    DataTransformOperation,
    NodeType,
)
from orchestration_platform.domain.models.node_configurations import (
    DataTransformNodeConfiguration,
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
from orchestration_platform.engine.nodes.data_transform_ops import json_path as json_path_module
from orchestration_platform.engine.nodes.data_transform_ops.json_path import (
    JsonPathResult,
    JsonPathSyntaxError,
    extract_json_path,
    parse_json_path,
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
# tokenizer（T5.1）
# ---------------------------------------------------------------------------


class TestParseJsonPath:
    def test_root_only(self):
        assert parse_json_path("$") == []

    def test_nested_keys(self):
        segments = parse_json_path("$.data.result.text")

        assert [segment.key for segment in segments] == ["data", "result", "text"]

    def test_index_and_wildcard(self):
        segments = parse_json_path("$.items[10][*].name")

        assert segments[0].key == "items"
        assert segments[1].index == 10
        assert segments[2].__class__.__name__ == "_WildcardSegment"
        assert segments[3].key == "name"

    def test_cjk_keys(self):
        segments = parse_json_path("$.用户.姓名")

        assert [segment.key for segment in segments] == ["用户", "姓名"]

    def test_zero_index(self):
        segments = parse_json_path("$[0]")

        assert segments[0].index == 0

    @pytest.mark.parametrize(
        "path",
        [
            "",            # 空表达式
            "data.result",  # 缺 $ 根前缀
            "$.",          # 空键名
            "$..a",        # 递归下降（子集外）
            "$[abc]",      # 非数字下标
            "$[-1]",       # 负下标（子集外）
            "$['a']",      # 引号键（子集外）
            "$[0",         # 括号未闭合
            "$.a]x",       # 非法字符
            "$a",          # $ 后非法字符
        ],
    )
    def test_invalid_syntax_raises(self, path: str):
        with pytest.raises(JsonPathSyntaxError):
            parse_json_path(path)


# ---------------------------------------------------------------------------
# 求值（T5.1 / FR-16 / AC-14）
# ---------------------------------------------------------------------------

_NESTED: Dict[str, Any] = {
    "data": {
        "result": {
            "text": "hello",
            "score": 0.87,
            "tags": ["a", "b"],
        },
        "items": [
            {"id": 1, "name": "n1"},
            {"id": 2},  # name 缺失（fan-out 部分缺失用例）
            {"id": 3, "name": "n3"},
        ],
        "empty": [],
        "single": [{"v": 7}],
    }
}


class TestExtractJsonPath:
    def test_root_identity(self):
        result = extract_json_path("$", _NESTED)

        assert result.found is True
        assert result.value is _NESTED
        assert result.missing_hits == 0

    def test_nested_key_extraction(self):
        """AC-14：[$.data.result.text 提取] → [提取出 hello]。"""
        result = extract_json_path("$.data.result.text", _NESTED)

        assert result == JsonPathResult(found=True, value="hello", missing_hits=0)

    def test_numeric_value_extraction(self):
        result = extract_json_path("$.data.result.score", _NESTED)

        assert result.found is True
        assert result.value == 0.87

    def test_index_extraction(self):
        result = extract_json_path("$.data.items[0].id", _NESTED)

        assert result.found is True
        assert result.value == 1

    def test_index_out_of_range(self):
        result = extract_json_path("$.data.items[9]", _NESTED)

        assert result.found is False
        assert result.value is None
        assert result.missing_hits == 1

    def test_missing_path_counted(self):
        """AC-14：[$.data.missing 缺失路径] → [置空并计数，不中断操作链]。"""
        result = extract_json_path("$.data.missing", _NESTED)

        assert result.found is False
        assert result.value is None
        assert result.missing_hits == 1

    def test_wildcard_returns_list(self):
        result = extract_json_path("$.data.result.tags[*]", _NESTED)

        assert result.found is True
        assert result.value == ["a", "b"]

    def test_wildcard_single_match_still_list(self):
        result = extract_json_path("$.data.single[*].v", _NESTED)

        assert result.found is True
        assert result.value == [7]

    def test_wildcard_partial_missing(self):
        result = extract_json_path("$.data.items[*].name", _NESTED)

        assert result.found is True
        assert result.value == ["n1", "n3"]
        assert result.missing_hits == 1  # id=2 行缺 name

    def test_wildcard_all_missing(self):
        result = extract_json_path("$.data.items[*].ghost", _NESTED)

        assert result.found is False
        assert result.value == []
        assert result.missing_hits == 3

    def test_wildcard_on_empty_list(self):
        result = extract_json_path("$.data.empty[*]", _NESTED)

        assert result.found is False
        assert result.value == []

    def test_wildcard_on_non_list(self):
        result = extract_json_path("$.data.result.text[*]", _NESTED)

        assert result.found is False
        assert result.missing_hits == 1

    def test_key_on_non_dict(self):
        result = extract_json_path("$.data.result.text.x", _NESTED)

        assert result.found is False
        assert result.missing_hits == 1

    def test_json_text_parsed_then_extracted(self):
        result = extract_json_path("$.data.result.text", json.dumps(_NESTED, ensure_ascii=False))

        assert result.found is True
        assert result.value == "hello"

    def test_invalid_json_text_counted_as_missing(self):
        result = extract_json_path("$.a", "{not json")

        assert result.found is False
        assert result.missing_hits == 1

    def test_json_text_over_length_limit_counted_as_missing(self):
        result = extract_json_path("$", "123456789", max_text_len=8)

        assert result.found is False

    def test_json_text_within_length_limit_parsed(self):
        result = extract_json_path("$", "123456789", max_text_len=9)

        assert result.found is True
        assert result.value == 123456789

    def test_scalar_root_with_dollar(self):
        result = extract_json_path("$", 42)

        assert result.found is True
        assert result.value == 42


# ---------------------------------------------------------------------------
# 与受限求值器隔离性（INV-14 / DC-05：互不复用、互不调用）
# ---------------------------------------------------------------------------


class TestEvaluatorIsolation:
    def test_no_import_of_evaluator(self):
        """json_path 模块不导入求值器所在模块（scheduler），仅标准库 + registry。"""
        tree = ast.parse(inspect.getsource(json_path_module))

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "scheduler" not in alias.name
            elif isinstance(node, ast.ImportFrom):
                assert node.module is not None
                assert "scheduler" not in node.module

    def test_no_evaluator_symbol_referenced(self):
        """模块命名空间不引入受限求值器符号（互不调用）。"""
        assert "RestrictedExpressionEvaluator" not in dir(json_path_module)

    def test_imports_are_stdlib_and_registry_only(self):
        tree = ast.parse(inspect.getsource(json_path_module))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                assert node.module is not None
                imported.add(node.module.split(".")[0])

        assert imported == {"json", "typing", "orchestration_platform"}


# ---------------------------------------------------------------------------
# json_path_extract 操作（T5.2 / CHG-B05）
# ---------------------------------------------------------------------------


class TestJsonPathExtractHandler:
    async def test_extract_from_parsed_structure(self):
        rows = [{"payload": _NESTED["data"]}]
        result = await _run_op(
            "json_path_extract",
            {"source_field": "payload", "path": "$.result.text", "target_field": "text"},
            rows,
        )

        assert result.success is True
        assert result.output_value[0]["text"] == "hello"
        assert result.output_value[0]["payload"] == _NESTED["data"]  # 原字段保留

    async def test_extract_from_json_text(self):
        rows = [{"payload": json.dumps({"data": {"result": {"text": "hi"}}})}]
        result = await _run_op(
            "json_path_extract",
            {
                "source_field": "payload",
                "path": "$.data.result.text",
                "target_field": "text",
            },
            rows,
        )

        assert result.success is True
        assert result.output_value[0]["text"] == "hi"

    async def test_missing_path_sets_none_with_count(self):
        rows = [{"payload": {"a": 1}}, {"payload": {"b": 2}}]
        result = await _run_op(
            "json_path_extract",
            {"source_field": "payload", "path": "$.b", "target_field": "hit"},
            rows,
        )

        assert result.success is True
        output = result.output_value
        assert output[0]["hit"] is None  # $.a 行缺 b → 置空
        assert output[1]["hit"] == 2
        warnings = _warnings(result)
        assert warnings and "1" in warnings[0]

    async def test_wildcard_list_to_target_field(self):
        rows = [{"payload": {"tags": ["a", "b"]}}]
        result = await _run_op(
            "json_path_extract",
            {"source_field": "payload", "path": "$.tags[*]", "target_field": "all"},
            rows,
        )

        assert result.success is True
        assert result.output_value[0]["all"] == ["a", "b"]

    async def test_invalid_path_rejected(self):
        result = await _run_op(
            "json_path_extract",
            {"source_field": "p", "path": "data.result", "target_field": "t"},
            [{"p": {}}],
        )

        assert result.success is False
        assert result.error_code == DATA_TRANSFORM_INVALID_STEP_PARAMS

    async def test_missing_required_params_rejected(self):
        result = await _run_op(
            "json_path_extract",
            {"source_field": "p", "path": "$.a"},  # 缺 target_field
            [{"p": {}}],
        )

        assert result.success is False
        assert result.error_code == DATA_TRANSFORM_INVALID_STEP_PARAMS

    async def test_source_field_absent_from_row(self):
        rows = [{"other": 1}]
        result = await _run_op(
            "json_path_extract",
            {"source_field": "payload", "path": "$.a", "target_field": "t"},
            rows,
        )

        assert result.success is True
        assert result.output_value[0]["t"] is None
        assert _warnings(result)

    async def test_sort_then_extract_chain(self):
        config = DataTransformNodeConfiguration(
            operation=DataTransformOperation.SORT,
            input_dataset_reference="raw_data",
            operations=[
                {
                    "operation": "sort",
                    "params": {"sort_keys": ["id"], "sort_orders": ["desc"]},
                },
                {
                    "operation": "json_path_extract",
                    "params": {
                        "source_field": "payload",
                        "path": "$.result.text",
                        "target_field": "text",
                    },
                },
            ],
        )
        rows = [
            {"id": 1, "payload": {"result": {"text": "a"}}},
            {"id": 2, "payload": {"result": {"text": "b"}}},
        ]
        outputs: Dict[str, Any] = {"raw_data": rows}
        executor = DataTransformNodeExecutor()
        result = await executor.execute(_make_context(config, outputs))

        assert result.success is True
        output = result.output_value
        assert [row["id"] for row in output] == [2, 1]  # 排序生效
        assert [row["text"] for row in output] == ["b", "a"]  # 提取生效
