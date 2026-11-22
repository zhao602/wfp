# =============================================================================
# LLM/Code 节点全链路场景单元测试（llm_code_nodes_v2 任务 8.2/8.3）
# 执行命令：PYTHONPATH=src python -m pytest tests/unit/test_llm_code_scenarios.py -q
#
# 覆盖（spec FR-35/FR-36 / AC-35/AC-36；design §5 全链路时序）：
#   - 场景一（8.2）：trigger → LLM(mock) → output_display 两接法全链路成功——
#     ① inputs=None 隐式全量平铺：user_message 含全部命名输出平铺行；
#     ② 显式选择集：仅清单内输出进入上下文（FR-09 清单外不进入）；
#     output_display 经 input_reference 引用 <output_name>.content 成功；
#     错误码零出现（on_node_failed 零捕获为全链路判据）。
#   - 场景二（8.3）：代码节点确定性 glue 两组合方向——
#     ① LLM(mock) content 文本 → 代码节点加工 → 下游引用 result 成功；
#     ② trigger 原始数据 → 代码节点清洗 → LLM(mock) 装配推理成功；
#     代码节点 params 正确接收上游（inputs=None 全量平铺）、result 正确
#     写入共享状态；代码走 A1 默认通道真实受限执行（inprocess）。
#
# 驱动方式：YamlWorkflowParser 解析 → GraphCompiler 编译 → Scheduler 调度
# （run_coro 手动驱动协程——await 链均为 AsyncMock/纯同步逻辑协程，无真实
# 挂起点；沙箱本机 asyncio 事件循环不可用，WinError 10051 既知事实）。
#
# 脚本书写约束（A1 受限执行，design 附录 B.1 白名单 44 项内建）：
#   - 顶层执行、params 注入、对全局 result 赋值（entry_function 展示语义）；
#   - 无 import（受限命名空间剥离 __import__）、仅白名单内建与方法调用。
#
# N 级登记：真实 LLM 调用不可验证（沙箱无网络，客户端为 mock）；A2 子进程
# 通道不在本文件范围（默认 A1）。
# =============================================================================

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from orchestration_platform.domain.models.shared_state import SharedState
from orchestration_platform.engine.graph_compiler import GraphCompiler
from orchestration_platform.engine.node_executor_registry import NodeExecutorRegistry
from orchestration_platform.engine.nodes.code_node_executor import CodeNodeExecutor
from orchestration_platform.engine.nodes.llm_node_executor import LlmNodeExecutor
from orchestration_platform.engine.nodes.output_display_node_executor import (
    OutputDisplayNodeExecutor,
)
from orchestration_platform.engine.nodes.trigger_node_executor import TriggerNodeExecutor
from orchestration_platform.engine.scheduler import Scheduler
from orchestration_platform.integrations.abstractions.external_clients import (
    InferenceResult,
)
from orchestration_platform.workflow.yaml_workflow_parser import YamlWorkflowParser


def run_coro(coro):
    """手动驱动协程至完成（沙箱事件循环不可用时的 fallback）。

    execute 的 await 链（llm_client.infer 为 AsyncMock，其余为纯同步逻辑
    协程）无真实挂起点，故以 send(None) 循环驱动至 StopIteration。
    """
    try:
        while True:
            coro.send(None)
    except StopIteration as exc:
        return exc.value


# 执行器 settings 覆盖（真实 Settings 已声明字段，测试注入命名空间）
_LLM_SETTINGS = SimpleNamespace(llm_node_enabled=True)
_CODE_SETTINGS = SimpleNamespace(
    code_node_enabled=True,
    code_execution_timeout_seconds=10.0,
    code_output_max_bytes=65536,
    code_execution_channel="inprocess",
)


def _llm_client(content: str) -> MagicMock:
    """构造 mock ILlmClient：infer 恰返回一次 content（再调即报错，防意外多轮）。"""
    client = MagicMock()
    client.infer = AsyncMock(
        side_effect=[InferenceResult(success=True, content=content)]
    )
    return client


class _CapturingCallback:
    """节点事件捕获器（output_display 的 display_content 断言载体）。"""

    def __init__(self):
        self.completed = []
        self.failed = []

    async def on_node_started(self, instance_id, node_id, trace_id):
        pass

    async def on_node_completed(self, instance_id, node_id, trace_id, payload=None):
        self.completed.append((node_id, payload))

    async def on_node_failed(self, instance_id, node_id, trace_id, error):
        self.failed.append((node_id, error))


