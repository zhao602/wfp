# =============================================================================
# NL 意图解析器抽象接口（governance/nl_orchestration/abstractions/nl_intent_parser.py）
#
# 职责：定义自然语言意图解析的统一契约。
# 设计意图（对应 design 2.2.2.1 / 模块A NLIntentParser）：
#   - 输入：自然语言指令 + 意图类型 + 既有工作流定义（修改意图时提供）
#   - 输出：结构化意图中间表示 ParsedIntent
# =============================================================================

from typing import Optional, Protocol, runtime_checkable

from orchestration_platform.domain.models.enums import IntentType
from orchestration_platform.domain.models.workflow_definition import WorkflowDefinition
from orchestration_platform.governance.nl_orchestration.models import ParsedIntent


@runtime_checkable
class INLIntentParser(Protocol):
    """自然语言意图解析器抽象接口。"""

    async def parse(
        self,
        directive: str,
        intent_type: IntentType,
        target_definition: Optional[WorkflowDefinition] = None,
        feedback: Optional[str] = None,
    ) -> ParsedIntent:
        """解析自然语言指令为结构化意图。

        前置条件：directive 非空且已通过注入防护校验。
        后置条件：返回 ParsedIntent，含节点规格列表与边规格列表（创建意图）
                 或修改操作列表（修改意图）。
        参数 feedback：上一轮校验失败的问题清单（自纠错重试时提供，None 为首轮）。
        异常映射：NL_PARSE_FAILED（LLM 不可用或返回无法解析）。
        """
        ...
