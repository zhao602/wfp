# =============================================================================
# YAML 定义生成器（governance/nl_orchestration/yaml_generator.py）
#
# 职责：将结构化意图中间表示转换为 YAML 工作流定义文本。
# 设计意图（对应偏差修正 spec 5.1.1 / design 模块A YamlGenerator）：
#   1. 输出完整五要素：definition_id / name / spec_version / definition_version
#      + nodes / edges + inputs / outputs（缺失 inputs/outputs 会被
#      DefinitionStructureValidator 判为「缺少必填要素」，DC-01）；
#   2. 字段别名归一：LLM 常写 model / prompt_template / tool_name / kind 等
#      同义键，统一映射为运行时真实字段名（model_identifier / system_prompt /
#      tool_identifier / control_flow_kind），落库语义不丢失（DC-02）；
#   3. 枚举纠偏：trigger_type / source_type / control_flow_kind / tool_source
#      非法值按同义词表归一，仍无法归一时回落到安全默认值并告警，避免
#      pydantic 字段校验直接失败（DC-03）；
#   4. 未知字段显式丢弃并告警（pydantic extra=ignore 会静默丢弃，此处提前
#      暴露问题，便于提示词侧持续收敛）；
#   5. 生成的 YAML 须可通过 YamlWorkflowParser 反向解析（AC-01）。
# =============================================================================

import logging
import uuid
from typing import Any, Dict, List, Optional, get_args

import yaml

from orchestration_platform.domain.models.enums import (
    ControlFlowKind,
    DataSourceType,
    NodeType,
    TriggerType,
)
from orchestration_platform.domain.models.node_configurations import NodeConfiguration
from orchestration_platform.governance.nl_orchestration.models import (
    ParsedEdgeSpec,
    ParsedIntent,
    ParsedNodeSpec,
)

_logger = logging.getLogger(__name__)

# 节点类型 → 配置模型（由判别联合即时派生，新增节点类型自动同步，零重复定义）
_CONFIG_CLASS_BY_TYPE = {
    config_cls.model_fields["node_type"].default: config_cls
    for config_cls in get_args(get_args(NodeConfiguration)[0])
}

# ---------------------------------------------------------------------------
# 字段别名表（按节点类型限定：键 = LLM 常见写法，值 = 运行时真实字段名）
# 只做「同义词 → 真实字段」映射，不引入新字段（INV-D03 精神）；
# 按类型限定可避免 type/source 这类泛化键跨类型误映射。
# ---------------------------------------------------------------------------
_FIELD_ALIASES_BY_TYPE: Dict[NodeType, Dict[str, str]] = {
    NodeType.AGENT: {
        "model": "model_identifier",
        "model_name": "model_identifier",
        "model_id": "model_identifier",
        "prompt": "system_prompt",
        "prompt_template": "system_prompt",
        "system_message": "system_prompt",
    },
    NodeType.LLM: {
        "model": "model_identifier",
        "model_name": "model_identifier",
        "model_id": "model_identifier",
        "prompt": "system_prompt",
        "prompt_template": "system_prompt",
    },
    NodeType.TOOL: {
        "tool": "tool_identifier",
        "tool_name": "tool_identifier",
        "tool_id": "tool_identifier",
    },
    NodeType.CONTROL_FLOW: {
        "kind": "control_flow_kind",
        "flow_kind": "control_flow_kind",
        "type": "control_flow_kind",
    },
    NodeType.TRIGGER: {
        "trigger": "trigger_type",
        "trigger_kind": "trigger_type",
    },
    NodeType.DATA_SOURCE: {
        "source": "source_type",
        "datasource_type": "source_type",
        "type": "source_type",
        "connection": "connection_reference",
        "connection_id": "connection_reference",
    },
}

# ---------------------------------------------------------------------------
# 枚举同义词表（无法命中的非法值统一回落默认值）
# ---------------------------------------------------------------------------
_TRIGGER_TYPE_SYNONYMS: Dict[str, str] = {
    "manual": TriggerType.MANUAL.value,
    "manual_or_api": TriggerType.MANUAL.value,
    "api": TriggerType.MANUAL.value,
    "user": TriggerType.MANUAL.value,
    "on_demand": TriggerType.MANUAL.value,
    "scheduled": TriggerType.SCHEDULED.value,
    "schedule": TriggerType.SCHEDULED.value,
    "cron": TriggerType.SCHEDULED.value,
    "timer": TriggerType.SCHEDULED.value,
    "interval": TriggerType.SCHEDULED.value,
    "data_event": TriggerType.DATA_EVENT.value,
    "event": TriggerType.DATA_EVENT.value,
    "webhook": TriggerType.DATA_EVENT.value,
    "event_driven": TriggerType.DATA_EVENT.value,
}

