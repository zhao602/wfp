# =============================================================================
# LlmNodeExecutor 单元测试（llm_code_nodes_v2 任务 5.3 摘块后改写 7 用例，
# 其余为 llm_and_code_nodes 任务 5.3/5.5 保留面 / spec §5.4）
# 执行命令：PYTHONPATH=src python -m pytest tests/unit/test_llm_node_executor.py -v
#
# 覆盖（出口闸门：开关、装配失败、主备、结构登记不生效、失败不写、
# 脱敏、零副作用）：
#   ① 开关：默认关闭 → LLM_NODE_DISABLED（客户端零调用）；
#   ② 消息装配：三态语义（None 全量平铺 / [] 空清单 / 非空选择集）+
#      {{...}} 占位符原样保留（AC-46 对照 agent 口径）；
#   ③ 装配失败：缺失引用 → NODE_INPUT_REFERENCE_MISSING 透传 + 客户端零调用；
#   ④ 推理失败映射：LLM_INFERENCE_FAILED（消息含状态码）/ 400·422 含参数名
#      → LLM_PARAM_UNSUPPORTED（DC-10 启发式，识别失败不谎报）/ 主备双败
#      聚合消息透传（AC-20 / AC-22）；
#   ⑤ 结构校验摘除（DC-05 / DC-09 / AC-13）：output_schema 登记不生效——
#      请求不携带、零校验、零重生成；输出恒 {"content": 文本}；
#   ⑥ metadata：model_identifier + 采样摘要（不含凭据，AC-53）+ 切换标记
#      （DC-08）；失败不写共享状态（INV-11 / AC-26 零副作用）；
#   ⑦ 配置即时读取：同执行器二次执行感知配置变更（AC-18）。
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
from orchestration_platform.engine.nodes.output_schema import validate_output_schema
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

# 开关默认态：字段缺省（Settings 落地前无该字段；落地后缺省亦为关闭）
_SETTINGS_CLOSED = SimpleNamespace()
# 显式开启态：llm_node_enabled=True + 输出重生成上限（缺省 2）
_SETTINGS_OPEN = SimpleNamespace(llm_node_enabled=True, agent_output_max_retries=2)


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
    """构造执行器并覆盖 settings（真实 Settings 未声明新字段，无法 setattr）。"""
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
# ① 能力开关（DC-15：始终注册 + 内部判开关）
# =============================================================================


class TestCapabilitySwitch:
    def test_node_type_returns_llm(self):
        assert LlmNodeExecutor().node_type == NodeType.LLM

    def test_disabled_when_flag_absent(self):
        """开关字段缺省（未落地/未配置）→ LLM_NODE_DISABLED，客户端零调用。"""
        client = _client([])
        executor = _executor(client, _SETTINGS_CLOSED)
        result = run_coro(executor.execute(_make_context(_make_config())))
        assert result.success is False
        assert result.error_code == LLM_NODE_DISABLED
        assert "llm-1" in result.error_message
        assert "llm_node_enabled" in result.error_message
        assert client.infer.call_count == 0
        assert result.output_value is None
        assert result.output_name is None

    def test_disabled_when_flag_false(self):
        """显式关闭 → LLM_NODE_DISABLED（与缺省等价，INV-07）。"""
        executor = _executor(
            _client([]), SimpleNamespace(llm_node_enabled=False, agent_output_max_retries=2)
        )
        result = run_coro(executor.execute(_make_context(_make_config())))
        assert result.success is False
        assert result.error_code == LLM_NODE_DISABLED

    def test_enabled_runs_inference(self):
        """开启 → 正常推理：输出 {"content": 文本} + 命名输出。"""
        executor = _executor(_client([_success("ok")]), _SETTINGS_OPEN)
        result = run_coro(executor.execute(_make_context(_make_config(user_prompt="P"))))
        assert result.success is True
        assert result.output_value == {"content": "ok"}
        assert result.output_name == "llm_result"
        assert result.error_code is None


# =============================================================================
# ② 用户消息装配三态 + 占位符原样（AC-46 对照 agent 口径）
# =============================================================================


