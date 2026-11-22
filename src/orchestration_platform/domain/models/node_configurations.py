# =============================================================================
# 五类节点配置模型（domain/models/node_configurations.py）
#
# 职责：定义 Agent / Tool / 数据源 / 触发器 / 控制流 五类节点的配置结构。
# 设计意图：
#   1. 用判别联合（discriminator=node_type）表达五类配置，保证类型安全；
#   2. 每类配置声明必备字段，缺失必备项时校验阶段即拒绝（需求规格 5.1.1.6）；
#   3. 控制流配置不含任何业务动作字段（模型/工具/数据源），
#      以结构约束落实「控制流禁止业务动作」（需求规格 5.3.1.14）。
# =============================================================================

from typing import Annotated, Any, Dict, List, Literal, Optional, Tuple, Union

from pydantic import BaseModel, Field, field_validator

from orchestration_platform.domain.models.enums import (
    AggregateFunction,
    ControlFlowKind,
    DataTransformOperation,
    DataTransformOutputFormat,
    DataSourceType,
    FrequencyPresetEnum,
    JoinType,
    JoinWaitPolicy,
    NodeType,
    ParallelFailureStrategy,
    SubworkflowContextStrategy,
    TriggerType,
    ContextWindowStrategyType,
)


class BranchCondition(BaseModel):
    """条件分支的单条分支条件：表达式为真则选择对应目标节点。

    表达式受限：仅允许读取共享状态字段的比较与逻辑运算，禁止函数调用。
    """

    target_node_id: str = Field(description="条件为真时跳转的目标节点标识")
    expression: str = Field(description="受限条件表达式，如 output.score >= 80")
    label: Optional[str] = Field(default=None, description="分支标签（画布展示用）")


class AgentInputEntry(BaseModel):
    """Agent 选择集条目：引用表达式 + 可选本地别名（unified_node_input DC-03）。

    reference 文法约束由加载期校验器把关（模型层仅声明类型）；
    alias 缺省用引用的命名输出名，节点内唯一性由校验器校验。
    """

    reference: str = Field(description="引用表达式（基线或字段路径形态）")
    alias: Optional[str] = Field(
        default=None,
        max_length=64,
        description="本地别名（缺省用命名输出名；节点内唯一）",
    )


class AgentNodeConfiguration(BaseModel):
    """Agent 节点配置：概率性推理节点。

    必备项：model_identifier（模型标识）——缺失时校验失败。
    """

    node_type: Literal[NodeType.AGENT] = NodeType.AGENT
    model_identifier: str = Field(description="模型标识（必备）")
    system_prompt: str = Field(default="", description="系统提示词")
    available_tools: List[str] = Field(default_factory=list, description="可用工具标识集合")
    output_schema: Optional[Dict[str, Any]] = Field(
        default=None, description="输出结构约束（字段名 -> 类型），用于结构化输出校验"
    )
    max_tool_rounds: Optional[int] = Field(default=None, ge=1, description="工具调用最大轮次（缺省用全局配置）")
    output_max_retries: Optional[int] = Field(default=None, ge=0, description="输出重生成最大次数（缺省用全局配置）")
    # 偏差修正 T140：沙箱策略标识（可选，None 则不启用沙箱隔离）
    sandbox_policy_id: Optional[str] = Field(default=None, description="沙箱策略标识（None 则不启用沙箱隔离）")
    # 统一节点输入引用（unified_node_input DC-03）：三态语义——
    # None=隐式全量平铺（旧 YAML 零迁移，INV-05③）；[]=显式空清单（用户消息无输入上下文条目）；
    # 非空=显式选择集（逐条装配，按「alias ?? 命名输出名」组织，清单外命名输出不可见 INV-12）
    inputs: Optional[List[AgentInputEntry]] = Field(
        default=None,
        description="上游输入选择集（None=隐式全量平铺；[]=显式空清单；非空=显式选择集）",
    )
    # 上下文窗口策略（context_engine 能力暴露，agent_tool_marketplace 差异化扩展）：
    # None=执行器缺省滑动窗口行为（与扩展前逐字节一致）；非空=ReAct 观察回填前按策略裁剪历史观察
    context_strategy: Optional[ContextWindowStrategyType] = Field(
        default=None,
        description="上下文窗口策略类型（None=缺省行为；映射 context_engine 四种窗口策略）",
    )

    @field_validator("available_tools")
    @classmethod
    def _dedupe_available_tools(cls, value: List[str]) -> List[str]:
        """绑定工具列表去重保序：同一标识至多出现一次（merge_tool_into_agent_node spec 9.2）。

        重复项归一后仍可解析旧 YAML（兼容）；不改字段名与类型（INV-1）。
        """
        seen: set = set()
        deduped: List[str] = []
        for identifier in value:
            if identifier in seen:
                continue
            seen.add(identifier)
            deduped.append(identifier)
        return deduped


class ToolNodeConfiguration(BaseModel):
    """Tool 节点配置：确定性工具调用节点。

    必备项：tool_identifier（工具标识）。
    """

    node_type: Literal[NodeType.TOOL] = NodeType.TOOL
    tool_identifier: str = Field(description="工具标识（必备）")
    tool_source: Literal["local", "mcp"] = Field(default="local", description="工具来源：本地函数或 MCP")
    argument_mapping: Dict[str, str] = Field(
        default_factory=dict, description="入参映射：工具参数名 -> 共享状态字段引用"
    )
    static_arguments: Dict[str, object] = Field(default_factory=dict, description="静态入参（常量）")


