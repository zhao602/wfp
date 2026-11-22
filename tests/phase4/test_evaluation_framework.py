# =============================================================================
# Agent 效能评估框架测试（tests/phase4/test_evaluation_framework.py）
#
# 职责：验证评估框架全部模块——模型、评分器、仓储、服务、运行器、回归、API。
# 设计意图（对应偏差修正 T182-T191 / 验收 M28）：
#   1. CLEAR 四维评分正确性与归一化；
#   2. 规则评分确定性（可复现）；
#   3. LLM 评分失败回退规则；
#   4. 本地仓储 CRUD 与 eval- 隔离；
#   5. 场景集服务上限校验；
#   6. 基线冻结与回归退化判定；
#   7. 评估运行器基线/回归全流程；
#   8. API 路由条件注册（evaluation_enabled）。
# =============================================================================

import asyncio
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

# 评估框架本身不依赖 evaluation_enabled 运行期开关（仅路由注册依赖），
# 故此处不强制设置环境变量，避免污染 get_settings 的 lru_cache。

from orchestration_platform.domain.errors import (
    EVAL_BASELINE_MISSING,
    EVAL_SCENARIO_INVALID,
    EVAL_TASK_INTERRUPTED,
    DomainError,
)
from orchestration_platform.domain.models.enums import (
    EvaluationTaskState,
    EvaluationType,
)
from orchestration_platform.governance.evaluation.abstractions.clear_scorer import (
    IClearScorer,
)
from orchestration_platform.governance.evaluation.abstractions.evaluation_repository import (
    IEvaluationRepository,
)
from orchestration_platform.governance.evaluation.abstractions.trajectory_scorer import (
    ITrajectoryScorer,
)
from orchestration_platform.governance.evaluation.baseline_store import BaselineStore
from orchestration_platform.governance.evaluation.clear_scorer import ClearScorer
from orchestration_platform.governance.evaluation.evaluation_repository_local import (
    LocalEvaluationRepository,
)
from orchestration_platform.governance.evaluation.evaluation_runner import (
    EvaluationRunner,
    StubTrajectoryExecutor,
)
from orchestration_platform.governance.evaluation.llm_trajectory_scorer import (
    LlmTrajectoryScorer,
)
from orchestration_platform.governance.evaluation.models import (
    AgentTrajectory,
    BaselineRecord,
    ClearScore,
    EvaluationMetrics,
    EvaluationScenario,
    EvaluationScenarioSet,
    EvaluationTask,
    TrajectoryScore,
)
from orchestration_platform.governance.evaluation.regression_reporter import (
    RegressionReporter,
)
from orchestration_platform.governance.evaluation.rule_based_trajectory_scorer import (
    RuleBasedTrajectoryScorer,
)
from orchestration_platform.governance.evaluation.scenario_set_service import (
    ScenarioSetService,
)


def _run_async(coro):
    """辅助：在同步测试中驱动协程至完成。

    评分器与本地仓储的协程均为纯计算/同步文件 I/O，不依赖事件循环调度，
    故直接以 coro.send 驱动协程树（await 经 yield from 自动穿透至最内层），
    避免 asyncio 事件循环创建（部分受限环境禁止 socketpair）。
    """
    try:
        while True:
            coro.send(None)
    except StopIteration as exc:
        return exc.value


def _utc_now():
    return datetime.now(timezone.utc)


def _make_scenario(
    scenario_id: str = "s1",
    set_id: str = "set1",
    prompt: str = "请总结文章",
    keywords=None,
    cost_ref: float = 0.10,
    latency_ref: int = 5000,
) -> EvaluationScenario:
    """构造测试场景。"""
    return EvaluationScenario(
        scenario_id=scenario_id,
        scenario_set_id=set_id,
        name="测试场景",
        description="",
        input_prompt=prompt,
        expected_keywords=keywords or ["总结"],
        metadata={
            "reference_cost": cost_ref,
            "reference_latency_ms": latency_ref,
        },
    )


def _make_trajectory(
    trajectory_id: str = "t1",
    scenario_id: str = "s1",
    task_id: str = "task1",
    output: str = "这是总结结果",
    cost: float = 0.05,
    latency_ms: int = 1000,
    success: bool = True,
    steps=None,
) -> AgentTrajectory:
    """构造测试轨迹。"""
    return AgentTrajectory(
        trajectory_id=trajectory_id,
        scenario_id=scenario_id,
        task_id=task_id,
        steps=steps or [{"action": "generate", "output": output}],
        final_output=output,
        total_cost=cost,
        total_latency_ms=latency_ms,
        tool_call_count=1,
        success=success,
        completed_at=_utc_now(),
    )


