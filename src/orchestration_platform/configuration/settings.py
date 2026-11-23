# =============================================================================
# 配置项类型化定义（configuration/settings.py）
#
# 职责：以 pydantic-settings 从 .env 加载全部运行期配置，并提供类型校验与默认值。
# 设计意图（对应需求规格 4.6 / 4.7）：
#   1. 配置与代码分离——任何环境参数（端口、连接串、密钥、限额）都不得硬编码在业务代码中；
#   2. 集中式类型化——所有配置项在一处声明，缺失或类型错误在启动阶段即暴露；
#   3. 密钥不入库——真实密钥仅存在于 .env（被 .gitignore 忽略），模板见 .env.example。
# =============================================================================

from functools import lru_cache
from typing import Literal, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """平台全局配置对象。

    字段分组：API 服务 / 存储后端 / 定义约束 / 引擎调度 / 适配层 /
    MCP 工具 / LLM 主备 / Agent 约束 / 数据源 / 可观测性。
    所有字段均可通过同名环境变量（大写）或 .env 文件覆盖。
    """

    # 允许从 .env 读取，环境变量名大小写不敏感，忽略未声明的额外变量
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---------------- API 服务 ----------------
    api_host: str = Field(default="0.0.0.0", description="API 监听地址")
    api_port: int = Field(default=8000, description="API 监听端口（可配置）")
    api_access_token: str = Field(default="change-me-please", description="接口访问令牌（Bearer）")

    # ---------------- 存储后端 ----------------
    storage_backend: Literal["local_file", "postgres"] = Field(
        default="local_file", description="存储后端实现选择"
    )
    local_storage_root: str = Field(default="./.data", description="本地文件存储根目录")
    postgres_host: str = Field(default="localhost", description="PostgreSQL 主机")
    postgres_port: int = Field(default=5432, description="PostgreSQL 端口")
    postgres_db: str = Field(default="orchestration_platform", description="PostgreSQL 数据库名")
    postgres_user: str = Field(default="postgres", description="PostgreSQL 用户名")
    postgres_password: str = Field(default="change-me-please", description="PostgreSQL 密码")

    # ---------------- 工作流定义约束 ----------------
    workflow_max_nodes: int = Field(default=50, description="单定义节点数量上限（第一阶段 50）")

    # ---------------- 引擎调度约束 ----------------
    node_default_timeout_seconds: int = Field(default=60, description="节点默认执行超时（秒）")
    loop_max_iterations: int = Field(default=10, description="循环最大迭代次数（防死循环）")
    checkpoint_interval_seconds: int = Field(default=30, description="长耗时节点中间检查点间隔（秒）")
    review_timeout_seconds: int = Field(default=3600, description="人工审核超时（秒）")
    instance_timeout_seconds: float = Field(default=1800, description="实例级超时（秒）；M3 看门狗接线（design D3），None 值经环境变量不可达——恒有上界")
    instance_max_concurrency: int = Field(default=20, description="实例最大并发数")

    # ---------------- 执行模式与队列（roadmap-v3 中期窗口 M1，F1 执行队列与提交路径统一） ----------------
    # execution_mode 默认 in_process：行为与历史完全等价（提交器内部仍是
    # create_task 进程内执行），3468 基线测试与本地开发零感知；
    # worker：提交器入队 execution_job，独立 worker 进程认领执行（M2 落地）。
    execution_mode: Literal["in_process", "worker"] = Field(
        default="in_process",
        description="执行模式（in_process=API 进程内执行，与历史行为完全等价；worker=入队由独立 worker 进程认领执行）",
    )
    execution_queue_lease_seconds: int = Field(
        default=30, ge=1, description="执行队列租约时长（秒）：认领即持租约，过期可被接管"
    )
    execution_queue_heartbeat_seconds: int = Field(
        default=10, ge=1, description="执行队列心跳续租间隔（秒）：worker 执行期周期续租"
    )
    execution_queue_poll_interval_seconds: float = Field(
        default=1.0, gt=0, description="执行队列轮询间隔（秒）：worker 认领循环间隔"
    )
    execution_queue_dir: str = Field(
        default="./.data/execution_queue", description="本地文件执行队列存储目录（local_file 后端）"
    )
    execution_queue_graceful_shutdown_seconds: int = Field(
        default=30,
        ge=1,
        description="worker 优雅停机宽限（秒）：SIGTERM 后停止认领，宽限期内等当前作业完成；超时停止心跳续租，租约到期由其他 worker 接管（M2）",
    )

    # ---------------- LangGraph 适配层 ----------------
    langgraph_checkpointer_mode: Literal["memory", "custom"] = Field(
        default="custom", description="检查点模式：custom 由平台仓储持久化"
    )
    langgraph_runtime_enabled: bool = Field(
        default=False,
        description=(
            "LangGraph 第二运行时开关（arch_review_gap_b4_architecture HC-07/DC-B4-03）："
            "默认 False 零变化；仅作用于容器 langgraph_engine_factory 与等价矩阵 harness，"
            "生产 start_instance 链路零改；True 且依赖缺失时降级内置引擎并告警"
        ),
    )

    # ---------------- MCP 工具服务（仅客户端侧） ----------------
    mcp_server_enabled: bool = Field(default=True, description="MCP 服务端是否启用")
    mcp_server_transport: Literal["stdio", "http", "sse"] = Field(default="http", description="MCP 传输方式")
    mcp_server_url: str = Field(default="http://localhost:9000/mcp", description="MCP HTTP 端点")
    mcp_server_command: str = Field(default="", description="MCP stdio 启动命令")
    mcp_server_timeout_seconds: int = Field(default=30, description="MCP 调用默认超时（秒）")
    mcp_connection_pool_max: int = Field(default=10, description="MCP 连接池最大连接数")
    mcp_connection_retry_max: int = Field(default=3, description="MCP 连接失败最大重试次数")
    mcp_tool_cache_ttl_seconds: int = Field(default=300, description="MCP 工具发现缓存 TTL（秒）")

    # ---------------- LLM 主模型 ----------------
    llm_primary_base_url: str = Field(default="", description="主模型服务地址")
    llm_primary_api_key: str = Field(default="", description="主模型密钥")
    llm_primary_model: str = Field(default="", description="主模型标识")
    llm_primary_timeout_seconds: int = Field(default=60, description="主模型超时（秒）")

    # ---------------- LLM 备用模型 ----------------
    llm_fallback_base_url: str = Field(default="", description="备用模型服务地址")
    llm_fallback_api_key: str = Field(default="", description="备用模型密钥")
    llm_fallback_model: str = Field(default="", description="备用模型标识")
    llm_fallback_timeout_seconds: int = Field(default=60, description="备用模型超时（秒）")

    # ---------------- LLM 推理重试（arch_review_gap_b2_observability_perf B2-C4） ----------------
    # 开关默认 False（GK-11）：关闭时 infer 行为与现状逐字节等价
    llm_retry_enabled: bool = Field(
        default=False, description="LLM 推理幂等失败自动重试开关（仅超时/连接失败重试）"
    )
    llm_retry_max_attempts: int = Field(
        default=3, description="LLM 推理重试总尝试次数上限（含首次）"
    )
    llm_retry_backoff_base_seconds: float = Field(
        default=0.5, description="LLM 推理重试指数退避基数（秒，退避=base*2^n+jitter）"
    )

    # ---------------- Agent 节点约束 ----------------
    agent_max_tool_rounds: int = Field(default=5, description="单次执行内工具调用最大轮次")
    agent_output_max_retries: int = Field(default=2, description="输出结构不合规时最大重生成次数")

    # ---------------- Agent 市场与 Tool 市场（agent_tool_marketplace T11.1，仅追加） ----------------
    # 开关默认 False：关闭时不注册市场路由，访问返回 404（INV-D05/INV-12/DC-09）
    agent_market_enabled: bool = Field(
        default=False, description="是否启用 Agent 市场路由（关闭时不注册，访问返回 404）"
    )
    agent_market_storage_root: str = Field(
        default="./.data/agent_market", description="Agent 市场条目本地文件存储根目录"
    )
    tool_market_enabled: bool = Field(
        default=False, description="是否启用 Tool 市场路由（关闭时不注册，访问返回 404）"
    )
    tool_market_storage_root: str = Field(
        default="./.data/tool_market", description="Tool 市场条目本地文件存储根目录"
    )

    # ---------------- Agent 市场用户自主开发（agent_market_user_development T4.1，仅追加） ----------------
    # 开关默认 False：关闭时不注册用户自主开发路由，访问返回 404（OP-9/DC-07/INV-DEV-10）
    agent_market_dev_enabled: bool = Field(
        default=False,
        description="是否启用 Agent 市场用户自主开发路由（关闭时不注册，访问返回 404）",
    )
    agent_market_dev_storage_root: str = Field(
        default="./.data/agent_market_dev", description="用户开发提交本地文件存储根目录"
    )
    agent_market_dev_max_package_mb: int = Field(
        default=10, description="单个提交包大小上限（MB）"
    )
    agent_market_dev_max_files: int = Field(
        default=200, description="单个提交包文件数上限"
    )
    agent_market_dev_max_file_kb: int = Field(
        default=2048, description="提交包内单文件大小上限（KB）"
    )
    agent_market_dev_max_manifest_kb: int = Field(
        default=64, description="manifest.yaml 大小上限（KB）"
    )
    agent_market_dev_max_unpacked_mb: int = Field(
        default=40, description="解压后总大小上限（MB）"
    )

    # ---------------- 数据源 ----------------
    data_source_connection_string: str = Field(default="", description="数据源只读连接串")

    # ---------------- 可观测性 ----------------
    log_level: str = Field(default="INFO", description="日志级别")
    # 运行日志（workflow_run_logging，design 2.2.3）
    log_dir: str = Field(default="./logs", description="运行日志根目录")
    log_rotation_enabled: bool = Field(default=True, description="是否启用运行日志轮转")
    log_rotation_when: Literal["midnight", "hourly", "daily", "minutely", "secondly"] = Field(
        default="midnight", description="运行日志按时间轮转的触发时点"
    )
    log_rotation_max_bytes: int = Field(
        default=52428800, ge=1048576, description="运行日志单文件大小上限（字节，≥1MB）"
    )
    log_retention_days: int = Field(default=14, ge=0, description="运行日志保留期（天，0=不清理）")
    log_max_payload_bytes: int = Field(default=65536, ge=1024, description="运行日志单字段规模上限（字节）")
    log_redaction_enabled: bool = Field(
        default=True, description="运行日志脱敏开关（关闭时仍保留字段名黑名单兜底，红线不可关）"
    )
    log_async_enabled: bool = Field(default=True, description="运行日志异步队列开关")
    log_queue_max_size: int = Field(default=10000, ge=1, description="运行日志异步队列上限")
    log_tenant_default: str = Field(default="default", description="运行日志租户缺失兜底目录名")

    # ---------------- 第二阶段新增配置 ----------------
    # 引擎增强
    parallel_branch_max: int = Field(default=10, ge=1, description="并行分支最大并行度")
    parallel_default_strategy: Literal["fast_fail", "wait_all"] = Field(
        default="fast_fail", description="并行默认失败策略"
    )
    subworkflow_max_depth: int = Field(default=5, ge=1, description="子工作流最大嵌套深度")
    dynamic_injection_max_count: int = Field(default=100, ge=1, description="动态注入最大数量")
    scheduler_batch_mode: bool = Field(default=True, description="是否启用调度器批处理")
    scheduler_batch_max_nodes: int = Field(default=20, ge=1, description="批处理单批最大节点数")
    checkpoint_compression_enabled: bool = Field(default=True, description="是否启用检查点压缩")
    checkpoint_compression_target_ratio: float = Field(
        default=0.6, ge=0.0, le=1.0, description="检查点压缩目标比率"
    )

    # 兼容性：保留可选字段以支持未来扩展而不破坏现有 .env
    extra_placeholder: Optional[str] = Field(default=None, description="预留扩展占位字段")

    # ---------------- 第三阶段治理配置 ----------------
    # RBAC
    rbac_enabled: bool = Field(default=True, description="是否启用 RBAC 鉴权")
    rbac_permission_cache_ttl_seconds: int = Field(default=300, ge=1, description="权限缓存 TTL（秒）")
    # 租户
    tenant_default_quota_max_instances: int = Field(default=100, ge=0, description="默认租户最大实例数")
    tenant_default_quota_max_workflows: int = Field(default=1000, ge=0, description="默认租户最大工作流数")
    tenant_isolation_enabled: bool = Field(default=True, description="是否启用租户隔离")
    default_tenant_id: str = Field(default="tenant_default", description="默认租户标识（业务仓储 tenant_id 缺省兜底，与 v1_2 列默认值/种子租户一致，roadmap B1）")
    # 令牌
    token_default_ttl_seconds: int = Field(default=3600, ge=1, description="令牌默认有效期（秒）")
    token_signing_secret: str = Field(default="change-me-token-secretF", description="令牌签名密钥")
    # 计量
    metering_enabled: bool = Field(default=True, description="是否启用 Token 计量")
    metering_buffer_queue_max_size: int = Field(default=10000, ge=1, description="计量缓冲队列最大长度")
    metering_buffer_persist_path: str = Field(default="./.data/usage_buffer", description="计量缓冲持久化路径")
    # 计费
    billing_cost_precision: int = Field(default=6, ge=1, le=10, description="成本金额精度（小数位数）")
    # 预算
    budget_circuit_break_latency_seconds: int = Field(default=5, ge=1, description="预算熔断检测延迟（秒）")
    budget_fail_closed: bool = Field(default=True, description="预算超限时是否拒绝（True）或仅告警（False）")
    # 告警
    alert_channel_type: str = Field(default="webhook", description="告警通道类型：webhook/email/log")
    alert_webhook_url: str = Field(default="", description="告警 Webhook URL")
    # 审计
    audit_enabled: bool = Field(default=True, description="是否启用审计日志")
    audit_write_fail_strategy: Literal["rollback", "skip"] = Field(default="rollback", description="审计写入失败策略")
    audit_hash_algorithm: str = Field(default="sha256", description="审计哈希链算法")
    audit_export_max_records: int = Field(default=100000, ge=1, description="审计导出最大记录数")
    audit_export_format: Literal["json", "csv"] = Field(default="json", description="审计导出格式")
    audit_mask_default_strategy: str = Field(default="full_mask", description="默认脱敏策略")
    # 第五阶段偏差修正（G5/T208）：字段级内容哈希开关，默认关闭保持旧行为
    audit_content_hash_enabled: bool = Field(default=False, description="是否启用审计条目字段级内容哈希（input_hash/output_hash）")
    # 第五阶段偏差修正（G2/T213）：结构化无损裁剪策略开关，默认关闭走既有兜底
    structural_lossless_pruning_enabled: bool = Field(default=False, description="是否启用结构化无损裁剪策略（关闭时走既有滑动窗口兜底）")
    # 第五阶段偏差修正（G1/T221）：监督者编排器配置，默认关闭保持既有行为
    supervisor_orchestration_enabled: bool = Field(default=False, description="是否启用监督者编排器（关闭时 SUPERVISOR 节点类型按未知拦截）")
    supervisor_orchestration_state_backend: Literal["memory", "local_file", "postgres"] = Field(default="memory", description="编排状态存储后端")
    supervisor_orchestration_max_sub_orchestrators: int = Field(default=20, ge=1, description="单编排子编排器数量上限")
    supervisor_sub_task_retry_max: int = Field(default=2, ge=0, description="子任务失败最大重试次数")
    # 第五阶段偏差修正（G6/T225）：触发器模板开关，默认关闭主循环不评估模板
    trigger_template_enabled: bool = Field(default=False, description="是否启用触发器模板（关闭时模板清单路由不注册、主循环不评估模板）")
    # 第五阶段偏差修正（G3/T229）：Agent 绩效开关，默认关闭不订阅不记录
    agent_performance_enabled: bool = Field(default=False, description="是否启用 Agent 绩效记录与查询（关闭时记录器不挂载、路由不注册）")
    agent_performance_memory_window_size: int = Field(default=10000, ge=1, description="内存绩效仓储环形窗口容量（先进先出淘汰）")
    # 全局调度
    global_scheduler_enabled: bool = Field(default=True, description="是否启用全局调度器")
    global_scheduler_decision_timeout_ms: int = Field(default=100, ge=1, description="调度决策超时（毫秒）")
    scheduler_queue_max_size: int = Field(default=10000, ge=1, description="调度队列最大长度")
    scheduler_fair_weight_default: int = Field(default=1, ge=1, description="公平调度默认权重")
    scheduler_preemption_enabled: bool = Field(default=True, description="是否启用抢占调度")
    scheduler_preemption_max_count: int = Field(default=3, ge=0, description="单实例最大被抢占次数")
    # 资源池
    resource_pool_concurrency_slot_quota: int = Field(default=100, ge=1, description="并发槽位配额")
    resource_pool_oversubscription_ratio: float = Field(default=1.0, ge=1.0, description="资源超售比")
    resource_estimate_default_slots: int = Field(default=1, ge=1, description="默认资源需求槽位")

    # ---------------- 第三阶段偏差修正配置 ----------------
    # 自然语言编排
    nl_orchestration_enabled: bool = Field(default=False, description="是否启用自然语言编排入口")
    nl_orchestration_max_input_length: int = Field(default=4000, ge=1, description="NL 指令最大输入长度（字符）")
    nl_orchestration_timeout_seconds: int = Field(default=30, ge=1, description="NL 解析超时（秒）")
    nl_orchestration_model: str = Field(default="", description="NL 编排使用的 LLM 模型标识")
    nl_orchestration_max_correction_rounds: int = Field(default=2, ge=0, description="拓扑校验失败后 LLM 自纠错最大轮次")

    # ABAC 权限扩展
    abac_enabled: bool = Field(default=False, description="是否启用 ABAC 策略引擎")
    abac_decision_timeout_ms: int = Field(default=50, ge=1, description="ABAC 单次决策超时（毫秒）")
    abac_default_fail_mode: Literal["deny", "allow"] = Field(default="deny", description="属性求值失败默认模式")

    # 墓碑与被遗忘权
    tombstone_enabled: bool = Field(default=True, description="是否启用墓碑删除（关闭则审计删除走物理删除）")
    tombstone_batch_size: int = Field(default=1000, ge=1, description="被遗忘权批量标记每批条数")

    # Redis 缓存
    redis_enabled: bool = Field(default=False, description="是否启用 Redis 缓存")
    redis_url: str = Field(default="", description="Redis 连接地址（空则降级内存缓存）")
    redis_permission_cache_ttl: int = Field(default=300, ge=1, description="权限缓存 TTL（秒）")
    redis_session_cache_ttl: int = Field(default=1800, ge=1, description="会话缓存 TTL（秒）")
    redis_lock_default_ttl: int = Field(default=30, ge=1, description="分布式锁默认过期时间（秒）")
    redis_rate_limit_window: int = Field(default=60, ge=1, description="限流计数窗口（秒）")

    # Kafka 消息中间件
    kafka_enabled: bool = Field(default=False, description="是否启用 Kafka 事件总线")
    kafka_bootstrap_servers: str = Field(default="", description="Kafka 引导服务器地址（空则降级内存总线）")
    kafka_workflow_topic: str = Field(default="workflow_events", description="工作流执行事件 topic")
    kafka_session_topic: str = Field(default="session_events", description="会话状态变更事件 topic")
    kafka_consumer_group: str = Field(default="orchestration_platform", description="Kafka 消费者组标识")
    kafka_replay_max_events: int = Field(default=10000, ge=1, description="单次重放最大事件数")

    # 事件总线与审计订阅（arch_review_v2_tasks_wave2 G1，CHG-59/DC-21/DC-22）
    # 三开关默认 False：关闭时发布旁路、订阅不装配，基线零行为变化。
    event_bus_publish_enabled: bool = Field(default=False, description="是否启用事件总线领域事件发布")
    event_bus_memory_history_max: int = Field(default=10000, ge=1, description="内存事件总线保留历史条数上限")
    audit_event_subscription_enabled: bool = Field(default=False, description="是否启用审计事件订阅落盘")

    # Cron 定时触发（arch_review_v2_tasks_wave2 G3，CHG-59/DC-28/DC-29）
    # 开关/参数默认关闭：扫描器不启动、管理路由不注册，基线零行为变化。
    schedule_scanner_enabled: bool = Field(default=False, description="是否启用 Cron 定时触发扫描器")
    schedule_scanner_interval_seconds: int = Field(default=30, ge=1, description="定时触发扫描间隔（秒）")
    trigger_schedule_api_enabled: bool = Field(default=False, description="是否注册定时触发器管理路由")

    # 事件出口（roadmap B3，契约倒置 + 内存态 DLQ；双开关默认 False：
    # 关闭时零装配、路由不注册 404，基线零行为变化）
    event_export_enabled: bool = Field(default=False, description="是否启用事件出口 Webhook 投递装配")
    event_export_api_enabled: bool = Field(default=False, description="是否注册事件出口管理路由（订阅/死信/SSE）")
    event_export_webhook_max_attempts: int = Field(default=5, ge=1, description="单事件 Webhook 最大投递尝试次数")
    event_export_webhook_backoff_base_seconds: float = Field(default=1.0, gt=0, description="投递指数退避基数（秒）")
    event_export_webhook_backoff_cap_seconds: float = Field(default=60.0, gt=0, description="投递退避上限（秒）")
    event_export_webhook_timeout_seconds: float = Field(default=5.0, gt=0, description="单次 Webhook 请求超时（秒）")
    event_export_delivery_history_max: int = Field(default=1000, ge=1, description="每订阅投递记录环形上限")
    event_export_dlq_max_entries: int = Field(default=1000, ge=1, description="死信总量上限（超限丢最旧）")
    event_export_subscriptions_persist_path: str = Field(default="./.data/event_export/subscriptions.json", description="事件出口订阅持久化路径（B7 W3；变更后原子落盘、启动期恢复）")

    # Agent 执行沙箱（偏差修正 T131）
    sandbox_enabled: bool = Field(default=False, description="是否启用 Agent 执行沙箱")
    sandbox_default_isolation: str = Field(default="logical", description="默认隔离模式：logical/physical")
    sandbox_execution_timeout_seconds: int = Field(default=60, ge=1, description="沙箱默认执行超时（秒）")
    sandbox_default_cpu_seconds: float = Field(default=10.0, gt=0, description="默认 CPU 时间上限（秒）")
    sandbox_default_memory_mb: int = Field(default=512, gt=0, description="默认内存上限（MB）")
    sandbox_default_output_bytes: int = Field(default=1048576, gt=0, description="默认输出尺寸上限（字节）")
    sandbox_workdir_root: str = Field(default="./.data/sandbox/workdirs", description="沙箱工作目录根")
    sandbox_network_default_deny: bool = Field(default=True, description="网络默认拒绝")
    sandbox_subprocess_mode: bool = Field(default=False, description="是否启用子进程隔离模式")
    sandbox_container_endpoint: str = Field(default="", description="容器运行时端点（空则降级逻辑隔离）")
    sandbox_container_timeout_seconds: int = Field(default=120, ge=1, description="容器执行超时（秒）")
    sandbox_violation_topic: str = Field(default="sandbox_violations", description="违规事件 topic")

    # 大模型节点与代码节点（llm_code_nodes_v2 CHG-02 / design §4.4）
    # 双开关默认 False：关闭时执行器返回 LLM_NODE_DISABLED / CODE_NODE_DISABLED
    llm_node_enabled: bool = Field(default=False, description="是否启用大模型节点（默认 False：关闭时返回 LLM_NODE_DISABLED）")
    code_node_enabled: bool = Field(default=False, description="是否启用代码节点（默认 False：关闭时返回 CODE_NODE_DISABLED）")
    code_execution_timeout_seconds: int = Field(default=10, ge=1, description="代码节点受限执行超时（秒；与沙箱策略超时取较小值）")
    code_output_max_bytes: int = Field(default=1048576, gt=0, description="代码节点输出序列化字节数上限（与沙箱策略输出上限取较小值）")
    code_execution_channel: str = Field(default="inprocess", description="代码节点执行通道（inprocess=受限进程内执行；subprocess=子进程；非法值由执行器降级 inprocess 并告警）")

    # 插件/节点 SDK（偏差修正 T149）
    plugin_sdk_enabled: bool = Field(default=False, description="是否启用插件节点 SDK")
    plugin_sdk_max_nodes: int = Field(default=200, ge=1, description="平台最大插件节点数")
    plugin_sdk_setup_timeout_seconds: int = Field(default=30, ge=1, description="setup 阶段超时（秒）")
    plugin_sdk_packages_root: str = Field(default="./.data/plugin_packages", description="插件节点包安装根目录")

    # 节点市场第三方贡献（偏差修正 T153）
    node_contribution_enabled: bool = Field(default=False, description="是否启用节点市场第三方贡献")
    node_contribution_signature_algorithm: str = Field(default="hmac-sha256", description="签名算法（hmac-sha256 或 rsa-sha256）")
    node_contribution_signature_secret: str = Field(default="", description="签名密钥（环境变量注入，空则拒绝提交）")
    node_contribution_scan_timeout_seconds: int = Field(default=120, ge=1, description="静态安全扫描超时（秒）")
    node_contribution_storage_root: str = Field(default="./.data/contributions", description="贡献存储根目录")

    # SSO 企业身份集成（偏差修正 T162）
    sso_enabled: bool = Field(default=False, description="是否启用 SSO 企业身份集成")
    sso_session_ttl_seconds: int = Field(default=3600, ge=1, description="SSO 会话 TTL（秒）")
    sso_assertion_replay_cache_ttl: int = Field(default=300, ge=1, description="断言防重放缓存 TTL（秒）")
    sso_default_role_on_missing_attr: str = Field(default="", description="属性缺失时缺省角色（空则拒绝开通）")
    credential_encryption_key: str = Field(default="", description="凭据加密密钥（空则拒绝持久化凭据）")

    # 企业连接器（偏差修正 T174）
    enterprise_connector_enabled: bool = Field(default=False, description="是否启用企业连接器")
    enterprise_connector_default_timeout_seconds: int = Field(default=30, ge=1, description="连接器默认超时（秒）")
    enterprise_connector_test_timeout_seconds: int = Field(default=8, ge=1, description="连接测试探活超时（秒）")

    # Agent 效能评估框架（偏差修正 T182）
    evaluation_enabled: bool = Field(default=False, description="是否启用评估框架")
    evaluation_llm_scoring_enabled: bool = Field(default=False, description="是否启用 LLM 评分（默认纯规则）")
    evaluation_random_seed: int = Field(default=42, description="固定随机种子（可复现）")
    evaluation_llm_temperature: float = Field(default=0.0, description="LLM 评分温度")
    evaluation_regression_degrade_threshold: float = Field(default=0.05, description="回归退化告警阈值")
    evaluation_max_scenarios: int = Field(default=500, ge=1, description="场景集条目上限")

    # 语义索引 Agent 发现（偏差修正 T192）
    agent_discovery_enabled: bool = Field(default=False, description="是否启用语义索引 Agent 发现")
    agent_discovery_vector_backend: str = Field(default="", description="向量索引后端（空则纯关键词）")
    agent_discovery_vector_endpoint: str = Field(default="", description="向量索引端点")
    agent_discovery_vector_timeout_ms: int = Field(default=300, ge=1, description="向量索引超时（毫秒）")
    agent_discovery_relevance_threshold: float = Field(default=0.3, description="相关度阈值")
    agent_discovery_result_limit: int = Field(default=10, ge=1, description="结果数量上限")
    agent_discovery_min_query_length: int = Field(default=4, ge=1, description="最短查询长度")
    agent_discovery_desc_max_length: int = Field(default=2000, ge=1, description="描述最大长度")

    # 向量知识库节点（vector_knowledge_base_node CHG-09，design §6.4.5 / FR-18）
    vector_kb_search_base_url: str = Field(default="", description="向量检索服务地址（api 模式 embedding/search/rerank 共用；空则 API 检索不可用）")
    vector_kb_search_timeout_seconds: int = Field(default=60, ge=1, description="向量检索服务超时（秒）")
    vector_kb_search_api_key: str = Field(default="", description="向量检索服务访问密钥（Bearer；仅 .env 注入，禁止写入工作流定义或日志）")

    # 本地向量数据库（local_vector_database，CHG-L16 / design DC-07）
    # 独立开关默认 False：关闭时 local_backend 委托分支不触达本组件（INV-L05）
    local_vector_database_enabled: bool = Field(default=False, description="是否启用本地向量数据库（默认 False：关闭时既有向量知识库节点行为完全不变）")
    local_vector_database_storage_root: str = Field(default="./.data/vector_kb", description="本地向量数据库存储根目录（一集合一文件 sqlite）")
    local_vector_database_default_dimension: int = Field(default=128, ge=1, le=4096, description="默认向量维度")
    local_vector_database_default_metric: Literal["cosine", "dot", "euclidean"] = Field(default="cosine", description="默认相似度度量（cosine / dot / euclidean）")
    local_vector_database_backend: str = Field(default="", description="增强检索后端标识（预留；空则暴力精确检索）")
    local_vector_database_query_timeout_ms: int = Field(default=1000, ge=1, description="查询超时（毫秒）")
    local_vector_database_max_records_per_collection: int = Field(default=100000, ge=1, description="单集合最大记录数")
    local_vector_database_max_top_k: int = Field(default=100, ge=1, le=1000, description="单次检索 Top-K 上限")
    # 向量诚实化（arch_review_gap_b2 B2-C5 / DC-B2-05，仅追加）
    # 独立开关默认 False：关闭时写入行为与现状逐字节等价（HC-07/GK-11）
    vector_real_embedding_enabled: bool = Field(default=False, description="是否强制真实 embedding 写入（默认 False：关闭时写入行为与现状逐字节等价；开启时仅 hash_fallback 可用的新写入以可辨识错误拒绝，不伪造语义）")
    local_vector_database_storage: Literal["sqlite", "pg_array"] = Field(default="sqlite", description="本地向量数据库存储路径（默认 sqlite：存量路径零变化；pg_array 为新建集合的可选 PostgreSQL 原生数组路径，零新增依赖）")

    # 向量知识库管理（vector_kb_management，design DC-10 / CHG-10）
    # 独立开关默认 False：关闭时管理路由不注册（/api/v1/kb-management/** 404），
    # 不触达 local_vector_database、不创建落点目录（INV-D23）
    vector_kb_management_enabled: bool = Field(default=False, description="是否启用向量知识库管理（默认 False：关闭时 /api/v1/kb-management/** 返回 404，既有行为完全不变）")
    vector_kb_management_storage_root: str = Field(default="./.data/vector_kb_management", description="知识库元数据存储根目录（独立 sqlite3 落点）")
    vector_kb_management_max_file_size_mb: int = Field(default=10, ge=1, description="单文件大小上界（MB，超限确定性拒绝）")
    vector_kb_management_max_chunks_per_kb: int = Field(default=100000, ge=1, description="单知识库分片总量上界（与内核条目上限对齐）")
    vector_kb_management_default_chunk_length: int = Field(default=500, ge=50, le=2000, description="默认分片长度上限（50..2000）")
    vector_kb_management_default_overlap: int = Field(default=50, ge=0, description="默认相邻分片重叠长度（必须 < max_chunk_length）")
    vector_kb_management_default_similarity_threshold: float = Field(default=0.75, ge=0.0, le=1.0, description="语义分块默认相似度阈值（仅 semantic）")

    # 向量知识库写入模式（vkb_write_mode，design §2.1.2 / OP-10）
    # 仅约束写入模式待写入文本长度上界；**不新增独立节点开关**（OP-10 / P-4）：
    # 写入生效依赖既有 vector_kb_management_enabled 与 local_vector_database_enabled
    vector_kb_write_max_text_length: int = Field(default=500000, ge=1, description="写入模式待写入文本长度上界（字符数；仅约束写入，默认不改变既有检索/加载行为）")

    # 文档标注能力（vkb_doc_annotation，design §2.3.1 / CHG-12 / DC-02）
    # 独立开关默认 False（INV-07）：未启用 → 标注路由不注册（404）、不产 annotations
    vector_kb_annotation_enabled: bool = Field(default=False, description="是否启用向量知识库文档标注能力（默认 False：关闭时标注端点 404、既有行为零变化）")
    vector_kb_annotation_max_items: int = Field(default=32, ge=0, description="自定义标注项数上界（默认 32）")
    vector_kb_annotation_max_value_length: int = Field(default=512, ge=0, description="自定义标注值长度上界（默认 512 字符）")

    # 数据转换自定义脚本（expand_data_transform_ops CHG-B17 / OP-10）
    # 仅控制脚本能力录入与静态校验入口；无论开关状态，运行期均不执行用户脚本
    data_transform_custom_code_enabled: bool = Field(
        default=False,
        description="是否启用数据转换自定义脚本能力（仅登记与静态校验，运行期不执行；默认 False）",
    )

    # ---------------- 运行环境与安全强化（arch_review_v2_tasks G1/G2/G3，仅追加） ----------------
    # environment：production 触发弱密钥 fail-fast 拒启动（startup_checks，DC-05/卡 A5）
    environment: Literal["development", "production"] = Field(
        default="development",
        description="运行环境（production 时弱密钥 fail-fast 拒启动；默认 development 仅告警不阻断）",
    )
    # token_verify_mode 三态（DC-03）：both=JWT 优先 + 明文回退（默认，存量令牌零失效）；
    # jwt=仅 JWT（明文令牌拒 AUTH_TOKEN_FORMAT_UNSUPPORTED）；legacy=仅明文
    # 生产强制（roadmap B0/D2）：production 下非 jwt 值由 startup_checks fail-fast 拒启动
    token_verify_mode: Literal["legacy", "jwt", "both"] = Field(
        default="both",
        description="令牌校验模式（both 默认：存量明文令牌继续可用，GK-02；production 下仅允许 jwt，非 jwt 值由启动强校验拒绝启动）",
    )
    token_audience: str = Field(
        default="orchestration-platform-api", description="JWT 受众校验值（aud）"
    )
    token_issuer: str = Field(
        default="orchestration-platform", description="JWT 签发方校验值（iss）"
    )
    token_clock_skew_seconds: int = Field(
        default=60, ge=0, description="JWT exp 校验时钟偏移容忍（秒）"
    )
    # JWT 签发端点开关（CHG-13）：关闭时路由不注册（POST /api/v1/auth/token 404）
    auth_token_issuance_enabled: bool = Field(
        default=False,
        description="是否启用 JWT 签发端点（默认 False：关闭时不注册，端点 404）",
    )
    # RBAC bootstrap 开关（CHG-14）：启动期幂等初始化默认不执行
    rbac_bootstrap_enabled: bool = Field(
        default=False,
        description="是否启用 RBAC 启动期幂等初始化（默认租户/内置角色/初始管理员绑定；默认 False 不执行）",
    )
    rbac_bootstrap_admin_user: str = Field(
        default="", description="bootstrap 初始管理员用户标识（空则不创建初始绑定）"
    )
    # ---- G6 实例级步数护栏（arch_review_v2 3.6，CHG-01/04/08；卡 A2；DC-10）----
    workflow_max_steps: int = Field(
        default=500,
        ge=1,
        description="实例级最大执行步数（轮次全局限额；超出抛 WF_BUDGET_EXCEEDED）",
    )
    workflow_step_guard_enabled: bool = Field(
        default=False,
        description="是否启用实例级步数护栏（默认 False：关闭时行为与基线完全一致，HC-07）",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """获取全局唯一配置实例（带缓存）。

    使用 lru_cache 保证进程内配置只加载一次，避免重复解析 .env 与重复校验。
    """
    return Settings()