class DataSourceNodeConfiguration(BaseModel):
    """数据源节点配置：只读数据读取节点。

    必备项：connection_reference、query。query 不得包含写/删/结构变更语句。
    source_type + 21 连接参数字段与前端 DataSourceNodeConfiguration 对齐（DC-3），
    全部 Optional + 安全默认值，旧版 YAML 无这些字段仍可解析（INV-3/AC-7）。
    """

    node_type: Literal[NodeType.DATA_SOURCE] = NodeType.DATA_SOURCE
    connection_reference: str = Field(description="数据源连接引用（必备）")
    query: str = Field(default="", description="只读查询语句（禁止写操作）")
    dataset_identifier: Optional[str] = Field(default=None, description="数据集/数据流标识（可选）")

    source_type: Optional[DataSourceType] = Field(default=None, description="数据源类型")
    # CSV / JSON
    file_path: Optional[str] = Field(default=None, description="文件路径")
    # REST API
    url: Optional[str] = Field(default=None, description="REST API 端点 URL")
    method: str = Field(default="GET", description="HTTP 方法")
    headers: Dict[str, str] = Field(default_factory=dict, description="请求头")
    # 数据库
    connection_string: Optional[str] = Field(default=None, description="数据库连接串")
    # 通用
    encoding: str = Field(default="utf-8", description="文件编码")
    # Hologres
    hologres_endpoint: Optional[str] = Field(default=None, description="Hologres 端点地址")
    hologres_port: int = Field(default=80, description="Hologres 端口")
    hologres_database: Optional[str] = Field(default=None, description="Hologres 数据库名")
    hologres_access_key: Optional[str] = Field(default=None, description="Hologres AccessKey（凭据）")
    # Kafka
    kafka_brokers: Optional[str] = Field(default=None, description="Kafka broker 列表（逗号分隔）")
    kafka_topic: Optional[str] = Field(default=None, description="Kafka topic 名称")
    kafka_group_id: Optional[str] = Field(default=None, description="Kafka 消费组 ID")
    kafka_offset: str = Field(default="earliest", description="Kafka 消费起始位置")
    # Flink
    flink_jobmanager_url: Optional[str] = Field(default=None, description="Flink JobManager REST URL")
    flink_jar_id: Optional[str] = Field(default=None, description="Flink JAR ID")
    flink_program_args: Optional[str] = Field(default=None, description="Flink 作业参数")
    # Spark
    spark_master_url: Optional[str] = Field(default=None, description="Spark Master URL")
    spark_submit_args: Optional[str] = Field(default=None, description="spark-submit 额外参数")
    spark_app_name: Optional[str] = Field(default=None, description="Spark 应用名")
    # 企业连接器
    target_endpoint: Optional[str] = Field(default=None, description="企业连接器端点（CRM/ERP/OA）")


class PresetConfiguration(BaseModel):
    """预设频率配置：与 schedule 协同存储，承载频率选择器的结构化参数。

    固定频率按 preset 取对应字段（hourly→minute、daily→hour+minute 等）；
    间隔频率取 value 字段。跨字段语义校验由 _validate_trigger 兜底。
    """

    preset: FrequencyPresetEnum = Field(description="频率预设标识")
    minute: Optional[int] = Field(default=None, description="分钟（0–59）")
    hour: Optional[int] = Field(default=None, description="小时（0–23）")
    day_of_week: Optional[int] = Field(default=None, description="星期几（1–7，7=周日）")
    day_of_month: Optional[int] = Field(default=None, description="几日（1–31）")
    month: Optional[int] = Field(default=None, description="几月（1–12）")
    value: Optional[int] = Field(default=None, description="间隔数值（正整数）")


class TriggerNodeConfiguration(BaseModel):
    """触发器节点配置：工作流入口，声明启动方式与输入参数映射。

    必备项：trigger_type。input_template 用于将触发上下文映射为实例输入。

    语义澄清（澄清「起始节点不应有输入」的常见误区）：
      1. 「触发器无上游节点产出」≠「触发上下文恒为空」——触发上下文的初值
         为实例启动入参（手动触发与数据事件触发下均会注入至上下文），故
         input_template 仍可解析到值，并非必然失败；
      2. 空模板时输出为入口标记（trigger_type），非空模板时输出为经模板收敛
         后的映射结果；
      3. 非空模板为「全有或全无」语义——任一引用字段缺失即整体映射失败，
         返回 TRIGGER_PAYLOAD_MAPPING_FAILED；
      4. 定时触发当前无运行时调度入口，input_template 在该场景下不生效。
    """

    node_type: Literal[NodeType.TRIGGER] = NodeType.TRIGGER
    trigger_type: TriggerType = Field(default=TriggerType.MANUAL, description="触发方式（必备）")
    schedule: Optional[str] = Field(default=None, description="定时表达式（trigger_type=scheduled 时必填）")
    event_source: Optional[str] = Field(default=None, description="事件源标识（trigger_type=data_event 时必填）")
    preset_config: Optional[PresetConfiguration] = Field(default=None, description="预设频率配置（可选，与 schedule 协同）")
    input_template: Dict[str, str] = Field(
        default_factory=dict,
        description="启动参数模板：输入参数名 -> 触发上下文字段引用（手动 / 数据事件触发下生效；定时触发暂无运行时入口）",
    )