class TestMessageAssembly:
    def test_none_inputs_flatten_all_outputs(self):
        """inputs=None → 快照全量平铺「名: 值」+ 提示词（AC-46）。"""
        executor = _executor(_client([_success()]), _SETTINGS_OPEN)
        context = _make_context(
            _make_config(user_prompt="请分析"), outputs={"a": 1, "b": "x"}
        )
        run_coro(executor.execute(context))
        request = executor._llm_client.infer.call_args_list[0].args[0]
        assert request.user_message == "a: 1\nb: x\n请分析"

    def test_empty_inputs_context_empty(self):
        """inputs=[] → 显式空清单：上下文为空，消息即提示词。"""
        executor = _executor(_client([_success()]), _SETTINGS_OPEN)
        run_coro(
            executor.execute(
                _make_context(_make_config(inputs=[], user_prompt="P"), outputs={"a": 1})
            )
        )
        request = executor._llm_client.infer.call_args_list[0].args[0]
        assert request.user_message == "P"

    def test_empty_inputs_and_empty_prompt(self):
        """inputs=[] 且提示词为空 → 空串（单一空方不产生占位内容）。"""
        executor = _executor(_client([_success()]), _SETTINGS_OPEN)
        run_coro(executor.execute(_make_context(_make_config(inputs=[]))))
        request = executor._llm_client.infer.call_args_list[0].args[0]
        assert request.user_message == ""

    def test_selected_inputs_use_alias(self):
        """非空选择集 → 装配值按「alias ?? 命名输出名」平铺（FR-09）。"""
        config = _make_config(
            inputs=[AgentInputEntry(reference="a", alias="首项")], user_prompt="P"
        )
        executor = _executor(_client([_success()]), _SETTINGS_OPEN)
        run_coro(executor.execute(_make_context(config, outputs={"a": 1, "b": 2})))
        request = executor._llm_client.infer.call_args_list[0].args[0]
        assert request.user_message == "首项: 1\nP"

    def test_placeholder_braces_preserved(self):
        """{{...}} 一律按普通文本原样保留，无替换逻辑（AC-12）。"""
        executor = _executor(_client([_success()]), _SETTINGS_OPEN)
        run_coro(
            executor.execute(
                _make_context(
                    _make_config(user_prompt="变量 {{a}} 不替换"), outputs={"a": 1}
                )
            )
        )
        request = executor._llm_client.infer.call_args_list[0].args[0]
        assert "{{a}}" in request.user_message

    def test_context_only_when_prompt_empty(self):
        """提示词为空 → 仅上下文文本，无尾部换行。"""
        executor = _executor(_client([_success()]), _SETTINGS_OPEN)
        run_coro(
            executor.execute(_make_context(_make_config(user_prompt=""), outputs={"a": 1}))
        )
        request = executor._llm_client.infer.call_args_list[0].args[0]
        assert request.user_message == "a: 1"

    def test_missing_reference_fails_before_client_call(self):
        """装配失败 → NODE_INPUT_REFERENCE_MISSING 透传 + 客户端零调用（fail-fast）。"""
        config = _make_config(inputs=[AgentInputEntry(reference="missing_out")])
        client = _client([])
        executor = _executor(client, _SETTINGS_OPEN)
        result = run_coro(executor.execute(_make_context(config, outputs={})))
        assert result.success is False
        assert result.error_code == NODE_INPUT_REFERENCE_MISSING
        assert "输入条目装配失败" in result.error_message
        assert "missing_out" in result.error_message
        assert client.infer.call_count == 0
        assert result.output_value is None


# =============================================================================
# ③ 推理失败映射（DC-10 / AC-20 / AC-22）
# =============================================================================


