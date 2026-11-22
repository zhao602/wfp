# =============================================================================
# NL 意图解析器实现（governance/nl_orchestration/nl_intent_parser.py）
#
# 职责：调用 LLM 将自然语言指令解析为结构化工作流意图。
# 设计意图（对应偏差修正 spec 5.1.1 / design 模块A NLIntentParser）：
#   - 构造意图解析 Prompt（系统提示 + 用户指令 + 目标定义上下文）
#   - 调用 ILlmClient.infer，通过 output_schema 约束 LLM 输出为结构化 JSON
#   - 解析 LLM 返回为 ParsedIntent（节点列表 + 边列表 / 修改操作列表）
#   - LLM 超时或返回无法解析时抛出 NL_PARSE_FAILED
# =============================================================================

import json
import logging
from typing import Any, Dict, List, Optional

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.models.enums import IntentType, NodeType
from orchestration_platform.domain.models.workflow_definition import WorkflowDefinition
from orchestration_platform.governance.nl_orchestration.models import (
    NodeModification,
    ParsedEdgeSpec,
    ParsedIntent,
    ParsedNodeSpec,
)
from orchestration_platform.integrations.abstractions.external_clients import (
    ILlmClient,
    InferenceRequest,
)

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 节点配置字段清单（提示词与运行时 schema 同源维护：字段名/枚举值均取自
# domain/models/node_configurations.py 与 enums.py，避免提示词与校验器漂移）
# ---------------------------------------------------------------------------
_NODE_SCHEMA_GUIDE = """## 可用节点类型与 configuration 字段（只能用下列字段，不要臆造字段名）

- trigger（触发器，全流程有且仅有一个）：trigger_type: manual | scheduled | data_event（默认 manual）
  · scheduled 必须同时给 schedule（cron 表达式）；data_event 必须同时给 event_source
- agent（智能体）：model_identifier（模型标识，可留空字符串）、system_prompt（必填）、
  available_tools（字符串数组）、max_tool_rounds（整数）、output_schema（对象）
- llm（单次大模型调用）：model_identifier、system_prompt、user_prompt
- tool（确定性工具调用）：tool_identifier（工具标识，必填）、tool_source: local | mcp、
  argument_mapping（{参数名: 上游引用}）、static_arguments（{参数名: 常量}）
- data_source（只读数据源）：source_type: csv | json | rest_api | database | hologres |
  kafka | flink | spark | crm | erp | oa；connection_reference（连接标识，必填）；query（只读查询）
- vector_knowledge_base（向量知识库）：knowledge_base_id、mode: retrieve | write、
  query_mode: manual | reference、query_value 或 query_reference（二选一）、top_k（整数）
- control_flow（控制流）：control_flow_kind: conditional_branch | loop | parallel_join | human_review_gate
  · conditional_branch：conditions（数组）+ default_branch_target
  · loop：loop_body_entry、loop_termination_condition、max_iterations（整数，建议 ≤ 10）
- code（代码节点）：language（如 python）、code（源码字符串，必填）、entry_function
- output_display（输出展示）：input_reference（上游引用或引用数组）、display_title"""

_DATA_SHAPE_GUIDE = """## 输出 JSON 结构（只输出 JSON 本体，不要 Markdown 代码块、不要任何解释）

{
  "workflow_name": "工作流名称（≤30 字）",
  "description": "一句话用途说明",
  "nodes": [
    {
      "node_id": "小写字母/数字/下划线，全局唯一",
      "name": "节点名称",
      "node_type": "trigger | agent | llm | tool | data_source | vector_knowledge_base | control_flow | code | output_display",
      "configuration": {},
      "description": "节点职责说明"
    }
  ],
  "edges": [
    {"source_node_id": "源节点 ID", "target_node_id": "目标节点 ID", "condition": "条件表达式（可选）"}
  ]
}"""

_TOPOLOGY_RULES = """## 硬性约束（违反任一条都会导致定义不可用）

1. 有且仅有一个 trigger 节点作为入口；
2. 每个节点都必须能从 trigger 到达，且都能到达终点，不允许孤立节点或悬空分支；
3. 不允许出现环；需要循环时改用 control_flow（control_flow_kind=loop）；
4. 边的两端必须是 nodes 中真实存在的 node_id；
5. 引用上游数据时写裸表达式 `节点ID.字段路径`（例如 `retrieve.documents`），
   不要加 {{ }}、不要写 .output.、不要引用尚未出现的下游节点；
6. 枚举字段只能取上面列出的值；缺省时按括号中的默认值处理。"""

