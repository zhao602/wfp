# =============================================================================
# CodeNodeExecutor 单元测试（llm_code_nodes_v2 任务 5.1/5.2/5.5 T9 / spec §6.2）
# 执行命令：PYTHONPATH=src python -m pytest tests/unit/test_code_node_executor.py -v
#
# 覆盖（任务 5.5 出口闸门 T9：五段管线 + 失败映射 + 违规发布）：
#   ① 开关：默认关闭/字段缺失/显式关闭 → CODE_NODE_DISABLED，守卫零调用，
#      metadata 无指纹（G-03）；
#   ② 段一 静态校验：禁项/语法/空脚本/语言/敏感属性分流；静态失败先于
#      装配与执行；消息不含脚本全文（FR-28）；
#   ③ 段二 params 装配：三态（None 平铺 / [] 显空 / 非空选择集）+ 悬空
#      引用/类型不符 fail-fast（透传既有装配码）；
#   ④ 段三 策略取小（DC-10）：默认护栏 / 策略收窄取小 / 策略更宽保守
#      settings / 策略不存在与仓储缺失 fail-closed / tenant_id 隔离 /
#      未引用策略零仓储访问；
#   ⑤ 段四 通道分派（DC-16）：默认 inprocess / 非法值降级 + 告警 /
#      subprocess 分派 / A2 通道异常 fail-closed；
#   ⑥ 段五 执行与输出：真实 A1（成功/运行期异常/result 缺失/不可序列化/
#      输出超限真实护栏）/ 超时映射（monkeypatched 失败结果驱动，真实
#      超时由 T8 test_code_node_runner.py 覆盖）/ 同因同码 ×3 /
#      duration+channel 登记；
#   ⑦ metadata 契约：成功恰六键 / 早期失败四键 / entry_function 零读取
#      （FR-18）/ dependencies 零消费（FR-19）/ output_name 回退 node_id /
#      script_length 按 UTF-8 字节数；
#   ⑧ 违规发布（DC-10-3）：超时（TIMEOUT）/ 输出超限（RESOURCE_EXCEEDED）
#      发布且记录不含脚本全文；运行期异常不发布；发布器缺失跳过；发布
#      异常仅告警不改变失败结果；
#   ⑨ 退役守卫：CODE_SCRIPT_NOT_EXECUTABLE 本执行器零产生（DC-11）；
#      五段顺序源码级机械证明。
# =============================================================================

# 沙箱限制：本机 asyncio 事件循环不可用（socketpair 被网络策略拦截，
# WinError 10051 / _ssock AttributeError），run_coro 采用手动驱动协程
# 方式（send(None) 循环），不创建事件循环，故无需引入 asyncio 模块。

import hashlib
import logging
from pathlib import Path
from types import SimpleNamespace

import orchestration_platform.engine.nodes.code_node_executor as code_node_executor_module
from orchestration_platform.domain.errors import (
    CODE_BANNED_CONSTRUCT,
    CODE_NODE_DISABLED,
    CODE_OUTPUT_MISSING,
    CODE_OUTPUT_TOO_LARGE,
    CODE_RUNTIME_ERROR,
    CODE_SCRIPT_NOT_EXECUTABLE,
    CODE_SYNTAX_ERROR,
    CODE_TIMEOUT,
    NODE_INPUT_REFERENCE_MISSING,
    NODE_INPUT_TYPE_MISMATCH,
)
from orchestration_platform.domain.models.enums import NodeType, ViolationType
from orchestration_platform.domain.models.node_configurations import (
    AgentInputEntry,
    CodeNodeConfiguration,
)
from orchestration_platform.domain.models.node_definition import NodeDefinition
from orchestration_platform.domain.models.shared_state import SharedState
from orchestration_platform.engine.nodes.base import NodeExecutionContext
from orchestration_platform.engine.nodes.code_execution.runner import (
    CodeRunFailure,
    CodeRunSuccess,
)
from orchestration_platform.engine.nodes.code_node_executor import CodeNodeExecutor
from orchestration_platform.integrations.sandbox.models import SandboxPolicy

# 被测源码文件（退役码零产生 + 五段顺序源码级断言）
_EXECUTOR_SOURCE = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "orchestration_platform"
    / "engine"
    / "nodes"
    / "code_node_executor.py"
)


def run_coro(coro):
    """手动驱动协程至完成（沙箱事件循环不可用时的 fallback）。"""
    try:
        while True:
            coro.send(None)
    except StopIteration as exc:
        return exc.value


# =============================================================================
# 测试辅助
# =============================================================================

# 开关默认态：字段缺省（getattr 防御口径，字段缺失视同关闭，FR-03-b）
_SETTINGS_CLOSED = SimpleNamespace()

