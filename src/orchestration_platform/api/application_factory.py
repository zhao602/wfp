# =============================================================================
# FastAPI 应用工厂（api/application_factory.py）
#
# 职责：装配 FastAPI 应用——路由、中间件、异常处理器、依赖注入容器。
# 设计意图（对应 spec 4.6、design 2.5.6）：
#   1. 仓储、引擎、客户端实现由 repository_factory 按配置选择；
#   2. 统一异常映射：DomainError → {code, message, details} + HTTP 状态码。
# =============================================================================

import logging
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from orchestration_platform.api.di import Container
from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.configuration.startup_checks import run_startup_checks
from orchestration_platform.domain.errors import (
    DomainError,
    InstanceStateConflictError,
    ResourceNotFoundError,
    TenantQuotaExceededError,
    WorkflowValidationError,
)
from orchestration_platform.governance.metering.usage_recorder import (
    get_usage_recorder,
)
from orchestration_platform.observability.metrics_collector import MetricsCollector
from orchestration_platform.application.engine_wiring import build_engine_settings
from orchestration_platform.application.schedule.models import TriggerSchedule
from orchestration_platform.application.schedule.schedule_scanner import (
    ScheduleFireCallback,
    ScheduleFireOutcome,
)
from orchestration_platform.engine.abstractions.domain_event_publisher import (
    IDomainEventPublisher,
)
from orchestration_platform.persistence.abstractions.repositories import (
    IWorkflowRepository,
)


def build_cron_fire_callback(
    workflow_repository: IWorkflowRepository,
    domain_event_publisher: Optional[IDomainEventPublisher] = None,
) -> ScheduleFireCallback:
    """构造 Cron 到期 fire 回调：经既有实例启动链路启动（与手动触发同源）。

    arch_review_v2_tasks_wave2 G3（CHG-59/CHG-66，DC-28 规则 5，design §3.3）：
    到期 → 定义仓储重取 → 编译 → run_and_record(trigger_source="cron")
    （发布点 P1 instance.started 由 run_and_record 既有链路发布；
    发布点 P8 instance.triggered/instance.created 由扫描器发布）。
    启动失败返回 None（扫描器告警不重发——一次且仅一次）；
    局部 import 防循环依赖（execution_instance_router 亦被本模块导入）。
    """
    import logging
    import uuid

    from orchestration_platform.api.routers.execution_instance_router import (
        InstanceRuntime,
        NodeEventCallbackImpl,
        _acquire_checkpoint_repository,
        _execution_submitter,
        _executor_registry,
        _instance_registry,
    )
    from orchestration_platform.domain.models.enums import NodeType
    from orchestration_platform.domain.models.shared_state import SharedState
    from orchestration_platform.engine.graph_compiler import GraphCompiler
    from orchestration_platform.engine.scheduler import Scheduler
    from orchestration_platform.observability.run_log.fields import (
        BUSINESS_CATEGORY_DEFAULT,
    )
    from orchestration_platform.observability.run_log.recorder import WorkflowRunLogger

    logger = logging.getLogger(__name__)

    async def _fire(
        schedule: TriggerSchedule, fire_at: datetime
    ) -> Optional[ScheduleFireOutcome]:
        """到期启动单次工作流实例（既有启动链路，trigger_source="cron"）。"""
        instance_id = str(uuid.uuid4())
        trace_id = str(uuid.uuid4())
        rt = InstanceRuntime(
            instance_id=instance_id,
            definition_id=schedule.workflow_id,
        )
        # M5 陈旧注册表消除（design D2）：worker 模式不注册——API 进程
        # 注册表保持空（读穿透口径）；rt 仍传 submit（worker 分支读
        # 租户/定义标识）。in_process 注册语义零变化。
        if get_settings().execution_mode != "worker":
            _instance_registry.register(rt)
        try:
            definition = await workflow_repository.get(schedule.workflow_id)
            compiled = await GraphCompiler(
                settings=build_engine_settings()
            ).compile(definition)
            rt.compiled = compiled
            rt.trace_id = trace_id
        except Exception:
            _instance_registry.remove(instance_id)
            logger.warning(
                "Cron 触发定义获取/编译失败（不重发）：%s",
                schedule.schedule_id,
                exc_info=True,
            )
            return None
        # 业务类别两级回退（start_instance 同语义，减请求级 trigger_type 一级）：
        # TRIGGER 节点配置 trigger_type → default
        business_category = BUSINESS_CATEGORY_DEFAULT
        for node in getattr(definition, "nodes", None) or []:
            if getattr(node, "node_type", None) == NodeType.TRIGGER:
                config = getattr(node, "configuration", None)
                trigger_type = getattr(config, "trigger_type", None)
                if trigger_type is not None:
                    business_category = str(
                        getattr(trigger_type, "value", trigger_type)
                    )
                    break
        rt.business_category = business_category
        # 运行日志采集门面（构造失败仅告警，本次运行零采集，INV-01）
        run_log_recorder = None
        try:
            run_log_recorder = WorkflowRunLogger(
                run_id=instance_id,
                trace_id=trace_id,
                tenant_id=None,
                business_category=business_category,
                workflow_id=schedule.workflow_id,
                definition_version=getattr(definition, "definition_version", None),
            )
        except Exception:
            logger.warning(
                "Cron 触发运行日志采集器构造失败，本次运行不采集: instance_id=%s",
                instance_id,
                exc_info=True,
            )
            run_log_recorder = None
        scheduler = Scheduler(
            executor_registry=_executor_registry,
            checkpoint_repository=_acquire_checkpoint_repository(),
            event_callback=NodeEventCallbackImpl(_instance_registry),
            run_log_recorder=run_log_recorder,
            domain_event_publisher=domain_event_publisher,
            settings=build_engine_settings(),
            usage_recorder=get_usage_recorder(),
        )
        initial_state = SharedState(instance_id=instance_id, node_outputs={})
        # M1 spawn 收口：统一提交器提交（in_process 默认 =
        # create_task(run_and_record(...)) 等价路径，rt.task 由提交器落值；
        # worker 模式入队由 worker 认领，M2 落地）
        await _execution_submitter.submit(
            scheduler,
            compiled,
            initial_state,
            instance_id,
            trace_id,
            _instance_registry,
            run_log_recorder=run_log_recorder,
            trigger_source="cron",
            rt=rt,
        )
        logger.info(
            "Cron 触发启动实例：%s -> instance=%s fire_at=%s",
            schedule.schedule_id,
            instance_id,
            fire_at.isoformat(),
        )
        return ScheduleFireOutcome(instance_id=instance_id, trace_id=trace_id)

    return _fire


