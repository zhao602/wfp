# =============================================================================
# LLM 超时故障注入套件（tests/unit/test_llm_timeout_injection.py）
#
# 职责（roadmap B14 T4「LLM 超时」，design D4-LLM）：经真实 HttpLlmClient
# 注入 httpx 超时（patch httpx.AsyncClient + AsyncMock(side_effect=
# httpx.TimeoutException)），固定三层封装契约（既有 test_llm_client_retry.py
# R1-R8/P1-P3 为客户端级 mock 传输断言，本套件差异化为真实客户端 + 执行器/
# 调度器级注入；既有文件零修改）：
#   A. 客户端级：超时封装为 InferenceResult(success=False) 文档化文案，
#      无未封装异常外逸；configure_retry(enabled=True, sleep=注入记录器)
#      退避序列 base*2^n 断言后仍失败路径一致；
#   B. 执行器级：LlmNodeExecutor 消费失败结果 → NodeExecutionResult
#      (LLM_INFERENCE_FAILED，消息含客户端文档化文案)；
#   C. 调度器级：节点失败 → on_node_failed 事件回调（事件导出缝）+
#      NodeExecutionError 上抛（实例状态迁移信号——run_and_record 消费后
#      置实例 failed 并导出 INSTANCE_FAILED 事件，其行为由
#      test_execution_runner 系列守门），下游节点零执行。
# 注入纪律：回调仅实现旧形态 on_node_failed（B13 在途调度器对
# on_node_failed_with_input 做 getattr 优先探测，缺席即回落旧路径，
# 本套件在净树与在途树均绿）。
# =============================================================================

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from orchestration_platform.domain.errors import (
    LLM_INFERENCE_FAILED,
    NodeExecutionError,
)
from orchestration_platform.domain.models.enums import NodeType
from orchestration_platform.domain.models.node_configurations import (
    LlmNodeConfiguration,
)
from orchestration_platform.domain.models.node_definition import NodeDefinition
from orchestration_platform.domain.models.shared_state import SharedState
from orchestration_platform.engine.graph_compiler import GraphCompiler
from orchestration_platform.engine.node_executor_registry import (
    NodeExecutorRegistry,
)
from orchestration_platform.engine.nodes.base import NodeExecutionContext
from orchestration_platform.engine.nodes.llm_node_executor import LlmNodeExecutor
from orchestration_platform.engine.nodes.output_display_node_executor import (
    OutputDisplayNodeExecutor,
)
from orchestration_platform.engine.nodes.trigger_node_executor import (
    TriggerNodeExecutor,
)
from orchestration_platform.engine.scheduler import Scheduler
from orchestration_platform.integrations.abstractions.external_clients import (
    InferenceRequest,
)
from orchestration_platform.integrations.llm.http_llm_client import HttpLlmClient
from orchestration_platform.workflow.yaml_workflow_parser import YamlWorkflowParser


def run_coro(coro):
    """手动驱动协程至完成（await 链为 AsyncMock/纯同步逻辑，无真实挂起点）。"""
    try:
        while True:
            coro.send(None)
    except StopIteration as exc:
        return exc.value


class _SleepRecorder:
    """假 sleep：记录每次退避时长（注入记录器，零真实等待）。"""

    def __init__(self):
        self.delays = []

    async def __call__(self, delay: float):
        self.delays.append(delay)


def _timeout_http_client(timeout_seconds: float) -> MagicMock:
    """mock httpx.AsyncClient：post 恒抛 TimeoutException（超时注入）。"""
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("inject"))
    return mock_client


def _make_request() -> InferenceRequest:
    return InferenceRequest(
        model_identifier="qwen-max",
        system_prompt="你是测试助手。",
        user_message="请总结输入素材。",
    )


# =============================================================================
# A. 客户端级：真实 HttpLlmClient 超时封装契约
# =============================================================================


class TestClientLevelTimeoutInjection:
    @pytest.mark.asyncio
    async def test_timeout_wrapped_as_failure_result_no_escape(self):
        """超时封装为失败结果（文档化文案），无未封装异常外逸；重试默认
        关闭单次尝试。"""
        mock_http = _timeout_http_client(timeout_seconds=12.0)
        with patch("httpx.AsyncClient", return_value=mock_http):
            client = HttpLlmClient(
                "http://llm.invalid", "sk-inject", "qwen-max", timeout_seconds=12.0
            )
            result = await client.infer(_make_request())

        assert result.success is False
        assert result.error_message == "LLM 请求超时（上限 12.0s）"
        assert mock_http.post.await_count == 1

    @pytest.mark.asyncio
    async def test_retry_backoff_sequence_then_failure_path_consistent(self):
        """configure_retry(enabled=True, sleep=注入记录器)：退避序列
        base*2^n（0.5, 1.0）断言；重试耗尽后失败路径一致（同一文档化文案）。"""
        recorder = _SleepRecorder()
        mock_http = _timeout_http_client(timeout_seconds=8.0)
        with patch("httpx.AsyncClient", return_value=mock_http):
            client = HttpLlmClient(
                "http://llm.invalid", "sk-inject", "qwen-max", timeout_seconds=8.0
            )
            client.configure_retry(
                enabled=True,
                max_attempts=3,
                backoff_base_seconds=0.5,
                sleep=recorder,
                jitter=lambda: 0.0,
            )
            result = await client.infer(_make_request())

        assert result.success is False
        assert result.error_message == "LLM 请求超时（上限 8.0s）"
        assert mock_http.post.await_count == 3
        assert recorder.delays == [0.5, 1.0], "指数退避 base*2^0, base*2^1"