# 超时失败消息样例（与 runner A1 超时口径一致，驱动映射与违规发布断言）
_TIMEOUT_MESSAGE = (
    "代码节点执行超时：已放弃工作线程（abandoned_thread），"
    "时限 10 秒，实际 10.012 秒"
)

# 脚本泄漏探针：唯一标记串，用于断言消息/违规记录不外泄脚本全文
_SCRIPT_MARKER = "SECRET_SCRIPT_MARKER_7f3a9"


def _settings_open(**overrides) -> SimpleNamespace:
    """显式开启态（P1 已落地的四个 settings 字段全量供给）。"""
    base = {
        "code_node_enabled": True,
        "code_execution_timeout_seconds": 10,
        "code_output_max_bytes": 1048576,
        "code_execution_channel": "inprocess",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _make_config(**kwargs) -> CodeNodeConfiguration:
    """构造代码节点配置（脚本文本必备）。"""
    defaults = {"code": "result = 1"}
    defaults.update(kwargs)
    return CodeNodeConfiguration(**defaults)


def _make_context(
    config: CodeNodeConfiguration,
    outputs: dict = None,
    node_id: str = "code-1",
    output_name: str = "code_result",
) -> NodeExecutionContext:
    """构造执行上下文（可带命名输出表）。"""
    node_def = NodeDefinition(
        node_id=node_id,
        name="code node",
        node_type=NodeType.CODE,
        configuration=config,
        output_name=output_name,
    )
    shared = SharedState(instance_id="inst-1")
    if outputs:
        shared.node_outputs.update(outputs)
    return NodeExecutionContext(
        node_definition=node_def,
        shared_state=shared,
        instance_id="inst-1",
        trace_id="trace-1",
        timeout_seconds=30,
    )


def _executor(
    settings,
    policy_repository=None,
    violation_publisher=None,
    tenant_id: str = "tenant-1",
) -> CodeNodeExecutor:
    """构造执行器并覆盖 settings（依赖注入缺省 None → 未引用路径零开销）。"""
    executor = CodeNodeExecutor(
        policy_repository=policy_repository,
        violation_publisher=violation_publisher,
        tenant_id=tenant_id,
    )
    executor._settings = settings
    return executor


def _make_policy(policy_id: str = "pol-1", **kwargs) -> SandboxPolicy:
    """构造沙箱策略（workdir 取系统临时目录，仅作护栏值载体）。"""
    import tempfile

    defaults = {
        "policy_id": policy_id,
        "tenant_id": "tenant-1",
        "workdir": tempfile.gettempdir(),
        "execution_timeout_seconds": 30,
        "output_bytes_limit": 2097152,
    }
    defaults.update(kwargs)
    return SandboxPolicy(**defaults)


def _success_outcome() -> CodeRunSuccess:
    return CodeRunSuccess(result={"ok": True}, duration_seconds=0.012)


def _failure_outcome(error_code: str, error_message: str, duration_seconds: float = 0.5, **flags) -> CodeRunFailure:
    return CodeRunFailure(
        error_code=error_code,
        error_message=error_message,
        duration_seconds=duration_seconds,
        **flags,
    )


class _RunnerSpy:
    """A1 同步执行 spy：记录入参，返回预置 outcome。"""

    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    def __call__(self, script, params, timeout_seconds, output_max_bytes):
        self.calls.append((script, params, timeout_seconds, output_max_bytes))
        return self.outcome


class _AsyncRunnerSpy:
    """A2 异步执行 spy：记录入参，返回预置 outcome。"""

    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    async def __call__(self, script, params, timeout_seconds, output_max_bytes):
        self.calls.append((script, params, timeout_seconds, output_max_bytes))
        return self.outcome


class _AsyncRaisingRunner:
    """A2 异常注入 spy：记录入参后抛预置异常。"""

    def __init__(self, exc):
        self.exc = exc
        self.calls = []

    async def __call__(self, script, params, timeout_seconds, output_max_bytes):
        self.calls.append((script, params, timeout_seconds, output_max_bytes))
        raise self.exc


class _RepoSpy:
    """沙箱策略仓储 spy：记录查询入参，返回预置策略表。"""

    def __init__(self, policies=None):
        self.policies = dict(policies or {})
        self.get_calls = []

    async def get(self, policy_id, tenant_id):
        self.get_calls.append((policy_id, tenant_id))
        return self.policies.get(policy_id)


class _PublisherSpy:
    """违规发布器 spy：记录发布入参；可注入发布异常。"""

    def __init__(self, raise_on_publish: bool = False):
        self.calls = []
        self.raise_on_publish = raise_on_publish

    async def publish_violations(self, result, session_id, tenant_id, policy_id):
        self.calls.append((result, session_id, tenant_id, policy_id))
        if self.raise_on_publish:
            raise RuntimeError("发布通道故障")


def _patch_runner(monkeypatch, spy) -> None:
    """将 A1 同步执行入口替换为 spy（通道用例另 patch A2 入口）。"""
    monkeypatch.setattr(code_node_executor_module, "_run_code_node_script", spy)


# =============================================================================
# ① 能力开关（关闭态 G-03：守卫零调用、metadata 无指纹）
# =============================================================================


class TestCapabilitySwitch:
    def test_node_type_returns_code(self):
        assert CodeNodeExecutor().node_type == NodeType.CODE

    def test_disabled_when_flag_absent(self, monkeypatch):
        """开关字段缺省 → CODE_NODE_DISABLED；守卫零调用（G-03）。"""

        def _guard_spy(code, language):
            raise AssertionError("关闭分支不得触达静态校验")

        monkeypatch.setattr(
            code_node_executor_module, "validate_custom_code", _guard_spy
        )
        config = _make_config(code=f'print("{_SCRIPT_MARKER}")')
        result = run_coro(_executor(_SETTINGS_CLOSED).execute(_make_context(config)))
        assert result.success is False
        assert result.error_code == CODE_NODE_DISABLED
        assert "code-1" in result.error_message
        assert "code_node_enabled" in result.error_message
        # 关闭分支先于任何脚本访问：无指纹、无长度、无脚本全文外泄
        assert result.metadata == {}
        assert _SCRIPT_MARKER not in result.error_message
        assert result.output_value is None
        assert result.output_name is None

    def test_disabled_when_flag_false(self):
        """显式关闭 → CODE_NODE_DISABLED（与缺省等价，INV-07）。"""
        result = run_coro(
            _executor(SimpleNamespace(code_node_enabled=False)).execute(
                _make_context(_make_config())
            )
        )
        assert result.success is False
        assert result.error_code == CODE_NODE_DISABLED
        assert result.metadata == {}

    def test_disabled_branch_precedes_script_access_in_source(self):
        """关闭分支代码顺序：开关判断先于 configuration 读取（G-03 顺序确认）。"""
        source = _EXECUTOR_SOURCE.read_text(encoding="utf-8")
        assert source.index("code_node_enabled") < source.index(
            "context.node_definition.configuration"
        )


# =============================================================================
# ② 段一：静态合规校验分流（静态失败先于装配与执行）
# =============================================================================


class TestStaticCheckSegment:
    def test_banned_import_fails_before_execution(self, monkeypatch):
        """敏感 import → CODE_BANNED_CONSTRUCT，不执行脚本。"""
        spy = _RunnerSpy(_success_outcome())
        _patch_runner(monkeypatch, spy)
        config = _make_config(code=f"value = '{_SCRIPT_MARKER}'\nimport os\nresult = 1")
        result = run_coro(_executor(_settings_open()).execute(_make_context(config)))
        assert result.success is False
        assert result.error_code == CODE_BANNED_CONSTRUCT
        assert "敏感模块导入：import os" in result.error_message
        assert spy.calls == []
        assert result.output_value is None

    def test_syntax_error_maps_with_line_number(self, monkeypatch):
        """语法失败 → CODE_SYNTAX_ERROR，消息含行号（DC-16-5）。"""
        spy = _RunnerSpy(_success_outcome())
        _patch_runner(monkeypatch, spy)
        config = _make_config(code="def f(:\n    pass\n")
        result = run_coro(_executor(_settings_open()).execute(_make_context(config)))
        assert result.error_code == CODE_SYNTAX_ERROR
        assert "第 1 行" in result.error_message
        assert spy.calls == []

    def test_empty_script_banned(self):
        """空脚本 → CODE_BANNED_CONSTRUCT「脚本文本为空」。"""
        result = run_coro(
            _executor(_settings_open()).execute(_make_context(_make_config(code="")))
        )
        assert result.error_code == CODE_BANNED_CONSTRUCT
        assert "脚本文本为空" in result.error_message

    def test_unsupported_language_banned(self):
        """language=javascript → CODE_BANNED_CONSTRUCT，注明本期仅支持 python。"""
        result = run_coro(
            _executor(_settings_open()).execute(
                _make_context(_make_config(language="javascript"))
            )
        )
        assert result.error_code == CODE_BANNED_CONSTRUCT
        assert "本期仅支持 python" in result.error_message

    def test_sensitive_attribute_banned(self):
        """敏感属性访问（params.__globals__）→ CODE_BANNED_CONSTRUCT。"""
        config = _make_config(code="result = 1\nleak = params.__globals__\n")
        result = run_coro(_executor(_settings_open()).execute(_make_context(config)))
        assert result.error_code == CODE_BANNED_CONSTRUCT
        assert "敏感属性访问" in result.error_message

    def test_static_failure_precedes_params_assembly(self, monkeypatch):
        """静态失败先于 params 装配（禁项脚本 + 悬空引用 → 禁项胜出）。"""

        def _assembly_spy(inputs, outputs_snapshot):
            raise AssertionError("静态失败先于 params 装配，不得触达装配")

        monkeypatch.setattr(code_node_executor_module, "assemble_params", _assembly_spy)
        config = _make_config(
            code="import os\nresult = 1",
            inputs=[AgentInputEntry(reference="missing_out")],
        )
        result = run_coro(_executor(_settings_open()).execute(_make_context(config)))
        assert result.error_code == CODE_BANNED_CONSTRUCT

    def test_static_failure_message_no_full_script(self):
        """失败消息不含脚本全文（FR-28），指纹以摘要形态留存可溯源。"""
        config = _make_config(code=f"value = '{_SCRIPT_MARKER}'\nimport os\nresult = 1")
        result = run_coro(_executor(_settings_open()).execute(_make_context(config)))
        assert _SCRIPT_MARKER not in result.error_message
        assert result.metadata["script_fingerprint"]


# =============================================================================
# ③ 段二：params 三态装配（与 agent/LLM 节点同口径）
# =============================================================================


class TestParamsAssemblySegment:
    def test_params_none_flattens_all_outputs(self, monkeypatch):
        """inputs=None → 隐式全量平铺（快照全量注入 params）。"""
        spy = _RunnerSpy(_success_outcome())
        _patch_runner(monkeypatch, spy)
        context = _make_context(_make_config(), outputs={"a": 1, "b": "x"})
        result = run_coro(_executor(_settings_open()).execute(context))
        assert result.success is True
        assert spy.calls[0][1] == {"a": 1, "b": "x"}

    def test_params_empty_list_explicit_empty(self, monkeypatch):
        """inputs=[] → 显式空清单：params == {}。"""
        spy = _RunnerSpy(_success_outcome())
        _patch_runner(monkeypatch, spy)
        context = _make_context(_make_config(inputs=[]), outputs={"a": 1})
        result = run_coro(_executor(_settings_open()).execute(context))
        assert result.success is True
        assert spy.calls[0][1] == {}

    def test_params_selected_with_alias(self, monkeypatch):
        """非空选择集 → 键 = alias 优先、缺省为被引用命名输出名。"""
        spy = _RunnerSpy(_success_outcome())
        _patch_runner(monkeypatch, spy)
        config = _make_config(inputs=[AgentInputEntry(reference="a", alias="首项")])
        context = _make_context(config, outputs={"a": 1, "b": 2})
        result = run_coro(_executor(_settings_open()).execute(context))
        assert result.success is True
        assert spy.calls[0][1] == {"首项": 1}

    def test_params_missing_reference_fails_before_execution(self, monkeypatch):
        """悬空引用 → NODE_INPUT_REFERENCE_MISSING 透传 + 不执行（fail-fast）。"""
        spy = _RunnerSpy(_success_outcome())
        _patch_runner(monkeypatch, spy)
        config = _make_config(inputs=[AgentInputEntry(reference="missing_out")])
        result = run_coro(_executor(_settings_open()).execute(_make_context(config)))
        assert result.success is False
        assert result.error_code == NODE_INPUT_REFERENCE_MISSING
        assert "missing_out" in result.error_message
        assert "code-1" in result.error_message
        assert spy.calls == []

    def test_params_type_mismatch_fails(self, monkeypatch):
        """字段路径取值失败 → NODE_INPUT_TYPE_MISMATCH 透传。"""
        spy = _RunnerSpy(_success_outcome())
        _patch_runner(monkeypatch, spy)
        config = _make_config(inputs=[AgentInputEntry(reference="a.sub")])
        context = _make_context(config, outputs={"a": 1})
        result = run_coro(_executor(_settings_open()).execute(context))
        assert result.success is False
        assert result.error_code == NODE_INPUT_TYPE_MISMATCH
        assert spy.calls == []


# =============================================================================
# ④ 段三：沙箱策略解析与护栏取小（DC-10；策略不存在不静默降级）
# =============================================================================


class TestPolicySegment:
    def test_no_policy_uses_settings_defaults(self, monkeypatch):
        """未引用策略 → settings 默认护栏（timeout=10s / max_bytes=1MiB）。"""
        spy = _RunnerSpy(_success_outcome())
        _patch_runner(monkeypatch, spy)
        result = run_coro(
            _executor(_settings_open(), policy_repository=_RepoSpy()).execute(
                _make_context(_make_config())
            )
        )
        assert result.success is True
        assert spy.calls[0][2] == 10
        assert spy.calls[0][3] == 1048576

    def test_policy_narrows_guardrails_to_min(self, monkeypatch):
        """策略更严 → 超时/输出上限取较小值（FR-26-a）。"""
        spy = _RunnerSpy(_success_outcome())
        _patch_runner(monkeypatch, spy)
        repo = _RepoSpy(
            {"pol-1": _make_policy(execution_timeout_seconds=5, output_bytes_limit=1024)}
        )
        config = _make_config(sandbox_policy_id="pol-1")
        result = run_coro(
            _executor(_settings_open(), policy_repository=repo).execute(
                _make_context(config)
            )
        )
        assert result.success is True
        assert spy.calls[0][2] == 5
        assert spy.calls[0][3] == 1024

    def test_policy_wider_than_settings_keeps_settings(self, monkeypatch):
        """策略更宽 → 保守取 settings 值（不放宽平台默认护栏）。"""
        spy = _RunnerSpy(_success_outcome())
        _patch_runner(monkeypatch, spy)
        repo = _RepoSpy(
            {"pol-1": _make_policy(execution_timeout_seconds=30, output_bytes_limit=2097152)}
        )
        config = _make_config(sandbox_policy_id="pol-1")
        result = run_coro(
            _executor(_settings_open(), policy_repository=repo).execute(
                _make_context(config)
            )
        )
        assert result.success is True
        assert spy.calls[0][2] == 10
        assert spy.calls[0][3] == 1048576

    def test_missing_policy_fails_closed(self, monkeypatch):
        """引用不存在的策略 ID → CODE_RUNTIME_ERROR（不静默降级）。"""
        spy = _RunnerSpy(_success_outcome())
        _patch_runner(monkeypatch, spy)
        config = _make_config(sandbox_policy_id="pol-x")
        result = run_coro(
            _executor(_settings_open(), policy_repository=_RepoSpy()).execute(
                _make_context(config)
            )
        )
        assert result.success is False
        assert result.error_code == CODE_RUNTIME_ERROR
        assert "策略不存在" in result.error_message
        assert "pol-x" in result.error_message
        assert spy.calls == []

    def test_policy_declared_without_repository_fails_closed(self, monkeypatch):
        """声明策略但仓储未注入 → 同样 fail-closed（不静默降级）。"""
        spy = _RunnerSpy(_success_outcome())
        _patch_runner(monkeypatch, spy)
        config = _make_config(sandbox_policy_id="pol-9")
        result = run_coro(_executor(_settings_open()).execute(_make_context(config)))
        assert result.success is False
        assert result.error_code == CODE_RUNTIME_ERROR
        assert "策略不存在" in result.error_message
        assert spy.calls == []

    def test_policy_lookup_uses_tenant_id(self, monkeypatch):
        """策略查询携带执行器租户标识（多租户隔离）。"""
        spy = _RunnerSpy(_success_outcome())
        _patch_runner(monkeypatch, spy)
        repo = _RepoSpy({"pol-1": _make_policy()})
        config = _make_config(sandbox_policy_id="pol-1")
        result = run_coro(
            _executor(_settings_open(), policy_repository=repo, tenant_id="tenant-77").execute(
                _make_context(config)
            )
        )
        assert result.success is True
        assert repo.get_calls == [("pol-1", "tenant-77")]

    def test_no_policy_zero_repository_access(self, monkeypatch):
        """未引用策略 → 仓储零访问（未引用路径零开销）。"""
        spy = _RunnerSpy(_success_outcome())
        _patch_runner(monkeypatch, spy)
        repo = _RepoSpy()
        result = run_coro(
            _executor(_settings_open(), policy_repository=repo).execute(
                _make_context(_make_config())
            )
        )
        assert result.success is True
        assert repo.get_calls == []


# =============================================================================
# ⑤ 段四：通道分派（非法值降级 + A2 异常 fail-closed）
# =============================================================================


class TestChannelSegment:
    def test_default_channel_inprocess(self, monkeypatch):
        """默认通道 inprocess → A1 同步执行、A2 零调用。"""
        runner_spy = _RunnerSpy(_success_outcome())
        sub_spy = _AsyncRunnerSpy(_success_outcome())
        _patch_runner(monkeypatch, runner_spy)
        monkeypatch.setattr(code_node_executor_module, "run_script_subprocess", sub_spy)
        result = run_coro(_executor(_settings_open()).execute(_make_context(_make_config())))
        assert result.success is True
        assert result.metadata["channel"] == "inprocess"
        assert len(runner_spy.calls) == 1
        assert sub_spy.calls == []

    def test_invalid_channel_degrades_to_inprocess(self, monkeypatch, caplog):
        """非法通道值 → 告警日志 + 降级 inprocess。"""
        runner_spy = _RunnerSpy(_success_outcome())
        _patch_runner(monkeypatch, runner_spy)
        executor = _executor(_settings_open(code_execution_channel="weird"))
        with caplog.at_level(logging.WARNING):
            result = run_coro(executor.execute(_make_context(_make_config())))
        assert result.success is True
        assert result.metadata["channel"] == "inprocess"
        assert len(runner_spy.calls) == 1
        assert any(
            "降级 inprocess" in record.getMessage() for record in caplog.records
        )

    def test_subprocess_channel_dispatch(self, monkeypatch):
        """channel=subprocess → A2 异步执行、A1 零调用。"""
        runner_spy = _RunnerSpy(_success_outcome())
        sub_spy = _AsyncRunnerSpy(_success_outcome())
        _patch_runner(monkeypatch, runner_spy)
        monkeypatch.setattr(code_node_executor_module, "run_script_subprocess", sub_spy)
        executor = _executor(_settings_open(code_execution_channel="subprocess"))
        result = run_coro(executor.execute(_make_context(_make_config())))
        assert result.success is True
        assert result.metadata["channel"] == "subprocess"
        assert len(sub_spy.calls) == 1
        assert runner_spy.calls == []

    def test_subprocess_failure_dispatches_fail_closed(self, monkeypatch):
        """A2 通道自身异常 → CODE_RUNTIME_ERROR（fail-closed，DC-16）。"""
        raising = _AsyncRaisingRunner(OSError("boom"))
        monkeypatch.setattr(code_node_executor_module, "run_script_subprocess", raising)
        executor = _executor(_settings_open(code_execution_channel="subprocess"))
        result = run_coro(executor.execute(_make_context(_make_config())))
        assert result.success is False
        assert result.error_code == CODE_RUNTIME_ERROR
        assert "OSError: boom" in result.error_message
        assert result.output_value is None


# =============================================================================
# ⑥ 段五：真实 A1 执行与输出（超时以 monkeypatched 失败结果驱动）
# =============================================================================


class TestExecutionSegment:
    """真实 A1 直跑（不 monkeypatch）；超时映射断言失败结果驱动的分支。"""

    def test_success_result_output_shape(self):
        """纯计算脚本成功 → {"result": 值} + 命名输出（FR-16）。"""
        config = _make_config(code='result = {"sum": 1 + 2}')
        result = run_coro(_executor(_settings_open()).execute(_make_context(config)))
        assert result.success is True
        assert result.output_value == {"result": {"sum": 3}}
        assert result.output_name == "code_result"
        assert result.error_code is None
        assert result.error_message is None

    def test_runtime_error_contains_exception_type(self):
        """运行期异常 → CODE_RUNTIME_ERROR，消息含异常类型名（DC-16-7）。"""
        result = run_coro(
            _executor(_settings_open()).execute(
                _make_context(_make_config(code="result = 1 / 0"))
            )
        )
        assert result.success is False
        assert result.error_code == CODE_RUNTIME_ERROR
        assert "ZeroDivisionError" in result.error_message
        assert result.output_value is None

    def test_result_missing(self):
        """result 未赋值 → CODE_OUTPUT_MISSING（显式置 None 与未定义统一）。"""
        result = run_coro(
            _executor(_settings_open()).execute(_make_context(_make_config(code="x = 1")))
        )
        assert result.success is False
        assert result.error_code == CODE_OUTPUT_MISSING
        assert "result" in result.error_message

    def test_unserializable_output(self):
        """result 不可序列化（函数）→ CODE_RUNTIME_ERROR 含「不可序列化」。"""
        result = run_coro(
            _executor(_settings_open()).execute(
                _make_context(_make_config(code="result = lambda: 1"))
            )
        )
        assert result.success is False
        assert result.error_code == CODE_RUNTIME_ERROR
        assert "不可序列化" in result.error_message

    def test_output_too_large_real_guard(self):
        """输出超限（64 字节上限真实护栏）→ CODE_OUTPUT_TOO_LARGE 附字节数。"""
        executor = _executor(_settings_open(code_output_max_bytes=64))
        result = run_coro(
            executor.execute(_make_context(_make_config(code='result = "x" * 200')))
        )
        assert result.success is False
        assert result.error_code == CODE_OUTPUT_TOO_LARGE
        assert "上限 64 字节，实际 202 字节" in result.error_message

    def test_timeout_maps_code_timeout_metadata(self, monkeypatch):
        """超时失败结果 → CODE_TIMEOUT + abandoned_thread + duration（DC-16-6）。"""
        _patch_runner(
            monkeypatch,
            _RunnerSpy(
                _failure_outcome(CODE_TIMEOUT, _TIMEOUT_MESSAGE, 10.012, abandoned_thread=True)
            ),
        )
        result = run_coro(_executor(_settings_open()).execute(_make_context(_make_config())))
        assert result.success is False
        assert result.error_code == CODE_TIMEOUT
        assert "已放弃工作线程" in result.error_message
        assert result.metadata["abandoned_thread"] is True
        assert result.metadata["duration"] == 10.012

    def test_same_cause_same_code_three_runs(self):
        """同因同码（FR-33）：同一失败脚本三次执行错误码与消息一致。"""
        executor = _executor(_settings_open())
        config = _make_config(code="result = 1 / 0")
        results = [run_coro(executor.execute(_make_context(config))) for _ in range(3)]
        assert {r.error_code for r in results} == {CODE_RUNTIME_ERROR}
        assert len({r.error_message for r in results}) == 1

    def test_duration_and_channel_recorded_on_success(self):
        """成功 metadata 含 channel 与 duration（AC-31 登记口径）。"""
        result = run_coro(
            _executor(_settings_open()).execute(_make_context(_make_config()))
        )
        assert result.success is True
        assert result.metadata["channel"] == "inprocess"
        assert isinstance(result.metadata["duration"], float)
        assert result.metadata["duration"] >= 0


# =============================================================================
# ⑦ metadata 契约与展示语义字段（entry_function/dependencies 零消费）
# =============================================================================


class TestMetadataAndContract:
    def test_success_metadata_exactly_six_keys(self):
        """成功 metadata 恰六键；指纹为 SHA-256 前 16 位；static_check=pre_exec。"""
        code = 'result = {"sum": 1 + 2}'
        result = run_coro(
            _executor(_settings_open()).execute(_make_context(_make_config(code=code)))
        )
        assert set(result.metadata.keys()) == {
            "language",
            "script_length",
            "script_fingerprint",
            "static_check",
            "channel",
            "duration",
        }
        assert result.metadata["static_check"] == "pre_exec"
        assert result.metadata["script_fingerprint"] == hashlib.sha256(
            code.encode("utf-8")
        ).hexdigest()[:16]

    def test_early_failure_metadata_four_keys_only(self):
        """早期失败（段一）metadata 仅四基键，无 channel/duration。"""
        config = _make_config(code="import os\nresult = 1")
        result = run_coro(_executor(_settings_open()).execute(_make_context(config)))
        assert result.error_code == CODE_BANNED_CONSTRUCT
        assert set(result.metadata.keys()) == {
            "language",
            "script_length",
            "script_fingerprint",
            "static_check",
        }

    def test_entry_function_zero_read(self):
        """entry_function 指向不存在函数 → 执行不受影响（FR-18 展示语义）。"""
        config = _make_config(code="result = 42", entry_function="no_such_function")
        result = run_coro(_executor(_settings_open()).execute(_make_context(config)))
        assert result.success is True
        assert result.output_value == {"result": 42}

    def test_dependencies_registered_not_imported(self):
        """dependencies 登记不存在的包 → 零消费、正常执行（FR-19）。"""
        config = _make_config(code="result = 42", dependencies=["nonexistent_pkg_xyz"])
        result = run_coro(_executor(_settings_open()).execute(_make_context(config)))
        assert result.success is True
        assert result.output_value == {"result": 42}

    def test_output_name_falls_back_to_node_id(self):
        """未声明 output_name → 有效输出名回退 node_id。"""
        context = _make_context(
            _make_config(code="result = 1"), node_id="code-9", output_name=None
        )
        result = run_coro(_executor(_settings_open()).execute(context))
        assert result.success is True
        assert result.output_name == "code-9"

    def test_script_length_counts_utf8_bytes(self):
        """script_length 按 UTF-8 字节数（非字符数，中文场景防退化）。"""
        code = '打印("你好")'  # 语法可解析；运行期 NameError 无碍 metadata 断言
        result = run_coro(
            _executor(_settings_open()).execute(_make_context(_make_config(code=code)))
        )
        assert result.metadata["script_length"] == len(code.encode("utf-8"))
        assert result.metadata["script_length"] != len(code)


# =============================================================================
# ⑧ 违规发布（DC-10-3：仅超时与输出超限；记录不含脚本全文）
# =============================================================================


class TestViolationPublishing:
    def test_timeout_publishes_violation_without_script_text(self, monkeypatch):
        """超时 → TIMEOUT 违规发布；记录含节点与时长摘要、无脚本全文。"""
        publisher = _PublisherSpy()
        executor = _executor(_settings_open(), violation_publisher=publisher)
        _patch_runner(
            monkeypatch,
            _RunnerSpy(
                _failure_outcome(CODE_TIMEOUT, _TIMEOUT_MESSAGE, 10.012, abandoned_thread=True)
            ),
        )
        config = _make_config(code=f"value = '{_SCRIPT_MARKER}'\nresult = 1")
        result = run_coro(executor.execute(_make_context(config)))
        assert result.error_code == CODE_TIMEOUT
        assert len(publisher.calls) == 1
        sandbox_result, session_id, tenant_id, policy_id = publisher.calls[0]
        assert session_id == "inst-1"
        assert tenant_id == "tenant-1"
        assert policy_id == ""
        assert sandbox_result.timed_out is True
        assert sandbox_result.error_code == CODE_TIMEOUT
        assert len(sandbox_result.violations) == 1
        violation = sandbox_result.violations[0]
        assert violation.violation_type == ViolationType.TIMEOUT
        assert "code-1" in violation.detail
        assert _SCRIPT_MARKER not in violation.detail

    def test_output_too_large_publishes_resource_exceeded(self, monkeypatch):
        """输出超限 → RESOURCE_EXCEEDED 违规发布（timed_out=False）。"""
        publisher = _PublisherSpy()
        executor = _executor(_settings_open(), violation_publisher=publisher)
        failure_message = "代码节点输出超尺寸上限：上限 64 字节，实际 202 字节"
        _patch_runner(
            monkeypatch,
            _RunnerSpy(_failure_outcome(CODE_OUTPUT_TOO_LARGE, failure_message, 0.02)),
        )
        result = run_coro(executor.execute(_make_context(_make_config())))
        assert result.error_code == CODE_OUTPUT_TOO_LARGE
        sandbox_result, _, _, _ = publisher.calls[0]
        assert sandbox_result.timed_out is False
        violation = sandbox_result.violations[0]
        assert violation.violation_type == ViolationType.RESOURCE_EXCEEDED

    def test_runtime_error_not_published(self, monkeypatch):
        """运行期异常 → 不发布违规（仅超时与输出超限两类）。"""
        publisher = _PublisherSpy()
        executor = _executor(_settings_open(), violation_publisher=publisher)
        _patch_runner(
            monkeypatch,
            _RunnerSpy(_failure_outcome(CODE_RUNTIME_ERROR, "ValueError: boom", 0.01)),
        )
        result = run_coro(executor.execute(_make_context(_make_config())))
        assert result.error_code == CODE_RUNTIME_ERROR
        assert publisher.calls == []

    def test_publisher_absent_skips_publishing(self, monkeypatch):
        """发布器未注入 → 跳过发布，失败结果正常返回（零开销路径）。"""
        executor = _executor(_settings_open())
        _patch_runner(
            monkeypatch,
            _RunnerSpy(
                _failure_outcome(CODE_TIMEOUT, _TIMEOUT_MESSAGE, 10.012, abandoned_thread=True)
            ),
        )
        result = run_coro(executor.execute(_make_context(_make_config())))
        assert result.error_code == CODE_TIMEOUT
        assert result.metadata["abandoned_thread"] is True

    def test_publish_exception_only_warning(self, monkeypatch):
        """发布通道自身异常 → 仅告警，不改变节点失败结果。"""
        publisher = _PublisherSpy(raise_on_publish=True)
        executor = _executor(_settings_open(), violation_publisher=publisher)
        _patch_runner(
            monkeypatch,
            _RunnerSpy(
                _failure_outcome(CODE_TIMEOUT, _TIMEOUT_MESSAGE, 10.012, abandoned_thread=True)
            ),
        )
        result = run_coro(executor.execute(_make_context(_make_config())))
        assert result.success is False
        assert result.error_code == CODE_TIMEOUT
        assert len(publisher.calls) == 1

    def test_publish_payload_shape(self, monkeypatch):
        """声明策略场景发布载荷：policy_id 回传 + 时长摘要落账。"""
        repo = _RepoSpy({"pol-1": _make_policy()})
        publisher = _PublisherSpy()
        executor = _executor(
            _settings_open(), policy_repository=repo, violation_publisher=publisher
        )
        _patch_runner(
            monkeypatch,
            _RunnerSpy(
                _failure_outcome(CODE_TIMEOUT, _TIMEOUT_MESSAGE, 10.012, abandoned_thread=True)
            ),
        )
        config = _make_config(sandbox_policy_id="pol-1")
        result = run_coro(executor.execute(_make_context(config)))
        assert result.error_code == CODE_TIMEOUT
        sandbox_result, session_id, tenant_id, policy_id = publisher.calls[0]
        assert policy_id == "pol-1"
        assert sandbox_result.success is False
        assert sandbox_result.execution_duration_seconds == 10.012
        assert sandbox_result.violations[0].detail.startswith("代码节点 code-1：")


# =============================================================================
# ⑨ 退役守卫（DC-11：退役码零产生 + 五段顺序源码级证明）
# =============================================================================


class TestRetiredCodeGate:
    def test_code_script_not_executable_not_produced(self):
        """CODE_SCRIPT_NOT_EXECUTABLE 本执行器零产生（定义保留在 errors.py）。"""
        source = _EXECUTOR_SOURCE.read_text(encoding="utf-8")
        assert CODE_SCRIPT_NOT_EXECUTABLE not in source

    def test_five_segment_order_in_source(self):
        """五段管线顺序源码级证明：段一 < 段二 < 段三 < 段四 < 段五，且开关在段一前。"""
        source = _EXECUTOR_SOURCE.read_text(encoding="utf-8")
        markers = ("段一：", "段二：", "段三：", "段四：", "段五：")
        positions = [source.index(marker) for marker in markers]
        assert positions == sorted(positions)
        assert source.index("code_node_enabled") < positions[0]