class ControlFlowNodeConfiguration(BaseModel):
    """控制流节点配置：分支/循环/并行汇聚/人工审核门四类子类型。

    禁止项：不得包含模型、工具、数据源等业务动作字段（结构上即无这些字段）。
    """

    node_type: Literal[NodeType.CONTROL_FLOW] = NodeType.CONTROL_FLOW
    control_flow_kind: ControlFlowKind = Field(description="控制流子类型（必备）")

    # 条件分支
    conditions: List[BranchCondition] = Field(default_factory=list, description="分支条件列表（按声明顺序求值）")
    default_branch_target: Optional[str] = Field(default=None, description="默认分支目标节点（全不命中时使用）")

    # 循环
    loop_body_entry: Optional[str] = Field(default=None, description="循环体入口节点标识")
    loop_termination_condition: Optional[str] = Field(
        default=None, description="循环终止条件表达式，为真则退出循环"
    )
    max_iterations: Optional[int] = Field(default=None, ge=1, description="循环最大迭代次数（缺省用全局配置）")

    # 并行汇聚
    parallel_failure_strategy: ParallelFailureStrategy = Field(
        default=ParallelFailureStrategy.FAST_FAIL, description="并行失败策略"
    )

    # 人工审核门
    review_instructions: Optional[str] = Field(default=None, description="审核说明（供人工决策参考）")
    review_timeout_action: Literal["reject", "wait"] = Field(
        default="reject", description="审核超时动作：自动驳回终止或保持等待"
    )
    # ---- 第二阶段新增：并行分支声明 ----
    parallel_branches: List[str] = Field(
        default_factory=list, description="并行分支入口节点标识列表（PARALLEL_JOIN 时使用）"
    )
    # ---- B6 并行扇出等待轴（roadmap_b6_parallel_fanout；Q2 裁决 2026-11-22：与失败轴正交）----
    wait_policy: JoinWaitPolicy = Field(
        default=JoinWaitPolicy.WAIT_ALL,
        description="汇聚等待策略：wait_all 等待全部成功到达 / wait_any 首个成功即触发 / wait_n_of_m 第 wait_count 个成功时触发",
    )
    wait_count: Optional[int] = Field(
        default=None,
        ge=1,
        description="wait_n_of_m 时必填：等待的成功分支数（1 ≤ n ≤ len(parallel_branches)，编译期校验）",
    )


# =============================================================================
# 第二阶段新增节点配置（对应 design 2.3.2(2)）
# =============================================================================

class SubWorkflowNodeConfiguration(BaseModel):
    """子工作流嵌套节点配置：递归调用另一工作流定义。

    必备项：subworkflow_definition_id（子工作流定义标识）。
    """

    node_type: Literal[NodeType.SUBWORKFLOW] = NodeType.SUBWORKFLOW
    subworkflow_definition_id: str = Field(description="子工作流定义标识（必备）")
    subworkflow_version: Optional[int] = Field(default=None, ge=1, description="子工作流版本（缺省取最新）")
    input_mapping: Dict[str, str] = Field(
        default_factory=dict, description="输入映射：子工作流参数名 -> 父共享状态字段引用"
    )
    output_mapping: Dict[str, str] = Field(
        default_factory=dict, description="输出映射：父状态字段名 -> 子工作流输出参数名"
    )
    context_strategy: SubworkflowContextStrategy = Field(
        default=SubworkflowContextStrategy.ISOLATE, description="上下文传递策略"
    )
    max_nesting_depth: int = Field(default=5, ge=1, description="最大嵌套深度（防递归溢出）")


class NodeTemplate(BaseModel):
    """动态注入节点模板：描述待注入节点的配置原型。"""

    template_id: str = Field(description="模板标识")
    node_type: NodeType = Field(description="模板节点类型")
    name: str = Field(description="模板节点名称")
    configuration: Dict[str, object] = Field(
        default_factory=dict, description="模板节点配置（键值对）"
    )


class DynamicInjectorNodeConfiguration(BaseModel):
    """动态注入节点配置：运行时按条件求值动态生成节点实例。

    必备项：injection_condition（注入条件表达式）、node_templates（模板列表）。
    """

    node_type: Literal[NodeType.DYNAMIC_INJECTOR] = NodeType.DYNAMIC_INJECTOR
    injection_condition: str = Field(description="注入条件表达式（受限求值，必备）")
    node_templates: List[NodeTemplate] = Field(
        default_factory=list, description="待注入节点模板列表"
    )
    max_injection_count: int = Field(default=100, ge=1, description="单次执行最大注入数量")
    target_position: Optional[str] = Field(
        default=None, description="注入位置：注入节点后接的目标节点标识"
    )


class MaskingCustomPattern(BaseModel):
    """自定义脱敏正则模板（expand_data_transform_ops CHG-B04，design §2.4.2-14）。

    pattern       —— 受限正则（须通过安全校验，长度 ≤ 512）
    mask_template —— 掩码模板：整段掩码 "MASK"，或按捕获组引用 "{1}**{2}"
    """

    pattern: str = Field(description="受限正则模式（长度 ≤ 512，禁回溯灾难模式）")
    mask_template: str = Field(description="掩码模板（MASK=整段掩码；{n}=第 n 捕获组保留）")


