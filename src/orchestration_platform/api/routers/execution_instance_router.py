# =============================================================================
# 执行实例路由（api/routers/execution_instance_router.py）
#
# 职责：启动工作流执行实例、查询实例状态与事件、SSE 事件流推送。
# 设计意图：接入真实执行引擎（YamlWorkflowParser → GraphCompiler → Scheduler），
#           以内存注册表暂存实例运行时状态，通过 SSE 实时推送执行事件。
# =============================================================================

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from orchestration_platform.domain.errors import (
    INSTANCE_CANCEL_CONFLICT,
    INSTANCE_NOT_FOUND,
    NODE_TOOL_ARGUMENT_MISMATCH,
    STATE_CHECKPOINT_CORRUPTED,
    WF_NODE_EXECUTION_FAILED,
    WorkflowValidationError,
    DomainError,
    InstanceStateConflictError,
    ResourceNotFoundError,
)
from orchestration_platform.domain.models.common import JsonValue
from orchestration_platform.domain.models.domain_event import (
    DEFAULT_TENANT_ID,
    DomainEvent,
    summarize_text,
)
from orchestration_platform.domain.models.enums import ControlFlowKind, NodeType
from orchestration_platform.domain.models.shared_state import SharedState
from orchestration_platform.engine.abstractions.domain_event_publisher import (
    IDomainEventPublisher,
)
from orchestration_platform.engine.scheduler import WorkflowSuspendedSignal
from orchestration_platform.governance.metering.usage_recorder import (
    get_usage_recorder,
)
from orchestration_platform.governance.tenant.quota_gate import (
    get_quota_gate,
)
from orchestration_platform.integrations.abstractions.external_clients import (
    IToolInvoker,
)
from orchestration_platform.observability.run_log.fields import (
    BUSINESS_CATEGORY_DEFAULT,
)

router = APIRouter(tags=["执行实例"])


# =============================================================================
# 请求体模型
# =============================================================================

class StartInstanceBody(BaseModel):
    definition_id: str
    definition_version: Optional[int] = None
    inputs: dict = Field(default_factory=dict)
    yaml_text: Optional[str] = None
    trigger_type: Optional[str] = None


# =============================================================================
# 实例运行时模型与内存注册表
# =============================================================================

# 实例终态集合（B2 嵌入 SDK：update_status / SSE 收尾 / cancel 校验 / results
# 四处统一口径，防漂移；cancelled 为 B2 取消端点新增终态）
_TERMINAL_STATUSES = ("succeeded", "failed", "rolled_back", "cancelled")


class InstanceRuntime:
    """实例运行时状态：持有执行状态、事件列表与后台任务引用。"""

    def __init__(
        self,
        instance_id: str,
        definition_id: str,
        tenant_id: Optional[str] = None,
        business_category: Optional[str] = None,
    ):
        self.instance_id: str = instance_id
        self.definition_id: str = definition_id
        # 租户/业务类别通道（T7.2，可选字段，既有构造调用不受影响）
        self.tenant_id: Optional[str] = tenant_id
        self.business_category: Optional[str] = business_category
        self.status: str = "pending"
        self.started_at: str = datetime.now(timezone.utc).isoformat()
        self.finished_at: Optional[str] = None
        self.events: List[Dict[str, Any]] = []
        self.task: Optional[asyncio.Task] = None
        # 恢复上下文缓存（arch_review_v2 3.4，CHG-07，卡 A1）：
        # 编译产物与 trace 供 recover 端点重建 Scheduler 任务（resume_checkpoint 续跑）
        self.compiled: Optional[Any] = None
        self.trace_id: Optional[str] = None
        # 挂起检查点登记（arch_review_v2_tasks_wave2 G2，CHG-62/DC-26 规则 3）：
        # review 门挂起时的检查点标识；approve 决策经 resume_checkpoint 唤醒、
        # 重启场景由 recover 分支重建（None = 未挂起或检查点未持久化）
        self.suspended_checkpoint_id: Optional[str] = None
        # 终态共享状态缓存（B2 嵌入 SDK 取结果端点）：run_and_record 成功路径
        # 落值，供 GET /instances/{id}/results 读取 node_outputs（None = 未终态）
        self.final_state: Optional[Any] = None


class InstanceRegistry:
    """模块级单例：实例内存注册表，供路由函数共享。"""

    def __init__(self):
        self._instances: Dict[str, InstanceRuntime] = {}

    def register(self, rt: InstanceRuntime) -> None:
        self._instances[rt.instance_id] = rt

    def get(self, instance_id: str) -> InstanceRuntime:
        rt = self._instances.get(instance_id)
        if rt is None:
            raise HTTPException(status_code=404, detail=f"实例 {instance_id} 不存在")
        return rt

    def remove(self, instance_id: str) -> None:
        self._instances.pop(instance_id, None)

    def append_event(self, instance_id: str, event: Dict[str, Any]) -> None:
        rt = self.get(instance_id)
        rt.events.append(event)

    def update_status(self, instance_id: str, status: str) -> None:
        rt = self.get(instance_id)
        rt.status = status
        if status in _TERMINAL_STATUSES:
            rt.finished_at = datetime.now(timezone.utc).isoformat()

    def get_events(self, instance_id: str) -> List[Dict[str, Any]]:
        rt = self.get(instance_id)
        return list(rt.events)

    def count_active_by_tenant(self, tenant_id: str) -> int:
        """统计租户当前并发活跃实例数（非终态计数，roadmap B4 D1 口径）。

        终态（succeeded/failed/rolled_back/cancelled）释放配额槽位；
        tenant_id 为 None 的运行时不计数（治理降级/cron 无租户不占槽），
        None 入参直接返回 0（闸门仅以非 None 租户调用，防御性语义闭合）。
        """
        if tenant_id is None:
            return 0
        return sum(
            1
            for rt in self._instances.values()
            if rt.tenant_id == tenant_id
            and rt.status not in _TERMINAL_STATUSES
        )


_instance_registry = InstanceRegistry()

