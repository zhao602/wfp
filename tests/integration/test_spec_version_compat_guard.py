# =============================================================================
# 1.x 只增不删护栏测试（tests/integration/test_spec_version_compat_guard.py）
#
# 覆盖（arch_review_v2_tasks 7.2，G11 / CHG-39 佐证；INV-03 / HC-05 / AC-04）：
#   1. 锚定「15 个 YAML 全部可加载」（E18 口径）：examples/ 下 13 个工作流
#      定义全链解析通过 + 2 个伴随 Agent 清单可加载且符合清单识别口径
#      （顶层 agents 键且无 spec_version，与 CLI SKIP 判定一致）；
#      新增范例属「只增」允许项，计数断言取下界、不冻结总数；
#   2. 固化「1.x 解析行为回归」清单（每条断言对应一个 spec 版本；最小样例
#      取自既有 12 套范例，不新增 YAML 资产）：
#      - "1.0"（sequential_pipeline）：节点数上限 = 全局配置默认 50；
#      - "1.1"（parallel_execution）：节点数上限 200（解析器按版本覆写）；
#      - 未支持版本（"1.2"/"2.0"）：精确匹配拒绝、不降级解析
#        （WF_UNSUPPORTED_VERSION）；缺失 spec_version：WF_VALIDATION_FAILED；
#   3. HC-02 守卫：NodeType 枚举仅追加（既有成员不删除、枚举值不变更；
#      新增成员允许，故做包含断言不做全等断言）；
#   4. 五要素守卫：REQUIRED_TOP_LEVEL_FIELDS 不得新增必备字段
#      （新增必备字段 = 收窄存量，禁止；放宽——移除必备约束——允许，
#      故做子集断言不做超集断言）。
#
# 约束：不 spawn 子进程（§13.1）；设置类断言经 env + get_settings.cache_clear()
# 隔离（与 tests/unit/test_cli_validate.py 同口径）。
# 执行：PYTHONPATH=src python -m pytest tests/integration/test_spec_version_compat_guard.py -v
# =============================================================================

from pathlib import Path

import pytest
import yaml

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.errors import (
    WF_DEFINITION_TOO_LARGE,
    WF_UNSUPPORTED_VERSION,
    WF_VALIDATION_FAILED,
    WorkflowValidationError,
)
from orchestration_platform.domain.models.enums import NodeType
from orchestration_platform.domain.validation.definition_structure_validator import (
    REQUIRED_TOP_LEVEL_FIELDS,
)
from orchestration_platform.workflow.specification_version_registry import (
    SpecificationVersionRegistry,
)
from orchestration_platform.workflow.yaml_workflow_parser import YamlWorkflowParser

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES_ROOT = _PROJECT_ROOT / "examples"

# 模块级发现：examples/ 下全部 YAML（当前 15 个；新增范例自动纳入参数化）
EXAMPLE_YAML_FILES = sorted(EXAMPLES_ROOT.rglob("*.yaml"))

# 每个受支持 spec 版本的最小回归样例（取自既有范例；7.2 动作约定）
VERSION_SAMPLES = [
    ("1.0", "sequential_pipeline", "sequential_pipeline.yaml"),
    ("1.1", "parallel_execution", "parallel_execution.yaml"),
]

# HC-02：既有 NodeType 成员清单（成员名 → 枚举值）；仅追加——删除/改名/改值即护栏失败
KNOWN_NODE_TYPES = {
    "AGENT": "agent",
    "TOOL": "tool",
    "DATA_SOURCE": "data_source",
    "TRIGGER": "trigger",
    "CONTROL_FLOW": "control_flow",
    "SUBWORKFLOW": "subworkflow",
    "DYNAMIC_INJECTOR": "dynamic_injector",
    "DATA_TRANSFORM": "data_transform",
    "PLUGIN": "plugin",
    "SUPERVISOR": "supervisor",
    "OUTPUT_DISPLAY": "output_display",
    "VECTOR_KNOWLEDGE_BASE": "vector_knowledge_base",
    "LLM": "llm",
    "CODE": "code",
}

