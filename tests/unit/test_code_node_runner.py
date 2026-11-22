# =============================================================================
# A1 受限执行核心与 A2 协议单元测试（tests/unit/test_code_node_runner.py）
#
# 覆盖验收：llm_code_nodes_v2 任务 4.1~4.6（T7/T8 / DC-01 / DC-02 /
#      FR-14~17/22/31 / AC-16~20/22/27/30/31/33 / G-04/G-05）。
# 驱动纪律：全同步直调（不经 asyncio，G-05）；A2 真实子进程运行为 N 级
#      （RISK-05，沙箱 asyncio socketpair 拦截），本文件仅覆盖协议纯函数
#      与白名单 parity。
# =============================================================================

import ast
import inspect
import json
import re
import time

import pytest

from orchestration_platform.domain.errors import (
    CODE_OUTPUT_MISSING,
    CODE_OUTPUT_TOO_LARGE,
    CODE_RUNTIME_ERROR,
    CODE_TIMEOUT,
)
from orchestration_platform.engine.nodes.code_execution.runner import (
    CodeRunFailure,
    CodeRunGuardError,
    CodeRunSuccess,
    _run_code_node_script,
    build_execution_namespace,
    format_script_error,
    guard_result_value,
)
from orchestration_platform.engine.nodes.code_execution.subprocess_runner import (
    build_bootstrap_source,
    build_request_payload,
    map_failure_report,
    map_timeout_failure,
    parse_response_payload,
    run_script_subprocess,
)
from orchestration_platform.engine.nodes.code_execution.whitelist import (
    EXCEPTION_TYPE_NAMES,
    ITERATION_COMPOSITION_NAMES,
    NUMERIC_LOGIC_NAMES,
    PREDICATE_REPR_NAMES,
    SEQUENCE_SET_NAMES,
    WHITELISTED_BUILTINS,
    WHITELISTED_BUILTIN_NAMES,
)

DEFAULT_TIMEOUT = 5.0
DEFAULT_MAX_BYTES = 1_000_000


class TestWhitelistSnapshot:
    """白名单快照（任务 4.1）：与 design 附录 B.1 恰等 44 项；剥离项零出现。"""

    def test_whitelist_exactly_matches_design_appendix_b1(self):
        """白名单键集与附录 B.1 的 44 项恰等（多一项少一项均失败，G-04）。"""
        expected = {
            # 数值与逻辑（12）
            "abs", "bool", "divmod", "float", "int", "pow", "round",
            "hash", "hex", "oct", "bin", "ord",
            # 迭代与组合（10）
            "enumerate", "filter", "iter", "len", "map", "next",
            "range", "reversed", "sorted", "zip",
            # 序列与集合构造（7）
            "dict", "frozenset", "list", "set", "slice", "tuple", "chr",
            # 判定与表示（7）
            "all", "any", "callable", "isinstance", "issubclass", "repr", "str",
            # 异常类型（8）
            "Exception", "ValueError", "TypeError", "KeyError",
            "IndexError", "ZeroDivisionError", "ArithmeticError", "StopIteration",
        }
        assert set(WHITELISTED_BUILTIN_NAMES) == expected
        assert len(WHITELISTED_BUILTIN_NAMES) == 44
        assert len(set(WHITELISTED_BUILTIN_NAMES)) == 44  # 无重复
        assert len(WHITELISTED_BUILTINS) == 44

    def test_category_counts_frozen(self):
        """五类分计数 12/10/7/7/8（附录 B.1 分类冻结）。"""
        assert len(NUMERIC_LOGIC_NAMES) == 12
        assert len(ITERATION_COMPOSITION_NAMES) == 10
        assert len(SEQUENCE_SET_NAMES) == 7
        assert len(PREDICATE_REPR_NAMES) == 7
        assert len(EXCEPTION_TYPE_NAMES) == 8

    def test_stripped_builtins_absent(self):
        """B.2 剥离项与未列内建零出现（封闭清单缺省不可达）。"""
        stripped = {
            "open", "input", "print", "exec", "eval", "compile", "__import__",
            "breakpoint", "globals", "locals", "vars", "dir", "getattr",
            "setattr", "delattr", "memoryview", "bytearray", "help", "exit",
            "quit", "copyright", "credits", "license", "type", "super",
            "property", "staticmethod", "min", "max", "sum",
        }
        assert stripped.isdisjoint(set(WHITELISTED_BUILTIN_NAMES))
        assert stripped.isdisjoint(set(WHITELISTED_BUILTINS))

    def test_contract_variables_not_in_whitelist(self):
        """params/result 两个契约变量名不在白名单内（design B.3，命名空间单独注入）。"""
        assert "params" not in WHITELISTED_BUILTINS
        assert "result" not in WHITELISTED_BUILTINS


