# =============================================================================
# 规范版本注册表（workflow/specification_version_registry.py）
#
# 职责：维护引擎支持的规范版本集合，判定给定版本号是否受支持。
# 设计意图（对应需求规格 5.1.3.2）：
#   版本高于支持上限时必须终止解析，且「不尝试降级解析」——避免用旧规则
#   误读新版本定义而引入隐蔽错误。故本注册表只做精确匹配判定。
# =============================================================================

from typing import List, Set, Tuple


def _parse_version(version: str) -> Tuple[int, ...]:
    """将 "1.0" 形式的版本号解析为可比较的整数元组 (1, 0)。

    解析失败返回空元组，调用方据此判定为不支持版本。
    """
    try:
        return tuple(int(part) for part in str(version).split("."))
    except (ValueError, AttributeError):
        return ()


class SpecificationVersionRegistry:
    """规范版本注册表：支持版本区间的唯一事实来源。"""

    # 第一阶段支持的规范版本集合；第二阶段追加 "1.1"
    SUPPORTED_VERSIONS: List[str] = ["1.0", "1.1"]

    def __init__(self, supported_versions: List[str] | None = None) -> None:
        """构造注册表。

        参数：
            supported_versions —— 自定义支持版本集合；缺省使用类级默认值。
        """
        self._supported_versions: List[str] = list(supported_versions or self.SUPPORTED_VERSIONS)
        self._supported_parsed: Set[Tuple[int, ...]] = {
            _parse_version(v) for v in self._supported_versions
        }

    def is_supported(self, version: str) -> bool:
        """判定版本号是否受支持（精确匹配，不做降级）。"""
        return _parse_version(version) in self._supported_parsed

    def supported_range_text(self) -> str:
        """返回人类可读的支持版本区间描述，用于错误提示。"""
        return "、".join(self._supported_versions)

    def highest_supported(self) -> str:
        """返回支持的最高版本号。"""
        return max(self._supported_versions, key=_parse_version) if self._supported_versions else ""

    def is_higher_than_supported(self, version: str) -> bool:
        """判定版本是否高于支持上限（用于给出更精确的错误提示）。"""
        parsed = _parse_version(version)
        if not parsed:
            return False
        highest = _parse_version(self.highest_supported())
        return parsed > highest
