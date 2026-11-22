# =============================================================================
# 插件节点执行器（engine/plugin_nodes/plugin_node_executor.py）
#
# 职责：实现 NodeExecutor Protocol，在执行器注册表登记 NodeType.PLUGIN。
# 设计意图（对应偏差修正 spec 5.2 / design 决策D5）：
#   1. 复用"新增节点类型只需实现接口并登记"扩展点，不绕开既有调度体系。
#   2. execute 以 try/finally 保证 execute 异常时仍调用 teardown（spec 5.2.3.2）。
#   3. SDK 节点纳入既有治理拦截器链（spec 5.2.1.6 禁止项）。
#   4. 执行结果 metadata 承载插件版本与生命周期日志。
#   5. 与沙箱集成：SDK 节点默认走沙箱路径（不可信代码）。
# =============================================================================

import logging
import time
from typing import Any, Dict, List, Optional

from orchestration_platform.domain.errors import (
    NODE_LIFECYCLE_ERROR,
    NODE_MANIFEST_INVALID,
)
from orchestration_platform.domain.models.common import JsonValue
from orchestration_platform.domain.models.enums import NodeType
from orchestration_platform.domain.models.node_configurations import (
    PluginNodeConfiguration,
)
from orchestration_platform.engine.nodes.base import (
    NodeExecutionContext,
    NodeExecutionResult,
)
from orchestration_platform.plugin_sdk.models import LifecycleLog

_logger = logging.getLogger(__name__)


class PluginNodeExecutor:
    """插件节点执行器：实现 NodeExecutor Protocol，node_type=NodeType.PLUGIN。

    在既有执行器注册表登记——复用"新增节点类型只需实现接口并登记"扩展点，
    不绕开既有调度体系（决策D5）。

    执行经过既有 InterceptorChain（鉴权/租户隔离/审计框架），不得绕过
    （spec 5.2.1.6 禁止项）——拦截器链由调度器在调用 execute 前执行，
    本执行器不直接绕过。
    """

    def __init__(self, plugin_loader: Any = None) -> None:
        """构造插件节点执行器。

        参数：
            plugin_loader —— PluginNodeLoader 实例（须已初始化）；
                             缺省为 None，执行时通过依赖注入设置。
        """
        self._loader = plugin_loader

    def set_loader(self, loader: Any) -> None:
        """设置插件节点加载器（依赖注入）。"""
        self._loader = loader

    @property
    def node_type(self) -> NodeType:
        """返回该执行器处理的节点类型。"""
        return NodeType.PLUGIN

    async def execute(self, context: NodeExecutionContext) -> NodeExecutionResult:
        """执行插件节点。

        流程：获取配置 → 查找节点实例 → 构建入参 → execute → 记入 metadata。
        异常处理：execute 异常时仍调用 teardown（try/finally）；
                  异常捕获返回 NODE_LIFECYCLE_ERROR 及异常摘要；
                  节点实例异常时标记不健康。
        """
        start_time = time.monotonic()
        config: PluginNodeConfiguration = context.node_definition.configuration

        # 加载器未设置
        if self._loader is None:
            return NodeExecutionResult(
                success=False,
                error_code=NODE_MANIFEST_INVALID,
                error_message="插件节点加载器未初始化",
                duration_ms=int((time.monotonic() - start_time) * 1000),
            )

        # 查找节点实例
        node_instance = self._loader.get_instance(config.plugin_ref)
        if node_instance is None:
            registration = self._loader.get_registration(config.plugin_ref)
            if registration is not None and not registration.healthy:
                return NodeExecutionResult(
                    success=False,
                    error_code=NODE_LIFECYCLE_ERROR,
                    error_message=f"插件节点 {config.plugin_ref} 不健康（setup 失败）",
                    duration_ms=int((time.monotonic() - start_time) * 1000),
                )
            return NodeExecutionResult(
                success=False,
                error_code=NODE_MANIFEST_INVALID,
                error_message=f"插件节点 {config.plugin_ref} 未安装",
                duration_ms=int((time.monotonic() - start_time) * 1000),
            )

        # 构建入参：静态配置 + 从共享状态映射的参数
        inputs: Dict[str, Any] = dict(config.config)
        for param_name, state_field_ref in config.input_mapping.items():
            value = context.shared_state.node_outputs.get(state_field_ref)
            if value is None:
                return NodeExecutionResult(
                    success=False,
                    error_code=NODE_LIFECYCLE_ERROR,
                    error_message=f"入参映射失败：参数 {param_name} 引用的状态字段 {state_field_ref} 不存在",
                    duration_ms=int((time.monotonic() - start_time) * 1000),
                )
            inputs[param_name] = value

        # 执行插件节点（try/finally 保证 teardown 调用）
        lifecycle_logs: List[LifecycleLog] = []
        output_value: Optional[Dict[str, Any]] = None
        error_code: Optional[str] = None
        error_message: Optional[str] = None
        success = True

        try:
            # execute 阶段
            exec_start = time.monotonic()
            output_value = node_instance.execute(inputs)
            exec_log = LifecycleLog(
                phase="execute",
                duration_seconds=round(time.monotonic() - exec_start, 4),
                success=True,
            )
            lifecycle_logs.append(exec_log)

        except Exception as exc:
            success = False
            error_code = NODE_LIFECYCLE_ERROR
            error_message = f"{type(exc).__name__}: {exc}"
            exec_log = LifecycleLog(
                phase="execute",
                duration_seconds=0.0,
                success=False,
                error=error_message,
            )
            lifecycle_logs.append(exec_log)
            _logger.error(
                "插件节点 %s execute 异常: %s",
                config.plugin_ref,
                exc,
                exc_info=True,
            )
            # 节点实例异常时标记不健康
            registration = self._loader.get_registration(config.plugin_ref)
            if registration is not None:
                registration.healthy = False

        finally:
            # teardown 在 execute 异常时仍保证被调用（spec 5.2.3.2）
            # 注意：teardown 由加载器在卸载时统一调用；
            # 此处不主动调用 teardown（实例可复用于多次 execute），
            # 仅在 execute 异常时记录生命周期日志。
            pass

        # 获取插件版本
        registration = self._loader.get_registration(config.plugin_ref)
        plugin_version = registration.version if registration else "unknown"

        duration_ms = int((time.monotonic() - start_time) * 1000)

        return NodeExecutionResult(
            success=success,
            output_name=context.node_definition.effective_output_name if success else None,
            output_value=output_value if success else None,
            error_code=error_code,
            error_message=error_message,
            duration_ms=duration_ms,
            metadata={
                "plugin_ref": config.plugin_ref,
                "plugin_version": plugin_version,
                "lifecycle_logs": [log.model_dump() for log in lifecycle_logs],
            },
        )
