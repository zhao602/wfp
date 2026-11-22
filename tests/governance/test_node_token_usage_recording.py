# =============================================================================
# 节点级 Token 记账测试（tests/governance/test_node_token_usage_recording.py）
#
# 职责：验证 B7 W1b——provider usage 透传链路（执行器 metadata → scheduler
# 成功路径钩子 → record_node_usage 真实 token 数落仓储）。
# 设计意图（对应 .codeartsdoer/specs/roadmap_b7_harvest spec W1b / design §2）：
#   1. llm 执行器：InferenceResult.usage → metadata["token_usage"]（无 usage 不写键）；
#   2. agent 执行器：usage 取「产出最终输出那次推理」（重试成功即重试结果）；
#   3. http_llm_client.infer：OpenAI 兼容响应体顶层 usage 提取（缺省 None）；
#   4. record_node_usage：真实 token 数、tenant 兜底 log_tenant_default、
#      异形条目归一化、enabled=False no-op；
#   5. scheduler 成功路径钩子：metadata 有 token_usage 时记账行真实值；
#      metering 未装配（recorder=None）静默跳过。
# =============================================================================

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.models.enums import NodeType
from orchestration_platform.domain.models.node_configurations import (
    AgentNodeConfiguration,
    LlmNodeConfiguration,
)
from orchestration_platform.domain.models.node_definition import NodeDefinition
from orchestration_platform.domain.models.shared_state import SharedState
from orchestration_platform.engine.abstractions.compiled_workflow import (
    CompiledWorkflow,
)
from orchestration_platform.engine.node_executor_registry import NodeExecutorRegistry
from orchestration_platform.engine.nodes.agent_node_executor import AgentNodeExecutor
from orchestration_platform.engine.nodes.base import (
    NodeExecutionContext,
    NodeExecutionResult,
)
from orchestration_platform.engine.nodes.llm_node_executor import LlmNodeExecutor
from orchestration_platform.engine.scheduler import Scheduler
from orchestration_platform.engine.shared_state_manager import SharedStateManager
from orchestration_platform.governance.metering.usage_recorder import (
    ExecutionUsageRecorder,
    set_usage_recorder,
)
from orchestration_platform.integrations.abstractions.external_clients import (
    InferenceRequest,
    InferenceResult,
)
from orchestration_platform.integrations.llm.http_llm_client import HttpLlmClient

_USAGE = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}


class _RepoSpy:
    """内存假仓储：捕获 append/append_batch 供断言。"""

    def __init__(self) -> None:
        self.records = []

    async def append(self, record):
        self.records.append(record)
        return record.record_id

    async def append_batch(self, records):
        self.records.extend(records)
        return [r.record_id for r in records]


@pytest.fixture(autouse=True)
def _isolate_usage_recorder():
    """模块级记录器注入状态隔离：每个用例前后复位为 None（不泄漏他处）。"""
    set_usage_recorder(None)
    yield
    set_usage_recorder(None)


def _make_context(config, node_id: str = "llm-1") -> NodeExecutionContext:
    """构造执行上下文（llm/agent 通用）。"""
    node_def = NodeDefinition(
        node_id=node_id,
        name="node",
        node_type=NodeType.LLM,
        configuration=config,
        output_name="out",
    )
    return NodeExecutionContext(
        node_definition=node_def,
        shared_state=SharedState(instance_id="inst-1"),
        instance_id="inst-1",
        trace_id="trace-1",
        timeout_seconds=30,
    )


# =============================================================================
# A. llm 执行器：usage → metadata 透传
# =============================================================================

class TestLlmExecutorUsagePassthrough:

    def _executor(self, client) -> LlmNodeExecutor:
        executor = LlmNodeExecutor(llm_client=client)
        executor._settings = SimpleNamespace(
            llm_node_enabled=True, agent_output_max_retries=2
        )
        return executor

    async def test_usage_written_to_metadata_when_provider_returns(self):
        client = MagicMock()
        client.infer = AsyncMock(
            return_value=InferenceResult(
                success=True, content="ok", usage=dict(_USAGE)
            )
        )

        result = await self._executor(client).execute(
            _make_context(LlmNodeConfiguration(model_identifier="m-1"))
        )

        assert result.success is True
        assert result.metadata["token_usage"] == _USAGE

    async def test_no_usage_no_key(self):
        client = MagicMock()
        client.infer = AsyncMock(
            return_value=InferenceResult(success=True, content="ok")
        )

        result = await self._executor(client).execute(
            _make_context(LlmNodeConfiguration(model_identifier="m-1"))
        )

        assert result.success is True
        assert "token_usage" not in result.metadata, "provider 未返回 usage 不写键"


