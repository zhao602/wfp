# =============================================================================
# 静态合规校验分流映射（engine/nodes/code_execution/static_check.py）
#
# 职责：将既有 custom_code_guard 的静态合规结论确定性分流为执行期错误码
# （llm_code_nodes_v2 任务 3.2 / DC-03 / FR-21/29 / AC-21/29 / FR-33 同因同码）。
# 设计意图：
#   1. 复用不修改：直接消费 validate_custom_code 的 CustomCodeVerdict，
#      custom_code_guard.py 零改动（ZD-10 / G-09）；
#   2. 确定性分流：findings 首条命中守卫语法失败消息形态
#      （「脚本语法解析失败：<msg>（第 N 行）」）→ CODE_SYNTAX_ERROR
#      （消息含行号）；其余 findings → CODE_BANNED_CONSTRUCT
#      （消息含命中项清单，不含脚本全文）；
#   3. 纯函数：无随机、无环境分支、无 IO，同因同码（FR-33）；
#   4. 消息卫生：命中项至多展示 5 条 + 余量计数（与加载期校验器
#      CODE_FINDINGS_DISPLAY_LIMIT 口径一致），不含脚本全文。
# =============================================================================

from typing import Optional, Tuple

from orchestration_platform.domain.errors import (
    CODE_BANNED_CONSTRUCT,
    CODE_SYNTAX_ERROR,
)
from orchestration_platform.engine.nodes.data_transform_ops.custom_code_guard import (
    CustomCodeVerdict,
)

# 守卫语法失败消息形态前缀（custom_code_guard.validate_custom_code 的
# ast.parse 失败分支输出，字形冻结为分流判据）
SYNTAX_FINDING_PREFIX = "脚本语法解析失败："

# 命中项消息展示上限（与加载期校验器 CODE_FINDINGS_DISPLAY_LIMIT 同口径）
FINDINGS_DISPLAY_LIMIT = 5


def classify_static_verdict(verdict: CustomCodeVerdict) -> Optional[Tuple[str, str]]:
    """静态合规结论分流映射（compliant=True → None；否则 → (错误码, 消息)）。

    - 语法失败形态（findings 首条前缀命中）→ (CODE_SYNTAX_ERROR, 消息含行号)；
    - 其余（敏感 import / 禁用调用 / 逃逸属性 / 空脚本 / 语言不支持）
      → (CODE_BANNED_CONSTRUCT, 消息含命中项清单与计数，不含脚本全文)。
    """
    if verdict.compliant:
        return None

    findings = list(verdict.findings)
    if findings and findings[0].startswith(SYNTAX_FINDING_PREFIX):
        return CODE_SYNTAX_ERROR, findings[0]

    shown = "；".join(findings[:FINDINGS_DISPLAY_LIMIT])
    if len(findings) > FINDINGS_DISPLAY_LIMIT:
        shown = f"{shown}（等 {len(findings) - FINDINGS_DISPLAY_LIMIT} 项）"
    return (
        CODE_BANNED_CONSTRUCT,
        f"脚本静态合规校验未通过（共 {len(findings)} 项命中）：{shown}",
    )
