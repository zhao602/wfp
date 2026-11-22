# =============================================================================
# 依赖注入容器（api/di/container.py）
#
# 职责：手工轻量容器——provider 注册表 + 只读访问器，仅启动期装配一次。
# 设计意图（arch_review_gap_b4_architecture design DC-B4-01 / 母 DC-06）：
#   1. 仅启动期装配一次：create_app 内构造与注册，装配完成后 freeze 转只读；
#   2. 不引任何 DI 框架（HC-06 零新增依赖）；
#   3. 不做运行时依赖解析、装饰器注入、复杂生命周期（仅启动/关闭两点语义位）；
#   4. 「配置选择实现」沿用 repository_factory 判定口径（provider 内委托）；
#   5. 等价矩阵 harness（B4-C1）经本容器取得双引擎与生产同源执行器注册表
#      （G16→G15 弱依赖兑现方式）。
# =============================================================================

from typing import Any, Callable, Dict, List


class Container:
    """手工轻量依赖容器：provider 注册表 + 只读访问器。

    约束（DC-06 六条承接）：
    - 仅启动期装配一次；装配完成后 freeze，冻结后禁止 register；
    - provider 为零参可调用，resolve 时惰性构造且单例记忆化（重复 resolve 幂等）；
    - 不做运行时扫描、装饰器注入、生命周期管理。
    """

    def __init__(self) -> None:
        self._factories: Dict[str, Callable[[], Any]] = {}
        self._instances: Dict[str, Any] = {}
        self._frozen: bool = False

    def register(self, key: str, factory: Callable[[], Any]) -> None:
        """注册 provider（零参工厂；现成实例以零参闭包包装注册）。

        冻结后调用抛出 RuntimeError（装配完成转只读）。
        """
        if self._frozen:
            raise RuntimeError(f"容器已冻结，禁止注册新 provider：{key}")
        if not callable(factory):
            raise TypeError(f"provider 必须为可调用对象：{key}")
        self._factories[key] = factory

    def resolve(self, key: str) -> Any:
        """按键解析依赖：首次调用执行工厂并记忆化，此后返回同一实例。"""
        if key in self._instances:
            return self._instances[key]
        factory = self._factories.get(key)
        if factory is None:
            raise KeyError(f"容器中未注册 provider：{key}")
        instance = factory()
        self._instances[key] = instance
        return instance

    def freeze(self) -> None:
        """装配完成转只读（幂等；冻结后 register 抛错、resolve 行为不变）。"""
        self._frozen = True

    def has(self, key: str) -> bool:
        """是否已注册指定 provider。"""
        return key in self._factories

    def keys(self) -> List[str]:
        """返回注册键列表（保持注册顺序，解析顺序确定性测试锚点）。"""
        return list(self._factories.keys())