# =============================================================================
# B. agent 执行器：usage 取产出最终输出那次推理
# =============================================================================

class TestAgentExecutorUsageSource:

    async def test_agent_usage_from_final_output_inference(self):
        client = MagicMock()
        client.infer = AsyncMock(
            return_value=InferenceResult(
                success=True, content="ok", usage=dict(_USAGE)
            )
        )
        executor = AgentNodeExecutor(llm_client=client)

        result = await executor.execute(
            _make_context(AgentNodeConfiguration(model_identifier="m-1"))
        )

        assert result.success is True
        assert result.metadata["token_usage"] == _USAGE

    async def test_agent_retry_takes_retry_usage(self):
        """输出校验重试成功时，usage 取重试结果（产出最终输出的那次推理）。"""
        client = MagicMock()
        client.infer = AsyncMock(
            side_effect=[
                InferenceResult(
                    success=True,
                    content="bad",
                    structured_output={"wrong": 1},
                    usage={"prompt_tokens": 99, "completion_tokens": 99, "total_tokens": 198},
                ),
                InferenceResult(
                    success=True,
                    content="good",
                    structured_output={"answer": 42},
                    usage=dict(_USAGE),
                ),
            ]
        )
        executor = AgentNodeExecutor(llm_client=client)
        executor._settings = SimpleNamespace(agent_output_max_retries=2)

        result = await executor.execute(
            _make_context(
                AgentNodeConfiguration(
                    model_identifier="m-1",
                    output_schema={"answer": "integer"},
                )
            )
        )

        assert result.success is True
        assert result.metadata["token_usage"] == _USAGE, "重试成功取重试结果 usage"


# =============================================================================
# C. http_llm_client.infer：响应体顶层 usage 提取
# =============================================================================

class TestHttpLlmClientUsageExtraction:

    @staticmethod
    def _mock_httpx(response) -> None:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        return patch("httpx.AsyncClient", return_value=mock_client)

    async def test_infer_extracts_top_level_usage(self):
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "choices": [{"message": {"content": "ok"}}],
            "usage": dict(_USAGE),
        }
        with self._mock_httpx(response):
            client = HttpLlmClient("http://localhost", "sk-key", "gpt-4")
            result = await client.infer(
                InferenceRequest(model_identifier="gpt-4")
            )

        assert result.success is True
        assert result.usage == _USAGE

    async def test_infer_without_usage_returns_none(self):
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
        with self._mock_httpx(response):
            client = HttpLlmClient("http://localhost", "sk-key", "gpt-4")
            result = await client.infer(
                InferenceRequest(model_identifier="gpt-4")
            )

        assert result.success is True
        assert result.usage is None


# =============================================================================
# D. record_node_usage：字段/兜底/归一化/开关
# =============================================================================