class DataTransformOperationParams(BaseModel):
    """数据转换操作参数：按操作类型携带不同参数。

    expand_data_transform_ops（design §2.4）：新操作参数统一追加为可选字段，
    既有字段不删改；同一字段被多个操作复用时语义以其所属操作口径为准。
    """

    # 映射操作
    map_expression: Optional[str] = Field(default=None, description="映射表达式（逐行变换）")
    # 过滤操作
    filter_condition: Optional[str] = Field(default=None, description="过滤条件表达式")
    # 聚合操作
    group_by: List[str] = Field(default_factory=list, description="分组字段列表")
    aggregate_function: Optional[AggregateFunction] = Field(default=None, description="聚合函数")
    aggregate_field: Optional[str] = Field(default=None, description="聚合目标字段")
    # 连接操作
    join_type: Optional[JoinType] = Field(default=None, description="连接类型")
    join_key: Optional[str] = Field(default=None, description="连接键字段名（单键；与 join_keys 二选一，单键优先兼容）")
    right_dataset_reference: Optional[str] = Field(
        default=None, description="右数据集引用（共享状态字段名）"
    )
    # ---- expand_data_transform_ops 仅追加（以下全部可选）----
    # sort（CHG-B03）：多键排序；sort_orders 与 sort_keys 对齐，缺省全升序
    sort_keys: List[str] = Field(default_factory=list, description="排序键列表（先到优先）")
    sort_orders: List[str] = Field(default_factory=list, description="与 sort_keys 对齐的方向列表：asc/desc（缺省 asc）")
    # distinct（CHG-B03）：去重
    distinct_keys: List[str] = Field(default_factory=list, description="去重键列表（空=全列去重）")
    keep: Optional[str] = Field(default=None, description="去重保留策略（first=保留首条，缺省 first）")
    # rename（CHG-B03）：字段重命名
    rename_map: Dict[str, str] = Field(default_factory=dict, description="重命名映射（旧字段名 → 新字段名）")
    # select_columns / drop_columns（CHG-B03）：列裁剪
    select_columns: List[str] = Field(default_factory=list, description="保留列清单（select_columns 操作）")
    drop_columns: List[str] = Field(default_factory=list, description="删除列清单（drop_columns 操作）")
    # cast（CHG-B03）：类型强转
    cast_field: Optional[str] = Field(default=None, description="强转目标字段名")
    cast_target: Optional[str] = Field(default=None, description="强转目标类型：number/date")
    date_format: Optional[str] = Field(default=None, description="date 解析格式（strptime 显式格式，如 %Y-%m-%d；date 目标必填，自然语言推断不可行）")
    # join 多条件（CHG-B03）：join_keys/right_join_keys 等长对齐，空 right_join_keys 视为同名
    join_keys: List[str] = Field(default_factory=list, description="左数据集连接键列表（多键）")
    right_join_keys: Optional[List[str]] = Field(default=None, description="右数据集连接键列表（空=与 join_keys 同名）")
    # row_number（CHG-B03）：窗口行号
    window_group_by: List[str] = Field(default_factory=list, description="窗口分组字段列表（空=全局）")
    window_order_by: List[str] = Field(default_factory=list, description="窗口排序键列表")
    window_orders: List[str] = Field(default_factory=list, description="与 window_order_by 对齐的方向列表：asc/desc")
    window_output_field: Optional[str] = Field(default=None, description="行号写入字段名（缺省 row_number）")
    # flatten / explode（CHG-B03）
    flatten_field: Optional[str] = Field(default=None, description="打平目标字段名（空=整行打平）")
    max_depth: Optional[int] = Field(default=None, description="打平最大深度（缺省 3）")
    explode_field: Optional[str] = Field(default=None, description="展开目标字段名（值须为 list）")
    max_rows: Optional[int] = Field(default=None, description="展开生成行数上限（缺省 100000）")
    # handle_nulls（CHG-B04）
    null_fields: List[str] = Field(default_factory=list, description="空值处理字段列表（空=全部字段）")
    null_value_scope: List[str] = Field(default_factory=list, description="空值判定口径：none/empty/missing（缺省全部）")
    strategy: Optional[str] = Field(default=None, description="空值策略：fill_constant/fill_mean/fill_median/drop_row")
    fill_value: Optional[Any] = Field(default=None, description="fill_constant 填充值")
    drop_scope: Optional[str] = Field(default=None, description="drop_row 删行触发范围：field=按指定列空值删行 / row=整行任意字段空值删行（缺省 field）")
    # regex_extract / regex_replace（CHG-B04）
    regex_field: Optional[str] = Field(default=None, description="正则操作目标字段名")
    pattern: Optional[str] = Field(default=None, description="正则模式（长度 ≤ 512，须通过安全校验）")
    extract_mode: Optional[str] = Field(default=None, description="regex_extract 模式：first/all（缺省 first）")
    group: Optional[int] = Field(default=None, description="regex_extract 捕获组序号（缺省 0=整段匹配）")
    replacement: Optional[str] = Field(default=None, description="regex_replace 替换串（支持 \\1 反向引用）")
    target_field: Optional[str] = Field(default=None, description="结果写入字段名（缺省覆盖目标字段）")
    # validate（CHG-B04）
    validate_field: Optional[str] = Field(default=None, description="校验目标字段名")
    validate_rule: Optional[str] = Field(default=None, description="校验规则：email/json")
    on_invalid: Optional[str] = Field(default=None, description="非法值处置：drop=删行 / mark=标记（缺省 drop）")
    # masking（CHG-B04）
    mask_fields: List[str] = Field(default_factory=list, description="脱敏字段清单")
    mask_rules: List[str] = Field(default_factory=list, description="脱敏规则名列表：phone/id_card/bank_card/email/name/address（内置规则集）")
    custom_patterns: List[MaskingCustomPattern] = Field(
        default_factory=list, description="自定义脱敏正则模板列表（pattern + mask_template，仅作用于 mask_fields）"
    )
    # json_path_extract（CHG-B05）
    source_field: Optional[str] = Field(default=None, description="JSON Path 来源字段名")
    path: Optional[str] = Field(default=None, description="JSON Path 表达式（受限语法，见 design §2.6.2）")
    # sampling（CHG-B05）
    sample_mode: Optional[str] = Field(default=None, description="采样模式：head/random（缺省 head）")
    sample_n: Optional[int] = Field(default=None, description="采样条数（head/random 按条数）")
    sample_percent: Optional[float] = Field(default=None, description="采样比例（0<比例≤100；与 sample_n 二选一，sample_n 优先）")
    random_seed: Optional[int] = Field(default=None, description="random 模式随机种子（可复现）")
    # custom_code（CHG-B05，仅登记+静态校验，不执行）
    language: Optional[str] = Field(default=None, description="脚本语言（仅 python，仅登记）")
    code: Optional[str] = Field(default=None, description="自定义脚本内容（仅登记与静态校验，运行期不执行）")