# =============================================================================
# 模型测试
# =============================================================================

class TestEvaluationModels(unittest.TestCase):
    """评估领域模型测试。"""

    def test_clear_score_defaults_in_range(self):
        """CLEAR 评分默认值在 [0, 1]。"""
        score = ClearScore()
        self.assertGreaterEqual(score.policy_compliance, 0.0)
        self.assertLessEqual(score.policy_compliance, 1.0)
        self.assertEqual(score.policy_compliance, 1.0)

    def test_scenario_set_creation(self):
        """场景集创建。"""
        scenario = _make_scenario()
        ss = EvaluationScenarioSet(
            set_id="set1", name="测试集", tenant_id="t1", scenarios=[scenario]
        )
        self.assertEqual(len(ss.scenarios), 1)
        self.assertEqual(ss.tenant_id, "t1")

    def test_evaluation_task_default_state(self):
        """评估任务默认状态为 PENDING。"""
        task = EvaluationTask(
            task_id="task1",
            scenario_set_id="set1",
            tenant_id="t1",
            evaluation_type=EvaluationType.BASELINE,
        )
        self.assertEqual(task.state, EvaluationTaskState.PENDING)


# =============================================================================
# CLEAR 评分器测试
# =============================================================================

class TestClearScorer(unittest.TestCase):
    """CLEAR 四维评分器测试。"""

    def setUp(self):
        self.scorer = ClearScorer()

    def test_perfect_trajectory_high_score(self):
        """完美轨迹（零成本、零延迟、命中关键词）得高分。"""
        scenario = _make_scenario(keywords=["总结"])
        traj = _make_trajectory(output="这是总结", cost=0.0, latency_ms=0)
        score = _run_async(self.scorer.score_clear(traj, scenario))
        self.assertGreater(score.overall, 0.9)
        self.assertEqual(score.cost_efficiency, 1.0)
        self.assertEqual(score.latency, 1.0)
        self.assertEqual(score.policy_compliance, 1.0)

    def test_high_cost_lowers_cost_efficiency(self):
        """高成本降低成本效率分。"""
        scenario = _make_scenario(cost_ref=0.10)
        traj_low = _make_trajectory(cost=0.05)
        traj_high = _make_trajectory(cost=1.0)
        s_low = _run_async(self.scorer.score_clear(traj_low, scenario))
        s_high = _run_async(self.scorer.score_clear(traj_high, scenario))
        self.assertGreater(s_low.cost_efficiency, s_high.cost_efficiency)

    def test_high_latency_lowers_latency_score(self):
        """高延迟降低延迟分。"""
        scenario = _make_scenario(latency_ref=5000)
        traj_fast = _make_trajectory(latency_ms=100)
        traj_slow = _make_trajectory(latency_ms=10000)
        s_fast = _run_async(self.scorer.score_clear(traj_fast, scenario))
        s_slow = _run_async(self.scorer.score_clear(traj_slow, scenario))
        self.assertGreater(s_fast.latency, s_slow.latency)

    def test_keyword_miss_lowers_reliability(self):
        """关键词未命中降低可靠性分。"""
        scenario = _make_scenario(keywords=["总结", "要点"])
        traj_hit = _make_trajectory(output="这是总结和要点")
        traj_miss = _make_trajectory(output="无关内容")
        s_hit = _run_async(self.scorer.score_clear(traj_hit, scenario))
        s_miss = _run_async(self.scorer.score_clear(traj_miss, scenario))
        self.assertGreater(s_hit.reliability, s_miss.reliability)

    def test_policy_violation_lowers_compliance(self):
        """策略违规降低合规分。"""
        scenario = _make_scenario()
        traj_ok = _make_trajectory(steps=[{"action": "ok"}])
        traj_bad = _make_trajectory(
            steps=[{"action": "x", "policy_violation": True}]
        )
        s_ok = _run_async(self.scorer.score_clear(traj_ok, scenario))
        s_bad = _run_async(self.scorer.score_clear(traj_bad, scenario))
        self.assertEqual(s_ok.policy_compliance, 1.0)
        self.assertLess(s_bad.policy_compliance, 1.0)

    def test_all_dimensions_in_unit_interval(self):
        """所有维度与 overall 在 [0, 1]。"""
        scenario = _make_scenario()
        traj = _make_trajectory(cost=0.5, latency_ms=8000)
        score = _run_async(self.scorer.score_clear(traj, scenario))
        for v in [
            score.cost_efficiency,
            score.reliability,
            score.latency,
            score.policy_compliance,
            score.overall,
        ]:
            self.assertGreaterEqual(v, 0.0)
            self.assertLessEqual(v, 1.0)

    def test_deterministic_same_input_same_output(self):
        """相同输入产出相同评分（确定性）。"""
        scenario = _make_scenario()
        traj = _make_trajectory(cost=0.05, latency_ms=1000)
        s1 = _run_async(self.scorer.score_clear(traj, scenario))
        s2 = _run_async(self.scorer.score_clear(traj, scenario))
        self.assertEqual(s1, s2)


