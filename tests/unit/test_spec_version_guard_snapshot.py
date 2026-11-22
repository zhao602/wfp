# =============================================================================
# 规范版本快照护栏（tests/unit/test_spec_version_guard_snapshot.py，roadmap B7 W4）
#
# 与 tests/integration/test_spec_version_compat_guard.py 互补：
#   - 既有件：lower-bound 自动发现（examples/ 全量参数化可加载 + 每版本行为
#     回归 + HC-02 枚举仅追加 + 五要素不收窄），防「新样例/新行为破坏承诺」；
#   - 本件：快照冻结（SNAPSHOT_VERSIONS），防「已注册版本被删」——
#     1) 快照锚：注册表现存版本 ⊇ {1.0, 1.1}（移除任一即失败）；
#     2) 解析兼容锚：快照内每个版本在 examples/ 下仍有对应范例且经全链
#        解析通过（该版本范例被删光或损坏即失败）。
#   新增版本（如未来 "1.2"）属「只增」允许项：超集断言、不做全等断言，
#   加法安全不误报。
#
# 承诺出处：docs/spec-versioning.md（1.x 只增不删，P7）。
# 执行：PYTHONPATH=src python -m pytest tests/unit/test_spec_version_guard_snapshot.py -v
# =============================================================================

from pathlib import Path

import pytest
import yaml

from orchestration_platform.workflow.specification_version_registry import (
    SpecificationVersionRegistry,
)
from orchestration_platform.workflow.yaml_workflow_parser import YamlWorkflowParser

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES_ROOT = _PROJECT_ROOT / "examples"

# 快照锚：已注册且对外承诺的规范版本（1.x 只增不删——仅追加，永不移除）
SNAPSHOT_VERSIONS = ("1.0", "1.1")


def _example_definitions_by_version() -> dict:
    """按 spec_version 归组 examples/ 下全部工作流定义路径。

    跳过伴随 Agent 清单（顶层 agents 键且无 spec_version，与 CLI SKIP
    判定及 compat_guard 识别口径一致）。
    """
    grouped: dict = {}
    for path in sorted(EXAMPLES_ROOT.rglob("*.yaml")):
        with path.open("r", encoding="utf-8") as handle:
            document = yaml.safe_load(handle.read())
        if not (isinstance(document, dict) and "spec_version" in document):
            continue
        version = str(document["spec_version"])
        grouped.setdefault(version, []).append(path)
    return grouped


def test_supported_versions_superset_of_snapshot() -> None:
    """快照锚：注册表现存版本 ⊇ {1.0, 1.1}（移除任一即失败；新增允许）。"""
    registry = SpecificationVersionRegistry()
    for version in SNAPSHOT_VERSIONS:
        assert registry.is_supported(version) is True, (
            f"已注册版本 {version} 被移除（1.x 只增不删承诺违例，"
            f"见 docs/spec-versioning.md）"
        )


@pytest.mark.parametrize("version", SNAPSHOT_VERSIONS)
def test_snapshot_version_example_parseable(version: str) -> None:
    """解析兼容锚：快照版本仍有范例且全链解析通过（删光/损坏即失败）。"""
    candidates = _example_definitions_by_version().get(version, [])
    assert candidates, (
        f"快照版本 {version} 在 examples/ 下已无对应工作流定义范例"
        f"（1.x 只增不删承诺违例）"
    )
    parser = YamlWorkflowParser()
    for path in candidates:
        definition = parser.parse(path.read_text(encoding="utf-8"))
        assert definition is not None, f"{path} 全链解析失败"
        assert definition.spec_version == version
        assert len(definition.nodes) > 0