# 创建工作流的系统提示词
_CREATE_SYSTEM_PROMPT = (
    "你是工作流编排助手。用户用自然语言描述一个工作流，你要把它转换成可直接落库的"
    "工作流定义 JSON。请严格遵守下面的结构与约束，宁可少用节点也不要臆造字段。\n\n"
    + _DATA_SHAPE_GUIDE
    + "\n\n"
    + _NODE_SCHEMA_GUIDE
    + "\n\n"
    + _TOPOLOGY_RULES
    + "\n\n## 示例（“先检索文档再生成摘要”）\n"
    '{"workflow_name":"文档检索摘要","description":"先检索文档，再由智能体生成摘要",'
    '"nodes":['
    '{"node_id":"start","name":"开始","node_type":"trigger",'
    '"configuration":{"trigger_type":"manual"},"description":"人工触发入口"},'
    '{"node_id":"retrieve","name":"检索文档","node_type":"vector_knowledge_base",'
    '"configuration":{"knowledge_base_id":"kb_default","mode":"retrieve",'
    '"query_mode":"manual","query_value":"","top_k":5},"description":"检索相关知识片段"},'
    '{"node_id":"summarize","name":"生成摘要","node_type":"agent",'
    '"configuration":{"model_identifier":"","available_tools":[],"max_tool_rounds":3,'
    '"system_prompt":"根据检索结果生成简洁摘要，只使用检索内容，不要编造"},"description":"生成摘要"},'
    '{"node_id":"show","name":"输出","node_type":"output_display",'
    '"configuration":{"input_reference":"summarize","display_title":"摘要结果"},"description":"展示结果"}'
    '],'
    '"edges":[{"source_node_id":"start","target_node_id":"retrieve"},'
    '{"source_node_id":"retrieve","target_node_id":"summarize"},'
    '{"source_node_id":"summarize","target_node_id":"show"}]}'
)

# 修改工作流的系统提示词
_MODIFY_SYSTEM_PROMPT = (
    "你是工作流编排助手。用户会对既有工作流提出修改指令，你要把它解析为结构化的修改操作列表。\n\n"
    "## 输出 JSON 结构（只输出 JSON 本体，不要 Markdown 代码块、不要任何解释）\n"
    "{\n"
    '  "description": "修改说明",\n'
    '  "modifications": [\n'
    "    {\n"
    '      "action": "insert | delete | update",\n'
    '      "target_node_id": "被修改的节点 ID（delete/update 必填，必须存在于既有工作流）",\n'
    '      "anchor_node_id": "插入锚点节点 ID（insert 必填，必须存在于既有工作流）",\n'
    '      "insert_position": "before | after（insert 必填）",\n'
    '      "node_spec": {"node_id": "", "name": "", "node_type": "", "configuration": {}, "description": ""},\n'
    '      "edge_specs": [{"source_node_id": "", "target_node_id": "", "condition": ""}]\n'
    "    }\n"
    "  ]\n"
    "}\n\n"
    + _NODE_SCHEMA_GUIDE
    + "\n\n## 硬性约束\n"
    "1. delete/update 的 target_node_id、insert 的 anchor_node_id 必须是既有工作流里出现的节点 ID；\n"
    "2. insert 后仍需保持“有且仅有一个 trigger”与全图连通；\n"
    "3. 枚举字段只能取上面列出的值，configuration 不要臆造字段名；\n"
    "4. 引用上游数据写裸表达式 `节点ID.字段路径`，不要加 {{ }}。"
)

# LLM 输出结构约束
_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "workflow_name": {"type": "string"},
        "description": {"type": "string"},
        "nodes": {"type": "array"},
        "edges": {"type": "array"},
        "modifications": {"type": "array"},
    },
}


