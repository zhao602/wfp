# =============================================================================
# 内置 5 个 Agent 模板预置常量（market/agent_templates.py）
#
# 职责（agent_tool_marketplace DC-05 / OP-2 / T2）：
#   1. 以代码内预置常量交付 5 个内置模板（数量恒 5，AC-12）；
#   2. 五维度文档均非空且为原理层描述（不含代码块与内部函数名，AC-16）；
#   3. 预设键集合 ⊆ TEMPLATE_PRESET_ALLOWED_KEYS（排除 sandbox_policy_id，INV-D10）；
#   4. recipe 节点一律复用既有节点类型与配置字段（INV-D03，零新增）；
#   5. 模块导入即校验（fail-fast，DC-12）。
#
# 差异化改造（模板互斥定位）：
#   - basic_react（single_node）：最简单环入门基线（提示词层）；
#   - plan_and_execute（recipe）：规划 Agent → 人工评审门 → 执行 Agent；
#   - reflexive_agent（recipe）：初稿 Agent → 独立 LLM 批判节点 → 修订 Agent；
#   - multi_agent（recipe）：协调 + 三路并行（含向量知识库检索分支）；
#   - autonomous_loop（recipe）：自主循环 + 上下文压缩（context_strategy）。
# =============================================================================

from typing import Dict, FrozenSet, List, Optional, Tuple

from orchestration_platform.domain.models.enums import NodeType
from orchestration_platform.domain.models.node_configurations import (
    AgentNodeConfiguration,
    ControlFlowNodeConfiguration,
    LlmNodeConfiguration,
    OutputDisplayNodeConfiguration,
    TriggerNodeConfiguration,
    VectorKnowledgeBaseNodeConfiguration,
)
from orchestration_platform.market.models import (
    AgentTemplate,
    TemplateDoc,
    TemplateEdgePreset,
    TemplateMetric,
    TemplateNodePreset,
    TemplatePerformanceBoundaries,
    TemplatePreset,
    TemplateToolConfig,
)
from orchestration_platform.tool_discovery.models import ToolSource

# 预设键白名单（T2.1）：恰含 8 键，排除 sandbox_policy_id（前端 TS 无该键，INV-D10）。
# context_strategy 为差异化扩展新增（与 AgentNodeConfiguration 同步扩展，INV-D04 子集关系保持）。
TEMPLATE_PRESET_ALLOWED_KEYS: FrozenSet[str] = frozenset(
    {
        "model_identifier",
        "system_prompt",
        "available_tools",
        "max_tool_rounds",
        "output_max_retries",
        "output_schema",
        "inputs",
        "context_strategy",
    }
)

# 通用安全边界（设计 §3.2.1 安全项，AC-18）
_COMMON_SECURITY_BOUNDARIES: List[str] = [
    "遵循工具 permission 标注",
    "沙箱策略沿用节点 sandbox_policy_id",
    "未绑定工具对 LLM 不可见",
    "预设零凭据",
]

# 通用参数约束 / 失败重试与异常处理策略（设计 §3.2.6 矩阵基准表述）
_COMMON_PARAMETER_CONSTRAINTS: str = (
    "依各工具 input_schema 约束传入；调用入参解析失败按 NODE_TOOL_ARGUMENT_MISMATCH 处理"
    "（观察回填，不终止节点）"
)
_COMMON_RETRY_AND_EXCEPTION_POLICY: str = (
    "单次工具失败 → 回填观察继续（受 max_tool_rounds 约束）；"
    "工具调用超时 → NODE_TOOL_TIMEOUT 观察回填；"
    "工具不存在 → 降级为「工具不存在」观察；"
    "未绑定工具对 LLM 不可见；轮次耗尽仍有调用意图 → 节点失败终止"
)

# ---------------------------------------------------------------------------
# 模板 1/5：Plan-and-Execute（kind=recipe，差异化改造：规划→人工评审门→执行）
# ---------------------------------------------------------------------------

_PLAN_AND_EXECUTE_PLANNER_PROMPT: str = (
    "你是规划 Agent：仅产出结构化执行计划（明确的步骤清单，每步标注目标与所需工具），"
    "不执行任何工具、不做任何行动。计划经人工评审通过后，"
    "将由执行 Agent 按计划逐步推进。"
)

_PLAN_AND_EXECUTE_EXECUTOR_PROMPT: str = (
    "你是执行 Agent：按上游传入的计划逐步执行，每步可调用工具获取事实，"
    "观察结果回填后推进下一步，直至计划执行完毕或轮次耗尽；"
    "计划与实际不符时在结果中说明偏差。"
    "最终输出必须包含执行结果汇总（result）。"
)

