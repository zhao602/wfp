# =============================================================================
# 领域枚举定义（domain/models/enums.py）
#
# 职责：集中定义平台全部判别字段的枚举取值，杜绝裸字符串魔法值。
# 设计意图（对应需求规格 6.1~6.5 与 design 2.3.2）：
#   - 枚举取值与需求规格数据约束逐项对齐，作为解析、校验、引擎、持久化的共同语言；
#   - 使用 (str, Enum) 混入使枚举天然可序列化为字符串（YAML/JSON 友好），
#     同时兼容 Python 3.10+（不依赖 3.11 才引入的 StrEnum）。
# =============================================================================

from enum import Enum


class NodeType(str, Enum):
    """节点类型：五类基础节点 + 三类第二阶段扩展节点。

    AGENT           —— 概率性推理节点（可调用模型与工具）
    TOOL            —— 确定性工具调用节点（已弃用：前端不再新建，存量解析兼容）
    DATA_SOURCE     —— 只读数据源读取节点
    TRIGGER         —— 工作流入口触发器节点
    CONTROL_FLOW    —— 控制流节点（分支/循环/并行/审核门，禁止业务动作）
    SUBWORKFLOW     —— 子工作流嵌套节点（第二阶段新增，递归调用另一工作流定义）
    DYNAMIC_INJECTOR—— 动态注入节点（第二阶段新增，运行时按条件生成节点实例）
    DATA_TRANSFORM  —— 数据转换节点（第二阶段新增，映射/过滤/聚合/Join）
    PLUGIN          —— 插件节点（第四阶段新增，第三方开发者自定义节点）
    SUPERVISOR      —— 监督者编排节点（第五阶段 G1/T216 新增，顶层 Supervisor
                       在业务逻辑级运行，子编排器管理领域专家 Agent 集群）
    OUTPUT_DISPLAY  —— 输出展示节点（读取上游命名输出并写入事件流，纯内存透出）
    VECTOR_KNOWLEDGE_BASE —— 向量知识库节点（在知识库集合上执行向量检索，产出文本片段集合）
    LLM             —— 大模型节点：单轮推理 + 采样参数可调
    CODE            —— 代码节点：仅登记 + 静态校验，不执行
    """

    AGENT = "agent"
    # 已弃用：前端不再新建，存量解析兼容（merge_tool_into_agent_node DC-E / FR-5 / CHG-12）；
    # 枚举成员保留不删（INV-2 只增不删），ToolNodeConfiguration / ToolNodeExecutor / 判别联合成员冻结零删除
    TOOL = "tool"
    DATA_SOURCE = "data_source"
    TRIGGER = "trigger"
    CONTROL_FLOW = "control_flow"
    SUBWORKFLOW = "subworkflow"
    DYNAMIC_INJECTOR = "dynamic_injector"
    DATA_TRANSFORM = "data_transform"
    PLUGIN = "plugin"
    # 第五阶段偏差修正（G1/T216）：监督者编排节点，枚举仅追加
    SUPERVISOR = "supervisor"
    # 输出展示节点：仅追加，枚举值与前端/YAML 逐字符一致
    OUTPUT_DISPLAY = "output_display"
    # 向量知识库节点：仅追加，枚举值与前端/YAML 逐字符一致
    VECTOR_KNOWLEDGE_BASE = "vector_knowledge_base"
    # 大模型节点：单轮推理 + 采样参数可调（llm_and_code_nodes，枚举仅追加）
    LLM = "llm"
    # 代码节点：仅登记 + 静态校验，不执行（llm_and_code_nodes，枚举仅追加）
    CODE = "code"


class ControlFlowKind(str, Enum):
    """控制流节点子类型。

    CONDITIONAL_BRANCH —— 条件分支：依据表达式求值选择唯一后继
    LOOP               —— 循环：按终止条件重复执行循环体，设最大迭代上限
    PARALLEL_JOIN      —— 并行汇聚：等待全部上游分支完成或快速失败
    HUMAN_REVIEW_GATE  —— 人工审核门：挂起实例等待人工决策
    """

    CONDITIONAL_BRANCH = "conditional_branch"
    LOOP = "loop"
    PARALLEL_JOIN = "parallel_join"
    HUMAN_REVIEW_GATE = "human_review_gate"