_SOURCE_TYPE_SYNONYMS: Dict[str, str] = {
    "csv": DataSourceType.CSV.value,
    "file": DataSourceType.CSV.value,
    "excel": DataSourceType.CSV.value,
    "json": DataSourceType.JSON.value,
    "rest_api": DataSourceType.REST_API.value,
    "restapi": DataSourceType.REST_API.value,
    "api": DataSourceType.REST_API.value,
    "http": DataSourceType.REST_API.value,
    "http_api": DataSourceType.REST_API.value,
    "database": DataSourceType.DATABASE.value,
    "db": DataSourceType.DATABASE.value,
    "sql": DataSourceType.DATABASE.value,
    "mysql": DataSourceType.DATABASE.value,
    "postgres": DataSourceType.DATABASE.value,
    "postgresql": DataSourceType.DATABASE.value,
    "vector_database": DataSourceType.DATABASE.value,
    "vector_db": DataSourceType.DATABASE.value,
    "vector": DataSourceType.DATABASE.value,
    "hologres": DataSourceType.HOLOGRES.value,
    "kafka": DataSourceType.KAFKA.value,
    "flink": DataSourceType.FLINK.value,
    "spark": DataSourceType.SPARK.value,
    "crm": DataSourceType.CRM.value,
    "erp": DataSourceType.ERP.value,
    "oa": DataSourceType.OA.value,
}

_CONTROL_FLOW_KIND_SYNONYMS: Dict[str, str] = {
    "conditional_branch": ControlFlowKind.CONDITIONAL_BRANCH.value,
    "branch": ControlFlowKind.CONDITIONAL_BRANCH.value,
    "condition": ControlFlowKind.CONDITIONAL_BRANCH.value,
    "conditional": ControlFlowKind.CONDITIONAL_BRANCH.value,
    "if": ControlFlowKind.CONDITIONAL_BRANCH.value,
    "switch": ControlFlowKind.CONDITIONAL_BRANCH.value,
    "loop": ControlFlowKind.LOOP.value,
    "while": ControlFlowKind.LOOP.value,
    "for": ControlFlowKind.LOOP.value,
    "iterate": ControlFlowKind.LOOP.value,
    "iteration": ControlFlowKind.LOOP.value,
    "parallel_join": ControlFlowKind.PARALLEL_JOIN.value,
    "parallel": ControlFlowKind.PARALLEL_JOIN.value,
    "fan_out": ControlFlowKind.PARALLEL_JOIN.value,
    "join": ControlFlowKind.PARALLEL_JOIN.value,
    "human_review_gate": ControlFlowKind.HUMAN_REVIEW_GATE.value,
    "human_review": ControlFlowKind.HUMAN_REVIEW_GATE.value,
    "review": ControlFlowKind.HUMAN_REVIEW_GATE.value,
    "approval": ControlFlowKind.HUMAN_REVIEW_GATE.value,
}

_TOOL_SOURCE_SYNONYMS: Dict[str, str] = {
    "local": "local",
    "builtin": "local",
    "platform": "local",
    "mcp": "mcp",
    "remote": "mcp",
}

# 各类型在字段缺失时的技术默认值（无业务语义，仅为满足 schema 必填约束）
_DEFAULT_VALUES: Dict[NodeType, Dict[str, Any]] = {
    NodeType.AGENT: {"model_identifier": "default-model"},
    NodeType.LLM: {"model_identifier": "default-model"},
    NodeType.TOOL: {"tool_identifier": "default_tool", "tool_source": "local"},
    NodeType.DATA_SOURCE: {
        "connection_reference": "default_connection",
        "query": "",
        "source_type": DataSourceType.REST_API.value,
    },
    NodeType.TRIGGER: {"trigger_type": TriggerType.MANUAL.value},
    NodeType.CONTROL_FLOW: {"control_flow_kind": ControlFlowKind.CONDITIONAL_BRANCH.value},
}