_PLAN_AND_EXECUTE = AgentTemplate(
    template_id="plan_and_execute",
    name="Plan-and-Execute",
    kind="recipe",
    preset=TemplatePreset(
        system_prompt=_PLAN_AND_EXECUTE_PLANNER_PROMPT,
        available_tools=[],
        max_tool_rounds=2,
        output_schema={"plan": "array"},
    ),
    system_prompt=_PLAN_AND_EXECUTE_PLANNER_PROMPT,
    allowed_tools=[],
    max_tool_rounds=None,  # recipe：各节点独立设置轮次（节点间不共享预算）
    output_max_retries=None,
    doc=TemplateDoc(
        core_principle_flow=(
            "将任务显式拆成「规划 → 人工评审 → 执行」三段：规划 Agent 仅产出结构化计划"
            "（步骤清单），计划经人工评审门（human_review_gate）审阅通过后，"
            "执行 Agent 逐步执行计划中的每一步；执行阶段的每一步可调用外部工具获取事实，"
            "观察结果回填后继续推进下一步。流程要点："
            "任务输入 → 生成计划 → 人工评审（驳回即终止）→ 逐条执行（含工具调用）→ 汇总结果。"
        ),
        application_features=(
            "计划可被人工审阅与干预（评审驳回即终止实例）、可分步校验（失败可定位到具体步骤）、"
            "适合需要「先想清楚、经人确认、再动手」的高风险场景；"
            "规划与执行节点各自独立设置轮次预算，互不挤占。"
        ),
        applicable_scenarios=(
            "多步复杂且高风险的任务：步骤可追溯、执行动作需人工把关"
            "（如对外发布、资源变更）、步骤间存在依赖关系的任务。"
        ),
        advantages_limitations=(
            "优势：结构清晰可控、计划经人工确认后错误传导风险大幅降低、失败定位容易；"
            "局限：引入人工评审等待（吞吐下降）、规划阶段质量仍依赖模型、多节点调试更复杂。"
        ),
        comparison=(
            "与基础 ReAct 对比：ReAct 无显式规划层且无人工干预点，适合低风险短程任务；"
            "Plan-and-Execute 先规划、经人工评审、后执行，以等待开销换取可控性与安全性。"
            "与 Autonomous Loop 对比：后者自主推进无需人工介入，本模板强调人机协同把关。"
        ),
    ),
    tool_config=TemplateToolConfig(
        tool_types=[ToolSource.PLATFORM, ToolSource.MCP, ToolSource.LOCAL],
        invocation_mode="recipe_nodes",
        parameter_constraints=(
            "各节点独立依工具 input_schema 约束传入；"
            "调用入参解析失败按 NODE_TOOL_ARGUMENT_MISMATCH 处理（观察回填，不终止节点）"
        ),
        retry_and_exception_policy=(
            "单次工具失败 → 回填观察继续（受节点 max_tool_rounds 约束）；"
            "工具调用超时 → NODE_TOOL_TIMEOUT 观察回填；"
            "人工评审驳回或超时 → 实例按 review_timeout_action=reject 终止"
        ),
    ),
    nodes=[
        TemplateNodePreset(
            local_ref="entry",
            node_type=NodeType.TRIGGER,
            name="入口",
            configuration={"trigger_type": "manual", "input_template": {}},
        ),
        TemplateNodePreset(
            local_ref="planner",
            node_type=NodeType.AGENT,
            name="规划 Agent",
            configuration={
                "model_identifier": "",
                "system_prompt": _PLAN_AND_EXECUTE_PLANNER_PROMPT,
                "available_tools": [],
                "max_tool_rounds": 2,
                "output_schema": {"plan": "array"},
            },
        ),
        TemplateNodePreset(
            local_ref="review_gate",
            node_type=NodeType.CONTROL_FLOW,
            name="人工评审门",
            configuration={
                "control_flow_kind": "human_review_gate",
                "review_instructions": (
                    "审阅执行计划：步骤是否完整、依赖是否成立、工具是否够用；"
                    "驳回将终止实例"
                ),
                # review_timeout_action 缺省即 "reject"（驳回终止），显式省略以精简预设
            },
        ),
        TemplateNodePreset(
            local_ref="executor",
            node_type=NodeType.AGENT,
            name="执行 Agent",
            configuration={
                "model_identifier": "",
                "system_prompt": _PLAN_AND_EXECUTE_EXECUTOR_PROMPT,
                "available_tools": ["get_workflow", "validate_workflow", "start_workflow"],
                "max_tool_rounds": 8,
            },
        ),
        TemplateNodePreset(
            local_ref="exit",
            node_type=NodeType.OUTPUT_DISPLAY,
            name="结果输出",
            configuration={},
        ),
    ],
    edges=[
        TemplateEdgePreset(source_ref="entry", target_ref="planner"),
        TemplateEdgePreset(source_ref="planner", target_ref="review_gate"),
        TemplateEdgePreset(source_ref="review_gate", target_ref="executor"),
        TemplateEdgePreset(source_ref="executor", target_ref="exit"),
    ],
    implementation_key_points=[
        "规划 Agent 仅产出计划（不执行工具），执行 Agent 按计划逐步推进（职责分离）",
        "人工评审门复用既有 human_review_gate 子类型，驳回即终止实例",
        "执行 Agent 轮次预算独立设置（8 轮），规划阶段不占用执行轮次",
    ],
    dependency_conditions=[
        "既有 Agent 节点与 ReAct 闭环",
        "既有 control_flow 人工评审门节点（human_review_gate + review_instructions）",
        "tool_invokers 工具装配",
        "ILlmClient（推理下游）",
    ],
    performance_boundaries=TemplatePerformanceBoundaries(
        max_tool_rounds=8, tool_timeout_seconds=60, output_max_retries=None
    ),
    security_boundaries=list(_COMMON_SECURITY_BOUNDARIES)
    + ["人工评审驳回即终止，无人工确认不产生副作用"],
    evaluation_metrics=[
        TemplateMetric(metric="template_apply_success_rate", collectable="static"),
        TemplateMetric(metric="tool_rounds_used", collectable="runtime"),
        TemplateMetric(metric="tool_calls_executed", collectable="runtime"),
        TemplateMetric(metric="output_schema_compliance_rate", collectable="runtime"),
        TemplateMetric(metric="human_review_outcomes", collectable="runtime"),
        TemplateMetric(metric="end_to_end_latency", collectable="not_quantifiable"),
    ],
)