class TriggerType(str, Enum):
    """触发器类型：声明工作流的启动方式。"""

    MANUAL = "manual"          # 手动触发：由接口调用驱动
    SCHEDULED = "scheduled"    # 定时触发：由定时调度器驱动
    DATA_EVENT = "data_event"  # 数据事件触发：由数据源事件信号驱动


class FrequencyPresetEnum(str, Enum):
    """频率预设类型：触发器定时频率选择器的 10 种预设标识。

    与前端 FrequencyPreset 类型逐字符对齐。
    固定频率（hourly~yearly）可设置具体时间参数；
    间隔频率（interval_*）可设置间隔数值。
    """

    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    YEARLY = "yearly"
    INTERVAL_MINUTES = "interval_minutes"
    INTERVAL_HOURS = "interval_hours"
    INTERVAL_DAYS = "interval_days"
    INTERVAL_WEEKS = "interval_weeks"
    INTERVAL_MONTHS = "interval_months"


class InstanceStatus(str, Enum):
    """执行实例状态（对应需求规格 6.3）。

    终态：SUCCEEDED / FAILED / CANCELLED / TIMEOUT_TERMINATED / UNRECOVERABLE。
    仅终态允许发起回滚或重放（禁止对 RUNNING 实例执行回滚/重放）。
    """

    PENDING = "pending"                        # 待执行
    RUNNING = "running"                        # 运行中
    WAITING_REVIEW = "waiting_review"          # 等待审核
    SUCCEEDED = "succeeded"                    # 成功
    FAILED = "failed"                          # 失败
    CANCELLED = "cancelled"                    # 已取消
    TIMEOUT_TERMINATED = "timeout_terminated"  # 超时终止
    UNRECOVERABLE = "unrecoverable"            # 不可恢复
    REVIEW_TIMEOUT = "review_timeout"          # 审核超时
    # ---- 第三阶段新增 ----
    QUEUED = "queued"                          # 全局调度排队中
    PREEMPTED = "preempted"                    # 被高优先级实例抢占


# 实例终态集合：用于判断是否允许回滚/重放等只读后续操作
TERMINAL_INSTANCE_STATUSES = frozenset(
    {
        InstanceStatus.SUCCEEDED,
        InstanceStatus.FAILED,
        InstanceStatus.CANCELLED,
        InstanceStatus.TIMEOUT_TERMINATED,
        InstanceStatus.UNRECOVERABLE,
        InstanceStatus.REVIEW_TIMEOUT,
    }
)


class EventType(str, Enum):
    """执行事件类型（对应需求规格 6.5 + 第二阶段扩展）。"""

    INSTANCE_CREATED = "instance_created"      # 实例创建
    NODE_STARTED = "node_started"              # 节点开始
    NODE_SUCCEEDED = "node_succeeded"          # 节点成功
    NODE_FAILED = "node_failed"                # 节点失败
    STATUS_CHANGED = "status_changed"          # 状态变更（含模型切换标记）
    CHECKPOINT_CREATED = "checkpoint_created"  # 检查点生成
    ROLLBACK = "rollback"                      # 回滚
    REPLAY = "replay"                          # 重放
    # ---- 第二阶段新增事件 ----
    NODE_INJECTED = "node_injected"                  # 动态注入节点
    SUBWORKFLOW_STARTED = "subworkflow_started"      # 子工作流启动
    SUBWORKFLOW_COMPLETED = "subworkflow_completed"  # 子工作流完成
    PARALLEL_BRANCH_STARTED = "parallel_branch_started"    # 并行分支启动
    PARALLEL_BRANCH_COMPLETED = "parallel_branch_completed"  # 并行分支完成
    CONTEXT_WINDOW_APPLIED = "context_window_applied"  # 上下文窗口策略应用
    MCP_TOOL_CALLED = "mcp_tool_called"              # MCP 工具调用
    # ---- 第三阶段治理事件 ----
    AUTH_DENIED = "auth_denied"                        # 鉴权拒绝
    TENANT_QUOTA_EXCEEDED = "tenant_quota_exceeded"    # 租户配额超限
    TOKEN_USAGE_RECORDED = "token_usage_recorded"      # Token 用量记录
    BUDGET_ALERT = "budget_alert"                      # 预算告警
    BUDGET_CIRCUIT_BREAK = "budget_circuit_break"      # 预算熔断
    AUDIT_LOG_WRITTEN = "audit_log_written"            # 审计日志写入
    INSTANCE_QUEUED = "instance_queued"                # 实例进入全局队列
    INSTANCE_PREEMPTED = "instance_preempted"          # 实例被抢占
    INSTANCE_RESTORED = "instance_restored"            # 实例从抢占恢复
    RESOURCE_POOL_EXHAUSTED = "resource_pool_exhausted"  # 资源池耗尽