# 五要素 + 定义元信息（1.x 内不得新增必备顶层字段）
KNOWN_REQUIRED_TOP_LEVEL_FIELDS = {
    "spec_version",
    "definition_id",
    "name",
    "nodes",
    "edges",
    "inputs",
    "outputs",
}


def _reset_settings_cache():
    """失效 get_settings 缓存（环境覆盖生效/还原的统一动作）。"""
    get_settings.cache_clear()


@pytest.fixture
def pinned_default_max_nodes(monkeypatch):
    """钉住 1.0 全局节点上限默认值 50（WORKFLOW_MAX_NODES 默认值，1.x 承诺事实）。"""
    monkeypatch.setenv("WORKFLOW_MAX_NODES", "50")
    _reset_settings_cache()
    yield 50
    _reset_settings_cache()


def _load_yaml_document(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle.read())


def _is_agent_manifest(document) -> bool:
    """伴随 Agent 清单识别（与 cli/commands 的 SKIP 判定同口径）。"""
    return isinstance(document, dict) and "spec_version" not in document and "agents" in document


def _trigger_only_document(spec_version: str, node_count: int) -> str:
    """构造最小可解析文档：全部为手动触发器节点（数量可控用于体积上限探针）。

    不落盘、不新增 YAML 资产（内存构造）；节点字段形态与既有范例一致。
    """
    document = {
        "spec_version": spec_version,
        "definition_id": "compat_guard_probe",
        "name": "兼容护栏探针",
        "definition_version": 1,
        "inputs": [],
        "outputs": [],
        "nodes": [
            {
                "node_id": f"trigger_{index}",
                "name": f"触发器{index}",
                "node_type": "trigger",
                "configuration": {"trigger_type": "manual"},
            }
            for index in range(node_count)
        ],
        "edges": [],
    }
    return yaml.safe_dump(document, allow_unicode=True, sort_keys=False)


# =============================================================================
# 1. 「15 个 YAML 全部可加载」锚点
# =============================================================================


@pytest.mark.parametrize(
    "yaml_path",
    EXAMPLE_YAML_FILES,
    ids=[str(path.relative_to(EXAMPLES_ROOT)) for path in EXAMPLE_YAML_FILES],
)
def test_example_yaml_file_loadable(yaml_path: Path) -> None:
    """examples/ 下每个 YAML 均可加载：定义走全链解析，清单走识别口径。"""
    document = _load_yaml_document(yaml_path)
    assert isinstance(document, dict)
    if _is_agent_manifest(document):
        assert "agents" in document
    else:
        parser = YamlWorkflowParser()
        definition = parser.parse(yaml_path.read_text(encoding="utf-8"))
        assert definition is not None
        assert len(definition.nodes) > 0


def test_example_yaml_inventory_lower_bounds() -> None:
    """范例清单下界锚点：≥15 文件 = ≥13 定义（≥3 个 1.0、≥10 个 1.1）+ ≥2 清单。"""
    total = len(EXAMPLE_YAML_FILES)
    definitions = 0
    manifests = 0
    version_counts: dict = {}
    for path in EXAMPLE_YAML_FILES:
        document = _load_yaml_document(path)
        if _is_agent_manifest(document):
            manifests += 1
        else:
            definitions += 1
            version = str(document.get("spec_version"))
            version_counts[version] = version_counts.get(version, 0) + 1
    assert total >= 15
    assert definitions >= 13
    assert manifests >= 2
    assert version_counts.get("1.0", 0) >= 3
    assert version_counts.get("1.1", 0) >= 10


# =============================================================================
# 2. 「1.x 解析行为回归」清单（每条对应一个 spec 版本）
# =============================================================================


def test_version_registry_exact_match_support() -> None:
    """版本注册表：支持 1.0/1.1，精确匹配、不降级（1.2/2.0 等一律拒绝）。"""
    registry = SpecificationVersionRegistry()
    assert registry.is_supported("1.0") is True
    assert registry.is_supported("1.1") is True
    assert registry.is_supported("1.2") is False
    assert registry.is_supported("2.0") is False
    assert registry.is_supported("0.9") is False
    assert registry.is_supported("1.10") is False
    assert registry.is_supported("") is False
    assert registry.supported_range_text() == "1.0、1.1"
    assert registry.highest_supported() == "1.1"


