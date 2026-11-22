# =============================================================================
# 开发者兜底操作单测（tests/unit/test_data_transform_ops_dev.py）
#
# 覆盖验收：expand_data_transform_ops T5.3/T5.4/T5.5/T5.6（CHG-B06/B11，DC-08）
# 覆盖模块：custom_code_guard.py（静态合规校验）/ handlers_dev.py
#           （sampling 抽样 + custom_code 登记型不执行）
# 覆盖维度：脚本静态校验命中（敏感 import/禁用调用/敏感属性/语法失败）、
#           执行含脚本步 → fail-fast 且绝不声称成功、
#           抽样 head 确定性 / random + 种子可重现、参数越界拒绝、
#           机检口径（ops 包无 subprocess/exec/eval/compile/importlib import 或调用）
# 覆盖不变式：INV-07（不夸大能力）/ INV-08（显式边界声明）
# =============================================================================

import ast
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from orchestration_platform.domain.errors import (
    DATA_TRANSFORM_INVALID_STEP_PARAMS,
    DATA_TRANSFORM_SCRIPT_NOT_EXECUTABLE,
)
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
from orchestration_platform.engine.nodes.data_transform_ops.custom_code_guard import (
    validate_custom_code,
)


# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------

OPS_DIR = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "orchestration_platform"
    / "engine"
    / "nodes"
    / "data_transform_ops"
)


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


def _rows(count: int) -> List[Dict[str, Any]]:
    return [{"id": index, "v": index * 10} for index in range(count)]


# ---------------------------------------------------------------------------
# 脚本静态合规校验（T5.3 / FR-17 / AC-15）
# ---------------------------------------------------------------------------


class TestCustomCodeGuard:
    @pytest.mark.parametrize(
        "code",
        [
            "import os\n",
            "import sys\n",
            "import socket\n",
            "import subprocess\n",
            "import importlib\n",
            "import shutil\n",
            "from subprocess import run\n",
            "from os import path\n",
            "import urllib.request\n",
        ],
    )
    def test_banned_imports_flagged(self, code: str):
        verdict = validate_custom_code(code, "python")

        assert verdict.compliant is False
        assert verdict.findings

    @pytest.mark.parametrize(
        "code",
        [
            "import json\nimport re\nimport math\n",
            "import datetime\n",
            "from typing import List\n",
            "import collections\n",
        ],
    )
    def test_benign_imports_compliant(self, code: str):
        verdict = validate_custom_code(code, "python")

        assert verdict.compliant is True, verdict.findings
        assert verdict.findings == []

    @pytest.mark.parametrize(
        "code",
        [
            "eval('1+1')\n",
            "exec('x=1')\n",
            "compile('1', '<s>', 'exec')\n",
            "open('/etc/passwd')\n",
            "__import__('os')\n",
            "breakpoint()\n",
        ],
    )
    def test_banned_calls_flagged(self, code: str):
        verdict = validate_custom_code(code, "python")

        assert verdict.compliant is False
        assert verdict.findings

    def test_sensitive_attribute_flagged(self):
        verdict = validate_custom_code("x = obj.__globals__\n", "python")

        assert verdict.compliant is False

    def test_subclasses_escape_flagged(self):
        verdict = validate_custom_code("xs = ().__class__.__subclasses__()\n", "python")

        assert verdict.compliant is False

    def test_syntax_error_flagged(self):
        verdict = validate_custom_code("def broken(:\n", "python")

        assert verdict.compliant is False
        assert any("语法解析失败" in finding for finding in verdict.findings)

    def test_empty_code_flagged(self):
        assert validate_custom_code("", "python").compliant is False
        assert validate_custom_code("   \n", "python").compliant is False

    def test_unsupported_language_flagged(self):
        verdict = validate_custom_code("console.log(1)", "javascript")

        assert verdict.compliant is False
        assert verdict.findings

    def test_clean_script_compliant(self):
        code = (
            "def run(input_dataset):\n"
            "    output_dataset = []\n"
            "    for row in input_dataset:\n"
            "        output_dataset.append(dict(row))\n"
            "    return output_dataset\n"
        )
        verdict = validate_custom_code(code, "python")

        assert verdict.compliant is True
        assert verdict.findings == []

    def test_multiple_findings_accumulate(self):
        verdict = validate_custom_code("import os\neval('1')\n", "python")

        assert verdict.compliant is False
        assert len(verdict.findings) >= 2