def create_app() -> FastAPI:
    """创建并装配 FastAPI 应用实例。"""
    app = FastAPI(
        title="工作流编排平台 API",
        version="1.0.0",
        docs_url="/docs",
    )

    settings = get_settings()

    # ---------------- DI 容器装配（arch_review_gap_b4_architecture B4-C2，DC-06/DC-B4-01） ----------------
    # 手工轻量容器：仅启动期装配一次；不引 DI 框架（HC-06）；provider 惰性构造单例
    # 记忆化；装配完成后 freeze 转只读（create_app 末段）；等价矩阵 harness（B4-C1）
    # 经本容器取得生产同源执行器注册表与双引擎工厂（G16→G15 弱依赖兑现）。
    container = Container()

    def _llm_client_provider():
        # 延迟导入（HC-06）：与 execution_instance_router._build_llm_client 同源工厂
        from orchestration_platform.integrations.llm.factory import build_llm_client

        return build_llm_client()

    def _executor_registry_provider():
        # 生产同源注册表：返回路由模块级单例（同一实例身份，INV-B4-01）
        from orchestration_platform.api.routers import execution_instance_router

        return execution_instance_router._executor_registry

    def _workflow_repository_provider():
        # 「配置选择实现」沿用 repository_factory 判定口径（DC-06 裁决 4）
        from orchestration_platform.persistence.repository_factory import RepositoryFactory

        return RepositoryFactory.create_workflow_repository()

    def _governance_chain_provider():
        # 治理链延迟构造：resolve 时点 = 治理中间件装配段（与迁移前调用时点一致）
        from orchestration_platform.governance.governance_factory import (
            create_governance_chain,
        )

        return create_governance_chain()

    def _builtin_engine_factory_provider():
        # 内置引擎工厂（harness 用）：调用即构造 Scheduler，生产路径零改（INV-B4-05）
        def _make_builtin_engine():
            from orchestration_platform.engine.scheduler import Scheduler

            return Scheduler(
                executor_registry=container.resolve("executor_registry"),
                settings=build_engine_settings(),
                usage_recorder=get_usage_recorder(),
            )

        return _make_builtin_engine

    def _langgraph_engine_factory_provider():
        # 开关三态（DC-B4-03/INV-B4-03）：False → None（HC-07 默认零变化）；
        # True + 依赖在位 → 构造闭包；True + 依赖缺失 → 降级日志 + None（不伪造等价）
        if not getattr(
            container.resolve("settings"), "langgraph_runtime_enabled", False
        ):
            return None

        def _make_langgraph_engine():
            from orchestration_platform.engine.adapter.langgraph_adapter import (
                LangGraphAdapter,
                is_langgraph_available,
            )

            if not is_langgraph_available():
                logging.getLogger(__name__).warning(
                    "langgraph 依赖不可用，第二运行时不构造（降级内置引擎，INV-B4-03）"
                )
                return None
            return LangGraphAdapter(
                executor_registry=container.resolve("executor_registry"),
                settings=build_engine_settings(),
            )

        return _make_langgraph_engine

    container.register("settings", get_settings)
    container.register("metrics", MetricsCollector)
    container.register("llm_client", _llm_client_provider)
    container.register("executor_registry", _executor_registry_provider)
    container.register("workflow_repository", _workflow_repository_provider)
    container.register("governance_chain", _governance_chain_provider)
    container.register("builtin_engine_factory", _builtin_engine_factory_provider)
    container.register("langgraph_engine_factory", _langgraph_engine_factory_provider)
    app.state.container = container

    settings = container.resolve("settings")
    metrics_service = container.resolve("metrics")

    # ---------------- 启动期强校验（arch_review_v2_tasks G1，DC-05/卡 A5） ----------------
    # 装配最早点执行弱密钥 fail-fast：production 命中黑名单拒绝启动
    # （拒绝启动而非启动后接口失败）；development 仅告警不阻断（基线零变化）。
    run_startup_checks(settings)

    # ---------------- 异常处理器 ----------------
    @app.exception_handler(WorkflowValidationError)
    async def handle_validation_error(request: Request, exc: WorkflowValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content=exc.to_dict())

    @app.exception_handler(InstanceStateConflictError)
    async def handle_state_conflict(request: Request, exc: InstanceStateConflictError) -> JSONResponse:
        return JSONResponse(status_code=409, content=exc.to_dict())

    @app.exception_handler(ResourceNotFoundError)
    async def handle_not_found(request: Request, exc: ResourceNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content=exc.to_dict())

    @app.exception_handler(TenantQuotaExceededError)
    async def handle_tenant_quota_exceeded(
        request: Request, exc: TenantQuotaExceededError
    ) -> JSONResponse:
        # 并发配额超限 429 映射（roadmap B4，DC-B4-01）：拒绝路径零副作用
        # （实例未注册未编译未执行）；Starlette 按异常类 MRO 精确优先于
        # DomainError(500) 兜底
        return JSONResponse(status_code=429, content=exc.to_dict())

    @app.exception_handler(DomainError)
    async def handle_domain_error(request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(status_code=500, content=exc.to_dict())

    # ---------------- 健康检查 ----------------
    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok"}

    # ---------------- 指标 ----------------
    @app.get("/metrics")
    async def metrics() -> dict:
        return metrics_service.snapshot()

    # ---------------- 路由注册 ----------------
    from orchestration_platform.api.routers.workflow_definition_router import router as wf_router
    from orchestration_platform.api.routers.execution_instance_router import router as inst_router
    from orchestration_platform.api.routers.rollback_replay_router import router as rb_router
    from orchestration_platform.api.routers.realtime_subscription_router import router as sub_router
    from orchestration_platform.api.routers.context_query_router import router as ctx_router
    from orchestration_platform.api.routers.mcp_server_router import router as mcp_tools_router
    from orchestration_platform.api.routers.tool_catalog_router import router as tool_catalog_router
    from orchestration_platform.api.routers.model_catalog_router import router as model_catalog_router

    app.include_router(wf_router, prefix="/api/v1")
    app.include_router(inst_router, prefix="/api/v1")
    app.include_router(rb_router, prefix="/api/v1")
    app.include_router(sub_router, prefix="/api/v1")
    app.include_router(ctx_router, prefix="/api/v1")
    app.include_router(mcp_tools_router, prefix="/api/v1")
    # 工具发现端点：GET /api/v1/tools（merge_tool_into_agent_node DC-A，仅追加注册）
    app.include_router(tool_catalog_router, prefix="/api/v1")
    # 模型候选端点：GET /api/v1/models/available（agent_model_selector DC-01，仅追加注册）
    app.include_router(model_catalog_router, prefix="/api/v1")

    # ---------------- 治理路由注册（第三阶段） ----------------
    try:
        from orchestration_platform.api.routers.governance.governance_router import (
            router as governance_router,
        )
        app.include_router(governance_router, prefix="/api/v1")
    except ImportError:
        pass

    # ---------------- NL 编排路由注册（偏差修正） ----------------
    if settings.nl_orchestration_enabled:
        try:
            from orchestration_platform.api.routers.nl_orchestration_router import (
                router as nl_router,
            )
            app.include_router(nl_router, prefix="/api/v1")
        except ImportError:
            pass

    # ---------------- ABAC 路由注册（偏差修正） ----------------
    if settings.abac_enabled:
        try:
            from orchestration_platform.api.routers.governance.abac_router import (
                router as abac_router,
            )
            app.include_router(abac_router, prefix="/api/v1")
        except ImportError:
            pass

    # ---------------- 墓碑路由注册（偏差修正） ----------------
    if settings.tombstone_enabled:
        try:
            from orchestration_platform.api.routers.governance.tombstone_router import (
                router as tombstone_router,
            )
            app.include_router(tombstone_router, prefix="/api/v1")
        except ImportError:
            pass

    # ---------------- 缓存管理路由注册（偏差修正） ----------------
    try:
        from orchestration_platform.api.routers.governance.cache_router import (
            router as cache_router,
        )
        app.include_router(cache_router, prefix="/api/v1")
    except ImportError:
        pass

    # ---------------- 事件总线路由注册（偏差修正） ----------------
    try:
        from orchestration_platform.api.routers.governance.event_bus_router import (
            router as event_bus_router,
        )
        app.include_router(event_bus_router, prefix="/api/v1")
    except ImportError:
        pass

    # ---------------- 沙箱路由注册（偏差修正 T141） ----------------
    if settings.sandbox_enabled:
        try:
            from orchestration_platform.api.routers.governance.sandbox_router import (
                router as sandbox_router,
            )
            app.include_router(sandbox_router, prefix="/api/v1")
        except ImportError:
            pass

    # ---------------- 插件节点路由注册（偏差修正 T151） ----------------
    if settings.plugin_sdk_enabled:
        try:
            from orchestration_platform.api.routers.plugin_node_router import (
                router as plugin_node_router,
            )
            app.include_router(plugin_node_router, prefix="/api/v1")
        except ImportError:
            pass

    # ---------------- 贡献市场路由注册（偏差修正 T159） ----------------
    if settings.node_contribution_enabled:
        try:
            from orchestration_platform.api.routers.governance.contribution_router import (
                router as contribution_router,
            )
            app.include_router(contribution_router, prefix="/api/v1")
        except ImportError:
            pass

    # ---------------- SSO 路由注册（偏差修正 T171） ----------------
    if settings.sso_enabled:
        try:
            from orchestration_platform.api.routers.governance.sso_router import (
                router as sso_router,
            )
            app.include_router(sso_router, prefix="/api/v1")
        except ImportError:
            pass

    # ---------------- 企业连接器路由注册（偏差修正 T180） ----------------
    if settings.enterprise_connector_enabled:
        try:
            from orchestration_platform.api.routers.governance.enterprise_connector_router import (
                router as enterprise_connector_router,
            )
            app.include_router(enterprise_connector_router, prefix="/api/v1")
        except ImportError:
            pass

    # ---------------- 本地数据源连接测试路由（fix_datasource_test_connection） ----------------
    from orchestration_platform.api.routers.governance.data_source_router import (
        router as data_source_router,
    )
    app.include_router(data_source_router, prefix="/api/v1")

    # ---------------- Agent 效能评估路由注册（偏差修正 T191） ----------------
    # evaluation_enabled=False 时路由不注册，评估数据与生产隔离。
    if settings.evaluation_enabled:
        try:
            from orchestration_platform.api.routers.governance.evaluation_router import (
                router as evaluation_router,
            )
            app.include_router(evaluation_router, prefix="/api/v1")
        except ImportError:
            pass

    # ---------------- 语义索引 Agent 发现路由注册（偏差修正 T192） ----------------
    if settings.agent_discovery_enabled:
        try:
            from orchestration_platform.api.routers.governance.agent_discovery_router import (
                router as agent_discovery_router,
            )
            app.include_router(agent_discovery_router, prefix="/api/v1")
        except ImportError:
            pass

    # ---------------- 触发器模板路由注册（偏差修正 G6/T225） ----------------
    # 开关关闭时不注册（访问模板清单返回 404），既有订阅路由注册顺序不变
    if settings.trigger_template_enabled:
        try:
            from orchestration_platform.api.routers.trigger_template_router import (
                router as trigger_template_router,
                set_trigger_template_registry,
            )
            from orchestration_platform.engine.realtime_subscription.trigger_templates.registry import (
                TriggerTemplateRegistry,
            )
            set_trigger_template_registry(
                TriggerTemplateRegistry.create_default_registry()
            )
            app.include_router(trigger_template_router, prefix="/api/v1")
        except ImportError:
            pass

    # ---------------- Agent 绩效查询路由注册（偏差修正 G3/T229） ----------------
    # 开关关闭时不注册（返回 404），既有六基础路由注册顺序不变
    if settings.agent_performance_enabled:
        try:
            from orchestration_platform.api.routers.governance.agent_performance_router import (
                router as agent_performance_router,
                set_performance_repository,
            )
            from orchestration_platform.observability.agent_performance.repository_postgres import (
                PostgresPerformanceRepository,
            )

            # 存储后端为 postgres 时用持久化实现（连接失败自动降级内存），
            # 否则直接用内存实现（data_source=in_memory_degraded）
            if settings.storage_backend == "postgres":
                perf_repository = PostgresPerformanceRepository(
                    host=settings.postgres_host,
                    port=settings.postgres_port,
                    db=settings.postgres_db,
                    user=settings.postgres_user,
                    password=settings.postgres_password,
                    memory_window_size=settings.agent_performance_memory_window_size,
                )
            else:
                from orchestration_platform.observability.agent_performance.repository_local import (
                    InMemoryPerformanceRepository,
                )
                perf_repository = InMemoryPerformanceRepository(
                    window_size=settings.agent_performance_memory_window_size,
                )
            set_performance_repository(perf_repository)
            app.include_router(agent_performance_router, prefix="/api/v1")
        except ImportError:
            pass

    # ---------------- 范例库路由注册（增量追加） ----------------
    # 暴露既有范例库服务为只读 REST 接口（GET /api/v1/examples[/{id}]）。
    # 模块导入失败时降级跳过，不影响应用启动与其余路由；无条件注册（不引入开关）。
    try:
        from orchestration_platform.api.routers.example_library_router import (
            router as example_library_router,
        )

        app.include_router(example_library_router, prefix="/api/v1")
    except ImportError:
        pass

    # ---------------- Agent 市场路由注册（agent_tool_marketplace T11.3） ----------------
    # 开关关闭时不注册（市场端点访问 404），既有路由注册顺序不变（DC-09/INV-D05）
    if settings.agent_market_enabled:
        try:
            from orchestration_platform.api.routers.agent_market_router import (
                router as agent_market_router,
            )
            app.include_router(agent_market_router, prefix="/api/v1")
        except ImportError:
            pass

    # ---------------- Agent 市场用户自主开发路由注册（agent_market_user_development T4.2） ----------------
    # 开关关闭时不注册（用户自主开发端点访问 404），既有路由注册顺序不变（DC-07/INV-DEV-01）
    if settings.agent_market_dev_enabled:
        try:
            from orchestration_platform.api.routers.agent_market_dev_router import (
                router as agent_market_dev_router,
            )
            app.include_router(agent_market_dev_router, prefix="/api/v1")
        except ImportError:
            pass

    # ---------------- Tool 市场路由注册（agent_tool_marketplace T11.3） ----------------
    # 开关关闭时不注册（市场端点访问 404），既有路由注册顺序不变（DC-09/INV-D05）
    if settings.tool_market_enabled:
        try:
            from orchestration_platform.api.routers.tool_market_router import (
                router as tool_market_router,
            )
            app.include_router(tool_market_router, prefix="/api/v1")
        except ImportError:
            pass

    # ---------------- 向量知识库管理路由注册（vector_kb_management T7.5） ----------------
    # 开关关闭时不注册（管理端点访问 404），服务不构造、目录零创建（INV-D23）；
    # 模块导入失败时降级跳过（CHG-DEP：缺失降级，不影响应用启动与其余路由）。
    if settings.vector_kb_management_enabled:
        try:
            from orchestration_platform.api.routers.vector_kb_management_router import (
                router as vector_kb_management_router,
            )
            app.include_router(vector_kb_management_router, prefix="/api/v1")
        except ImportError:
            pass

    # ---------------- 文档标注路由注册（vkb_doc_annotation / CHG-13） ----------------
    # 独立开关（默认 False）门控：未启用 → 标注端点 404、既有导入/写入/检索零变化（AC-61）；
    # 与既有 kb-management 门控范式一致；模块导入失败时降级跳过（CHG-DEP）。
    if settings.vector_kb_annotation_enabled:
        try:
            from orchestration_platform.api.routers.vector_kb_management_router import (
                annotation_router as vector_kb_annotation_router,
            )
            app.include_router(vector_kb_annotation_router, prefix="/api/v1")
        except ImportError:
            pass

    # ---------------- JWT 签发端点注册（arch_review_v2_tasks G2，CHG-13/DC-03） ----------------
    # 独立开关默认 False：关闭时路由不注册（POST /api/v1/auth/token 404）；
    # 面向部署侧信任边界（内网/网关后调用，deploy.md 明示）
    if settings.auth_token_issuance_enabled:
        try:
            from orchestration_platform.api.routers.auth_router import (
                router as auth_router,
            )
            app.include_router(auth_router, prefix="/api/v1")
        except ImportError:
            pass

    # ---------------- RBAC 启动期幂等初始化（arch_review_v2_tasks G3，CHG-14/DC-06） ----------------
    # 开关默认 False 不执行；开启时幂等初始化（默认租户 → 内置角色 → 可选初始管理员绑定）。
    # 无事件循环时同步执行（失败上抛 fail-fast）；已有运行循环时调度后台任务。
    if settings.rbac_bootstrap_enabled:
        import asyncio

        from orchestration_platform.governance.rbac.bootstrap import (
            initialize_rbac_bootstrap,
        )

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(initialize_rbac_bootstrap(settings))
        else:
            asyncio.get_running_loop().create_task(
                initialize_rbac_bootstrap(settings)
            )

    # ---------------- 事件总线与审计订阅组装（arch_review_v2_tasks_wave2 G1，CHG-66/DC-21/DC-23/DC-25） ----------------
    # 双开关默认 False：关闭时零组装、零发布、零订阅（基线零变化）；
    # 开启时按 kafka 配置选择总线后端，装配期注入发布器与审计订阅。
    # roadmap B3：gate 追加 event_export_enabled（出口 worker 复用同一总线实例；
    # api 开关为 enabled 的子开关——注册路由前置条件为出口装配就绪）。
    if (
        settings.event_bus_publish_enabled
        or settings.audit_event_subscription_enabled
        or settings.event_export_enabled
    ):
        try:
            from orchestration_platform.api.routers.execution_instance_router import (
                set_domain_event_publisher,
            )
            from orchestration_platform.governance.audit.audit_event_subscriber import (
                AuditEventSubscriber,
                acquire_audit_repository,
            )
            from orchestration_platform.governance.audit.audit_logger import AuditLogger
            from orchestration_platform.governance.audit.mask_engine import MaskEngine
            from orchestration_platform.governance.event_bus.event_bus_manager import (
                EventBusManager,
            )
            from orchestration_platform.governance.event_bus.in_memory_event_bus import (
                InMemoryEventBus,
            )
            from orchestration_platform.governance.event_bus.workflow_domain_event_publisher import (
                WorkflowDomainEventPublisher,
            )

            if settings.kafka_enabled and settings.kafka_bootstrap_servers:
                event_bus = EventBusManager().get_bus()
            else:
                event_bus = InMemoryEventBus(
                    history_max=settings.event_bus_memory_history_max,
                )
            # 总线实例留存 app.state（roadmap B3：出口测试/调试取用同一实例）。
            app.state.event_bus = event_bus
            if settings.event_bus_publish_enabled:
                # G1 发布器实例留存（arch_review_v2_tasks_wave2 G3 复用微改，DC-23）：
                # 模块级注入供 P1~P4 发布链；app.state 留存供 Cron 扫描器
                # （P8/启动链路）取用同一实例（事件同源）。
                _g1_publisher = WorkflowDomainEventPublisher(
                    event_bus=event_bus,
                    topic=settings.kafka_workflow_topic,
                )
                set_domain_event_publisher(_g1_publisher)
                app.state.domain_event_publisher = _g1_publisher
                container.register(
                    "domain_event_publisher", lambda p=_g1_publisher: p
                )
            if settings.audit_event_subscription_enabled:
                primary_repository, fallback_repository = acquire_audit_repository()
                if primary_repository is not None or fallback_repository is not None:
                    audit_subscriber = AuditEventSubscriber(
                        primary_repository=primary_repository,
                        fallback_repository=fallback_repository,
                        audit_logger=AuditLogger(),
                        mask_engine=MaskEngine(),
                        domain_event_publisher=WorkflowDomainEventPublisher(
                            event_bus=event_bus,
                            topic=settings.kafka_workflow_topic,
                        ),
                    )

                    async def _startup_audit_subscription() -> None:
                        """启动期恢复审计哈希链并挂接事件订阅（DC-25 规则 3）。"""
                        await audit_subscriber.resume_audit_hash_chain()
                        audit_subscriber.attach(
                            event_bus=event_bus,
                            topic=settings.kafka_workflow_topic,
                        )

                    app.add_event_handler("startup", _startup_audit_subscription)
            if settings.event_export_enabled:
                # 事件出口装配（roadmap B3，契约倒置 + 内存态 DLQ）：
                # 注册表/工作器与路由共享同一内存实例；startup 挂接消费循环
                # （与审计订阅不同消费组 → 扇出各得全量）；shutdown 幂等停止。
                from orchestration_platform.api.routers.event_export_router import (
                    router as event_export_router,
                    set_event_export_context,
                )
                from orchestration_platform.governance.event_export.subscription_registry import (
                    EventSubscriptionRegistry,
                )
                from orchestration_platform.governance.event_export.webhook_delivery_worker import (
                    WebhookDeliveryWorker,
                )

                # B7 W3：订阅持久化路径注入注册表（变更后原子落盘、
                # startup 恢复；默认 ./.data/event_export/subscriptions.json）
                _export_registry = EventSubscriptionRegistry(
                    persist_path=settings.event_export_subscriptions_persist_path,
                )
                _export_worker = WebhookDeliveryWorker(
                    _export_registry,
                    max_attempts=settings.event_export_webhook_max_attempts,
                    backoff_base_seconds=settings.event_export_webhook_backoff_base_seconds,
                    backoff_cap_seconds=settings.event_export_webhook_backoff_cap_seconds,
                    timeout_seconds=settings.event_export_webhook_timeout_seconds,
                    delivery_history_max=settings.event_export_delivery_history_max,
                    dlq_max_entries=settings.event_export_dlq_max_entries,
                )
                set_event_export_context(
                    _export_registry,
                    _export_worker,
                    event_bus=event_bus,
                    topic=settings.kafka_workflow_topic,
                )
                container.register(
                    "event_export_registry", lambda r=_export_registry: r
                )
                container.register(
                    "event_export_worker", lambda w=_export_worker: w
                )

                async def _startup_event_export() -> None:
                    """启动期恢复持久化订阅并挂接事件出口消费循环
                    （roadmap B3；B7 W3 订阅持久化恢复）。"""
                    _export_registry.load_from_disk()
                    _export_worker.attach(
                        event_bus=event_bus,
                        topic=settings.kafka_workflow_topic,
                    )

                async def _shutdown_event_export() -> None:
                    """关停期停止事件出口投递（幂等）。"""
                    _export_worker.stop()

                app.add_event_handler("startup", _startup_event_export)
                app.add_event_handler("shutdown", _shutdown_event_export)
                if settings.event_export_api_enabled:
                    app.include_router(event_export_router, prefix="/api/v1")
        except ImportError:
            logging.getLogger(__name__).warning(
                "事件总线/审计订阅组装失败：依赖模块不可用（跳过）",
                exc_info=True,
            )

    # ---------------- 计量配额闭环组装（roadmap B4，DC-B4-02/03/04；B7 W1a 查询解耦） ----------------
    # 配额闸门始终装配（配额源三级回退：仓储 get_quota → settings 默认 → 内置
    # 兜底）；执行级记账按 metering_enabled 门控：用量仓储按 storage_backend
    # 双后端选择（B7 W1a 上提门控外，usage 查询端点共用），缓冲队列两段构造
    # （queue 先建 → recorder 后建 → callback 绑定），startup/shutdown
    # 挂接定时刷新。metering_enabled=False 时无用量记录（对账必差异，
    # reconcile 脚本已文档化前提）。
    try:
        from orchestration_platform.governance.tenant.quota_gate import (
            TenantQuotaGate,
            set_quota_gate,
        )

        # 租户仓储按后端选择（双后端 get_quota 签名一致，quota_provider 协议）
        if settings.storage_backend == "postgres":
            from orchestration_platform.governance.persistence.postgres.postgres_tenant_repository import (
                PostgresTenantRepository,
            )

            _tenant_repository = PostgresTenantRepository()
        else:
            from orchestration_platform.governance.persistence.local_file.local_file_tenant_repository import (
                LocalFileTenantRepository,
            )

            _tenant_repository = LocalFileTenantRepository()

        # 用量仓储按后端选择（append/append_batch 双后端签名一致）。
        # B7 W1a 上提至 metering_enabled 门控之外：查询路径（治理 usage
        # 端点）与记账路径解耦——metering 关闭时端点仍可查历史数据；
        # metering 段复用同实例，不重复构造。
        if settings.storage_backend == "postgres":
            from orchestration_platform.governance.persistence.postgres.postgres_token_usage_repository import (
                PostgresTokenUsageRepository,
            )

            _usage_repository = PostgresTokenUsageRepository()
        else:
            from orchestration_platform.governance.persistence.local_file.local_file_token_usage_repository import (
                LocalFileTokenUsageRepository,
            )

            _usage_repository = LocalFileTokenUsageRepository()

        # 治理用量查询仓储注入（roadmap B7 W1a：模块级 setter，先例 =
        # set_usage_recorder / set_quota_gate）
        from orchestration_platform.governance.application.governance_services import (
            set_token_usage_repository,
        )

        set_token_usage_repository(_usage_repository)
        container.register(
            "token_usage_repository", lambda r=_usage_repository: r
        )

        _quota_gate = TenantQuotaGate(quota_provider=_tenant_repository)
        set_quota_gate(_quota_gate)
        container.register("quota_gate", lambda g=_quota_gate: g)

        if settings.metering_enabled:
            from orchestration_platform.governance.metering.usage_buffer_queue import (
                UsageBufferQueue,
            )
            from orchestration_platform.governance.metering.usage_recorder import (
                ExecutionUsageRecorder,
                set_usage_recorder,
            )

            # 两段构造：queue 先建（恢复语义）→ recorder 后建 → callback 绑定
            # （_flush_callback 为实例属性可后置赋值；按租户分组批量写入）
            _usage_buffer_queue = UsageBufferQueue(flush_callback=None)
            _usage_recorder = ExecutionUsageRecorder(
                _usage_repository,
                enabled=True,
                buffer_queue=_usage_buffer_queue,
            )
            _usage_buffer_queue._flush_callback = _usage_recorder._flush_to_repository
            set_usage_recorder(_usage_recorder)
            container.register("usage_recorder", lambda r=_usage_recorder: r)

            async def _startup_usage_recorder() -> None:
                """启动期开启用量缓冲定时刷新（roadmap B4）。"""
                await _usage_recorder.start()

            async def _shutdown_usage_recorder() -> None:
                """关停期最终刷新并停止定时任务（roadmap B4）。"""
                await _usage_recorder.stop()

            app.add_event_handler("startup", _startup_usage_recorder)
            app.add_event_handler("shutdown", _shutdown_usage_recorder)
    except ImportError:
        logging.getLogger(__name__).warning(
            "计量配额闭环组装失败：依赖模块不可用（跳过）",
            exc_info=True,
        )

    # ---------------- Cron 定时触发组装（arch_review_v2_tasks_wave2 G3，CHG-59/CHG-66，DC-28/DC-29） ----------------
    # 注册表始终装配（管理 API 与扫描器共享同一内存实例）；双开关默认 False：
    # 关闭时管理路由不注册（404）、扫描器不启动（基线零变化，HC-07）。
    # 重启后注册表为空的已知边界经管理 API 文档明示（触发器需重新注册）。
    try:
        from orchestration_platform.api.routers.trigger_schedule_router import (
            router as trigger_schedule_router,
            set_trigger_schedule_registry,
        )
        from orchestration_platform.application.schedule.schedule_scanner import (
            ScheduleScanner,
            TriggerScheduleRegistry,
        )

        _trigger_schedule_registry = TriggerScheduleRegistry()
        set_trigger_schedule_registry(_trigger_schedule_registry)
        if settings.trigger_schedule_api_enabled:
            app.include_router(trigger_schedule_router, prefix="/api/v1")
        if settings.schedule_scanner_enabled:
            _cron_publisher = getattr(app.state, "domain_event_publisher", None)
            _cron_fire = build_cron_fire_callback(
                container.resolve("workflow_repository"),
                _cron_publisher,
            )
            _schedule_scanner = ScheduleScanner(
                registry=_trigger_schedule_registry,
                fire=_cron_fire,
                interval_seconds=settings.schedule_scanner_interval_seconds,
                publisher=_cron_publisher,
            )

            async def _startup_schedule_scanner() -> None:
                """启动期拉起 Cron 扫描循环（schedule_scanner_enabled 门控）。"""
                await _schedule_scanner.start()

            async def _shutdown_schedule_scanner() -> None:
                """关停期停止 Cron 扫描循环（幂等）。"""
                await _schedule_scanner.stop()

            app.add_event_handler("startup", _startup_schedule_scanner)
            app.add_event_handler("shutdown", _shutdown_schedule_scanner)
    except ImportError:
        logging.getLogger(__name__).warning(
            "定时触发组装失败：依赖模块不可用（跳过）",
            exc_info=True,
        )

    # ---------------- 治理中间件注入（第三阶段） ----------------
    # 在路由注册之后添加中间件，确保拦截器链对全部 /api/v1/** 请求生效。
    # 未启用治理时拦截器链为 None，中间件自动放行（降级模式）。
    try:
        from orchestration_platform.api.middleware.rbac_authorization_middleware import (
            RbacAuthorizationMiddleware,
        )

        governance_chain = container.resolve("governance_chain")
        app.add_middleware(
            RbacAuthorizationMiddleware,
            interceptor_chain=governance_chain,
        )
    except ImportError:
        # 治理模块未安装时降级放行（容器 provider 延迟导入同源，行为等价替换）
        pass

    container.freeze()

    return app
