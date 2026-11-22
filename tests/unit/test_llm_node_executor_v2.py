# =============================================================================
# LlmNodeExecutor 薄链路回归测试（llm_code_nodes_v2 任务 5.4：T3/T4）
# 执行命令：PYTHONPATH=src python -m pytest tests/unit/test_llm_node_executor_v2.py -v
#
# 覆盖（DC-05 薄链路「装配 → 请求 → 透出」）：
#   T3 开关：显式关闭 → LLM_NODE_DISABLED + mock 客户端零调用；settings
#      字段缺失 → 视同关闭（getattr 防御，FR-03-b）；
#   T4 薄链路：mock 文本 T → <output_name>.content == T 无二次加工；
#      output_schema 登记不生效（请求不携带 / 零校验 / 零重试 / 零
#      LLM_OUTPUT_INVALID，AC-13 / DC-09）；inputs 三态装配与 agent 一致；
#      悬空引用 → NODE_INPUT_REFERENCE_MISSING 含表达式与 node_id；采样
#      显式值入请求、未配置不注默认；400 含参数名 → LLM_PARAM_UNSUPPORTED；
#      连接异常 → LLM_INFERENCE_FAILED 脱敏无凭据；三类失败（装配/推理/
#      开关）共享状态均无该节点输出（AC-14）。
# N 级登记：真实模型调用不可验证（沙箱无网络，全部基于 mock 客户端）。
# =============================================================================

# 沙箱限制：本机 asyncio 事件循环不可用（socketpair 被网络策略拦截，
# WinError 10051 / _ssock AttributeError），run_coro 采用手动驱动协程
# 方式（send(None) 循环），不创建事件循环，故无需引入 asyncio 模块。

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from orchestration_platform.domain.errors import (
    LLM_INFERENCE_FAILED,
    LLM_NODE_DISABLED,
    LLM_PARAM_UNSUPPORTED,
    NODE_INPUT_REFERENCE_MISSING,
)
from orchestration_platform.domain.models.enums import NodeType
from orchestration_platform.domain.models.node_configurations import (
    AgentInputEntry,
    LlmNodeConfiguration,
)
from orchestration_platform.domain.models.node_definition import NodeDefinition
from orchestration_platform.domain.models.shared_state import SharedState
from orchestration_platform.engine.nodes.base import NodeExecutionContext
from orchestration_platform.engine.nodes.llm_node_executor import LlmNodeExecutor
from orchestration_platform.integrations.abstractions.external_clients import (
    InferenceRequest,
    InferenceResult,
)


def run_coro(coro):
    """手动驱动协程至完成（沙箱事件循环不可用时的 fallback）。

    execute 的 await 链（llm_client.infer）为 AsyncMock，无真实挂起点，
    故以 send(None) 循环驱动至 StopIteration，环境无关。
    """
    try:
        while True:
            coro.send(None)
    except StopIteration as exc:
        return exc.value


# =============================================================================
# 测试辅助
# =============================================================================

# 显式关闭态（开关字段存在且为 False）
_SETTINGS_OFF_EXPLICIT = SimpleNamespace(llm_node_enabled=False)
# 字段缺失态（getattr 防御场景：缺字段视同关闭）
_SETTINGS_FIELD_ABSENT = SimpleNamespace()
# 开启态
_SETTINGS_OPEN = SimpleNamespace(llm_node_enabled=True)

# metadata / 请求中不允许出现的凭据类标记（小写子串匹配）
_SECRET_MARKERS = ("api_key", "apikey", "secret", "bearer", "authorization", "sk-")


def _make_config(**kwargs) -> LlmNodeConfiguration:
    """构造大模型节点配置。"""
    defaults = {"model_identifier": "primary-model"}
    defaults.update(kwargs)
    return LlmNodeConfiguration(**defaults)


