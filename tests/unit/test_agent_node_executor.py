# =============================================================================
# AgentNodeExecutor ReAct 工具闭环单元测试
# 执行命令：PYTHONPATH=src python -m pytest tests/unit/test_agent_node_executor.py -v
#
# 覆盖 merge_tool_into_agent_node T2.5 用例 ①~⑥（AC-7 / FR-4 / DC-D / DC-F）：
#   ① 闭环调用：意图消费 → invoker 实际调用 → 观察回填进下一轮 user_message → 成功；
#   ② 轮次上限：达 max_tool_rounds 仍有意图 → NODE_AGENT_OUTPUT_INVALID（含轮次数）；
#   ③ 未绑定拒绝：清单外工具被拒（invoker 未被调）+ 拒绝观察回填；
#   ④ 绑定但无 invoker → 「工具不存在」观察，节点不因单次缺失失败（FR-4 规则 4）；
#   ⑤ INV-9 缺省等价：tool_invokers=None 行为与改造前一致（含主备切换/输出校验）；
#   ⑥ 既有 tool_invoker 单实例参数 → 并入映射兼容。
# =============================================================================

# 沙箱限制：本机 asyncio 事件循环不可用（socketpair 被网络策略拦截，
# WinError 10051 / _ssock AttributeError），run_coro 采用手动驱动协程
# 方式（send(None) 循环），不创建事件循环，故无需 import asyncio。

from unittest.mock import AsyncMock, MagicMock

import pytest

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.errors import (
    NODE_AGENT_OUTPUT_INVALID,
    NODE_INPUT_REFERENCE_MISSING,
    NODE_INPUT_TYPE_MISMATCH,
    NODE_LLM_UNAVAILABLE,
)
from orchestration_platform.domain.models.enums import NodeType
from orchestration_platform.domain.models.node_configurations import (
    AgentInputEntry,
    AgentNodeConfiguration,
)
from orchestration_platform.domain.models.node_definition import NodeDefinition
from orchestration_platform.domain.models.shared_state import SharedState
from orchestration_platform.engine.nodes.agent_node_executor import AgentNodeExecutor
from orchestration_platform.engine.nodes.base import NodeExecutionContext
from orchestration_platform.integrations.abstractions.external_clients import (
    InferenceResult,
    ToolResult,
)


def run_coro(coro):
    """手动驱动协程至完成（沙箱事件循环不可用时的 fallback）。

    execute 的 await 链（llm_client.infer / invoker.invoke 等）均为
    AsyncMock 或纯同步逻辑协程，无真实挂起点，故以 send(None) 循环
    驱动至 StopIteration，环境无关。
    """
    try:
        while True:
            coro.send(None)
    except StopIteration as exc:
        return exc.value


# =============================================================================
# 测试辅助
# =============================================================================

def _make_config(**kwargs) -> AgentNodeConfiguration:
    """构造 Agent 节点配置。"""
    defaults = {"model_identifier": "primary-model"}
    defaults.update(kwargs)
    return AgentNodeConfiguration(**defaults)


def _make_context(config: AgentNodeConfiguration) -> NodeExecutionContext:
    """构造执行上下文（空共享状态，user_message 为空串）。"""
    node_def = NodeDefinition(
        node_id="agent-1",
        name="analyze",
        node_type=NodeType.AGENT,
        configuration=config,
        output_name="agent_result",
    )
    return NodeExecutionContext(
        node_definition=node_def,
        shared_state=SharedState(instance_id="inst-1"),
        instance_id="inst-1",
        trace_id="trace-1",
        timeout_seconds=30,
    )


def _make_context_with_outputs(
    config: AgentNodeConfiguration, outputs: dict
) -> NodeExecutionContext:
    """构造带命名输出表的执行上下文（unified_node_input 双模式用例）。"""
    context = _make_context(config)
    context.shared_state.node_outputs.update(outputs)
    return context


