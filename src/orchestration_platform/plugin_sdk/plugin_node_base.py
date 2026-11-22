# =============================================================================
# 插件节点基类（plugin_sdk/plugin_node_base.py）
#
# 职责：定义第三方节点开发基类与生命周期契约。
# 设计意图（对应偏差修正 spec 5.2.1.1 / design 2.2.2.2）：
#   - setup：初始化钩子（每实例调用一次）
#   - execute：执行钩子（可多次调用）
#   - teardown：清理钩子（默认空实现，异常时仍保证调用）
#   - 契约自本版本起冻结
# =============================================================================

from abc import ABC, abstractmethod
from typing import Any, Dict


class PluginNodeBase(ABC):
    """插件节点基类。

    第三方开发者继承此类实现自定义节点。
    生命周期：setup（一次）→ execute（多次）→ teardown（一次）。

    契约自本版本起冻结，后续仅追加带默认实现的钩子。

    使用示例::

        class MyNode(PluginNodeBase):
            def setup(self, config: dict) -> None:
                self._api_key = config["api_key"]

            def execute(self, inputs: dict) -> dict:
                return {"result": self._api_key + inputs["query"]}

            def teardown(self) -> None:
                pass
    """

    @abstractmethod
    def setup(self, config: Dict[str, Any]) -> None:
        """初始化钩子（每实例调用一次）。

        参数：
            config：节点配置字典

        异常：
            异常时实例标记不健康，从实例池剔除。
        """
        ...

    @abstractmethod
    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """执行钩子（可多次调用）。

        参数：
            inputs：输入数据

        返回：
            输出数据

        约束：
            须无跨调用可变副作用依赖。
        """
        ...

    def teardown(self) -> None:
        """清理钩子（默认空实现）。

        在 execute 异常时仍保证被调用。
        """
        pass
