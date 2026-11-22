# =============================================================================
# 清洗治理操作处理器（engine/nodes/data_transform_ops/handlers_cleaning.py）
#
# 职责：承载 data_transform 操作链的清洗治理操作处理器
#       （expand_data_transform_ops design §2.4.2（11）~（15），CHG-B04，T4 批次）：
#   - handle_nulls（T4.2 / FR-12 / AC-09）：统一空值判定 + 三档填充 + 按列/全列删行；
#   - masking（T4.4 / AC-13）：内置规则集 + 自定义模式，不可逆掩码，缺口最小暴露 + 告警；
#   - regex_extract（T4.5 / FR-13 / AC-10）：执行前经正则三层安全，无匹配置空 + 计数；
#   - regex_replace（T4.6 / FR-13 / AC-10）：同三层安全，无匹配保持原值 + 计数；
#   - validate（T4.7 / FR-14 / AC-12）：email 近似正则（显式声明非 RFC 完整）/
#     json.loads（受文本长度上限），on_invalid = drop/mark。
# 正则安全残余风险显式声明（DC-07）：三层防护不承诺完全免 ReDoS。
# =============================================================================

import json
import re
import statistics
from typing import Any, Dict, List, Optional, Tuple

from orchestration_platform.domain.errors import (
    DATA_TRANSFORM_INVALID_STEP_PARAMS,
    DATA_TRANSFORM_REGEX_INPUT_TOO_LONG,
    DATA_TRANSFORM_REGEX_UNSAFE_PATTERN,
)
from orchestration_platform.domain.models.enums import DataTransformOperation
from orchestration_platform.domain.models.node_configurations import (
    DataTransformOperationParams,
)
from orchestration_platform.engine.nodes.data_transform_ops.masking import (
    MASKING_IRREVERSIBLE_NOTE,
    MaskingRuleSet,
)
from orchestration_platform.engine.nodes.data_transform_ops.registry import (
    MAX_JSON_TEXT_LENGTH,
    StepContext,
    StepOutcome,
    register,
)
from orchestration_platform.engine.nodes.data_transform_ops.regex_safety import (
    check_regex_input_length,
    check_regex_safety,
)

# 统一空值判定口径（design §2.4.2-11）：None / 空串 / 缺失键
_NULL_SCOPES = ("none", "empty", "missing")

# handle_nulls 策略集
_NULL_STRATEGIES = ("fill_constant", "fill_mean", "fill_median", "drop_row")

# 邮箱近似校验正则（显式声明：非 RFC 5322 完整实现，仅覆盖常见形态）
_EMAIL_APPROX_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def _dataset_columns(dataset: List[Dict[str, Any]]) -> List[str]:
    """汇总数据集列名（首见顺序，跨行并集）。"""
    columns: List[str] = []
    seen = set()
    for row in dataset:
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                columns.append(key)
    return columns


def _compile_safe_pattern(pattern: str) -> Tuple[Optional[re.Pattern], Optional[StepOutcome]]:
    """经正则三层安全编译模式；不安全时返回 fail-fast StepOutcome。

    残余风险显式声明：三层防护不承诺完全免 ReDoS（win32 无 SIGALRM）。
    """
    verdict = check_regex_safety(pattern)
    if not verdict.safe:
        return None, StepOutcome.failed(
            DATA_TRANSFORM_REGEX_UNSAFE_PATTERN,
            f"正则模式被安全校验拒绝：{verdict.reason}",
        )
    return re.compile(pattern), None  # 编译失败已在安全校验中拒绝