# ---------------------------------------------------------------------------
# 模板 2/5：基础 ReAct（kind=single_node，设计 §3.2.2）
# ---------------------------------------------------------------------------

_BASIC_REACT_SYSTEM_PROMPT: str = (
    "你是轻量 ReAct 智能体，按「推理 → 行动 → 观察 → 再推理」的单环闭环工作："
    "每一轮先思考，判断是否已可作答或需要外部信息；"
    "需要外部信息时发出工具调用意图并执行工具，将观察结果回填上下文后进入下一轮思考；"
    "无需工具即收敛输出最终答案。"
    "坚持最短路径：能一次回答就不多余轮次，能用一次工具就不过度推理。"
)

_BASIC_REACT = AgentTemplate(
    template_id="basic_react",
    name="基础 ReAct",
    kind="single_node",
    preset=TemplatePreset(
        system_prompt=_BASIC_REACT_SYSTEM_PROMPT,
        available_tools=[],  # 通用集由编排者按需勾选
        max_tool_rounds=None,  # 缺省回落全局 agent_max_tool_rounds=5（AC-13 规则 3）
        output_max_retries=None,
        output_schema=None,
        inputs=None,
    ),
    system_prompt=_BASIC_REACT_SYSTEM_PROMPT,
    allowed_tools=[],
    max_tool_rounds=None,
    output_max_retries=None,
    doc=TemplateDoc(
        core_principle_flow=(
            "Reasoning 与 Acting 交替的单环闭环：每一轮先推理（判断是否需要外部信息），"
            "需要则发出工具调用意图并执行工具，观察结果回填上下文后进入下一轮推理；"
            "无工具意图即收敛输出。流程要点：推理 → 行动 → 观察 → 再推理。"
        ),
        application_features=(
            "实现最简单、门槛最低、开销最小（五类模板中的入门基线）；"
            "单环结构无额外阶段、无规划层、无批判环节、无人工干预点；"
            "适合工具调用轮次少的任务与快速验证。"
        ),
        applicable_scenarios=(
            "单跳或少步工具调用任务、问答/查询类任务、对实现复杂度敏感的项目。"
        ),
        advantages_limitations=(
            "优势：实现最简、延迟低、无规划开销；"
            "局限：无长程规划（易陷入局部最优）、多步任务中容易在早期偏离目标、轮次敏感。"
        ),
        comparison=(
            "五模板定位对照（本模板为「最低门槛基线」）："
            "无规划层——需要先想清楚再动手、或需人工把关时选 Plan-and-Execute；"
            "无批判环节——对输出质量敏感时选 Reflexive Agent；"
            "无多角色协作——需要分工与并行时选 Multi-Agent；"
            "无自主循环——步数不定、需自主推进的长程任务选 Autonomous Loop。"
        ),
    ),
    tool_config=TemplateToolConfig(
        tool_types=[ToolSource.PLATFORM, ToolSource.MCP, ToolSource.LOCAL],
        invocation_mode="react_loop",
        parameter_constraints=_COMMON_PARAMETER_CONSTRAINTS,
        retry_and_exception_policy=_COMMON_RETRY_AND_EXCEPTION_POLICY,
    ),
    nodes=[],
    edges=[],
    implementation_key_points=[
        "提示词明确「先思考再行动」的交替格式",
        "工具选择集按任务最小化（减少无效轮次）",
        "不设节点级轮次上限以复用全局策略",
    ],
    dependency_conditions=[
        "既有 Agent 节点与 ReAct 闭环",
        "ILlmClient（推理下游）",
        "tool_invokers 工具装配",
    ],
    performance_boundaries=TemplatePerformanceBoundaries(
        max_tool_rounds=None, tool_timeout_seconds=60, output_max_retries=None
    ),
    security_boundaries=list(_COMMON_SECURITY_BOUNDARIES),
    evaluation_metrics=[
        TemplateMetric(metric="tool_rounds_used", collectable="runtime"),
        TemplateMetric(metric="tool_calls_executed", collectable="runtime"),
        TemplateMetric(metric="template_apply_success_rate", collectable="static"),
        TemplateMetric(metric="end_to_end_latency", collectable="not_quantifiable"),
    ],
)

# ---------------------------------------------------------------------------
# 模板 3/5：Multi-Agent（kind=recipe，设计 §3.2.3）
# ---------------------------------------------------------------------------

_MULTI_AGENT_COORDINATOR_PROMPT: str = (
    "你是多智能体协作的主协调 Agent：将任务分解为若干子任务并委派给合适的子任务分支"
    "（事实查询交检索子 Agent、知识库资料交知识库检索节点、推理加工交分析子 Agent），"
    "等待分支结果返回后汇总整合，产出最终答案；分解时明确各子任务的目标与边界。"
    "知识库检索结果由 vkb_retrieve 节点确定性产出，与检索子 Agent 的动态查询结果一并汇总。"
)