class DataTransformStep(BaseModel):
    """数据转换操作链步骤（expand_data_transform_ops design §2.2 / INV-01）。

    operation —— 步骤操作类型（必填）
    params    —— 步骤参数（缺省空参数，由各操作按需解释）
    """

    operation: DataTransformOperation = Field(description="步骤操作类型（必填）")
    params: DataTransformOperationParams = Field(
        default_factory=DataTransformOperationParams, description="步骤参数"
    )


class DataTransformOutputFormatParams(BaseModel):
    """数据转换节点输出格式参数（expand_data_transform_ops design §2.5，OP-05）。

    全部可选；缺省值由各输出形态按 design §2.5 归一：
      single_object —— record_index 缺省 0（首条）
      text          —— text_field 拼接、text_separator 缺省 "\\n"；两者皆空则全文 JSON 序列化
      kv            —— kv_key_field/kv_value_field 缺省取记录前两列
    """

    record_index: Optional[int] = Field(default=None, description="single_object：目标记录索引（缺省 0）")
    text_field: Optional[str] = Field(default=None, description="text：拼接字段名（空=全文 JSON 序列化）")
    text_separator: Optional[str] = Field(default=None, description="text：行间分隔符（缺省换行）")
    kv_key_field: Optional[str] = Field(default=None, description="kv：键字段名（空=取记录首列）")
    kv_value_field: Optional[str] = Field(default=None, description="kv：值字段名（空=取记录第二列）")


class DataTransformNodeConfiguration(BaseModel):
    """数据转换节点配置：对数据集执行操作链并按输出格式投影。

    必备项：operation（操作类型）、input_dataset_reference（输入数据集引用）。
    expand_data_transform_ops（design §2.2，CHG-B01/CHG-B02，既有字段不删改）：
      - operations：可选操作链（每步 {operation, params}）；空/缺失时按单步链
        [{operation: 顶层 operation, params: 顶层 params}] 归一（链长 1，行为等价），
        归一唯一权威入口 normalize_operations；
      - output_format：输出格式投影（缺省 dataset，行为不变）；
      - output_format_params：输出格式参数（text/kv/single_object 细化）。
    """

    node_type: Literal[NodeType.DATA_TRANSFORM] = NodeType.DATA_TRANSFORM
    operation: DataTransformOperation = Field(description="转换操作类型（必备）")
    input_dataset_reference: str = Field(description="输入数据集引用（共享状态字段名，必备）")
    params: DataTransformOperationParams = Field(
        default_factory=DataTransformOperationParams, description="操作参数"
    )
    # ---- expand_data_transform_ops 可选追加（INV-02：旧配置无此字段即单步链）----
    operations: Optional[List[DataTransformStep]] = Field(
        default=None, description="操作链（空/缺失=单步链，见 normalize_operations）"
    )
    output_format: Optional[DataTransformOutputFormat] = Field(
        default=None, description="输出格式（缺省 dataset：List[Dict]，行为不变）"
    )
    output_format_params: Optional[DataTransformOutputFormatParams] = Field(
        default=None, description="输出格式参数（text/kv/single_object 细化）"
    )


