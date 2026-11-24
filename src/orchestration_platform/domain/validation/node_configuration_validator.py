# =============================================================================
# 节点配置校验器（domain/validation/node_configuration_validator.py）
#
# 职责：按节点类型校验必备配置项，并拦截语义违规的配置。
# 设计意图（对应需求规格 5.1.1.6、5.3.1.9、5.3.1.14）：
#   1. 必备配置项校验：每类节点声明必备字段，缺失时返回明确错误（5.1.1.6）；
#   2. 数据源写操作拦截：query 不得包含写/删/结构变更语句，在校验阶段即拒绝（5.3.1.9）；
#   3. 控制流业务动作拦截：控制流节点不得携带模型/工具/数据源等业务动作字段（5.3.1.14）；
#   4. 统一输入引用静态校验 pass（unified_node_input T4.1/T4.2，design DC-04/DC-08）：
#      引用提取登记表（矩阵驱动的提取单一声明处）+ 静态 8 规则
#      （语法 / 存在性 / 自引用 / 非上游 / 不可引用类别 / 类别错配 /
#        output_schema 疑似深度错配 / Agent 选择集别名冲突）。
#   5. 数据转换操作链合法性校验（expand_data_transform_ops T7.1/T7.2，FR-04）：
#      步数上限 / 未知操作 / 必填参数缺失 / 取值越界 / 正则模式安全 /
#      JSON Path 语法（纯参数、数据无关子集，镜像运行期判定口径）；
#      保留用户原值不被静默改写，报告含节点与步骤定位；
#      链内 join 步 right_dataset_reference 纳入引用提取（新增提取位置，
#      既有单 operation 分支语义不变，8 规则口径不变）。
#   6. 大模型 / 代码节点加载期校验（llm_and_code_nodes 任务 3，CHG-06）：
#      必备项（模型标识 / 脚本文本）/ 提示词上界 / 采样取值域（llm_sampling_spec
#      单一权威）/ 脚本字节上界与静态合规（复用既有 validate_custom_code 守卫，
#      只调用不修改）/ 依赖与入口格式 / 输出结构类型名值域；语义错误码作消息
#      前缀（LLM_PARAM_INVALID / CODE_SCRIPT_INVALID / CODE_SCRIPT_TOO_LARGE，
#      D-04），issue.code 统一 WF_VALIDATION_FAILED（既有校验契约不变）。
# =============================================================================

import ast
import re
from collections import deque
from typing import Dict, List, Optional, Set, Tuple

from orchestration_platform.domain.errors import (
    CODE_SCRIPT_INVALID,
    CODE_SCRIPT_TOO_LARGE,
    DATA_TRANSFORM_CHAIN_TOO_LONG,
    DATA_TRANSFORM_INVALID_STEP_PARAMS,
    DATA_TRANSFORM_REGEX_UNSAFE_PATTERN,
    DATA_TRANSFORM_UNKNOWN_OPERATION,
    LLM_PARAM_INVALID,
    WF_VALIDATION_FAILED,
)
from orchestration_platform.domain.models.common import ValidationIssue
from orchestration_platform.domain.models.enums import (
    DataTransformOperation,
    NodeType,
    Severity,
)
from orchestration_platform.domain.models.node_configurations import (
    AgentNodeConfiguration,
    CodeNodeConfiguration,
    ControlFlowNodeConfiguration,
    DataTransformNodeConfiguration,
    DataTransformStep,
    DataSourceNodeConfiguration,
    DynamicInjectorNodeConfiguration,
    LlmNodeConfiguration,
    LlmSamplingParameters,
    OutputDisplayNodeConfiguration,
    PluginNodeConfiguration,
    SubWorkflowNodeConfiguration,
    SUPPORTED_SCRIPT_LANGUAGES,
    ToolNodeConfiguration,
    TriggerNodeConfiguration,
    VectorKnowledgeBaseNodeConfiguration,
    normalize_operations,
    normalize_output_references,
)
from orchestration_platform.domain.models.node_definition import NodeDefinition
from orchestration_platform.domain.models.workflow_definition import WorkflowDefinition
from orchestration_platform.domain.values.llm_sampling_spec import validate_sampling
from orchestration_platform.domain.values.node_output_categories import (
    resolve_output_category,
)
from orchestration_platform.domain.values.reference_expression import parse_reference

# 数据源禁止的 SQL 写/删/结构变更关键词。
# 用不区分大小写的正则匹配单词边界，避免误报 SELECT 中的子串（如 "updated_at" 列名）。
_FORBIDDEN_SQL_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|MERGE|GRANT|REVOKE|RENAME)\b",
    re.IGNORECASE,
)

# 表达式内字面量名（求值器 Name 分支特判，scheduler.RestrictedExpressionEvaluator）：
# true/True/false/False/null/None 在运行时绑定布尔/空值而非 node_outputs 顶格键，
# 故不构成输入引用——提取时排除，与求值器运行时绑定语义严格一致（DC-08）。
_EXPRESSION_LITERAL_NAMES = frozenset(
    {"True", "true", "False", "false", "None", "null"}
)

# 大模型节点提示词长度上界（字符数；design §2.3.2(1)：system_prompt /
# user_prompt 均 ≤ 32768 字符，加载期拒绝而非截断）
LLM_PROMPT_MAX_CHARS = 32768

# 输出结构类型名字典：与 agent 执行器 _check_type 既有类型映射同口径
# （design §2.3.2(1)：output_schema 值 ∈ 既有类型映射，大小写不敏感；
# 空对象视为未声明）
_OUTPUT_SCHEMA_TYPE_NAMES = frozenset(
    {
        "string", "str", "integer", "int", "number", "float",
        "boolean", "bool", "object", "dict", "array", "list",
    }
)

# 代码节点入口函数名格式（design §2.3.2(2)：^[A-Za-z_][A-Za-z0-9_]*$ 且 ≤64）
CODE_ENTRY_FUNCTION_MAX_CHARS = 64
_CODE_ENTRY_FUNCTION_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# 静态合规命中项展示上界：前 5 条 + 「等 N 项」（design §2.3.2(2)）
CODE_FINDINGS_DISPLAY_LIMIT = 5


def _extract_expression_names(expression: str) -> List[str]:
    """AST 解析提取表达式内引用名（Name / Attribute 链根名首段，DC-08）。

    与求值器运行时绑定语义严格一致（不重构求值器）：
    - Name → node_outputs 顶格键（引用目标 = 命名输出名，基线形态）；
    - Attribute 链（如 output.field）的根 Name 同样构成顶格键引用；
    - 字面量特判名（true/True/...）不构成引用；
    - 语法非法返回空列表（求值期已有既有报错路径，提取器不重复裁判）。
    同一表达式内的重名去重保序。
    """
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError:
        return []

    names: List[str] = []
    for element in ast.walk(tree):
        if (
            isinstance(element, ast.Name)
            and element.id not in _EXPRESSION_LITERAL_NAMES
            and element.id not in names
        ):
            names.append(element.id)
    return names


def collect_input_references(node: NodeDefinition) -> List[Tuple[str, str]]:
    """引用提取登记表：穷举节点配置内的全部输入引用（unified_node_input T4.1，DC-08）。

    返回 (field_path, reference) 列表——field_path 为配置内定位路径（报告三元组用），
    reference 为引用表达式原文。按 DC-06 矩阵逐类型声明提取位置：
    - trigger：input_template.values()；
    - tool：argument_mapping.values()；
    - subworkflow / plugin：input_mapping.values()；
    - data_transform：input_dataset_reference + join 时 params.right_dataset_reference；
    - output_display：input_reference（联合类型归一化后逐条登记：单元素保持
      既有形态，多元素带索引，optimize_output_display_node DC-11）；
    - agent：inputs[].reference（显式选择集）；
    - llm / code：inputs[].reference（登记形态与 agent 完全一致，
      llm_and_code_nodes 任务 3.3，AC-46/47/48/50）；
    - control_flow / dynamic_injector：表达式字段（conditions[].expression /
      loop_termination_condition / injection_condition，经 AST 提取引用名首段）；
    - vector_knowledge_base：query_reference（检索 reference 模式且非空时）+
      write_reference（写入 reference 模式且非空时；vkb_write_mode）；
    - 豁免类型零提取：data_source（无输入引用，DC-06#4）、supervisor（待定只读，DC-06#11）。
    新节点类型接入 = 此处加提取分支（扩展路径成立，INV-D03 / FR-17 覆盖面）。
    """
    config = node.configuration
    references: List[Tuple[str, str]] = []

    if isinstance(config, TriggerNodeConfiguration):
        for param_name, reference in config.input_template.items():
            references.append((f"input_template.{param_name}", reference))
    elif isinstance(config, ToolNodeConfiguration):
        for argument_name, reference in config.argument_mapping.items():
            references.append((f"argument_mapping.{argument_name}", reference))
    elif isinstance(config, SubWorkflowNodeConfiguration):
        for param_name, reference in config.input_mapping.items():
            references.append((f"input_mapping.{param_name}", reference))
    elif isinstance(config, PluginNodeConfiguration):
        for param_name, reference in config.input_mapping.items():
            references.append((f"input_mapping.{param_name}", reference))
    elif isinstance(config, DataTransformNodeConfiguration):
        references.append(
            ("input_dataset_reference", config.input_dataset_reference)
        )
        if config.operations:
            # 操作链形态（expand_data_transform_ops T7.2 / CHG-B16）：
            # 运行期仅使用链步（normalize_operations 以 operations 为权威），
            # 逐 join 步提取副引用（新增引用提取位置）；顶层 operation/params
            # 不再参与提取（与运行期语义一致，避免登记运行期不使用的引用）。
            for step_index, step in enumerate(config.operations):
                if (
                    step.operation == DataTransformOperation.JOIN
                    and step.params.right_dataset_reference
                ):
                    references.append(
                        (
                            f"operations[{step_index}].params.right_dataset_reference",
                            step.params.right_dataset_reference,
                        )
                    )
        elif (
            config.operation == DataTransformOperation.JOIN
            and config.params.right_dataset_reference
        ):
            references.append(
                (
                    "params.right_dataset_reference",
                    config.params.right_dataset_reference,
                )
            )
    elif isinstance(config, OutputDisplayNodeConfiguration):
        # optimize_output_display_node CHG-05（DC-11）：归一化后逐条登记——
        # 单元素保持既有 ("input_reference", ref) 形态（INV-D14 逐字守护）；
        # 多元素加索引保证 field_path 唯一（报告三元组可定位）。
        refs = normalize_output_references(config.input_reference)
        if len(refs) == 1:
            references.append(("input_reference", refs[0]))
        else:
            for index, reference in enumerate(refs):
                references.append((f"input_reference[{index}]", reference))
    elif isinstance(config, AgentNodeConfiguration):
        if config.inputs:
            for index, entry in enumerate(config.inputs):
                references.append((f"inputs[{index}].reference", entry.reference))
    elif isinstance(config, (LlmNodeConfiguration, CodeNodeConfiguration)):
        # llm_and_code_nodes 任务 3.3：llm/code 提取形态与 agent 完全一致
        # （inputs[i].reference），自动进入既有八规则检查（悬空 / 自引用 /
        # 非上游 / 不可引用 / 别名冲突，AC-46/47/48/50）
        if config.inputs:
            for index, entry in enumerate(config.inputs):
                references.append((f"inputs[{index}].reference", entry.reference))
    elif isinstance(config, ControlFlowNodeConfiguration):
        for index, condition in enumerate(config.conditions):
            for name in _extract_expression_names(condition.expression):
                references.append((f"conditions[{index}].expression", name))
        if config.loop_termination_condition:
            for name in _extract_expression_names(config.loop_termination_condition):
                references.append(("loop_termination_condition", name))
    elif isinstance(config, DynamicInjectorNodeConfiguration):
        for name in _extract_expression_names(config.injection_condition):
            references.append(("injection_condition", name))
    elif isinstance(config, VectorKnowledgeBaseNodeConfiguration):
        if config.query_mode == "reference" and config.query_reference:
            references.append(("query_reference", config.query_reference))
        # vkb_write_mode：仅 write 模式 + write_source_mode=reference + 非空时登记；
        # retrieve 模式或 manual 时零登记（避免非生效模式字段误报，R8）。
        if (
            config.mode == "write"
            and config.write_source_mode == "reference"
            and config.write_reference
        ):
            references.append(("write_reference", config.write_reference))

    return references