# 领域事件发布器（CHG-62/DC-23 发布点 P1~P3）：由 application_factory 在
# 装配期注入（G1 开关开启时）；None 表示发布旁路，基线行为零变化。
_domain_event_publisher: Optional[IDomainEventPublisher] = None


def set_domain_event_publisher(publisher: Optional[IDomainEventPublisher]) -> None:
    """注入领域事件发布器（应用装配期调用；None 表示关闭发布）。"""
    global _domain_event_publisher
    _domain_event_publisher = publisher


# =============================================================================
# 执行器注册表构造（延迟导入 + 降级 stub，保证模块导入不失败）
# =============================================================================

def _build_llm_client():
    """构造 LLM 客户端；配置缺失或构造失败时返回 None（DC-2）。

    实现已上移到 integrations/llm/factory.build_llm_client（唯一权威），
    本函数仅作路由侧别名保留，避免既有调用点与测试导入路径变更。
    重依赖延迟导入，不在模块顶层引入（spec 4.5.5）。
    """
    from orchestration_platform.integrations.llm.factory import build_llm_client

    return build_llm_client()


def _build_executor_registry():
    """构造执行器注册表，登记基础执行器；构造失败时跳过该执行器并记录日志。"""
    try:
        from orchestration_platform.engine.node_executor_registry import NodeExecutorRegistry
        registry = NodeExecutorRegistry()

        executor_configs = [
            ("orchestration_platform.engine.nodes.trigger_node_executor", "TriggerNodeExecutor"),
            ("orchestration_platform.engine.nodes.control_flow_node_executor", "ControlFlowNodeExecutor"),
            ("orchestration_platform.engine.nodes.data_source_node_executor", "DataSourceNodeExecutor"),
            ("orchestration_platform.engine.nodes.data_transform_node_executor", "DataTransformNodeExecutor"),
            ("orchestration_platform.engine.nodes.tool_node_executor", "ToolNodeExecutor"),
            ("orchestration_platform.engine.nodes.agent_node_executor", "AgentNodeExecutor"),
            ("orchestration_platform.engine.nodes.dynamic_injector_node_executor", "DynamicInjectorNodeExecutor"),
            ("orchestration_platform.engine.nodes.subworkflow_node_executor", "SubWorkflowNodeExecutor"),
            ("orchestration_platform.engine.nodes.output_display_node_executor", "OutputDisplayNodeExecutor"),
            ("orchestration_platform.engine.nodes.vector_knowledge_base_node_executor", "VectorKnowledgeBaseNodeExecutor"),
            ("orchestration_platform.engine.nodes.llm_node_executor", "LlmNodeExecutor"),
            ("orchestration_platform.engine.nodes.code_node_executor", "CodeNodeExecutor"),
        ]

        # LLM 客户端单次构造，Agent 与 LLM 执行器共享同一实例（llm_code_nodes_v2
        # CHG-03/DC-07：构造次数较基线不增加；工厂失败两者一致跳过注册）
        llm_client = _build_llm_client()

        for module_path, class_name in executor_configs:
            try:
                import importlib
                mod = importlib.import_module(module_path)
                cls = getattr(mod, class_name)

                # Agent 执行器需要 llm_client 参数，按类名分派构造（DC-2/DC-6）
                if class_name == "AgentNodeExecutor":
                    if llm_client is not None:
                        executor = cls(
                            llm_client=llm_client, tool_invokers=_tool_invokers
                        )
                        registry.register(executor)
                        logging.info("Agent 执行器注册成功")
                    else:
                        logging.warning("Agent 执行器注册跳过: LLM 客户端不可用")
                    continue

                # LLM 执行器与 Agent 共享同一 llm_client 实例（CHG-03/DC-07）；
                # 工厂失败 → 跳过注册 + 告警（与 Agent 分支一致，不拖垮启动）
                if class_name == "LlmNodeExecutor":
                    if llm_client is not None:
                        executor = cls(llm_client=llm_client)
                        registry.register(executor)
                        logging.info("LLM 执行器注册成功")
                    else:
                        logging.warning("LLM 执行器注册跳过: LLM 客户端不可用")
                    continue

                # Tool 执行器：构造后注入同一 invoker 映射（顺带修复存量 tool 节点
                # 生产装配下必返 NODE_TOOL_NOT_FOUND 的缺陷，不改任何接口，CHG-9）
                if class_name == "ToolNodeExecutor":
                    executor = cls()
                    for invoker in _tool_invokers.values():
                        executor.register_tool(invoker)
                    registry.register(executor)
                    logging.info(
                        "Tool 执行器注册成功（注入 %d 个工具调用器）",
                        len(_tool_invokers),
                    )
                    continue

                # DataSource 执行器需要 connector_registry 参数，按类名分派构造（DC-2/CHG-3）
                if class_name == "DataSourceNodeExecutor":
                    from orchestration_platform.integrations.data_source_connectors.connector_registry import DataSourceConnectorRegistry
                    connector_registry = DataSourceConnectorRegistry()
                    executor = cls(connector_registry=connector_registry)
                    registry.register(executor)
                    logging.info("DataSource 执行器注册成功")
                    continue

                executor = cls()
                registry.register(executor)
            except Exception as exc:
                logging.warning("执行器 %s 注册失败: %s", class_name, exc)

        return registry
    except Exception as exc:
        logging.warning("执行器注册表构造失败: %s", exc)
        return None


# =============================================================================
# 工具调用器生产装配（merge_tool_into_agent_node DC-D / FR-4 / CHG-9）
# =============================================================================

def _run_coro_sync_for_assembly(coro):
    """模块装配期同步驱动协程至完成并返回结果值。

    仅用于本地文件仓储读取（协程无真实异步挂起）；协程若真实挂起
    （如未来切换 Postgres 仓储）则关闭并抛 RuntimeError，由调用方
    按源 try/except + warning 降级（防拖垮启动）。
    """
    try:
        coro.send(None)
    except StopIteration as si:
        return si.value
    coro.close()
    raise RuntimeError("协程存在真实异步挂起，无法在装配期同步驱动")