def _registry(llm_client: MagicMock) -> NodeExecutorRegistry:
    """构造场景注册表：trigger/llm(mock)/code(A1)/output_display 四真实执行器。"""
    registry = NodeExecutorRegistry()
    registry.register(TriggerNodeExecutor())
    llm_executor = LlmNodeExecutor(llm_client=llm_client)
    llm_executor._settings = _LLM_SETTINGS
    registry.register(llm_executor)
    code_executor = CodeNodeExecutor()
    code_executor._settings = _CODE_SETTINGS
    registry.register(code_executor)
    registry.register(OutputDisplayNodeExecutor())
    return registry


def _run(yaml_text: str, llm_content: str):
    """全链路驱动：解析 → 编译 → 调度执行；返回（终态，事件捕获，mock 客户端）。"""
    client = _llm_client(llm_content)
    definition = YamlWorkflowParser().parse(yaml_text)
    compiled = run_coro(GraphCompiler().compile(definition))
    callback = _CapturingCallback()
    scheduler = Scheduler(_registry(client), event_callback=callback)
    initial_state = SharedState(
        instance_id="run-scenario",
        current_node_id=compiled.entry_node_id,
    )
    final_state = run_coro(
        scheduler.execute(compiled, initial_state, "run-scenario", "trace-scenario")
    )
    return final_state, callback, client


def _display_payload(callback: _CapturingCallback, node_id: str):
    for completed_node_id, payload in callback.completed:
        if completed_node_id == node_id:
            return payload
    return None


# =============================================================================
# 场景一（8.2 / FR-35 / AC-35）：trigger → LLM(mock) → output_display
# =============================================================================

# 接法一：inputs=None（配置省略键）→ 隐式全量平铺
_SCENARIO_ONE_FLATTEN_YAML = """\
spec_version: "1.0"
definition_id: scenario_one_flatten
name: 场景一全量平铺
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

# 接法二：显式选择集（字段路径引用 + 别名；清单外输出不进入，FR-09）
_SCENARIO_ONE_SELECTION_YAML = """\
spec_version: "1.0"
definition_id: scenario_one_selection
name: 场景一显式选择集
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
      inputs:
        - reference: trigger_start.trigger_type
          alias: trigger_kind
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


class TestScenarioOneLlmInferenceBase:
    """场景一：LLM 节点作为纯推理底座（FR-35 / AC-35）。"""

    def test_flatten_wiring_full_chain_succeeds(self):
        final_state, callback, client = _run(
            _SCENARIO_ONE_FLATTEN_YAML, "模拟推理输出内容"
        )

        # 全链路成功：无节点失败（错误码零出现判据），命名输出就位
        assert callback.failed == []
        assert set(final_state.node_outputs) == {"trigger_start", "summary"}
        assert final_state.node_outputs["trigger_start"] == {"trigger_type": "manual"}
        assert final_state.node_outputs["summary"] == {"content": "模拟推理输出内容"}

        # mock 恰一次推理；请求三键与全量平铺装配的机械判据
        client.infer.assert_awaited_once()
        request = client.infer.await_args.args[0]
        assert request.model_identifier == "qwen-max"
        assert request.system_prompt == "你是测试助手。"
        assert request.user_message == (
            "trigger_start: {'trigger_type': 'manual'}\n请总结输入素材。"
        )

        # output_display 引用 <output_name>.content 成功（display 事件载荷）
        payload = _display_payload(callback, "display_result")
        assert payload == {
            "display_content": "模拟推理输出内容",
            "display_title": "最终结果",
        }

    def test_selection_wiring_full_chain_succeeds(self):
        final_state, callback, client = _run(
            _SCENARIO_ONE_SELECTION_YAML, "选择集接法推理输出"
        )

        assert callback.failed == []
        assert set(final_state.node_outputs) == {"trigger_start", "summary"}
        assert final_state.node_outputs["summary"] == {"content": "选择集接法推理输出"}

        # 显式选择集装配：仅清单内输出进入上下文（alias 组织键），
        # 清单外命名输出（trigger_start 整体）不进入（FR-09）
        client.infer.assert_awaited_once()
        request = client.infer.await_args.args[0]
        assert request.user_message == "trigger_kind: manual\n请总结输入素材。"
        assert "trigger_start:" not in request.user_message

        payload = _display_payload(callback, "display_result")
        assert payload == {
            "display_content": "选择集接法推理输出",
            "display_title": "最终结果",
        }


# =============================================================================
# 场景二（8.3 / FR-36 / AC-36）：代码节点确定性 glue 两组合方向
# =============================================================================