class TestInferenceFailureMapping:
    def test_missing_client_returns_inference_failed(self):
        """客户端未配置 → LLM_INFERENCE_FAILED，不抛异常。"""
        executor = _executor(None, _SETTINGS_OPEN)
        result = run_coro(executor.execute(_make_context(_make_config())))
        assert result.success is False
        assert result.error_code == LLM_INFERENCE_FAILED
        assert "客户端未配置" in result.error_message

    def test_inference_failure_maps_with_status(self):
        """通用失败 → LLM_INFERENCE_FAILED，消息含上游状态码。"""
        executor = _executor(
            _client([_failure("上游过载", status=503)]), _SETTINGS_OPEN
        )
        result = run_coro(executor.execute(_make_context(_make_config())))
        assert result.success is False
        assert result.error_code == LLM_INFERENCE_FAILED
        assert "上游过载" in result.error_message
        assert "503" in result.error_message

    def test_inference_failure_without_status(self):
        """传输级失败（无状态码）→ 不追加状态码尾巴。"""
        executor = _executor(_client([_failure("连接超时")]), _SETTINGS_OPEN)
        result = run_coro(executor.execute(_make_context(_make_config())))
        assert result.error_code == LLM_INFERENCE_FAILED
        assert "连接超时" in result.error_message
        assert "状态码" not in result.error_message

    def test_param_unsupported_400_temperature(self):
        """400 + error_detail 含 temperature → LLM_PARAM_UNSUPPORTED 指明参数。"""
        executor = _executor(
            _client([_failure("bad", status=400, detail="temperature must be a float")]),
            _SETTINGS_OPEN,
        )
        result = run_coro(executor.execute(_make_context(_make_config())))
        assert result.success is False
        assert result.error_code == LLM_PARAM_UNSUPPORTED
        assert "temperature" in result.error_message
        assert "400" in result.error_message

    def test_param_unsupported_422_top_k(self):
        """422 + error_detail 含 top_k → LLM_PARAM_UNSUPPORTED 指明参数。"""
        executor = _executor(
            _client([_failure("bad", status=422, detail="Unsupported parameter: top_k")]),
            _SETTINGS_OPEN,
        )
        result = run_coro(executor.execute(_make_context(_make_config())))
        assert result.error_code == LLM_PARAM_UNSUPPORTED
        assert "top_k" in result.error_message
        assert "422" in result.error_message

    def test_status_400_without_param_name_stays_inference_failed(self):
        """400 但 error_detail 不含采样参数名 → 识别失败不谎报（仍 LLM_INFERENCE_FAILED）。"""
        executor = _executor(
            _client([_failure("bad", status=400, detail="malformed payload")]),
            _SETTINGS_OPEN,
        )
        result = run_coro(executor.execute(_make_context(_make_config())))
        assert result.error_code == LLM_INFERENCE_FAILED
        assert "400" in result.error_message

    def test_status_500_with_param_name_not_upgraded(self):
        """参数名命中但状态码非 400/422 → 不升级 LLM_PARAM_UNSUPPORTED。"""
        executor = _executor(
            _client([_failure("bad", status=500, detail="temperature too high")]),
            _SETTINGS_OPEN,
        )
        result = run_coro(executor.execute(_make_context(_make_config())))
        assert result.error_code == LLM_INFERENCE_FAILED

    def test_both_fail_aggregate_maps_to_inference_failed(self):
        """主备双败聚合消息 → LLM_INFERENCE_FAILED 透传（不掩盖主备信息）。"""
        executor = _executor(
            _client(
                [
                    _failure(
                        "主备模型均不可用：主=primary 503，备=fallback 503",
                        status=503,
                    )
                ]
            ),
            _SETTINGS_OPEN,
        )
        result = run_coro(executor.execute(_make_context(_make_config())))
        assert result.error_code == LLM_INFERENCE_FAILED
        assert "主备模型均不可用" in result.error_message
        assert "503" in result.error_message


# =============================================================================
# ④ 结构校验摘除（DC-05 / DC-09 / AC-13：登记不生效，输出恒 {"content"}）
# =============================================================================


class TestOutputSchemaRemoved:
    """DC-05/DC-09 摘块回归：output_schema 登记不生效，输出恒 content 透传。"""

    def test_structured_output_ignored_when_schema_declared(self):
        """声明结构 + 客户端返回结构化输出 → 结构化被忽略，输出仍 content 文本。"""
        config = _make_config(output_schema={"answer": "string"})
        executor = _executor(
            _client([_success(content="ok", structured={"answer": "42"})]), _SETTINGS_OPEN
        )
        result = run_coro(executor.execute(_make_context(config)))
        assert result.success is True
        assert result.output_value == {"content": "ok"}
        assert executor._llm_client.infer.call_count == 1

    def test_schema_declared_single_call_no_regeneration(self):
        """声明结构 + 不合结构文本 → 零校验零重生成，单次调用成功（AC-13）。"""
        config = _make_config(output_schema={"answer": "string"})
        executor = _executor(
            _client([_success("不合结构的原文")]),
            _SETTINGS_OPEN,
        )
        result = run_coro(executor.execute(_make_context(config)))
        assert result.success is True
        assert result.output_value == {"content": "不合结构的原文"}
        assert result.error_code is None
        assert executor._llm_client.infer.call_count == 1

    def test_schema_declared_never_yields_output_invalid(self):
        """声明结构 + 备用多轮结果在手 → 仍单次调用，LLM_OUTPUT_INVALID 零产生。"""
        config = _make_config(output_schema={"answer": "string"})
        executor = _executor(
            _client([_success("a"), _success("b"), _success("c")]), _SETTINGS_OPEN
        )
        result = run_coro(executor.execute(_make_context(config)))
        assert result.success is True
        assert result.error_code is None
        assert executor._llm_client.infer.call_count == 1

    def test_settings_without_retry_field_still_executes(self):
        """settings 缺 agent_output_max_retries 字段 → 照常执行（该字段零消费）。"""
        config = _make_config(output_schema={"answer": "string"})
        executor = _executor(
            _client([_success("ok")]),
            SimpleNamespace(llm_node_enabled=True),
        )
        result = run_coro(executor.execute(_make_context(config)))
        assert result.success is True
        assert result.output_value == {"content": "ok"}

    def test_schema_declared_inference_failure_stays_inference_failed(self):
        """声明结构 + 推理失败 → LLM_INFERENCE_FAILED（不再收敛 LLM_OUTPUT_INVALID）。"""
        config = _make_config(output_schema={"answer": "string"})
        executor = _executor(
            _client([_failure("上游过载", status=503)]),
            _SETTINGS_OPEN,
        )
        result = run_coro(executor.execute(_make_context(config)))
        assert result.success is False
        assert result.error_code == LLM_INFERENCE_FAILED
        assert executor._llm_client.infer.call_count == 1
        assert result.output_value is None

    def test_type_mismatch_structured_not_validated(self):
        """声明结构 + 结构化输出类型不符 → 不校验，content 文本照常透传。"""
        config = _make_config(output_schema={"answer": "string"})
        executor = _executor(
            _client([_success(content="42", structured={"answer": 42})]),
            SimpleNamespace(llm_node_enabled=True),
        )
        result = run_coro(executor.execute(_make_context(config)))
        assert result.success is True
        assert result.output_value == {"content": "42"}