def _intent(name: str, arguments=None) -> dict:
    """构造工具调用意图（OpenAI tool_calls 兼容结构）。"""
    return {
        "tool_calls": [
            {"function": {"name": name, "arguments": arguments if arguments is not None else {}}}
        ]
    }


def _llm_client(results) -> MagicMock:
    """构造 mock ILlmClient：infer 依序返回 results（耗尽再调即报错，防意外多轮）。"""
    client = MagicMock()
    client.infer = AsyncMock(side_effect=list(results))
    return client


def _invoker(identifier: str, output=None) -> MagicMock:
    """构造 mock IToolInvoker：成功返回固定输出。"""
    inv = MagicMock()
    inv.tool_identifier = identifier
    inv.invoke = AsyncMock(
        return_value=ToolResult(
            success=True, output=output if output is not None else {"ok": True}
        )
    )
    return inv


_FINAL = InferenceResult(success=True, content="done", structured_output={"answer": "3"})


# =============================================================================
# 用例 ①②③④⑥：ReAct 闭环行为（AC-7）
# =============================================================================

class TestReActLoop:
    """AC-7：ReAct 闭环——意图消费、观察回填、轮次上限、降级分支。"""

    def test_tool_invoked_and_observation_backfilled(self):
        """①闭环调用：LLM 返回意图 → invoker 实际被调 ≥1 次、观察回填进下一轮
        user_message、节点执行成功（AC-7）。"""
        invoker = _invoker("calc", output={"sum": 3})
        llm = _llm_client([
            InferenceResult(
                success=True,
                content="thinking",
                tool_call_intent=_intent("calc", {"a": 1, "b": 2}),
            ),
            InferenceResult(success=True, content="done", structured_output={"answer": "3"}),
        ])
        executor = AgentNodeExecutor(
            llm_client=llm, tool_invokers={"calc": invoker}
        )
        config = _make_config(available_tools=["calc"])
        result = run_coro(executor.execute(_make_context(config)))

        assert result.success is True
        assert result.output_value == {"answer": "3"}
        assert result.metadata["tool_rounds_used"] == 1
        assert result.metadata["tool_calls_executed"] == 1
        # invoker 实际被调用，入参与超时透传
        invoker.invoke.assert_called_once_with({"a": 1, "b": 2}, 30)
        # LLM 恰被调用两轮
        assert llm.infer.await_count == 2
        # 第二轮 user_message 含观察回填（TOOL_RESULT[calc]: ...）
        second_request = llm.infer.call_args_list[1].args[0]
        assert "TOOL_RESULT[calc]" in second_request.user_message
        assert "sum" in second_request.user_message

    def test_rounds_cap_fails_with_round_count(self):
        """②轮次上限：LLM 持续返回意图 → 达 max_tool_rounds 后节点失败
        NODE_AGENT_OUTPUT_INVALID 且错误信息含轮次数（AC-7）。"""
        invoker = _invoker("calc")
        intent_result = InferenceResult(
            success=True, content="loop", tool_call_intent=_intent("calc")
        )
        llm = _llm_client([intent_result, intent_result, intent_result])
        executor = AgentNodeExecutor(
            llm_client=llm, tool_invokers={"calc": invoker}
        )
        config = _make_config(available_tools=["calc"], max_tool_rounds=2)
        result = run_coro(executor.execute(_make_context(config)))

        assert result.success is False
        assert result.error_code == NODE_AGENT_OUTPUT_INVALID
        assert "工具调用轮次超限" in result.error_message
        assert "2 轮" in result.error_message
        assert result.metadata["tool_rounds_used"] == 2
        assert result.metadata["tool_calls_executed"] == 2
        # 三轮推理（2 轮工具执行 + 第 3 轮判超限），无第 4 次调用
        assert llm.infer.await_count == 3

    def test_unbound_tool_rejected(self):
        """③未绑定拒绝：Agent 仅绑定工具 bound，LLM 请求 other → 调用被拒
        （invoker 未被调）、回填拒绝观察（AC-7）。"""
        invoker = _invoker("bound")
        llm = _llm_client([
            InferenceResult(
                success=True, content="x", tool_call_intent=_intent("other")
            ),
            _FINAL,
        ])
        executor = AgentNodeExecutor(
            llm_client=llm, tool_invokers={"bound": invoker}
        )
        config = _make_config(available_tools=["bound"])
        result = run_coro(executor.execute(_make_context(config)))

        assert result.success is True
        invoker.invoke.assert_not_called()
        second_request = llm.infer.call_args_list[1].args[0]
        assert "未绑定" in second_request.user_message
        assert "other" in second_request.user_message
        assert result.metadata["tool_calls_executed"] == 1

    def test_bound_tool_missing_invoker_degrades(self):
        """④绑定工具无 invoker → 回填「工具不存在」观察，节点不因单次缺失
        失败（FR-4 规则 4）。"""
        other = _invoker("real")
        llm = _llm_client([
            InferenceResult(
                success=True, content="x", tool_call_intent=_intent("ghost")
            ),
            _FINAL,
        ])
        # 映射非空但缺 ghost：激活 ReAct 循环且走「工具不存在」分支
        executor = AgentNodeExecutor(
            llm_client=llm, tool_invokers={"real": other}
        )
        config = _make_config(available_tools=["ghost"])
        result = run_coro(executor.execute(_make_context(config)))

        assert result.success is True
        other.invoke.assert_not_called()
        second_request = llm.infer.call_args_list[1].args[0]
        assert "工具 ghost 不存在" in second_request.user_message

    def test_single_tool_invoker_param_merged_into_map(self):
        """⑥既有 tool_invoker 单实例参数传入 → 并入映射兼容（向后兼容）。"""
        invoker = _invoker("single_tool")
        llm = _llm_client([
            InferenceResult(
                success=True,
                content="x",
                tool_call_intent=_intent("single_tool", {"q": 1}),
            ),
            _FINAL,
        ])
        executor = AgentNodeExecutor(llm_client=llm, tool_invoker=invoker)

        # 单实例并入映射：键为 tool_identifier，值同实例
        assert executor._tool_invokers == {"single_tool": invoker}

        config = _make_config(available_tools=["single_tool"])
        result = run_coro(executor.execute(_make_context(config)))

        assert result.success is True
        invoker.invoke.assert_called_once_with({"q": 1}, 30)