class TestExecutionNamespace:
    """执行命名空间构建（任务 4.2）：白名单内建 + params 注入 + result 预置。"""

    def test_namespace_contains_contract_variables(self):
        """params 注入、result 预置 None、__name__ 固定（FR-14 / D-07）。"""
        namespace = build_execution_namespace({"x": 1})
        assert namespace["params"] == {"x": 1}
        assert namespace["result"] is None
        assert namespace["__name__"] == "__workflow_code__"

    def test_namespace_builtins_is_fresh_whitelist_copy(self):
        """__builtins__ 为白名单新拷贝：篡改不污染白名单常量（防跨执行残留）。"""
        namespace = build_execution_namespace({})
        builtins_dict = namespace["__builtins__"]
        assert set(builtins_dict) == set(WHITELISTED_BUILTIN_NAMES)
        builtins_dict["abs"] = None
        assert WHITELISTED_BUILTINS["abs"] is abs

    def test_namespaces_independent_per_execution(self):
        """两次执行的命名空间互不影响（单次编译单次执行，FR-28）。"""
        first = build_execution_namespace({})
        second = build_execution_namespace({})
        first["__builtins__"]["injected_marker"] = 1
        assert "injected_marker" not in second["__builtins__"]


class TestRunCodeNodeScriptCore:
    """A1 执行核心（T7）：同步直调 _run_code_node_script，不经 asyncio（G-05）。"""

    def test_pure_computation_success(self):
        """纯计算脚本：result = {"sum": 1+2} → 成功且值恰等（AC-16/22）。"""
        outcome = _run_code_node_script(
            'result = {"sum": 1 + 2}', {}, DEFAULT_TIMEOUT, DEFAULT_MAX_BYTES
        )
        assert isinstance(outcome, CodeRunSuccess)
        assert outcome.result == {"sum": 3}
        assert outcome.duration_seconds >= 0.0

    def test_params_injection_readable(self):
        """params 注入：脚本内可读装配产物（AC-16 / FR-14）。"""
        outcome = _run_code_node_script(
            "result = params['a'] + params['b']",
            {"a": 1, "b": 2},
            DEFAULT_TIMEOUT,
            DEFAULT_MAX_BYTES,
        )
        assert isinstance(outcome, CodeRunSuccess)
        assert outcome.result == 3

    def test_result_missing_maps_to_output_missing(self):
        """脚本未定义 result → CODE_OUTPUT_MISSING（AC-17）。"""
        outcome = _run_code_node_script(
            "x = 1", {}, DEFAULT_TIMEOUT, DEFAULT_MAX_BYTES
        )
        assert isinstance(outcome, CodeRunFailure)
        assert outcome.error_code == CODE_OUTPUT_MISSING
        assert "result" in outcome.error_message

    def test_explicit_none_result_also_missing(self):
        """显式 result = None 与未定义统一判缺失（D-07 单一判定机制）。"""
        outcome = _run_code_node_script(
            "result = None", {}, DEFAULT_TIMEOUT, DEFAULT_MAX_BYTES
        )
        assert isinstance(outcome, CodeRunFailure)
        assert outcome.error_code == CODE_OUTPUT_MISSING

    def test_open_unreachable_nameerror(self):
        """脚本内 open(...) → NameError（B.2 剥离项缺省不可达，AC-22）。"""
        outcome = _run_code_node_script(
            "result = open('x')", {}, DEFAULT_TIMEOUT, DEFAULT_MAX_BYTES
        )
        assert isinstance(outcome, CodeRunFailure)
        assert outcome.error_code == CODE_RUNTIME_ERROR
        assert "NameError" in outcome.error_message
        assert "open" in outcome.error_message

    def test_min_not_in_closed_whitelist_nameerror(self):
        """封闭清单：min/max/sum 未列入 B.1 → NameError（静态校验放行、白名单拦截）。"""
        outcome = _run_code_node_script(
            "result = min(1, 2)", {}, DEFAULT_TIMEOUT, DEFAULT_MAX_BYTES
        )
        assert isinstance(outcome, CodeRunFailure)
        assert outcome.error_code == CODE_RUNTIME_ERROR
        assert "NameError" in outcome.error_message

    def test_import_statement_unreachable(self):
        """import 语句 → __import__ 剥离 → ImportError（运行期第二道防线）。"""
        outcome = _run_code_node_script(
            "import json\nresult = 1", {}, DEFAULT_TIMEOUT, DEFAULT_MAX_BYTES
        )
        assert isinstance(outcome, CodeRunFailure)
        assert outcome.error_code == CODE_RUNTIME_ERROR
        assert "ImportError" in outcome.error_message

    def test_zero_division_runtime_error_with_type_name(self):
        """1÷0 → CODE_RUNTIME_ERROR 且消息含 ZeroDivisionError（AC-20）。"""
        outcome = _run_code_node_script(
            "result = 1 / 0", {}, DEFAULT_TIMEOUT, DEFAULT_MAX_BYTES
        )
        assert isinstance(outcome, CodeRunFailure)
        assert outcome.error_code == CODE_RUNTIME_ERROR
        assert "ZeroDivisionError" in outcome.error_message

    def test_runtime_error_message_excludes_script_text(self):
        """运行期异常消息不含脚本全文（防日志泄漏，AC-30）。"""
        secret = "UNIQUE_MARKER_MUST_NOT_LEAK"
        script = f"value = '{secret}'\nresult = 1 / 0"
        outcome = _run_code_node_script(
            script, {}, DEFAULT_TIMEOUT, DEFAULT_MAX_BYTES
        )
        assert isinstance(outcome, CodeRunFailure)
        assert secret not in outcome.error_message

    def test_unserializable_function_result_rejected(self):
        """result 为函数 → 输出不可序列化 → CODE_RUNTIME_ERROR（AC-18）。"""
        outcome = _run_code_node_script(
            "def _f():\n    pass\nresult = _f",
            {},
            DEFAULT_TIMEOUT,
            DEFAULT_MAX_BYTES,
        )
        assert isinstance(outcome, CodeRunFailure)
        assert outcome.error_code == CODE_RUNTIME_ERROR
        assert "输出不可序列化" in outcome.error_message

    def test_nan_result_rejected_as_unserializable(self):
        """NaN 非合法 JSON（allow_nan=False）→ 不可序列化（纯 JSON 契约）。"""
        outcome = _run_code_node_script(
            'result = float("nan")', {}, DEFAULT_TIMEOUT, DEFAULT_MAX_BYTES
        )
        assert isinstance(outcome, CodeRunFailure)
        assert outcome.error_code == CODE_RUNTIME_ERROR
        assert "输出不可序列化" in outcome.error_message

    def test_result_round_trip_pure_json(self):
        """成功路径 result JSON 往返为纯 JSON 形态（AC-17 / FR-16）。"""
        outcome = _run_code_node_script(
            'result = {"nested": [1, 2.5, "文本", True, None]}',
            {},
            DEFAULT_TIMEOUT,
            DEFAULT_MAX_BYTES,
        )
        assert isinstance(outcome, CodeRunSuccess)
        assert outcome.result == {"nested": [1, 2.5, "文本", True, None]}

    def test_output_too_large_with_actual_bytes(self):
        """result 超输出上限 → CODE_OUTPUT_TOO_LARGE 附上界与实际字节数（AC-19）。"""
        outcome = _run_code_node_script(
            'result = "x" * 100', {}, DEFAULT_TIMEOUT, 50
        )
        assert isinstance(outcome, CodeRunFailure)
        assert outcome.error_code == CODE_OUTPUT_TOO_LARGE
        assert "50" in outcome.error_message
        assert "102" in outcome.error_message

    def test_output_within_limit_boundary_success(self):
        """输出恰在上限内 → 成功（边界含入，无截断部分成功）。"""
        outcome = _run_code_node_script(
            'result = "x" * 48', {}, DEFAULT_TIMEOUT, 50
        )
        assert isinstance(outcome, CodeRunSuccess)
        assert outcome.result == "x" * 48

    def test_whitelisted_builtins_combination_usable(self):
        """白名单内建组合可用：abs/len/sorted/chr/all（B.1 保留项可达）。"""
        script = (
            "values = [abs(-3), len([1, 2]), sorted([3, 1, 2])[0]]\n"
            "result = {'v': values, 'chars': [chr(65)], "
            "'flag': all([True, True])}"
        )
        outcome = _run_code_node_script(
            script, {}, DEFAULT_TIMEOUT, DEFAULT_MAX_BYTES
        )
        assert isinstance(outcome, CodeRunSuccess)
        assert outcome.result == {"v": [3, 2, 1], "chars": ["A"], "flag": True}

    def test_whitelist_exception_catchable(self):
        """白名单异常可捕获：脚本内 try/except ZeroDivisionError 正常分支。"""
        script = (
            "try:\n"
            "    value = 1 / 0\n"
            "except ZeroDivisionError:\n"
            "    value = 'caught'\n"
            "result = value"
        )
        outcome = _run_code_node_script(
            script, {}, DEFAULT_TIMEOUT, DEFAULT_MAX_BYTES
        )
        assert isinstance(outcome, CodeRunSuccess)
        assert outcome.result == "caught"

    def test_syntax_error_falls_back_to_runtime_error(self):
        """语法错误经静态校验前置拦截；直调路径兜底归 RUNTIME（fail-closed）。"""
        outcome = _run_code_node_script(
            "def f(:", {}, DEFAULT_TIMEOUT, DEFAULT_MAX_BYTES
        )
        assert isinstance(outcome, CodeRunFailure)
        assert outcome.error_code == CODE_RUNTIME_ERROR
        assert "SyntaxError" in outcome.error_message

    def test_no_script_cache_across_executions(self):
        """禁脚本缓存跨实例复用：前次执行定义的名字不泄漏到后次（FR-28）。"""
        first = _run_code_node_script(
            "leak_marker = 42\nresult = leak_marker",
            {},
            DEFAULT_TIMEOUT,
            DEFAULT_MAX_BYTES,
        )
        assert isinstance(first, CodeRunSuccess)
        assert first.result == 42
        second = _run_code_node_script(
            "result = leak_marker", {}, DEFAULT_TIMEOUT, DEFAULT_MAX_BYTES
        )
        assert isinstance(second, CodeRunFailure)
        assert "NameError" in second.error_message

    def test_same_cause_same_code_zero_division_three_runs(self):
        """同因同码 ×3：同一 1÷0 脚本三次执行同码同消息（AC-33 / FR-33）。"""
        outcomes = [
            _run_code_node_script(
                "result = 1 / 0", {}, DEFAULT_TIMEOUT, DEFAULT_MAX_BYTES
            )
            for _ in range(3)
        ]
        for outcome in outcomes:
            assert isinstance(outcome, CodeRunFailure)
            assert outcome.error_code == CODE_RUNTIME_ERROR
        assert outcomes[0].error_message == outcomes[1].error_message
        assert outcomes[1].error_message == outcomes[2].error_message

    def test_same_cause_same_code_missing_result_three_runs(self):
        """同因同码 ×3：result 缺失三次一致（AC-33 / FR-33）。"""
        outcomes = [
            _run_code_node_script(
                "x = 1", {}, DEFAULT_TIMEOUT, DEFAULT_MAX_BYTES
            )
            for _ in range(3)
        ]
        for outcome in outcomes:
            assert isinstance(outcome, CodeRunFailure)
            assert outcome.error_code == CODE_OUTPUT_MISSING
        assert outcomes[0].error_message == outcomes[1].error_message
        assert outcomes[1].error_message == outcomes[2].error_message