class HandleNullsHandler:
    """缺失值处理操作处理器（T4.2 / FR-12 / AC-09）。

    统一空值判定口径：None（scope=none）/ 空串（scope=empty）/ 缺失键
    （scope=missing），缺省三者全部。
    策略：fill_constant（缺 fill_value → 配置期拒绝）/ fill_mean /
    fill_median（statistics）/ drop_row（drop_scope：field=按指定列空值
    删行，缺省；row=整行任意字段空值删行）。
    数值缺失无法统计 → 置空 + 告警（degraded），不中断链。
    """

    operation = DataTransformOperation.HANDLE_NULLS
    degradable = True  # 统计不可得时置空 + 告警，不中断链

    def apply(self, ctx: StepContext) -> StepOutcome:
        params = ctx.step.params
        strategy = params.strategy
        if not strategy:
            return StepOutcome(value=list(ctx.left_dataset))  # 无策略 → 透传（既有口径）

        if strategy not in _NULL_STRATEGIES:
            return StepOutcome.failed(
                DATA_TRANSFORM_INVALID_STEP_PARAMS,
                f"handle_nulls strategy 取值不支持：{strategy}",
            )
        scope = list(params.null_value_scope)
        if any(item not in _NULL_SCOPES for item in scope):
            return StepOutcome.failed(
                DATA_TRANSFORM_INVALID_STEP_PARAMS,
                f"handle_nulls null_value_scope 含非法取值：{scope}",
            )
        scope_set = set(scope) or set(_NULL_SCOPES)

        fields = list(params.null_fields) or _dataset_columns(ctx.left_dataset)
        warnings: List[str] = []

        if strategy in ("fill_mean", "fill_median"):
            return self._fill_statistics(ctx, fields, scope_set, strategy)
        if strategy == "fill_constant":
            fill_value = params.fill_value
            if fill_value is None:
                return StepOutcome.failed(
                    DATA_TRANSFORM_INVALID_STEP_PARAMS,
                    "handle_nulls strategy=fill_constant 缺 fill_value",
                )
            result: List[Dict[str, Any]] = []
            for row in ctx.left_dataset:
                new_row = dict(row)
                for field in fields:
                    if self._is_null(row, field, scope_set):
                        new_row[field] = fill_value
                result.append(new_row)
            return StepOutcome(value=result, warnings=warnings)

        # strategy == "drop_row"：按列 / 全列删行（spec：丢弃行支持按列或全列）
        drop_scope = params.drop_scope or "field"
        if drop_scope not in ("field", "row"):
            return StepOutcome.failed(
                DATA_TRANSFORM_INVALID_STEP_PARAMS,
                f"handle_nulls drop_scope 取值不支持：{drop_scope}",
            )
        check_fields = fields if drop_scope == "field" else _dataset_columns(ctx.left_dataset)
        kept: List[Dict[str, Any]] = []
        dropped = 0
        for row in ctx.left_dataset:
            if any(self._is_null(row, field, scope_set) for field in check_fields):
                dropped += 1
                continue
            kept.append(row)
        if dropped:
            warnings.append(f"handle_nulls 删除空值行 {dropped} 行")
        return StepOutcome(value=kept, warnings=warnings)

    def _fill_statistics(
        self,
        ctx: StepContext,
        fields: List[str],
        scope_set: set,
        strategy: str,
    ) -> StepOutcome:
        """统计填充（均值/中位数，statistics 标准库）。

        数值缺失无法统计 → 置空（None）+ 告警，不中断链。
        """
        warnings: List[str] = []
        degraded = False
        result: List[Dict[str, Any]] = []

        # 逐字段统计（按非空数值单元格）
        stats: Dict[str, Optional[float]] = {}
        for field in fields:
            numeric_values = [
                row[field]
                for row in ctx.left_dataset
                if field in row
                and not self._is_null(row, field, scope_set)
                and isinstance(row[field], (int, float))
                and not isinstance(row[field], bool)
            ]
            if numeric_values:
                stats[field] = (
                    statistics.mean(numeric_values)
                    if strategy == "fill_mean"
                    else statistics.median(numeric_values)
                )
            else:
                stats[field] = None

        for row in ctx.left_dataset:
            new_row = dict(row)
            for field in fields:
                if not self._is_null(row, field, scope_set):
                    continue
                stat_value = stats[field]
                if stat_value is None:
                    # 数值缺失无法统计 → 置空 + 告警（degraded）
                    new_row[field] = None
                    degraded = True
                    warning = f"handle_nulls 字段 {field} 无数值可统计，空值已置空未填充"
                    if warning not in warnings:
                        warnings.append(warning)
                else:
                    new_row[field] = stat_value
            result.append(new_row)

        return StepOutcome(value=result, degraded=degraded, warnings=warnings)

    @staticmethod
    def _is_null(row: Dict[str, Any], field: str, scope_set: set) -> bool:
        """统一空值判定：缺失键 / None / 空串（按 scope 口径）。"""
        if field not in row:
            return "missing" in scope_set
        value = row[field]
        if value is None:
            return "none" in scope_set
        if isinstance(value, str) and value == "":
            return "empty" in scope_set
        return False