# 方向一：LLM(mock) content 文本 → 代码节点解析加工 → 下游 output_display 引用
_SCENARIO_TWO_LLM_TO_CODE_YAML = """\
spec_version: "1.0"
definition_id: scenario_two_llm_to_code
name: 场景二大模型到代码
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
    name: 生成结论
    node_type: llm
    output_name: summary
    configuration:
      model_identifier: qwen-max
      system_prompt: 你是测试助手。
      user_prompt: 请输出「状态码:分数」格式的结论。
  - node_id: code_parse
    name: 解析结论
    node_type: code
    output_name: parsed
    configuration:
      language: python
      code: |
        parts = params["summary"]["content"].split(":")
        result = {"status": parts[0], "score": int(parts[1])}
      entry_function: main
  - node_id: display_result
    name: 展示解析结果
    node_type: output_display
    configuration:
      input_reference: parsed.result
edges:
  - edge_id: e1
    source_node_id: trigger_start
    target_node_id: llm_generate
  - edge_id: e2
    source_node_id: llm_generate
    target_node_id: code_parse
  - edge_id: e3
    source_node_id: code_parse
    target_node_id: display_result
"""

# 方向二：trigger 原始数据 → 代码节点清洗 → LLM(mock) 装配推理 → 下游引用
_SCENARIO_TWO_CODE_TO_LLM_YAML = """\
spec_version: "1.0"
definition_id: scenario_two_code_to_llm
name: 场景二代码到大模型
definition_version: 1
inputs: []
outputs: []
nodes:
  - node_id: trigger_start
    name: 手动触发
    node_type: trigger
    configuration:
      trigger_type: manual
  - node_id: code_clean
    name: 清洗素材
    node_type: code
    output_name: cleaned
    configuration:
      language: python
      code: |
        raw = params["trigger_start"]["trigger_type"]
        result = {"cleaned_topic": str(raw).upper()}
      entry_function: main
  - node_id: llm_infer
    name: 基于素材推理
    node_type: llm
    output_name: inference
    configuration:
      model_identifier: qwen-max
      system_prompt: 你是测试助手。
      user_prompt: 请基于素材推理。
      inputs:
        - reference: cleaned.result
          alias: material
  - node_id: display_result
    name: 展示推理结果
    node_type: output_display
    configuration:
      input_reference: inference.content
edges:
  - edge_id: e1
    source_node_id: trigger_start
    target_node_id: code_clean
  - edge_id: e2
    source_node_id: code_clean
    target_node_id: llm_infer
  - edge_id: e3
    source_node_id: llm_infer
    target_node_id: display_result
"""


class TestScenarioTwoCodeDeterministicGlue:
    """场景二：代码节点确定性 glue 两组合方向（FR-36 / AC-36）。"""

    def test_llm_content_processed_by_code_then_referenced(self):
        final_state, callback, client = _run(
            _SCENARIO_TWO_LLM_TO_CODE_YAML, "ok:95"
        )

        # 全链路成功（错误码零出现）；代码经 A1 真实受限执行
        assert callback.failed == []
        assert set(final_state.node_outputs) == {"trigger_start", "summary", "parsed"}

        # params 正确接收上游（inputs=None 全量平铺：脚本读到 summary.content）
        # 且 result 正确写入共享状态（{"result": <值>} 形态）
        assert final_state.node_outputs["summary"] == {"content": "ok:95"}
        assert final_state.node_outputs["parsed"] == {
            "result": {"status": "ok", "score": 95}
        }

        # 下游引用代码产出 result 成功（parsed.result → display_content）
        payload = _display_payload(callback, "display_result")
        assert payload["display_content"] == {"status": "ok", "score": 95}
        client.infer.assert_awaited_once()

    def test_trigger_data_cleaned_by_code_then_llm_inference(self):
        final_state, callback, client = _run(
            _SCENARIO_TWO_CODE_TO_LLM_YAML, "基于清洗后素材的推理结果"
        )

        assert callback.failed == []
        assert set(final_state.node_outputs) == {"trigger_start", "cleaned", "inference"}

        # trigger 原始数据 → 代码清洗：params 全量平铺接收 trigger 产出，
        # result 写入共享状态
        assert final_state.node_outputs["cleaned"] == {
            "result": {"cleaned_topic": "MANUAL"}
        }

        # 代码产出 → LLM 显式选择集装配：material 别名承载 cleaned.result，
        # 清单外命名输出（trigger_start）不进入上下文（FR-09）
        client.infer.assert_awaited_once()
        request = client.infer.await_args.args[0]
        assert request.user_message == (
            "material: {'cleaned_topic': 'MANUAL'}\n请基于素材推理。"
        )
        assert "trigger_start:" not in request.user_message

        # LLM 推理输出落共享状态，下游 output_display 引用 content 成功
        assert final_state.node_outputs["inference"] == {
            "content": "基于清洗后素材的推理结果"
        }
        payload = _display_payload(callback, "display_result")
        assert payload["display_content"] == "基于清洗后素材的推理结果"