# =============================================================================
# 用例 ⑤：INV-9 缺省等价回归（无 tool_invokers → 与改造前行为一致）
# =============================================================================

class TestLegacyEquivalence:
    """⑤INV-9 缺省等价：tool_invokers=None → 行为与改造前完全一致
    （意图忽略、主备切换、输出校验路径、metadata 仅含 model_identifier）。"""

    def test_intent_ignored_without_invokers(self):
        """无 invokers 时 LLM 返回意图不触发任何工具循环：仅推理一次、
        意图被忽略、metadata 与改造前一致。"""
        llm = _llm_client([
            InferenceResult(
                success=True, content="hi", tool_call_intent=_intent("anything")
            ),
        ])
        executor = AgentNodeExecutor(llm_client=llm)
        config = _make_config(available_tools=["anything"])
        result = run_coro(executor.execute(_make_context(config)))

        assert result.success is True
        assert result.output_value == {"content": "hi"}
        assert llm.infer.await_count == 1
        assert result.metadata == {"model_identifier": "primary-model"}

    def test_primary_fail_without_fallback(self):
        """主模型失败且无备用 → NODE_LLM_UNAVAILABLE「LLM 推理失败：…」。"""
        llm = _llm_client([InferenceResult(success=False, error_message="boom")])
        executor = AgentNodeExecutor(llm_client=llm)
        result = run_coro(executor.execute(_make_context(_make_config())))

        assert result.success is False
        assert result.error_code == NODE_LLM_UNAVAILABLE
        assert result.error_message == "LLM 推理失败：boom"

    def test_fallback_switch_on_primary_failure(self):
        """主模型失败 → 切备用成功；备用请求 model_identifier 取全局备用模型
        （缺省回落主模型标识）。"""
        settings = get_settings()
        expected_model = settings.llm_fallback_model or "primary-model"

        primary = _llm_client([InferenceResult(success=False, error_message="down")])
        fallback = _llm_client([
            InferenceResult(success=True, content="from-fallback"),
        ])
        executor = AgentNodeExecutor(
            llm_client=primary, fallback_llm_client=fallback
        )
        result = run_coro(executor.execute(_make_context(_make_config())))

        assert result.success is True
        assert result.output_value == {"content": "from-fallback"}
        fallback.infer.assert_awaited_once()
        fb_request = fallback.infer.call_args_list[0].args[0]
        assert fb_request.model_identifier == expected_model

    def test_fallback_also_fails(self):
        """主备均失败 → NODE_LLM_UNAVAILABLE「主备模型均不可用：…」+ 
        metadata 标记 model_switched（与改造前一致）。"""
        primary = _llm_client([InferenceResult(success=False, error_message="down")])
        fallback = _llm_client([InferenceResult(success=False, error_message="fbfail")])
        executor = AgentNodeExecutor(
            llm_client=primary, fallback_llm_client=fallback
        )
        result = run_coro(executor.execute(_make_context(_make_config())))

        assert result.success is False
        assert result.error_code == NODE_LLM_UNAVAILABLE
        assert result.error_message == "主备模型均不可用：fbfail"
        assert result.metadata == {"model_switched": True}

    def test_output_validation_retry_then_success(self):
        """输出不合规 → 用原请求重试一次 → 重试结果合规即成功（与改造前一致）。"""
        llm = _llm_client([
            InferenceResult(success=True, content="x", structured_output={"wrong": 1}),
            InferenceResult(success=True, content="y", structured_output={"answer": "ok"}),
        ])
        executor = AgentNodeExecutor(llm_client=llm)
        config = _make_config(
            output_schema={"answer": "string"}, output_max_retries=1
        )
        result = run_coro(executor.execute(_make_context(config)))

        assert result.success is True
        assert result.output_value == {"answer": "ok"}
        assert llm.infer.await_count == 2
        # 重试复用原请求（INV-9：重试请求与首轮一致）
        first_request = llm.infer.call_args_list[0].args[0]
        retry_request = llm.infer.call_args_list[1].args[0]
        assert retry_request.user_message == first_request.user_message
        assert retry_request.model_identifier == first_request.model_identifier

    def test_output_validation_exhausted(self):
        """输出不合规且重试仍不合规 → NODE_AGENT_OUTPUT_INVALID（与改造前一致）。"""
        llm = _llm_client([
            InferenceResult(success=True, content="x", structured_output={"wrong": 1}),
            InferenceResult(success=True, content="y", structured_output={"still": 2}),
        ])
        executor = AgentNodeExecutor(llm_client=llm)
        config = _make_config(
            output_schema={"answer": "string"}, output_max_retries=1
        )
        result = run_coro(executor.execute(_make_context(config)))

        assert result.success is False
        assert result.error_code == NODE_AGENT_OUTPUT_INVALID
        assert "输出结构不合规" in result.error_message
        assert llm.infer.await_count == 2


