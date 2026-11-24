# =============================================================================
# 旧 YAML 零破坏守护单测（tests/unit/test_vector_kb_management_compat.py）
#
# 覆盖（CHG-22 / design §2.6.3）：
#   - 含 embedding_model 的旧 YAML 加载成功、不报字段非法（AC-50，
#     必填→可选 + 保留字段名/类型 + 值不作废）；
#   - 不含该字段的新 YAML 加载成功（可选字段，INV-D05）；
#   - 判别联合解析（NodeConfiguration 成员与顺序零改动，INV-D01）；
#   - 旧字段与绑定并存 → 检索以绑定为准（AC-51，经 YAML 加载路径）；
#   - 绑定缺失 + 字段存在 → 确定性回退（AC-52，经 YAML 加载路径）；
#   - 既有执行器行为零变化（管理开关未启用 → 生效模型/检索器与
#     改造前逐字一致；绑定与字段均缺失 → 确定性失败前置）。
# 测试纪律：全同步用例（禁 asyncio.run）；加载经 YamlWorkflowParser
# 与 NodeConfiguration 判别联合（与生产解析同路径）。
# =============================================================================

from typing import Optional

from pydantic import TypeAdapter

from orchestration_platform.domain.models.enums import NodeType
from orchestration_platform.domain.models.node_configurations import (
    ModelLoadingConfig,
    NodeConfiguration,
    VectorKnowledgeBaseNodeConfiguration,
)
from orchestration_platform.domain.validation.node_configuration_validator import (
    NodeConfigurationValidator,
)
from orchestration_platform.engine.nodes.vector_knowledge_base_node_executor import (
    VectorKnowledgeBaseNodeExecutor,
)
from orchestration_platform.integrations.vector_kb_management import (
    KBEmbeddingBinding,
)
from orchestration_platform.integrations.vector_knowledge_base.local_backend import (
    LocalKnowledgeBaseRetriever,
)
from orchestration_platform.integrations.vector_knowledge_base.retriever import (
    HttpKnowledgeBaseRetriever,
)

# 判别联合适配器：与 NodeDefinition.configuration 同一解析路径
_UNION_ADAPTER: TypeAdapter = TypeAdapter(NodeConfiguration)

# 含 embedding_model 的旧 YAML（历史工作流原样，INV-D05 守护对象）
_LEGACY_WORKFLOW_YAML = (
    'spec_version: "1.0"\n'
    "definition_id: vkb_legacy_compat\n"
    "name: 旧版含embedding字段工作流\n"
    "definition_version: 1\n"
    "inputs: []\n"
    "outputs: []\n"
    "nodes:\n"
    "  - node_id: trigger_manual\n"
    "    name: 手动触发\n"
    "    node_type: trigger\n"
    "    output_name: trigger_output\n"
    "    configuration:\n"
    "      trigger_type: manual\n"
    "  - node_id: vector_search\n"
    "    name: 向量检索\n"
    "    node_type: vector_knowledge_base\n"
    "    output_name: chunks\n"
    "    configuration:\n"
    "      knowledge_base_id: kb_main\n"
    "      query_mode: reference\n"
    "      query_reference: trigger_output\n"
    "      top_k: 5\n"
    "      embedding_model:\n"
    "        mode: api\n"
    "        model_identifier: text-embedding-v3\n"
    "edges:\n"
    "  - edge_id: e1\n"
    "    source_node_id: trigger_manual\n"
    "    target_node_id: vector_search\n"
)

# 同一工作流去除 embedding_model 块（新 YAML：字段可缺省）
_FIELDLESS_WORKFLOW_YAML = _LEGACY_WORKFLOW_YAML.replace(
    "      top_k: 5\n"
    "      embedding_model:\n"
    "        mode: api\n"
    "        model_identifier: text-embedding-v3\n",
    "      top_k: 5\n",
)


