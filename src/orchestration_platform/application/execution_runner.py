# =============================================================================
# 执行运行器（application/execution_runner.py）
#
# 职责：工作流后台执行的进程内运行时——实例注册表、执行协程、统一提交器。
# 设计意图（roadmap-v3 中期窗口 M1，F1 执行队列与提交路径统一）：
#   1. run_and_record / InstanceRegistry / InstanceRuntime /
#      NodeEventCallbackImpl / 执行器注册表构建器自 execution_instance_router
#      迁入本模块（plan M1 改动 3）——router 只留 HTTP 关注点，原符号经
#      router 顶部 from-import 转发保持全部既有导入路径与 monkeypatch 语义；
#   2. 领域事件发布器状态仍由 router 模块持有（_domain_event_publisher 全局
#      + set_domain_event_publisher），本模块经 bind_domain_event_publisher_source
#      注入读取源（调用期读 router 命名空间）——测试对 router 模块属性的
#      monkeypatch.setattr 在 run_and_record 内保持可见（零测试修改）；
#   3. ExecutionSubmitter 统一五处 spawn 提交路径（plan M1 改动 4）：
#      in_process（默认）= asyncio.create_task(run_and_record(...)) 与历史
#      行为完全等价；worker = 入队 execution_job（消费侧 M2 落地）；
#   4. 依赖方向：application → domain/engine/persistence，绝不 import
#      api（解循环，plan D5-2）；router → application 单向。
# =============================================================================

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from fastapi import HTTPException

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.errors import (
    INSTANCE_NOT_FOUND,
    NODE_TOOL_ARGUMENT_MISMATCH,
    WF_NODE_EXECUTION_FAILED,
    DomainError,
    WorkflowValidationError,
)
from orchestration_platform.domain.models.common import JsonValue
from orchestration_platform.domain.models.domain_event import (
    DEFAULT_TENANT_ID,
    DomainEvent,
    summarize_text,
)
from orchestration_platform.domain.models.enums import NodeType
from orchestration_platform.domain.models.execution_job import ExecutionJob
from orchestration_platform.engine.abstractions.domain_event_publisher import (
    IDomainEventPublisher,
)
from orchestration_platform.engine.scheduler import WorkflowSuspendedSignal
from orchestration_platform.governance.metering.usage_recorder import (
    get_usage_recorder,
)
from orchestration_platform.integrations.abstractions.external_clients import (
    IToolInvoker,
)
from orchestration_platform.observability.run_log.fields import (
    BUSINESS_CATEGORY_DEFAULT,
)
from orchestration_platform.persistence.abstractions.execution_queue import (
    IExecutionQueue,
)


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


# =============================================================================
# 领域事件发布器读取源（M1 hook：发布器状态仍由 router 模块持有）
# =============================================================================

_domain_event_publisher_source: Optional[
    Callable[[], Optional[IDomainEventPublisher]]
] = None


def bind_domain_event_publisher_source(
    source: Callable[[], Optional[IDomainEventPublisher]],
) -> None:
    """注入领域事件发布器读取源（router 模块装配期调用）。

    发布器状态与 set_domain_event_publisher 保留在 router 模块（历史契约），
    本模块经 source 在调用期读取——router 绑定
    bind_domain_event_publisher_source(lambda: _domain_event_publisher)
    后，测试对 router 模块属性的 monkeypatch.setattr 在 run_and_record
    内保持可见（零测试修改，plan M1 兼容优先）。
    """
    global _domain_event_publisher_source
    _domain_event_publisher_source = source


def _current_domain_event_publisher() -> Optional[IDomainEventPublisher]:
    """经绑定读取源解析当前发布器；未绑定/读取异常返回 None（旁路）。"""
    if _domain_event_publisher_source is None:
        return None
    try:
        return _domain_event_publisher_source()
    except Exception:
        logging.warning(
            "领域事件发布器读取失败（旁路）", exc_info=True,
        )
        return None


# =============================================================================
# 执行器注册表构造（延迟导入 + 降级 stub，保证模块导入不失败）
# =============================================================================

