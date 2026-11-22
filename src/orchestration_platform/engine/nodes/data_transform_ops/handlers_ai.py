# =============================================================================
# AI 增强操作处理器（engine/nodes/data_transform_ops/handlers_ai.py）
#
# 职责：承载 data_transform 操作链的 AI 增强操作处理器
#       （expand_data_transform_ops design DC-05 / CHG-B05，T5.2，AC-14）：
#   - json_path_extract：以 JSON Path 子集从嵌套 JSON 精准提取字段值回填目标列；
#     字符串型 JSON 先 json.loads（受 MAX_JSON_TEXT_LENGTH 长度上限）；
#     缺失路径置空 + 计数（degraded），不中断链；路径语法非法 → 配置期
#     DATA_TRANSFORM_INVALID_STEP_PARAMS。
# =============================================================================

from typing import Any, Dict, List

from orchestration_platform.domain.errors import DATA_TRANSFORM_INVALID_STEP_PARAMS
from orchestration_platform.domain.models.enums import DataTransformOperation
from orchestration_platform.engine.nodes.data_transform_ops.json_path import (
    JsonPathSyntaxError,
    evaluate_json_path,
    parse_json_path,
)
from orchestration_platform.engine.nodes.data_transform_ops.registry import (
    StepContext,
    StepOutcome,
    register,
)


class JsonPathExtractHandler:
    """JSON Path 提取操作处理器（T5.2 / FR-16 / AC-14）。

    source_field（承载 JSON 文本/结构的列）+ path（受限子集表达式）+
    target_field（回填列，必填）。路径缺失 → 目标列置空 + 行计数告警；
    [*] fan-out 分支部分缺失 → 处数告警（不中断链）。
    """

    operation = DataTransformOperation.JSON_PATH_EXTRACT
    degradable = True  # 缺失置空 + 计数，不中断链

    def apply(self, ctx: StepContext) -> StepOutcome:
        params = ctx.step.params
        source_field = params.source_field
        path = params.path
        target_field = params.target_field

        if not source_field or not path or not target_field:
            return StepOutcome.failed(
                DATA_TRANSFORM_INVALID_STEP_PARAMS,
                "json_path_extract 缺必填参数（source_field/path/target_field）",
            )
        try:
            segments = parse_json_path(path)
        except JsonPathSyntaxError as exc:
            return StepOutcome.failed(
                DATA_TRANSFORM_INVALID_STEP_PARAMS,
                f"json_path_extract 路径语法非法：{exc}",
            )

        missing_rows = 0
        partial_hits = 0
        result: List[Dict[str, Any]] = []
        for row in ctx.left_dataset:
            new_row = dict(row)
            extraction = evaluate_json_path(segments, row.get(source_field))
            if not extraction.found:
                missing_rows += 1
                new_row[target_field] = None
            else:
                partial_hits += extraction.missing_hits
                new_row[target_field] = extraction.value
            result.append(new_row)

        warnings: List[str] = []
        if missing_rows:
            warnings.append(f"json_path_extract 路径缺失 {missing_rows} 行（已置空）")
        if partial_hits:
            warnings.append(
                f"json_path_extract 通配分支部分缺失 {partial_hits} 处（已跳过）"
            )
        return StepOutcome(
            value=result,
            degraded=bool(missing_rows or partial_hits),
            warnings=warnings,
        )


# 模块导入时注册（DC-02：注册表为唯一分派权威）
register(JsonPathExtractHandler())