# =============================================================================
# ⑤ metadata（脱敏 / 切换标记）与零副作用（AC-26 / AC-53 / DC-08 / INV-11）
# =============================================================================


class TestMetadataAndSideEffects:
    def test_metadata_contains_resolved_sampling_and_no_credentials(self):
        """metadata = model_identifier + 采样摘要；无任何凭据键（AC-53 脱敏）。"""
        config = _make_config(temperature=0.5)
        executor = _executor(_client([_success()]), _SETTINGS_OPEN)
        result = run_coro(executor.execute(_make_context(config)))
        assert result.success is True
        assert set(result.metadata.keys()) == {"model_identifier", "sampling"}
        assert result.metadata["model_identifier"] == "primary-model"
        assert result.metadata["sampling"]["temperature"] == 0.5
        assert result.metadata["sampling"]["max_tokens"] == 4096

    def test_request_carries_sampling_and_prompt(self):
        """请求携带采样对象与提示词（不落输出值，AC-19 采样不入输出）。"""
        config = _make_config(system_prompt="sys", temperature=0.5, top_p=0.9)
        executor = _executor(_client([_success()]), _SETTINGS_OPEN)
        run_coro(executor.execute(_make_context(config)))
        request = executor._llm_client.infer.call_args_list[0].args[0]
        assert isinstance(request, InferenceRequest)
        assert request.system_prompt == "sys"
        assert request.model_identifier == "primary-model"
        assert request.sampling.temperature == 0.5
        assert request.sampling.top_p == 0.9
        assert request.sampling.seed is None

    def test_model_switched_flag_in_metadata(self):
        """备用通道成功（model_switched=True）→ metadata 标记（DC-08 / AC-22）。"""
        executor = _executor(_client([_success(switched=True)]), _SETTINGS_OPEN)
        result = run_coro(executor.execute(_make_context(_make_config())))
        assert result.metadata.get("model_switched") is True

    def test_model_switched_absent_when_false(self):
        """主通道成功 → 不落切换标记（metadata 仅两项）。"""
        executor = _executor(_client([_success(switched=False)]), _SETTINGS_OPEN)
        result = run_coro(executor.execute(_make_context(_make_config())))
        assert "model_switched" not in result.metadata

    def test_model_switched_with_schema_declared(self):
        """声明结构 + 备用通道成功 → 切换标记照常落账（与结构登记正交，DC-08）。"""
        config = _make_config(output_schema={"answer": "string"})
        executor = _executor(
            _client([_success("ok", switched=True)]),
            _SETTINGS_OPEN,
        )
        result = run_coro(executor.execute(_make_context(config)))
        assert result.success is True
        assert result.output_value == {"content": "ok"}
        assert result.metadata.get("model_switched") is True

    def test_success_writes_nothing_to_shared_state(self):
        """成功执行零副作用：不写命名输出、不写增量表（AC-26 / INV-11）。"""
        context = _make_context(_make_config(), outputs={"a": 1})
        executor = _executor(_client([_success()]), _SETTINGS_OPEN)
        run_coro(executor.execute(context))
        assert context.shared_state.node_outputs == {"a": 1}
        assert context.shared_state.pending_diff == {}

    def test_failure_writes_nothing(self):
        """失败执行零副作用 + output_value=None（INV-11 失败不写）。"""
        context = _make_context(_make_config(), outputs={"a": 1})
        executor = _executor(_client([_failure("挂了")]), _SETTINGS_OPEN)
        result = run_coro(executor.execute(context))
        assert result.success is False
        assert result.output_value is None
        assert result.output_name is None
        assert context.shared_state.node_outputs == {"a": 1}
        assert context.shared_state.pending_diff == {}

    def test_output_name_falls_back_to_node_id(self):
        """未声明 output_name → 有效输出名回退 node_id。"""
        config = _make_config()
        context = _make_context(config, node_id="llm-9", output_name=None)
        executor = _executor(_client([_success()]), _SETTINGS_OPEN)
        result = run_coro(executor.execute(context))
        assert result.output_name == "llm-9"

    def test_config_reread_each_execute(self):
        """同执行器二次执行感知配置变更（每次即时读取，AC-18）。"""
        config = _make_config(temperature=None)
        executor = _executor(_client([_success(), _success()]), _SETTINGS_OPEN)
        run_coro(executor.execute(_make_context(config)))
        first = executor._llm_client.infer.call_args_list[0].args[0]
        assert first.sampling.temperature is None
        config.temperature = 0.9
        run_coro(executor.execute(_make_context(config)))
        second = executor._llm_client.infer.call_args_list[1].args[0]
        assert second.sampling.temperature == 0.9