class ReplayMode(str, Enum):
    """重放模式。

    STRICT_REPLAY —— 严格重放：复用历史节点输出，跳过实际调用，无外部调用
    RE_EXECUTE    —— 重新执行：重新调用全部节点，记录与历史输出的差异
    """

    STRICT_REPLAY = "strict_replay"
    RE_EXECUTE = "re_execute"


class ParallelFailureStrategy(str, Enum):
    """并行汇聚失败策略。

    FAST_FAIL —— 快速失败：任一分支失败即终止，其余分支置已取消
    WAIT_ALL  —— 等待全部：等待全部分支结束后统一汇总失败
    """

    FAST_FAIL = "fast_fail"
    WAIT_ALL  = "wait_all"


class JoinWaitPolicy(str, Enum):
    """并行汇聚等待策略（B6 roadmap_b6_parallel_fanout；Q2 用户裁决 2026-11-22：
    等待轴与 ParallelFailureStrategy 失败轴正交——等待轴决定 join 等待多少
    成功分支到达后触发，失败轴决定分支失败时的行为）。

    WAIT_ALL    —— 等待全部：全部成功分支到达后触发汇聚
    WAIT_ANY    —— 等待任一：首个成功分支到达即触发（剩余分支取消置 SKIPPED）
    WAIT_N_OF_M —— 等待 N 个：第 wait_count 个成功分支到达时触发（剩余取消）
    """

    WAIT_ALL = "wait_all"
    WAIT_ANY = "wait_any"
    WAIT_N_OF_M = "wait_n_of_m"


class ReviewDecision(str, Enum):
    """人工审核决策。"""

    APPROVED = "approved"  # 通过：从审核门后继节点继续
    REJECTED = "rejected"  # 驳回：按配置回退或终止


class NodeExecutionStatus(str, Enum):
    """节点执行状态（用于画布可视化与事件映射）。

    PENDING / RUNNING / SUCCEEDED / FAILED / SKIPPED 五态，
    与需求规格 5.5.1.6「待执行、执行中、成功、失败、已跳过」一一对应。
    """

    PENDING = "pending"      # 待执行
    RUNNING = "running"      # 执行中
    SUCCEEDED = "succeeded"  # 成功
    FAILED = "failed"        # 失败
    SKIPPED = "skipped"      # 已跳过（如并行快速失败时被取消的分支）


class Severity(str, Enum):
    """校验问题严重级别。"""

    ERROR = "error"
    WARNING = "warning"


# =============================================================================
# 第二阶段新增枚举（对应 spec 6.6~6.7、design 2.3.2）
# =============================================================================