_MULTI_AGENT = AgentTemplate(
    template_id="multi_agent",
    name="Multi-Agent",
    kind="recipe",
    preset=TemplatePreset(
        system_prompt=_MULTI_AGENT_COORDINATOR_PROMPT,
        available_tools=[],
        max_tool_rounds=3,
    ),
    system_prompt=_MULTI_AGENT_COORDINATOR_PROMPT,
    allowed_tools=[],
    max_tool_rounds=None,  # recipe：各节点独立设置轮次（节点间不共享预算）
    output_max_retries=None,
    doc=TemplateDoc(
        core_principle_flow=(
            "多智能体分工协作（角色化）：以一个主 Agent 承担任务分解与结果汇总，"
            "若干子任务分支各自承担专门角色（动态检索、知识库确定性检索、推理分析）；"
            "节点间经既有控制流/子工作流协作，各 Agent 节点各自运行独立的 ReAct 闭环，"
            "知识库分支为向量知识库节点的确定性检索。"
            "流程要点：主 Agent 分解 → 三路分支并行处理 → 主 Agent 汇总。"
        ),
        application_features=(
            "能力解耦（每角色专注一类能力）、可并行（三路分支无依赖时并行）、"
            "角色可独立配置工具选择集与提示词、知识库分支零 LLM 开销（确定性检索）。"
        ),
        applicable_scenarios=(
            "复杂任务需多角色协作（如「检索 + 分析 + 撰写」）、职责分离要求明确的场景。"
        ),
        advantages_limitations=(
            "优势：专业分工带来更高的单环节质量、可并行提速；"
            "局限：协调成本高（主 Agent 分解质量决定整体）、"
            "误差累积（子环节错误会在汇总放大）、多节点调试更复杂。"
        ),
        comparison=(
            "与单 Agent 对比：单 Agent 无协作开销但能力受限；"
            "Multi-Agent 以协作开销换取专业化与并行能力。"
        ),
    ),
    tool_config=TemplateToolConfig(
        tool_types=[ToolSource.PLATFORM, ToolSource.MCP, ToolSource.LOCAL],
        invocation_mode="recipe_nodes",
        parameter_constraints=(
            "各节点独立依工具 input_schema 约束传入；"
            "调用入参解析失败按 NODE_TOOL_ARGUMENT_MISMATCH 处理（观察回填，不终止节点）"
        ),
        retry_and_exception_policy=(
            "各节点沿用同一失败/超时/轮次策略，节点间不共享轮次预算（各自独立）；"
            "并行分支失败沿用 parallel_failure_strategy（fast_fail）"
        ),
    ),
    nodes=[
        TemplateNodePreset(
            local_ref="entry",
            node_type=NodeType.TRIGGER,
            name="入口",
            configuration={"trigger_type": "manual", "input_template": {}},
        ),
        TemplateNodePreset(
            local_ref="coordinator",
            node_type=NodeType.AGENT,
            name="主协调 Agent",
            configuration={
                "model_identifier": "",
                "system_prompt": _MULTI_AGENT_COORDINATOR_PROMPT,
                "available_tools": [],
                "max_tool_rounds": 3,
            },
        ),
        TemplateNodePreset(
            local_ref="worker_retrieval",
            node_type=NodeType.AGENT,
            name="检索子 Agent",
            configuration={
                "model_identifier": "",
                "system_prompt": (
                    "你是检索子 Agent：仅使用只读查询类工具获取事实与资料，"
                    "不做分析加工，输出检索到的原始要点。"
                ),
                "available_tools": ["get_workflow"],
                "max_tool_rounds": 5,
            },
        ),
        TemplateNodePreset(
            local_ref="vkb_retrieve",
            node_type=NodeType.VECTOR_KNOWLEDGE_BASE,
            name="知识库检索",
            configuration={
                "knowledge_base_id": "",  # 编排者必填（与 model_identifier 空值约定一致）
                "query_mode": "manual",
                "top_k": 5,
            },
        ),
        TemplateNodePreset(
            local_ref="worker_analysis",
            node_type=NodeType.AGENT,
            name="分析子 Agent",
            configuration={
                "model_identifier": "",
                "system_prompt": "你是分析子 Agent：基于上游输入的资料进行推理分析，输出结论与依据。",
                "available_tools": [],
                "max_tool_rounds": 5,
            },
        ),
        TemplateNodePreset(
            local_ref="aggregate",
            node_type=NodeType.CONTROL_FLOW,
            name="并行汇聚",
            configuration={
                "control_flow_kind": "parallel_join",
                "parallel_branches": [
                    "worker_retrieval",
                    "vkb_retrieve",
                    "worker_analysis",
                ],
                "parallel_failure_strategy": "fast_fail",
            },
        ),
        TemplateNodePreset(
            local_ref="exit",
            node_type=NodeType.OUTPUT_DISPLAY,
            name="结果输出",
            configuration={},
        ),
    ],
    edges=[
        TemplateEdgePreset(source_ref="entry", target_ref="coordinator"),
        TemplateEdgePreset(source_ref="coordinator", target_ref="worker_retrieval"),
        TemplateEdgePreset(source_ref="coordinator", target_ref="vkb_retrieve"),
        TemplateEdgePreset(source_ref="coordinator", target_ref="worker_analysis"),
        TemplateEdgePreset(source_ref="worker_retrieval", target_ref="aggregate"),
        TemplateEdgePreset(source_ref="vkb_retrieve", target_ref="aggregate"),
        TemplateEdgePreset(source_ref="worker_analysis", target_ref="aggregate"),
        TemplateEdgePreset(source_ref="aggregate", target_ref="coordinator"),
        TemplateEdgePreset(source_ref="coordinator", target_ref="exit"),
    ],
    implementation_key_points=[
        "主 Agent 提示词引导「分解—委派—汇总」",
        "各子 Agent/分支职责互不重叠（检索动态查询、知识库确定性检索、纯分析）",
        "知识库检索复用既有向量知识库节点（knowledge_base_id 由编排者补填）",
        "并行分支声明复用既有 parallel_branches / parallel_failure_strategy 字段",
    ],
    dependency_conditions=[
        "既有 Agent 节点与 ReAct 闭环",
        "既有 control_flow 并行汇聚节点（parallel_join + parallel_branches）",
        "既有向量知识库节点（VectorKnowledgeBaseNodeConfiguration，knowledge_base_id 编排者补填）",
        "tool_invokers 工具装配",
        "ILlmClient（推理下游）",
    ],
    performance_boundaries=TemplatePerformanceBoundaries(
        max_tool_rounds=None, tool_timeout_seconds=60, output_max_retries=None
    ),
    security_boundaries=list(_COMMON_SECURITY_BOUNDARIES)
    + ["预设不含绕过沙箱或越权 admin 工具的项"],
    evaluation_metrics=[
        TemplateMetric(metric="template_apply_success_rate", collectable="static"),
        TemplateMetric(metric="tool_rounds_used", collectable="runtime"),
        TemplateMetric(metric="tool_calls_executed", collectable="runtime"),
        TemplateMetric(metric="output_schema_compliance_rate", collectable="runtime"),
        TemplateMetric(metric="end_to_end_latency", collectable="not_quantifiable"),
    ],
)