# =============================================================================
# 规则轨迹评分器测试
# =============================================================================

class TestRuleBasedTrajectoryScorer(unittest.TestCase):
    """规则轨迹评分器测试。"""

    def setUp(self):
        self.scorer = RuleBasedTrajectoryScorer()

    def test_score_returns_trajectory_score(self):
        """评分返回 TrajectoryScore 且类型为 rule_based。"""
        scenario = _make_scenario()
        traj = _make_trajectory()
        result = _run_async(self.scorer.score(traj, scenario))
        self.assertIsInstance(result, TrajectoryScore)
        self.assertEqual(result.scorer_type, "rule_based")
        self.assertEqual(result.trajectory_id, traj.trajectory_id)

    def test_deterministic(self):
        """规则评分确定性。"""
        scenario = _make_scenario()
        traj = _make_trajectory()
        r1 = _run_async(self.scorer.score(traj, scenario))
        r2 = _run_async(self.scorer.score(traj, scenario))
        self.assertEqual(r1.clear_score, r2.clear_score)

    def test_implements_protocol(self):
        """实现 ITrajectoryScorer 协议。"""
        self.assertIsInstance(self.scorer, ITrajectoryScorer)


# =============================================================================
# LLM 轨迹评分器测试
# =============================================================================

class TestLlmTrajectoryScorer(unittest.TestCase):
    """LLM 轨迹评分器测试。"""

    def test_fallback_when_no_client(self):
        """无 LLM 客户端时回退规则评分。"""
        scorer = LlmTrajectoryScorer(llm_client=None)
        scenario = _make_scenario()
        traj = _make_trajectory()
        result = _run_async(scorer.score(traj, scenario))
        self.assertEqual(result.scorer_type, "rule_based")

    def test_llm_success_marks_llm_type(self):
        """LLM 成功时标记 llm 类型。"""
        llm = MagicMock()
        llm.complete = AsyncMock(
            return_value=json.dumps(
                {
                    "cost_efficiency": 0.8,
                    "reliability": 0.9,
                    "latency": 0.7,
                    "policy_compliance": 1.0,
                    "rationale": "LLM 评分",
                }
            )
        )
        scorer = LlmTrajectoryScorer(llm_client=llm)
        scenario = _make_scenario()
        traj = _make_trajectory()
        result = _run_async(scorer.score(traj, scenario))
        self.assertEqual(result.scorer_type, "llm")
        self.assertAlmostEqual(result.clear_score.cost_efficiency, 0.8)
        self.assertAlmostEqual(result.clear_score.reliability, 0.9)

    def test_llm_failure_falls_back_to_rule(self):
        """LLM 失败时回退规则评分。"""
        llm = MagicMock()
        llm.complete = AsyncMock(side_effect=RuntimeError("LLM 不可用"))
        scorer = LlmTrajectoryScorer(llm_client=llm)
        scenario = _make_scenario()
        traj = _make_trajectory()
        result = _run_async(scorer.score(traj, scenario))
        self.assertEqual(result.scorer_type, "rule_based")

    def test_llm_malformed_response_falls_back(self):
        """LLM 返回非法 JSON 时回退规则评分。"""
        llm = MagicMock()
        llm.complete = AsyncMock(return_value="这不是JSON")
        scorer = LlmTrajectoryScorer(llm_client=llm)
        scenario = _make_scenario()
        traj = _make_trajectory()
        result = _run_async(scorer.score(traj, scenario))
        self.assertEqual(result.scorer_type, "rule_based")