def build_output_owner_index(definition: WorkflowDefinition) -> Dict[str, List[str]]:
    """构建命名输出名 → 拥有者节点标识列表索引（effective_output_name）。

    重复命名输出（多节点同名）登记为多拥有者——引用判定按「任一拥有者
    通过即通过」的宽松口径（运行时后写覆盖，静态无法唯一归因）。
    供本校验器 8 规则 pass 与 GraphTopologyValidator 引用边提取复用（T4.3）。
    """
    owners: Dict[str, List[str]] = {}
    for node in definition.nodes:
        owners.setdefault(node.effective_output_name, []).append(node.node_id)
    return owners


def _extract_input_positions(node: NodeDefinition) -> List[Tuple[str, str]]:
    """输入位置登记分支：节点类型 → (positionName, 绑定引用) 列表。

    upstream_input_ref T3.1（design §2.5 候选生成矩阵 / DC-04）：字段集与
    collect_input_references 逐类对齐（同一字段集、两套用途——提取引用建
    依赖边 / 提取可寻址位置建形态 B 寻址索引）；矩阵 ❌ 行类型
    （control_flow / dynamic_injector：表达式内引用无名；data_source：无输入
    引用）不产生输入位置；plugin 为后端专有登记行（矩阵 §2.5）。
    矩阵未含 llm / code / vkb 行（矩阵早于该类节点加入）：按前端 T2.1
    同口径不登记——形态 B 引用其输入位置三端一致拒绝（规则 5′ /
    前端 notExist / 运行期 NODE_INPUT_REFERENCE_MISSING）。
    """
    config = node.configuration
    positions: List[Tuple[str, str]] = []

    if isinstance(config, TriggerNodeConfiguration):
        for param_name, reference in config.input_template.items():
            positions.append((param_name, reference))
    elif isinstance(config, ToolNodeConfiguration):
        # static_arguments 常量项无引用语义，不入（矩阵 §2.5，OP-02②）
        for argument_name, reference in config.argument_mapping.items():
            positions.append((argument_name, reference))
    elif isinstance(config, (SubWorkflowNodeConfiguration, PluginNodeConfiguration)):
        for param_name, reference in config.input_mapping.items():
            positions.append((param_name, reference))
    elif isinstance(config, DataTransformNodeConfiguration):
        positions.append(
            ("input_dataset_reference", config.input_dataset_reference)
        )
        if config.operations:
            for step_index, step in enumerate(config.operations):
                if (
                    step.operation == DataTransformOperation.JOIN
                    and step.params.right_dataset_reference
                ):
                    positions.append(
                        (
                            f"operations[{step_index}].params.right_dataset_reference",
                            step.params.right_dataset_reference,
                        )
                    )
        elif (
            config.operation == DataTransformOperation.JOIN
            and config.params.right_dataset_reference
        ):
            positions.append(
                (
                    "params.right_dataset_reference",
                    config.params.right_dataset_reference,
                )
            )
    elif isinstance(config, OutputDisplayNodeConfiguration):
        # 归一化后逐条登记（与 collect_input_references 同口径）：
        # 单元素位置名 = input_reference；多元素带索引（位置名唯一可寻址）
        refs = normalize_output_references(config.input_reference)
        if len(refs) == 1:
            positions.append(("input_reference", refs[0]))
        else:
            for index, reference in enumerate(refs):
                positions.append((f"input_reference[{index}]", reference))
    elif isinstance(config, AgentNodeConfiguration):
        if config.inputs:
            for entry in config.inputs:
                # 须 alias 非空：缺省名随引用漂移，非稳定寻址键（矩阵 §2.5）
                if entry.alias:
                    positions.append((entry.alias, entry.reference))
    return positions


def build_input_position_index(definition: WorkflowDefinition) -> Dict[str, Dict[str, str]]:
    """构建输入位置索引（node_id → { positionName → 形态 A 绑定引用 }）。

    upstream_input_ref T3.1（design DC-04）：仅登记形态 A 绑定——空引用 /
    语法非法 / 形态 B 绑定均不入（规则 5′ 按「不可一次解析」统一报告，
    不在索引层区分）；build_output_owner_index 零改动（独立索引，不污染
    命名输出索引，守 INV-E03 / FR-07 顶层键不劫持）。
    供 _validate_input_references 规则 5′~7′ 复用；确定性：节点声明顺序 +
    字段声明顺序（重复位置名后者覆盖，与运行期 dict 装配语义一致）。
    """
    index: Dict[str, Dict[str, str]] = {}
    for node in definition.nodes:
        positions: Dict[str, str] = {}
        for position_name, reference in _extract_input_positions(node):
            parsed = parse_reference(reference)
            if parsed is None or parsed.kind != "output":
                continue
            positions[position_name] = reference
        index[node.node_id] = positions
    return index


def extract_input_position_bindings(node: NodeDefinition) -> Dict[str, str]:
    """取单节点输入位置名 → 绑定引用映射（upstream_input_ref T4.2 / CHG-B04 配套）。

    运行期形态 B 装配经本函数复用 _extract_input_positions 同一登记权威
    （登记分支单一声明处，OP-08）；返回**原始绑定**（含形态 B 绑定，不过滤，
    与 build_input_position_index 的「仅形态 A」过滤口径不同）——由装配层
    按 DC-03 状态机自行判定（绑定为形态 B → 「不支持递归穿透」失败）。
    重复位置名后者覆盖（与静态索引及运行期 dict 装配语义一致）。
    """
    bindings: Dict[str, str] = {}
    for position_name, reference in _extract_input_positions(node):
        bindings[position_name] = reference
    return bindings


