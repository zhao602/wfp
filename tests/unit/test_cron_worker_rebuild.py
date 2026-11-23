# =============================================================================
# M3 cron 定义版本传递链测试
# （tests/unit/test_cron_worker_rebuild.py）
#
# 职责：锁 P5/执行链 cron 仓储定义流的版本钉扎（roadmap design D8）：
#       cron fire（仓储定义编译，唯一不依赖注册表解析的路径）→
#       ExecutionJob.definition_version（compiled 钉版本，submitter 既有
#       落点）→ worker run_claimed_job 仓储重建按 (definition_id,
#       definition_version) 精确取版——fire 与认领之间定义升版时执行
#       fire 时刻版本（非 latest）。
# 设计意图：
#   1. 版本化仓储桩（get 调用记录 + 版本表驱动）——钉版本断言可精确；
#   2. fire→认领窗口升版（v2 fire → v3 入库 → 重建仍取 v2）——
#      design D8 关键场景；
#   3. cron 流不带 yaml_text（仓储定义流；重建走仓储钉版本取回）。
# =============================================================================

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

import orchestration_platform.worker.execution_worker as worker_mod
from orchestration_platform.api.application_factory import build_cron_fire_callback
from orchestration_platform.api.routers import execution_instance_router as inst_router
from orchestration_platform.application.execution_runner import (
    ExecutionSubmitter,
    InstanceRegistry,
)
from orchestration_platform.application.schedule.models import TriggerSchedule
from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.models.execution_job import ExecutionJob
from orchestration_platform.domain.models.enums import NodeType
from orchestration_platform.engine.node_executor_registry import (
    NodeExecutorRegistry,
)
from orchestration_platform.engine.nodes.base import NodeExecutionResult
from orchestration_platform.engine.nodes.trigger_node_executor import (
    TriggerNodeExecutor,
)
from orchestration_platform.workflow.yaml_workflow_parser import YamlWorkflowParser

_FOUR_NODE_YAML = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "valid_workflow_definitions"
    / "valid_sequential_four_nodes.yaml"
).read_text(encoding="utf-8")

_WF_ID = "sequential_four_nodes"


def _definition_at_version(version: int):
    """四节点 fixture 解析结果，definition_version 覆写为指定版。"""
    text = _FOUR_NODE_YAML.replace(
        "definition_version: 1", f"definition_version: {version}"
    )
    definition = YamlWorkflowParser().parse(text)
    definition.definition_version = version
    return definition


class _VersionedRepo:
    """版本化工作流仓储桩：版本表驱动 + get 调用记录（钉版本断言点）。"""

    def __init__(self, versions: dict) -> None:
        self._versions = versions
        self.get_calls: list = []

    async def get(self, definition_id: str, version=None):
        self.get_calls.append((definition_id, version))
        if version is None:
            version = max(self._versions)
        return self._versions[version]


class _CaptureQueue:
    """入队捕获队列（cron fire 作业断言点）。"""

    def __init__(self) -> None:
        self.jobs: list = []

    async def enqueue(self, job):
        self.jobs.append(job)
        return job

    async def claim(self, worker_id, lease_ttl_seconds=None):
        return None

    async def take_over_expired(self, worker_id, lease_ttl_seconds=None):
        return None

    async def heartbeat(self, job_id, worker_id):
        return True

    async def complete(self, job_id, worker_id):
        return True

    async def fail(self, job_id, worker_id, reason=""):
        return True

    async def get(self, job_id):
        return None


def _stub_executor_registry() -> NodeExecutorRegistry:
    registry = NodeExecutorRegistry()
    registry.register(TriggerNodeExecutor())

    class _Noop:
        @property
        def node_type(self) -> NodeType:
            return NodeType.AGENT

        async def execute(self, context) -> NodeExecutionResult:
            return NodeExecutionResult(success=True)

    registry.register(_Noop())
    return registry


@pytest.fixture
def worker_mode(monkeypatch):
    """worker 执行模式 + settings 缓存清理。"""
    monkeypatch.setenv("EXECUTION_MODE", "worker")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# =============================================================================
# A. cron fire → 作业钉版本（design D8 前半链）
# =============================================================================