# =============================================================================
# unified_node_input T6.1：Agent 输入双模式用例
# （DC-03 / FR-09 / INV-D02 / INV-D07 / INV-D08 / INV-12 / AC-01 / AC-03）
# =============================================================================

class TestUnifiedInputModes:
    """Agent 用户消息双模式：隐式全量 / 显式空清单 / 显式选择集（DC-03）。"""

    def test_implicit_mode_byte_identical(self):
        """隐式全量（inputs=None）：用户消息与既有全量平铺格式逐字符一致
        （INV-D08 / AC-01 场景 2，旧 YAML 零迁移语义锚点）。"""
        outputs = {"alpha": {"x": 1}, "beta": [1, 2], "gamma": "文本"}
        expected = "\n".join(f"{name}: {value}" for name, value in outputs.items())

        llm = _llm_client([_FINAL])
        executor = AgentNodeExecutor(llm_client=llm)
        config = _make_config()  # inputs 缺省 None → 隐式全量
        result = run_coro(
            executor.execute(_make_context_with_outputs(config, outputs))
        )

        assert result.success is True
        user_message = llm.infer.call_args_list[0].args[0].user_message
        # 逐字符一致：不引入额外前缀 / 分隔 / 包装
        assert user_message == expected
        assert "alpha: {'x': 1}" in user_message
        assert "beta: [1, 2]" in user_message

    def test_explicit_narrowing_field_path(self):
        """显式收窄：仅声明 1 条字段路径引用 → 用户消息仅含该引用内容，
        清单外命名输出不可见（INV-D07 / INV-12 / AC-01 场景 1）。"""
        outputs = {"alpha": {"items": ["a", "b"]}, "beta": "其他输出"}

        llm = _llm_client([_FINAL])
        executor = AgentNodeExecutor(llm_client=llm)
        config = _make_config(inputs=[AgentInputEntry(reference="alpha.items")])
        result = run_coro(
            executor.execute(_make_context_with_outputs(config, outputs))
        )

        assert result.success is True
        user_message = llm.infer.call_args_list[0].args[0].user_message
        # 条目名缺省用命名输出名（alias ?? output_name，FR-09）
        assert user_message == "alpha: ['a', 'b']"
        assert "beta" not in user_message

    def test_explicit_empty_list(self):
        """显式空清单（inputs == []）：用户消息上下文条目为空
        （即使共享状态存在命名输出）。"""
        outputs = {"alpha": 1}

        llm = _llm_client([_FINAL])
        executor = AgentNodeExecutor(llm_client=llm)
        config = _make_config(inputs=[])
        result = run_coro(
            executor.execute(_make_context_with_outputs(config, outputs))
        )

        assert result.success is True
        user_message = llm.infer.call_args_list[0].args[0].user_message
        assert user_message == ""

    def test_alias_organization(self):
        """别名组织：两条目 {reference, alias} → 用户消息含「别名: 值」两行
        （FR-09 验收场景）。"""
        outputs = {"alpha": "第一条", "beta": "第二条"}

        llm = _llm_client([_FINAL])
        executor = AgentNodeExecutor(llm_client=llm)
        config = _make_config(
            inputs=[
                AgentInputEntry(reference="alpha", alias="上下文"),
                AgentInputEntry(reference="beta", alias="摘要"),
            ]
        )
        result = run_coro(
            executor.execute(_make_context_with_outputs(config, outputs))
        )

        assert result.success is True
        user_message = llm.infer.call_args_list[0].args[0].user_message
        assert user_message == "上下文: 第一条\n摘要: 第二条"

    def test_fail_fast_missing_reference(self):
        """fail-fast：清单任一条目引用缺失 → 节点失败，错误码统一、消息含
        条目引用与缺失目标名（INV-D07）；装配失败后不发起任何推理。"""
        outputs = {"alpha": 1}

        llm = _llm_client([_FINAL])
        executor = AgentNodeExecutor(llm_client=llm)
        config = _make_config(
            inputs=[
                AgentInputEntry(reference="alpha"),
                AgentInputEntry(reference="ghost"),
            ]
        )
        result = run_coro(
            executor.execute(_make_context_with_outputs(config, outputs))
        )

        assert result.success is False
        assert result.error_code == NODE_INPUT_REFERENCE_MISSING
        # 节点级 node_id 定位 + 条目引用与缺失目标名
        assert "Agent 节点 agent-1" in result.error_message
        assert "ghost" in result.error_message
        # fail-fast：不再调用 LLM
        llm.infer.assert_not_called()

    def test_fail_fast_path_into_scalar_type_mismatch(self):
        """fail-fast（类型）：字段路径穿过标量 → NODE_INPUT_TYPE_MISMATCH
        透传，消息含引用表达式与段名（DC-05 统一码场景）。"""
        outputs = {"alpha": "纯文本"}

        llm = _llm_client([_FINAL])
        executor = AgentNodeExecutor(llm_client=llm)
        config = _make_config(inputs=[AgentInputEntry(reference="alpha.items")])
        result = run_coro(
            executor.execute(_make_context_with_outputs(config, outputs))
        )

        assert result.success is False
        assert result.error_code == NODE_INPUT_TYPE_MISMATCH
        assert "alpha.items" in result.error_message
        assert "items" in result.error_message
        llm.infer.assert_not_called()

    def test_multi_upstream_dual_references(self):
        """多上游双引用独立装配：两条目引用不同上游命名输出 → 各自独立
        解析、互不干扰（AC-03 / INV-D02 同快照）。"""
        outputs = {"up_a": {"v": 1}, "up_b": [1, 2]}

        llm = _llm_client([_FINAL])
        executor = AgentNodeExecutor(llm_client=llm)
        config = _make_config(
            inputs=[
                AgentInputEntry(reference="up_a"),
                AgentInputEntry(reference="up_b"),
            ]
        )
        result = run_coro(
            executor.execute(_make_context_with_outputs(config, outputs))
        )

        assert result.success is True
        user_message = llm.infer.call_args_list[0].args[0].user_message
        assert user_message == "up_a: {'v': 1}\nup_b: [1, 2]"