class TestRecordNodeUsage:

    async def test_record_node_usage_persists_real_tokens_with_tenant_fallback(self):
        repo = _RepoSpy()
        recorder = ExecutionUsageRecorder(repo, enabled=True)

        record_id = await recorder.record_node_usage(
            tenant_id=None,
            instance_id="inst-9",
            node_id="llm-1",
            agent_node_id=None,
            model_identifier="gpt-4",
            token_usage=dict(_USAGE),
            workflow_definition_id="wf-1",
        )

        assert record_id is not None
        assert len(repo.records) == 1
        record = repo.records[0]
        assert record.tenant_id == get_settings().log_tenant_default, "计量域兜底"
        assert record.instance_id == "inst-9"
        assert record.node_id == "llm-1"
        assert record.agent_node_id is None
        assert record.model_identifier == "gpt-4"
        assert record.prompt_tokens == 10
        assert record.completion_tokens == 5
        assert record.total_tokens == 15
        assert record.workflow_definition_id == "wf-1"

    async def test_record_node_usage_normalizes_malformed_entries(self):
        repo = _RepoSpy()
        recorder = ExecutionUsageRecorder(repo, enabled=True)

        await recorder.record_node_usage(
            tenant_id="t1",
            instance_id="inst-9",
            node_id="agent-1",
            agent_node_id="agent-1",
            model_identifier="m",
            token_usage={"prompt_tokens": "12", "completion_tokens": None, "total_tokens": True},
        )

        record = repo.records[0]
        assert record.prompt_tokens == 12, "数字字符串取整"
        assert record.completion_tokens == 0, "缺失/None 计 0"
        assert record.total_tokens == 0, "bool 异形计 0"

    async def test_record_node_usage_disabled_noop(self):
        repo = _RepoSpy()
        recorder = ExecutionUsageRecorder(repo, enabled=False)

        record_id = await recorder.record_node_usage(
            tenant_id="t1",
            instance_id="inst-9",
            node_id="llm-1",
            agent_node_id=None,
            model_identifier="m",
            token_usage=dict(_USAGE),
        )

        assert record_id is None
        assert repo.records == [], "enabled=False no-op 零落盘"


# =============================================================================
# E. scheduler 成功路径钩子：metadata.token_usage → record_node_usage
# =============================================================================

class TestSchedulerNodeUsageHook:

    @staticmethod
    def _compiled() -> CompiledWorkflow:
        node_def = NodeDefinition(
            node_id="llm-1",
            name="n1",
            node_type=NodeType.LLM,
            configuration=LlmNodeConfiguration(model_identifier="m-1"),
            output_name="out",
        )
        return CompiledWorkflow(
            definition_id="wf-1",
            definition_version=1,
            nodes=[node_def],
            edges=[],
            entry_node_id="llm-1",
        )

    async def test_success_path_records_node_usage(self):
        repo = _RepoSpy()
        set_usage_recorder(ExecutionUsageRecorder(repo, enabled=True))
        scheduler = Scheduler(NodeExecutorRegistry())

        async def _fake_execute_node(node, state_manager, iid, tid, ri, compiled):
            now = datetime.now(timezone.utc)
            return (
                NodeExecutionResult(
                    success=True,
                    output_name="out",
                    output_value={"v": "ok"},
                    metadata={
                        "model_identifier": "m-1",
                        "token_usage": dict(_USAGE),
                    },
                ),
                now,
                now,
            )

        scheduler._execute_node = _fake_execute_node
        state_manager = SharedStateManager(SharedState(instance_id="inst-9"))

        await scheduler._run_node_lifecycle(
            self._compiled(), state_manager, "inst-9", "trace-9",
            self._compiled().nodes[0], 0,
        )

        assert len(repo.records) == 1
        record = repo.records[0]
        assert record.node_id == "llm-1"
        assert record.instance_id == "inst-9"
        assert record.prompt_tokens == 10
        assert record.total_tokens == 15
        assert record.model_identifier == "m-1"
        assert record.workflow_definition_id == "wf-1"

    async def test_hook_silent_without_recorder(self):
        scheduler = Scheduler(NodeExecutorRegistry())  # recorder 未装配（fixture 置 None）

        async def _fake_execute_node(node, state_manager, iid, tid, ri, compiled):
            now = datetime.now(timezone.utc)
            return (
                NodeExecutionResult(
                    success=True,
                    output_name="out",
                    output_value={"v": "ok"},
                    metadata={
                        "model_identifier": "m-1",
                        "token_usage": dict(_USAGE),
                    },
                ),
                now,
                now,
            )

        scheduler._execute_node = _fake_execute_node
        state_manager = SharedStateManager(SharedState(instance_id="inst-9"))

        result = await scheduler._run_node_lifecycle(
            self._compiled(), state_manager, "inst-9", "trace-9",
            self._compiled().nodes[0], 0,
        )

        assert result.success is True, "metering 未装配静默跳过，不影响执行"