# ---------------------------------------------------------------------------
# 模板 4/5：Reflexive Agent（kind=recipe，差异化改造：生成→独立批判节点→修订）
# ---------------------------------------------------------------------------

_REFLEXIVE_DRAFTER_PROMPT: str = (
    "你是初稿 Agent：专注产出高质量初稿，不做自我审查、不反复修改；"
    "初稿产出后即交给下游批判节点审阅。"
)

_REFLEXIVE_CRITIC_SYSTEM_PROMPT: str = (
    "你是批判节点：只批判、不重写。对上游初稿逐项检查正确性、完整性、"
    "遗漏与幻觉风险，批判必须给出具体改进点，不做泛泛评价。"
)

_REFLEXIVE_CRITIC_USER_PROMPT: str = (
    "对上游初稿逐项批判：正确性、完整性、遗漏与幻觉风险；"
    "输出 issues（具体改进点清单）与 verdict（pass/revise）。"
)

_REFLEXIVE_REVISER_PROMPT: str = (
    "你是修订 Agent：按批判节点给出的改进点逐条修订初稿；"
    "批判结论 verdict 为 pass 时保留原稿原样输出。"
    "最终输出必须包含修订后终稿（final）与批判结论（verdict）。"
)

_REFLEXIVE_AGENT = AgentTemplate(
    template_id="reflexive_agent",
    name="Reflexive Agent",
    kind="recipe",
    preset=TemplatePreset(
        system_prompt=_REFLEXIVE_DRAFTER_PROMPT,
        available_tools=[],
        max_tool_rounds=4,
        output_max_retries=3,
        output_schema={"final": "string", "verdict": "string"},
    ),
    system_prompt=_REFLEXIVE_DRAFTER_PROMPT,
    allowed_tools=[],
    max_tool_rounds=None,  # recipe：各节点独立设置轮次（节点间不共享预算）
    output_max_retries=None,
    doc=TemplateDoc(
        core_principle_flow=(
            "生成 → 独立批判 → 修订的流水线式自反思：初稿 Agent 产出初稿后，"
            "由独立的 LLM 批判节点（确定性单轮推理，无工具循环开销）"
            "逐项检查正确性/完整性与幻觉风险并输出改进点清单与结论（pass/revise），"
            "修订 Agent 据改进点修订；verdict 为 pass 时保留原稿。"
            "流程要点：生成初稿 → 独立批判（issues + verdict）→ 修订 → 输出终稿。"
        ),
        application_features=(
            "批判职能由独立节点承担（批判视角与生成视角分离，避免自证偏差）、"
            "批判为确定性单轮推理（无多轮工具开销）、"
            "pass 快速通道（原稿免修订直出）、修订节点独享输出校验重试预算。"
        ),
        applicable_scenarios=(
            "对质量/正确性要求高的生成任务（报告、方案、代码说明）、"
            "易出现遗漏或幻觉的任务。"
        ),
        advantages_limitations=(
            "优势：批判与生成分离带来更高的批判客观性、确定性批判节点成本可控、"
            "pass 快速通道避免过修；"
            "局限：三节点流水线延迟高于单节点、批判质量依赖批判提示词设计、"
            "存在过修风险（需靠 pass 通道与改进点约束兜底）。"
        ),
        comparison=(
            "与基础 ReAct 对比：ReAct 面向「是否需要外部信息」，"
            "Reflexive 面向「输出质量是否达标」。"
            "与单 Agent 提示词自反思对比：单 Agent 批判与生成同视角易自证偏差，"
            "本模板以独立批判节点换取批判客观性与成本可控。"
        ),
    ),
    tool_config=TemplateToolConfig(
        tool_types=[ToolSource.PLATFORM, ToolSource.MCP, ToolSource.LOCAL],
        invocation_mode="recipe_nodes",
        parameter_constraints=(
            "各节点独立依工具 input_schema 约束传入；"
            "调用入参解析失败按 NODE_TOOL_ARGUMENT_MISMATCH 处理（观察回填，不终止节点）"
        ),
        retry_and_exception_policy=(
            "单次工具失败 → 回填观察继续（受节点 max_tool_rounds 约束）；"
            "修订节点输出结构不合规按其节点级 output_max_retries=3 重试，"
            "耗尽 → NODE_AGENT_OUTPUT_INVALID"
        ),
    ),
    nodes=[
        TemplateNodePreset(
            local_ref="entry",
            node_type=NodeType.TRIGGER,
            name="入口",
            configuration={"trigger_type": "manual", "input_template": {}},
        ),
        TemplateNodePreset(
            local_ref="drafter",
            node_type=NodeType.AGENT,
            name="初稿 Agent",
            configuration={
                "model_identifier": "",
                "system_prompt": _REFLEXIVE_DRAFTER_PROMPT,
                "available_tools": [],
                "max_tool_rounds": 4,
                "output_schema": {"draft": "string"},
            },
        ),
        TemplateNodePreset(
            local_ref="critic",
            node_type=NodeType.LLM,
            name="批判节点",
            configuration={
                "model_identifier": "",
                "system_prompt": _REFLEXIVE_CRITIC_SYSTEM_PROMPT,
                "user_prompt": _REFLEXIVE_CRITIC_USER_PROMPT,
                "output_schema": {"issues": "array", "verdict": "string"},
            },
        ),
        TemplateNodePreset(
            local_ref="reviser",
            node_type=NodeType.AGENT,
            name="修订 Agent",
            configuration={
                "model_identifier": "",
                "system_prompt": _REFLEXIVE_REVISER_PROMPT,
                "available_tools": [],
                "max_tool_rounds": 4,
                "output_max_retries": 3,
                "output_schema": {"final": "string", "verdict": "string"},
            },
        ),
        TemplateNodePreset(
            local_ref="exit",
            node_type=NodeType.OUTPUT_DISPLAY,
            name="结果输出",
            configuration={},
        ),
    ],
    edges=[
        TemplateEdgePreset(source_ref="entry", target_ref="drafter"),
        TemplateEdgePreset(source_ref="drafter", target_ref="critic"),
        TemplateEdgePreset(source_ref="critic", target_ref="reviser"),
        TemplateEdgePreset(source_ref="reviser", target_ref="exit"),
    ],
    implementation_key_points=[
        "初稿 Agent 专注产出不自我审查；批判节点只批判不重写",
        "批判节点复用既有 LLM 节点（确定性单轮推理），避免多轮工具开销",
        "修订节点 output_max_retries=3 承载输出校验重试（节点级独立预算）",
    ],
    dependency_conditions=[
        "既有 Agent 节点与 ReAct 闭环",
        "既有 LLM 单轮推理节点（LlmNodeConfiguration）",
        "输出结构校验链路（output_schema + output_max_retries）",
        "ILlmClient（推理下游）",
    ],
    performance_boundaries=TemplatePerformanceBoundaries(
        max_tool_rounds=4, tool_timeout_seconds=60, output_max_retries=3
    ),
    security_boundaries=list(_COMMON_SECURITY_BOUNDARIES),
    evaluation_metrics=[
        TemplateMetric(metric="output_schema_compliance_rate", collectable="runtime"),
        TemplateMetric(metric="tool_rounds_used", collectable="runtime"),
        TemplateMetric(metric="tool_calls_executed", collectable="runtime"),
        TemplateMetric(metric="critic_verdict_distribution", collectable="runtime"),
        TemplateMetric(metric="template_apply_success_rate", collectable="static"),
        TemplateMetric(metric="end_to_end_latency", collectable="not_quantifiable"),
    ],
)