# =============================================================================
# 本地仓储测试
# =============================================================================

class TestLocalEvaluationRepository(unittest.TestCase):
    """本地评估仓储测试。"""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.repo = LocalEvaluationRepository(storage_root=self._tmpdir)

    def test_eval_prefix_isolation(self):
        """评估数据存储于 eval_ 前缀目录，与生产隔离。"""
        ss = EvaluationScenarioSet(
            set_id="set1", name="测试", tenant_id="t1", scenarios=[]
        )
        _run_async(self.repo.save_scenario_set(ss))
        # 验证 eval_ 前缀目录存在
        self.assertTrue((Path(self._tmpdir) / "eval_scenario_sets").exists())
        # 生产目录不应被创建
        self.assertFalse((Path(self._tmpdir) / "workflow_definitions").exists())

    def test_scenario_set_crud(self):
        """场景集 CRUD。"""
        ss = EvaluationScenarioSet(
            set_id="set1", name="测试", tenant_id="t1", scenarios=[]
        )
        _run_async(self.repo.save_scenario_set(ss))

        loaded = _run_async(self.repo.get_scenario_set("set1"))
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.name, "测试")

        listed = _run_async(self.repo.list_scenario_sets("t1"))
        self.assertEqual(len(listed), 1)

        deleted = _run_async(self.repo.delete_scenario_set("set1", "t1"))
        self.assertTrue(deleted)
        self.assertIsNone(_run_async(self.repo.get_scenario_set("set1")))

    def test_task_persistence(self):
        """评估任务持久化。"""
        task = EvaluationTask(
            task_id="task1",
            scenario_set_id="set1",
            tenant_id="t1",
            evaluation_type=EvaluationType.BASELINE,
        )
        _run_async(self.repo.save_task(task))
        loaded = _run_async(self.repo.get_task("task1"))
        self.assertIsNotNone(loaded)
        listed = _run_async(self.repo.list_tasks("t1"))
        self.assertEqual(len(listed), 1)

    def test_baseline_persistence(self):
        """基线持久化。"""
        baseline = BaselineRecord(
            baseline_id="b1",
            scenario_set_id="set1",
            tenant_id="t1",
            metrics=EvaluationMetrics(),
        )
        _run_async(self.repo.save_baseline(baseline))
        loaded = _run_async(self.repo.get_baseline("b1"))
        self.assertIsNotNone(loaded)
        listed = _run_async(self.repo.list_baselines("t1"))
        self.assertEqual(len(listed), 1)

    def test_implements_protocol(self):
        """实现 IEvaluationRepository 协议。"""
        self.assertIsInstance(self.repo, IEvaluationRepository)

    def test_delete_wrong_tenant_returns_false(self):
        """删除场景集时租户不匹配返回 False。"""
        ss = EvaluationScenarioSet(
            set_id="set1", name="测试", tenant_id="t1", scenarios=[]
        )
        _run_async(self.repo.save_scenario_set(ss))
        self.assertFalse(_run_async(self.repo.delete_scenario_set("set1", "other")))


# =============================================================================
# 场景集服务测试
# =============================================================================

class TestScenarioSetService(unittest.TestCase):
    """场景集服务测试。"""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.repo = LocalEvaluationRepository(storage_root=self._tmpdir)
        self.service = ScenarioSetService(self.repo)

    def test_create_and_get(self):
        """创建并查询场景集。"""
        scenario = _make_scenario(scenario_id="", set_id="")
        ss = _run_async(
            self.service.create_scenario_set(
                name="集1", tenant_id="t1", scenarios=[scenario]
            )
        )
        self.assertTrue(ss.set_id)
        self.assertEqual(len(ss.scenarios), 1)
        # 场景标识与归属被回填
        self.assertTrue(ss.scenarios[0].scenario_id)
        self.assertEqual(ss.scenarios[0].scenario_set_id, ss.set_id)

        loaded = _run_async(self.service.get_scenario_set(ss.set_id))
        self.assertEqual(loaded.name, "集1")

    def test_get_nonexistent_raises(self):
        """查询不存在场景集抛出 EVAL_SCENARIO_INVALID。"""
        with self.assertRaises(DomainError) as ctx:
            _run_async(self.service.get_scenario_set("nonexistent"))
        self.assertEqual(ctx.exception.code, EVAL_SCENARIO_INVALID)

    def test_add_scenario(self):
        """追加场景。"""
        ss = _run_async(
            self.service.create_scenario_set(name="集1", tenant_id="t1")
        )
        new_scenario = _make_scenario(scenario_id="", set_id="")
        updated = _run_async(
            self.service.add_scenario(ss.set_id, "t1", new_scenario)
        )
        self.assertEqual(len(updated.scenarios), 1)

    def test_add_scenario_wrong_tenant_raises(self):
        """追加场景时租户不匹配抛出异常。"""
        ss = _run_async(
            self.service.create_scenario_set(name="集1", tenant_id="t1")
        )
        with self.assertRaises(DomainError):
            _run_async(
                self.service.add_scenario(ss.set_id, "other", _make_scenario())
            )


