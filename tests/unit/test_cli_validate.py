# =============================================================================
# wfp CLI 单测（tests/unit/test_cli_validate.py）
#
# 覆盖（arch_review_v2 5.6，CHG-30；AC-04/AC-06 的 V 部分）：
#   - 骨架：--help 就绪、四命令注册、裸命令返回 1；
#   - validate：15 YAML（E18 口径）exit 0、失败 YAML exit 1、
#     YAML 语法错误/目标缺失 exit 2、Agent 清单 SKIP、--json 机器读；
#   - graph：mermaid flowchart TD 产出、条件边标签、失败 exit 2；
#   - run --local：本地执行成功 0、--checkpoint 持久化、代码节点禁用
#     快速失败 1（CODE_NODE_DISABLED）、解析失败 2、校验失败 1；
#   - diff：解析失败 2、编译失败 1、引擎未合入骨架形态 1（6.3 合入后转 0）。
#
# 约束：进程内 main(argv) 直调（不 spawn 子进程，§13.1）；
#       环境相关设置经 env + get_settings.cache_clear() 隔离
#       （与 tests/unit/test_checkpoint_resume.py 同口径）。
# =============================================================================

import importlib.util
import json
from pathlib import Path

import pytest

from orchestration_platform.cli import main

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES_DIR = _PROJECT_ROOT / "examples"
VALID_FIXTURES = _PROJECT_ROOT / "tests" / "fixtures" / "valid_workflow_definitions"
INVALID_FIXTURES = _PROJECT_ROOT / "tests" / "fixtures" / "invalid_workflow_definitions"

_SINGLE_TRIGGER = VALID_FIXTURES / "valid_single_trigger_node.yaml"
_SEQUENTIAL_FOUR = VALID_FIXTURES / "valid_sequential_four_nodes.yaml"
_WITH_BRANCH = VALID_FIXTURES / "valid_with_branch.yaml"
_CYCLIC = INVALID_FIXTURES / "invalid_cyclic_connection.yaml"
_FOLLOWS_GLOBAL = VALID_FIXTURES / "valid_agent_model_follows_global.yaml"
_YAML_SYNTAX = INVALID_FIXTURES / "invalid_yaml_syntax_error.yaml"
_AGENT_MANIFEST = EXAMPLES_DIR / "drift_detection" / "drift_detection_agents.yaml"


def _reset_settings_cache():
    """失效 get_settings 缓存（环境覆盖生效/还原的统一动作）。"""
    from orchestration_platform.configuration.settings import get_settings

    get_settings.cache_clear()


@pytest.fixture
def code_node_disabled_env(monkeypatch):
    """强制 code_node_enabled=false（确定性 CODE_NODE_DISABLED 快速失败）。"""
    monkeypatch.setenv("CODE_NODE_ENABLED", "false")
    _reset_settings_cache()
    yield
    _reset_settings_cache()


@pytest.fixture
def local_storage_root(tmp_path, monkeypatch):
    """检查点本地存储重定向到 tmp（--checkpoint 用例不污染仓库 .data）。"""
    root = tmp_path / "data"
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", str(root))
    _reset_settings_cache()
    yield root
    _reset_settings_cache()


# =============================================================================
# ① 骨架（5.1）
# =============================================================================