class TestCronFirePinsVersion:

    async def test_fire_enqueues_job_with_compiled_version(
        self, worker_mode, monkeypatch
    ):
        """fire（worker 模式）入队作业携带 compiled.definition_version
        （仓储 latest）+ trigger_source=cron；不带 yaml_text（仓储定义流）。"""
        repo = _VersionedRepo({1: _definition_at_version(1),
                               2: _definition_at_version(2)})
        capture = _CaptureQueue()
        # monkeypatch 保证测试后还原（router._execution_submitter 与
        # execution_runner 同一对象的 M1 兼容缝不被本测试破坏）
        monkeypatch.setattr(
            inst_router,
            "_execution_submitter",
            ExecutionSubmitter(queue=capture),
        )
        callback = build_cron_fire_callback(repo)
        schedule = TriggerSchedule(
            workflow_id=_WF_ID,
            trigger_node_id="trigger_manual",
            schedule="0 0 * * *",
        )

        outcome = await callback(schedule, datetime.now(timezone.utc))

        assert outcome is not None
        assert len(capture.jobs) == 1
        job = capture.jobs[0]
        assert job.definition_id == _WF_ID
        assert job.definition_version == 2, "fire 时刻仓储 latest 版钉入作业"
        assert job.trigger_source == "cron"
        assert job.yaml_text is None, "仓储定义流：重建走仓储取版"
        # cron 回调经模块级注册表登记 rt，测试后摘除防泄漏
        inst_router._instance_registry.remove(outcome.instance_id)


# =============================================================================
# B. worker 重建按钉版本取回（design D8 后半链：窗口升版不漂移）
# =============================================================================


class TestWorkerRebuildPinnedVersion:

    async def test_rebuild_gets_pinned_version_not_latest(self, monkeypatch):
        """fire→认领窗口升版（v2 fire 后 v3 入库）：worker 重建按
        (definition_id, 2) 精确取版，compiled 版本不漂移到 v3。"""
        captured = {}

        async def _spy_run_and_record(
            scheduler, compiled, initial_state, instance_id, trace_id,
            registry, **kwargs,
        ):
            captured["compiled_version"] = getattr(
                compiled, "definition_version", None
            )
            registry.update_status(instance_id, "succeeded")
            return None

        monkeypatch.setattr(worker_mod, "run_and_record", _spy_run_and_record)

        repo = _VersionedRepo({1: _definition_at_version(1),
                               2: _definition_at_version(2)})
        job = ExecutionJob(
            job_id="job-cron-m3",
            instance_id="inst-cron-m3",
            definition_id=_WF_ID,
            definition_version=2,
            initial_inputs={},
            trigger_source="cron",
            trace_id="trace-cron-m3",
            idempotency_key="inst-cron-m3",
            yaml_text=None,  # 仓储定义流
        )

        # fire→认领窗口升版：v3 入库（latest 漂移源）
        repo._versions[3] = _definition_at_version(3)

        registry = InstanceRegistry()
        ok = await worker_mod.run_claimed_job(
            job,
            registry=registry,
            executor_registry=_stub_executor_registry(),
            checkpoint_repository=None,
            workflow_repository=repo,
        )
        assert ok is True
        assert repo.get_calls == [(_WF_ID, 2)], "钉版本取回（非 latest None）"
        assert captured["compiled_version"] == 2, "重建版本不漂移"

    async def test_yaml_text_path_skips_repository(self, monkeypatch):
        """对照：yaml_text 随作业携带时零仓储调用（fire 链外路径回归）。"""
        captured = {}

        async def _spy_run_and_record(
            scheduler, compiled, initial_state, instance_id, trace_id,
            registry, **kwargs,
        ):
            captured["called"] = True
            registry.update_status(instance_id, "succeeded")
            return None

        monkeypatch.setattr(worker_mod, "run_and_record", _spy_run_and_record)

        repo = _VersionedRepo({1: _definition_at_version(1)})
        job = ExecutionJob(
            job_id="job-cron-m3-y",
            instance_id="inst-cron-m3-y",
            definition_id=_WF_ID,
            definition_version=1,
            initial_inputs={},
            trigger_source="cron",
            trace_id="trace-cron-m3-y",
            idempotency_key="inst-cron-m3-y",
            yaml_text=_FOUR_NODE_YAML,
        )

        ok = await worker_mod.run_claimed_job(
            job,
            registry=InstanceRegistry(),
            executor_registry=_stub_executor_registry(),
            checkpoint_repository=None,
            workflow_repository=repo,
        )
        assert ok is True
        assert captured["called"] is True
        assert repo.get_calls == [], "yaml_text 路径不经仓储"