def normalize_operations(config: DataTransformNodeConfiguration) -> List[DataTransformStep]:
    """操作链归一化唯一权威（expand_data_transform_ops design §2.2，INV-03）。

    规则（纯函数，导出/导入/执行/校验共用）：
      - operations 非空 → 原样返回（None 步骤剔除）；
      - operations 缺失/空 → [{operation: 顶层 operation, params: 顶层 params}]
        （链长 1，旧 YAML 行为逐字等价，CHG-B06）。
    """
    if config.operations:
        steps = [step for step in config.operations if step is not None]
        if steps:
            return steps
    return [DataTransformStep(operation=config.operation, params=config.params)]


class PluginNodeConfiguration(BaseModel):
    """插件节点配置：引用已安装的第三方插件节点包。

    必备项：plugin_ref（插件节点名称，须已安装且健康）。
    设计意图（对应偏差修正 design 决策D5）：
      - 工作流 YAML 以 type: plugin + plugin_ref 引用已安装节点包；
      - config 传递给插件节点 setup 钩子；
      - input_mapping 从共享状态映射输入参数；
      - sandbox_policy_id 指定沙箱策略（SDK 节点默认走沙箱路径）。
    """

    node_type: Literal[NodeType.PLUGIN] = NodeType.PLUGIN
    plugin_ref: str = Field(description="插件节点名称（须已安装且健康，必备）")
    config: Dict[str, Any] = Field(default_factory=dict, description="节点配置（传递给 setup 钩子）")
    input_mapping: Dict[str, str] = Field(
        default_factory=dict, description="入参映射（参数名 → 共享状态字段名）"
    )
    sandbox_policy_id: Optional[str] = Field(
        default=None, description="沙箱策略标识（None 则使用默认沙箱策略）"
    )


class OutputDisplayNodeConfiguration(BaseModel):
    """输出展示节点配置：读取上游命名输出并写入事件流，纯内存透出。

    字段语义（optimize_output_display_node DC-01/DC-02）：
      - input_reference：输出引用集合（同名字段联合类型；旧单值=单元素集合，
        留空/空集合展示上游全部命名输出）；
      - display_title：展示标题（≤100 字符，留空以节点名称为标题）；
      - output_mode：输出方式（screen=屏幕显示 / file=输出为文件；缺省 None
        ≡ ["screen"]，由 normalize_output_mode 归一化解释）；
      - 不新增 output_content 字段（输出内容由 input_reference 集合承载，DC-02）。
    """

    node_type: Literal[NodeType.OUTPUT_DISPLAY] = NodeType.OUTPUT_DISPLAY
    input_reference: Optional[Union[str, List[str]]] = Field(
        default=None, description="输出引用集合（旧单值=单元素集合，留空展示全部）"
    )
    display_title: Optional[str] = Field(
        default=None, max_length=100, description="展示标题（可选，≤100 字符）"
    )
    output_mode: Optional[List[Literal["screen", "file"]]] = Field(
        default=None, description="输出方式（screen=屏幕显示 / file=输出为文件，缺省 screen）"
    )


def normalize_output_references(
    raw: Optional[Union[str, List[str]]],
) -> List[str]:
    """归一化输出引用为字符串集合（optimize_output_display_node CHG-02 / OP-03）。

    None / "" / [] → []；str → [str]（逐字节一致）；List[str] → 剔除首尾
    空白为空的元素、保序（不去重）。纯函数、零副作用、不修改入参。
    """

    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw] if raw else []
    result: List[str] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            result.append(item)
    return result


def normalize_output_mode(raw: Optional[List[str]]) -> List[str]:
    """归一化输出方式集合（optimize_output_display_node CHG-02 / DC-01）。

    None / [] → ["screen"]（缺省兼容存量屏幕显示语义）；仅保留取值域
    {"screen","file"} 内元素（保序、去重）；过滤后为空亦回落 ["screen"]
    （禁止"无任何输出方式"的不确定态）。
    """

    if not raw:
        return ["screen"]
    result: List[str] = []
    for item in raw:
        if item in ("screen", "file") and item not in result:
            result.append(item)
    return result if result else ["screen"]


# =============================================================================
# 向量知识库节点配置（vector_knowledge_base_node，design §2.2.1 / CHG-02）
# =============================================================================

# 分片策略取值域（vkb_write_mode / design §2.2.2(1)）。
# 取值与 integrations/vector_kb_management/models.py 的 ChunkStrategy 逐值一致；
# domain 层不得依赖 integrations 层，故本地声明同构 Literal 别名（SV-33 保证跨栈一一对应）。
ChunkStrategy = Literal["paragraph", "fixed_length", "recursive", "structural", "semantic"]


class ModelLoadingConfig(BaseModel):
    """模型加载配置（embedding/rerank 同构子结构，spec §6.4）。

    密钥约束：不含 api_key 字段（禁止内联明文密钥，INV-D04）；
    API 模式密钥统一来自运行期配置源（settings）。
    """

    mode: Literal["local", "api"] = Field(description="加载方式：本地运行 / 调用 API")
    model_identifier: str = Field(description="模型标识（必填非空）")
    base_url: Optional[str] = Field(default=None, description="服务地址（api 模式，非凭据）")
    timeout_seconds: Optional[int] = Field(default=None, ge=1, description="服务超时（秒）")
    model_path: Optional[str] = Field(default=None, description="本地模型路径（local 模式）")
    device: Optional[Literal["cpu", "gpu"]] = Field(default="cpu", description="本地运行设备")
    allow_degraded_fallback: bool = Field(default=False, description="local 依赖缺失时是否允许确定性兜底降级")


