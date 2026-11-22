# =============================================================================
# 运行日志接线集成测试（tests/integration/test_run_log_wiring.py，workflow_run_logging 批次 5）
#
# 覆盖验收：T6.4（采集接线不误伤执行守护）、T7.6（端到端落盘 tmp_path、
# run_id/trace_id 全行一致与按 run_id 聚合、跨天聚合、事件行为不变、
# 租户/业务类别回退各分支、InstanceRuntime 前后兼容构造）。
# =============================================================================
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from starlette.requests import Request

import orchestration_platform.api.routers.execution_instance_router as execution_instance_router
from orchestration_platform.configuration.settings import Settings
from orchestration_platform.domain.errors import (
    NODE_TIMEOUT,
    WF_NODE_EXECUTION_FAILED,
    NodeExecutionError,
)
from orchestration_platform.domain.models.enums import (
    EventType,
    InstanceStatus,
    NodeType,
)
from orchestration_platform.domain.models.execution_instance import StartInstanceRequest
from orchestration_platform.domain.models.shared_state import SharedState
from orchestration_platform.engine.graph_compiler import GraphCompiler
from orchestration_platform.engine.node_executor_registry import NodeExecutorRegistry
from orchestration_platform.engine.nodes.base import NodeExecutionResult
from orchestration_platform.engine.scheduler import Scheduler
from orchestration_platform.observability.run_log.fields import (
    BUSINESS_CATEGORY_DEFAULT,
    LEVEL_ERROR,
    LEVEL_INFO,
    RECORD_TYPE_NODE_RUN,
    RECORD_TYPE_WORKFLOW_RUN,
    STATUS_FAILED,
    STATUS_RUNNING,
    STATUS_SUCCEEDED,
    STATUS_TIMEOUT,
)
from orchestration_platform.observability.run_log.recorder import WorkflowRunLogger
from orchestration_platform.observability.run_log.sink import RunLogSink
from orchestration_platform.workflow.yaml_workflow_parser import YamlWorkflowParser

SEQUENTIAL_YAML = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "valid_workflow_definitions"
    / "valid_sequential_four_nodes.yaml"
).read_text(encoding="utf-8")

_TIMEOUT_YAML = """
spec_version: "1.0"
definition_id: timeout_probe
name: 超时探针
definition_version: 1
inputs:
  - name: topic
    type: string
    required: true
    description: 分析主题
outputs:
  - name: slow
    type: object
    description: 慢节点输出
nodes:
  - node_id: trigger_manual
    name: 手动触发
    node_type: trigger
    configuration:
      trigger_type: manual
  - node_id: slow_agent
    name: 慢节点
    node_type: agent
    timeout_seconds: 1
    output_name: slow
    configuration:
      model_identifier: primary-model
      system_prompt: 测试用慢节点。
edges:
  - edge_id: e1
    source_node_id: trigger_manual
    target_node_id: slow_agent
"""


@pytest.fixture(autouse=True)
def _isolated_run_log_dir(tmp_path, monkeypatch):
    """R-C 安全网：LOG_DIR 指向临时目录并清理 settings 缓存，防仓库 logs/ 污染。"""
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "run_log_isolated"))
    from orchestration_platform.configuration.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# =============================================================================
# 辅助构造
# =============================================================================

def _make_settings(tmp_path, **overrides):
    values = {
        "log_dir": str(tmp_path / "logs"),
        "log_level": "INFO",
        "log_async_enabled": False,
    }
    values.update(overrides)
    return Settings(**values)


async def _compile_sequential():
    definition = YamlWorkflowParser().parse(SEQUENTIAL_YAML)
    return await GraphCompiler().compile(definition)


def _registry(*executors):
    registry = NodeExecutorRegistry()
    for executor in executors:
        registry.register(executor)
    return registry