class ContextWindowStrategyType(str, Enum):
    """上下文窗口策略类型：防止超出模型上下文长度限制。

    SLIDING_WINDOW     —— 滑动窗口：保留最近 N 轮对话
   +SUMMARY_COMPRESSION—— 摘要压缩：将早期对话摘要为简短描述
    IMPORTANCE_RANKING —— 重要性排序：按相关性评分保留高价值上下文
    STRUCTURAL_LOSSLESS—— 结构化无损裁剪（第五阶段 G2/T211 新增）：
                          按结构单元（冗余字段/重复段落/已消费区块）裁剪，
                          语义无损且裁剪记录可追溯
    """

    SLIDING_WINDOW = "sliding_window"
    SUMMARY_COMPRESSION = "summary_compression"
    IMPORTANCE_RANKING = "importance_ranking"
    # 第五阶段偏差修正（G2/T211）：结构化无损裁剪策略，枚举仅追加
    STRUCTURAL_LOSSLESS = "structural_lossless"


class ToolPermission(str, Enum):
    """MCP 工具权限级别。

    READONLY —— 只读：仅查询不产生副作用
    READWRITE—— 读写：可修改状态
    ADMIN    —— 管理：可注册/注销工具
    """

    READONLY = "readonly"
    READWRITE = "readwrite"
    ADMIN = "admin"


class SubworkflowContextStrategy(str, Enum):
    """子工作流上下文传递策略。

    INHERIT —— 继承：子工作流共享父工作流只读状态视图
    ISOLATE —— 隔离：深拷贝父状态作为初始值，修改不影响父状态
    """

    INHERIT = "inherit"
    ISOLATE = "isolate"


class DataTransformOperation(str, Enum):
    """数据转换操作类型。

    MAP      —— 映射：按表达式逐行变换
    FILTER   —— 过滤：按条件保留子集
    AGGREGATE—— 聚合：按分组键与聚合函数汇总
    JOIN     —— 连接：按键连接两个数据集（expand_data_transform_ops：支持多键/多条件）

    ---- expand_data_transform_ops 仅追加（design §2.4，枚举只追加不删改）----
    结构化操作：sort/distinct/rename/select_columns/drop_columns/cast/row_number/
                flatten/explode
    清洗治理：handle_nulls/regex_extract/regex_replace/validate/masking
    开发者兜底：json_path_extract/sampling/custom_code
    """

    MAP = "map"
    FILTER = "filter"
    AGGREGATE = "aggregate"
    JOIN = "join"
    # ---- expand_data_transform_ops 仅追加 ----
    SORT = "sort"                                  # 排序：多键升降序（CHG-B03）
    DISTINCT = "distinct"                          # 去重：全列/按键保留首条（CHG-B03）
    RENAME = "rename"                              # 重命名：字段映射（CHG-B03）
    SELECT_COLUMNS = "select_columns"              # 列裁剪：保留指定列（CHG-B03）
    DROP_COLUMNS = "drop_columns"                  # 列删除：剔除指定列（CHG-B03）
    CAST = "cast"                                  # 类型强转：number/date（CHG-B03）
    ROW_NUMBER = "row_number"                      # 窗口行号：分组排序编号（CHG-B03）
    FLATTEN = "flatten"                            # 嵌套打平：多级 dict 展开（CHG-B03）
    EXPLODE = "explode"                            # 行转多行：列表字段展开（CHG-B03）
    HANDLE_NULLS = "handle_nulls"                  # 空值处理：填充/删行（CHG-B04）
    REGEX_EXTRACT = "regex_extract"                # 正则抽取（CHG-B04）
    REGEX_REPLACE = "regex_replace"                # 正则替换（CHG-B04）
    VALIDATE = "validate"                          # 规则校验：email/json（CHG-B04）
    MASKING = "masking"                            # 脱敏：内置规则+自定义正则模板（CHG-B04）
    JSON_PATH_EXTRACT = "json_path_extract"        # JSON Path 提取（CHG-B05）
    SAMPLING = "sampling"                          # 采样：head/random（CHG-B05）
    CUSTOM_CODE = "custom_code"                    # 自定义脚本（仅登记+静态校验，不执行；CHG-B05）