# =============================================================================
# 基线存储测试
# =============================================================================

class TestBaselineStore(unittest.TestCase):
    """基线存储测试。"""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.repo = LocalEvaluationRepository(storage_root=self._tmpdir)
        self.store = BaselineStore(self.repo)

    def test_create_and_get_baseline(self):
        """创建并查询基线。"""
        metrics = EvaluationMetrics(
            total_scenarios=2,
            passed_scenarios=2,
            success_rate=1.0,
            average_clear_score=ClearScore(overall=0.9, policy_compliance=1.0),
        )
        baseline = _run_async(
            self.store.create_baseline(
                scenario_set_id="set1",
                tenant_id="t1",
                metrics=metrics,
                trajectory_scores=[],
            )
        )
        self.assertTrue(baseline.baseline_id)
        loaded = _run_async(self.store.get_baseline(baseline.baseline_id))
        self.assertIsNotNone(loaded)
        self.assertAlmostEqual(loaded.metrics.average_clear_score.overall, 0.9)


# =============================================================================
# 回归报告器测试
# =============================================================================

class TestRegressionReporter(unittest.TestCase):
    """回归报告器测试。"""

    def setUp(self):
        self.reporter = RegressionReporter()

    def _make_baseline(self, overall: float) -> BaselineRecord:
        return BaselineRecord(
            baseline_id="b1",
            scenario_set_id="set1",
            tenant_id="t1",
            metrics=EvaluationMetrics(
                average_clear_score=ClearScore(
                    cost_efficiency=overall,
                    reliability=overall,
                    latency=overall,
                    policy_compliance=overall,
                    overall=overall,
                )
            ),
        )

    def test_no_degradation_when_improved(self):
        """当前优于基线时无退化。"""
        baseline = self._make_baseline(0.8)
        current = EvaluationMetrics(
            average_clear_score=ClearScore(
                cost_efficiency=0.9,
                reliability=0.9,
                latency=0.9,
                policy_compliance=0.9,
                overall=0.9,
            )
        )
        comparison = _run_async(self.reporter.compare(baseline, current))
        self.assertFalse(comparison.degraded)
        self.assertGreater(comparison.clear_delta.overall, 0)

    def test_degradation_when_below_threshold(self):
        """当前显著低于基线时判定退化。"""
        baseline = self._make_baseline(0.9)
        current = EvaluationMetrics(
            average_clear_score=ClearScore(
                cost_efficiency=0.5,
                reliability=0.5,
                latency=0.5,
                policy_compliance=0.5,
                overall=0.5,
            )
        )
        comparison = _run_async(self.reporter.compare(baseline, current))
        self.assertTrue(comparison.degraded)
        self.assertGreater(len(comparison.degrade_details), 0)

    def test_delta_is_current_minus_baseline(self):
        """差异 = 当前 - 基线。"""
        baseline = self._make_baseline(0.7)
        current = EvaluationMetrics(
            average_clear_score=ClearScore(
                cost_efficiency=0.8,
                reliability=0.7,
                latency=0.6,
                policy_compliance=0.9,
                overall=0.75,
            )
        )
        comparison = _run_async(self.reporter.compare(baseline, current))
        self.assertAlmostEqual(comparison.clear_delta.cost_efficiency, 0.1, places=6)
        self.assertAlmostEqual(comparison.clear_delta.reliability, 0.0, places=6)
        self.assertAlmostEqual(comparison.clear_delta.latency, -0.1, places=6)


# =============================================================================
# 评估运行器测试
# =============================================================================