class NodeConfigurationValidator:
    """节点配置校验器：必备项 + 数据源写操作拦截 + 控制流业务动作拦截。"""

    def __init__(
        self,
        code_node_max_script_bytes: int = 65536,
        code_node_max_dependencies: int = 32,
    ) -> None:
        """代码节点上界从构造参数注入（不在 domain 反向读 settings，任务 3.2）。

        缺省值与 settings 配置项默认一致（65536 字节 / 32 条）；
        生产装配侧负责把 settings 实际值传入（llm_and_code_nodes 任务 6）。
        采样取值域与提示词上界为规格常量（llm_sampling_spec /
        LLM_PROMPT_MAX_CHARS），不随环境配置漂移。
        """
        self._code_node_max_script_bytes = code_node_max_script_bytes
        self._code_node_max_dependencies = code_node_max_dependencies

    def validate(self, definition: WorkflowDefinition) -> List[ValidationIssue]:
        """校验全部节点的配置，返回问题清单（空列表表示通过）。

        顺序：逐节点必备项校验 → 全定义统一输入引用静态校验 pass
        （unified_node_input T4.2，持有全定义视野，签名不变）。
        """
        issues: List[ValidationIssue] = []
        for node in definition.nodes:
            issues.extend(self._validate_node(node))
        issues.extend(self._validate_input_references(definition))
        return issues

    # ------------------------------------------------------------------
    # 单节点校验分发
    # ------------------------------------------------------------------
    def _validate_node(self, node: NodeDefinition) -> List[ValidationIssue]:
        """按节点类型分发到对应的校验方法。"""
        if node.node_type == NodeType.AGENT:
            return self._validate_agent(node)
        if node.node_type == NodeType.TOOL:
            return self._validate_tool(node)
        if node.node_type == NodeType.DATA_SOURCE:
            return self._validate_data_source(node)
        if node.node_type == NodeType.TRIGGER:
            return self._validate_trigger(node)
        if node.node_type == NodeType.CONTROL_FLOW:
            return self._validate_control_flow(node)
        if node.node_type == NodeType.DATA_TRANSFORM:
            return self._validate_data_transform(node)
        if node.node_type == NodeType.VECTOR_KNOWLEDGE_BASE:
            return self._validate_vector_knowledge_base(node)
        if node.node_type == NodeType.LLM:
            return self._validate_llm(node)
        if node.node_type == NodeType.CODE:
            return self._validate_code(node)
        return []

    # ------------------------------------------------------------------
    # Agent 节点：model_identifier 留空 = 跟随全局主模型（WARNING 提示）
    # ------------------------------------------------------------------
    def _validate_agent(self, node: NodeDefinition) -> List[ValidationIssue]:
        config: AgentNodeConfiguration = node.configuration
        if not config.model_identifier or not config.model_identifier.strip():
            # 跟随全局语义（2026-11-20）：留空 = 运行时回退 settings
            # llm_primary_model（HttpLlmClient 对空 request.model_identifier
            # 本就回退构造期模型）；不再判 ERROR，降为 WARNING 可辨识提示，
            # 避免 .env 换模型后已落盘工作流因旧模型名 403。
            return [
                ValidationIssue(
                    code=WF_VALIDATION_FAILED,
                    severity=Severity.WARNING,
                    message=(
                        f"Agent 节点 {node.node_id} model_identifier 为空："
                        f"运行时跟随全局主模型（LLM_PRIMARY_MODEL）；"
                        f"如需显式钉住请在节点配置填写模型名"
                    ),
                    node_id=node.node_id,
                )
            ]
        return []

    # ------------------------------------------------------------------
    # Tool 节点：必备 tool_identifier
    # ------------------------------------------------------------------
    def _validate_tool(self, node: NodeDefinition) -> List[ValidationIssue]:
        config: ToolNodeConfiguration = node.configuration
        if not config.tool_identifier or not config.tool_identifier.strip():
            return [
                ValidationIssue(
                    code=WF_VALIDATION_FAILED,
                    severity=Severity.ERROR,
                    message=f"Tool 节点 {node.node_id} 缺少工具配置：tool_identifier 不能为空",
                    node_id=node.node_id,
                )
            ]
        return []

    # ------------------------------------------------------------------
    # 数据源节点：必备 connection_reference + 写操作拦截
    # ------------------------------------------------------------------
    def _validate_data_source(self, node: NodeDefinition) -> List[ValidationIssue]:
        config: DataSourceNodeConfiguration = node.configuration
        issues: List[ValidationIssue] = []

        if not config.connection_reference or not config.connection_reference.strip():
            issues.append(
                ValidationIssue(
                    code=WF_VALIDATION_FAILED,
                    severity=Severity.ERROR,
                    message=f"数据源节点 {node.node_id} 缺少连接引用：connection_reference 不能为空",
                    node_id=node.node_id,
                )
            )

        # 写操作守卫：query 不得包含写/删/结构变更语句
        if config.query:
            forbidden_match = _FORBIDDEN_SQL_KEYWORDS.search(config.query)
            if forbidden_match:
                issues.append(
                    ValidationIssue(
                        code=WF_VALIDATION_FAILED,
                        severity=Severity.ERROR,
                        message=(
                            f"数据源节点 {node.node_id} 的查询包含禁止的写/结构变更操作："
                            f"检测到关键词 '{forbidden_match.group()}'。"
                            f"数据源节点仅允许只读查询（SELECT）。"
                        ),
                        node_id=node.node_id,
                    )
                )

        return issues

    # ------------------------------------------------------------------
    # 触发器节点：必备 trigger_type + 条件必填项
    # ------------------------------------------------------------------
    def _validate_trigger(self, node: NodeDefinition) -> List[ValidationIssue]:
        config: TriggerNodeConfiguration = node.configuration
        issues: List[ValidationIssue] = []

        # trigger_type 由枚举保证非空，此处校验条件必填项
        from orchestration_platform.domain.models.enums import TriggerType

        if config.trigger_type == TriggerType.SCHEDULED:
            if not config.schedule or not config.schedule.strip():
                issues.append(
                    ValidationIssue(
                        code=WF_VALIDATION_FAILED,
                        severity=Severity.ERROR,
                        message=f"触发器节点 {node.node_id} 类型为 scheduled 但未配置 schedule 表达式",
                        node_id=node.node_id,
                    )
                )
        if config.trigger_type == TriggerType.DATA_EVENT:
            if not config.event_source or not config.event_source.strip():
                issues.append(
                    ValidationIssue(
                        code=WF_VALIDATION_FAILED,
                        severity=Severity.ERROR,
                        message=f"触发器节点 {node.node_id} 类型为 data_event 但未配置 event_source",
                        node_id=node.node_id,
                    )
                )

        # input_template 结构校验：仅「键非空、值非空」两条静态规则，不校验
        # 引用可解析性 / 值格式 / 键唯一性。依据 OQ-1 = ERROR 阻断 + 降级兜底：
        # 存量普查（examples/ 13 处 + tests/ 夹具）未发现空键 / 空值定义，故保持
        # ERROR 阻断级别；若后续普查发现非法存量定义，再降级为 WARNING。
        for param_name, context_field_ref in config.input_template.items():
            if not param_name or not param_name.strip():
                issues.append(
                    ValidationIssue(
                        code=WF_VALIDATION_FAILED,
                        severity=Severity.ERROR,
                        message=f"触发器节点 {node.node_id} 的 input_template 存在空参数名",
                        node_id=node.node_id,
                    )
                )
            if not context_field_ref or not context_field_ref.strip():
                issues.append(
                    ValidationIssue(
                        code=WF_VALIDATION_FAILED,
                        severity=Severity.ERROR,
                        message=f"触发器节点 {node.node_id} 的 input_template 参数 {param_name!r} 的引用字段为空",
                        node_id=node.node_id,
                    )
                )

        # 预设频率配置语义校验（可选，存在时校验参数合法性）
        if config.preset_config is not None:
            pc = config.preset_config
            from orchestration_platform.domain.models.enums import FrequencyPresetEnum
            _preset = pc.preset
            _days_in_month = {1: 31, 2: 29, 3: 31, 4: 30, 5: 31, 6: 30, 7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31}
            _interval_limits = {
                FrequencyPresetEnum.INTERVAL_MINUTES: 59,
                FrequencyPresetEnum.INTERVAL_HOURS: 23,
                FrequencyPresetEnum.INTERVAL_DAYS: 31,
                FrequencyPresetEnum.INTERVAL_WEEKS: 52,
                FrequencyPresetEnum.INTERVAL_MONTHS: 12,
            }

            if _preset == FrequencyPresetEnum.HOURLY:
                if pc.minute is None or not (0 <= pc.minute <= 59):
                    issues.append(ValidationIssue(code=WF_VALIDATION_FAILED, severity=Severity.ERROR, message=f"触发器节点 {node.node_id} 预设 hourly 的 minute 须为 0–59", node_id=node.node_id))
            elif _preset == FrequencyPresetEnum.DAILY:
                if pc.hour is None or not (0 <= pc.hour <= 23):
                    issues.append(ValidationIssue(code=WF_VALIDATION_FAILED, severity=Severity.ERROR, message=f"触发器节点 {node.node_id} 预设 daily 的 hour 须为 0–23", node_id=node.node_id))
                if pc.minute is None or not (0 <= pc.minute <= 59):
                    issues.append(ValidationIssue(code=WF_VALIDATION_FAILED, severity=Severity.ERROR, message=f"触发器节点 {node.node_id} 预设 daily 的 minute 须为 0–59", node_id=node.node_id))
            elif _preset == FrequencyPresetEnum.WEEKLY:
                if pc.day_of_week is None or not (1 <= pc.day_of_week <= 7):
                    issues.append(ValidationIssue(code=WF_VALIDATION_FAILED, severity=Severity.ERROR, message=f"触发器节点 {node.node_id} 预设 weekly 的 day_of_week 须为 1–7", node_id=node.node_id))
                if pc.hour is None or not (0 <= pc.hour <= 23):
                    issues.append(ValidationIssue(code=WF_VALIDATION_FAILED, severity=Severity.ERROR, message=f"触发器节点 {node.node_id} 预设 weekly 的 hour 须为 0–23", node_id=node.node_id))
                if pc.minute is None or not (0 <= pc.minute <= 59):
                    issues.append(ValidationIssue(code=WF_VALIDATION_FAILED, severity=Severity.ERROR, message=f"触发器节点 {node.node_id} 预设 weekly 的 minute 须为 0–59", node_id=node.node_id))
            elif _preset == FrequencyPresetEnum.MONTHLY:
                if pc.day_of_month is None or not (1 <= pc.day_of_month <= 31):
                    issues.append(ValidationIssue(code=WF_VALIDATION_FAILED, severity=Severity.ERROR, message=f"触发器节点 {node.node_id} 预设 monthly 的 day_of_month 须为 1–31", node_id=node.node_id))
                if pc.hour is None or not (0 <= pc.hour <= 23):
                    issues.append(ValidationIssue(code=WF_VALIDATION_FAILED, severity=Severity.ERROR, message=f"触发器节点 {node.node_id} 预设 monthly 的 hour 须为 0–23", node_id=node.node_id))
                if pc.minute is None or not (0 <= pc.minute <= 59):
                    issues.append(ValidationIssue(code=WF_VALIDATION_FAILED, severity=Severity.ERROR, message=f"触发器节点 {node.node_id} 预设 monthly 的 minute 须为 0–59", node_id=node.node_id))
            elif _preset == FrequencyPresetEnum.YEARLY:
                if pc.month is None or not (1 <= pc.month <= 12):
                    issues.append(ValidationIssue(code=WF_VALIDATION_FAILED, severity=Severity.ERROR, message=f"触发器节点 {node.node_id} 预设 yearly 的 month 须为 1–12", node_id=node.node_id))
                elif pc.day_of_month is None or not (1 <= pc.day_of_month <= _days_in_month[pc.month]):
                    issues.append(ValidationIssue(code=WF_VALIDATION_FAILED, severity=Severity.ERROR, message=f"触发器节点 {node.node_id} 预设 yearly 的 day_of_month 超出 {pc.month} 月天数", node_id=node.node_id))
                if pc.hour is None or not (0 <= pc.hour <= 23):
                    issues.append(ValidationIssue(code=WF_VALIDATION_FAILED, severity=Severity.ERROR, message=f"触发器节点 {node.node_id} 预设 yearly 的 hour 须为 0–23", node_id=node.node_id))
                if pc.minute is None or not (0 <= pc.minute <= 59):
                    issues.append(ValidationIssue(code=WF_VALIDATION_FAILED, severity=Severity.ERROR, message=f"触发器节点 {node.node_id} 预设 yearly 的 minute 须为 0–59", node_id=node.node_id))
            elif _preset in _interval_limits:
                if pc.value is None or not isinstance(pc.value, int) or pc.value < 1:
                    issues.append(ValidationIssue(code=WF_VALIDATION_FAILED, severity=Severity.ERROR, message=f"触发器节点 {node.node_id} 预设 {_preset.value} 的 value 须为正整数", node_id=node.node_id))
                elif pc.value > _interval_limits[_preset]:
                    issues.append(ValidationIssue(code=WF_VALIDATION_FAILED, severity=Severity.ERROR, message=f"触发器节点 {node.node_id} 预设 {_preset.value} 的 value 不能超过 {_interval_limits[_preset]}", node_id=node.node_id))

        return issues

    # ------------------------------------------------------------------
    # 控制流节点：禁止业务动作字段
    # ------------------------------------------------------------------
    def _validate_control_flow(self, node: NodeDefinition) -> List[ValidationIssue]:
        """校验控制流节点不含业务动作字段。

        设计意图：ControlFlowNodeConfiguration 结构上已无模型/工具/数据源字段
        （pydantic 解析时自动忽略未声明字段），此处做语义层二次确认，
        确保配置中不存在 model_identifier / tool_identifier / connection_reference 等键。
        """
        config: ControlFlowNodeConfiguration = node.configuration
        issues: List[ValidationIssue] = []

        # 结构上已由 pydantic 保证，此处检查控制流子类型的必备项
        from orchestration_platform.domain.models.enums import ControlFlowKind

        if config.control_flow_kind == ControlFlowKind.CONDITIONAL_BRANCH:
            if not config.conditions:
                issues.append(
                    ValidationIssue(
                        code=WF_VALIDATION_FAILED,
                        severity=Severity.ERROR,
                        message=f"条件分支节点 {node.node_id} 未配置任何分支条件（conditions 为空）",
                        node_id=node.node_id,
                    )
                )
        elif config.control_flow_kind == ControlFlowKind.LOOP:
            if not config.loop_body_entry or not config.loop_body_entry.strip():
                issues.append(
                    ValidationIssue(
                        code=WF_VALIDATION_FAILED,
                        severity=Severity.ERROR,
                        message=f"循环节点 {node.node_id} 未配置循环体入口（loop_body_entry 为空）",
                        node_id=node.node_id,
                    )
                )
            if not config.loop_termination_condition or not config.loop_termination_condition.strip():
                issues.append(
                    ValidationIssue(
                        code=WF_VALIDATION_FAILED,
                        severity=Severity.ERROR,
                        message=f"循环节点 {node.node_id} 未配置循环终止条件（loop_termination_condition 为空）",
                        node_id=node.node_id,
                    )
                )

        return issues

    # ------------------------------------------------------------------
    # 数据转换节点：操作链合法性校验
    # （expand_data_transform_ops T7.1，FR-04 / DC-02 / CHG-B16）
    # ------------------------------------------------------------------
    def _validate_data_transform(self, node: NodeDefinition) -> List[ValidationIssue]:
        """数据转换节点操作链合法性校验（加载/编辑期，FR-04 / AC-03）。

        - 步数超上限 → DATA_TRANSFORM_CHAIN_TOO_LONG；
        - 未知操作（枚举合法但未注册处理器，运行期将 fail-fast）→
          DATA_TRANSFORM_UNKNOWN_OPERATION；
        - 必填参数缺失 / 取值越界（纯参数、数据无关子集，镜像运行期
          判定口径）→ DATA_TRANSFORM_INVALID_STEP_PARAMS；
        - 正则模式安全（长度/编译/复杂度，纯参数）→
          DATA_TRANSFORM_REGEX_UNSAFE_PATTERN；
        - JSON Path 语法（纯参数）→ DATA_TRANSFORM_INVALID_STEP_PARAMS。
        只读校验：保留用户原值不被静默改写；每条问题含节点与步骤定位
        （步骤 #N（操作））；issue.code 统一 WF_VALIDATION_FAILED（对齐
        既有报告机制），语义错误码写入 message。
        """
        # 引擎侧注册表按需惰性导入：domain 校验层不建立对 engine 的模块级
        # 依赖（导入无环：engine 包不回依赖 domain.validation）
        from orchestration_platform.engine.nodes.data_transform_ops import (
            MAX_CHAIN_STEPS,
            get_handler,
        )

        config: DataTransformNodeConfiguration = node.configuration
        steps = normalize_operations(config)
        issues: List[ValidationIssue] = []

        if len(steps) > MAX_CHAIN_STEPS:
            issues.append(
                self._dt_issue(
                    node,
                    None,
                    DATA_TRANSFORM_CHAIN_TOO_LONG,
                    f"操作链步数 {len(steps)} 超上限 {MAX_CHAIN_STEPS}",
                )
            )

        for step_index, step in enumerate(steps):
            operation_label = (
                step.operation.value
                if isinstance(step.operation, DataTransformOperation)
                else str(step.operation)
            )
            if get_handler(step.operation) is None:
                issues.append(
                    self._dt_issue(
                        node,
                        (step_index, operation_label),
                        DATA_TRANSFORM_UNKNOWN_OPERATION,
                        "操作未注册（无对应处理器，运行期将 fail-fast）",
                    )
                )
                continue
            for semantic_code, detail in self._check_step_params(step):
                issues.append(
                    self._dt_issue(
                        node,
                        (step_index, operation_label),
                        semantic_code,
                        detail,
                    )
                )

        return issues

    def _check_step_params(self, step: DataTransformStep) -> List[Tuple[str, str]]:
        """单步参数静态校验：返回 (语义错误码, 说明) 列表。

        仅覆盖纯参数、数据无关的判定子集（必填缺失 / 取值越界 / 语法），
        逐操作镜像运行期 fail-fast 口径（运行期数据依赖判定不在此重复）；
        透传型缺参（缺参 → 原样返回，如 filter 缺条件）不校验——
        静态报错会造成「校验拒绝但运行期合法」的口径冲突。
        """
        # 引擎侧纯函数按需惰性导入（同 _validate_data_transform 口径）
        from orchestration_platform.engine.nodes.data_transform_ops.json_path import (
            JsonPathSyntaxError,
            parse_json_path,
        )
        from orchestration_platform.engine.nodes.data_transform_ops.masking import (
            MaskingRuleSet,
        )
        from orchestration_platform.engine.nodes.data_transform_ops.regex_safety import (
            check_regex_safety,
        )

        operation = step.operation
        params = step.params
        invalid = DATA_TRANSFORM_INVALID_STEP_PARAMS
        problems: List[Tuple[str, str]] = []

        def regex_safety_problem(pattern_text: str, prefix: str = "") -> Optional[Tuple[str, str]]:
            verdict = check_regex_safety(pattern_text)
            if verdict.safe:
                return None
            return (
                DATA_TRANSFORM_REGEX_UNSAFE_PATTERN,
                f"{prefix}正则模式被安全校验拒绝：{verdict.reason}",
            )

        if operation == DataTransformOperation.JOIN:
            if params.join_keys:
                if (
                    params.right_join_keys is not None
                    and len(params.right_join_keys) != len(params.join_keys)
                ):
                    problems.append(
                        (
                            invalid,
                            (
                                f"join_keys（{len(params.join_keys)}）与 "
                                f"right_join_keys（{len(params.right_join_keys)}）长度不符"
                            ),
                        )
                    )
                if not params.right_dataset_reference:
                    problems.append(
                        (invalid, "join 多条件复合键形态缺 right_dataset_reference")
                    )
        elif operation == DataTransformOperation.DISTINCT:
            if params.keep is not None and params.keep != "first":
                problems.append(
                    (invalid, f"distinct keep 取值不支持：{params.keep}（仅支持 first）")
                )
        elif operation == DataTransformOperation.RENAME:
            if params.rename_map:
                targets = list(params.rename_map.values())
                if len(set(targets)) != len(targets):
                    problems.append(
                        (invalid, "rename 目标名重复（多个源映射到同一目标），拒绝静默覆盖")
                    )
        elif operation == DataTransformOperation.SELECT_COLUMNS:
            if not params.select_columns:
                problems.append(
                    (invalid, "select_columns 为空（拒绝输出空列集）")
                )
        elif operation == DataTransformOperation.CAST:
            if not params.cast_field:
                problems.append((invalid, "cast 缺 cast_field"))
            if params.cast_target not in ("number", "date"):
                problems.append(
                    (
                        invalid,
                        f"cast_target 取值不支持：{params.cast_target}（仅支持 number/date）",
                    )
                )
            elif params.cast_target == "date" and not params.date_format:
                problems.append(
                    (invalid, "cast_target=date 缺 date_format（仅支持显式 strptime 格式）")
                )
        elif operation == DataTransformOperation.FLATTEN:
            if params.max_depth is not None and params.max_depth < 1:
                problems.append(
                    (invalid, f"flatten max_depth 须 ≥ 1（当前 {params.max_depth}）")
                )
        elif operation == DataTransformOperation.EXPLODE:
            if not params.explode_field:
                problems.append((invalid, "explode 缺 explode_field"))
            if params.max_rows is not None and params.max_rows < 1:
                problems.append(
                    (invalid, f"explode max_rows 须 ≥ 1（当前 {params.max_rows}）")
                )
        elif operation == DataTransformOperation.HANDLE_NULLS:
            strategy = params.strategy
            if strategy:
                if strategy not in ("fill_constant", "fill_mean", "fill_median", "drop_row"):
                    problems.append(
                        (invalid, f"handle_nulls strategy 取值不支持：{strategy}")
                    )
                else:
                    bad_scopes = [
                        scope
                        for scope in params.null_value_scope
                        if scope not in ("none", "empty", "missing")
                    ]
                    if bad_scopes:
                        problems.append(
                            (
                                invalid,
                                f"handle_nulls null_value_scope 含非法取值：{bad_scopes}",
                            )
                        )
                    if strategy == "fill_constant" and params.fill_value is None:
                        problems.append(
                            (invalid, "handle_nulls strategy=fill_constant 缺 fill_value")
                        )
                    if (
                        strategy == "drop_row"
                        and params.drop_scope is not None
                        and params.drop_scope not in ("field", "row")
                    ):
                        problems.append(
                            (
                                invalid,
                                f"handle_nulls drop_scope 取值不支持：{params.drop_scope}",
                            )
                        )
        elif operation == DataTransformOperation.REGEX_EXTRACT:
            if not params.regex_field or not params.pattern or not params.target_field:
                problems.append(
                    (
                        invalid,
                        "regex_extract 缺必填参数（regex_field/pattern/target_field）",
                    )
                )
            else:
                if params.extract_mode is not None and params.extract_mode not in ("first", "all"):
                    problems.append(
                        (
                            invalid,
                            f"regex_extract extract_mode 取值不支持：{params.extract_mode}",
                        )
                    )
                if params.group is not None and params.group < 0:
                    problems.append(
                        (invalid, f"regex_extract group 须 ≥ 0（当前 {params.group}）")
                    )
                safety = regex_safety_problem(params.pattern)
                if safety is not None:
                    problems.append(safety)
        elif operation == DataTransformOperation.REGEX_REPLACE:
            if not params.regex_field or not params.pattern or params.replacement is None:
                problems.append(
                    (
                        invalid,
                        "regex_replace 缺必填参数（regex_field/pattern/replacement）",
                    )
                )
            else:
                safety = regex_safety_problem(params.pattern)
                if safety is not None:
                    problems.append(safety)
        elif operation == DataTransformOperation.VALIDATE:
            if not params.validate_field or not params.validate_rule:
                problems.append(
                    (invalid, "validate 缺必填参数（validate_field/validate_rule）")
                )
            else:
                if params.validate_rule not in ("email", "json"):
                    problems.append(
                        (
                            invalid,
                            f"validate validate_rule 取值不支持：{params.validate_rule}",
                        )
                    )
                if params.on_invalid is not None and params.on_invalid not in ("drop", "mark"):
                    problems.append(
                        (invalid, f"validate on_invalid 取值不支持：{params.on_invalid}")
                    )
        elif operation == DataTransformOperation.MASKING:
            # 无字段/无规则 → 运行期透传（不校验）；有字段且有规则/模式才校验
            if params.mask_fields and (params.mask_rules or params.custom_patterns):
                rule_set = MaskingRuleSet.builtin()
                for rule_id in params.mask_rules:
                    if not rule_set.has_rule(rule_id):
                        problems.append(
                            (invalid, f"masking 内置规则不存在：{rule_id}")
                        )
                for index, custom in enumerate(params.custom_patterns):
                    safety = regex_safety_problem(
                        custom.pattern, prefix=f"masking 自定义模式 #{index} "
                    )
                    if safety is not None:
                        problems.append(safety)
        elif operation == DataTransformOperation.JSON_PATH_EXTRACT:
            if not params.source_field or not params.path or not params.target_field:
                problems.append(
                    (
                        invalid,
                        "json_path_extract 缺必填参数（source_field/path/target_field）",
                    )
                )
            else:
                try:
                    parse_json_path(params.path)
                except JsonPathSyntaxError as exc:
                    problems.append(
                        (invalid, f"json_path_extract 路径语法非法：{exc}")
                    )
        elif operation == DataTransformOperation.SAMPLING:
            if params.sample_mode is not None and params.sample_mode not in ("head", "random"):
                problems.append(
                    (invalid, f"sampling sample_mode 取值不支持：{params.sample_mode}")
                )
            if params.sample_n is None and params.sample_percent is None:
                problems.append(
                    (invalid, "sampling 缺抽样规模（sample_n/sample_percent 二选一）")
                )
            if params.sample_n is not None and params.sample_n < 0:
                problems.append(
                    (invalid, f"sampling sample_n 须为非负整数：{params.sample_n}")
                )
            if params.sample_percent is not None and not 0 < params.sample_percent <= 100:
                problems.append(
                    (
                        invalid,
                        f"sampling sample_percent 须满足 0<比例≤100：{params.sample_percent}",
                    )
                )
        elif operation == DataTransformOperation.CUSTOM_CODE:
            # 执行期无条件 fail-fast（不校验参数，DC-08）；静态仅校验
            # 语言支持域（录入期守卫同口径：本期仅 python）
            if params.language is not None and params.language != "python":
                problems.append(
                    (
                        invalid,
                        f"custom_code language 取值不支持：{params.language}（本期仅 python）",
                    )
                )

        return problems

    def _dt_issue(
        self,
        node: NodeDefinition,
        step_position: Optional[Tuple[int, str]],
        semantic_code: str,
        detail: str,
    ) -> ValidationIssue:
        """构造数据转换链校验问题（语义错误码写入 message，含节点与步骤定位）。"""
        if step_position is None:
            message = f"数据转换节点 {node.node_id} {detail}（{semantic_code}）"
        else:
            step_index, operation_label = step_position
            message = (
                f"数据转换节点 {node.node_id} 步骤 #{step_index}（{operation_label}）"
                f"{detail}（{semantic_code}）"
            )
        return ValidationIssue(
            code=WF_VALIDATION_FAILED,
            severity=Severity.ERROR,
            message=message,
            node_id=node.node_id,
        )

    # ------------------------------------------------------------------
    # 统一输入引用静态校验 pass（unified_node_input T4.1/T4.2，DC-04 八规则）
    # ------------------------------------------------------------------
    def _collect_input_references(self, node: NodeDefinition) -> List[Tuple[str, str]]:
        """引用提取登记表的实例侧入口（委托模块级 collect_input_references）。"""
        return collect_input_references(node)

    def _validate_input_references(self, definition: WorkflowDefinition) -> List[ValidationIssue]:
        """统一输入引用静态校验 pass：DC-04 八规则（T4.2）。

        规则与级别（OP-04 裁决细化）：
          1. 引用语法非法（parse_reference 返回 None）→ ERROR；
          2. 引用目标不存在（output_name 不在全定义登记表——命名输出 ∪ 定义级
             输入参数声明）→ ERROR（消息含「节点 + 字段 + 引用目标」三元组，AC-10）；
          3. 自引用 → ERROR（trigger 豁免，DC-06#1）；
          4. 非上游引用 → ERROR（trigger 豁免；存量普查零误伤保持 ERROR，INV-D11）；
          5. 不可引用类别输出被引用（referable=False，如 control_flow 控制信号）
             → ERROR（AC-07 场景 1）；
          6. 类别级错配（data_transform 主/副输入须为 dataset 类）→ ERROR
             （有确定性契约背书的判定）；
          7. Agent output_schema 可判定疑似路径深度错配 → WARNING
             （动态结构输出不可静态断言，OP-04）；
          8. Agent 选择集别名冲突（节点内条目名集合判重，含 alias 与另一条目
             缺省名冲突）→ ERROR（AC-01 场景 3）。

        形态 B 分支（upstream_input_ref T3.2，design DC-04 规则表）：
        kind == "input_position" 的引用走规则 2′~7′——来源节点存在 / 自引用 /
        非上游（含合法回边豁免）/ 位置绑定可一次解析（无该位置名、绑定为空、
        绑定为形态 B 均不可一次解析）/ 绑定 owner 可引用与类别 / 疑似路径
        深度；trigger 对 2′/3′/4′ 沿用既有豁免口径；错误码零新增（INV-13）；
        形态 A 分支逐行不变（INV-E02）。

        trigger input_template 特殊口径（普查后定级，INV-D11，见 §12 普查记录）：
        仅保留规则 1（语法）——存在性/上游/自引用不可静态判定（input_template
        读触发上下文即运行期启动入参，其键集静态不可枚举，且定义级 inputs 声明
        为可选元数据；存量 T-06 恒等映射与 13 处 examples 均依赖运行期注入）。
        上游可达集 = 逆邻接 BFS（与前端 collectUpstreamNodeIds 同算法口径，
        禁用 WorkflowDefinition.predecessors——仅直接前驱，口径不足）。
        """
        issues: List[ValidationIssue] = []
        node_index = definition.node_index()
        owner_index = build_output_owner_index(definition)
        input_position_index = build_input_position_index(definition)
        input_param_names = {parameter.name for parameter in definition.inputs}
        reverse_adjacency = self._build_reverse_adjacency(definition)
        forward_adjacency = self._build_forward_adjacency(definition)
        upstream_cache: Dict[str, Set[str]] = {}
        downstream_cache: Dict[str, Set[str]] = {}

        for node in definition.nodes:
            is_trigger = node.node_type == NodeType.TRIGGER
            for field_path, reference in self._collect_input_references(node):
                parsed = parse_reference(reference)
                if parsed is None:
                    # 规则 1：引用语法非法
                    issues.append(
                        ValidationIssue(
                            code=WF_VALIDATION_FAILED,
                            severity=Severity.ERROR,
                            message=(
                                f"节点 {node.node_id} 的 {field_path} 引用语法非法："
                                f"{reference!r}（须为「命名输出名[.字段路径]」形态）"
                            ),
                            node_id=node.node_id,
                        )
                    )
                    continue

                if parsed.kind == "input_position":
                    # 形态 B 分支（upstream_input_ref T3.2 规则 2′~7′，
                    # design DC-04 规则表）；形态 A 分支（下方）逐行不变（INV-E02）
                    scope_node_id = parsed.output_name
                    position_name = parsed.input_position or ""
                    scope_node = node_index.get(scope_node_id)

                    # 规则 2′：来源节点不存在（trigger 豁免，与规则 2 同口径）
                    if not is_trigger and scope_node is None:
                        issues.append(
                            ValidationIssue(
                                code=WF_VALIDATION_FAILED,
                                severity=Severity.ERROR,
                                message=(
                                    f"节点 {node.node_id} 的 {field_path} 引用目标不存在："
                                    f"{reference}（来源节点 {scope_node_id} 不在本定义的节点登记内）"
                                ),
                                node_id=node.node_id,
                            )
                        )
                        continue

                    # 规则 3′：自引用（trigger 豁免，与规则 3 同口径）
                    if not is_trigger and scope_node_id == node.node_id:
                        issues.append(
                            ValidationIssue(
                                code=WF_VALIDATION_FAILED,
                                severity=Severity.ERROR,
                                message=(
                                    f"节点 {node.node_id} 的 {field_path} 存在自引用："
                                    f"{reference}（引用了节点自身的输入位置）"
                                ),
                                node_id=node.node_id,
                            )
                        )
                        continue

                    # 规则 4′：来源节点不在上游可达集（trigger 豁免；
                    # 合法回边结构豁免，与规则 4 同口径）
                    if not is_trigger:
                        upstream = self._upstream_reachable(
                            node.node_id, reverse_adjacency, upstream_cache
                        )
                        if scope_node_id not in upstream:
                            if not self._within_back_edge_structure(
                                node.node_id,
                                [scope_node_id],
                                forward_adjacency,
                                downstream_cache,
                            ):
                                issues.append(
                                    ValidationIssue(
                                        code=WF_VALIDATION_FAILED,
                                        severity=Severity.ERROR,
                                        message=(
                                            f"节点 {node.node_id} 的 {field_path} 引用了"
                                            f"非上游输入位置：{reference}"
                                            f"（来源节点不在上游可达集内）"
                                        ),
                                        node_id=node.node_id,
                                    )
                                )
                                continue

                    if scope_node is None:
                        # trigger 豁免路径：来源节点不可静态判定，安全跳过
                        # （与规则 2 定义级入参跳过同构）
                        continue

                    # 规则 5′：来源节点无该位置名 / 绑定为空 / 绑定为形态 B
                    # → 不可一次解析（OP-08；索引只登记形态 A 绑定，
                    # 复用「目标不存在」语义，不新增错误码，INV-13）
                    binding_reference = input_position_index.get(
                        scope_node_id, {}
                    ).get(position_name)
                    if binding_reference is None:
                        issues.append(
                            ValidationIssue(
                                code=WF_VALIDATION_FAILED,
                                severity=Severity.ERROR,
                                message=(
                                    f"节点 {node.node_id} 的 {field_path} 引用目标不存在："
                                    f"{reference}（来源节点 {scope_node_id} 无输入位置 "
                                    f"{position_name!r} 的形态 A 绑定——无该位置名 / "
                                    f"绑定为空 / 绑定为形态 B，不可一次解析）"
                                ),
                                node_id=node.node_id,
                            )
                        )
                        continue

                    # 规则 6′/7′：绑定为形态 A（索引登记保证）→ 等价「引用该
                    # owner 的输出」，referable / 类别 / 路径深度按既有口径复用
                    binding_parsed = parse_reference(binding_reference)
                    if binding_parsed is None or binding_parsed.kind != "output":
                        continue  # 防御：索引只登记形态 A，正常不可到达
                    binding_owner_ids = owner_index.get(
                        binding_parsed.output_name, []
                    )
                    binding_owner_nodes = [
                        node_index[owner_id]
                        for owner_id in binding_owner_ids
                        if owner_id in node_index
                    ]

                    # 规则 6′（其一）：不可引用类别的绑定（referable=False）
                    if binding_owner_nodes and not any(
                        self._owner_referable(owner)
                        for owner in binding_owner_nodes
                    ):
                        non_referable_kinds = sorted(
                            {
                                kind
                                for kind in (
                                    self._owner_value_kind(owner)
                                    for owner in binding_owner_nodes
                                )
                                if kind is not None
                            }
                        )
                        kind_text = (
                            "/".join(non_referable_kinds)
                            if non_referable_kinds
                            else "未登记"
                        )
                        issues.append(
                            ValidationIssue(
                                code=WF_VALIDATION_FAILED,
                                severity=Severity.ERROR,
                                message=(
                                    f"节点 {node.node_id} 的 {field_path} 引用了"
                                    f"不可引用类别的输入位置：{reference}"
                                    f"（其绑定来源节点类型产出 {kind_text}，"
                                    f"referable=false）"
                                ),
                                node_id=node.node_id,
                            )
                        )
                        continue

                    # 规则 6′（其二）：类别级错配（data_transform 主/副输入
                    # 须为 dataset 类，绑定来源须为数据集）
                    if (
                        self._is_dataset_contract_reference(field_path)
                        and binding_owner_nodes
                        and not any(
                            self._owner_is_dataset(owner)
                            for owner in binding_owner_nodes
                        )
                    ):
                        actual_kinds = sorted(
                            {
                                kind
                                for kind in (
                                    self._owner_value_kind(owner)
                                    for owner in binding_owner_nodes
                                )
                                if kind is not None
                            }
                        )
                        issues.append(
                            ValidationIssue(
                                code=WF_VALIDATION_FAILED,
                                severity=Severity.ERROR,
                                message=(
                                    f"节点 {node.node_id} 的 {field_path} 引用了"
                                    f"非数据集类的输入位置：{reference}"
                                    f"（data_transform 主/副输入要求数据集，"
                                    f"其绑定实际为 {'/'.join(actual_kinds)} 类）"
                                ),
                                node_id=node.node_id,
                            )
                        )
                        continue

                    # 规则 7′：绑定值内疑似路径深度错配 → WARNING
                    # （绑定字段路径 + 引用字段路径合并对照 output_schema，既有口径）
                    if parsed.path_segments and binding_owner_nodes:
                        combined_segments = (
                            binding_parsed.path_segments + parsed.path_segments
                        )
                        if any(
                            self._schema_path_suspect(owner, combined_segments)
                            for owner in binding_owner_nodes
                        ):
                            issues.append(
                                ValidationIssue(
                                    code=WF_VALIDATION_FAILED,
                                    severity=Severity.WARNING,
                                    message=(
                                        f"节点 {node.node_id} 的 {field_path} 疑似"
                                        f"路径深度错配：{reference}（字段路径深入"
                                        f"其绑定 output_schema 声明的标量字段）"
                                    ),
                                    node_id=node.node_id,
                                )
                            )
                    continue

                target = parsed.output_name
                owner_ids = owner_index.get(target, [])

                # 规则 2：引用目标不存在（trigger 豁免，普查后定级见 docstring）
                if not is_trigger and not owner_ids and target not in input_param_names:
                    issues.append(
                        ValidationIssue(
                            code=WF_VALIDATION_FAILED,
                            severity=Severity.ERROR,
                            message=(
                                f"节点 {node.node_id} 的 {field_path} 引用目标不存在："
                                f"{target!r}（不在本定义的命名输出与输入参数登记内）"
                            ),
                            node_id=node.node_id,
                        )
                    )
                    continue

                if not owner_ids:
                    # 引用目标为定义级输入参数（启动入参）：无拥有者节点，
                    # 节点级规则（自引用/上游/类别/深度）不适用，安全跳过
                    continue

                # 规则 3：自引用（trigger 豁免，DC-06#1）
                if not is_trigger and node.node_id in owner_ids:
                    issues.append(
                        ValidationIssue(
                            code=WF_VALIDATION_FAILED,
                            severity=Severity.ERROR,
                            message=(
                                f"节点 {node.node_id} 的 {field_path} 存在自引用："
                                f"{reference}（引用了节点自身的输出）"
                            ),
                            node_id=node.node_id,
                        )
                    )
                    continue

                # 规则 4：非上游引用（trigger 豁免；任一拥有者上游可达即通过）
                if not is_trigger:
                    upstream = self._upstream_reachable(
                        node.node_id, reverse_adjacency, upstream_cache
                    )
                    if not any(owner_id in upstream for owner_id in owner_ids):
                        # 合法回边内豁免（FR-16）：owner 与本节点经连线互相可达
                        # （同一回边结构内）时，引用属循环体内可见——上一轮已写入，
                        # 上游规则不误报；该回边结构的合法性（环上是否含循环节点）
                        # 由 GraphTopologyValidator._check_acyclic 统一裁决（T4.3）
                        if not self._within_back_edge_structure(
                            node.node_id, owner_ids, forward_adjacency, downstream_cache
                        ):
                            issues.append(
                                ValidationIssue(
                                    code=WF_VALIDATION_FAILED,
                                    severity=Severity.ERROR,
                                    message=(
                                        f"节点 {node.node_id} 的 {field_path} 引用了"
                                        f"非上游输出：{reference}（目标节点不在上游可达集内）"
                                    ),
                                    node_id=node.node_id,
                                )
                            )
                            continue

                owner_nodes = [
                    node_index[owner_id]
                    for owner_id in owner_ids
                    if owner_id in node_index
                ]

                # 规则 5：不可引用类别输出（referable=False，如 control_flow
                # 信号 / code 节点输出）；拒绝原因按 resolve_output_category
                # 的 value_kind 动态生成（llm_and_code_nodes 任务 3.4 / DC-14：
                # 硬编码「产出控制信号」改为按登记类别动态化，控制流既有
                # 拒绝语义不变，仅文案更准确）
                if owner_nodes and not any(
                    self._owner_referable(owner) for owner in owner_nodes
                ):
                    non_referable_kinds = sorted(
                        {
                            kind
                            for kind in (
                                self._owner_value_kind(owner)
                                for owner in owner_nodes
                            )
                            if kind is not None
                        }
                    )
                    kind_text = "/".join(non_referable_kinds) if non_referable_kinds else "未登记"
                    issues.append(
                        ValidationIssue(
                            code=WF_VALIDATION_FAILED,
                            severity=Severity.ERROR,
                            message=(
                                f"节点 {node.node_id} 的 {field_path} 引用了"
                                f"不可引用类别的输出：{reference}（来源节点类型"
                                f"产出 {kind_text}，referable=false）"
                            ),
                            node_id=node.node_id,
                        )
                    )
                    continue

                # 规则 6：类别级错配（data_transform 主/副输入须为 dataset 类，
                # 有确定性契约背书：运行级 expected_kind="dataset" 必然失败）；
                # 适用域含链内 join 步副引用（T7.2 新增提取位置同规则适用，
                # 8 规则口径不变）
                if (
                    self._is_dataset_contract_reference(field_path)
                    and owner_nodes
                    and not any(
                        self._owner_is_dataset(owner) for owner in owner_nodes
                    )
                ):
                    actual_kinds = sorted(
                        {
                            kind
                            for kind in (
                                self._owner_value_kind(owner)
                                for owner in owner_nodes
                            )
                            if kind is not None
                        }
                    )
                    issues.append(
                        ValidationIssue(
                            code=WF_VALIDATION_FAILED,
                            severity=Severity.ERROR,
                            message=(
                                f"节点 {node.node_id} 的 {field_path} 引用了"
                                f"非数据集类输出：{reference}"
                                f"（data_transform 主/副输入要求数据集，实际为 "
                                f"{'/'.join(actual_kinds)} 类）"
                            ),
                            node_id=node.node_id,
                        )
                    )
                    continue

                # 规则 7：Agent output_schema 可判定疑似路径深度错配 → WARNING
                if parsed.path_segments:
                    suspect = any(
                        self._schema_path_suspect(owner, parsed.path_segments)
                        for owner in owner_nodes
                    )
                    if suspect:
                        issues.append(
                            ValidationIssue(
                                code=WF_VALIDATION_FAILED,
                                severity=Severity.WARNING,
                                message=(
                                    f"节点 {node.node_id} 的 {field_path} 疑似"
                                    f"路径深度错配：{reference}（字段路径深入"
                                    " output_schema 声明的标量字段）"
                                ),
                                node_id=node.node_id,
                            )
                        )

            # 规则 8：选择集别名冲突（Agent/llm/code，节点内条目名集合判重）
            issues.extend(self._validate_agent_input_aliases(node))

        return issues

    def _validate_agent_input_aliases(self, node: NodeDefinition) -> List[ValidationIssue]:
        """选择集条目名判重：alias 或缺省名在节点内唯一（DC-03，AC-01 场景 3）。

        条目名 = alias ?? 引用的命名输出名；冲突域为「本节点上下文条目名集合」。
        语法非法条目跳过（规则 1 已报，此处不可得缺省名）。

        llm_and_code_nodes 任务 3.3：适用域扩展至 llm/code（inputs 与 agent
        同构，从而兑现「别名冲突」检查，AC-46/50）；判重逻辑与 agent 完全
        一致，既有 agent 行为与消息零变更。
        """
        config = node.configuration
        if node.node_type not in (NodeType.AGENT, NodeType.LLM, NodeType.CODE):
            return []
        if not isinstance(
            config,
            (AgentNodeConfiguration, LlmNodeConfiguration, CodeNodeConfiguration),
        ):
            return []
        if not config.inputs:
            return []

        node_label = {
            NodeType.AGENT: "Agent 节点",
            NodeType.LLM: "大模型节点",
            NodeType.CODE: "代码节点",
        }[node.node_type]

        first_seen: Dict[str, str] = {}
        conflicted: List[str] = []
        for entry in config.inputs:
            parsed = parse_reference(entry.reference)
            if parsed is None:
                continue
            entry_name = entry.alias if entry.alias else parsed.output_name
            if entry_name in first_seen and entry_name not in conflicted:
                conflicted.append(entry_name)
            first_seen.setdefault(entry_name, entry.reference)

        return [
            ValidationIssue(
                code=WF_VALIDATION_FAILED,
                severity=Severity.ERROR,
                message=(
                    f"{node_label} {node.node_id} 的输入选择集存在重名条目："
                    f"{name!r}（别名与缺省名的冲突域为本节点条目名集合）"
                ),
                node_id=node.node_id,
            )
            for name in conflicted
        ]

    @staticmethod
    def _is_dataset_contract_reference(field_path: str) -> bool:
        """规则 6 适用域：data_transform 主/副数据集引用定位判定。

        既有两处定位保持不变；expand_data_transform_ops T7.2 新增链内
        join 步副引用定位（operations[i].params.right_dataset_reference）
        与运行期 expected_kind="dataset" 装配同一契约，纳入同一类别校验。
        """
        return field_path in (
            "input_dataset_reference",
            "params.right_dataset_reference",
        ) or (
            field_path.startswith("operations[")
            and field_path.endswith(".params.right_dataset_reference")
        )

    @staticmethod
    def _owner_referable(owner: NodeDefinition) -> bool:
        """拥有者输出是否可引用：未登记类型（plugin/supervisor/output_display）
        返回 None 时安全跳过类别规则（视为可引用，不因未知类型误报，OP-07）。"""
        category = resolve_output_category(owner.node_type)
        return category is None or category.referable

    @staticmethod
    def _owner_is_dataset(owner: NodeDefinition) -> bool:
        """拥有者输出是否为数据集类（data_transform 主/副输入的类别契约）。"""
        category = resolve_output_category(owner.node_type)
        return category is None or category.value_kind == "dataset"

    @staticmethod
    def _owner_value_kind(owner: NodeDefinition) -> Optional[str]:
        """拥有者输出的登记类别；未登记返回 None（报告消息中过滤）。"""
        category = resolve_output_category(owner.node_type)
        return category.value_kind if category is not None else None

    @staticmethod
    def _schema_path_suspect(owner: NodeDefinition, path_segments: Tuple[str, ...]) -> bool:
        """判定引用路径是否深入 Agent output_schema 声明的标量字段（疑似错配）。

        逐段对照 schema：字段未声明 → 不可静态断言（返回 False，OP-04 保守）；
        路径深入 str 标量声明（声明为叶子却继续下钻）→ True（WARNING 判定）；
        拥有者非 Agent 或未声明 output_schema → False。
        """
        if owner.node_type != NodeType.AGENT or not isinstance(
            owner.configuration, AgentNodeConfiguration
        ):
            return False
        schema = owner.configuration.output_schema
        if not isinstance(schema, dict):
            return False

        current: object = schema
        for segment in path_segments:
            if isinstance(current, dict):
                if segment not in current:
                    return False  # 字段未声明——动态结构，不可静态断言
                current = current[segment]
                continue
            # current 为叶子声明但路径仍继续下钻：仅 str 标量声明可判疑似
            return isinstance(current, str)
        return False

    @staticmethod
    def _build_reverse_adjacency(definition: WorkflowDefinition) -> Dict[str, List[str]]:
        """构建连线逆邻接表（target → [source]），供上游可达集 BFS 复用。

        防御性过滤：引用不存在节点的连线不纳入（该类问题由拓扑校验器报告）。
        """
        node_ids = {node.node_id for node in definition.nodes}
        reverse_adjacency: Dict[str, List[str]] = {node_id: [] for node_id in node_ids}
        for edge in definition.edges:
            if (
                edge.target_node_id in reverse_adjacency
                and edge.source_node_id in node_ids
            ):
                reverse_adjacency[edge.target_node_id].append(edge.source_node_id)
        return reverse_adjacency

    @staticmethod
    def _build_forward_adjacency(definition: WorkflowDefinition) -> Dict[str, List[str]]:
        """构建连线正向邻接表（source → [target]），供下游可达集 BFS 复用。

        防御性过滤：引用不存在节点的连线不纳入（该类问题由拓扑校验器报告）。
        """
        node_ids = {node.node_id for node in definition.nodes}
        forward_adjacency: Dict[str, List[str]] = {node_id: [] for node_id in node_ids}
        for edge in definition.edges:
            if (
                edge.source_node_id in forward_adjacency
                and edge.target_node_id in node_ids
            ):
                forward_adjacency[edge.source_node_id].append(edge.target_node_id)
        return forward_adjacency

    def _within_back_edge_structure(
        self,
        node_id: str,
        owner_ids: List[str],
        forward_adjacency: Dict[str, List[str]],
        downstream_cache: Dict[str, Set[str]],
    ) -> bool:
        """判定引用是否处于合法回边结构内（FR-16：循环体内可见，不误报）。

        口径：owner 与引用节点经连线互相可达（同一回边结构 / 强连通区域）——
        运行期上一轮已写入（循环每轮装配到上一轮的值）；
        该结构的合法性（环上是否含循环节点）由环检测统一裁决（T4.3），
        此处不做重复判定（非法环结构由 WF_CYCLIC_GRAPH 报告，不叠加非上游误述）。
        任一 owner 满足互相可达即视为回边内。
        """
        node_downstream = self._downstream_reachable(
            node_id, forward_adjacency, downstream_cache
        )
        for owner_id in owner_ids:
            if owner_id in node_downstream and node_id in self._downstream_reachable(
                owner_id, forward_adjacency, downstream_cache
            ):
                return True
        return False

    @staticmethod
    def _downstream_reachable(
        node_id: str,
        forward_adjacency: Dict[str, List[str]],
        cache: Dict[str, Set[str]],
    ) -> Set[str]:
        """正向邻接 BFS 计算节点的下游可达集（结果按 node_id 缓存）。"""
        cached = cache.get(node_id)
        if cached is not None:
            return cached

        reachable: Set[str] = set()
        queue = deque(forward_adjacency.get(node_id, []))
        reachable.update(queue)
        while queue:
            current = queue.popleft()
            for successor in forward_adjacency.get(current, []):
                if successor not in reachable:
                    reachable.add(successor)
                    queue.append(successor)

        cache[node_id] = reachable
        return reachable

    @staticmethod
    def _upstream_reachable(
        node_id: str,
        reverse_adjacency: Dict[str, List[str]],
        cache: Dict[str, Set[str]],
    ) -> Set[str]:
        """逆邻接 BFS 计算节点的上游可达集（与前端 collectUpstreamNodeIds 同算法口径）。

        结果按 node_id 缓存（同节点多引用共享一次 BFS）。
        """
        cached = cache.get(node_id)
        if cached is not None:
            return cached

        reachable: Set[str] = set()
        queue = deque(reverse_adjacency.get(node_id, []))
        reachable.update(queue)
        while queue:
            current = queue.popleft()
            for predecessor in reverse_adjacency.get(current, []):
                if predecessor not in reachable:
                    reachable.add(predecessor)
                    queue.append(predecessor)

        cache[node_id] = reachable
        return reachable

    # ------------------------------------------------------------------
    # 向量知识库节点：必备 knowledge_base_id + query 模式生效字段
    # （vector_knowledge_base_node T3.2 / CHG-07-b，design §2.2.5）
    # ------------------------------------------------------------------
    def _validate_vector_knowledge_base(self, node: NodeDefinition) -> List[ValidationIssue]:
        """向量知识库节点加载期校验（FR-03 / FR-12；vkb_write_mode 双模式）。

        - knowledge_base_id 必填非空（空白拦截，两模式共用，约束不变）；
        - mode=retrieve（缺省/显式）：query_mode 互斥语义的生效字段非空——
          manual 需 query_value、reference 需 query_reference（另一侧字段允许
          共存声明，不校验）；**既有校验逐字不变**；
        - mode=write：write_source_mode 生效字段非空——manual 需 write_text
          （去空白后非空）、reference 需 write_reference；**不要求** query
          （AC-05）；write_chunk_strategy 枚举合法性由 pydantic Literal 解析期
          拒绝；分片参数取值域（max_chunk_length∈[50,2000]、overlap≥0、
          similarity_threshold∈[0,1]）由 pydantic 约束保证，跨字段约束
          （overlap < max_chunk_length）由既有 ``validate_chunk_params`` 在写入
          分片前置统一拦截（INV-17 单点复用，校验器不重复实现）；
        - top_k 取值域（1..100）由 pydantic 解析期约束保证（判别联合解析
          失败先于本校验到达），不在此重复；
        - embedding_model 可选兼容占位（CHG-14）：生效模型由知识库绑定
          提供（绑定优先，AC-51）；本函数不校验 embedding 必填（缺失不
          报错，AC-50），rerank_model 可选，未配置不报错（AC-28）。
        """
        config: VectorKnowledgeBaseNodeConfiguration = node.configuration
        issues: List[ValidationIssue] = []

        if not config.knowledge_base_id or not config.knowledge_base_id.strip():
            issues.append(
                ValidationIssue(
                    code=WF_VALIDATION_FAILED,
                    severity=Severity.ERROR,
                    message=f"向量知识库节点 {node.node_id} 缺少知识库配置：knowledge_base_id 不能为空",
                    node_id=node.node_id,
                )
            )

        if config.mode == "write":
            # 写入模式：校验 write_source_mode 生效字段非空；不要求 query（AC-05）。
            if config.write_source_mode == "manual":
                if not config.write_text or not config.write_text.strip():
                    issues.append(
                        ValidationIssue(
                            code=WF_VALIDATION_FAILED,
                            severity=Severity.ERROR,
                            message=f"向量知识库节点 {node.node_id} 缺少待写入文本：write manual 模式必须配置 write_text",
                            node_id=node.node_id,
                        )
                    )
            else:
                if not config.write_reference or not config.write_reference.strip():
                    issues.append(
                        ValidationIssue(
                            code=WF_VALIDATION_FAILED,
                            severity=Severity.ERROR,
                            message=f"向量知识库节点 {node.node_id} 缺少写入引用：write reference 模式必须配置 write_reference",
                            node_id=node.node_id,
                        )
                    )
            # 标注字段（annotation_origin / annotation_published_at / annotation_custom）
            # 的语义校验（名称白名单/保留键/JSON 兼容/上界）由装配单点
            # integrations.vector_kb_management.annotations 在写入/受理期执行
            # （vkb_doc_annotation / CHG-10 / INV-19 单点复用）；
            # domain 层不重复实现（分层约束：domain 零依赖 integrations）。
            return issues

        # 检索模式（缺省/显式）——既有校验逐字不变
        if config.query_mode == "manual":
            if not config.query_value or not config.query_value.strip():
                issues.append(
                    ValidationIssue(
                        code=WF_VALIDATION_FAILED,
                        severity=Severity.ERROR,
                        message=f"向量知识库节点 {node.node_id} 缺少查询文本：manual 模式必须配置 query_value",
                        node_id=node.node_id,
                    )
                )
        else:
            if not config.query_reference or not config.query_reference.strip():
                issues.append(
                    ValidationIssue(
                        code=WF_VALIDATION_FAILED,
                        severity=Severity.ERROR,
                        message=f"向量知识库节点 {node.node_id} 缺少查询引用：reference 模式必须配置 query_reference",
                        node_id=node.node_id,
                    )
                )

        return issues

    # ------------------------------------------------------------------
    # 大模型节点：必备模型标识 + 提示词上界 + 采样取值域 + 输出结构值域
    # （llm_and_code_nodes 任务 3.1 / CHG-06 / spec §6.1 / design §2.3.2(1)）
    # ------------------------------------------------------------------
    def _validate_llm(self, node: NodeDefinition) -> List[ValidationIssue]:
        """大模型节点加载期校验（FR-04 / FR-10 / FR-13 / AC-04 / AC-15）。

        - model_identifier 留空 = 跟随全局主模型（WARNING 提示，FR-04
          修订 2026-11-20）；显式填写不校验是否在候选集内——允许自定义；
        - system_prompt / user_prompt ≤ 32768 字符（design §2.3.2(1)）；
        - 采样六参数走 validate_sampling 单一权威（INV-12 / FR-13），
          违规消息「参数名 + 实际值 + 允许区间」（如「温度 2.5 超出
          [0.0, 2.0]」），多违规逐项全部返回，不静默截断（DC-04）；
        - output_schema 类型名 ∈ 既有类型映射（空对象视为未声明）。
        语义错误码作消息前缀（LLM_PARAM_INVALID: ...，D-04）；issue.code
        统一 WF_VALIDATION_FAILED、severity=ERROR（既有校验契约不变）。
        """
        config: LlmNodeConfiguration = node.configuration
        issues: List[ValidationIssue] = []

        # 模型标识（FR-04 修订 2026-11-20）：留空 = 跟随全局主模型
        # （运行时 HttpLlmClient 回退构造期模型 = settings.llm_primary_model），
        # 降为 WARNING 可辨识提示，不再判 ERROR；显式填写仍按原语义。
        if not config.model_identifier or not config.model_identifier.strip():
            issues.append(
                ValidationIssue(
                    code=WF_VALIDATION_FAILED,
                    severity=Severity.WARNING,
                    message=(
                        f"大模型节点 {node.node_id} "
                        f"model_identifier 为空：运行时跟随全局主模型"
                        f"（LLM_PRIMARY_MODEL）；如需显式钉住请在节点配置填写模型名"
                    ),
                    node_id=node.node_id,
                )
            )

        # 提示词长度上界（字符数，design §2.3.2(1)）
        for field_name, label in (
            ("system_prompt", "系统提示词"),
            ("user_prompt", "用户提示词"),
        ):
            text = getattr(config, field_name)
            if len(text) > LLM_PROMPT_MAX_CHARS:
                issues.append(
                    ValidationIssue(
                        code=WF_VALIDATION_FAILED,
                        severity=Severity.ERROR,
                        message=(
                            f"{LLM_PARAM_INVALID}: 大模型节点 {node.node_id} "
                            f"{label}长度 {len(text)} 超出上界 "
                            f"{LLM_PROMPT_MAX_CHARS} 字符"
                        ),
                        node_id=node.node_id,
                    )
                )

        # 采样六参数：取值域单一权威（FR-13 / INV-12，未配置字段不参与检测）
        sampling = LlmSamplingParameters(
            temperature=config.temperature,
            top_p=config.top_p,
            top_k=config.top_k,
            max_tokens=config.max_tokens,
            stop=config.stop,
            seed=config.seed,
        )
        for violation in validate_sampling(sampling):
            issues.append(
                ValidationIssue(
                    code=WF_VALIDATION_FAILED,
                    severity=Severity.ERROR,
                    message=f"{LLM_PARAM_INVALID}: {violation}",
                    node_id=node.node_id,
                )
            )

        # 输出结构类型名值域（空对象视为未声明，design §2.3.2(1)）
        issues.extend(
            self._check_output_schema_types(
                node, config.output_schema, "大模型节点", LLM_PARAM_INVALID
            )
        )
        return issues

    # ------------------------------------------------------------------
    # 代码节点：语言 / 脚本 / 依赖 / 入口 / 输出结构静态校验
    # （llm_and_code_nodes 任务 3.2 / CHG-06 / spec §6.2 / design §2.3.2(2)）
    # ------------------------------------------------------------------
    def _validate_code(self, node: NodeDefinition) -> List[ValidationIssue]:
        """代码节点加载期校验（FR-25~FR-28 / AC-28~AC-32）。

        只登记与静态校验，不执行任何脚本（FR-31 / N-2）。语义错误码作
        消息前缀（CODE_SCRIPT_INVALID / CODE_SCRIPT_TOO_LARGE，D-04）：
        - language ∈ SUPPORTED_SCRIPT_LANGUAGES（非 python →
          「本期仅支持 python」定制消息，AC-28）；
        - code 去空白非空（FR-04b「脚本文本为空」）；
        - UTF-8 字节数 ≤ 构造注入上界（超界报「上界 + 实际字节数」，
          FR-26 / AC-29，拒绝而非截断）；
        - 静态合规复用既有 validate_custom_code（ast.parse 只解析不执行，
          守卫零修改 G-03；命中项前 5 条 + 「等 N 项」，AC-31/32）；
        - dependencies 条数 ≤ 上界、去重保序、拒版本分隔符（仅名称，FR-27）；
        - entry_function ^[A-Za-z_][A-Za-z0-9_]*$ 且 ≤64（FR-28）；
        - output_schema 类型名值域同 llm（仅声明语义，本期不产出输出）。
        """
        config: CodeNodeConfiguration = node.configuration
        issues: List[ValidationIssue] = []

        def _issue(semantic_code: str, detail: str) -> ValidationIssue:
            return ValidationIssue(
                code=WF_VALIDATION_FAILED,
                severity=Severity.ERROR,
                message=(
                    f"{semantic_code}: 代码节点 {node.node_id} {detail}"
                ),
                node_id=node.node_id,
            )

        # 语言取值域（FR-25 / AC-28）
        if config.language not in SUPPORTED_SCRIPT_LANGUAGES:
            issues.append(
                _issue(
                    CODE_SCRIPT_INVALID,
                    f"脚本语言不支持：{config.language}（本期仅支持 python）",
                )
            )

        # 脚本文本：去空白非空（FR-04b）→ UTF-8 字节上界（FR-26）→
        # 静态合规守卫（AC-31/32；空 / 超界 / 语言不支持的跳过路径均已有
        # ERROR 问题，无静默通过路径）
        if not config.code or not config.code.strip():
            issues.append(_issue(CODE_SCRIPT_INVALID, "脚本文本为空"))
        else:
            byte_count = len(config.code.encode("utf-8"))
            if byte_count > self._code_node_max_script_bytes:
                issues.append(
                    _issue(
                        CODE_SCRIPT_TOO_LARGE,
                        f"脚本长度 {byte_count} 字节超出上界 "
                        f"{self._code_node_max_script_bytes} 字节",
                    )
                )
            elif config.language in SUPPORTED_SCRIPT_LANGUAGES:
                issues.extend(self._check_code_static_guard(node, config))

        # 依赖清单：条数上界 / 去重保序 / 仅名称（FR-27，去重语义无独立
        # 归一化步骤，加载期校验为唯一执行点）
        if config.dependencies:
            dependencies = config.dependencies
            if len(dependencies) > self._code_node_max_dependencies:
                issues.append(
                    _issue(
                        CODE_SCRIPT_INVALID,
                        f"依赖条数 {len(dependencies)} 超出上界 "
                        f"{self._code_node_max_dependencies}",
                    )
                )
            seen: Set[str] = set()
            duplicated: List[str] = []
            for dependency in dependencies:
                if dependency in seen and dependency not in duplicated:
                    duplicated.append(dependency)
                seen.add(dependency)
            if duplicated:
                issues.append(
                    _issue(
                        CODE_SCRIPT_INVALID,
                        f"依赖清单存在重复条目："
                        f"{'、'.join(repr(name) for name in duplicated)}"
                        f"（依赖须去重保序）",
                    )
                )
            versioned = [
                dependency
                for dependency in dependencies
                if any(
                    separator in dependency
                    for separator in ("==", ">=", "~=", ";", "@")
                )
            ]
            if versioned:
                issues.append(
                    _issue(
                        CODE_SCRIPT_INVALID,
                        f"依赖条目含版本信息（依赖仅登记名称，不含版本号）："
                        f"{'、'.join(repr(name) for name in versioned)}",
                    )
                )

        # 入口函数名格式（FR-28：展示语义，本期无执行效果）
        entry_function = config.entry_function
        if (
            len(entry_function) > CODE_ENTRY_FUNCTION_MAX_CHARS
            or _CODE_ENTRY_FUNCTION_PATTERN.match(entry_function) is None
        ):
            issues.append(
                _issue(
                    CODE_SCRIPT_INVALID,
                    f"入口函数名 {entry_function!r} 非法"
                    f"（须匹配 ^[A-Za-z_][A-Za-z0-9_]*$ 且不超过 "
                    f"{CODE_ENTRY_FUNCTION_MAX_CHARS} 字符）",
                )
            )

        # 输出结构类型名值域（仅声明语义，值域同 llm）
        issues.extend(
            self._check_output_schema_types(
                node, config.output_schema, "代码节点", CODE_SCRIPT_INVALID
            )
        )
        return issues

    def _check_output_schema_types(
        self,
        node: NodeDefinition,
        output_schema: Optional[Dict[str, str]],
        node_label: str,
        semantic_code: str,
    ) -> List[ValidationIssue]:
        """输出结构类型名值域校验（llm / code 共用，design §2.3.2(1)/(2)）。

        值 ∈ 既有类型映射（agent 执行器 _check_type 同口径，大小写不敏感）；
        None 或空对象视为未声明（返回空清单）；语义错误码作消息前缀（D-04）。
        """
        if not output_schema:
            return []
        issues: List[ValidationIssue] = []
        for field_name, type_name in output_schema.items():
            if type_name.lower() not in _OUTPUT_SCHEMA_TYPE_NAMES:
                issues.append(
                    ValidationIssue(
                        code=WF_VALIDATION_FAILED,
                        severity=Severity.ERROR,
                        message=(
                            f"{semantic_code}: {node_label} {node.node_id} "
                            f"输出结构字段 {field_name!r} 声明的类型 "
                            f"{type_name!r} 未登记（类型名须 ∈ 既有类型映射）"
                        ),
                        node_id=node.node_id,
                    )
                )
        return issues

    def _check_code_static_guard(
        self, node: NodeDefinition, config: CodeNodeConfiguration
    ) -> List[ValidationIssue]:
        """脚本静态合规校验：复用既有 validate_custom_code（只调用不修改，G-03）。

        - 命中项取前 5 条 + 「等 N 项」计数，不含脚本全文
          （design §2.3.2(2)，AC-31/32）；
        - 守卫自身异常按「不合规 + 内部错误」处理，禁止放行
          （spec 5.5.3-4：代码节点不得因校验器异常而通过）。
        引擎侧守卫按需惰性导入（同 _validate_data_transform 口径：domain
        校验层不建立对 engine 的模块级依赖，custom_code_guard.py 零修改）。
        """
        # 引擎侧守卫按需惰性导入（导入无环：engine 不回依赖 domain.validation）
        from orchestration_platform.engine.nodes.data_transform_ops.custom_code_guard import (
            validate_custom_code,
        )

        try:
            verdict = validate_custom_code(config.code, config.language)
            if verdict.compliant:
                return []
            findings = list(verdict.findings)
        except Exception:
            return [
                ValidationIssue(
                    code=WF_VALIDATION_FAILED,
                    severity=Severity.ERROR,
                    message=(
                        f"{CODE_SCRIPT_INVALID}: 代码节点 {node.node_id} "
                        f"脚本静态校验未完成（校验器内部错误），按不合规处理"
                    ),
                    node_id=node.node_id,
                )
            ]

        summary_parts = list(findings[:CODE_FINDINGS_DISPLAY_LIMIT])
        if len(findings) > CODE_FINDINGS_DISPLAY_LIMIT:
            summary_parts.append(
                f"等 {len(findings) - CODE_FINDINGS_DISPLAY_LIMIT} 项"
            )
        return [
            ValidationIssue(
                code=WF_VALIDATION_FAILED,
                severity=Severity.ERROR,
                message=(
                    f"{CODE_SCRIPT_INVALID}: 代码节点 {node.node_id} "
                    f"脚本静态校验不合规（共 {len(findings)} 项命中）："
                    f"{'；'.join(summary_parts)}"
                ),
                node_id=node.node_id,
            )
        ]
