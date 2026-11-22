# =============================================================================
# 检查点崩溃续跑单测（tests/unit/test_checkpoint_resume.py）
#
# 覆盖：arch_review_v2 任务 3.5（CHG-28；AC-02 崩溃续跑核心证据）
#       + 3.1 同轮幂等（CHG-15/DC-09）+ 3.2/3.4 接线与端点契约（卡 A1）。
# 分层：
#   A. 仓储层——local_file 同轮幂等 / 不可变保持 / v1_3 迁移与 PG 幂等静态核对；
#   B. 引擎层——检查点 next_node_id 真实后继 + resume_checkpoint 续跑语义；
#   C. 路由层——start_instance 检查点落盘（3.2 接线）+ recover 端点崩溃续跑（3.4）。
# =============================================================================

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from orchestration_platform.domain.errors import (
    STATE_CHECKPOINT_CORRUPTED,
    WF_NODE_EXECUTION_FAILED,
    DomainError,
)
from orchestration_platform.domain.models.checkpoint import Checkpoint
from orchestration_platform.domain.models.enums import NodeType
from orchestration_platform.domain.models.shared_state import SharedState
from orchestration_platform.engine.graph_compiler import GraphCompiler
from orchestration_platform.engine.node_executor_registry import NodeExecutorRegistry
from orchestration_platform.engine.nodes.base import (
    NodeExecutionResult,
)
from orchestration_platform.engine.nodes.trigger_node_executor import TriggerNodeExecutor
from orchestration_platform.engine.scheduler import Scheduler
from orchestration_platform.persistence.local_file.local_file_checkpoint_repository import (
    LocalFileCheckpointRepository,
)
from orchestration_platform.workflow.yaml_workflow_parser import YamlWorkflowParser

_FOUR_NODE_YAML = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "valid_workflow_definitions"
    / "valid_sequential_four_nodes.yaml"
).read_text(encoding="utf-8")

_MIGRATIONS_DIR = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "orchestration_platform"
    / "persistence"
    / "postgres"
    / "schema_migrations"
)


# =============================================================================
# 测试基建：计数桩执行器（可配置「首次执行失败」模拟节点崩溃）
# =============================================================================

class _CountingStubExecutor:
    """计数桩执行器：按 node_id 统计执行次数；指定节点首次执行失败（模拟崩溃）。"""

    def __init__(self, node_type: NodeType, fail_first_node_ids=None):
        self._node_type = node_type
        self._fail_first_node_ids = fail_first_node_ids or set()
        self.node_calls: dict = {}

    @property
    def node_type(self) -> NodeType:
        return self._node_type

    async def execute(self, context) -> NodeExecutionResult:
        node_id = context.node_definition.node_id
        self.node_calls[node_id] = self.node_calls.get(node_id, 0) + 1
        if node_id in self._fail_first_node_ids and self.node_calls[node_id] == 1:
            return NodeExecutionResult(
                success=False,
                error_code=WF_NODE_EXECUTION_FAILED,
                error_message="模拟节点崩溃（首次执行失败，恢复后重试）",
            )
        return NodeExecutionResult(
            success=True,
            output_name=getattr(
                context.node_definition, "effective_output_name", None
            ),
            output_value={"stub": node_id},
        )


def _build_registry(fail_first_agent: bool = False) -> NodeExecutorRegistry:
    """构造带计数桩的执行器注册表（trigger 用真实执行器）。"""
    registry = NodeExecutorRegistry()
    registry.register(TriggerNodeExecutor())
    registry.register(_CountingStubExecutor(NodeType.DATA_SOURCE))
    registry.register(
        _CountingStubExecutor(
            NodeType.AGENT,
            fail_first_node_ids={"analyze_data"} if fail_first_agent else None,
        )
    )
    registry.register(_CountingStubExecutor(NodeType.TOOL))
    return registry


async def _compile_four_nodes():
    definition = YamlWorkflowParser().parse(_FOUR_NODE_YAML)
    return await GraphCompiler().compile(definition)


def _mk_checkpoint(instance_id, round_index, checkpoint_id,
                   completed=None, next_node_id=None) -> Checkpoint:
    return Checkpoint(
        checkpoint_id=checkpoint_id,
        instance_id=instance_id,
        completed_node_id=completed,
        next_node_id=next_node_id,
        state_snapshot=SharedState(instance_id=instance_id),
        round_index=round_index,
    )


# =============================================================================
# A. 仓储层：同轮幂等 + 不可变保持 + v1_3/PG 静态核对（3.1）
# =============================================================================