async def _read_mcp_market_registrations() -> List[Any]:
    """读取 MCP 市场仓储全量注册记录（三权限并集 + 同标识取最大版本）。

    仓储无 list_all，零接口改动按三权限并集读取（与 ToolCatalogService
    同法，INV-1）；同标识多版本取 version 字符序最大，保证与工具清单
    呈现一致。仅装配期经 _run_coro_sync_for_assembly 同步驱动。
    """
    from orchestration_platform.domain.models.enums import ToolPermission
    from orchestration_platform.mcp_tool_market.persistence.local_file_tool_market_repository import (
        LocalFileToolMarketRepository,
    )

    repo = LocalFileToolMarketRepository()
    latest: Dict[str, Any] = {}
    seen: set = set()
    for permission in (
        ToolPermission.READONLY,
        ToolPermission.READWRITE,
        ToolPermission.ADMIN,
    ):
        for reg in await repo.list_by_permission(permission):
            identifier = reg.descriptor.tool_identifier
            key = (identifier, reg.descriptor.version)
            if key in seen:
                continue
            seen.add(key)
            current = latest.get(identifier)
            if current is None or reg.descriptor.version > current.descriptor.version:
                latest[identifier] = reg
    return list(latest.values())


async def _platform_tool_validate_workflow(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """平台工具 validate_workflow 服务绑定：校验工作流定义（只读最小集）。

    入参：yaml_text（YAML 文本）或 definition（dict → JSON 序列化，JSON 为
    YAML 子集；str → 直接作为 YAML 文本）；出参：校验报告 dict。
    """
    yaml_text = arguments.get("yaml_text")
    if not isinstance(yaml_text, str) or not yaml_text:
        definition = arguments.get("definition")
        if isinstance(definition, str) and definition:
            yaml_text = definition
        elif isinstance(definition, dict):
            yaml_text = json.dumps(definition, ensure_ascii=False)
        else:
            raise DomainError(
                NODE_TOOL_ARGUMENT_MISMATCH, "缺少 yaml_text 或 definition 参数"
            )
    try:
        from orchestration_platform.workflow.yaml_workflow_parser import (
            YamlWorkflowParser,
        )
        from orchestration_platform.domain.validation.validation_report_builder import (
            ValidationReportBuilder,
        )
        definition_model = YamlWorkflowParser().parse(yaml_text)
        report = ValidationReportBuilder().validate(definition_model)
        return report.model_dump(mode="json")
    except WorkflowValidationError as e:
        return {
            "valid": False,
            "issues": [{"code": e.code, "message": e.message, "severity": "error"}],
            "definition_id": None,
        }


async def _platform_tool_get_workflow(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """平台工具 get_workflow 服务绑定：按 ID 与版本查询工作流定义（只读最小集）。"""
    definition_id = arguments.get("definition_id")
    if not isinstance(definition_id, str) or not definition_id:
        raise DomainError(NODE_TOOL_ARGUMENT_MISMATCH, "缺少 definition_id 参数")
    version = arguments.get("version")

    from orchestration_platform.persistence.repository_factory import RepositoryFactory
    repo = RepositoryFactory.create_workflow_repository()
    definition = await repo.get(
        definition_id, version if isinstance(version, int) else None
    )
    return definition.model_dump(mode="json")


async def _platform_tool_get_instance(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """平台工具 get_instance 服务绑定：按实例 ID 查询执行状态（只读最小集）。"""
    instance_id = arguments.get("instance_id")
    if not isinstance(instance_id, str) or not instance_id:
        raise DomainError(NODE_TOOL_ARGUMENT_MISMATCH, "缺少 instance_id 参数")
    try:
        rt = _instance_registry.get(instance_id)
    except HTTPException:
        raise DomainError(INSTANCE_NOT_FOUND, f"实例 {instance_id} 不存在")
    return {"instance_id": rt.instance_id, "status": rt.status}


def _build_tool_invokers() -> Dict[str, IToolInvoker]:
    """生产装配三源工具调用器映射（DC-D / FR-4 / CHG-9）。

    装配序（每源独立 try/except + warning，失败跳过该源，防拖垮启动）：
        ① local 注册表全量（get_local_function_tool_registry().list_invokers()）；
        ② mcp 市场仓储读 descriptor，endpoint 非空者构造 McpHttpToolInvoker；
        ③ 平台工具经 ToolRouterInvokerAdapter 适配（首期只读最小集：
          validate_workflow / get_workflow / get_instance，绑定既有服务函数；
          写类工具后续批次补齐，未装配者执行时走 FR-4 规则 4 降级）。
    """
    invokers: Dict[str, IToolInvoker] = {}

    # ① local 注册表全量
    try:
        from orchestration_platform.integrations.tools.local_function_tool_registry import (
            get_local_function_tool_registry,
        )
        local_invokers = get_local_function_tool_registry().list_invokers()
        invokers.update(local_invokers)
        logging.info("工具装配: local 源注册 %d 个", len(local_invokers))
    except Exception as exc:
        logging.warning("工具装配: local 源加载失败，已跳过: %s", exc)

    # ② mcp 市场仓储（endpoint 非空者构造 McpHttpToolInvoker，实验性）
    try:
        from orchestration_platform.integrations.tools.mcp_http_tool_invoker import (
            McpHttpToolInvoker,
        )
        registrations = _run_coro_sync_for_assembly(
            _read_mcp_market_registrations()
        )
        registered = 0
        for reg in registrations:
            if not reg.descriptor.endpoint:
                continue
            invokers[reg.descriptor.tool_identifier] = McpHttpToolInvoker(
                tool_identifier=reg.descriptor.tool_identifier,
                endpoint=reg.descriptor.endpoint,
            )
            registered += 1
        logging.info("工具装配: mcp 源注册 %d 个", registered)
    except Exception as exc:
        logging.warning("工具装配: mcp 源加载失败，已跳过: %s", exc)

    # ③ 平台工具（ToolRouter 只读最小集适配）
    try:
        from orchestration_platform.integrations.mcp_server.tool_router import (
            ToolRouter,
        )
        from orchestration_platform.integrations.tools.tool_router_invoker_adapter import (
            ToolRouterInvokerAdapter,
        )
        platform_router = ToolRouter(
            service_methods={
                "validate_workflow": _platform_tool_validate_workflow,
                "get_workflow": _platform_tool_get_workflow,
                "get_instance": _platform_tool_get_instance,
            }
        )
        for tool_id in ("validate_workflow", "get_workflow", "get_instance"):
            invokers[tool_id] = ToolRouterInvokerAdapter(
                router=platform_router, tool_identifier=tool_id
            )
        logging.info("工具装配: platform 源注册 3 个（只读最小集）")
    except Exception as exc:
        logging.warning("工具装配: platform 源加载失败，已跳过: %s", exc)

    return invokers


_tool_invokers: Dict[str, IToolInvoker] = _build_tool_invokers()


_executor_registry = _build_executor_registry()


# =============================================================================
# 节点事件回调实现（DC-3，供 Scheduler 实时上报节点级事件）
# =============================================================================

class NodeEventCallbackImpl:
    """节点事件回调实现：将 Scheduler 节点级事件实时追加到实例注册表。

    满足 NodeEventCallback Protocol（无需显式继承）。
    回调异常内部捕获不传播（DC-5，spec 5.3.3.2）。
    """

    def __init__(self, registry: InstanceRegistry) -> None:
        self._registry = registry

    async def on_node_started(
        self, instance_id: str, node_id: str, trace_id: str
    ) -> None:
        try:
            self._registry.append_event(instance_id, {
                "event_id": str(uuid.uuid4()),
                "instance_id": instance_id,
                "event_type": "NODE_STARTED",
                "node_id": node_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": {},
            })
        except Exception:
            logging.warning(
                "NODE_STARTED 事件追加失败: instance_id=%s, node_id=%s",
                instance_id, node_id, exc_info=True,
            )

    async def on_node_completed(
        self, instance_id: str, node_id: str, trace_id: str,
        payload: Optional[Dict[str, JsonValue]] = None
    ) -> None:
        try:
            self._registry.append_event(instance_id, {
                "event_id": str(uuid.uuid4()),
                "instance_id": instance_id,
                "event_type": "NODE_COMPLETED",
                "node_id": node_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": payload if payload is not None else {},
            })
        except Exception:
            logging.warning(
                "NODE_COMPLETED 事件追加失败: instance_id=%s, node_id=%s",
                instance_id, node_id, exc_info=True,
            )

    async def on_node_failed(
        self, instance_id: str, node_id: str, trace_id: str, error: str
    ) -> None:
        try:
            self._registry.append_event(instance_id, {
                "event_id": str(uuid.uuid4()),
                "instance_id": instance_id,
                "event_type": "NODE_FAILED",
                "node_id": node_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": {"error": error},
            })
        except Exception:
            logging.warning(
                "NODE_FAILED 事件追加失败: instance_id=%s, node_id=%s",
                instance_id, node_id, exc_info=True,
            )


def _acquire_checkpoint_repository():
    """经仓储工厂获取检查点仓储（arch_review_v2 3.2，CHG-07/DC-08；E3 双后端）。

    获取失败（如 postgres 不可达）→ 降级 None + 告警（对齐 run_log_recorder
    降级范式），执行链不中断；启动场景 None = 本次运行不持久化检查点（仅内存）。
    recover 场景（3.4）失败返回 None 由调用方决定 500。
    """
    try:
        from orchestration_platform.persistence.repository_factory import RepositoryFactory

        return RepositoryFactory.create_checkpoint_repository()
    except Exception:
        logging.warning(
            "检查点仓储获取失败，本次运行不持久化检查点", exc_info=True,
        )
        return None


# =============================================================================
# 后台执行协程
# =============================================================================

def _resolve_business_category(body: StartInstanceBody, definition: Any) -> str:
    """业务类别三级回退：请求可选 trigger_type → TRIGGER 节点配置 → default。

    承载 design 关键风险 ④（路由无 trigger_type 的回退链）；值异常字符
    由 LogPathResolver.sanitize_segment 统一消毒，不在此处校验。
    """
    if body.trigger_type:
        return body.trigger_type
    for node in getattr(definition, "nodes", None) or []:
        if getattr(node, "node_type", None) == NodeType.TRIGGER:
            config = getattr(node, "configuration", None)
            trigger_type = getattr(config, "trigger_type", None)
            if trigger_type is not None:
                return str(getattr(trigger_type, "value", trigger_type))
    return BUSINESS_CATEGORY_DEFAULT


async def _publish_instance_domain_event(
    rt: InstanceRuntime,
    trace_id: str,
    event_type: str,
    payload: Dict[str, Any],
) -> None:
    """发布实例级领域事件；发布器缺失或发布失败时静默旁路（DC-31）。

    构造/发布异常独立吞掉并记录告警，绝不影响执行主链（INV-01）。
    """
    if _domain_event_publisher is None:
        return
    try:
        event = DomainEvent(
            event_type=event_type,
            instance_id=rt.instance_id,
            tenant_id=rt.tenant_id or DEFAULT_TENANT_ID,
            trace_id=trace_id,
            payload=payload,
        )
    except Exception:
        logging.warning(
            "领域事件构造失败（跳过发布）: instance_id=%s, event_type=%s",
            rt.instance_id, event_type, exc_info=True,
        )
        return
    try:
        await _domain_event_publisher.publish_domain_event(event)
    except Exception:
        logging.warning(
            "领域事件发布失败（降级吞异常）: instance_id=%s, event_type=%s",
            rt.instance_id, event_type, exc_info=True,
        )


async def run_and_record(
    scheduler,
    compiled,
    initial_state,
    instance_id: str,
    trace_id: str,
    registry: InstanceRegistry,
    run_log_recorder=None,
    resume_checkpoint=None,
    trigger_source: str = "manual",
) -> None:
    """后台协程：执行工作流并记录事件与运行日志到注册表。

    工作流级运行日志接线（T7.4）：开始处 begin_run（INSTANCE_STARTED 前）、
    成功 INSTANCE_COMPLETED 后 end_run(succeeded)、失败 INSTANCE_FAILED 后
    end_run(failed, error)；采集调用独立 try/except，绝不影响执行（INV-01）；
    不伪造结束记录（无终态时保留开始记录）。
    """
    run_started_at = datetime.now(timezone.utc)
    try:
        registry.update_status(instance_id, "running")

        if run_log_recorder is not None:
            try:
                run_log_recorder.begin_run()
            except Exception:
                logging.warning(
                    "运行日志 begin_run 接线失败: instance_id=%s", instance_id,
                    exc_info=True,
                )

        # 执行级用量记账（roadmap B4，DC-B4-03）：与 begin_run 同 invocation
        # 构造性配对，每次执行启动记 1 行（tokens=0 占位）；拒绝路径不进本函数
        # 天然不计；tenant_id 缺失由 recorder 兜底 log_tenant_default；
        # 失败降级不阻断执行（INV-01）
        usage_recorder = get_usage_recorder()
        if usage_recorder is not None:
            try:
                rt_meta = registry.get(instance_id)
                await usage_recorder.record_execution_start(
                    rt_meta.tenant_id,
                    instance_id,
                    rt_meta.definition_id,
                )
            except Exception:
                logging.warning(
                    "执行级用量记账失败（降级吞异常）: instance_id=%s", instance_id,
                    exc_info=True,
                )

        registry.append_event(instance_id, {
            "event_id": str(uuid.uuid4()),
            "instance_id": instance_id,
            "event_type": "INSTANCE_STARTED",
            "node_id": None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": {},
        })

        # 领域事件：实例启动发布（CHG-62/DC-23 发布点 P1）
        rt_run = registry.get(instance_id)
        await _publish_instance_domain_event(
            rt_run,
            trace_id,
            "instance.started",
            {
                "workflow_id": rt_run.definition_id,
                "trigger_source": trigger_source,
                "business_category": rt_run.business_category
                or BUSINESS_CATEGORY_DEFAULT,
            },
        )

        # W2a 租户穿参：rt_run 携带真实租户（四触发路径均流入），
        # 传入调度器供 node.* 领域事件与节点级计量按租户发布/记录
        final_state = await scheduler.execute(
            compiled, initial_state, instance_id, trace_id,
            resume_checkpoint=resume_checkpoint,
            tenant_id=rt_run.tenant_id,
        )
        # 终态共享状态缓存（B2 嵌入 SDK）：供 GET /instances/{id}/results 读取
        rt_run.final_state = final_state

        run_finished_at = datetime.now(timezone.utc)

        registry.update_status(instance_id, "succeeded")
        registry.append_event(instance_id, {
            "event_id": str(uuid.uuid4()),
            "instance_id": instance_id,
            "event_type": "INSTANCE_COMPLETED",
            "node_id": None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": {},
        })

        # 领域事件：实例成功发布（CHG-62/DC-23 发布点 P2）
        node_count_summary = sum(
            1 for e in rt_run.events if e.get("event_type") == "NODE_COMPLETED"
        )
        await _publish_instance_domain_event(
            rt_run,
            trace_id,
            "instance.succeeded",
            {"node_count_summary": node_count_summary},
        )

        if run_log_recorder is not None:
            try:
                node_summary = getattr(run_log_recorder, "node_summary", None)
                if isinstance(node_summary, dict) and node_summary.get("total", 0) > 0:
                    run_log_recorder.end_run(
                        "succeeded",
                        finished_at=run_finished_at,
                        duration_ms=max(
                            0,
                            int(
                                (run_finished_at - run_started_at)
                                .total_seconds()
                                * 1000
                            ),
                        ),
                        node_summary=node_summary,
                    )
                else:
                    run_log_recorder.end_run(
                        "succeeded",
                        finished_at=run_finished_at,
                        duration_ms=max(
                            0,
                            int(
                                (run_finished_at - run_started_at)
                                .total_seconds()
                                * 1000
                            ),
                        ),
                    )
            except Exception:
                logging.warning(
                    "运行日志 end_run 接线失败: instance_id=%s", instance_id,
                    exc_info=True,
                )
    except WorkflowSuspendedSignal as signal:
        # HITL 挂起落点（arch_review_v2_tasks_wave2 G2，CHG-62/DC-26 规则 3）：
        # 先于 except Exception 判定（E-W2-11）——挂起不是失败。置 WAITING_REVIEW
        # （既有枚举值，零新增状态）+ 登记 suspended_checkpoint_id + 追加
        # INSTANCE_SUSPENDED 执行事件 + 发布 instance.suspended（发布点 P4）；
        # 不写运行日志终态（不伪造结束记录）；后台 task 就此结束（不占调度
        # 游标，task.done()=True，可被取消/恢复查询）
        registry.update_status(instance_id, "waiting_review")
        rt_run = registry.get(instance_id)
        rt_run.suspended_checkpoint_id = signal.checkpoint_id
        registry.append_event(instance_id, {
            "event_id": str(uuid.uuid4()),
            "instance_id": instance_id,
            "event_type": "INSTANCE_SUSPENDED",
            "node_id": signal.suspended_node_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": {
                "checkpoint_id": signal.checkpoint_id,
                "next_node_id": signal.next_node_id,
                "review_instructions": signal.review_instructions,
                "timeout_action": signal.timeout_action,
            },
        })
        # 领域事件：实例挂起发布（CHG-62/DC-23 发布点 P4；载荷键对齐 design
        # 附录 A.1：suspended_node_id/checkpoint_id/next_node_id/review_instructions）
        await _publish_instance_domain_event(
            rt_run,
            trace_id,
            "instance.suspended",
            {
                "suspended_node_id": signal.suspended_node_id,
                "checkpoint_id": signal.checkpoint_id,
                "next_node_id": signal.next_node_id,
                "review_instructions": signal.review_instructions,
            },
        )
    except Exception as exc:
        run_finished_at = datetime.now(timezone.utc)
        registry.update_status(instance_id, "failed")
        registry.append_event(instance_id, {
            "event_id": str(uuid.uuid4()),
            "instance_id": instance_id,
            "event_type": "INSTANCE_FAILED",
            "node_id": None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": {"error": str(exc)},
        })

        # 领域事件：实例失败发布（CHG-62/DC-23 发布点 P3）
        rt_run = registry.get(instance_id)
        await _publish_instance_domain_event(
            rt_run,
            trace_id,
            "instance.failed",
            {
                "error_code": getattr(exc, "code", None)
                or WF_NODE_EXECUTION_FAILED,
                "error_message": summarize_text(str(exc)),
            },
        )

        if run_log_recorder is not None:
            try:
                run_log_recorder.end_run(
                    "failed",
                    finished_at=run_finished_at,
                    duration_ms=max(
                        0,
                        int((run_finished_at - run_started_at).total_seconds() * 1000),
                    ),
                    error={
                        "error_code": getattr(exc, "code", None)
                        or WF_NODE_EXECUTION_FAILED,
                        "error_message": str(exc),
                    },
                )
            except Exception:
                logging.warning(
                    "运行日志 end_run(failed) 接线失败: instance_id=%s", instance_id,
                    exc_info=True,
                )
    except asyncio.CancelledError:
        # 取消收尾（B2 嵌入 SDK取消端点，design §D1-5）：置 cancelled 终态 +
        # INSTANCE_CANCELLED 执行事件 + instance.cancelled 领域事件（发布点 P5）+
        # 运行日志终态记录（WARN 级，end_run 任意状态串安全）；随后必须
        # re-raise（asyncio 取消语义；CancelledError 为 BaseException 系，
        # except Exception 接不住，与挂起/失败分支互不干扰）
        run_cancelled_at = datetime.now(timezone.utc)
        registry.update_status(instance_id, "cancelled")
        registry.append_event(instance_id, {
            "event_id": str(uuid.uuid4()),
            "instance_id": instance_id,
            "event_type": "INSTANCE_CANCELLED",
            "node_id": None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": {},
        })
        rt_run = registry.get(instance_id)
        await _publish_instance_domain_event(
            rt_run,
            trace_id,
            "instance.cancelled",
            {"workflow_id": rt_run.definition_id},
        )
        if run_log_recorder is not None:
            try:
                run_log_recorder.end_run(
                    "cancelled",
                    finished_at=run_cancelled_at,
                    duration_ms=max(
                        0,
                        int(
                            (run_cancelled_at - run_started_at)
                            .total_seconds()
                            * 1000
                        ),
                    ),
                )
            except Exception:
                logging.warning(
                    "运行日志 end_run(cancelled) 接线失败: instance_id=%s",
                    instance_id,
                    exc_info=True,
                )
        raise
    finally:
        # 采集通道收尾：冲刷落盘；门面自建 sink 时由门面停后台线程（幂等）
        if run_log_recorder is not None:
            try:
                run_log_recorder.close()
            except Exception:
                logging.warning(
                    "运行日志 close 接线失败: instance_id=%s", instance_id,
                    exc_info=True,
                )


# =============================================================================
# SSE 事件生成器
# =============================================================================

async def event_generator(instance_id: str):
    """异步生成器：轮询注册表事件列表，以 SSE 格式推送新事件。"""
    _instance_registry.get(instance_id)
    index = 0
    while True:
        events = _instance_registry.get_events(instance_id)
        while index < len(events):
            event = events[index]
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            index += 1
        rt = _instance_registry._instances.get(instance_id)
        if rt is not None and rt.status in _TERMINAL_STATUSES and index >= len(events):
            yield f"data: {json.dumps({'event_type': 'STREAM_END', 'instance_id': instance_id}, ensure_ascii=False)}\n\n"
            break
        await asyncio.sleep(0.1)


# =============================================================================
# 路由函数
# =============================================================================

@router.post("/instances")
async def start_instance(body: StartInstanceBody, request: Request) -> dict:
    """启动工作流实例：解析 YAML → 编译 → 异步调度 → 返回 instance_id。"""
    if not body.yaml_text:
        raise HTTPException(status_code=422, detail="请提供 yaml_text（工作流 YAML 文本）")

    instance_id = str(uuid.uuid4())
    trace_id = str(uuid.uuid4())

    # 租户快照（T7.3）：启动瞬间取值，后台协程存活期不依赖请求对象（INV-11）
    tenant_id: Optional[str] = getattr(request.state, "tenant_id", None)

    # 并发配额闸门（roadmap B4，DC-B4-02）：并发活跃实例数达配额上限即
    # 429 拒绝（TenantQuotaExceededError → handler 映射）；唯一 await =
    # get_quota，count→check→raise→register 全同步块（事件循环单线程原子，
    # 超配额 100% 拒绝）；拒绝路径零副作用：不注册、不解析、不编译、不执行。
    # tenant_id 为 None（治理降级/cron 无租户）不闸门。
    quota_gate = get_quota_gate()
    if quota_gate is not None and tenant_id is not None:
        await quota_gate.ensure_capacity(
            tenant_id,
            _instance_registry.count_active_by_tenant(tenant_id),
        )

    rt = InstanceRuntime(
        instance_id=instance_id,
        definition_id=body.definition_id,
        tenant_id=tenant_id,
    )
    _instance_registry.register(rt)

    try:
        from orchestration_platform.workflow.yaml_workflow_parser import YamlWorkflowParser
        from orchestration_platform.engine.graph_compiler import GraphCompiler

        definition = YamlWorkflowParser().parse(body.yaml_text)
        compiled = await GraphCompiler().compile(definition)
        # 恢复上下文缓存（arch_review_v2 3.4，CHG-07）：编译产物与 trace 落注册表
        rt.compiled = compiled
        rt.trace_id = trace_id
    except HTTPException:
        _instance_registry.remove(instance_id)
        raise
    except Exception as exc:
        _instance_registry.remove(instance_id)
        raise HTTPException(status_code=422, detail=f"工作流解析/编译失败: {str(exc)}")

    # 业务类别三级回退（T7.3）：请求 trigger_type → TRIGGER 节点配置 → default
    business_category = _resolve_business_category(body, definition)
    rt.business_category = business_category

    # 运行日志采集门面（T7.3）：构造失败仅告警，本次运行零采集（INV-01）；
    # run_id=instance_id、trace_id 与实例同源（2.0.1 公共字段）
    run_log_recorder = None
    try:
        from orchestration_platform.observability.run_log.recorder import WorkflowRunLogger

        run_log_recorder = WorkflowRunLogger(
            run_id=instance_id,
            trace_id=trace_id,
            tenant_id=tenant_id,
            business_category=business_category,
            workflow_id=body.definition_id,
            definition_version=getattr(compiled, "definition_version", None)
            or body.definition_version,
        )
    except Exception:
        logging.warning(
            "运行日志采集器构造失败，本次运行不采集: instance_id=%s", instance_id,
            exc_info=True,
        )
        run_log_recorder = None

    try:
        from orchestration_platform.engine.scheduler import Scheduler

        # 构造初始共享状态（DC-1，INV-1）
        try:
            initial_state = SharedState(
                instance_id=instance_id,
                node_outputs=body.inputs or {},
            )
        except Exception as exc:
            _instance_registry.remove(instance_id)
            raise HTTPException(
                status_code=422,
                detail=f"初始状态构造失败: {type(exc).__name__}",
            )

        # 构造节点事件回调并注入 Scheduler（DC-3，变更点 C34-5）
        callback = NodeEventCallbackImpl(_instance_registry)
        # 检查点仓储接线（arch_review_v2 3.2，CHG-07/DC-08）：经仓储工厂取双后端
        # 实现（E2/E3 唯一接线点）；失败降级 None + 告警，执行链不中断
        scheduler = Scheduler(
            executor_registry=_executor_registry,
            checkpoint_repository=_acquire_checkpoint_repository(),
            event_callback=callback,
            run_log_recorder=run_log_recorder,
            domain_event_publisher=_domain_event_publisher,
        )

        task = asyncio.create_task(
            run_and_record(
                scheduler,
                compiled,
                initial_state,
                instance_id,
                trace_id,
                _instance_registry,
                run_log_recorder=run_log_recorder,
            )
        )
        rt.task = task
    except Exception as exc:
        _instance_registry.remove(instance_id)
        raise HTTPException(status_code=500, detail=f"启动执行引擎失败: {str(exc)}")

    return {
        "instance_id": instance_id,
        "status": "running",
        "started_at": rt.started_at,
    }


@router.get("/instances/{instance_id}")
async def get_instance(instance_id: str) -> dict:
    """查询实例状态。"""
    rt = _instance_registry.get(instance_id)
    result: dict = {
        "instance_id": rt.instance_id,
        "definition_id": rt.definition_id,
        "status": rt.status,
        "started_at": rt.started_at,
    }
    if rt.finished_at:
        result["finished_at"] = rt.finished_at
    return result


@router.get("/instances/{instance_id}/events")
async def get_instance_events(instance_id: str, include_voided: bool = True) -> list:
    """查询实例事件序列。"""
    return _instance_registry.get_events(instance_id)


@router.get("/instances/{instance_id}/events/stream")
async def stream_instance_events(instance_id: str):
    """SSE 事件流：以 text/event-stream 格式实时推送执行事件。"""
    _instance_registry.get(instance_id)
    return StreamingResponse(
        event_generator(instance_id),
        media_type="text/event-stream",
    )


@router.post("/instances/{instance_id}/cancel")
async def cancel_instance(instance_id: str) -> dict:
    """请求取消执行中的实例（B2 嵌入 SDK：取消能力后端落点）。

    契约（docs/sdk-contract.md §1-7）：
    - 未知实例 → 404（注册表既有 detail）；已终态或无可取消任务
      （task 缺失/已结束，含 waiting_review 挂起态）→ 409
      INSTANCE_CANCEL_CONFLICT（{code, message, details} 同构错误体）；
    - 运行中 → task.cancel() 投递取消，终态由 run_and_record 的
      CancelledError 分支落定（本响应不等待，返回请求时刻快照）；
    - 竞态容忍：任务恰在校验后完成时 task.cancel() 为 no-op，
      客户端以 GET /instances/{id} 轮询到的终态为准。
    """
    rt = _instance_registry.get(instance_id)

    if rt.status in _TERMINAL_STATUSES:
        raise InstanceStateConflictError(
            INSTANCE_CANCEL_CONFLICT,
            f"实例 {instance_id} 已处于终态 {rt.status}，无法取消",
        )
    if rt.task is None or rt.task.done():
        raise InstanceStateConflictError(
            INSTANCE_CANCEL_CONFLICT,
            f"实例 {instance_id} 当前无可取消的运行任务（状态 {rt.status}）",
        )

    rt.task.cancel()
    return {
        "instance_id": rt.instance_id,
        "definition_id": rt.definition_id,
        "status": rt.status,
        "started_at": rt.started_at,
    }


@router.get("/instances/{instance_id}/results")
async def get_instance_results(instance_id: str) -> dict:
    """查询实例执行结果（B2 嵌入 SDK：取结果端点）。

    契约（docs/sdk-contract.md §1-8）：
    - 未知实例 → 404；非终态 → 200 {outputs: null}（轮询友好，不设 409）；
    - 终态 → 200 {outputs, status, finished_at}；outputs 取
      run_and_record 缓存的终态共享状态 node_outputs（仅 succeeded
      且 final_state 非 None 有值；failed/cancelled/rolled_back 或
      重启后老实例 → {}）。
    """
    rt = _instance_registry.get(instance_id)

    if rt.status not in _TERMINAL_STATUSES:
        return {
            "instance_id": rt.instance_id,
            "status": rt.status,
            "outputs": None,
        }

    outputs: Dict[str, Any] = {}
    if rt.status == "succeeded" and rt.final_state is not None:
        node_outputs = getattr(rt.final_state, "node_outputs", None)
        if isinstance(node_outputs, dict):
            outputs = dict(node_outputs)
    return {
        "instance_id": rt.instance_id,
        "status": rt.status,
        "outputs": outputs,
        "finished_at": rt.finished_at,
    }


@router.get("/instances/{instance_id}/checkpoints")
async def get_checkpoints(instance_id: str) -> list:
    """查询实例检查点列表。"""
    return []


# =============================================================================
# 故障恢复（arch_review_v2 3.4，CHG-07，卡 A1 契约；损坏降级复用 E4）
# =============================================================================

async def _resolve_recovery_checkpoint(checkpoint_repo, rt: InstanceRuntime):
    """调既有 ExecutionInstanceService.recover_instance 取恢复位点（E4 复用）。

    latest_for_instance → is_available 校验 → 损坏 mark_unavailable →
    回退更早检查点：全部复用服务既有逻辑（application/execution_instance_service.py）。
    服务侧实例对象由路由注册表映射注入服务内存索引（B15 复活接线，
    服务 recover 流程仅消费 instance_id 与状态位）。
    """
    from orchestration_platform.application.execution_instance_service import (
        ExecutionInstanceService,
    )
    from orchestration_platform.domain.models.enums import InstanceStatus
    from orchestration_platform.domain.models.execution_instance import ExecutionInstance
    from orchestration_platform.engine.scheduler import Scheduler
    from orchestration_platform.persistence.repository_factory import RepositoryFactory

    workflow_repo = RepositoryFactory.create_workflow_repository()
    event_repo = RepositoryFactory.create_event_repository()
    service = ExecutionInstanceService(
        workflow_repo,
        checkpoint_repo,
        event_repo,
        Scheduler(
            executor_registry=_executor_registry,
            checkpoint_repository=checkpoint_repo,
        ),
    )
    service._instances[rt.instance_id] = ExecutionInstance(
        instance_id=rt.instance_id,
        definition_id=rt.definition_id,
        definition_version=1,
        status=InstanceStatus.FAILED,
        inputs={},
        trigger_type="manual",
        trace_id=rt.trace_id or "",
    )

    await service.recover_instance(rt.instance_id)
    return await checkpoint_repo.latest_for_instance(rt.instance_id)


@router.post("/instances/{instance_id}/recover")
async def recover_instance(instance_id: str) -> dict:
    """故障恢复：从最近可用检查点续跑实例（CHG-07，卡 A1 契约）。

    契约（卡 A1）：
    - 出参 {instance_id, resumed_from_checkpoint_id, status}；
    - INSTANCE_NOT_FOUND → 404；STATE_CHECKPOINT_CORRUPTED → 500（复用既有错误码）；
    - 不入 rollback_replay_router（Z-4，F4 属 W5–7 桩现状冻结）。
    """
    rt = _instance_registry.get(instance_id)

    if rt.compiled is None:
        raise HTTPException(
            status_code=409,
            detail=f"实例 {instance_id} 缺少可恢复的执行上下文",
        )
    if rt.task is not None and not rt.task.done():
        raise HTTPException(
            status_code=409,
            detail=f"实例 {instance_id} 仍在运行，禁止恢复",
        )

    checkpoint_repo = _acquire_checkpoint_repository()
    if checkpoint_repo is None:
        raise HTTPException(
            status_code=500, detail="检查点仓储不可用，无法恢复",
        )

    # 恢复位点解析（E4 损坏降级复用服务既有逻辑）
    try:
        checkpoint = await _resolve_recovery_checkpoint(checkpoint_repo, rt)
    except ResourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except DomainError as exc:
        if exc.code == STATE_CHECKPOINT_CORRUPTED:
            raise HTTPException(status_code=500, detail=str(exc))
        raise

    if checkpoint is None:
        raise HTTPException(
            status_code=500,
            detail=f"实例 {instance_id} 无可用检查点，无法恢复",
        )

    # HITL 挂起恢复分支（arch_review_v2_tasks_wave2 G2，CHG-62/DC-26 规则 6）：
    # 恢复位点检查点（latest_for_instance，round_index 最大 = 挂起点）的
    # completed_node_id 为人工审核门节点时，实例置 WAITING_REVIEW 而非直接
    # 续跑（交由 review 决策驱动，AC-01 重启可恢复）；非挂起实例零变化
    completed_node = (
        rt.compiled.get_node(checkpoint.completed_node_id)
        if checkpoint.completed_node_id
        else None
    )
    if (
        completed_node is not None
        and completed_node.node_type == NodeType.CONTROL_FLOW
        and getattr(
            getattr(completed_node, "configuration", None),
            "control_flow_kind",
            None,
        )
        == ControlFlowKind.HUMAN_REVIEW_GATE
    ):
        gate_config = getattr(completed_node, "configuration", None)
        _instance_registry.update_status(instance_id, "waiting_review")
        rt.suspended_checkpoint_id = checkpoint.checkpoint_id
        _instance_registry.append_event(instance_id, {
            "event_id": str(uuid.uuid4()),
            "instance_id": instance_id,
            "event_type": "INSTANCE_SUSPENDED",
            "node_id": checkpoint.completed_node_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": {
                "checkpoint_id": checkpoint.checkpoint_id,
                "next_node_id": checkpoint.next_node_id,
                "review_instructions": str(
                    getattr(gate_config, "review_instructions", None) or ""
                ),
                "timeout_action": str(
                    getattr(gate_config, "review_timeout_action", None) or ""
                ),
                "recovered": True,
            },
        })
        return {
            "instance_id": instance_id,
            "resumed_from_checkpoint_id": checkpoint.checkpoint_id,
            "status": "waiting_review",
        }

    # 重建 Scheduler 任务（resume_checkpoint 续跑，CHG-08）
    from orchestration_platform.engine.scheduler import Scheduler

    callback = NodeEventCallbackImpl(_instance_registry)
    scheduler = Scheduler(
        executor_registry=_executor_registry,
        checkpoint_repository=checkpoint_repo,
        event_callback=callback,
    )
    task = asyncio.create_task(
        run_and_record(
            scheduler,
            rt.compiled,
            checkpoint.state_snapshot,
            instance_id,
            rt.trace_id or "",
            _instance_registry,
            resume_checkpoint=checkpoint,
        )
    )
    rt.task = task

    return {
        "instance_id": instance_id,
        "resumed_from_checkpoint_id": checkpoint.checkpoint_id,
        "status": "running",
    }