class RegexExtractHandler:
    """正则提取操作处理器（T4.5 / FR-13 / AC-10）。

    执行前经正则三层安全（DC-07）：不安全 → DATA_TRANSFORM_REGEX_UNSAFE_PATTERN；
    输入超长 → DATA_TRANSFORM_REGEX_INPUT_TOO_LONG（fail-fast）。
    无匹配 → 目标列置空 + 计数（degraded），不中断链。
    extract_mode：first（首个匹配，缺省）/ all（全部匹配列表）。
    """

    operation = DataTransformOperation.REGEX_EXTRACT
    degradable = True  # 无匹配置空 + 计数，不中断链

    def apply(self, ctx: StepContext) -> StepOutcome:
        params = ctx.step.params
        field = params.regex_field
        pattern_text = params.pattern
        target_field = params.target_field

        if not field or not pattern_text or not target_field:
            return StepOutcome.failed(
                DATA_TRANSFORM_INVALID_STEP_PARAMS,
                "regex_extract 缺必填参数（regex_field/pattern/target_field）",
            )
        extract_mode = params.extract_mode or "first"
        if extract_mode not in ("first", "all"):
            return StepOutcome.failed(
                DATA_TRANSFORM_INVALID_STEP_PARAMS,
                f"regex_extract extract_mode 取值不支持：{extract_mode}",
            )
        group = params.group if params.group is not None else 0
        if group < 0:
            return StepOutcome.failed(
                DATA_TRANSFORM_INVALID_STEP_PARAMS,
                f"regex_extract group 须 ≥ 0（当前 {group}）",
            )

        pattern, failure = _compile_safe_pattern(pattern_text)
        if failure is not None:
            return failure
        assert pattern is not None

        no_match = 0
        result: List[Dict[str, Any]] = []
        for row in ctx.left_dataset:
            new_row = dict(row)
            value = row.get(field)
            extracted: Optional[Any] = None
            if isinstance(value, str):
                if check_regex_input_length(value):
                    return StepOutcome.failed(
                        DATA_TRANSFORM_REGEX_INPUT_TOO_LONG,
                        f"regex_extract 字段 {field} 输入长度超上限",
                    )
                if extract_mode == "first":
                    match = pattern.search(value)
                    if match is not None:
                        try:
                            extracted = match.group(group)
                        except IndexError:
                            extracted = None  # 捕获组越界按无匹配处理
                else:
                    matches = []
                    for match in pattern.finditer(value):
                        try:
                            matches.append(match.group(group))
                        except IndexError:
                            continue  # 捕获组越界按无匹配处理
                    extracted = matches if matches else None
            if extracted is None:
                no_match += 1
            new_row[target_field] = extracted
            result.append(new_row)

        warnings: List[str] = []
        if no_match:
            warnings.append(f"regex_extract 无匹配 {no_match} 行（已置空）")
        return StepOutcome(value=result, degraded=bool(no_match), warnings=warnings)


