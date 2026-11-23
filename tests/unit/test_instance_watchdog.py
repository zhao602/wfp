# =============================================================================
# M3 实例级看门狗测试（tests/unit/test_instance_watchdog.py）
#
# 职责：验证 P5 实例级超时（roadmap design D3）：
#       wait_for 上界两模式同点生效；超时 → failed 终态 + WF_INSTANCE_TIMEOUT
#       （非 cancelled、非 timeout_terminated——终态集合冻结）；超时不重抛
#       （正常业务结局，作业照常 complete）；取消与超时严格分流
#       （CancelledError BaseException 系 vs TimeoutError Exception 系）。
# 设计意图：
#   1. 亚秒上界经 INSTANCE_TIMEOUT_SECONDS 环境变量注入（字段 float 化
#      后向兼容）；get_settings lru_cache 用例级清理；
#   2. 慢调度器桩（execute 睡 30s）保证超时/取消窗口；
#   3. 生产缺省 1800s 上界不进测试（全量回归由既有套件承担）。
# =============================================================================

import asyncio

import pytest

from orchestration_platform.application.execution_runner import (
    InstanceRegistry,
    InstanceRuntime,
    run_and_record,
)
from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.errors import WF_INSTANCE_TIMEOUT


class _SlowScheduler:
    """执行期长睡眠调度器桩：保证超时/取消窗口内不自然完成。"""

    async def execute(
        self, compiled, initial_state, instance_id, trace_id,
        resume_checkpoint=None, tenant_id=None,
    ):
        await asyncio.sleep(30)
        return initial_state


class _FastScheduler:
    """立即完成调度器桩。"""

    async def execute(
        self, compiled, initial_state, instance_id, trace_id,
        resume_checkpoint=None, tenant_id=None,
    ):
        return initial_state


@pytest.fixture
def subsecond_watchdog(monkeypatch):
    """亚秒看门狗上界（0.05s）+ settings 缓存清理。"""
    monkeypatch.setenv("INSTANCE_TIMEOUT_SECONDS", "0.05")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def registry():
    return InstanceRegistry()


def _register(registry: InstanceRegistry, instance_id: str) -> None:
    registry.register(
        InstanceRuntime(instance_id=instance_id, definition_id="wf-m3-wd")
    )


# =============================================================================
# A. 超时分支：failed + WF_INSTANCE_TIMEOUT（design D3）
# =============================================================================


class TestTimeoutBranch:

    async def test_timeout_lands_failed_with_instance_timeout_code(
        self, registry, subsecond_watchdog
    ):
        """超时 → failed 终态 + INSTANCE_FAILED 事件载荷
        WF_INSTANCE_TIMEOUT；不重抛（超时是正常业务结局）。"""
        instance_id = "inst-m3wd-timeout"
        _register(registry, instance_id)

        # 不抛出：超时分支吞掉（作业照常 complete 的前提）
        await run_and_record(
            _SlowScheduler(),
            None,
            {"node_outputs": {}},
            instance_id,
            "trace-m3wd",
            registry,
        )

        rt = registry.get(instance_id)
        assert rt.status == "failed", "超时终态 = failed（非 cancelled）"
        assert rt.finished_at is not None
        types = [e["event_type"] for e in rt.events]
        assert "INSTANCE_STARTED" in types
        assert "INSTANCE_FAILED" in types
        failed_event = next(
            e for e in rt.events if e["event_type"] == "INSTANCE_FAILED"
        )
        assert failed_event["payload"]["error"] == WF_INSTANCE_TIMEOUT
        assert failed_event["payload"]["timeout_seconds"] == 0.05

    async def test_timeout_does_not_raise(self, registry, subsecond_watchdog):
        """超时分支不重抛：run_and_record 正常返回（等价 failed 分支）。"""
        instance_id = "inst-m3wd-noraise"
        _register(registry, instance_id)

        await asyncio.wait_for(
            run_and_record(
                _SlowScheduler(),
                None,
                {"node_outputs": {}},
                instance_id,
                "trace-m3wd",
                registry,
            ),
            timeout=5,
        )
        assert registry.get(instance_id).status == "failed"


# =============================================================================
# B. 取消/超时严格分流（无继承交集，design D3）
# =============================================================================


class TestCancelTimeoutDistinct:

    async def test_cancel_under_watchdog_still_cancelled(
        self, registry, monkeypatch
    ):
        """看门狗在位时外部取消：CancelledError re-raise + cancelled 终态
        （不被超时分支误吞；两异常族无交集）。"""
        monkeypatch.setenv("INSTANCE_TIMEOUT_SECONDS", "0.5")
        get_settings.cache_clear()
        try:
            instance_id = "inst-m3wd-cancel"
            _register(registry, instance_id)

            task = asyncio.create_task(
                run_and_record(
                    _SlowScheduler(),
                    None,
                    {"node_outputs": {}},
                    instance_id,
                    "trace-m3wd",
                    registry,
                )
            )
            await asyncio.sleep(0.02)  # 看门狗（0.5s）未到，取消先行
            task.cancel()

            with pytest.raises(asyncio.CancelledError):
                await task

            rt = registry.get(instance_id)
            assert rt.status == "cancelled", "取消终态 = cancelled（非 failed）"
            types = [e["event_type"] for e in rt.events]
            assert "INSTANCE_CANCELLED" in types
            assert "INSTANCE_FAILED" not in types
        finally:
            get_settings.cache_clear()


# =============================================================================
# C. 缺省无干扰（快速完成路径）
# =============================================================================


class TestFastCompletionUnaffected:

    async def test_fast_completion_succeeds_under_default_watchdog(
        self, registry
    ):
        """缺省上界（1800s）下快速完成：succeeded（回归口径）。"""
        instance_id = "inst-m3wd-fast"
        _register(registry, instance_id)

        await run_and_record(
            _FastScheduler(),
            None,
            {"node_outputs": {}},
            instance_id,
            "trace-m3wd",
            registry,
        )

        rt = registry.get(instance_id)
        assert rt.status == "succeeded"