class DataTransformOutputFormat(str, Enum):
    """数据转换节点输出格式（expand_data_transform_ops design §2.5，OP-05）。

    DATASET      —— List[Dict] 数据集（既有缺省，行为不变）
    SINGLE_OBJECT—— 单对象：整体视作一条记录的索引（缺省首条）
    TEXT         —— 文本：指定字段拼接（缺省全文 JSON 序列化）
    KV           —— 键值对：两列转映射（缺省取首两列）
    """

    DATASET = "dataset"
    SINGLE_OBJECT = "single_object"
    TEXT = "text"
    KV = "kv"


class JoinType(str, Enum):
    """数据连接类型。

    INNER —— 内连接
    LEFT  —— 左外连接
    RIGHT —— 右外连接
    FULL  —— 全外连接
    """

    INNER = "inner"
    LEFT = "left"
    RIGHT = "right"
    FULL = "full"


class AggregateFunction(str, Enum):
    """聚合函数。

    SUM   —— 求和
    COUNT —— 计数
    AVG   —— 平均
    MAX   —— 最大
    MIN   —— 最小
    """

    SUM = "sum"
    COUNT = "count"
    AVG = "avg"
    MAX = "max"
    MIN = "min"


class DataSourceType(str, Enum):
    """数据源类型。

    CSV      —— CSV 文件
    JSON     —— JSON 文件
    REST_API —— REST API 端点
    DATABASE —— 数据库（SQL 查询）
    HOLOGRES —— Hologres 实时数仓
    KAFKA    —— Kafka 消息流
    FLINK    —— Flink 流计算引擎
    SPARK    —— Spark 批计算引擎
    CRM      —— CRM 系统（第四阶段新增）
    ERP      —— ERP 系统（第四阶段新增）
    OA       —— OA 系统（第四阶段新增）
    """

    CSV = "csv"
    JSON = "json"
    REST_API = "rest_api"
    DATABASE = "database"
    HOLOGRES = "hologres"
    KAFKA = "kafka"
    FLINK = "flink"
    SPARK = "spark"
    CRM = "crm"
    ERP = "erp"
    OA = "oa"


class McpTransportType(str, Enum):
    """MCP 传输协议类型。

    STDIO —— 标准输入输出
    HTTP  —— HTTP 长连接
    SSE   —— Server-Sent Events
    """

    STDIO = "stdio"
    HTTP = "http"
    SSE = "sse"


# =============================================================================
# 第三阶段治理枚举（对应 spec 第 6 章、design 2.3）
# =============================================================================

class ResourceType(str, Enum):
    """资源类型：RBAC 中受保护的七类资源。"""

    WORKFLOW_DEFINITION = "workflow_definition"    # 工作流定义
    EXECUTION_INSTANCE = "execution_instance"      # 执行实例
    MCP_TOOL = "mcp_tool"                          # MCP 工具
    DATA_SOURCE_CONNECTOR = "data_source_connector"  # 数据源连接器
    TENANT_CONFIG = "tenant_config"                # 租户配置
    AUDIT_LOG = "audit_log"                        # 审计日志
    GOVERNANCE_POLICY = "governance_policy"        # 治理策略


class OperationType(str, Enum):
    """操作类型：对资源执行的操作。"""

    CREATE = "create"    # 创建
    READ = "read"        # 读取
    UPDATE = "update"    # 更新
    DELETE = "delete"    # 删除
    EXECUTE = "execute"  # 执行
    EXPORT = "export"    # 导出


class ResourceScope(str, Enum):
    """资源范围粒度。"""

    TENANT = "tenant"    # 租户级
    ORG = "org"          # 组织级
    SELF = "self"        # 个人级


class TenantStatus(str, Enum):
    """租户状态。"""

    ACTIVE = "active"        # 活跃
    SUSPENDED = "suspended"  # 暂停
    TERMINATED = "terminated"  # 终止


class UserStatus(str, Enum):
    """用户状态。"""

    ACTIVE = "active"        # 活跃
    DISABLED = "disabled"    # 禁用
    LOCKED = "locked"        # 锁定


class TokenStatus(str, Enum):
    """访问令牌状态。"""

    ACTIVE = "active"      # 有效
    EXPIRED = "expired"    # 过期
    REVOKED = "revoked"    # 撤销