class TestSameRoundIdempotency:
    """同轮幂等（CHG-15/DC-09）：重试/恢复场景同轮重复写是合法重入。"""

    async def test_local_file_same_round_append_idempotent(self, tmp_path):
        repo = LocalFileCheckpointRepository(storage_root=str(tmp_path))
        first_id = await repo.append(_mk_checkpoint("inst-a", 1, "cp_a1"))
        assert first_id == "cp_a1"

        returned = await repo.append(_mk_checkpoint("inst-a", 1, "cp_a2"))
        assert returned == "cp_a1", "同轮重复写应返回既有检查点标识，不抛异常"
        files = list((tmp_path / "checkpoints" / "inst-a").glob("*.json"))
        assert len(files) == 1, "同轮重复写不应产生新文件"

    async def test_local_file_same_checkpoint_id_still_immutable(self, tmp_path):
        repo = LocalFileCheckpointRepository(storage_root=str(tmp_path))
        cp = _mk_checkpoint("inst-b", 0, "cp_b0")
        await repo.append(cp)

        with pytest.raises(DomainError) as exc_info:
            await repo.append(cp)
        assert exc_info.value.code == STATE_CHECKPOINT_CORRUPTED, (
            "同一 checkpoint_id 重复追加的不可变约束保持（既有路径零影响）"
        )

    async def test_local_file_different_rounds_append_normally(self, tmp_path):
        repo = LocalFileCheckpointRepository(storage_root=str(tmp_path))
        for round_index in (0, 1, 2):
            await repo.append(
                _mk_checkpoint("inst-c", round_index, f"cp_c{round_index}")
            )
        files = list((tmp_path / "checkpoints" / "inst-c").glob("*.json"))
        assert len(files) == 3, "不同轮次写入不受同轮幂等分支影响（既有路径）"

        latest = await repo.latest_for_instance("inst-c")
        assert latest is not None and latest.round_index == 2

    def test_v1_3_migration_idempotent_replay(self):
        """v1_3 迁移幂等重放（测试口径，3.8 门禁项；PG 实库行为 N 移交）。"""
        sql = (_MIGRATIONS_DIR / "v1_3_checkpoint_unique.sql").read_text(
            encoding="utf-8"
        )
        assert "VALUES ('1.3'" in sql, "版本登记 1.3"
        assert "ON CONFLICT (version) DO NOTHING" in sql, "版本登记幂等（E10 范式）"
        assert (
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_checkpoint_instance_round"
            in sql
        ), "唯一索引幂等创建"
        assert "ON checkpoint (instance_id, round_index)" in sql, "同轮唯一索引目标列"
        assert "is_available = FALSE" in sql, "存量重复行软去重（置不可用）"
        assert "DELETE FROM checkpoint" not in sql, "不物理删除（不可变追加写原则）"

        rollback = (_MIGRATIONS_DIR / "v1_3_rollback.sql").read_text(
            encoding="utf-8"
        )
        assert "DROP INDEX IF EXISTS uq_checkpoint_instance_round" in rollback
        assert "DELETE FROM schema_versions WHERE version = '1.3'" in rollback

    def test_postgres_append_uses_same_round_conflict_target(self):
        """PG append 同轮幂等静态核对（实库行为 N 移交）。"""
        import inspect

        from orchestration_platform.persistence.postgres.postgres_checkpoint_repository import (
            PostgresCheckpointRepository,
        )

        source = inspect.getsource(PostgresCheckpointRepository.append)
        assert (
            "ON CONFLICT (instance_id, round_index) DO NOTHING" in source
        ), "同轮冲突目标 + DO NOTHING"
        assert "RETURNING checkpoint_id" in source, "插入结果探测"
        assert "SELECT checkpoint_id FROM checkpoint" in source, (
            "冲突时回查既有标识（告警 + 返回，不抛异常）"
        )


# =============================================================================
# B. 引擎层：检查点位点语义 + resume_checkpoint 续跑（3.3）
# =============================================================================

