# =============================================================================
# 节点执行器注册表（engine/node_executor_registry.py）
#
# 职责：按 node_type 登记与查找节点执行器实例。
# 设计意图（对应 design 2.2.2.5、spec 5.2.1）：
#   1. 调度器通过注册表获取执行器，不硬编码节点类型与执行器的绑定关系；
#   2. 新增节点类型只需实现 NodeExecutor 并调用 register 登记。
# =============================================================================

from typing import Dict, Optional

from orchestration_platform.domain.errors import NODE_TOOL_NOT_FOUND, DomainError
from orchestration_platform.domain.models.enums import NodeType
from orchestration_platform.engine.nodes.base import NodeExecutor


class NodeExecutorRegistry:
    """节点执行器注册表：按 NodeType 登记与查找执行器。"""

    def __init__(self) -> None:
        self._executors: Dict[NodeType, NodeExecutor] = {}

    def register(self, executor: NodeExecutor) -> None:
        """登记节点执行器。

        若同类型执行器已存在则覆盖（支持热替换，如测试时注入 mock）。
        """
        self._executors[executor.node_type] = executor

    def get_executor(self, node_type: NodeType) -> NodeExecutor:
        """按节点类型获取执行器。

        不存在时抛出 DomainError（NODE_TOOL_NOT_FOUND 复用为通用"执行器未注册"）。
        """
        executor = self._executors.get(node_type)
        if executor is None:
            raise DomainError(
                NODE_TOOL_NOT_FOUND,
                f"节点类型 {node_type.value} 的执行器未注册",
            )
        return executor

    def has_executor(self, node_type: NodeType) -> bool:
        """检查是否已登记指定类型的执行器。"""
        return node_type in self._executors

    def registered_types(self) -> list:
        """返回已登记的全部节点类型。"""
        return list(self._executors.keys())

    def register_phase2_executors(
        self,
        workflow_engine=None,
        workflow_repository=None,
        event_repository=None,
    ) -> None:
        """登记第二阶段新增的节点执行器（子工作流 + 动态注入）。

        在第一阶段执行器登记后调用，追加第二阶段能力。
        数据转换节点执行器由 T30 单独登记。
        """
        from orchestration_platform.engine.nodes.subworkflow_node_executor import (
            SubWorkflowNodeExecutor,
        )
        from orchestration_platform.engine.nodes.dynamic_injector_node_executor import (
            DynamicInjectorNodeExecutor,
        )

        self.register(
            SubWorkflowNodeExecutor(
                workflow_engine=workflow_engine,
                workflow_repository=workflow_repository,
                event_repository=event_repository,
            )
        )
        self.register(
            DynamicInjectorNodeExecutor(
                event_repository=event_repository,
            )
        )
        # 数据转换节点执行器
        from orchestration_platform.engine.nodes.data_transform_node_executor import (
            DataTransformNodeExecutor,
        )
        self.register(DataTransformNodeExecutor())

        # 输出展示节点执行器（零外部依赖，无参构造）
        from orchestration_platform.engine.nodes.output_display_node_executor import (
            OutputDisplayNodeExecutor,
        )
        self.register(OutputDisplayNodeExecutor())

        # 向量知识库节点执行器（零外部依赖，无参构造；vector_knowledge_base_node T3.4/CHG-06）
        from orchestration_platform.engine.nodes.vector_knowledge_base_node_executor import (
            VectorKnowledgeBaseNodeExecutor,
        )
        self.register(VectorKnowledgeBaseNodeExecutor())

    def register_supervisor_executor(
        self,
        decision_table=None,
        state_store=None,
        max_sub_orchestrators: int = 20,
        retry_max: int = 2,
        agent_executor=None,
    ) -> None:
        """登记监督者编排器节点执行器（第五阶段 G1/T220）。

        开关控制：仅在 supervisor_orchestration_enabled=True 时调用此方法；
        开关关闭时不注册，SUPERVISOR 节点类型按未知节点类型拦截（A1.6.a）。
        与 SubWorkflowNodeExecutor 完全平行、互不引用（D1）。
        """
        from orchestration_platform.engine.supervisor_orchestration.node_executor import (
            SupervisorNodeExecutor,
        )

        self.register(
            SupervisorNodeExecutor(
                decision_table=decision_table,
                state_store=state_store,
                max_sub_orchestrators=max_sub_orchestrators,
                retry_max=retry_max,
                agent_executor=agent_executor,
            )
        )
