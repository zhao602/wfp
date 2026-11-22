# =============================================================================
# 运行日志字段常量（observability/run_log/fields.py，workflow_run_logging CHG-02）
#
# 职责：集中声明运行日志的格式版本、记录类型、状态、级别、业务类别常量
#       与字段名/必填字段名集合。
# 设计意图（design 2.0.1 统一字段规范、DC-03）：
#   1. 以 record_type 区分记录族（node_run / workflow_run），两族共享公共字段；
#   2. 枚举一律为字符串常量（Python 3.10 兼容，禁用 StrEnum，INV-06）；
#   3. 字段名集合与必填集合供数据类序列化与测试校验共用。
# =============================================================================

# ---------------------------------------------------------------------------
# 格式版本锚点
# ---------------------------------------------------------------------------
SCHEMA_VERSION = "1.0"

# ---------------------------------------------------------------------------
# 记录类型（record_type）
# ---------------------------------------------------------------------------
RECORD_TYPE_NODE_RUN = "node_run"
RECORD_TYPE_WORKFLOW_RUN = "workflow_run"
RECORD_TYPES = (RECORD_TYPE_NODE_RUN, RECORD_TYPE_WORKFLOW_RUN)

# ---------------------------------------------------------------------------
# 运行状态（status）
# ---------------------------------------------------------------------------
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_TIMEOUT = "timeout"
STATUS_RUNNING = "running"
STATUS_UNKNOWN = "unknown"

# 节点级取值域；终结状态失败口径与事件 NODE_FAILED 同源（INV-D-13）
NODE_RUN_STATUSES = (STATUS_SUCCEEDED, STATUS_FAILED, STATUS_TIMEOUT)
# 工作流级取值域
WORKFLOW_RUN_STATUSES = (STATUS_SUCCEEDED, STATUS_FAILED, STATUS_RUNNING, STATUS_UNKNOWN)

# ---------------------------------------------------------------------------
# 日志级别（level，字符串常量，不得使用 enum.StrEnum）
# ---------------------------------------------------------------------------
LEVEL_DEBUG = "DEBUG"
LEVEL_INFO = "INFO"
LEVEL_WARN = "WARN"
LEVEL_ERROR = "ERROR"
LEVELS = (LEVEL_DEBUG, LEVEL_INFO, LEVEL_WARN, LEVEL_ERROR)

# ---------------------------------------------------------------------------
# 业务类别（business_category，取值对齐既有 TriggerType 值域 + 兜底，DC-06）
# ---------------------------------------------------------------------------
BUSINESS_CATEGORY_MANUAL = "manual"
BUSINESS_CATEGORY_SCHEDULED = "scheduled"
BUSINESS_CATEGORY_DATA_EVENT = "data_event"
BUSINESS_CATEGORY_DEFAULT = "default"
BUSINESS_CATEGORIES = (
    BUSINESS_CATEGORY_MANUAL,
    BUSINESS_CATEGORY_SCHEDULED,
    BUSINESS_CATEGORY_DATA_EVENT,
    BUSINESS_CATEGORY_DEFAULT,
)

# ---------------------------------------------------------------------------
# 公共字段（两族记录均含，design 2.0.1（1））
# ---------------------------------------------------------------------------
COMMON_FIELDS = (
    "schema_version",
    "record_type",
    "run_id",
    "trace_id",
    "tenant_id",
    "business_category",
    "level",
    "timestamp",
    "status",
    "started_at",
    "finished_at",
    "duration_ms",
)
# 公共字段全部必填；工作流运行中记录允许省略 finished_at / duration_ms（E-9）
COMMON_REQUIRED_FIELDS = COMMON_FIELDS

# ---------------------------------------------------------------------------
# 节点级字段（record_type="node_run"，design 2.0.1（2））
# ---------------------------------------------------------------------------
NODE_RUN_FIELDS = (
    "node_id",
    "node_type",
    "round_index",
    "input",
    "output",
    "error",
    "truncated",
    "payload_note",
    "subworkflow_depth",
)
NODE_RUN_REQUIRED_FIELDS = ("node_id", "node_type", "round_index", "input")

# 节点错误对象字段（失败/超时必填 error_type/error_code/error_message；error_stack 可得时必填）
NODE_ERROR_FIELDS = ("error_type", "error_code", "error_message", "error_stack")
NODE_ERROR_REQUIRED_FIELDS = ("error_type", "error_code", "error_message")

# ---------------------------------------------------------------------------
# 工作流级字段（record_type="workflow_run"，design 2.0.1（3））
# ---------------------------------------------------------------------------
WORKFLOW_RUN_FIELDS = (
    "workflow_id",
    "definition_version",
    "trigger_type",
    "node_summary",
)
WORKFLOW_RUN_REQUIRED_FIELDS = ("workflow_id",)

# ---------------------------------------------------------------------------
# 节点汇总指标键（node_summary，须与节点级日志计数自洽）
# ---------------------------------------------------------------------------
NODE_SUMMARY_KEYS = ("total", "succeeded", "failed", "timeout")