class TestEngineResume:
    """引擎级续跑语义（CHG-08/09/10；AC-02 引擎侧证据）。"""

    async def test_checkpoint_chain_records_true_successor(self, tmp_path):
        """检查点链应记录真实后继（completed→next 相邻节点对，终态 next=None）。"""
        repo = LocalFileCheckpointRepository(storage_root=str(tmp_path))
        scheduler = Scheduler(
            executor_registry=_build_registry(), checkpoint_repository=repo
        )
        compiled = await _compile_four_nodes()
        await scheduler.execute(
            compiled,
            SharedState(instance_id="inst-chain", node_outputs={"topic": "t"}),
            "inst-chain",
            "trace-chain",
        )

        summaries = sorted(
            await repo.list_for_instance("inst-chain"),
            key=lambda s: s.round_index,
        )
        assert [s.round_index for s in summaries] == [0, 1, 2, 3]
        assert [(s.completed_node_id, s.next_node_id) for s in summaries] == [
            ("trigger_manual", "read_dataset"),
            ("read_dataset", "analyze_data"),
            ("analyze_data", "build_report"),
            ("build_report", None),
        ], "next_node_id 必须是真实后继，恢复才能从正确位点续跑"

    async def test_resume_from_mid_checkpoint_skips_completed_nodes(self, tmp_path):
        """从中间检查点续跑：位点前节点零重复执行、轮次续统计、同轮重写被幂等吸收。"""
        repo = LocalFileCheckpointRepository(storage_root=str(tmp_path))
        scheduler = Scheduler(
            executor_registry=_build_registry(), checkpoint_repository=repo
        )
        compiled = await _compile_four_nodes()
        await scheduler.execute(
            compiled,
            SharedState(instance_id="inst-resume", node_outputs={"topic": "t"}),
            "inst-resume",
            "trace-resume-1",
        )

        # 取 read_dataset 完成后的检查点（round=1）作为崩溃位点
        summaries = sorted(
            await repo.list_for_instance("inst-resume"),
            key=lambda s: s.round_index,
        )
        resume_point = await repo.get(summaries[1].checkpoint_id)
        assert resume_point is not None
        assert resume_point.completed_node_id == "read_dataset"
        assert resume_point.next_node_id == "analyze_data"

        # 上下文重建：新执行器注册表（新计数器）+ 新调度器（模拟 kill 后恢复）
        registry2 = _build_registry()
        scheduler2 = Scheduler(
            executor_registry=registry2, checkpoint_repository=repo
        )
        final_state = await scheduler2.execute(
            compiled,
            SharedState(instance_id="inst-resume"),
            "inst-resume",
            "trace-resume-2",
            resume_checkpoint=resume_point,
        )

        # 已成功节点（trigger/read_dataset）不重复产生副作用（AC-02 核心）
        data_stub = registry2.get_executor(NodeType.DATA_SOURCE)
        assert "read_dataset" not in data_stub.node_calls, (
            "已成功的数据源节点不得重复执行（副作用计数为 0）"
        )

        # 位点后节点正常执行
        agent_stub = registry2.get_executor(NodeType.AGENT)
        tool_stub = registry2.get_executor(NodeType.TOOL)
        assert agent_stub.node_calls.get("analyze_data", 0) == 1
        assert tool_stub.node_calls.get("build_report", 0) == 1

        # 轮次续统计：全局检查点恰为 0..3，同轮重写被幂等吸收、无重复行
        summaries_after = sorted(
            await repo.list_for_instance("inst-resume"),
            key=lambda s: s.round_index,
        )
        assert [s.round_index for s in summaries_after] == [0, 1, 2, 3], (
            "续跑轮次从检查点 round_index + 1 续统计，同轮不产生重复检查点"
        )
        assert final_state.instance_id == "inst-resume"

    async def test_execute_without_resume_matches_baseline(self):
        """resume_checkpoint=None：行为与现状完全一致（HC-01 既有调用零破坏）。"""
        registry = _build_registry()
        scheduler = Scheduler(executor_registry=registry)
        compiled = await _compile_four_nodes()
        final_state = await scheduler.execute(
            compiled,
            SharedState(instance_id="inst-base", node_outputs={"topic": "t"}),
            "inst-base",
            "trace-base",
        )

        for node_id in ("read_dataset", "analyze_data", "build_report"):
            assert any(
                node_id in executor.node_calls
                for executor in (
                    registry.get_executor(NodeType.DATA_SOURCE),
                    registry.get_executor(NodeType.AGENT),
                    registry.get_executor(NodeType.TOOL),
                )
            ), f"基线路径节点 {node_id} 应正常执行"
        assert final_state.instance_id == "inst-base"

    async def test_resume_with_terminal_checkpoint_returns_immediately(self, tmp_path):
        """终态检查点（next=None）续跑：无节点可执行，直接返回快照状态。"""
        repo = LocalFileCheckpointRepository(storage_root=str(tmp_path))
        registry = _build_registry()
        scheduler = Scheduler(executor_registry=registry, checkpoint_repository=repo)
        compiled = await _compile_four_nodes()
        await scheduler.execute(
            compiled,
            SharedState(instance_id="inst-term", node_outputs={"topic": "t"}),
            "inst-term",
            "trace-term",
        )

        summaries = sorted(
            await repo.list_for_instance("inst-term"),
            key=lambda s: s.round_index,
        )
        terminal = await repo.get(summaries[-1].checkpoint_id)
        assert terminal is not None and terminal.next_node_id is None

        # 上下文重建：新执行器注册表（新计数器）+ 复用调度器
        registry2 = _build_registry()
        scheduler2 = Scheduler(executor_registry=registry2, checkpoint_repository=repo)
        final_state = await scheduler2.execute(
            compiled,
            SharedState(instance_id="inst-term"),
            "inst-term",
            "trace-term-2",
            resume_checkpoint=terminal,
        )
        assert final_state.instance_id == "inst-term"
        for node_type in (NodeType.DATA_SOURCE, NodeType.AGENT, NodeType.TOOL):
            assert registry2.get_executor(node_type).node_calls == {}, (
                "终态检查点续跑不得执行任何节点"
            )


