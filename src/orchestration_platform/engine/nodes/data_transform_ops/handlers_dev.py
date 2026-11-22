# =============================================================================
# 开发者兜底操作处理器（engine/nodes/data_transform_ops/handlers_dev.py）
#
# 职责：承载 data_transform 操作链的开发者兜底操作处理器
#       （expand_data_transform_ops design DC-08 / CHG-B06，T5.4/T5.5）：
#   - sampling（FR-18 / AC-16）：head（前 N 行，缺省确定性）/ random
#     （随机抽样，可重现性靠 random_seed，不以确定性作验收）；
#     sample_n / sample_percent 二选一（sample_n 优先），越界拒绝；
#   - custom_code（FR-17 / AC-15）：**仅登记 + 静态合规校验，不执行**
#     （Python/JS 均不执行）；执行含脚本步的节点 → fail-fast
#     DATA_TRANSFORM_SCRIPT_NOT_EXECUTABLE（含步骤定位），**绝不声称执行成功**；
#     录入期静态校验由 custom_code_guard.validate_custom_code 承担；
#     切换口径须回 OP-03 重裁。
# =============================================================================

import random
from typing import Any, Dict, List

from orchestration_platform.domain.errors import (
    DATA_TRANSFORM_INVALID_STEP_PARAMS,
    DATA_TRANSFORM_SCRIPT_NOT_EXECUTABLE,
)
from orchestration_platform.domain.models.enums import DataTransformOperation
from orchestration_platform.engine.nodes.data_transform_ops.registry import (
    StepContext,
    StepOutcome,
    register,
)


class SamplingHandler:
    """数据抽样操作处理器（T5.5 / FR-18 / AC-16）。

    sample_mode：head（缺省，取前 N 行，确定性）/ random（随机抽样）。
    规模：sample_n（条数）与 sample_percent（0<比例≤100）二选一，
    sample_n 优先；两者均缺 → 配置期拒绝；比例换算行数四舍五入。
    可重现性：random 模式由 random_seed 保证（相同输入与种子可重现；
    无种子每次不同，不以确定性作验收）；head 模式天然确定。
    """

    operation = DataTransformOperation.SAMPLING
    degradable = False

    def apply(self, ctx: StepContext) -> StepOutcome:
        params = ctx.step.params
        mode = params.sample_mode or "head"
        if mode not in ("head", "random"):
            return StepOutcome.failed(
                DATA_TRANSFORM_INVALID_STEP_PARAMS,
                f"sampling sample_mode 取值不支持：{mode}",
            )

        sample_n = params.sample_n
        sample_percent = params.sample_percent
        if sample_n is None and sample_percent is None:
            return StepOutcome.failed(
                DATA_TRANSFORM_INVALID_STEP_PARAMS,
                "sampling 缺抽样规模（sample_n/sample_percent 二选一）",
            )
        if sample_n is not None and sample_n < 0:
            return StepOutcome.failed(
                DATA_TRANSFORM_INVALID_STEP_PARAMS,
                f"sampling sample_n 须为非负整数：{sample_n}",
            )
        if sample_percent is not None and not 0 < sample_percent <= 100:
            return StepOutcome.failed(
                DATA_TRANSFORM_INVALID_STEP_PARAMS,
                f"sampling sample_percent 须满足 0<比例≤100：{sample_percent}",
            )

        dataset = list(ctx.left_dataset)
        total = len(dataset)
        if sample_n is not None:
            count = sample_n
        else:
            # 比例换算行数（四舍五入，确定性换算）
            count = int(total * sample_percent / 100.0 + 0.5)
        count = min(count, total)

        if mode == "head":
            return StepOutcome(value=dataset[:count])

        rng = random.Random(params.random_seed)
        return StepOutcome(value=rng.sample(dataset, count))


class CustomCodeHandler:
    """自定义脚本登记型处理器（T5.4 / FR-17 / AC-15，DC-08 只登记不执行）。

    本期不执行（Python 与 JS 均不执行；无可靠内联执行环境）：
    执行含脚本步的节点 → fail-fast DATA_TRANSFORM_SCRIPT_NOT_EXECUTABLE
    （executor 附步骤定位），绝不声称执行成功、不产生执行副作用。
    录入期静态合规校验由 custom_code_guard.validate_custom_code 承担
    （ast.parse 只解析不执行）；受开关 data_transform_custom_code_enabled
    （默认 False）控制录入入口，**即便开启也不执行**。
    """

    operation = DataTransformOperation.CUSTOM_CODE
    degradable = False

    _NOT_EXECUTABLE_NOTE = (
        "custom_code 本期仅登记 + 静态合规校验，不执行（DC-08）；"
        "执行能力登记为后续独立任务，切换口径须回 OP-03 重裁"
    )

    def apply(self, ctx: StepContext) -> StepOutcome:
        # 无条件 fail-fast：脚本步不可执行（不校验参数——执行本身即被拒绝）
        return StepOutcome.failed(
            DATA_TRANSFORM_SCRIPT_NOT_EXECUTABLE,
            self._NOT_EXECUTABLE_NOTE,
        )


# 模块导入时注册（DC-02：注册表为唯一分派权威）
register(SamplingHandler())
register(CustomCodeHandler())