# ---------------------------------------------------------------------------
# custom_code 登记型（不执行）（T5.4 / FR-17 / AC-15 / DC-08）
# ---------------------------------------------------------------------------


class TestCustomCodeExecution:
    async def test_execution_fails_with_script_not_executable(self):
        """AC-15：执行节点 → 脚本步骤不执行且不声称成功。"""
        result = await _run_op(
            "custom_code",
            {"language": "python", "code": "import os\n"},
            _rows(3),
        )

        assert result.success is False  # 绝不声称成功
        assert result.error_code == DATA_TRANSFORM_SCRIPT_NOT_EXECUTABLE
        assert "步骤 #0" in result.error_message
        assert "custom_code" in result.error_message
        assert result.output_value is None  # 不落共享状态、无执行副作用

    async def test_chain_failure_localized_to_script_step(self):
        config = DataTransformNodeConfiguration(
            operation=DataTransformOperation.SORT,
            input_dataset_reference="raw_data",
            operations=[
                {"operation": "sort", "params": {"sort_keys": ["id"]}},
                {
                    "operation": "custom_code",
                    "params": {"language": "python", "code": "x = 1\n"},
                },
            ],
        )
        outputs: Dict[str, Any] = {"raw_data": _rows(3)}
        executor = DataTransformNodeExecutor()
        result = await executor.execute(_make_context(config, outputs))

        assert result.success is False
        assert result.error_code == DATA_TRANSFORM_SCRIPT_NOT_EXECUTABLE
        assert "步骤 #1" in result.error_message  # 脚本步定位

    async def test_failure_regardless_of_params(self):
        """不校验参数即拒绝：执行本身不可行（空参数同样 fail-fast）。"""
        result = await _run_op("custom_code", {}, _rows(2))

        assert result.success is False
        assert result.error_code == DATA_TRANSFORM_SCRIPT_NOT_EXECUTABLE


# ---------------------------------------------------------------------------
# sampling 数据抽样（T5.5 / FR-18 / AC-16）
# ---------------------------------------------------------------------------


class TestSampling:
    async def test_head_first_n_rows(self):
        """AC-16：[配置取前 5 行] → [输出为原数据集前 5 行]。"""
        result = await _run_op(
            "sampling", {"sample_mode": "head", "sample_n": 5}, _rows(10)
        )

        assert result.success is True
        assert [row["id"] for row in result.output_value] == [0, 1, 2, 3, 4]

    async def test_head_is_default_mode(self):
        result = await _run_op("sampling", {"sample_n": 3}, _rows(10))

        assert result.success is True
        assert [row["id"] for row in result.output_value] == [0, 1, 2]

    async def test_head_n_exceeds_total_returns_all(self):
        result = await _run_op("sampling", {"sample_n": 99}, _rows(4))

        assert result.success is True
        assert len(result.output_value) == 4

    async def test_head_n_zero_returns_empty(self):
        result = await _run_op("sampling", {"sample_n": 0}, _rows(4))

        assert result.success is True
        assert result.output_value == []

    async def test_head_percent(self):
        result = await _run_op("sampling", {"sample_percent": 30}, _rows(10))

        assert result.success is True
        assert [row["id"] for row in result.output_value] == [0, 1, 2]

    async def test_head_percent_100_returns_all(self):
        result = await _run_op("sampling", {"sample_percent": 100}, _rows(7))

        assert result.success is True
        assert len(result.output_value) == 7

    async def test_head_ignores_seed(self):
        result = await _run_op(
            "sampling",
            {"sample_mode": "head", "sample_n": 3, "random_seed": 123},
            _rows(10),
        )

        assert result.success is True
        assert [row["id"] for row in result.output_value] == [0, 1, 2]

    async def test_random_with_seed_reproducible(self):
        """AC-16：[随机抽样 10% 且给定种子] → [相同输入与种子下结果可重现]。"""
        rows = _rows(50)
        first = await _run_op(
            "sampling",
            {"sample_mode": "random", "sample_n": 10, "random_seed": 42},
            rows,
        )
        second = await _run_op(
            "sampling",
            {"sample_mode": "random", "sample_n": 10, "random_seed": 42},
            rows,
        )

        assert first.success is True
        assert second.success is True
        assert first.output_value == second.output_value
        assert len(first.output_value) == 10

    async def test_random_percent_with_seed_reproducible(self):
        rows = _rows(100)
        first = await _run_op(
            "sampling",
            {"sample_mode": "random", "sample_percent": 10, "random_seed": 7},
            rows,
        )
        second = await _run_op(
            "sampling",
            {"sample_mode": "random", "sample_percent": 10, "random_seed": 7},
            rows,
        )

        assert first.output_value == second.output_value
        assert len(first.output_value) == 10

    async def test_random_without_seed_keeps_scale(self):
        """无种子不以确定性作验收（INV-08），仅验抽样规模。"""
        result = await _run_op(
            "sampling", {"sample_mode": "random", "sample_n": 6}, _rows(20)
        )

        assert result.success is True
        assert len(result.output_value) == 6

    async def test_different_seeds_produce_different_samples(self):
        rows = _rows(40)
        first = await _run_op(
            "sampling",
            {"sample_mode": "random", "sample_n": 20, "random_seed": 1},
            rows,
        )
        second = await _run_op(
            "sampling",
            {"sample_mode": "random", "sample_n": 20, "random_seed": 2},
            rows,
        )

        assert first.output_value != second.output_value

    async def test_empty_dataset_returns_empty(self):
        result = await _run_op("sampling", {"sample_n": 3}, [])

        assert result.success is True
        assert result.output_value == []

    async def test_missing_scale_rejected(self):
        result = await _run_op("sampling", {"sample_mode": "head"}, _rows(5))

        assert result.success is False
        assert result.error_code == DATA_TRANSFORM_INVALID_STEP_PARAMS

    async def test_invalid_mode_rejected(self):
        result = await _run_op("sampling", {"sample_mode": "stratified", "sample_n": 2}, _rows(5))

        assert result.success is False
        assert result.error_code == DATA_TRANSFORM_INVALID_STEP_PARAMS

    async def test_negative_n_rejected(self):
        result = await _run_op("sampling", {"sample_n": -1}, _rows(5))

        assert result.success is False
        assert result.error_code == DATA_TRANSFORM_INVALID_STEP_PARAMS

    @pytest.mark.parametrize("percent", [0, -5, 150])
    async def test_percent_out_of_range_rejected(self, percent: float):
        result = await _run_op("sampling", {"sample_percent": percent}, _rows(5))

        assert result.success is False
        assert result.error_code == DATA_TRANSFORM_INVALID_STEP_PARAMS