class YamlGenerator:
    """YAML 工作流定义生成器。

    将 NLIntentParser 产出的 ParsedIntent 映射为 YAML 文本。
    """

    def generate(self, parsed_intent: ParsedIntent) -> str:
        """将结构化意图转换为 YAML 工作流定义文本。

        前置条件：parsed_intent.nodes 非空且至少含一个触发器节点。
        后置条件：返回合法 YAML 文本，可通过 YamlWorkflowParser 反向解析。
        """
        document = self._build_document(parsed_intent)
        return yaml.dump(
            document,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        )

    def _build_document(self, parsed_intent: ParsedIntent) -> Dict[str, Any]:
        """构造 YAML 文档字典（五要素齐备，DC-01）。"""
        nodes_yaml = [self._build_node(node_spec) for node_spec in parsed_intent.nodes]
        edges_yaml = [self._build_edge(edge_spec, idx) for idx, edge_spec in enumerate(parsed_intent.edges)]

        return {
            "definition_id": f"wf_{uuid.uuid4().hex[:12]}",
            "name": parsed_intent.workflow_name or "自然语言生成的工作流",
            "spec_version": "1.0",
            "definition_version": 1,
            "nodes": nodes_yaml,
            "edges": edges_yaml,
            # 五要素中的输入/输出声明：结构校验要求必须存在（可为空列表）
            "inputs": [],
            "outputs": [],
        }

    def _build_node(self, node_spec: ParsedNodeSpec) -> Dict[str, Any]:
        """将 ParsedNodeSpec 映射为 YAML 节点字典。"""
        configuration = self._build_configuration(node_spec)
        return {
            "node_id": node_spec.node_id,
            "name": node_spec.name or node_spec.node_id,
            "node_type": node_spec.node_type.value,
            "configuration": configuration,
        }

    # ------------------------------------------------------------------
    # 配置归一
    # ------------------------------------------------------------------
    def _build_configuration(self, node_spec: ParsedNodeSpec) -> Dict[str, Any]:
        """按节点类型归一配置：别名映射 → 未知字段剔除 → 枚举纠偏 → 必填补默认。"""
        node_type = node_spec.node_type
        node_id = node_spec.node_id

        raw: Dict[str, Any] = dict(node_spec.configuration or {})
        config = self._apply_aliases(raw, node_type, node_id)
        config = self._drop_unknown_fields(config, node_type, node_id)

        if node_type == NodeType.TRIGGER:
            config = self._normalize_trigger(config, node_id)
        elif node_type == NodeType.CONTROL_FLOW:
            config = self._normalize_control_flow(config, node_id)
        elif node_type == NodeType.DATA_SOURCE:
            config = self._normalize_data_source(config, node_id)
        elif node_type == NodeType.TOOL:
            config = self._normalize_tool(config, node_id)

        # 技术默认值兜底（仅在字段缺失或为空时填充）
        for key, default_value in _DEFAULT_VALUES.get(node_type, {}).items():
            if not config.get(key):
                config[key] = default_value

        # Agent 节点：system_prompt 缺失时用节点说明兜底
        if node_type == NodeType.AGENT and not config.get("system_prompt"):
            config["system_prompt"] = node_spec.description or ""

        return config

    def _apply_aliases(
        self, config: Dict[str, Any], node_type: NodeType, node_id: str
    ) -> Dict[str, Any]:
        """同义字段名归一；真实字段已存在时别名让位（真实字段优先）。"""
        aliases = _FIELD_ALIASES_BY_TYPE.get(node_type, {})
        if not aliases:
            return config

        # 第一轮：真实字段原样保留
        normalized: Dict[str, Any] = {
            key: value for key, value in config.items() if key not in aliases
        }
        # 第二轮：别名补位，不覆盖已存在的真实字段
        for key, value in config.items():
            target = aliases.get(key)
            if target is None:
                continue
            if target in normalized:
                continue
            _logger.info("NL YAML 归一: 节点 %s 字段 %s 映射为 %s", node_id, key, target)
            normalized[target] = value
        return normalized

    def _drop_unknown_fields(
        self, config: Dict[str, Any], node_type: NodeType, node_id: str
    ) -> Dict[str, Any]:
        """剔除目标类型不认识的字段（pydantic extra=ignore 会静默丢弃，此处显式告警）。"""
        config_cls = _CONFIG_CLASS_BY_TYPE.get(node_type)
        if config_cls is None:
            return config
        allowed = set(config_cls.model_fields) - {"node_type"}
        dropped = sorted(set(config) - allowed)
        if dropped:
            _logger.warning(
                "NL YAML 归一: 节点 %s（%s）丢弃未知字段 %s",
                node_id,
                node_type.value,
                dropped,
            )
        return {key: value for key, value in config.items() if key in allowed}

    def _normalize_trigger(self, config: Dict[str, Any], node_id: str) -> Dict[str, Any]:
        """触发器枚举纠偏 + scheduled/data_event 前置条件兜底。"""
        trigger_type = self._normalize_enum(
            config.get("trigger_type"),
            _TRIGGER_TYPE_SYNONYMS,
            {item.value for item in TriggerType},
            TriggerType.MANUAL.value,
            node_id,
            "trigger_type",
        )
        # scheduled 缺 schedule / data_event 缺 event_source 会被配置校验判错 → 回落 manual
        if trigger_type == TriggerType.SCHEDULED.value and not config.get("schedule"):
            _logger.warning(
                "NL YAML 归一: 节点 %s 声明 scheduled 但缺少 schedule，回落 manual", node_id
            )
            trigger_type = TriggerType.MANUAL.value
        if trigger_type == TriggerType.DATA_EVENT.value and not config.get("event_source"):
            _logger.warning(
                "NL YAML 归一: 节点 %s 声明 data_event 但缺少 event_source，回落 manual", node_id
            )
            trigger_type = TriggerType.MANUAL.value
        config["trigger_type"] = trigger_type
        return config

    def _normalize_control_flow(self, config: Dict[str, Any], node_id: str) -> Dict[str, Any]:
        """控制流枚举纠偏 + 循环上限兜底（禁止无界循环）。"""
        kind = self._normalize_enum(
            config.get("control_flow_kind"),
            _CONTROL_FLOW_KIND_SYNONYMS,
            {item.value for item in ControlFlowKind},
            ControlFlowKind.CONDITIONAL_BRANCH.value,
            node_id,
            "control_flow_kind",
        )
        config["control_flow_kind"] = kind
        if kind == ControlFlowKind.LOOP.value and not config.get("max_iterations"):
            config["max_iterations"] = 10
        return config

    def _normalize_data_source(self, config: Dict[str, Any], node_id: str) -> Dict[str, Any]:
        """数据源类型枚举纠偏。"""
        config["source_type"] = self._normalize_enum(
            config.get("source_type"),
            _SOURCE_TYPE_SYNONYMS,
            {item.value for item in DataSourceType},
            DataSourceType.REST_API.value,
            node_id,
            "source_type",
        )
        return config

    def _normalize_tool(self, config: Dict[str, Any], node_id: str) -> Dict[str, Any]:
        """工具来源枚举纠偏。"""
        if config.get("tool_source"):
            config["tool_source"] = self._normalize_enum(
                config.get("tool_source"),
                _TOOL_SOURCE_SYNONYMS,
                {"local", "mcp"},
                "local",
                node_id,
                "tool_source",
            )
        return config

    @staticmethod
    def _normalize_enum(
        value: Any,
        synonyms: Dict[str, str],
        allowed: set,
        default: str,
        node_id: str,
        field_name: str,
    ) -> str:
        """把值归一到允许集合：合法值直通 → 同义词映射 → 默认值兜底。"""
        if value is None or value == "":
            return default
        text = str(value).strip().lower().replace("-", "_").replace(" ", "_")
        if text in allowed:
            return text
        mapped = synonyms.get(text)
        if mapped is not None and mapped in allowed:
            if mapped != text:
                _logger.info(
                    "NL YAML 归一: 节点 %s 字段 %s 的 %r 映射为 %r",
                    node_id,
                    field_name,
                    value,
                    mapped,
                )
            return mapped
        _logger.warning(
            "NL YAML 归一: 节点 %s 字段 %s 的值 %r 非法，回落默认 %r",
            node_id,
            field_name,
            value,
            default,
        )
        return default

    def _build_edge(self, edge_spec: ParsedEdgeSpec, index: int) -> Dict[str, Any]:
        """将 ParsedEdgeSpec 映射为 YAML 连线字典。"""
        edge: Dict[str, Any] = {
            "edge_id": f"edge_{index}",
            "source_node_id": edge_spec.source_node_id,
            "target_node_id": edge_spec.target_node_id,
        }
        if edge_spec.condition:
            edge["condition"] = edge_spec.condition
        return edge