def _read_jsonl(path: Path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _read_all(directory: Path, pattern: str):
    """读取目录下全部匹配文件的记录（跨天/轮转拆分多文件时仍可聚合）。"""
    records = []
    for path in sorted(directory.glob(pattern)):
        records.extend(_read_jsonl(path))
    return records


class _StubTriggerExecutor:
    @property
    def node_type(self):
        return NodeType.TRIGGER

    async def execute(self, ctx):
        return NodeExecutionResult(
            success=True, output_name="trigger", output_value={"started": True}
        )


class _StubDataSourceExecutor:
    @property
    def node_type(self):
        return NodeType.DATA_SOURCE

    async def execute(self, ctx):
        return NodeExecutionResult(
            success=True,
            output_name=ctx.node_definition.effective_output_name,
            output_value={"rows": [1, 2, 3]},
        )


class _StubAgentExecutor:
    @property
    def node_type(self):
        return NodeType.AGENT

    async def execute(self, ctx):
        return NodeExecutionResult(
            success=True,
            output_name=ctx.node_definition.effective_output_name,
            output_value={"result": "agent_done"},
        )


class _StubToolExecutor:
    @property
    def node_type(self):
        return NodeType.TOOL

    async def execute(self, ctx):
        return NodeExecutionResult(
            success=True,
            output_name=ctx.node_definition.effective_output_name,
            output_value={"report": "done"},
        )


class _RaisingAgentExecutor:
    @property
    def node_type(self):
        return NodeType.AGENT

    async def execute(self, ctx):
        raise RuntimeError("模拟节点执行失败")


class _SlowAgentExecutor:
    @property
    def node_type(self):
        return NodeType.AGENT

    async def execute(self, ctx):
        await asyncio.sleep(1.5)
        return NodeExecutionResult(
            success=True,
            output_name=ctx.node_definition.effective_output_name,
            output_value={},
        )


def _full_registry():
    return _registry(
        _StubTriggerExecutor(),
        _StubDataSourceExecutor(),
        _StubAgentExecutor(),
        _StubToolExecutor(),
    )


class _RaisingRecorder:
    """T6.4 守护桩：全部采集方法抛异常，验证采集故障不误伤执行（INV-01 红线）。"""

    def begin_run(self, *args, **kwargs):
        raise RuntimeError("recorder 采集通道故障")

    def end_run(self, *args, **kwargs):
        raise RuntimeError("recorder 采集通道故障")

    def begin_node(self, *args, **kwargs):
        raise RuntimeError("recorder 采集通道故障")

    def end_node(self, *args, **kwargs):
        raise RuntimeError("recorder 采集通道故障")

    def record_node_failure(self, *args, **kwargs):
        raise RuntimeError("recorder 采集通道故障")

    def close(self):
        pass


class _StubEventRepository:
    """服务层事件仓储桩：仅记录 append 事件。"""

    def __init__(self):
        self.events = []

    async def append(self, event):
        self.events.append(event)
        return len(self.events)

    async def list_for_instance(self, instance_id, include_voided=True):
        return [e for e in self.events if e.instance_id == instance_id]


# =============================================================================
# 端到端成功路径（T7.6：节点级 + 工作流级落盘、全行一致、按 run_id 聚合）
# =============================================================================

class TestEndToEndSuccess:
    async def test_node_and_workflow_records_written(self, tmp_path):
        settings = _make_settings(tmp_path)
        compiled = await _compile_sequential()
        recorder = WorkflowRunLogger(
            run_id="run-e2e",
            trace_id="trace-e2e",
            tenant_id="tenant-x",
            business_category="manual",
            workflow_id="sequential_four_nodes",
            definition_version=1,
            settings=settings,
        )
        scheduler = Scheduler(_full_registry(), run_log_recorder=recorder)
        initial_state = SharedState(
            instance_id="run-e2e",
            node_outputs={},
            current_node_id=compiled.entry_node_id,
        )

        recorder.begin_run()
        final_state = await scheduler.execute(
            compiled, initial_state, "run-e2e", "trace-e2e"
        )
        recorder.end_run(
            STATUS_SUCCEEDED,
            finished_at=datetime.now(timezone.utc),
            duration_ms=42,
            node_summary=recorder.node_summary,
        )
        recorder.close()

        assert final_state.node_outputs.get("report") == {"report": "done"}

        base = tmp_path / "logs" / "tenant-x" / "manual"
        node_records = _read_all(base, "node_run_manual_*.jsonl")
        wf_records = _read_all(base, "workflow_run_manual_*.jsonl")

        # 节点级：4 节点 ×（running + succeeded）= 8 条，级别全 INFO
        assert len(node_records) == 8
        assert [r["status"] for r in node_records].count(STATUS_RUNNING) == 4
        assert [r["status"] for r in node_records].count(STATUS_SUCCEEDED) == 4
        assert {r["node_id"] for r in node_records} == {
            "trigger_manual",
            "read_dataset",
            "analyze_data",
            "build_report",
        }
        assert {r["node_type"] for r in node_records} == {
            "trigger",
            "data_source",
            "agent",
            "tool",
        }
        assert all(r["record_type"] == RECORD_TYPE_NODE_RUN for r in node_records)
        assert all(r["level"] == LEVEL_INFO for r in node_records)
        assert sorted({r["round_index"] for r in node_records}) == [0, 1, 2, 3]
        # 输入快照随执行推进：首节点空、后续包含上游命名输出
        assert node_records[0]["input"] == {}
        assert node_records[2]["input"].get("trigger") == {"started": True}

        # 工作流级：running → succeeded（E-9：开始行不伪造 finished_at）
        assert [r["record_type"] for r in wf_records] == [
            RECORD_TYPE_WORKFLOW_RUN,
            RECORD_TYPE_WORKFLOW_RUN,
        ]
        assert wf_records[0]["status"] == STATUS_RUNNING
        assert "finished_at" not in wf_records[0]
        assert wf_records[0]["level"] == LEVEL_INFO
        assert wf_records[1]["status"] == STATUS_SUCCEEDED
        assert wf_records[1]["duration_ms"] == 42
        assert wf_records[1]["node_summary"] == {
            "total": 4,
            "succeeded": 4,
            "failed": 0,
            "timeout": 0,
        }
        assert wf_records[1]["workflow_id"] == "sequential_four_nodes"
        assert wf_records[1]["definition_version"] == 1

        # run_id/trace_id 全行一致，可按 run_id 聚合节点 + 工作流日志（AC-21/22）
        for record in node_records + wf_records:
            assert record["run_id"] == "run-e2e"
            assert record["trace_id"] == "trace-e2e"
        aggregated = [r for r in node_records + wf_records if r["run_id"] == "run-e2e"]
        assert len(aggregated) == 10


# =============================================================================
# 失败路径（run_and_record 接线 + 事件不变 INV-08 + 错误三元组/堆栈）
# =============================================================================

class TestFailurePath:
    async def test_failure_run_and_record_wiring(self, tmp_path):
        settings = _make_settings(tmp_path)
        compiled = await _compile_sequential()
        registry = _registry(
            _StubTriggerExecutor(),
            _StubDataSourceExecutor(),
            _RaisingAgentExecutor(),
            _StubToolExecutor(),
        )
        recorder = WorkflowRunLogger(
            run_id="run-fail",
            trace_id="trace-fail",
            tenant_id="tenant-x",
            business_category="manual",
            workflow_id="sequential_four_nodes",
            settings=settings,
        )
        instance_registry = execution_instance_router.InstanceRegistry()
        rt = execution_instance_router.InstanceRuntime(
            "run-fail", "sequential_four_nodes"
        )
        instance_registry.register(rt)
        callback = execution_instance_router.NodeEventCallbackImpl(instance_registry)
        scheduler = Scheduler(
            registry, event_callback=callback, run_log_recorder=recorder
        )
        initial_state = SharedState(
            instance_id="run-fail",
            node_outputs={},
            current_node_id=compiled.entry_node_id,
        )

        await execution_instance_router.run_and_record(
            scheduler,
            compiled,
            initial_state,
            "run-fail",
            "trace-fail",
            instance_registry,
            run_log_recorder=recorder,
        )

        assert rt.status == "failed"
        assert rt.finished_at is not None

        # 事件序列（INV-08 不变）：STARTED → 2×(NS,NC) → NS → NODE_FAILED → INSTANCE_FAILED
        events = instance_registry.get_events("run-fail")
        assert [e["event_type"] for e in events] == [
            "INSTANCE_STARTED",
            "NODE_STARTED",
            "NODE_COMPLETED",
            "NODE_STARTED",
            "NODE_COMPLETED",
            "NODE_STARTED",
            "NODE_FAILED",
            "INSTANCE_FAILED",
        ]
        assert all(e["payload"] == {} for e in events[:6])
        assert events[6]["payload"] == {"error": "模拟节点执行失败"}
        assert events[7]["payload"] == {"error": "模拟节点执行失败"}

        base = tmp_path / "logs" / "tenant-x" / "manual"
        node_records = _read_all(base, "node_run_manual_*.jsonl")
        wf_records = _read_all(base, "workflow_run_manual_*.jsonl")

        # 节点级：2 节点成功（running+succeeded×2）+ 失败节点（running + failed）
        assert len(node_records) == 6
        failed_records = [r for r in node_records if r["status"] == STATUS_FAILED]
        assert len(failed_records) == 1
        failed = failed_records[0]
        assert failed["node_id"] == "analyze_data"
        assert failed["level"] == LEVEL_ERROR
        assert failed["error"]["error_type"] == "RuntimeError"
        assert failed["error"]["error_code"] == WF_NODE_EXECUTION_FAILED
        assert "模拟节点执行失败" in failed["error"]["error_message"]
        assert "RuntimeError" in failed["error"]["error_stack"]

        # 工作流级：running → failed（ERROR；error_code 回退 WF_NODE_EXECUTION_FAILED）
        assert wf_records[0]["status"] == STATUS_RUNNING
        assert wf_records[1]["status"] == STATUS_FAILED
        assert wf_records[1]["level"] == LEVEL_ERROR
        assert wf_records[1]["error"]["error_code"] == WF_NODE_EXECUTION_FAILED
        assert wf_records[1]["error"]["error_message"] == "模拟节点执行失败"
        for record in node_records + wf_records:
            assert record["run_id"] == "run-fail"
            assert record["trace_id"] == "trace-fail"


# =============================================================================
# 超时路径（timeout 状态区分 + NODE_TIMEOUT + 时长补算）
# =============================================================================

class TestTimeoutPath:
    async def test_timeout_run_records_timeout_status(self, tmp_path):
        settings = _make_settings(tmp_path)
        definition = YamlWorkflowParser().parse(_TIMEOUT_YAML)
        compiled = await GraphCompiler().compile(definition)
        registry = _registry(_StubTriggerExecutor(), _SlowAgentExecutor())
        recorder = WorkflowRunLogger(
            run_id="run-timeout",
            trace_id="trace-timeout",
            tenant_id="tenant-x",
            business_category="scheduled",
            workflow_id="timeout_probe",
            settings=settings,
        )
        instance_registry = execution_instance_router.InstanceRegistry()
        rt = execution_instance_router.InstanceRuntime("run-timeout", "timeout_probe")
        instance_registry.register(rt)
        callback = execution_instance_router.NodeEventCallbackImpl(instance_registry)
        scheduler = Scheduler(
            registry, event_callback=callback, run_log_recorder=recorder
        )
        initial_state = SharedState(
            instance_id="run-timeout",
            node_outputs={},
            current_node_id=compiled.entry_node_id,
        )

        await execution_instance_router.run_and_record(
            scheduler,
            compiled,
            initial_state,
            "run-timeout",
            "trace-timeout",
            instance_registry,
            run_log_recorder=recorder,
        )

        assert rt.status == "failed"
        events = instance_registry.get_events("run-timeout")
        assert [e["event_type"] for e in events] == [
            "INSTANCE_STARTED",
            "NODE_STARTED",
            "NODE_COMPLETED",
            "NODE_STARTED",
            "NODE_FAILED",
            "INSTANCE_FAILED",
        ]
        assert "执行超时" in events[4]["payload"]["error"]

        base = tmp_path / "logs" / "tenant-x" / "scheduled"
        node_records = _read_all(base, "node_run_scheduled_*.jsonl")
        wf_records = _read_all(base, "workflow_run_scheduled_*.jsonl")

        # 节点级：trigger 成功对 + 慢节点（running + timeout）
        assert len(node_records) == 4
        timeout_records = [
            r for r in node_records if r["status"] == STATUS_TIMEOUT
        ]
        assert len(timeout_records) == 1
        timeout_record = timeout_records[0]
        assert timeout_record["node_id"] == "slow_agent"
        assert timeout_record["level"] == LEVEL_ERROR
        assert timeout_record["error"]["error_code"] == NODE_TIMEOUT
        assert timeout_record["error"]["error_type"] == "TimeoutError"
        assert timeout_record["duration_ms"] >= 900

        # 工作流级：failed + NODE_TIMEOUT 同源透传
        assert wf_records[1]["status"] == STATUS_FAILED
        assert wf_records[1]["error"]["error_code"] == NODE_TIMEOUT
        assert "执行超时" in wf_records[1]["error"]["error_message"]
        for record in node_records + wf_records:
            assert record["run_id"] == "run-timeout"


# =============================================================================
# T6.4 守护：采集接线不误伤执行（成功输出 / 原始异常 / 超时异常逐字符等价）
# =============================================================================

class TestRecorderFailureGuard:
    async def test_success_outputs_identical_with_raising_recorder(self):
        compiled = await _compile_sequential()

        baseline_state = SharedState(
            instance_id="i-guard", node_outputs={}, current_node_id=compiled.entry_node_id
        )
        baseline = await Scheduler(_full_registry()).execute(
            compiled, baseline_state, "i-guard", "t-guard"
        )

        guarded_state = SharedState(
            instance_id="i-guard", node_outputs={}, current_node_id=compiled.entry_node_id
        )
        guarded = await Scheduler(
            _full_registry(), run_log_recorder=_RaisingRecorder()
        ).execute(compiled, guarded_state, "i-guard", "t-guard")

        assert guarded.node_outputs == baseline.node_outputs

    async def test_failure_exception_identical_with_raising_recorder(self):
        compiled = await _compile_sequential()
        registry = _registry(
            _StubTriggerExecutor(),
            _StubDataSourceExecutor(),
            _RaisingAgentExecutor(),
            _StubToolExecutor(),
        )

        with pytest.raises(RuntimeError) as baseline_exc:
            await Scheduler(registry).execute(
                compiled,
                SharedState(
                    instance_id="i-guard",
                    node_outputs={},
                    current_node_id=compiled.entry_node_id,
                ),
                "i-guard",
                "t-guard",
            )
        with pytest.raises(RuntimeError) as guarded_exc:
            await Scheduler(registry, run_log_recorder=_RaisingRecorder()).execute(
                compiled,
                SharedState(
                    instance_id="i-guard",
                    node_outputs={},
                    current_node_id=compiled.entry_node_id,
                ),
                "i-guard",
                "t-guard",
            )

        assert type(guarded_exc.value) is type(baseline_exc.value)
        assert str(guarded_exc.value) == str(baseline_exc.value)

    async def test_timeout_error_identical_with_raising_recorder(self):
        definition = YamlWorkflowParser().parse(_TIMEOUT_YAML)
        compiled = await GraphCompiler().compile(definition)
        registry = _registry(_StubTriggerExecutor(), _SlowAgentExecutor())

        with pytest.raises(NodeExecutionError) as baseline_exc:
            await Scheduler(registry).execute(
                compiled,
                SharedState(
                    instance_id="i-guard",
                    node_outputs={},
                    current_node_id=compiled.entry_node_id,
                ),
                "i-guard",
                "t-guard",
            )
        with pytest.raises(NodeExecutionError) as guarded_exc:
            await Scheduler(registry, run_log_recorder=_RaisingRecorder()).execute(
                compiled,
                SharedState(
                    instance_id="i-guard",
                    node_outputs={},
                    current_node_id=compiled.entry_node_id,
                ),
                "i-guard",
                "t-guard",
            )

        assert guarded_exc.value.code == baseline_exc.value.code == NODE_TIMEOUT
        assert guarded_exc.value.message == baseline_exc.value.message

    async def test_no_recorder_writes_nothing(self, tmp_path):
        """未注入 recorder：零采集、零文件写入（INV-D-12 / 惰性初始化）。"""
        compiled = await _compile_sequential()
        scheduler = Scheduler(_full_registry())
        await scheduler.execute(
            compiled,
            SharedState(
                instance_id="i-none",
                node_outputs={},
                current_node_id=compiled.entry_node_id,
            ),
            "i-none",
            "t-none",
        )
        assert list(tmp_path.rglob("*.jsonl")) == []


# =============================================================================
# 路由 start_instance 直连（request 注入 / 事件不变 / 默认异步通道端到端）
# =============================================================================

class TestRouterStartInstanceWiring:
    @staticmethod
    def _make_request(tenant_id=None):
        state = {"tenant_id": tenant_id} if tenant_id is not None else {}
        scope = {
            "type": "http",
            "method": "POST",
            "url": "http://testserver/instances",
            "headers": [],
            "query_string": b"",
            "state": state,
        }
        return Request(scope)

    async def test_events_and_records_end_to_end(self, tmp_path, monkeypatch):
        from orchestration_platform.configuration import settings as settings_module

        # 默认异步通道（log_async_enabled=True）端到端：close() 冲刷落盘
        tmp_settings = Settings(log_dir=str(tmp_path / "logs"), log_level="INFO")
        monkeypatch.setattr(settings_module, "get_settings", lambda: tmp_settings)
        monkeypatch.setattr(
            execution_instance_router, "_executor_registry", _full_registry()
        )

        body = execution_instance_router.StartInstanceBody(
            definition_id="sequential_four_nodes",
            definition_version=1,
            inputs={"topic": "集成测试"},
            yaml_text=SEQUENTIAL_YAML,
            trigger_type="scheduled",
        )
        request = self._make_request(tenant_id="tenant-x")

        result = await execution_instance_router.start_instance(body, request)
        instance_id = result["instance_id"]
        rt = execution_instance_router._instance_registry.get(instance_id)
        await rt.task

        assert rt.status == "succeeded"
        assert rt.tenant_id == "tenant-x"
        assert rt.business_category == "scheduled"

        # 三态事件序列与载荷不变（INV-08）
        events = execution_instance_router._instance_registry.get_events(instance_id)
        assert [e["event_type"] for e in events] == ["INSTANCE_STARTED"] + [
            "NODE_STARTED",
            "NODE_COMPLETED",
        ] * 4 + ["INSTANCE_COMPLETED"]
        assert all(e["payload"] == {} for e in events)

        base = tmp_path / "logs" / "tenant-x" / "scheduled"
        node_records = _read_all(base, "node_run_scheduled_*.jsonl")
        wf_records = _read_all(base, "workflow_run_scheduled_*.jsonl")
        assert len(node_records) == 8
        assert len(wf_records) == 2
        assert wf_records[1]["status"] == STATUS_SUCCEEDED
        assert wf_records[1]["node_summary"] == {
            "total": 4,
            "succeeded": 4,
            "failed": 0,
            "timeout": 0,
        }
        assert node_records[0]["input"] == {"topic": "集成测试"}
        for record in node_records + wf_records:
            assert record["run_id"] == instance_id

        execution_instance_router._instance_registry.remove(instance_id)

    async def test_tenant_and_category_fallbacks(self, tmp_path, monkeypatch):
        from orchestration_platform.configuration import settings as settings_module

        tmp_settings = Settings(
            log_dir=str(tmp_path / "logs"), log_level="INFO", log_async_enabled=False
        )
        monkeypatch.setattr(settings_module, "get_settings", lambda: tmp_settings)
        monkeypatch.setattr(
            execution_instance_router, "_executor_registry", _full_registry()
        )

        # 无 request.state.tenant_id → log_tenant_default（default）；
        # 无 body.trigger_type → TRIGGER 节点配置 trigger_type（manual）
        body = execution_instance_router.StartInstanceBody(
            definition_id="sequential_four_nodes",
            yaml_text=SEQUENTIAL_YAML,
        )
        request = self._make_request()

        result = await execution_instance_router.start_instance(body, request)
        instance_id = result["instance_id"]
        rt = execution_instance_router._instance_registry.get(instance_id)
        await rt.task

        assert rt.tenant_id is None
        assert rt.business_category == "manual"

        base = tmp_path / "logs" / "default" / "manual"
        wf_records = _read_all(base, "workflow_run_manual_*.jsonl")
        assert [r["status"] for r in wf_records] == [STATUS_RUNNING, STATUS_SUCCEEDED]
        assert wf_records[0]["tenant_id"] == "default"

        execution_instance_router._instance_registry.remove(instance_id)


# =============================================================================
# 业务类别三级回退（design 关键风险 ④）
# =============================================================================

class TestBusinessCategoryResolution:
    def test_body_trigger_type_wins(self):
        body = execution_instance_router.StartInstanceBody(
            definition_id="d", trigger_type="scheduled"
        )
        definition = YamlWorkflowParser().parse(SEQUENTIAL_YAML)
        assert (
            execution_instance_router._resolve_business_category(body, definition)
            == "scheduled"
        )

    def test_falls_back_to_trigger_node_config(self):
        body = execution_instance_router.StartInstanceBody(definition_id="d")
        definition = YamlWorkflowParser().parse(SEQUENTIAL_YAML)
        assert (
            execution_instance_router._resolve_business_category(body, definition)
            == "manual"
        )

    def test_falls_back_to_default_without_trigger_config(self):
        body = execution_instance_router.StartInstanceBody(definition_id="d")
        fake_definition = SimpleNamespace(
            nodes=[
                SimpleNamespace(
                    node_type=NodeType.TRIGGER,
                    configuration=SimpleNamespace(trigger_type=None),
                )
            ]
        )
        assert (
            execution_instance_router._resolve_business_category(
                body, fake_definition
            )
            == BUSINESS_CATEGORY_DEFAULT
        )

    def test_empty_nodes_falls_back_to_default(self):
        body = execution_instance_router.StartInstanceBody(definition_id="d")
        assert (
            execution_instance_router._resolve_business_category(
                body, SimpleNamespace(nodes=[])
            )
            == BUSINESS_CATEGORY_DEFAULT
        )


# =============================================================================
# InstanceRuntime 前后兼容构造（T7.2）
# =============================================================================

class TestInstanceRuntimeCompatibility:
    def test_legacy_positional_construction(self):
        rt = execution_instance_router.InstanceRuntime("inst-1", "def-1")
        assert rt.instance_id == "inst-1"
        assert rt.definition_id == "def-1"
        assert rt.tenant_id is None
        assert rt.business_category is None
        assert rt.status == "pending"

    def test_new_optional_fields(self):
        rt = execution_instance_router.InstanceRuntime(
            "inst-2", "def-2", tenant_id="t-1", business_category="manual"
        )
        assert rt.tenant_id == "t-1"
        assert rt.business_category == "manual"


# =============================================================================
# 跨天聚合（同一 run_id 记录按记录时间路由到不同日期文件，可聚合）
# =============================================================================

class TestCrossDayAggregation:
    def test_same_run_routes_by_date_and_aggregates(self, tmp_path):
        settings = _make_settings(tmp_path)
        sink = RunLogSink(settings)
        common = {
            "record_type": RECORD_TYPE_WORKFLOW_RUN,
            "run_id": "run-cross",
            "trace_id": "trace-cross",
            "tenant_id": "tenant-x",
            "business_category": "manual",
            "level": LEVEL_INFO,
            "started_at": "2026-10-23T10:00:00+00:00",
        }
        sink.emit(
            dict(common, timestamp="2026-10-23T10:00:00+00:00", status=STATUS_RUNNING)
        )
        sink.emit(
            dict(
                common,
                timestamp="2026-10-24T10:00:00+00:00",
                status=STATUS_SUCCEEDED,
                finished_at="2026-10-24T10:05:00+00:00",
                duration_ms=300000,
            )
        )
        sink.close()

        base = tmp_path / "logs" / "tenant-x" / "manual"
        files = sorted(base.glob("workflow_run_manual_*.jsonl"))
        assert [f.name for f in files] == [
            "workflow_run_manual_20261023.jsonl",
            "workflow_run_manual_20261024.jsonl",
        ]
        day1 = _read_jsonl(files[0])
        day2 = _read_jsonl(files[1])
        assert [r["run_id"] for r in day1 + day2] == ["run-cross", "run-cross"]
        assert day1[0]["status"] == STATUS_RUNNING
        assert day2[0]["status"] == STATUS_SUCCEEDED
        # 按 run_id 跨天聚合可还原完整生命周期
        aggregated = sorted(day1 + day2, key=lambda r: r["timestamp"])
        assert [r["status"] for r in aggregated] == [STATUS_RUNNING, STATUS_SUCCEEDED]


# =============================================================================
# T9.2 守护：Scheduler 四种调用形态兼容（位置参数红线，INV-04/INV-D-04）
# =============================================================================

class TestSchedulerConstructionCompat:
    async def test_four_construction_forms(self, tmp_path):
        compiled = await _compile_sequential()
        callback = execution_instance_router.NodeEventCallbackImpl(
            execution_instance_router.InstanceRegistry()
        )
        recorder = WorkflowRunLogger(
            run_id="r-compat",
            trace_id="t-compat",
            tenant_id="tenant-x",
            business_category="manual",
            settings=_make_settings(tmp_path),
        )
        forms = {
            "single_positional": lambda reg: Scheduler(reg),
            "three_positional": lambda reg: Scheduler(reg, None, None),
            "keyword_callback": lambda reg: Scheduler(reg, event_callback=callback),
            "keyword_registry_recorder": lambda reg: Scheduler(
                executor_registry=reg, run_log_recorder=recorder
            ),
        }

        reference = None
        for name, factory in forms.items():
            scheduler = factory(_full_registry())
            final = await scheduler.execute(
                compiled,
                SharedState(
                    instance_id="i-compat",
                    node_outputs={},
                    current_node_id=compiled.entry_node_id,
                ),
                "i-compat",
                "t-compat",
            )
            if reference is None:
                reference = final.node_outputs
            assert final.node_outputs == reference, name

        recorder.close()


# =============================================================================
# T9.4 守护复验：采集故障与事件回调并行互不干扰；NODE_FAILED 先事件后抛异常
# =============================================================================

class TestCollectorCallbackParallelism:
    async def test_raising_recorder_does_not_affect_events(self):
        definition = YamlWorkflowParser().parse(_TIMEOUT_YAML)
        compiled = await GraphCompiler().compile(definition)
        instance_registry = execution_instance_router.InstanceRegistry()
        rt = execution_instance_router.InstanceRuntime("i-par", "timeout_probe")
        instance_registry.register(rt)
        callback = execution_instance_router.NodeEventCallbackImpl(instance_registry)
        scheduler = Scheduler(
            _registry(_StubTriggerExecutor(), _SlowAgentExecutor()),
            event_callback=callback,
            run_log_recorder=_RaisingRecorder(),
        )

        with pytest.raises(NodeExecutionError) as exc_info:
            await scheduler.execute(
                compiled,
                SharedState(
                    instance_id="i-par",
                    node_outputs={},
                    current_node_id=compiled.entry_node_id,
                ),
                "i-par",
                "t-par",
            )

        assert exc_info.value.code == NODE_TIMEOUT
        # 采集通道故障不影响事件回调；NODE_FAILED 先发事件、后抛 NodeExecutionError
        events = instance_registry.get_events("i-par")
        assert [e["event_type"] for e in events] == [
            "NODE_STARTED",
            "NODE_COMPLETED",
            "NODE_STARTED",
            "NODE_FAILED",
        ]
        assert "执行超时" in events[3]["payload"]["error"]


# =============================================================================
# T9.5 守护：NodeEventCallback Protocol 不可扩展（runtime_checkable 兼容红线）
# =============================================================================

class TestEventCallbackProtocolGuard:
    def test_protocol_not_extended_and_impl_satisfies(self):
        from orchestration_platform.engine.scheduler import NodeEventCallback

        callback = execution_instance_router.NodeEventCallbackImpl(
            execution_instance_router.InstanceRegistry()
        )
        # 结构检查仍通过（runtime_checkable）
        assert isinstance(callback, NodeEventCallback)
        # Protocol 仅保留既有三个方法，未追加任何日志相关方法
        assert not hasattr(NodeEventCallback, "on_run_log")
        assert not hasattr(NodeEventCallback, "on_node_record")
        assert not hasattr(NodeEventCallback, "on_log_record")


# =============================================================================
# 服务层路径（T7.5：双路径同源产出工作流级记录；未注入 recorder 零节点级文件）
# =============================================================================

class TestServiceStartInstanceWiring:
    async def test_service_path_records_workflow_run(self, tmp_path, monkeypatch):
        from orchestration_platform.application.execution_instance_service import (
            ExecutionInstanceService,
        )
        from orchestration_platform.configuration import settings as settings_module

        tmp_settings = Settings(
            log_dir=str(tmp_path / "logs"), log_level="INFO", log_async_enabled=False
        )
        monkeypatch.setattr(settings_module, "get_settings", lambda: tmp_settings)

        compiled = await _compile_sequential()
        scheduler = Scheduler(_full_registry())
        event_repo = _StubEventRepository()
        service = ExecutionInstanceService(
            workflow_repo=MagicMock(),
            checkpoint_repo=MagicMock(),
            event_repo=event_repo,
            scheduler=scheduler,
        )
        request = StartInstanceRequest(definition_id="sequential_four_nodes")
        instance = await service.start_instance(request, compiled)

        assert instance.status == InstanceStatus.SUCCEEDED
        assert instance.outputs.get("report") == {"report": "done"}
        assert [e.event_type for e in event_repo.events] == [EventType.INSTANCE_CREATED]

        base = tmp_path / "logs" / "default" / "manual"
        wf_records = _read_all(base, "workflow_run_manual_*.jsonl")
        assert [r["status"] for r in wf_records] == [STATUS_RUNNING, STATUS_SUCCEEDED]
        for record in wf_records:
            assert record["run_id"] == instance.instance_id
            assert record["trace_id"] == instance.trace_id

        # Scheduler 未注入 recorder：无节点级采集，不伪造 node_summary（INV-D-12）
        assert "node_summary" not in wf_records[1]
        assert list((tmp_path / "logs").rglob("node_run_*.jsonl")) == []