# ---------------------------------------------------------------------------
# 模板 5/5：Autonomous Loop（kind=recipe，设计 §3.2.5 / §3.1.2）
# ---------------------------------------------------------------------------

_AUTONOMOUS_LOOP_SYSTEM_PROMPT: str = (
    "你是自主循环推进型智能体：每一轮先判断当前状态是否已满足任务目标（退出条件），"
    "未满足则继续推进下一轮（可调用工具获取事实或产生副作用），"
    "满足则在输出中标记 done=true 并收敛结果。"
    "每轮输出须包含 progress 字段：摘要本轮进展与剩余事项，作为下一轮的记忆锚点；"
    "上下文过长时系统自动压缩早期观察，progress 不受压缩影响。"
)

_AUTONOMOUS_LOOP = AgentTemplate(
    template_id="autonomous_loop",
    name="Autonomous Loop",
    kind="recipe",
    preset=TemplatePreset(
        system_prompt=_AUTONOMOUS_LOOP_SYSTEM_PROMPT,
        available_tools=["get_workflow", "get_instance", "start_workflow"],
        max_tool_rounds=10,
        context_strategy="summary_compression",
    ),
    system_prompt=_AUTONOMOUS_LOOP_SYSTEM_PROMPT,
    allowed_tools=["get_workflow", "get_instance", "start_workflow"],
    max_tool_rounds=10,
    output_max_retries=None,
    doc=TemplateDoc(
        core_principle_flow=(
            "自主循环推进直至满足退出条件：Agent 在每一轮自主决定「是否达成目标」；"
            "未达成则继续推进（可调用工具获取事实或产生副作用），"
            "达成则由既有控制流条件退出循环并输出结果。"
            "流程要点：判断 → 推进 → 判断 → …… → 退出。"
        ),
        application_features=(
            "自主性高、可处理长程任务、退出条件与循环上限双约束；"
            "跨轮上下文自动压缩（summary_compression），配合 progress 记忆锚点"
            "保证长循环下早期进展不丢失。"
        ),
        applicable_scenarios="长流程自主推进类任务（持续巡检、迭代优化、直到条件满足的任务）。",
        advantages_limitations=(
            "优势：自主性强、人工干预少、适合目标明确但步数不定的任务；"
            "局限：退出条件设计难（设计不当会空转或过早退出）、成本随轮次累积、"
            "存在失控风险（需循环上限兜底）。"
        ),
        comparison=(
            "与固定步数流程对比：固定步数不具自适应能力但成本可预测；"
            "与 Plan-and-Execute 对比：Plan-and-Execute 计划固定、执行确定，"
            "Autonomous Loop 每轮重新判断，适应性强但可控性低。"
        ),
    ),
    tool_config=TemplateToolConfig(
        tool_types=[ToolSource.PLATFORM, ToolSource.MCP, ToolSource.LOCAL],
        invocation_mode="recipe_nodes",
        parameter_constraints=_COMMON_PARAMETER_CONSTRAINTS,
        retry_and_exception_policy=(
            "单次工具失败 → 回填观察继续（受 max_tool_rounds 约束）；"
            "循环退出由既有控制流条件保证（不依赖无限轮次）"
        ),
    ),
    nodes=[
        TemplateNodePreset(
            local_ref="entry",
            node_type=NodeType.TRIGGER,
            name="入口",
            configuration={"trigger_type": "manual", "input_template": {}},
        ),
        TemplateNodePreset(
            local_ref="loop_gate",
            node_type=NodeType.CONTROL_FLOW,
            name="循环门",
            configuration={
                "control_flow_kind": "loop",
                "loop_body_entry": "loop_agent",
                "loop_termination_condition": "output.done == true",
                "max_iterations": 10,
            },
        ),
        TemplateNodePreset(
            local_ref="loop_agent",
            node_type=NodeType.AGENT,
            name="自主 Agent",
            configuration={
                "model_identifier": "",
                "system_prompt": _AUTONOMOUS_LOOP_SYSTEM_PROMPT,
                "available_tools": ["get_instance"],
                "max_tool_rounds": 10,
                "context_strategy": "summary_compression",
            },
        ),
        TemplateNodePreset(
            local_ref="exit",
            node_type=NodeType.OUTPUT_DISPLAY,
            name="结果输出",
            configuration={},
        ),
    ],
    edges=[
        TemplateEdgePreset(source_ref="entry", target_ref="loop_gate"),
        TemplateEdgePreset(source_ref="loop_gate", target_ref="loop_agent"),
        TemplateEdgePreset(
            source_ref="loop_agent", target_ref="loop_gate", condition_label="未满足退出条件"
        ),
        TemplateEdgePreset(
            source_ref="loop_gate", target_ref="exit", condition_label="满足退出条件"
        ),
    ],
    implementation_key_points=[
        "提示词引导「每轮先判断退出条件」并输出 progress 记忆锚点",
        "循环退出由既有控制流条件保证（不依赖无限轮次）",
        "轮次上限与循环最大迭代数双重约束",
        "loop_agent 启用 summary_compression 上下文压缩（长循环记忆能力）",
    ],
    dependency_conditions=[
        "既有 Agent 节点与 ReAct 闭环",
        "既有 control_flow 循环节点（loop_body_entry / loop_termination_condition / max_iterations）",
        "tool_invokers 工具装配",
        "ILlmClient（推理下游）",
    ],
    performance_boundaries=TemplatePerformanceBoundaries(
        max_tool_rounds=10, tool_timeout_seconds=60, output_max_retries=None
    ),
    security_boundaries=list(_COMMON_SECURITY_BOUNDARIES),
    evaluation_metrics=[
        TemplateMetric(metric="template_apply_success_rate", collectable="static"),
        TemplateMetric(metric="tool_rounds_used", collectable="runtime"),
        TemplateMetric(metric="tool_calls_executed", collectable="runtime"),
        TemplateMetric(metric="actual_iteration_count", collectable="runtime"),
        TemplateMetric(metric="context_compression_events", collectable="runtime"),
        TemplateMetric(metric="end_to_end_latency", collectable="not_quantifiable"),
    ],
)