class TestRunCodeNodeScriptTimeout:
    """A1 超时护栏（T8）：等待型超时 → CODE_TIMEOUT + duration≈时限 + abandoned_thread。

    等待型脚本经 params 注入 time.sleep（脚本内 import time 不可达——
    __import__ 剥离），runner 级直调绕过装配层。
    """

    def test_wait_type_timeout_maps_to_code_timeout(self):
        """等待型脚本超时 → CODE_TIMEOUT + abandoned_thread=true（AC-27/31）。"""
        outcome = _run_code_node_script(
            "params['delay'](0.5)", {"delay": time.sleep}, 0.15, DEFAULT_MAX_BYTES
        )
        assert isinstance(outcome, CodeRunFailure)
        assert outcome.error_code == CODE_TIMEOUT
        assert outcome.abandoned_thread is True
        assert outcome.killed is False

    def test_timeout_duration_close_to_limit(self):
        """duration≈时限：0.1 ≤ duration < 0.5（AC-31 断言口径）。"""
        outcome = _run_code_node_script(
            "params['delay'](0.5)", {"delay": time.sleep}, 0.15, DEFAULT_MAX_BYTES
        )
        assert isinstance(outcome, CodeRunFailure)
        assert 0.1 <= outcome.duration_seconds < 0.5

    def test_timeout_message_has_limit_not_script_text(self):
        """超时消息含时限、不含脚本全文/哨兵标记（AC-30 违规记录无全文）。"""
        sentinel = "UNIQUE_SLEEP_MARKER"
        script = f"# {sentinel}\nparams['delay'](0.5)"
        outcome = _run_code_node_script(
            script, {"delay": time.sleep}, 0.15, DEFAULT_MAX_BYTES
        )
        assert isinstance(outcome, CodeRunFailure)
        assert "0.15" in outcome.error_message
        assert sentinel not in outcome.error_message

    def test_fast_script_completes_without_thread_side_effects(self):
        """短脚本正常完成：无超时副作用，duration 记录于结果（RISK-02 边界）。"""
        outcome = _run_code_node_script(
            "result = 1 + 1", {}, DEFAULT_TIMEOUT, DEFAULT_MAX_BYTES
        )
        assert isinstance(outcome, CodeRunSuccess)
        assert outcome.result == 2
        assert outcome.duration_seconds >= 0.0

    def test_timeout_same_code_three_runs(self):
        """同因同码 ×3：超时三次同码且 abandoned_thread 恒真（AC-33）。"""
        outcomes = [
            _run_code_node_script(
                "params['delay'](0.5)", {"delay": time.sleep}, 0.15, DEFAULT_MAX_BYTES
            )
            for _ in range(3)
        ]
        for outcome in outcomes:
            assert isinstance(outcome, CodeRunFailure)
            assert outcome.error_code == CODE_TIMEOUT
            assert outcome.abandoned_thread is True