class WriteChunkParams(BaseModel):
    """写入模式分片参数（vkb_write_mode / design §2.2.2(2)）。

    承载写入模式分片参数，字段名与既有 ``ChunkStrategyConfig``
    （integrations/vector_kb_management/models.py:76-100）保持一致。
    跨字段约束（overlap < max_chunk_length）不在 pydantic 重复实现，
    由既有 ``validate_chunk_params`` 在分片前置统一拦截（INV-17 单点复用）。
    """

    max_chunk_length: int = Field(
        default=500, ge=50, le=2000, description="单分片长度上限（50..2000，默认 500）"
    )
    overlap: int = Field(default=50, ge=0, description="相邻分片重叠字符数（默认 50）")
    similarity_threshold: Optional[float] = Field(
        default=None, ge=0, le=1, description="语义相似度阈值（0..1，缺省取服务默认）"
    )
    hierarchy: Optional[List[str]] = Field(
        default=None, description="structural 标题行正则清单（缺省用内置清单）"
    )


class VectorKnowledgeBaseNodeConfiguration(BaseModel):
    """向量知识库节点配置：在知识库集合上执行向量检索，产出文本片段集合。

    互斥语义（design §2.2.1）：query_mode 决定生效字段——manual 仅用
    query_value（忽略 query_reference）；reference 仅用 query_reference
    （忽略 query_value）。两字段可共存声明，仅生效模式下的取值参与执行。
    """

    node_type: Literal[NodeType.VECTOR_KNOWLEDGE_BASE] = NodeType.VECTOR_KNOWLEDGE_BASE
    knowledge_base_id: str = Field(default="", description="知识库集合标识（必填非空）")
    query_mode: Literal["manual", "reference"] = Field(default="manual", description="query 录入模式")
    query_value: Optional[str] = Field(default=None, max_length=2000, description="手动录入查询文本")
    query_reference: Optional[str] = Field(default=None, description="继承录入引用表达式")
    top_k: int = Field(default=5, ge=1, le=100, description="返回数量上限（1..100，默认 5）")
    embedding_model: Optional[ModelLoadingConfig] = Field(
        default=None,
        description=(
            "embedding 模型配置（可选兼容占位：生效模型以知识库绑定优先，"
            "AC-51/AC-52；旧 YAML 含该字段照常加载，值不作废）"
        ),
    )
    rerank_model: Optional[ModelLoadingConfig] = Field(default=None, description="rerank 模型配置（可选）")
    # ---- 双模式与写入字段组（vkb_write_mode / design §2.2.2(1)；全部可选，旧 YAML 零迁移）----
    mode: Literal["retrieve", "write"] = Field(
        default="retrieve", description="节点模式：retrieve=检索（缺省，兼容存量）/ write=写入"
    )
    write_source_mode: Literal["manual", "reference"] = Field(
        default="manual", description="待写入文本录入模式：manual=手动 / reference=继承上游"
    )
    write_text: Optional[str] = Field(
        default=None, description="手动录入的待写入文本（长度上界由运行期配置约束，见门面校验）"
    )
    write_reference: Optional[str] = Field(default=None, description="继承录入的待写入文本引用表达式")
    write_chunk_strategy: Optional[ChunkStrategy] = Field(
        default=None, description="写入模式分片策略（缺省取知识库默认策略）"
    )
    write_chunk_params: Optional[WriteChunkParams] = Field(
        default=None, description="写入模式分片参数（可选，缺省取服务默认）"
    )
    write_document_id: Optional[str] = Field(
        default=None, description="写入幂等键显式覆盖（缺省 f'{knowledge_base_id}:{node_id}'，同键覆盖）"
    )
    # ---- 文档标注字段组（vkb_doc_annotation / CHG-08 / DC-15；全部可选，不收紧既有必填性）----
    annotation_origin: Optional[str] = Field(
        default=None, description="标注-来源信息（节点写入缺省'工作流写入'，可显式覆盖）"
    )
    annotation_published_at: Optional[str] = Field(
        default=None, description="标注-文档发布时间（ISO-8601 或 None=未知）"
    )
    annotation_custom: Optional[Dict[str, Any]] = Field(
        default=None, description="标注-用户自定义标注（名称→值，JSON 兼容、零凭据）"
    )
    annotation_enabled: Optional[bool] = Field(
        default=None, description="标注能力是否启用（None=跟随全局开关 vector_kb_annotation_enabled）"
    )


# =============================================================================
# 大模型节点与代码节点配置（llm_and_code_nodes，spec §6.1/§6.2 / design §2.3.2）
# =============================================================================

class LlmSamplingParameters(BaseModel):
    """大模型采样参数值对象（运行时映射载体，design §2.3.2 / DC-04）。

    六字段全部 Optional=None：None 表示「未配置」，与「显式配置缺省值」
    区分（D-01）；语义缺省与发送规则由 domain/values/llm_sampling_spec.py
    统一提供。本类只承载数据，不在模型层做取值域校验（取值域单一权威
    INV-12，校验入口在加载期校验器与 llm_sampling_spec 纯函数）。
    """

    temperature: Optional[float] = None
    top_p: Optional[float] = None
    top_k: Optional[int] = None
    max_tokens: Optional[int] = None
    stop: Optional[List[str]] = None
    seed: Optional[int] = None