# =============================================================================
# C. 路由层：3.2 接线（检查点实际落盘）+ 3.4 recover 端点契约（卡 A1）
# =============================================================================

@pytest.fixture
def routed_storage_root(tmp_path, monkeypatch):
    """将 LOCAL_STORAGE_ROOT 重定向至临时目录并刷新 settings 缓存。

    路由侧 _acquire_checkpoint_repository 经仓储工厂取 local_file 实现，
    存储根取 get_settings()（lru_cache），故须 cache_clear 使 env 覆盖生效。
    """
    root = tmp_path / "storage"
    root.mkdir()
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", str(root))

    from orchestration_platform.configuration.settings import get_settings

    get_settings.cache_clear()
    try:
        yield root
    finally:
        get_settings.cache_clear()


@pytest.fixture
def stubbed_router_executors():
    """热替换路由模块级执行器注册表中的 data_source/agent/tool 执行器。

    agent 桩配置 analyze_data 首次执行失败——模拟节点崩溃（kill），
    恢复后重试成功（transient 失败语义）。测试后恢复原执行器。
    """
    import orchestration_platform.api.routers.execution_instance_router as router_mod

    registry = router_mod._executor_registry
    originals = dict(registry._executors)
    data_stub = _CountingStubExecutor(NodeType.DATA_SOURCE)
    agent_stub = _CountingStubExecutor(
        NodeType.AGENT, fail_first_node_ids={"analyze_data"}
    )
    tool_stub = _CountingStubExecutor(NodeType.TOOL)
    registry.register(data_stub)
    registry.register(agent_stub)
    registry.register(tool_stub)
    try:
        yield {"data": data_stub, "agent": agent_stub, "tool": tool_stub}
    finally:
        registry._executors.clear()
        registry._executors.update(originals)