# =============================================================================
# context_strategy（差异化扩展）：观察块窗口策略裁剪用例
# =============================================================================

class TestContextStrategy:
    """context_strategy 接线：None 缺省等价 + summary_compression 裁剪生效。"""

    def test_context_strategy_none_keeps_legacy_concatenation(self):
        """context_strategy=None（缺省）：多轮观察回填与既有字符串追加逐字节一致，
        metadata 不含 context_compression_events（缺省行为不变）。"""
        invoker = _invoker("calc", output={"step": 1})
        intent = InferenceResult(
            success=True, content="loop", tool_call_intent=_intent("calc")
        )
        llm = _llm_client([intent, intent, intent, _FINAL])
        executor = AgentNodeExecutor(llm_client=llm, tool_invokers={"calc": invoker})
        config = _make_config(available_tools=["calc"], max_tool_rounds=3)
        result = run_coro(executor.execute(_make_context(config)))

        assert result.success is True
        # 三轮观察块以 "\n" 串联（与既有追加语义逐字节一致）
        expected_block = 'TOOL_RESULT[calc]: {"step": 1}'
        expected = "\n".join([expected_block] * 3)
        assert llm.infer.call_args_list[3].args[0].user_message == expected
        assert "context_compression_events" not in result.metadata

    def test_summary_compression_prunes_old_observation_blocks(self):
        """context_strategy=summary_compression：观察块超过 retain_recent=5 后
        早期块被压缩为摘要占位，metadata 记录 context_compression_events。"""
        invoker = _invoker("calc", output={"step": 1})
        intent = InferenceResult(
            success=True, content="loop", tool_call_intent=_intent("calc")
        )
        # 7 轮意图 + 第 8 轮收敛：第 6 个观察块入列时触发首次压缩
        llm = _llm_client([intent] * 7 + [_FINAL])
        executor = AgentNodeExecutor(llm_client=llm, tool_invokers={"calc": invoker})
        config = _make_config(
            available_tools=["calc"],
            max_tool_rounds=7,
            context_strategy="summary_compression",
        )
        result = run_coro(executor.execute(_make_context(config)))

        assert result.success is True
        # 压缩发生：metadata 记录事件数
        assert result.metadata.get("context_compression_events", 0) >= 1
        # 最后一轮 user_message 含压缩摘要占位与近期观察块（progress 锚点语义：
        # 早期观察被压缩，最近 5 轮观察保留）
        final_message = llm.infer.call_args_list[7].args[0].user_message
        assert "[已压缩" in final_message
        assert "TOOL_RESULT[calc]" in final_message