class BudgetPeriod(str, Enum):
    """预算周期。"""

    DAILY = "daily"      # 日
    WEEKLY = "weekly"    # 周
    MONTHLY = "monthly"  # 月
    YEARLY = "yearly"    # 年


class CircuitBreakStrategy(str, Enum):
    """预算熔断策略。"""

    HARD_STOP = "hard_stop"    # 硬停止：立即拒绝新实例
    SOFT_ALERT = "soft_alert"  # 软告警：仅告警不拒绝


class MaskMethod(str, Enum):
    """脱敏方法。"""

    FULL_MASK = "full_mask"          # 全遮盖
    PARTIAL_MASK = "partial_mask"    # 部分遮盖
    HASH = "hash"                    # 哈希替换
    REPLACE = "replace"              # 常量替换


class SchedulerPolicyType(str, Enum):
    """调度策略类型。"""

    STRICT_PRIORITY = "strict_priority"  # 严格优先级
    FAIR_SHARE = "fair_share"            # 公平共享
    WEIGHTED_QUEUE = "weighted_queue"    # 加权队列


class ResourcePoolType(str, Enum):
    """资源池类型。"""

    CONCURRENCY_SLOT = "concurrency_slot"  # 并发槽位
    CPU = "cpu"                            # CPU
    MEMORY = "memory"                      # 内存


class InstancePriority(str, Enum):
    """实例优先级。"""

    LOW = "low"          # 低
    NORMAL = "normal"    # 普通
    HIGH = "high"        # 高
    URGENT = "urgent"    # 紧急


class BudgetScope(str, Enum):
    """预算范围。"""

    TENANT = "tenant"          # 租户级
    WORKFLOW = "workflow"      # 工作流级
    INSTANCE = "instance"      # 实例级


class AuditOperationResult(str, Enum):
    """审计操作结果。"""

    SUCCESS = "success"    # 成功
    FAILURE = "failure"    # 失败
    DENIED = "denied"      # 拒绝


# =============================================================================
# 第三阶段偏差修正枚举（对应 spec 5.x / design 2.3.2）
# =============================================================================

class IntentType(str, Enum):
    """自然语言编排意图类型。

    CREATE_WORKFLOW  —— 创建工作流：从自然语言描述生成全新工作流定义
    MODIFY_WORKFLOW  —— 修改工作流：在既有工作流基础上应用自然语言修改指令
    """

    CREATE_WORKFLOW = "create_workflow"
    MODIFY_WORKFLOW = "modify_workflow"


class NLErrorCode(str, Enum):
    """自然语言编排错误码。

    NL_PARSE_FAILED       —— LLM 意图解析失败（服务不可用或返回无法解析）
    TOPO_INVALID          —— 生成的 YAML 拓扑校验失败（环路/悬空节点/非法连接）
    NODE_NOT_FOUND        —— 修改指令引用的节点名在既有工作流中不存在
    INJECTION_DETECTED    —— 输入检测到注入攻击（YAML 注入或提示注入）
    DEFINITION_NOT_FOUND  —— 修改指令指向的既有工作流定义不存在
    """

    NL_PARSE_FAILED = "nl_parse_failed"
    TOPO_INVALID = "topo_invalid"
    NODE_NOT_FOUND = "node_not_found"
    INJECTION_DETECTED = "injection_detected"
    DEFINITION_NOT_FOUND = "definition_not_found"


class AttributeSource(str, Enum):
    """ABAC 属性来源维度。

    SUBJECT     —— 主体属性：发起请求的用户属性（部门、角色、标签等）
    RESOURCE    —— 资源属性：被访问目标对象的属性（敏感等级、所有者等）
    ENVIRONMENT —— 环境属性：请求上下文属性（当前时间、请求 IP、租户标签）
    """

    SUBJECT = "subject"
    RESOURCE = "resource"
    ENVIRONMENT = "environment"


class PolicyEffect(str, Enum):
    """ABAC 策略效果。"""

    ALLOW = "allow"    # 允许
    DENY = "deny"      # 拒绝