# ---------------------------------------------------------------------------
# 内置模板清单（长度恒 5，顺序固定，AC-12）
# ---------------------------------------------------------------------------

AGENT_TEMPLATES: Tuple[AgentTemplate, ...] = (
    _PLAN_AND_EXECUTE,
    _BASIC_REACT,
    _MULTI_AGENT,  # kind=recipe
    _REFLEXIVE_AGENT,
    _AUTONOMOUS_LOOP,  # kind=recipe
)

# 配方节点配置字段集登记表（导入期校验 configuration 键集合用，设计 §3.1.2 要点 1）
# 差异化扩展：登记 LLM（reflexive_agent 批判节点）与 VECTOR_KNOWLEDGE_BASE（multi_agent 检索分支）
_NODE_CONFIGURATION_FIELD_SETS: Dict[NodeType, FrozenSet[str]] = {
    NodeType.AGENT: frozenset(AgentNodeConfiguration.model_fields),
    NodeType.TRIGGER: frozenset(TriggerNodeConfiguration.model_fields),
    NodeType.CONTROL_FLOW: frozenset(ControlFlowNodeConfiguration.model_fields),
    NodeType.OUTPUT_DISPLAY: frozenset(OutputDisplayNodeConfiguration.model_fields),
    NodeType.LLM: frozenset(LlmNodeConfiguration.model_fields),
    NodeType.VECTOR_KNOWLEDGE_BASE: frozenset(
        VectorKnowledgeBaseNodeConfiguration.model_fields
    ),
}