class RegexReplaceHandler:
    """正则替换操作处理器（T4.6 / FR-13 / AC-10）。

    replacement 支持 \\1 捕获组引用；target_field 缺省原地替换。
    同正则三层安全；无匹配保持原值 + 计数（degraded），不中断链。
    """

    operation = DataTransformOperation.REGEX_REPLACE
    degradable = True  # 无匹配保持原值 + 计数，不中断链

    def apply(self, ctx: StepContext) -> StepOutcome:
        params = ctx.step.params
        field = params.regex_field
        pattern_text = params.pattern
        replacement = params.replacement
        target_field = params.target_field or field

        if not field or not pattern_text or replacement is None:
            return StepOutcome.failed(
                DATA_TRANSFORM_INVALID_STEP_PARAMS,
                "regex_replace 缺必填参数（regex_field/pattern/replacement）",
            )

        pattern, failure = _compile_safe_pattern(pattern_text)
        if failure is not None:
            return failure
        assert pattern is not None

        # 替换模板捕获组引用预校验（引用不存在的组 → 配置期拒绝）
        try:
            pattern.sub(replacement, "")
        except re.error as exc:
            return StepOutcome.failed(
                DATA_TRANSFORM_INVALID_STEP_PARAMS,
                f"regex_replace 替换模板非法：{exc}",
            )

        no_match = 0
        result: List[Dict[str, Any]] = []
        for row in ctx.left_dataset:
            new_row = dict(row)
            value = row.get(field)
            if isinstance(value, str):
                if check_regex_input_length(value):
                    return StepOutcome.failed(
                        DATA_TRANSFORM_REGEX_INPUT_TOO_LONG,
                        f"regex_replace 字段 {field} 输入长度超上限",
                    )
                replaced, hits = pattern.subn(replacement, value)
                if hits == 0:
                    no_match += 1  # 无匹配保持原值 + 计数
                new_row[target_field] = replaced
            else:
                no_match += 1  # 非字符串无法替换，保持原值
                new_row[target_field] = value
            result.append(new_row)

        warnings: List[str] = []
        if no_match:
            warnings.append(f"regex_replace 无匹配 {no_match} 行（保持原值）")
        return StepOutcome(value=result, degraded=bool(no_match), warnings=warnings)


class ValidateHandler:
    """格式校验过滤操作处理器（T4.7 / FR-14 / AC-12）。

    validate_rule：email（近似正则，显式声明非 RFC 完整，仅覆盖常见形态）/
    json（json.loads 合法性判定，受 MAX_JSON_TEXT_LENGTH 文本长度上限）。
    on_invalid：drop（删行，缺省）/ mark（追加 {field}_invalid 标记列）。
    不合规为过滤条件，非失败（无错误码）。
    """

    operation = DataTransformOperation.VALIDATE
    degradable = False  # 不合规行按 on_invalid 处理，属过滤条件非失败

    def apply(self, ctx: StepContext) -> StepOutcome:
        params = ctx.step.params
        field = params.validate_field
        rule = params.validate_rule

        if not field or not rule:
            return StepOutcome.failed(
                DATA_TRANSFORM_INVALID_STEP_PARAMS,
                "validate 缺必填参数（validate_field/validate_rule）",
            )
        if rule not in ("email", "json"):
            return StepOutcome.failed(
                DATA_TRANSFORM_INVALID_STEP_PARAMS,
                f"validate validate_rule 取值不支持：{rule}",
            )
        on_invalid = params.on_invalid or "drop"
        if on_invalid not in ("drop", "mark"):
            return StepOutcome.failed(
                DATA_TRANSFORM_INVALID_STEP_PARAMS,
                f"validate on_invalid 取值不支持：{on_invalid}",
            )

        result: List[Dict[str, Any]] = []
        dropped = 0
        for row in ctx.left_dataset:
            valid = self._is_valid(row.get(field), rule)
            if valid:
                result.append(dict(row) if on_invalid == "mark" else row)
                if on_invalid == "mark":
                    result[-1][f"{field}_invalid"] = False
            elif on_invalid == "drop":
                dropped += 1
            else:
                marked = dict(row)
                marked[f"{field}_invalid"] = True
                result.append(marked)

        warnings: List[str] = []
        if dropped:
            warnings.append(f"validate 剔除不合规行 {dropped} 行")
        return StepOutcome(value=result, warnings=warnings)

    @staticmethod
    def _is_valid(value: Any, rule: str) -> bool:
        if rule == "email":
            # 近似校验（非 RFC 完整，显式声明局限）
            return isinstance(value, str) and _EMAIL_APPROX_RE.fullmatch(value) is not None
        # rule == "json"：json.loads 合法性判定（受文本长度上限）
        if isinstance(value, (dict, list)):
            return True  # 已解析结构视为合法 JSON
        if not isinstance(value, str):
            return False
        if len(value) > MAX_JSON_TEXT_LENGTH:
            return False  # 超长不解析，判不合规
        try:
            json.loads(value)
        except (ValueError, TypeError):
            return False
        return True