class TestGuardResultValue:
    """result 输出护栏直调（任务 4.4，A1/A2 共用）。"""

    def test_none_raises_output_missing(self):
        """result is None → CODE_OUTPUT_MISSING（D-07 统一判定）。"""
        with pytest.raises(CodeRunGuardError) as exc_info:
            guard_result_value(None, 1000)
        assert exc_info.value.code == CODE_OUTPUT_MISSING
        assert "result" in exc_info.value.message

    def test_function_value_raises_unserializable(self):
        """函数值 → CODE_RUNTIME_ERROR「输出不可序列化」（AC-18）。"""
        with pytest.raises(CodeRunGuardError) as exc_info:
            guard_result_value({"f": print}, 1000)
        assert exc_info.value.code == CODE_RUNTIME_ERROR
        assert "输出不可序列化" in exc_info.value.message

    def test_nan_raises_unserializable(self):
        """NaN → allow_nan=False 拒绝 → 不可序列化。"""
        with pytest.raises(CodeRunGuardError) as exc_info:
            guard_result_value(float("nan"), 1000)
        assert exc_info.value.code == CODE_RUNTIME_ERROR
        assert "输出不可序列化" in exc_info.value.message

    def test_oversize_raises_too_large_with_bounds(self):
        """超限 → CODE_OUTPUT_TOO_LARGE 且消息附上界与实际字节数（AC-19）。"""
        with pytest.raises(CodeRunGuardError) as exc_info:
            guard_result_value("x" * 100, 50)
        assert exc_info.value.code == CODE_OUTPUT_TOO_LARGE
        assert "50" in exc_info.value.message
        assert "102" in exc_info.value.message

    def test_round_trip_returns_pure_json_value(self):
        """成功路径 JSON 往返值恰等（JsonValue 契约，AC-17）。"""
        value = {"a": [1, "文本", None, True], "b": {"c": 2.5}}
        assert guard_result_value(value, 1_000_000) == value