# =============================================================================
# validate_output_schema 纯函数直测（任务 5.2：CHG-10 / DC-09 / FR-10 / FR-22 / AC-24）
# =============================================================================


class TestValidateOutputSchemaUnit:
    """字段存在性 + 类型映射（agent 同口径 + null）；纯函数、无异常外抛。"""

    def test_valid_returns_none(self):
        """合规 → None。"""
        assert validate_output_schema({"answer": "x"}, {"answer": "string"}) is None

    def test_missing_field_names_field(self):
        """缺字段 → 指明字段名。"""
        message = validate_output_schema({}, {"answer": "string"})
        assert message == "缺少必填字段：answer"

    def test_type_mismatch_names_expected_and_actual(self):
        """类型不符 → 指明字段名与期望/实际。"""
        message = validate_output_schema({"answer": 42}, {"answer": "string"})
        assert message == "字段 answer 类型不匹配：期望 string，实际 int"

    def test_schema_none_means_undeclared(self):
        """schema=None → 未声明，返回 None。"""
        assert validate_output_schema({"a": 1}, None) is None

    def test_schema_empty_means_undeclared(self):
        """schema={} → 未声明，返回 None。"""
        assert validate_output_schema({"a": 1}, {}) is None

    def test_null_type_accepts_none(self):
        """null 类型接受 None 值（agent 口径扩展）。"""
        assert validate_output_schema({"x": None}, {"x": "null"}) is None

    def test_number_accepts_int_and_float(self):
        """number 同时接受 int 与 float（agent 同口径）。"""
        assert validate_output_schema({"n": 3}, {"n": "number"}) is None
        assert validate_output_schema({"n": 3.5}, {"n": "number"}) is None

    def test_integer_rejects_string(self):
        """integer 拒绝字符串。"""
        assert validate_output_schema({"n": "3"}, {"n": "integer"}) is not None

    def test_boolean_object_array_types(self):
        """boolean/object/array 基本类型判定。"""
        assert validate_output_schema({"f": True}, {"f": "boolean"}) is None
        assert validate_output_schema({"o": {"k": 1}}, {"o": "object"}) is None
        assert validate_output_schema({"l": [1]}, {"l": "array"}) is None

    def test_multiple_fields_first_failure_reported_in_order(self):
        """多字段按声明顺序报告首个失败。"""
        message = validate_output_schema({"b": 1}, {"a": "string", "b": "integer"})
        assert message == "缺少必填字段：a"

    def test_unknown_type_string_passes(self):
        """未知类型字符串不设限（agent 同口径：未知 → 通过）。"""
        assert validate_output_schema({"x": "v"}, {"x": "weird"}) is None

    def test_no_exception_on_any_input(self):
        """纯函数：任意输入不外抛异常（返回 None 或原因文本）。"""
        outcome = validate_output_schema({"x": []}, {"x": "string"})
        assert isinstance(outcome, str)