def _load_vkb_config(yaml_text: str) -> VectorKnowledgeBaseNodeConfiguration:
    """经生产 YAML 解析路径加载并取出向量知识库节点配置。"""
    from orchestration_platform.workflow.yaml_workflow_parser import (
        YamlWorkflowParser,
    )

    definition = YamlWorkflowParser().parse(yaml_text)
    for node in definition.nodes:
        if node.node_type == NodeType.VECTOR_KNOWLEDGE_BASE:
            config = node.configuration
            assert isinstance(config, VectorKnowledgeBaseNodeConfiguration)
            return config
    raise AssertionError("YAML 中未找到 vector_knowledge_base 节点")


# ---------------------------------------------------------------------------
# 1. 旧 YAML 零破坏（AC-50 / INV-D05）
# ---------------------------------------------------------------------------


def test_legacy_yaml_with_embedding_model_loads():
    """含 embedding_model 的旧 YAML 加载成功，值不作废（AC-50）。"""
    config = _load_vkb_config(_LEGACY_WORKFLOW_YAML)

    assert config.embedding_model is not None
    assert config.embedding_model.mode == "api"
    assert config.embedding_model.model_identifier == "text-embedding-v3"
    assert config.knowledge_base_id == "kb_main"
    assert config.query_reference == "trigger_output"
    assert config.top_k == 5


def test_fieldless_yaml_loads_with_optional_embedding_model():
    """不含 embedding_model 的新 YAML 加载成功（可选字段，INV-D05）。"""
    config = _load_vkb_config(_FIELDLESS_WORKFLOW_YAML)

    assert config.embedding_model is None
    assert config.knowledge_base_id == "kb_main"


def test_validator_accepts_missing_embedding_model_without_issues():
    """embedding 缺失不触发加载期校验问题（T6.2 零行为变化）。"""
    from orchestration_platform.workflow.yaml_workflow_parser import (
        YamlWorkflowParser,
    )

    definition = YamlWorkflowParser().parse(_FIELDLESS_WORKFLOW_YAML)

    issues = NodeConfigurationValidator().validate(definition)

    assert issues == []


# ---------------------------------------------------------------------------
# 2. 判别联合解析（INV-D01：成员与顺序零改动）
# ---------------------------------------------------------------------------


def test_discriminated_union_resolves_vector_knowledge_base_member():
    """判别联合按 node_type 精确解析（vkb 成员 + 既有成员共存不漂移）。"""
    vkb_payload = {
        "node_type": "vector_knowledge_base",
        "knowledge_base_id": "kb_main",
        "query_value": "查询文本",
        "embedding_model": {
            "mode": "api",
            "model_identifier": "text-embedding-v3",
        },
    }
    parsed = _UNION_ADAPTER.validate_python(vkb_payload)
    assert isinstance(parsed, VectorKnowledgeBaseNodeConfiguration)
    assert parsed.embedding_model is not None

    trigger_payload = {
        "node_type": "trigger",
        "trigger_type": "manual",
    }
    trigger_parsed = _UNION_ADAPTER.validate_python(trigger_payload)
    assert not isinstance(trigger_parsed, VectorKnowledgeBaseNodeConfiguration)


def test_optional_embedding_model_direct_instantiation():
    """直接构造：embedding_model 可缺省、可赋值（可选字段双形态）。"""
    fieldless = VectorKnowledgeBaseNodeConfiguration(
        knowledge_base_id="kb_main", query_value="查询文本"
    )
    assert fieldless.embedding_model is None

    with_field = VectorKnowledgeBaseNodeConfiguration(
        knowledge_base_id="kb_main",
        query_value="查询文本",
        embedding_model=ModelLoadingConfig(
            mode="local", model_identifier="local-embed-model"
        ),
    )
    assert with_field.embedding_model is not None
    assert with_field.embedding_model.model_identifier == "local-embed-model"


# ---------------------------------------------------------------------------
# 3. 绑定优先 / 缺失回退（AC-51 / AC-52，经 YAML 加载路径）
# ---------------------------------------------------------------------------