class FailMode(str, Enum):
    """ABAC 属性求值失败时的决策回退行为。"""

    DENY = "deny"    # 默认拒绝
    ALLOW = "allow"  # 默认放行


class ConditionOperator(str, Enum):
    """ABAC 条件操作符。

    EQ            —— 等于
    NEQ           —— 不等于
    IN            —— 属于集合
    NOT_IN        —— 不属于集合
    RANGE         —— 在数值范围内
    IP_IN_RANGE   —— IP 在指定网段内
    TIME_IN_RANGE —— 时间在指定时段内
    """

    EQ = "eq"
    NEQ = "neq"
    IN = "in"
    NOT_IN = "not_in"
    RANGE = "range"
    IP_IN_RANGE = "ip_in_range"
    TIME_IN_RANGE = "time_in_range"


class AbacErrorCode(str, Enum):
    """ABAC 错误码。"""

    ATTR_EVAL_FAILED = "attr_eval_failed"        # 属性求值失败
    POLICY_FORMAT_INVALID = "policy_format_invalid"  # 策略格式非法
    NO_ABAC_POLICY = "no_abac_policy"            # 无 ABAC 策略


class TombstoneErrorCode(str, Enum):
    """墓碑操作错误码。"""

    HASH_CHAIN_BREAK = "hash_chain_break"        # 哈希链断裂
    ENTRY_NOT_FOUND = "entry_not_found"          # 条目不存在
    ALREADY_TOMBSTONE = "already_tombstone"      # 已标记为墓碑
    TOMBSTONE_PARTIAL_FAIL = "tombstone_partial_fail"  # 批量标记部分失败
    PHYSICAL_DELETE_FORBIDDEN = "physical_delete_forbidden"  # 物理删除被禁止


class EventBusType(str, Enum):
    """事件总线类型。"""

    KAFKA = "kafka"            # Kafka 事件总线
    IN_MEMORY = "in_memory"    # 内存事件总线（降级）


class MessageEventType(str, Enum):
    """消息事件类型（用于事件总线发布）。

    WORKFLOW_EXECUTION    —— 工作流执行事件（节点开始/成功/失败等）
    SESSION_STATE_CHANGE  —— Agent 会话状态变更事件
    """

    WORKFLOW_EXECUTION = "workflow_execution"
    SESSION_STATE_CHANGE = "session_state_change"


class EventBusErrorCode(str, Enum):
    """事件总线错误码。"""

    MQ_PUBLISH_FAILED = "mq_publish_failed"      # 事件发布失败
    MQ_DEGRADED = "mq_degraded"                  # 消息中间件降级
    REPLAY_GAP_DETECTED = "replay_gap_detected"  # 重放事件缺失区间


class CacheBackendType(str, Enum):
    """缓存后端类型。"""

    REDIS = "redis"          # Redis 缓存后端
    IN_MEMORY = "in_memory"  # 内存缓存后端（降级）


class CacheErrorCode(str, Enum):
    """缓存错误码。"""

    REDIS_DEGRADED = "redis_degraded"      # Redis 降级
    LOCK_ACQUIRE_FAILED = "lock_acquire_failed"  # 锁获取失败
    RATE_LIMITED = "rate_limited"          # 限流拒绝


# =============================================================================
# 第四阶段：Agent 执行沙箱枚举（偏差修正 T130）
# =============================================================================

class IsolationMode(str, Enum):
    """沙箱隔离模式。

    LOGICAL  —— 逻辑隔离：进程内三守卫（路径/网络/资源），默认实现
    PHYSICAL —— 物理隔离：容器级隔离，需容器运行时支持
    """

    LOGICAL = "logical"    # 逻辑隔离（默认）
    PHYSICAL = "physical"  # 物理隔离（容器）


class NetworkPolicyMode(str, Enum):
    """沙箱网络策略模式。

    DEFAULT_DENY —— 默认拒绝：不提供任何出网通道
    WHITELIST    —— 白名单模式：仅白名单目标放行
    """

    DEFAULT_DENY = "default_deny"  # 默认拒绝
    WHITELIST = "whitelist"        # 白名单放行