def _make_context(
    config: LlmNodeConfiguration,
    outputs: dict = None,
    node_id: str = "llm-1",
    output_name: str = "llm_result",
) -> NodeExecutionContext:
    """构造执行上下文（可带命名输出表）。"""
    node_def = NodeDefinition(
        node_id=node_id,
        name="llm node",
        node_type=NodeType.LLM,
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


def _client(results) -> MagicMock:
    """构造 mock ILlmClient：infer 依序返回 results（耗尽再调即报错，防意外多轮）。"""
    client = MagicMock()
    client.infer = AsyncMock(side_effect=list(results))
    return client


def _executor(client, settings) -> LlmNodeExecutor:
    """构造执行器并覆盖 settings（真实 Settings 已声明新字段，测试直接注入命名空间）。"""
    executor = LlmNodeExecutor(llm_client=client)
    executor._settings = settings
    return executor


def _success(content="ok", structured=None, switched=False) -> InferenceResult:
    return InferenceResult(
        success=True,
        content=content,
        structured_output=structured,
        model_switched=switched,
    )


def _failure(message="boom", status=None, detail=None) -> InferenceResult:
    return InferenceResult(
        success=False,
        error_message=message,
        error_status_code=status,
        error_detail=detail,
    )


# =============================================================================
# T3 能力开关（DC-15：始终注册 + 内部判开关；FR-03-b 字段缺失视同关闭）
# =============================================================================


class TestCapabilitySwitchV2:
    def test_disabled_returns_llm_node_disabled(self):
        """显式关闭 → LLM_NODE_DISABLED，消息含 node_id 与开关名。"""
        executor = _executor(_client([]), _SETTINGS_OFF_EXPLICIT)
        result = run_coro(executor.execute(_make_context(_make_config())))
        assert result.success is False
        assert result.error_code == LLM_NODE_DISABLED
        assert "llm-1" in result.error_message
        assert "llm_node_enabled" in result.error_message

    def test_disabled_client_zero_calls(self):
        """关闭 → mock 客户端零调用（不装配、不推理）。"""
        client = _client([])
        executor = _executor(client, _SETTINGS_OFF_EXPLICIT)
        run_coro(executor.execute(_make_context(_make_config())))
        assert client.infer.call_count == 0

    def test_settings_field_absent_treated_as_closed(self):
        """settings 缺 llm_node_enabled 字段 → 视同关闭（getattr 防御场景）。"""
        executor = _executor(_client([]), _SETTINGS_FIELD_ABSENT)
        result = run_coro(executor.execute(_make_context(_make_config())))
        assert result.success is False
        assert result.error_code == LLM_NODE_DISABLED

    def test_disabled_output_value_none(self):
        """关闭 → output_value/output_name 均为 None（INV-05 失败不落输出）。"""
        executor = _executor(_client([]), _SETTINGS_OFF_EXPLICIT)
        result = run_coro(executor.execute(_make_context(_make_config())))
        assert result.output_value is None
        assert result.output_name is None

    def test_enabled_executes_inference(self):
        """开启 → 推理执行且客户端恰被调用一次（开关放行面）。"""
        client = _client([_success("ok")])
        executor = _executor(client, _SETTINGS_OPEN)
        result = run_coro(executor.execute(_make_context(_make_config())))
        assert result.success is True
        assert client.infer.call_count == 1


# =============================================================================
# T4 薄链路（装配 → 请求 → 透出；AC-13 / AC-14 / AC-53）
# =============================================================================


class TestThinPipelineV2:
    def test_mock_text_written_under_output_name(self):
        """mock 返回文本 T → <output_name>.content == T（无二次加工）。"""
        executor = _executor(_client([_success("T")]), _SETTINGS_OPEN)
        result = run_coro(executor.execute(_make_context(_make_config())))
        assert result.success is True
        assert result.output_name == "llm_result"
        assert result.output_value == {"content": "T"}

    def test_json_like_text_not_parsed_or_reshaped(self):
        """类 JSON 文本 → 原样字符串透传，不解析不塑形（无二次加工）。"""
        executor = _executor(_client([_success('{"answer": 1}')]), _SETTINGS_OPEN)
        result = run_coro(executor.execute(_make_context(_make_config())))
        assert result.output_value == {"content": '{"answer": 1}'}
        assert isinstance(result.output_value["content"], str)

    def test_schema_declared_nonconforming_text_succeeds(self):
        """AC-13 核心断言：声明 output_schema + 不合结构文本 → 成功产出原文、
        零重试、零 LLM_OUTPUT_INVALID。"""
        config = _make_config(output_schema={"answer": "string"})
        executor = _executor(_client([_success("不是结构化输出")]), _SETTINGS_OPEN)
        result = run_coro(executor.execute(_make_context(config)))
        assert result.success is True
        assert result.output_value == {"content": "不是结构化输出"}
        assert result.error_code is None
        assert executor._llm_client.infer.call_count == 1

    def test_schema_declared_request_carries_no_output_schema(self):
        """声明 output_schema → 请求体不携带该键（登记不生效，DC-09）。"""
        config = _make_config(output_schema={"answer": "string"})
        executor = _executor(_client([_success("ok")]), _SETTINGS_OPEN)
        run_coro(executor.execute(_make_context(config)))
        request = executor._llm_client.infer.call_args_list[0].args[0]
        assert isinstance(request, InferenceRequest)
        assert request.output_schema is None

    def test_metadata_contains_no_credentials(self):
        """metadata 仅模型标识与采样摘要（采样键集封闭），无凭据类内容（AC-53）。"""
        executor = _executor(_client([_success("ok")]), _SETTINGS_OPEN)
        result = run_coro(executor.execute(_make_context(_make_config())))
        assert set(result.metadata.keys()) == {"model_identifier", "sampling"}
        assert set(result.metadata["sampling"].keys()) <= {
            "temperature",
            "top_p",
            "top_k",
            "max_tokens",
            "stop",
            "seed",
        }
        serialized = str(result.metadata).lower()
        for marker in _SECRET_MARKERS:
            assert marker not in serialized

    def test_inputs_none_flattens_all_outputs(self):
        """inputs=None → 全量平铺「名: 值」+ 提示词（与 agent 一致）。"""
        executor = _executor(_client([_success()]), _SETTINGS_OPEN)
        run_coro(
            executor.execute(
                _make_context(
                    _make_config(user_prompt="P"), outputs={"a": 1, "b": "x"}
                )
            )
        )
        request = executor._llm_client.infer.call_args_list[0].args[0]
        assert request.user_message == "a: 1\nb: x\nP"

    def test_inputs_empty_list_yields_prompt_only(self):
        """inputs=[] → 显式空清单：上下文为空，消息即提示词。"""
        executor = _executor(_client([_success()]), _SETTINGS_OPEN)
        run_coro(
            executor.execute(
                _make_context(_make_config(inputs=[], user_prompt="P"), outputs={"a": 1})
            )
        )
        request = executor._llm_client.infer.call_args_list[0].args[0]
        assert request.user_message == "P"

    def test_inputs_selected_set_uses_alias(self):
        """非空选择集 → 装配值按「alias ?? 命名输出名」平铺（FR-09）。"""
        config = _make_config(
            inputs=[AgentInputEntry(reference="a", alias="首项")], user_prompt="P"
        )
        executor = _executor(_client([_success()]), _SETTINGS_OPEN)
        run_coro(executor.execute(_make_context(config, outputs={"a": 1, "b": 2})))
        request = executor._llm_client.infer.call_args_list[0].args[0]
        assert request.user_message == "首项: 1\nP"

    def test_dangling_reference_reports_expression_and_node_id(self):
        """悬空引用 → NODE_INPUT_REFERENCE_MISSING，消息含引用表达式与 node_id。"""
        config = _make_config(inputs=[AgentInputEntry(reference="missing_out")])
        executor = _executor(_client([]), _SETTINGS_OPEN)
        result = run_coro(executor.execute(_make_context(config, outputs={})))
        assert result.success is False
        assert result.error_code == NODE_INPUT_REFERENCE_MISSING
        assert "missing_out" in result.error_message
        assert "llm-1" in result.error_message

    def test_explicit_temperature_reaches_request(self):
        """temperature=0.2 → 请求采样对象携带该显式值。"""
        config = _make_config(temperature=0.2)
        executor = _executor(_client([_success()]), _SETTINGS_OPEN)
        run_coro(executor.execute(_make_context(config)))
        request = executor._llm_client.infer.call_args_list[0].args[0]
        assert request.sampling.temperature == 0.2

    def test_unconfigured_sampling_all_none_in_request(self):
        """采样全未配置 → 请求采样对象全字段 None（执行器不注入默认值）。"""
        config = _make_config(
            temperature=None,
            top_p=None,
            top_k=None,
            max_tokens=None,
            stop=None,
            seed=None,
        )
        executor = _executor(_client([_success()]), _SETTINGS_OPEN)
        run_coro(executor.execute(_make_context(config)))
        sampling = executor._llm_client.infer.call_args_list[0].args[0].sampling
        assert sampling.temperature is None
        assert sampling.top_p is None
        assert sampling.top_k is None
        assert sampling.max_tokens is None
        assert sampling.stop is None
        assert sampling.seed is None

    def test_explicit_top_k_zero_reaches_request(self):
        """top_k=0 → 请求采样对象保留显式 0（payload 层省略属客户端发送矩阵，
        由 llm_sampling_spec 既有测试覆盖）。"""
        config = _make_config(top_k=0)
        executor = _executor(_client([_success()]), _SETTINGS_OPEN)
        run_coro(executor.execute(_make_context(config)))
        sampling = executor._llm_client.infer.call_args_list[0].args[0].sampling
        assert sampling.top_k == 0

    def test_400_with_param_name_maps_param_unsupported(self):
        """mock 400 + error_detail 含参数名 → LLM_PARAM_UNSUPPORTED 且指明参数。"""
        executor = _executor(
            _client([_failure("bad", status=400, detail="unsupported parameter: top_k")]),
            _SETTINGS_OPEN,
        )
        result = run_coro(executor.execute(_make_context(_make_config())))
        assert result.success is False
        assert result.error_code == LLM_PARAM_UNSUPPORTED
        assert "top_k" in result.error_message

    def test_connection_failure_maps_inference_failed_sanitized(self):
        """连接异常 → LLM_INFERENCE_FAILED，消息不含凭据类内容（脱敏）。"""
        executor = _executor(
            _client([_failure("Connection to model host refused")]), _SETTINGS_OPEN
        )
        result = run_coro(executor.execute(_make_context(_make_config())))
        assert result.success is False
        assert result.error_code == LLM_INFERENCE_FAILED
        lowered = (result.error_message or "").lower()
        for marker in _SECRET_MARKERS:
            assert marker not in lowered

    def test_assembly_failure_writes_no_node_output(self):
        """装配失败 → 共享状态无该节点输出（AC-14 之一）。"""
        config = _make_config(inputs=[AgentInputEntry(reference="missing_out")])
        context = _make_context(config, outputs={"pre": 1})
        executor = _executor(_client([]), _SETTINGS_OPEN)
        result = run_coro(executor.execute(context))
        assert result.success is False
        assert context.shared_state.node_outputs == {"pre": 1}
        assert context.shared_state.pending_diff == {}

    def test_inference_failure_writes_no_node_output(self):
        """推理失败 → 共享状态无该节点输出（AC-14 之二）。"""
        context = _make_context(_make_config(), outputs={"pre": 1})
        executor = _executor(_client([_failure("挂了")]), _SETTINGS_OPEN)
        result = run_coro(executor.execute(context))
        assert result.success is False
        assert context.shared_state.node_outputs == {"pre": 1}
        assert context.shared_state.pending_diff == {}

    def test_switch_off_writes_no_node_output(self):
        """开关关闭 → 共享状态无该节点输出（AC-14 之三）。"""
        context = _make_context(_make_config(), outputs={"pre": 1})
        executor = _executor(_client([]), _SETTINGS_OFF_EXPLICIT)
        result = run_coro(executor.execute(context))
        assert result.success is False
        assert context.shared_state.node_outputs == {"pre": 1}
        assert context.shared_state.pending_diff == {}
