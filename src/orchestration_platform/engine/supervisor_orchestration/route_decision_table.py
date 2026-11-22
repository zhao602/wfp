# =============================================================================
# YAML 路由决策表加载与确定性匹配（engine/supervisor_orchestration/route_decision_table.py）
#
# 职责：加载 YAML 格式的路由决策表，对子任务进行确定性路由匹配（G1/T217）。
# 设计意图（对应 spec 5.1.2、design D1/2.1.3.1）：
#   1. 决策表以 YAML 声明，含 version/default_route/routes[]/fallback_on_no_decision；
#   2. 确定性匹配：按 priority 降序 + 声明顺序稳定排序后逐条匹配关键词；
#   3. 同一输入永远得到同一决策序列（路径可预测可复现，A1.2.a）；
#   4. 复杂度 O(任务数×路由数)，杜绝平方级扫描（性能约束 4.1.1）。
#
# 禁止 import langgraph；全部路由决策在平台自有确定性控制路径上实现。
# =============================================================================

from typing import Any, Dict, List, Optional

from orchestration_platform.domain.errors import SUP_ROUTE_DECISION_FAILED, DomainError
from orchestration_platform.engine.supervisor_orchestration.models import RouteDecision


class RouteEntry:
    """单条路由声明：关键词匹配规则。

    属性：
        sub_orchestrator_id —— 目标子编排器标识
        match_keywords      —— 匹配关键词列表（任一命中即匹配）
        priority            —— 优先级（数值越大越优先）
        declaration_index   —— 声明顺序索引（稳定排序依据）
    """

    def __init__(
        self,
        sub_orchestrator_id: str,
        match_keywords: List[str],
        priority: int,
        declaration_index: int,
    ) -> None:
        self.sub_orchestrator_id = sub_orchestrator_id
        self.match_keywords = match_keywords
        self.priority = priority
        self.declaration_index = declaration_index

    def matches(self, task_description: str) -> List[str]:
        """检查任务描述是否命中本路由，返回匹配到的关键词列表。

        确定性匹配：关键词在描述中出现即命中（大小写不敏感）。
        """
        desc_lower = task_description.lower()
        matched: List[str] = []
        for kw in self.match_keywords:
            if kw.lower() in desc_lower:
                matched.append(kw)
        return matched