class TestCliSkeleton:
    def test_help_ready(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main(["--help"])
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        for command in ("validate", "diff", "graph", "run"):
            assert command in out

    @pytest.mark.parametrize("command", ["validate", "diff", "graph", "run"])
    def test_subcommand_help_ready(self, command, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main([command, "--help"])
        assert exc_info.value.code == 0
        assert command in capsys.readouterr().out

    def test_bare_command_returns_fail(self, capsys):
        assert main([]) == 1
        assert "validate" in capsys.readouterr().out


# =============================================================================
# ② validate（5.2）
# =============================================================================


class TestValidateCommand:
    def test_examples_dir_sixteen_yaml_exit_zero(self, capsys):
        """AC-04：16 个 YAML（14 定义 + 2 清单，E18 glob 口径）exit 0。

        （2026-11-22 B6 注记，roadmap_b6_parallel_fanout：原锚 15 个 YAML，
        新增 examples/parallel_execution/parallel_fanout.yaml 后 15→16，
        passed 13→14；测试名与锚同步更新。）
        """
        assert main(["validate", str(EXAMPLES_DIR)]) == 0
        out = capsys.readouterr().out
        assert "16" in out
        assert "跳过" in out
        assert "加载异常" not in out.replace("0 加载异常", "")

    def test_single_valid_file_exit_zero(self, capsys):
        assert main(["validate", str(_SINGLE_TRIGGER)]) == 0
        out = capsys.readouterr().out
        assert "通过" in out

    def test_topology_invalid_exit_one(self, capsys):
        assert main(["validate", str(_CYCLIC)]) == 1
        captured = capsys.readouterr()
        assert "校验未通过" in captured.out or "环路" in captured.out

    def test_node_config_missing_model_warns_exit_zero(self, capsys):
        """跟随全局语义（2026-11-20 修订 FR-04）：agent 空模型 = WARNING 不计失败。

        原 ERROR 语义（exit 1）由 test_topology_invalid_exit_one（_CYCLIC）
        继续覆盖；本用例转为验证 WARNING 可辨识提示 + exit 0。
        （样本迁移：invalid_missing_agent_model.yaml → valid_agent_model_
        follows_global.yaml，空模型自本修订起属合法定义。）
        """
        assert main(["validate", str(_FOLLOWS_GLOBAL)]) == 0
        out = capsys.readouterr().out
        assert "跟随全局主模型" in out
        assert "[warning]" in out

    def test_yaml_syntax_error_exit_two(self, capsys):
        assert main(["validate", str(_YAML_SYNTAX)]) == 2
        # 逐文件加载异常明细随校验报告走 stdout（报告即载荷）
        assert "YAML 语法错误" in capsys.readouterr().out

    def test_missing_target_exit_two(self, capsys):
        assert main(["validate", str(_PROJECT_ROOT / "not_exists_dir")]) == 2
        assert "目标不存在" in capsys.readouterr().err

    def test_agent_manifest_skip_not_fail(self, capsys):
        """伴随 Agent 清单（agents 键 + 无 spec_version）SKIP 不计失败（E18）。"""
        assert main(["validate", str(_AGENT_MANIFEST)]) == 0
        out = capsys.readouterr().out
        assert "跳过" in out

    def test_json_output_machine_readable(self, capsys):
        # （2026-11-22 B6 注记：15→16 / passed 13→14，新增 parallel_fanout.yaml）
        assert main(["validate", str(EXAMPLES_DIR), "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        summary = payload["summary"]
        assert summary["total"] == 16
        assert summary["passed"] == 14
        assert summary["skipped"] == 2
        assert summary["failed"] == 0
        assert summary["load_errors"] == 0


# =============================================================================
# ③ graph（5.3）
# =============================================================================


class TestGraphCommand:
    def test_mermaid_flowchart_output(self, capsys):
        assert main(["graph", str(_SEQUENTIAL_FOUR)]) == 0
        out = capsys.readouterr().out
        assert out.startswith("flowchart TD")
        assert "-->" in out
        for node_id in ("trigger_manual", "read_dataset", "analyze_data", "build_report"):
            assert node_id in out

    def test_branch_condition_edge_labels(self, capsys):
        assert main(["graph", str(_WITH_BRANCH)]) == 0
        out = capsys.readouterr().out
        assert out.startswith("flowchart TD")
        assert "-->|intent == finance|" in out

    def test_json_output_wraps_mermaid(self, capsys):
        assert main(["graph", str(_SINGLE_TRIGGER), "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["definition_id"] == "single_trigger_node"
        assert payload["mermaid"].startswith("flowchart TD")
        assert "trigger_manual" in payload["mermaid"]

    def test_invalid_yaml_exit_two(self, capsys):
        assert main(["graph", str(_YAML_SYNTAX)]) == 2

    def test_missing_file_exit_two(self, capsys):
        assert main(["graph", str(_PROJECT_ROOT / "not_exists.yaml")]) == 2


# =============================================================================
# ④ run --local（5.4）
# =============================================================================


_CODE_NODE_WORKFLOW = """\
spec_version: "1.0"
definition_id: local_code_node_workflow
name: 本地代码节点工作流
definition_version: 1
inputs: []
outputs: []
nodes:
  - node_id: trigger_manual
    name: 手动触发
    node_type: trigger
    configuration:
      trigger_type: manual
  - node_id: code_calc
    name: 代码节点
    node_type: code
    configuration:
      language: python
      code: |
        def main():
            return {'ok': True}
edges:
  - edge_id: e1
    source_node_id: trigger_manual
    target_node_id: code_calc
"""


class TestRunLocalCommand:
    def test_run_requires_local_flag(self, capsys):
        assert main(["run", str(_SINGLE_TRIGGER)]) == 1
        assert "--local" in capsys.readouterr().err

    def test_single_trigger_success(self, capsys):
        assert main(["run", str(_SINGLE_TRIGGER), "--local"]) == 0
        out = capsys.readouterr().out
        assert "成功" in out
        assert "顺序" in out
        assert "trigger_manual" in out

    def test_run_json_output(self, capsys):
        assert main(["run", str(_SINGLE_TRIGGER), "--local", "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["状态"] == "成功"
        assert payload["顺序"] == ["trigger_manual"]
        assert payload["definition_id"] == "single_trigger_node"

    def test_checkpoint_enabled_persists(self, local_storage_root, capsys):
        """--checkpoint 可选启用：本地文件检查点落盘（默认关闭，DC-08 互不影响）。"""
        assert main(["run", str(_SINGLE_TRIGGER), "--local", "--checkpoint"]) == 0
        out = capsys.readouterr().out
        assert "检查点" in out
        checkpoint_dir = Path(local_storage_root) / "checkpoints"
        assert checkpoint_dir.exists()
        assert any(checkpoint_dir.rglob("*"))

    def test_code_node_disabled_fast_fail_exit_one(
        self, tmp_path, code_node_disabled_env, capsys
    ):
        """code_node_enabled=false → CODE_NODE_DISABLED 快速失败，exit 1（不触网）。"""
        workflow_file = tmp_path / "code_node_workflow.yaml"
        workflow_file.write_text(_CODE_NODE_WORKFLOW, encoding="utf-8")
        assert main(["run", str(workflow_file), "--local"]) == 1
        captured = capsys.readouterr()
        assert "功能未启用" in captured.err

    def test_parse_error_exit_two(self, capsys):
        assert main(["run", str(_YAML_SYNTAX), "--local"]) == 2

    def test_validation_error_exit_one(self, capsys):
        assert main(["run", str(_CYCLIC), "--local"]) == 1


# =============================================================================
# ⑤ diff（5.5 骨架；6.3 合入后引擎路径转全量）
# =============================================================================


class TestDiffCommand:
    def test_parse_error_exit_two(self, capsys):
        assert main(["diff", str(_YAML_SYNTAX), str(_SINGLE_TRIGGER)]) == 2

    def test_compile_invalid_exit_one(self, capsys):
        assert main(["diff", str(_CYCLIC), str(_SINGLE_TRIGGER)]) == 1

    def test_engine_missing_skeleton_or_merged_full(self, capsys):
        """引擎未合入 → 骨架提示 + exit 1；6.3 合入后同输入 exit 0（前向兼容）。"""
        spec = importlib.util.find_spec(
            "orchestration_platform.workflow.semantic_diff"
        )
        exit_code = main(["diff", str(_SINGLE_TRIGGER), str(_SINGLE_TRIGGER)])
        captured = capsys.readouterr()
        if spec is None:
            assert exit_code == 1
            assert "未合入" in captured.err
        else:
            assert exit_code == 0