class MaskingHandler:
    """脱敏操作处理器（T4.4 / AC-13）。

    mask_fields × mask_rules（内置规则 id）+ custom_patterns（自定义值模式）。
    不可逆掩码（非加密级，不提供反解）：结果说明固定附不可逆声明
    （MASKING_IRREVERSIBLE_NOTE）。
    缺口最小暴露：声明脱敏的字段值未命中任何规则 → 整值替换掩码占位
    （***）+ 计数告警（degraded）。
    自定义模式经正则三层安全（DC-07），不安全 → fail-fast。
    """

    operation = DataTransformOperation.MASKING
    degradable = True  # 缺口告警（最小暴露），不中断链

    _GAP_MASK = "***"

    def apply(self, ctx: StepContext) -> StepOutcome:
        params = ctx.step.params
        mask_fields = list(params.mask_fields)
        mask_rules = list(params.mask_rules)
        custom_patterns = list(params.custom_patterns)

        if not mask_fields or (not mask_rules and not custom_patterns):
            return StepOutcome(value=list(ctx.left_dataset))  # 无字段/无规则 → 透传

        rule_set = MaskingRuleSet.builtin()
        for rule_id in mask_rules:
            if not rule_set.has_rule(rule_id):
                return StepOutcome.failed(
                    DATA_TRANSFORM_INVALID_STEP_PARAMS,
                    f"masking 内置规则不存在：{rule_id}",
                )

        # 自定义模式：经正则三层安全后登记（{n} 捕获组引用 → re 分组引用）
        for index, custom in enumerate(custom_patterns):
            verdict = check_regex_safety(custom.pattern)
            if not verdict.safe:
                return StepOutcome.failed(
                    DATA_TRANSFORM_REGEX_UNSAFE_PATTERN,
                    f"masking 自定义模式 #{index} 被安全校验拒绝：{verdict.reason}",
                )
            rule_set.add_rule(
                f"custom_{index}", custom.pattern, self._translate_template(custom.mask_template)
            )

        selected_rules = list(mask_rules) + [f"custom_{i}" for i in range(len(custom_patterns))]

        gaps = 0
        result: List[Dict[str, Any]] = []
        for row in ctx.left_dataset:
            new_row = dict(row)
            for field in mask_fields:
                raw = new_row.get(field)
                if raw is None or raw == "":
                    continue  # 空值无暴露内容，不计缺口
                masked_value = raw
                any_hit = False
                for rule_id in selected_rules:
                    masked = rule_set.mask_field(masked_value, rule_id)
                    if masked.hit:
                        masked_value = masked.value
                        any_hit = True
                if not any_hit:
                    # 缺口最小暴露：整值替换掩码占位 + 计数告警
                    masked_value = self._GAP_MASK
                    gaps += 1
                new_row[field] = masked_value
            result.append(new_row)

        warnings: List[str] = [MASKING_IRREVERSIBLE_NOTE]  # 不可逆声明（结果说明体现）
        if gaps:
            warnings.append(
                f"masking 未命中任何规则 {gaps} 处（已整值替换掩码，缺口最小暴露）"
            )
        return StepOutcome(value=result, degraded=bool(gaps), warnings=warnings)

    @staticmethod
    def _translate_template(mask_template: str) -> str:
        """自定义掩码模板 → re.sub 替换模板：{n}（捕获组）→ \\n，其余字面量。

        模板语义（node_configurations.MaskingCustomPattern）：MASK=整段掩码
        （字面量替换整段匹配）；{1}**{2}=保留捕获组 1/2、中间字面掩码。
        """
        parts: List[str] = []
        index = 0
        length = len(mask_template)
        while index < length:
            char = mask_template[index]
            if char == "{":
                close = mask_template.find("}", index + 1)
                if close != -1 and mask_template[index + 1 : close].isdigit():
                    parts.append(f"\\{mask_template[index + 1 : close]}")
                    index = close + 1
                    continue
            if char == "\\":
                parts.append("\\\\")
                index += 1
                continue
            parts.append(char)
            index += 1
        return "".join(parts)


# 模块导入时注册（DC-02：注册表为唯一分派权威）
register(HandleNullsHandler())
register(RegexExtractHandler())
register(RegexReplaceHandler())
register(ValidateHandler())
register(MaskingHandler())