# TemplateDoc 五维度字段名（校验遍历用）
_DOC_FIELDS: Tuple[str, ...] = (
    "core_principle_flow",
    "application_features",
    "applicable_scenarios",
    "advantages_limitations",
    "comparison",
)


def _validate_templates() -> None:
    """导入期校验（DC-07 / DC-12，必做守护 ⑤）。

    校验项：数量=5、template_id 唯一、逐模板五维度非空、
    预设键 ⊆ TEMPLATE_PRESET_ALLOWED_KEYS、recipe 节点类型 ∈ 既有 NodeType、
    recipe 节点配置键 ⊆ 对应节点配置字段集、配方连线引用存在于节点 local_ref 集合。
    任何违规 → 抛 RuntimeError（fail-fast，进程启动即失败）。
    """
    if len(AGENT_TEMPLATES) != 5:
        raise RuntimeError(f"内置模板数量必须为 5，实际为 {len(AGENT_TEMPLATES)}")

    template_ids = [template.template_id for template in AGENT_TEMPLATES]
    if len(template_ids) != len(set(template_ids)):
        raise RuntimeError(f"内置模板 template_id 必须唯一，实际为 {template_ids}")

    for template in AGENT_TEMPLATES:
        # 五维度文档均非空（AC-16）
        for doc_field in _DOC_FIELDS:
            if not getattr(template.doc, doc_field):
                raise RuntimeError(
                    f"模板 {template.template_id} 五维度文档字段 {doc_field} 不能为空"
                )
        # 预设键 ⊆ 白名单（INV-D10）
        preset_keys = set(template.preset.model_dump().keys())
        illegal_preset_keys = preset_keys - TEMPLATE_PRESET_ALLOWED_KEYS
        if illegal_preset_keys:
            raise RuntimeError(
                f"模板 {template.template_id} 预设键越界：{sorted(illegal_preset_keys)}"
            )
        if template.kind == "recipe":
            # recipe 结构校验（AC-14 / AC-18，INV-D03）
            local_refs = [node.local_ref for node in template.nodes]
            if len(template.nodes) < 2:
                raise RuntimeError(f"模板 {template.template_id} 配方节点数至少为 2")
            if len(local_refs) != len(set(local_refs)):
                raise RuntimeError(
                    f"模板 {template.template_id} 配方节点 local_ref 必须唯一"
                )
            for node in template.nodes:
                if node.node_type not in _NODE_CONFIGURATION_FIELD_SETS:
                    raise RuntimeError(
                        f"模板 {template.template_id} 配方节点 {node.local_ref} "
                        f"使用了未登记配置字段集的节点类型 {node.node_type}"
                    )
                allowed_fields = _NODE_CONFIGURATION_FIELD_SETS[node.node_type]
                illegal_config_keys = set(node.configuration.keys()) - allowed_fields
                if illegal_config_keys:
                    raise RuntimeError(
                        f"模板 {template.template_id} 配方节点 {node.local_ref} "
                        f"配置键越界：{sorted(illegal_config_keys)}"
                    )
            known_refs = set(local_refs)
            for edge in template.edges:
                if edge.source_ref not in known_refs or edge.target_ref not in known_refs:
                    raise RuntimeError(
                        f"模板 {template.template_id} 配方连线 "
                        f"{edge.source_ref}->{edge.target_ref} 引用了不存在的节点 local_ref"
                    )
        else:
            if template.nodes or template.edges:
                raise RuntimeError(
                    f"模板 {template.template_id} kind=single_node 时 nodes/edges 必须为空"
                )


def list_templates() -> List[AgentTemplate]:
    """按预置顺序返回全部内置模板（长度恒 5，AC-12）。"""
    return list(AGENT_TEMPLATES)


def get_template(template_id: str) -> Optional[AgentTemplate]:
    """按 template_id 查找内置模板；不存在返回 None。"""
    for template in AGENT_TEMPLATES:
        if template.template_id == template_id:
            return template
    return None


_validate_templates()  # 模块导入即校验（fail-fast，DC-12）