class ViolationType(str, Enum):
    """沙箱违规类型。

    FILE_ESCAPE       —— 文件路径穿越：访问工作目录外路径
    NETWORK_DENIED    —— 网络访问拒绝：目标不在白名单
    RESOURCE_EXCEEDED —— 资源超限：CPU/内存/输出超限
    TIMEOUT           —— 执行超时
    """

    FILE_ESCAPE = "file_escape"            # 路径穿越
    NETWORK_DENIED = "network_denied"      # 网络拒绝
    RESOURCE_EXCEEDED = "resource_exceeded"  # 资源超限
    TIMEOUT = "timeout"                    # 执行超时


class SandboxErrorCode(str, Enum):
    """沙箱错误码。"""

    SANDBOX_POLICY_INVALID = "sandbox_policy_invalid"        # 策略格式非法
    SANDBOX_INTERNAL_ERROR = "sandbox_internal_error"        # 沙箱内部错误
    SANDBOX_TIMEOUT = "sandbox_timeout"                      # 执行超时
    SANDBOX_LIMIT_EXCEEDED = "sandbox_limit_exceeded"        # 资源超限
    SANDBOX_VIOLATION_FILE = "sandbox_violation_file"        # 路径穿越违规
    SANDBOX_VIOLATION_NETWORK = "sandbox_violation_network"  # 网络违规
    SANDBOX_DEGRADED = "sandbox_degraded"                    # 沙箱降级
    SANDBOX_RESOURCE_METER_DEGRADED = "sandbox_resource_meter_degraded"  # 资源计量降级


class ContributionState(str, Enum):
    """贡献审核状态机七态（偏差修正 T153）。

    DRAFT          —— 草稿
    SUBMITTED      —— 已提交
    SECURITY_SCAN  —— 安全扫描中
    REVIEWING      —— 审核中
    APPROVED       —— 已批准
    REJECTED       —— 已拒绝（终态）
    PUBLISHED      —— 已发布（终态）
    """

    DRAFT = "draft"
    SUBMITTED = "submitted"
    SECURITY_SCAN = "security_scan"
    REVIEWING = "reviewing"
    APPROVED = "approved"
    REJECTED = "rejected"
    PUBLISHED = "published"


class ContributorType(str, Enum):
    """贡献者类型。"""

    THIRD_PARTY = "third_party"  # 第三方
    COMMUNITY = "community"      # 社区


class SsoProtocolType(str, Enum):
    """SSO 协议类型（偏差修正 T162）。"""

    SAML2 = "saml2"  # SAML 2.0
    OIDC = "oidc"    # OpenID Connect


class EvaluationType(str, Enum):
    """评估类型（偏差修正 T182）。"""

    BASELINE = "baseline"      # 基线评估
    REGRESSION = "regression"  # 回归评估


class EvaluationTaskState(str, Enum):
    """评估任务状态（偏差修正 T182）。"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"


class DescriptionSource(str, Enum):
    """语义描述来源三态（偏差修正 T192）。"""

    PROVIDER = "provider"            # 注册方提供
    LLM_GENERATED = "llm_generated"  # LLM 辅助生成
    MANUAL_REVISION = "manual_revision"  # 人工修订


class TriggerTemplateType(str, Enum):
    """触发器模板类型（第五阶段偏差修正 T223）。

    三类预置异常检测触发器模板，作为订阅的可选附加能力：
        SLIDING_WINDOW_ANOMALY     —— 滑动窗口统计异常（窗口均值偏离基线超容差）
        THRESHOLD_SUSTAINED_BREACH —— 阈值持续突破（连续 N 次突破才触发，防抖）
        VARIANCE_SPIKE             —— 方差突变（本窗方差超过前窗方差指定倍数）
    """

    SLIDING_WINDOW_ANOMALY = "sliding_window_anomaly"
    THRESHOLD_SUSTAINED_BREACH = "threshold_sustained_breach"
    VARIANCE_SPIKE = "variance_spike"