@pytest.mark.parametrize(
    "spec_version,example_dir,yaml_name",
    VERSION_SAMPLES,
    ids=[entry[0] for entry in VERSION_SAMPLES],
)
def test_version_sample_parse(spec_version: str, example_dir: str, yaml_name: str) -> None:
    """各受支持版本的最小样例（取自既有范例）经全链解析通过。"""
    parser = YamlWorkflowParser()
    text = (EXAMPLES_ROOT / example_dir / yaml_name).read_text(encoding="utf-8")
    definition = parser.parse(text)
    assert definition.spec_version == spec_version
    assert len(definition.nodes) > 0


def test_spec_1_0_node_cap_default_fifty(pinned_default_max_nodes) -> None:
    """1.0 解析行为回归：节点数上限 = 全局配置默认 50（51 节点即拒绝）。"""
    parser = YamlWorkflowParser()
    with pytest.raises(WorkflowValidationError) as exc_info:
        parser.parse(_trigger_only_document("1.0", pinned_default_max_nodes + 1))
    assert exc_info.value.code == WF_DEFINITION_TOO_LARGE


def test_spec_1_1_node_cap_two_hundred(pinned_default_max_nodes) -> None:
    """1.1 解析行为回归：节点数上限 200（51 节点通过，201 节点拒绝）。"""
    parser = YamlWorkflowParser()
    definition = parser.parse(_trigger_only_document("1.1", pinned_default_max_nodes + 1))
    assert len(definition.nodes) == pinned_default_max_nodes + 1
    with pytest.raises(WorkflowValidationError) as exc_info:
        parser.parse(_trigger_only_document("1.1", 201))
    assert exc_info.value.code == WF_DEFINITION_TOO_LARGE


@pytest.mark.parametrize("unsupported_version", ["1.2", "2.0"])
def test_unsupported_version_rejected_without_downgrade(unsupported_version: str) -> None:
    """未支持版本：精确匹配拒绝、不降级解析（错误信息附当前支持区间）。"""
    parser = YamlWorkflowParser()
    with pytest.raises(WorkflowValidationError) as exc_info:
        parser.parse(_trigger_only_document(unsupported_version, 3))
    assert exc_info.value.code == WF_UNSUPPORTED_VERSION
    assert "不受支持" in exc_info.value.message
    assert "1.0、1.1" in exc_info.value.message


def test_missing_spec_version_rejected() -> None:
    """缺失 spec_version：必填五要素之一，WF_VALIDATION_FAILED。"""
    document = yaml.safe_load(_trigger_only_document("1.0", 3))
    del document["spec_version"]
    text = yaml.safe_dump(document, allow_unicode=True, sort_keys=False)
    parser = YamlWorkflowParser()
    with pytest.raises(WorkflowValidationError) as exc_info:
        parser.parse(text)
    assert exc_info.value.code == WF_VALIDATION_FAILED


# =============================================================================
# 3. HC-02 守卫：NodeType 枚举仅追加
# =============================================================================


def test_node_type_enum_append_only() -> None:
    """HC-02：既有枚举成员不删除、枚举值不变更（新增成员允许，包含断言）。"""
    actual = {member.name: member.value for member in NodeType}
    for member_name, member_value in KNOWN_NODE_TYPES.items():
        assert actual.get(member_name) == member_value, (
            f"NodeType 既有成员 {member_name}={member_value} 被删除或改值（HC-02 违例）"
        )


# =============================================================================
# 4. 五要素守卫：不新增必备顶层字段
# =============================================================================


def test_required_top_level_fields_not_widened() -> None:
    """REQUIRED_TOP_LEVEL_FIELDS 不得超出既有七字段（新增必备字段 = 收窄存量）。"""
    assert set(REQUIRED_TOP_LEVEL_FIELDS) <= KNOWN_REQUIRED_TOP_LEVEL_FIELDS, (
        "1.x 内新增必备顶层字段属收窄变更（只增不删承诺违例）"
    )