class TestRouterRecover:
    """路由级崩溃续跑（AC-02 端到端证据；卡 A1 契约）。"""

    def test_start_instance_wires_checkpoint_repository(self):
        """3.2 接线静态核对：Scheduler 构造经仓储工厂取检查点仓储。"""
        import inspect

        from orchestration_platform.api.routers.execution_instance_router import (
            start_instance,
        )

        source = inspect.getsource(start_instance)
        assert "checkpoint_repository=_acquire_checkpoint_repository()" in source

    async def test_recover_endpoint_resumes_after_node_failure(
        self, routed_storage_root, stubbed_router_executors
    ):
        """崩溃续跑端到端：节点崩溃 → recover → 从最近检查点续跑至完成。

        断言（AC-02）：
        - 从最近 checkpoint 的 next_node_id 续跑；
        - round_index 续统计（0..3 无重复）；
        - 已成功节点零重复副作用（副作用计数 + 事件序列双口径）；
        - 同轮重复写幂等（续跑检查点与崩溃前检查点无同轮冲突）。
        """
        import orchestration_platform.api.routers.execution_instance_router as router_mod
        from orchestration_platform.api.routers.execution_instance_router import (
            StartInstanceBody,
            recover_instance,
            start_instance,
        )

        request = SimpleNamespace(state=SimpleNamespace(tenant_id=None))
        body = StartInstanceBody(
            definition_id="sequential_four_nodes",
            yaml_text=_FOUR_NODE_YAML,
            inputs={"topic": "崩溃续跑"},
        )
        result = await start_instance(body, request)
        instance_id = result["instance_id"]

        rt = router_mod._instance_registry.get(instance_id)
        await rt.task  # 首次运行：analyze_data 首次执行失败（模拟崩溃）→ failed
        assert rt.status == "failed"

        # 崩溃前落盘检查点：trigger(0) + read_dataset(1)
        repo = LocalFileCheckpointRepository()
        summaries_before = sorted(
            await repo.list_for_instance(instance_id),
            key=lambda s: s.round_index,
        )
        assert [s.round_index for s in summaries_before] == [0, 1]
        latest_before = await repo.latest_for_instance(instance_id)
        assert latest_before is not None
        assert latest_before.completed_node_id == "read_dataset"
        assert latest_before.next_node_id == "analyze_data"

        # recover：从最近可用检查点续跑
        response = await recover_instance(instance_id)
        assert response["instance_id"] == instance_id
        assert (
            response["resumed_from_checkpoint_id"] == latest_before.checkpoint_id
        ), "续跑位点 = 崩溃前最近检查点（卡 A1 出参契约）"
        assert response["status"] == "running"

        rt = router_mod._instance_registry.get(instance_id)
        await rt.task  # 恢复运行：analyze_data 重试成功 → succeeded
        assert rt.status == "succeeded"

        # 已成功节点零重复副作用（AC-02）：崩溃前完成节点副作用计数恰为 1
        stubs = stubbed_router_executors
        assert stubs["data"].node_calls.get("read_dataset", 0) == 1, (
            "已成功的数据源节点不得重复执行"
        )
        assert stubs["agent"].node_calls.get("analyze_data", 0) == 2, (
            "崩溃节点：首次失败 + 恢复重试共 2 次"
        )
        assert stubs["tool"].node_calls.get("build_report", 0) == 1

        # 轮次续统计：全局检查点恰为 0..3，无重复轮次（同轮重写幂等）
        summaries_after = sorted(
            await repo.list_for_instance(instance_id),
            key=lambda s: s.round_index,
        )
        assert [s.round_index for s in summaries_after] == [0, 1, 2, 3]

        # 事件序列断言：双段生命周期 + 已成功节点事件零重复
        events = router_mod._instance_registry.get_events(instance_id)
        event_types = [e["event_type"] for e in events]
        assert event_types.count("INSTANCE_STARTED") == 2
        assert event_types.count("INSTANCE_FAILED") == 1
        assert event_types.count("INSTANCE_COMPLETED") == 1
        assert event_types.index("INSTANCE_FAILED") < event_types.index(
            "INSTANCE_COMPLETED"
        )
        read_dataset_starts = [
            e
            for e in events
            if e["event_type"] == "NODE_STARTED" and e["node_id"] == "read_dataset"
        ]
        assert len(read_dataset_starts) == 1, "已成功节点的 NODE_STARTED 事件零重复"

    async def test_recover_endpoint_instance_not_found_404(self):
        """INSTANCE_NOT_FOUND → 404（卡 A1）。"""
        from orchestration_platform.api.routers.execution_instance_router import (
            recover_instance,
        )

        with pytest.raises(HTTPException) as exc_info:
            await recover_instance("no-such-instance")
        assert exc_info.value.status_code == 404

    async def test_recover_endpoint_without_checkpoints_500(self):
        """有上下文、无可用检查点 → STATE_CHECKPOINT_CORRUPTED → 500（卡 A1）。"""
        import orchestration_platform.api.routers.execution_instance_router as router_mod

        rt = router_mod.InstanceRuntime(instance_id="inst-empty", definition_id="d")
        rt.compiled = object()
        rt.trace_id = "trace-empty"
        router_mod._instance_registry.register(rt)
        try:
            with pytest.raises(HTTPException) as exc_info:
                await router_mod.recover_instance("inst-empty")
            assert exc_info.value.status_code == 500
            assert "无可用检查点" in str(exc_info.value.detail)
        finally:
            router_mod._instance_registry.remove("inst-empty")

    async def test_recover_endpoint_running_instance_409(self):
        """实例仍在运行时禁止恢复（防止双任务并发副作用）。"""
        import orchestration_platform.api.routers.execution_instance_router as router_mod

        rt = router_mod.InstanceRuntime(instance_id="inst-running", definition_id="d")
        rt.compiled = object()

        async def _never():
            await asyncio.sleep(30)

        rt.task = asyncio.create_task(_never())
        router_mod._instance_registry.register(rt)
        try:
            with pytest.raises(HTTPException) as exc_info:
                await router_mod.recover_instance("inst-running")
            assert exc_info.value.status_code == 409
        finally:
            rt.task.cancel()
            router_mod._instance_registry.remove("inst-running")