class TestFormatScriptError:
    """运行期异常消息形态：「类型名: 文本」，≤512 截断。"""

    def test_combines_type_and_message(self):
        """类型名与消息以「: 」拼接。"""
        formatted = format_script_error("ZeroDivisionError", "division by zero")
        assert formatted == "ZeroDivisionError: division by zero"

    def test_truncates_to_message_limit(self):
        """超长消息截断 ≤512 字符（脱敏防日志膨胀）。"""
        formatted = format_script_error("ValueError", "x" * 1000)
        assert len(formatted) <= 512


class TestSubprocessProtocolPure:
    """A2 协议纯函数（任务 4.5）：载荷构造 / 响应解析 / 失败映射 / 超时映射。"""

    def test_build_request_payload_exact_keys(self):
        """请求载荷恰三键 script/params/output_max_bytes；timeout 不下发。"""
        payload = build_request_payload("result = 1", {"x": 2}, 4096)
        assert payload == {
            "script": "result = 1",
            "params": {"x": 2},
            "output_max_bytes": 4096,
        }
        assert "timeout" not in payload
        assert "timeout_seconds" not in payload

    def test_build_request_payload_wire_serializable(self):
        """请求载荷可 JSON 序列化为线协议字节（ensure_ascii 兼容中文参数）。"""
        payload = build_request_payload("result = 1", {"文本": "值"}, 4096)
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        assert json.loads(encoded.decode("utf-8")) == payload

    def test_parse_response_valid_json_object(self):
        """合法 JSON 对象响应可解析。"""
        report = parse_response_payload(
            '{"ok": true, "result": 42}'.encode("utf-8")
        )
        assert report["ok"] is True
        assert report["result"] == 42

    def test_parse_response_rejects_non_json(self):
        """非 JSON 输出 → ValueError（fail-closed）。"""
        with pytest.raises(ValueError):
            parse_response_payload(b"not json at all")

    def test_parse_response_rejects_non_object(self):
        """JSON 数组（非对象）→ ValueError。"""
        with pytest.raises(ValueError):
            parse_response_payload(b"[1, 2, 3]")

    def test_parse_response_rejects_empty_stdout(self):
        """子进程无输出（崩溃形态）→ ValueError（fail-closed）。"""
        with pytest.raises(ValueError):
            parse_response_payload(b"")

    def test_map_timeout_failure_killed_flag(self):
        """超时映射：CODE_TIMEOUT + killed=true + 时限与时长入消息（AC-27）。

        消息仅由时限/时长构成（纯函数不接收脚本文本，结构上无全文泄漏面）。
        """
        failure = map_timeout_failure(1.5, 1.502)
        assert failure.error_code == CODE_TIMEOUT
        assert failure.killed is True
        assert failure.abandoned_thread is False
        assert "1.5" in failure.error_message
        assert "1.502" in failure.error_message

    def test_map_failure_report_script_error(self):
        """script_error 报告 → CODE_RUNTIME_ERROR 含异常类型名。"""
        code, message = map_failure_report(
            {
                "ok": False,
                "kind": "script_error",
                "error_type": "ZeroDivisionError",
                "error_message": "division by zero",
            },
            4096,
        )
        assert code == CODE_RUNTIME_ERROR
        assert "ZeroDivisionError" in message

    def test_map_failure_report_result_missing(self):
        """result_missing 报告 → CODE_OUTPUT_MISSING。"""
        code, message = map_failure_report(
            {"ok": False, "kind": "result_missing"}, 4096
        )
        assert code == CODE_OUTPUT_MISSING
        assert "result" in message

    def test_map_failure_report_unserializable(self):
        """unserializable 报告 → CODE_RUNTIME_ERROR「输出不可序列化」。"""
        code, message = map_failure_report(
            {"ok": False, "kind": "unserializable"}, 4096
        )
        assert code == CODE_RUNTIME_ERROR
        assert "输出不可序列化" in message

    def test_map_failure_report_too_large(self):
        """too_large 报告 → CODE_OUTPUT_TOO_LARGE 附上界与实际字节数。"""
        code, message = map_failure_report(
            {"ok": False, "kind": "too_large", "actual_bytes": 9999}, 4096
        )
        assert code == CODE_OUTPUT_TOO_LARGE
        assert "4096" in message
        assert "9999" in message

    def test_map_failure_report_unknown_kind_fail_closed(self):
        """未知 kind → CODE_RUNTIME_ERROR（fail-closed 不静默）。"""
        code, _ = map_failure_report({"ok": False, "kind": "surprise"}, 4096)
        assert code == CODE_RUNTIME_ERROR

    def test_run_script_subprocess_is_async_entry(self):
        """A2 入口为协程函数（真实子进程运行 N 级，本文件不执行真实子进程）。"""
        assert inspect.iscoroutinefunction(run_script_subprocess)


class TestBootstrapWhitelistParity:
    """A2 bootstrap 白名单 parity（任务 4.5 / G-04 / design B.3 单一定义双端同源）。"""

    def test_bootstrap_embeds_same_whitelist_constant(self):
        """bootstrap 内嵌清单与 whitelist 常量恰等（A1/A2 parity）。"""
        source = build_bootstrap_source()
        match = re.search(r"_NAMES = (\([^)]*\))", source)
        assert match is not None
        embedded = ast.literal_eval(match.group(1))
        assert tuple(embedded) == tuple(WHITELISTED_BUILTIN_NAMES)

    def test_bootstrap_source_parses_as_python(self):
        """bootstrap 为合法 Python 源码（python -I -c 可执行形态）。"""
        ast.parse(build_bootstrap_source())

    def test_bootstrap_reads_request_from_stdin(self):
        """bootstrap 经 stdin 读请求、内建取自 builtins 模块（协议通道核对）。"""
        source = build_bootstrap_source()
        assert "sys.stdin.read()" in source
        assert "import builtins" in source
