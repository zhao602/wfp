# =============================================================================
# 逐级聚合降噪器（engine/supervisor_orchestration/finding_aggregator.py）
#
# 职责：对子编排发现进行逐级聚合降噪，防止平方级噪声增长（G1/T219）。
# 设计意图（对应 spec 5.1.3/5.1.4、design D1/2.1.3.3）：
#   1. 去重——内容摘要哈希相同合并；
#   2. 限量——每子编排最多 top-K 条（K 可配置默认 10）；
#   3. 附来源——每条携带 sub_orchestrator_id，禁止匿名汇聚（A1.3.a）；
#   4. Supervisor 接收信息量 = Σ(直接下级聚合上限) = O(直接下级数×K)，
#      与全体 Agent 总数解耦（A1.4.a 线性增长）。
#
# 禁止 import langgraph。
# =============================================================================

from typing import List

from orchestration_platform.engine.supervisor_orchestration.models import (
    AggregatedFinding,
    FindingItem,
)


class FindingAggregator:
    """逐级聚合降噪器：去重 → 限量 → 附来源。

    构造参数：
        top_k —— 每子编排最多保留的发现项数（默认 10）
    """

    def __init__(self, top_k: int = 10) -> None:
        if top_k < 1:
            top_k = 1
        self._top_k = top_k

    @property
    def top_k(self) -> int:
        return self._top_k

    def aggregate(self, finding: AggregatedFinding) -> AggregatedFinding:
        """对单个子编排的聚合发现执行三步降噪。

        步骤：
            1. 去重——content_hash 相同的 FindingItem 仅保留首份；
            2. 限量——保留前 top_k 条（按原始顺序）；
            3. 附来源——每条 FindingItem 的 source_sub_orchestrator_id 保持不变。

        Returns:
            降噪后的 AggregatedFinding（同 sub_orchestration_id/sub_orchestrator_id）
        """
        # 步骤 1：去重（content_hash 相同仅保留首份）
        seen_hashes: set = set()
        deduped: List[FindingItem] = []
        for item in finding.items:
            if item.content_hash not in seen_hashes:
                seen_hashes.add(item.content_hash)
                deduped.append(item)

        # 步骤 2：限量（保留前 top_k 条）
        limited = deduped[: self._top_k]

        # 步骤 3：附来源（来源标识已在 FindingItem 中，保持不变）
        return AggregatedFinding(
            sub_orchestration_id=finding.sub_orchestration_id,
            sub_orchestrator_id=finding.sub_orchestrator_id,
            items=limited,
            success=finding.success,
            failure_reason=finding.failure_reason,
        )

    def aggregate_batch(
        self, findings: List[AggregatedFinding]
    ) -> List[AggregatedFinding]:
        """批量逐级聚合。

        对每个子编排的发现独立执行降噪，结果顺序与输入一致。
        Supervisor 接收信息量 = Σ(各子编排降噪后条数) ≤ len(findings) × top_k。
        """
        return [self.aggregate(f) for f in findings]

    def estimate_supervisor_input_size(
        self, num_sub_orchestrators: int
    ) -> int:
        """估算 Supervisor 接收信息量上界。

        Returns:
            num_sub_orchestrators × top_k（O(直接下级数×K)，与全体 Agent 总数解耦）
        """
        return num_sub_orchestrators * self._top_k
