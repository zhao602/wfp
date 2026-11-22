# TODO(tech-debt): B档候选孤儿（族群 B11），待 APM 链路追踪专项排查确认无调用后清理，见 docs/tech-debt-ledger.md
# =============================================================================
# 节点包加载器抽象（engine/plugin_nodes/abstractions/node_package_loader.py）
#
# 职责：定义节点包加载器接口契约。
# 设计意图（对应 design 2.2.2.2）：
#   - load_package：加载并安装节点包
#   - list_installed：列出已安装节点（含健康标记）
#   - unload_package：卸载节点包（逐实例 teardown 后移除注册）
# =============================================================================

from typing import List, Protocol, runtime_checkable


@runtime_checkable
class INodePackageLoader(Protocol):
    """节点包加载器抽象。

    前置条件：package_path 符合打包规范（manifest + 实现文件）。
    后置条件：manifest 校验通过、依赖可解析、节点实例完成 setup；
              单节点失败仅该节点不可用，不影响其余节点（spec 5.2.1.3）。
    异常映射：NODE_MANIFEST_INVALID / NODE_PACKAGE_INVALID /
              NODE_DEPENDENCY_MISSING / NODE_LIFECYCLE_ERROR。
    """

    def load_package(self, package_path: str):
        """加载并安装节点包，返回注册信息。"""
        ...

    def list_installed(self) -> List:
        """列出已安装节点（含健康标记与版本）。"""
        ...

    def unload_package(self, node_name: str) -> None:
        """卸载节点包：逐实例调用 teardown 后移除注册。"""
        ...