class TestEvaluationRunner(unittest.TestCase):
    """评估运行器测试。"""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.repo = LocalEvaluationRepository(storage_root=self._tmpdir)
        self.runner = EvaluationRunner(self.repo)

    def _prepare_scenario_set(self, count: int = 2) -> EvaluationScenarioSet:
        """准备场景集并持久化。"""
        scenarios = [
            _make_scenario(
                scenario_id=f"s{i}",
                set_id="set1",
                prompt=f"提示{i}",
                keywords=["总结"],
            )
            for i in range(count)
        ]
        ss = EvaluationScenarioSet(
            set_id="set1", name="测试集", tenant_id="t1", scenarios=scenarios
        )
        _run_async(self.repo.save_scenario_set(ss))
        return ss

    def test_baseline_evaluation_completes(self):
        """基线评估完成并产出报告。"""
        self._prepare_scenario_set(count=2)
        task = _run_async(
            self.runner.start_evaluation(
                scenario_set_id="set1",
                tenant_id="t1",
                evaluation_type=EvaluationType.BASELINE,
            )
        )
        self.assertEqual(task.state, EvaluationTaskState.COMPLETED)
        self.assertIsNotNone(task.report)
        self.assertEqual(task.report.metrics.total_scenarios, 2)
        self.assertEqual(task.report.metrics.passed_scenarios, 2)
        self.assertGreater(task.report.metrics.average_clear_score.overall, 0.0)

    def test_nonexistent_scenario_set_raises(self):
        """场景集不存在抛出异常。"""
        with self.assertRaises(DomainError) as ctx:
            _run_async(
                self.runner.start_evaluation(
                    scenario_set_id="nonexistent",
                    tenant_id="t1",
                    evaluation_type=EvaluationType.BASELINE,
                )
            )
        self.assertEqual(ctx.exception.code, EVAL_SCENARIO_INVALID)

    def test_regression_without_baseline_raises(self):
        """回归评估未提供基线抛出异常。"""
        self._prepare_scenario_set()
        with self.assertRaises(DomainError) as ctx:
            _run_async(
                self.runner.start_evaluation(
                    scenario_set_id="set1",
                    tenant_id="t1",
                    evaluation_type=EvaluationType.REGRESSION,
                )
            )
        self.assertEqual(ctx.exception.code, EVAL_BASELINE_MISSING)

    def test_regression_with_nonexistent_baseline_raises(self):
        """回归评估基线不存在抛出异常。"""
        self._prepare_scenario_set()
        with self.assertRaises(DomainError) as ctx:
            _run_async(
                self.runner.start_evaluation(
                    scenario_set_id="set1",
                    tenant_id="t1",
                    evaluation_type=EvaluationType.REGRESSION,
                    baseline_id="nonexistent",
                )
            )
        self.assertEqual(ctx.exception.code, EVAL_BASELINE_MISSING)

    def test_regression_evaluation_with_baseline(self):
        """回归评估对比基线产出 RegressionComparison。"""
        self._prepare_scenario_set(count=2)
        # 先做基线评估并冻结
        baseline_task = _run_async(
            self.runner.start_evaluation(
                scenario_set_id="set1",
                tenant_id="t1",
                evaluation_type=EvaluationType.BASELINE,
            )
        )
        baseline = _run_async(self.runner.freeze_baseline(baseline_task))

        # 再做回归评估
        reg_task = _run_async(
            self.runner.start_evaluation(
                scenario_set_id="set1",
                tenant_id="t1",
                evaluation_type=EvaluationType.REGRESSION,
                baseline_id=baseline.baseline_id,
            )
        )
        self.assertEqual(reg_task.state, EvaluationTaskState.COMPLETED)
        self.assertIsNotNone(reg_task.report.regression_comparison)
        # 桩执行器确定性，当前与基线一致，无退化
        self.assertFalse(reg_task.report.regression_comparison.degraded)

    def test_freeze_baseline_requires_completed_task(self):
        """冻结基线要求任务已完成。"""
        task = EvaluationTask(
            task_id="task1",
            scenario_set_id="set1",
            tenant_id="t1",
            evaluation_type=EvaluationType.BASELINE,
            state=EvaluationTaskState.RUNNING,
        )
        with self.assertRaises(DomainError) as ctx:
            _run_async(self.runner.freeze_baseline(task))
        self.assertEqual(ctx.exception.code, EVAL_TASK_INTERRUPTED)

    def test_reproducibility_with_fixed_seed(self):
        """固定随机种子保证可复现（相同场景集产出相同指标）。"""
        self._prepare_scenario_set(count=3)
        t1 = _run_async(
            self.runner.start_evaluation(
                scenario_set_id="set1",
                tenant_id="t1",
                evaluation_type=EvaluationType.BASELINE,
            )
        )
        # 重新准备并再次评估
        self._prepare_scenario_set(count=3)
        t2 = _run_async(
            self.runner.start_evaluation(
                scenario_set_id="set1",
                tenant_id="t1",
                evaluation_type=EvaluationType.BASELINE,
            )
        )
        self.assertEqual(
            t1.report.metrics.average_clear_score.overall,
            t2.report.metrics.average_clear_score.overall,
        )

    def test_empty_scenario_set_raises(self):
        """空场景集抛出异常。"""
        ss = EvaluationScenarioSet(
            set_id="empty", name="空", tenant_id="t1", scenarios=[]
        )
        _run_async(self.repo.save_scenario_set(ss))
        with self.assertRaises(DomainError):
            _run_async(
                self.runner.start_evaluation(
                    scenario_set_id="empty",
                    tenant_id="t1",
                    evaluation_type=EvaluationType.BASELINE,
                )
            )

    def test_wrong_tenant_raises(self):
        """租户不匹配抛出异常。"""
        self._prepare_scenario_set()
        with self.assertRaises(DomainError):
            _run_async(
                self.runner.start_evaluation(
                    scenario_set_id="set1",
                    tenant_id="other",
                    evaluation_type=EvaluationType.BASELINE,
                )
            )