# =============================================================================
# B. 执行器级：节点失败结果含文档化文案
# =============================================================================


class TestNodeLevelTimeoutInjection:
    @pytest.mark.asyncio
    async def test_node_failure_carries_documented_message(self):
        """执行器消费超时失败结果 → LLM_INFERENCE_FAILED，消息含客户端
        文档化文案（全链封装：httpx 超时 → InferenceResult → 节点结果）。"""
        mock_http = _timeout_http_client(timeout_seconds=30.0)
        with patch("httpx.AsyncClient", return_value=mock_http):
            real_client = HttpLlmClient(
                "http://llm.invalid", "sk-inject", "qwen-max", timeout_seconds=30.0
            )
            executor = LlmNodeExecutor(llm_client=real_client)
            executor._settings = SimpleNamespace(llm_node_enabled=True)

            node_def = NodeDefinition(
                node_id="llm-1",
                name="llm node",
                node_type=NodeType.LLM,
                configuration=LlmNodeConfiguration(
                    model_identifier="qwen-max",
                    system_prompt="你是测试助手。",
                    user_prompt="请总结输入素材。",
                ),
                output_name="llm_result",
            )
            context = NodeExecutionContext(
                node_definition=node_def,
                shared_state=SharedState(instance_id="inst-1"),
                instance_id="inst-1",
                trace_id="trace-1",
                timeout_seconds=30,
            )
            result = await executor.execute(context)

        assert result.success is False
        assert result.error_code == LLM_INFERENCE_FAILED
        assert result.error_message == (
            "大模型节点 llm-1 推理失败：LLM 请求超时（上限 30.0s）"
        )


# =============================================================================
# C. 调度器级：实例失败信号 + 事件导出 + 下游零执行
# =============================================================================


_TIMEOUT_YAML = """\
spec_version: "1.0"
definition_id: b14_llm_timeout_injection
name: LLM超时注入场景
definition_version: 1
inputs: []
outputs: []
nodes:
  - node_id: trigger_start
    name: 手动触发
    node_type: trigger
    configuration:
      trigger_type: manual
  - node_id: llm_generate
    name: 生成摘要
    node_type: llm
    output_name: summary
    configuration:
      model_identifier: qwen-max
      system_prompt: 你是测试助手。
      user_prompt: 请总结输入素材。
  - node_id: display_result
    name: 展示结果
    node_type: output_display
    configuration:
      input_reference: summary.content
      display_title: 最终结果
edges:
  - edge_id: e1
    source_node_id: trigger_start
    target_node_id: llm_generate
  - edge_id: e2
    source_node_id: llm_generate
    target_node_id: display_result
"""


class _CapturingCallback:
    """节点事件捕获器（事件导出缝；仅旧形态 on_node_failed）。"""

    def __init__(self):
        self.started = []
        self.completed = []
        self.failed = []

    async def on_node_started(self, instance_id, node_id, trace_id):
        self.started.append(node_id)

    async def on_node_completed(self, instance_id, node_id, trace_id, payload=None):
        self.completed.append((node_id, payload))

    async def on_node_failed(self, instance_id, node_id, trace_id, error):
        self.failed.append((node_id, error))


class TestSchedulerLevelTimeoutInjection:
    def test_node_timeout_fails_instance_and_exports_events(self):
        """调度器级注入：LLM 节点超时 → on_node_failed 事件回调携带文档化
        文案（事件导出）+ NodeExecutionError 上抛（实例状态迁移信号，
        run_and_record 消费后置实例 failed）+ 下游节点零执行。"""
        mock_http = _timeout_http_client(timeout_seconds=45.0)
        with patch("httpx.AsyncClient", return_value=mock_http):
            real_client = HttpLlmClient(
                "http://llm.invalid", "sk-inject", "qwen-max", timeout_seconds=45.0
            )
            registry = NodeExecutorRegistry()
            registry.register(TriggerNodeExecutor())
            llm_executor = LlmNodeExecutor(llm_client=real_client)
            llm_executor._settings = SimpleNamespace(llm_node_enabled=True)
            registry.register(llm_executor)
            registry.register(OutputDisplayNodeExecutor())

            definition = YamlWorkflowParser().parse(_TIMEOUT_YAML)
            compiled = run_coro(GraphCompiler().compile(definition))
            callback = _CapturingCallback()
            scheduler = Scheduler(registry, event_callback=callback)
            initial_state = SharedState(
                instance_id="inst-inject",
                current_node_id=compiled.entry_node_id,
            )

            with pytest.raises(NodeExecutionError) as excinfo:
                run_coro(
                    scheduler.execute(
                        compiled, initial_state, "inst-inject", "trace-inject"
                    )
                )

        # 事件导出：失败回调携带执行器文档化文案
        assert callback.failed == [
            ("llm_generate", "大模型节点 llm_generate 推理失败：LLM 请求超时（上限 45.0s）")
        ]
        # 实例状态迁移信号：NodeExecutionError（非未封装 httpx 异常）上抛
        assert "节点 llm_generate 执行失败" in str(excinfo.value)
        assert "LLM 请求超时" in str(excinfo.value)
        # 下游节点零执行：display 未启动未完成
        assert "display_result" not in callback.started
        assert all(node_id != "display_result" for node_id, _ in callback.completed)