class LlmNodeConfiguration(BaseModel):
    """大模型节点配置：单轮推理节点（spec §6.1 / design §2.3.2(1)）。

    必备项：model_identifier（模型标识）——缺失时校验失败。
    采样六字段平铺在配置上，None=未配置（语义缺省见 llm_sampling_spec）；
    不声明模型服务级连接与凭据字段（禁止字段清单见 spec §6.1-13，
    建模时不声明即不可配置）。
    """

    node_type: Literal[NodeType.LLM] = NodeType.LLM
    model_identifier: str = Field(description="模型标识（必备）")
    system_prompt: str = Field(default="", description="系统提示词")
    user_prompt: str = Field(default="", description="用户提示词")
    # 输入选择集：三态语义与 agent 节点一致（unified_node_input DC-03）
    inputs: Optional[List[AgentInputEntry]] = Field(
        default=None,
        description="上游输入选择集（None=隐式全量平铺；[]=显式空清单；非空=显式选择集）",
    )
    output_schema: Optional[Dict[str, str]] = Field(
        default=None, description="输出结构约束（字段名 -> 类型名），用于结构化输出校验"
    )
    temperature: Optional[float] = Field(
        default=None, description="温度（None=未配置，语义缺省 0.7，见 llm_sampling_spec）"
    )
    top_p: Optional[float] = Field(
        default=None, description="Top P（None=未配置，语义缺省 1.0）"
    )
    top_k: Optional[int] = Field(
        default=None, description="Top K（None=未配置，语义缺省 0=不启用）"
    )
    max_tokens: Optional[int] = Field(
        default=None, description="最大生成长度（None=未配置，语义缺省 4096）"
    )
    stop: Optional[List[str]] = Field(
        default=None, description="停止序列（None=未配置不发送；至多 4 条、去重保序）"
    )
    seed: Optional[int] = Field(
        default=None, description="随机种子（None=未配置，不发送）"
    )


# 代码节点支持的脚本语言白名单（本期仅 python，spec §6.2-2 / DC-22 细化）。
# 校验器以此生成「本期仅支持 python」定制消息；模型层 language 保持 str 不收紧，
# 以便历史非法值可被打开并修正（前端同口径，design §2.3.2(6)）。
SUPPORTED_SCRIPT_LANGUAGES: Tuple[str, ...] = ("python",)


class CodeNodeConfiguration(BaseModel):
    """代码节点配置：仅登记与静态校验，本期不执行（spec §6.2 / design §2.3.2(2)）。

    必备项：code（脚本文本）——必填且无默认值（新建为空串，加载期被必备项
    校验拒绝，禁止预置可运行样例，spec §6.2-9）。
    不声明脚本运行环境与依赖获取相关字段（禁止字段清单见 spec §6.2-9）。
    """

    node_type: Literal[NodeType.CODE] = NodeType.CODE
    language: str = Field(
        default="python", description="脚本语言（本期取值域仅 python，由加载期校验器把关）"
    )
    code: str = Field(description="脚本文本（必备，无默认值；本期仅登记与静态校验，不执行）")
    entry_function: str = Field(default="main", description="入口函数名（展示语义，本期无执行效果）")
    dependencies: Optional[List[str]] = Field(
        default=None, description="依赖名称清单（仅名称不含版本号，去重保序；仅登记）"
    )
    inputs: Optional[List[AgentInputEntry]] = Field(
        default=None,
        description="上游输入选择集（三态语义同 agent 节点）",
    )
    output_schema: Optional[Dict[str, str]] = Field(
        default=None, description="输出结构声明（声明语义为主，本期不产出输出）"
    )
    sandbox_policy_id: Optional[str] = Field(
        default=None, description="沙箱策略标识（与 agent 同名字段语义一致；本期不执行故不生效）"
    )


# 五类节点配置的判别联合：以 node_type 字段区分，供 pydantic 精确解析。
# 第二阶段追加三类成员（SUBWORKFLOW/DYNAMIC_INJECTOR/DATA_TRANSFORM），仍以 node_type 为判别字段。
# 第四阶段追加 PLUGIN 成员（偏差修正 T150），仍以 node_type 为判别字段。
# 输出展示追加 OUTPUT_DISPLAY 成员（枚举仅追加，既有成员顺序不变）。
# 向量知识库追加 VECTOR_KNOWLEDGE_BASE 成员（枚举仅追加，既有成员顺序不变）。
# 大模型/代码节点追加 LLM / CODE 成员（llm_and_code_nodes，枚举仅追加，既有成员顺序不变）。
NodeConfiguration = Annotated[
    Union[
        AgentNodeConfiguration,
        ToolNodeConfiguration,
        DataSourceNodeConfiguration,
        TriggerNodeConfiguration,
        ControlFlowNodeConfiguration,
        SubWorkflowNodeConfiguration,
        DynamicInjectorNodeConfiguration,
        DataTransformNodeConfiguration,
        PluginNodeConfiguration,
        OutputDisplayNodeConfiguration,
        VectorKnowledgeBaseNodeConfiguration,
        LlmNodeConfiguration,
        CodeNodeConfiguration,
    ],
    Field(discriminator="node_type"),
]