class RouteDecisionTable:
    """路由决策表：加载 YAML 并提供确定性路由匹配。

    决策表 YAML 结构（design 2.1.3.1）：
        version: "1.0"
        default_route: "generalist"
        fallback_on_no_decision: "default"   # 或 "fail"
        routes:
          - sub_orchestrator_id: "security_expert"
            match_keywords: ["安全", "漏洞"]
            priority: 100
          - sub_orchestrator_id: "data_analyst"
            match_keywords: ["数据", "分析"]
            priority: 80

    匹配算法：
        1. 按 priority 降序排序；priority 相同时按声明顺序（declaration_index）稳定排序；
        2. 逐条匹配关键词，首个命中产出 RouteDecision；
        3. 无命中按 fallback_on_no_decision 走 default_route 兜底或明确失败。
    """

    def __init__(
        self,
        version: str,
        default_route: str,
        fallback_on_no_decision: str,
        routes: List[RouteEntry],
    ) -> None:
        self.version = version
        self.default_route = default_route
        self.fallback_on_no_decision = fallback_on_no_decision
        # 预排序：priority 降序 + 声明顺序稳定（保证确定性）
        self._sorted_routes = sorted(
            routes,
            key=lambda r: (-r.priority, r.declaration_index),
        )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RouteDecisionTable":
        """从字典构造决策表（已解析的 YAML 结构）。

        结构非法、必填字段缺失、priority 类型错误时抛 DomainError(SUP_ROUTE_DECISION_FAILED)。
        """
        if not isinstance(data, dict):
            raise DomainError(
                SUP_ROUTE_DECISION_FAILED,
                "路由决策表结构非法：顶层非字典",
            )

        # 校验必填字段
        version = data.get("version")
        if not isinstance(version, str) or not version:
            raise DomainError(
                SUP_ROUTE_DECISION_FAILED,
                "路由决策表缺少必填字段 version 或类型错误",
            )

        default_route = data.get("default_route")
        if not isinstance(default_route, str) or not default_route:
            raise DomainError(
                SUP_ROUTE_DECISION_FAILED,
                "路由决策表缺少必填字段 default_route 或类型错误",
            )

        fallback = data.get("fallback_on_no_decision", "default")
        if fallback not in ("default", "fail"):
            raise DomainError(
                SUP_ROUTE_DECISION_FAILED,
                f"fallback_on_no_decision 取值非法：{fallback}（仅允许 'default' 或 'fail'）",
            )

        raw_routes = data.get("routes", [])
        if not isinstance(raw_routes, list):
            raise DomainError(
                SUP_ROUTE_DECISION_FAILED,
                "路由决策表 routes 字段非列表",
            )

        routes: List[RouteEntry] = []
        for idx, raw in enumerate(raw_routes):
            if not isinstance(raw, dict):
                raise DomainError(
                    SUP_ROUTE_DECISION_FAILED,
                    f"路由声明第 {idx} 条非字典结构",
                )
            sub_id = raw.get("sub_orchestrator_id")
            if not isinstance(sub_id, str) or not sub_id:
                raise DomainError(
                    SUP_ROUTE_DECISION_FAILED,
                    f"路由声明第 {idx} 条缺少 sub_orchestrator_id 或类型错误",
                )
            keywords = raw.get("match_keywords", [])
            if not isinstance(keywords, list) or not all(
                isinstance(k, str) for k in keywords
            ):
                raise DomainError(
                    SUP_ROUTE_DECISION_FAILED,
                    f"路由声明第 {idx} 条 match_keywords 非字符串列表",
                )
            priority = raw.get("priority", 0)
            if not isinstance(priority, int) or isinstance(priority, bool):
                raise DomainError(
                    SUP_ROUTE_DECISION_FAILED,
                    f"路由声明第 {idx} 条 priority 类型错误（须整数）",
                )
            routes.append(
                RouteEntry(
                    sub_orchestrator_id=sub_id,
                    match_keywords=keywords,
                    priority=priority,
                    declaration_index=idx,
                )
            )

        return cls(
            version=version,
            default_route=default_route,
            fallback_on_no_decision=fallback,
            routes=routes,
        )

    @classmethod
    def from_yaml_file(cls, path: str) -> "RouteDecisionTable":
        """从 YAML 文件加载决策表。

        延迟导入 yaml（仅加载时需要），避免在无 PyYAML 环境下导入失败。
        """
        try:
            import yaml
        except ImportError as exc:
            raise DomainError(
                SUP_ROUTE_DECISION_FAILED,
                f"PyYAML 不可用，无法加载路由决策表：{exc}",
            )
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except Exception as exc:
            raise DomainError(
                SUP_ROUTE_DECISION_FAILED,
                f"路由决策表 YAML 加载失败：{exc}",
            )
        return cls.from_dict(data)

    def decide(self, task_description: str) -> RouteDecision:
        """对任务描述进行确定性路由决策。

        同一输入永远得到同一决策（路径可预测可复现，A1.2.a）。
        复杂度 O(路由数)，单次匹配单遍扫描排序后的路由列表。

        Returns:
            RouteDecision —— 路由决策结果

        Raises:
            DomainError(SUP_ROUTE_DECISION_FAILED) —— 无匹配且 fallback 为 fail 时
        """
        for entry in self._sorted_routes:
            matched = entry.matches(task_description)
            if matched:
                return RouteDecision(
                    sub_orchestrator_id=entry.sub_orchestrator_id,
                    matched_keywords=matched,
                    decision_index=entry.declaration_index,
                )

        # 无命中：按 fallback 策略处置
        if self.fallback_on_no_decision == "default":
            return RouteDecision(
                sub_orchestrator_id=self.default_route,
                matched_keywords=[],
                decision_index=-1,
            )
        raise DomainError(
            SUP_ROUTE_DECISION_FAILED,
            f"路由决策无匹配且 fallback 策略为 fail：task='{task_description}'",
        )

    def decide_batch(self, task_descriptions: List[str]) -> List[RouteDecision]:
        """批量确定性路由决策。

        复杂度 O(任务数×路由数)，逐任务独立匹配，结果顺序与输入一致。
        """
        return [self.decide(t) for t in task_descriptions]

    def list_routes(self) -> List[Dict[str, Any]]:
        """列出全部路由声明（按排序后顺序），供调试与可追溯审查。"""
        return [
            {
                "sub_orchestrator_id": e.sub_orchestrator_id,
                "match_keywords": list(e.match_keywords),
                "priority": e.priority,
                "declaration_index": e.declaration_index,
            }
            for e in self._sorted_routes
        ]