class NLIntentParser:
    """自然语言意图解析器。

    构造注入 ILlmClient，通过 Prompt 工程将自然语言指令解析为 ParsedIntent。
    """

    def __init__(self, llm_client: ILlmClient) -> None:
        """初始化意图解析器。

        参数：
            llm_client：LLM 推理客户端（支持主备切换）
        """
        self._llm_client = llm_client

    async def parse(
        self,
        directive: str,
        intent_type: IntentType,
        target_definition: Optional[WorkflowDefinition] = None,
        feedback: Optional[str] = None,
    ) -> ParsedIntent:
        """解析自然语言指令为结构化意图。

        前置条件：directive 非空且已通过注入防护校验。
        后置条件：返回 ParsedIntent。
        异常映射：抛出 ValueError（含 NL_PARSE_FAILED 语义）当 LLM 不可用或返回无法解析。

        参数 feedback：上一次生成的校验问题清单（自纠错重试时传入）；
        为空表示首轮生成。
        """
        settings = get_settings()
        model_identifier = settings.nl_orchestration_model or settings.llm_primary_model

        # 构造系统提示与用户消息
        if intent_type == IntentType.CREATE_WORKFLOW:
            system_prompt = _CREATE_SYSTEM_PROMPT
            user_message = directive
        else:
            system_prompt = _MODIFY_SYSTEM_PROMPT
            user_message = self._build_modify_message(directive, target_definition)

        if feedback:
            user_message = (
                f"{user_message}\n\n"
                "上一次生成的结果未通过校验，问题如下：\n"
                f"{feedback}\n\n"
                "请针对上述问题修正后重新输出完整 JSON（只输出 JSON，不要解释）。"
            )

        # 构造推理请求
        request = InferenceRequest(
            model_identifier=model_identifier,
            system_prompt=system_prompt,
            user_message=user_message,
            output_schema=_OUTPUT_SCHEMA,
        )

        # 调用 LLM
        try:
            result = await self._llm_client.infer(request)
        except Exception as exc:
            _logger.error("NL 意图解析 LLM 调用失败: %s", exc)
            raise ValueError(f"NL_PARSE_FAILED: LLM 调用异常 - {exc}") from exc

        if not result.success:
            _logger.error("NL 意图解析失败: %s", result.error_message)
            raise ValueError(f"NL_PARSE_FAILED: {result.error_message}")

        # 解析 LLM 返回为 ParsedIntent
        return self._parse_llm_output(result.structured_output, result.content, intent_type)

    def _build_modify_message(
        self,
        directive: str,
        target_definition: Optional[WorkflowDefinition],
    ) -> str:
        """构造修改意图的用户消息（含既有工作流上下文）。"""
        if target_definition is None:
            return directive

        # 序列化既有工作流定义为上下文
        nodes_desc = []
        for node in target_definition.nodes:
            nodes_desc.append(
                f"  - {node.node_id}（{node.name}，类型: {node.node_type.value}）"
            )
        edges_desc = []
        for edge in target_definition.edges:
            cond = f" 条件: {edge.condition}" if hasattr(edge, "condition") and edge.condition else ""
            edges_desc.append(f"  - {edge.source_node_id} → {edge.target_node_id}{cond}")

        context = (
            f"既有工作流定义：\n"
            f"  名称: {target_definition.name}\n"
            f"  节点列表:\n"
            + "\n".join(nodes_desc)
            + f"\n  连线列表:\n"
            + "\n".join(edges_desc)
            + f"\n\n修改指令：{directive}"
        )
        return context

    def _parse_llm_output(
        self,
        structured_output: Optional[Dict[str, Any]],
        raw_content: str,
        intent_type: IntentType,
    ) -> ParsedIntent:
        """将 LLM 输出解析为 ParsedIntent。

        优先使用 structured_output，回退到 raw_content JSON 解析。
        """
        # 获取解析数据
        data: Dict[str, Any]
        if structured_output is not None:
            data = structured_output
        else:
            try:
                data = json.loads(raw_content)
            except (json.JSONDecodeError, TypeError) as exc:
                _logger.error("NL 意图解析 LLM 返回无法解析: %s", exc)
                raise ValueError(f"NL_PARSE_FAILED: LLM 返回无法解析 - {exc}") from exc

        if not isinstance(data, dict):
            raise ValueError(
                f"NL_PARSE_FAILED: LLM 返回的 JSON 顶层必须是对象，实际为 {type(data).__name__}"
            )

        # 构造 ParsedIntent
        nodes: List[ParsedNodeSpec] = []
        edges: List[ParsedEdgeSpec] = []
        modifications: List[NodeModification] = []

        for node_data in data.get("nodes") or []:
            if not isinstance(node_data, dict):
                continue
            nodes.append(self._parse_node_spec(node_data))

        for edge_data in data.get("edges") or []:
            if not isinstance(edge_data, dict):
                continue
            edges.append(
                ParsedEdgeSpec(
                    source_node_id=edge_data.get("source_node_id", ""),
                    target_node_id=edge_data.get("target_node_id", ""),
                    condition=edge_data.get("condition"),
                )
            )

        for mod_data in data.get("modifications") or []:
            if not isinstance(mod_data, dict):
                continue
            modifications.append(self._parse_modification(mod_data))

        # 创建意图必须产出节点（空节点无法生成可用定义，交由自纠错循环重试）
        if intent_type == IntentType.CREATE_WORKFLOW and not nodes:
            raise ValueError("NL_PARSE_FAILED: LLM 未产出任何节点（nodes 为空）")

        return ParsedIntent(
            intent_type=intent_type,
            workflow_name=data.get("workflow_name", ""),
            nodes=nodes,
            edges=edges,
            modifications=modifications,
            description=data.get("description", ""),
        )

    def _parse_node_spec(self, node_data: Dict[str, Any]) -> ParsedNodeSpec:
        """解析节点规格。"""
        node_type_str = node_data.get("node_type", "agent")
        try:
            node_type = NodeType(node_type_str)
        except ValueError:
            node_type = NodeType.AGENT

        return ParsedNodeSpec(
            node_id=node_data.get("node_id", ""),
            name=node_data.get("name", ""),
            node_type=node_type,
            configuration=node_data.get("configuration", {}),
            description=node_data.get("description", ""),
        )

    def _parse_modification(self, mod_data: Dict[str, Any]) -> NodeModification:
        """解析修改操作。"""
        node_spec = None
        if mod_data.get("node_spec"):
            node_spec = self._parse_node_spec(mod_data["node_spec"])

        edge_specs = []
        for edge_data in mod_data.get("edge_specs", []):
            edge_specs.append(
                ParsedEdgeSpec(
                    source_node_id=edge_data.get("source_node_id", ""),
                    target_node_id=edge_data.get("target_node_id", ""),
                    condition=edge_data.get("condition"),
                )
            )

        return NodeModification(
            action=mod_data.get("action", "insert"),
            target_node_id=mod_data.get("target_node_id"),
            anchor_node_id=mod_data.get("anchor_node_id"),
            insert_position=mod_data.get("insert_position"),
            node_spec=node_spec,
            edge_specs=edge_specs,
        )