# =============================================================================
# 桩轨迹执行器测试
# =============================================================================

class TestStubTrajectoryExecutor(unittest.TestCase):
    """桩轨迹执行器测试。"""

    def test_deterministic_output(self):
        """桩执行器产出确定性轨迹。"""
        executor = StubTrajectoryExecutor()
        scenario = _make_scenario(prompt="测试输入", keywords=["a", "b"])
        traj = _run_async(executor.execute(scenario, "task1"))
        self.assertIn("测试输入", traj.final_output)
        self.assertIn("a", traj.final_output)
        self.assertTrue(traj.success)
        self.assertGreater(traj.total_cost, 0.0)


# =============================================================================
# API 路由条件注册测试
# =============================================================================

class TestEvaluationRouterRegistration(unittest.TestCase):
    """评估路由条件注册测试。"""

    def setUp(self):
        """保存 get_settings 缓存以便恢复。"""
        from orchestration_platform.configuration.settings import get_settings

        self._original_get_settings = get_settings

    def tearDown(self):
        """恢复 get_settings 并清除缓存。"""
        from orchestration_platform.configuration import settings as settings_mod

        settings_mod.get_settings = self._original_get_settings
        # 清除 lru_cache 使后续调用重新加载
        if hasattr(self._original_get_settings, "cache_clear"):
            self._original_get_settings.cache_clear()

    def test_routes_not_registered_when_disabled(self):
        """evaluation_enabled=False 时评估路由不注册。"""
        from orchestration_platform.api.application_factory import create_app
        from orchestration_platform.configuration.settings import get_settings

        # 清除缓存并以默认配置（evaluation_enabled=False）创建应用
        get_settings.cache_clear()
        app = create_app()
        paths = {getattr(route, "path", "") for route in app.routes}
        self.assertFalse(
            any("/governance/evaluation" in p for p in paths),
            "评估路由不应在 evaluation_enabled=False 时注册",
        )

    def test_routes_registered_when_enabled(self):
        """evaluation_enabled=True 时评估路由注册。"""
        from orchestration_platform.configuration import settings as settings_mod
        from orchestration_platform.configuration.settings import Settings

        # 构造启用评估的配置并替换 get_settings
        enabled_settings = Settings(
            evaluation_enabled=True,
            storage_backend="local_file",
        )

        def _mock_settings():
            return enabled_settings

        # application_factory 在模块顶部绑定了 get_settings，需同时替换
        import orchestration_platform.api.application_factory as factory_mod

        original_factory_gs = factory_mod.get_settings
        factory_mod.get_settings = _mock_settings
        settings_mod.get_settings = _mock_settings
        try:
            app = factory_mod.create_app()
            from starlette.testclient import TestClient
            resp = TestClient(app).get("/api/v1/governance/evaluation/status")
            self.assertTrue(
                resp.status_code == 200,
                "评估路由应在 evaluation_enabled=True 时注册",
            )
        finally:
            factory_mod.get_settings = original_factory_gs


if __name__ == "__main__":
    unittest.main()