# ---------------------------------------------------------------------------
# 注册与机检口径（T5.6 / design §3 守护④）
# ---------------------------------------------------------------------------


class TestRegistrationAndMachineCheck:
    def test_b5_operations_registered(self):
        from orchestration_platform.engine.nodes.data_transform_ops import (
            registered_operations,
        )

        registered = {op.value for op in registered_operations()}
        assert {"json_path_extract", "sampling", "custom_code"} <= registered

    def test_ops_package_free_of_banned_execution_imports_or_calls(self):
        """机检口径：ops 包模块不得 import/调用 subprocess/exec/eval/
        compile/os.system/importlib（ast 静态扫描，防无意引入执行）。"""
        banned_import_roots = {"subprocess", "importlib"}
        banned_calls = {"exec", "eval", "compile"}
        violations: List[str] = []

        for source_file in sorted(OPS_DIR.glob("*.py")):
            tree = ast.parse(source_file.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.split(".")[0] in banned_import_roots:
                            violations.append(f"{source_file.name}: import {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    if (node.module or "").split(".")[0] in banned_import_roots:
                        violations.append(f"{source_file.name}: from {node.module}")
                elif isinstance(node, ast.Call):
                    func = node.func
                    if isinstance(func, ast.Name) and func.id in banned_calls:
                        violations.append(f"{source_file.name}: {func.id}()")
                    elif (
                        isinstance(func, ast.Attribute)
                        and func.attr == "system"
                        and isinstance(func.value, ast.Name)
                        and func.value.id == "os"
                    ):
                        violations.append(f"{source_file.name}: os.system()")

        assert violations == []

    async def test_chain_sort_then_sampling(self):
        config = DataTransformNodeConfiguration(
            operation=DataTransformOperation.SORT,
            input_dataset_reference="raw_data",
            operations=[
                {"operation": "sort", "params": {"sort_keys": ["id"], "sort_orders": ["desc"]}},
                {"operation": "sampling", "params": {"sample_mode": "head", "sample_n": 2}},
            ],
        )
        outputs: Dict[str, Any] = {"raw_data": _rows(5)}
        executor = DataTransformNodeExecutor()
        result = await executor.execute(_make_context(config, outputs))

        assert result.success is True
        assert [row["id"] for row in result.output_value] == [4, 3]