def test_legacy_field_with_binding_binding_wins(monkeypatch):
    """旧字段与绑定并存 → 检索以绑定为准（AC-51，YAML 加载路径）。"""
    config = _load_vkb_config(_LEGACY_WORKFLOW_YAML)
    binding = KBEmbeddingBinding(
        mode="local", model_identifier="bound-embed-model"
    )
    monkeypatch.setattr(
        VectorKnowledgeBaseNodeExecutor,
        "_try_resolve_binding",
        lambda self, knowledge_base_id: binding,
    )
    executor = VectorKnowledgeBaseNodeExecutor()

    effective_model = executor._resolve_effective_model(config)
    assert effective_model is not None
    assert effective_model.model_identifier == "bound-embed-model"
    assert effective_model.mode == "local"
    # 节点旧字段原样保留（值不作废，仅不生效）
    assert config.embedding_model is not None
    assert config.embedding_model.model_identifier == "text-embedding-v3"
    assert isinstance(
        executor._resolve_retriever(config), LocalKnowledgeBaseRetriever
    )


def test_legacy_field_without_binding_deterministic_fallback(monkeypatch):
    """绑定缺失 + 字段存在 → 确定性回退字段（AC-52，YAML 加载路径）。"""
    config = _load_vkb_config(_LEGACY_WORKFLOW_YAML)
    monkeypatch.setattr(
        VectorKnowledgeBaseNodeExecutor,
        "_try_resolve_binding",
        lambda self, knowledge_base_id: None,
    )
    executor = VectorKnowledgeBaseNodeExecutor()

    effective_model = executor._resolve_effective_model(config)
    assert effective_model is not None
    assert effective_model.model_identifier == "text-embedding-v3"
    assert effective_model.mode == "api"
    assert isinstance(
        executor._resolve_retriever(config), HttpKnowledgeBaseRetriever
    )


# ---------------------------------------------------------------------------
# 4. 既有执行器行为零变化（管理开关未启用 → 与改造前一致）
# ---------------------------------------------------------------------------


def test_executor_behavior_zero_change_with_management_disabled():
    """开关未启用（真实解析链路）：生效模型/检索器与改造前逐字一致。"""
    executor = VectorKnowledgeBaseNodeExecutor()
    config = _load_vkb_config(_LEGACY_WORKFLOW_YAML)

    # 真实 _try_resolve_binding：settings 无 vector_kb_management_enabled
    # （B7 前）/ 开关默认 False → None → 回退既有节点字段
    assert executor._try_resolve_binding(config.knowledge_base_id) is None
    effective_model = executor._resolve_effective_model(config)
    assert effective_model is not None
    assert effective_model is config.embedding_model
    assert effective_model.model_identifier == "text-embedding-v3"
    assert isinstance(
        executor._resolve_retriever(config), HttpKnowledgeBaseRetriever
    )

    # local 模式旧配置 → Local 检索器（既有分发不变，AC-25/AC-26）
    local_config = VectorKnowledgeBaseNodeConfiguration(
        knowledge_base_id="kb_main",
        query_value="查询文本",
        embedding_model=ModelLoadingConfig(
            mode="local", model_identifier="local-embed-model"
        ),
    )
    assert isinstance(
        executor._resolve_retriever(local_config), LocalKnowledgeBaseRetriever
    )


def test_neither_binding_nor_field_resolves_to_none(monkeypatch):
    """绑定与字段均缺失 → 生效模型 None（执行期确定性失败前置，AC-40）。"""
    config = _load_vkb_config(_FIELDLESS_WORKFLOW_YAML)
    monkeypatch.setattr(
        VectorKnowledgeBaseNodeExecutor,
        "_try_resolve_binding",
        lambda self, knowledge_base_id: None,
    )
    executor = VectorKnowledgeBaseNodeExecutor()

    effective_model: Optional[ModelLoadingConfig] = (
        executor._resolve_effective_model(config)
    )
    assert effective_model is None