def _build_llm_client():
    """构造 LLM 客户端；配置缺失或构造失败时返回 None（DC-2）。

    实现已上移到 integrations/llm/factory.build_llm_client（唯一权威），
    本函数仅作执行运行器侧别名保留，避免既有调用点与测试导入路径变更。
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
    同法，INV-1）；同标识多版本取 version 字段序最大，保证与工具清单
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

async def _publish_instance_domain_event(
    rt: InstanceRuntime,
    trace_id: str,
    event_type: str,
    payload: Dict[str, Any],
) -> None:
    """发布实例级领域事件；发布器缺失或发布失败时静默旁路（DC-31）。

    构造/发布异常独立吞掉并记录告警，绝不影响执行主链（INV-01）。
    发布器经 bind_domain_event_publisher_source 绑定的读取源在调用期解析
    （M1：状态持有方为 router 模块，monkeypatch 语义保持）。
    """
    publisher = _current_domain_event_publisher()
    if publisher is None:
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
        await publisher.publish_domain_event(event)
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
# 统一提交器（五处 spawn 收口，plan M1 改动 4）
# =============================================================================

def build_execution_queue() -> IExecutionQueue:
    """按 storage_backend 构造执行队列实例（延迟导入，零新增依赖）。"""
    backend = get_settings().storage_backend
    if backend == "local_file":
        from orchestration_platform.persistence.local_file.local_file_execution_queue import (
            LocalFileExecutionQueue,
        )
        return LocalFileExecutionQueue()
    if backend == "postgres":
        from orchestration_platform.persistence.postgres.postgres_execution_queue import (
            PostgresExecutionQueue,
        )
        return PostgresExecutionQueue()
    raise ValueError(f"不支持的存储后端：{backend}")


class ExecutionSubmitter:
    """统一提交器：按 execution_mode 分支提交执行。

    - in_process（默认）：asyncio.create_task(run_and_record(...))，
      与历史五处 spawn 行为完全等价（plan D1 兼容优先）；
    - worker：构造 ExecutionJob 入队（幂等键 = instance_id），
      返回 None——rt.task 不落值，执行由独立 worker 进程认领（M2 落地）。
    """

    def __init__(self, queue: Optional[IExecutionQueue] = None) -> None:
        """构造提交器。

        参数：
            queue —— 执行队列实例（测试注入用）；缺省 worker 模式下按
                     storage_backend 现场构造。
        """
        self._queue = queue

    def _submit_in_process(
        self,
        scheduler,
        compiled,
        initial_state,
        instance_id: str,
        trace_id: str,
        registry: InstanceRegistry,
        run_log_recorder=None,
        resume_checkpoint=None,
        trigger_source: str = "manual",
        rt: Optional[InstanceRuntime] = None,
    ) -> asyncio.Task:
        """in_process 直达路径：create_task(run_and_record(...)) + rt.task
        落值（同步无 await，与历史 spawn 逐拍等价）。"""
        task = asyncio.create_task(
            run_and_record(
                scheduler,
                compiled,
                initial_state,
                instance_id,
                trace_id,
                registry,
                run_log_recorder=run_log_recorder,
                resume_checkpoint=resume_checkpoint,
                trigger_source=trigger_source,
            )
        )
        if rt is not None:
            rt.task = task
        return task

    async def submit(
        self,
        scheduler,
        compiled,
        initial_state,
        instance_id: str,
        trace_id: str,
        registry: InstanceRegistry,
        run_log_recorder=None,
        resume_checkpoint=None,
        trigger_source: str = "manual",
        rt: Optional[InstanceRuntime] = None,
        yaml_text: Optional[str] = None,
    ) -> Optional[asyncio.Task]:
        """提交执行：in_process 创建后台任务并落 rt.task；worker 入队返回 None。

        yaml_text（M2）：worker 模式随作业携带工作流 YAML 文本，供 worker
        进程重解析编译；缺省时 worker 回退工作流仓储按 definition_id+version
        取定义。in_process 路径不消费该参数（零行为变化）。
        """
        if get_settings().execution_mode == "worker":
            queue = self._queue if self._queue is not None else build_execution_queue()
            initial_inputs = dict(
                getattr(initial_state, "node_outputs", None) or {}
            )
            job = ExecutionJob(
                job_id=str(uuid.uuid4()),
                instance_id=instance_id,
                tenant_id=getattr(rt, "tenant_id", None),
                definition_id=getattr(rt, "definition_id", "") or "",
                definition_version=getattr(compiled, "definition_version", None),
                yaml_text=yaml_text,
                initial_inputs=initial_inputs,
                trigger_source=trigger_source,
                trace_id=trace_id,
                resume_checkpoint_id=getattr(
                    resume_checkpoint, "checkpoint_id", None
                ),
                idempotency_key=instance_id,
            )
            await queue.enqueue(job)
            return None
        return self._submit_in_process(
            scheduler,
            compiled,
            initial_state,
            instance_id,
            trace_id,
            registry,
            run_log_recorder=run_log_recorder,
            resume_checkpoint=resume_checkpoint,
            trigger_source=trigger_source,
            rt=rt,
        )

    def submit_sync(
        self,
        scheduler,
        compiled,
        initial_state,
        instance_id: str,
        trace_id: str,
        registry: InstanceRegistry,
        run_log_recorder=None,
        resume_checkpoint=None,
        trigger_source: str = "manual",
        rt: Optional[InstanceRuntime] = None,
    ) -> Optional[asyncio.Task]:
        """同步提交入口（同步回调场景，M1 review approve 唤醒链）。

        in_process 直达 create_task（与历史行为逐拍等价——rt.task 同步
        落值，调用方无需事件循环让拍即可断言）；worker 模式经运行中的
        事件循环调度异步 submit 驱动入队，返回 None（rt.task 不落值，
        执行由 worker 认领，M2 落地；无运行循环时如实抛错不静默丢弃）。
        """
        if get_settings().execution_mode == "worker":
            loop = asyncio.get_running_loop()
            loop.create_task(
                self.submit(
                    scheduler,
                    compiled,
                    initial_state,
                    instance_id,
                    trace_id,
                    registry,
                    run_log_recorder=run_log_recorder,
                    resume_checkpoint=resume_checkpoint,
                    trigger_source=trigger_source,
                    rt=rt,
                )
            )
            return None
        return self._submit_in_process(
            scheduler,
            compiled,
            initial_state,
            instance_id,
            trace_id,
            registry,
            run_log_recorder=run_log_recorder,
            resume_checkpoint=resume_checkpoint,
            trigger_source=trigger_source,
            rt=rt,
        )


_execution_submitter = ExecutionSubmitter()
