# =============================================================================
# 结构化操作处理器（engine/nodes/data_transform_ops/handlers_structure.py）
#
# 职责：承载 data_transform 操作链的结构化操作处理器（expand_data_transform_ops
#       design §2.4.1，CHG-B03）。
# 既有四操作（B2，行为与既有 executor 逐字等价，T2.2/T2.3/T2.4）：
#   - map/filter 表达式仍走 RestrictedExpressionEvaluator 白名单路径（INV-14，
#     不修改求值器；OP-08 缺陷现状保持，不闭环）；
#   - 既有"求值失败静默吞异常"不确定态改为"结果等价 + 失败计数告警"（OP-12，
#     CHG-B15 删除静默吞异常路径：输出值不变，失败不再不可见）；
#   - aggregate 语义（sum/count/avg/max/min + count 列）逐字保持；
#   - join 单键（join_key）+ right_ 前缀 + 副引用 DC-10 四态装配保持，
#     装配失败透传装配层错误码（INV-09，不回退）。
# 结构化操作（B3，T3.1~T3.10，design §2.4.1（1）~（10））：
#   sort/distinct/rename/select_columns/drop_columns/cast/row_number/flatten/explode
#   + join 多条件复合键增强（T3.7）。
# =============================================================================

import json
from functools import cmp_to_key
from typing import Any, Dict, List, Optional, Tuple

from orchestration_platform.domain.errors import (
    DATA_TRANSFORM_INVALID_STEP_PARAMS,
    DATA_TRANSFORM_ROW_LIMIT_EXCEEDED,
)
from orchestration_platform.domain.models.enums import (
    AggregateFunction,
    DataTransformOperation,
    JoinType,
)
from orchestration_platform.domain.models.node_configurations import DataTransformOperationParams
from orchestration_platform.engine.input_assembly import (
    AssemblyFailure,
    assemble_single_reference,
)
from orchestration_platform.engine.nodes.data_transform_ops.registry import (
    DEFAULT_FLATTEN_MAX_DEPTH,
    MAX_EXPLODE_ROWS,
    StepContext,
    StepOutcome,
    register,
)


def _evaluate_expression(expression: str, row: Dict[str, Any]) -> Optional[Any]:
    """以单行 dict 为求值状态执行受限表达式求值（不修改求值器，INV-14）。

    返回 None 表示求值失败（调用方按既有口径处理并计数）。
    注意：受限求值器变量引用走 state.node_outputs（OP-08 缺陷根因，现状保持）。
    """
    try:
        from orchestration_platform.engine.scheduler import RestrictedExpressionEvaluator

        evaluator = RestrictedExpressionEvaluator()
        return evaluator.evaluate(expression, row)  # type: ignore[arg-type]
    except Exception:
        return None


class MapHandler:
    """映射操作处理器：按表达式逐行变换（既有行为等价，OP-08 现状保持）。

    行为口径（与既有 _execute_map 逐字对齐）：
      - 无 map_expression → 原样返回；
      - 求值结果为 dict → 采用变换行；
      - 求值结果非 dict（含求值器恒 bool 语义）→ 保留原行（计数告警）；
      - 求值失败（含 OP-08 变量引用必失败）→ 保留原行（计数告警，不再静默）。
    """

    operation = DataTransformOperation.MAP
    degradable = True  # 行级容错：失败/非对象结果保留原行 + 计数，不中断链

    def apply(self, ctx: StepContext) -> StepOutcome:
        params = ctx.step.params
        expression = params.map_expression
        if not expression:
            return StepOutcome(value=list(ctx.left_dataset))

        result: List[Dict[str, Any]] = []
        kept_original = 0
        failed_rows = 0
        for row in ctx.left_dataset:
            transformed = _evaluate_expression(expression, row)
            if transformed is None:
                failed_rows += 1
                result.append(row)
                continue
            if isinstance(transformed, dict):
                result.append(transformed)
            else:
                kept_original += 1
                result.append(row)

        warnings: List[str] = []
        if failed_rows:
            warnings.append(f"map 表达式求值失败 {failed_rows} 行，已保留原行")
        if kept_original:
            warnings.append(f"map 表达式结果非对象 {kept_original} 行，已保留原行")
        return StepOutcome(value=result, degraded=bool(warnings), warnings=warnings)


class FilterHandler:
    """过滤操作处理器：按条件保留子集（既有行为等价，OP-08 现状保持）。

    行为口径（与既有 _execute_filter 逐字对齐）：
      - 无 filter_condition → 原样返回；
      - 求值为真 → 保留；
      - 求值为假 → 剔除；
      - 求值失败（含 OP-08 变量引用必失败）→ 剔除（计数告警，不再静默）。
    """

    operation = DataTransformOperation.FILTER
    degradable = True  # 行级容错：失败行剔除 + 计数，不中断链

    def apply(self, ctx: StepContext) -> StepOutcome:
        params = ctx.step.params
        condition = params.filter_condition
        if not condition:
            return StepOutcome(value=list(ctx.left_dataset))

        result: List[Dict[str, Any]] = []
        failed_rows = 0
        for row in ctx.left_dataset:
            verdict = _evaluate_expression(condition, row)
            if verdict is None:
                failed_rows += 1
                continue
            if verdict:
                result.append(row)

        warnings: List[str] = []
        if failed_rows:
            warnings.append(f"filter 条件求值失败 {failed_rows} 行，已按不保留处理")
        return StepOutcome(value=result, degraded=bool(warnings), warnings=warnings)


class AggregateHandler:
    """聚合操作处理器：按分组键与聚合函数汇总（既有行为等价）。"""

    operation = DataTransformOperation.AGGREGATE
    degradable = False

    def apply(self, ctx: StepContext) -> StepOutcome:
        params = ctx.step.params
        group_by = params.group_by
        agg_func = params.aggregate_function
        agg_field = params.aggregate_field

        if not agg_func or not agg_field:
            return StepOutcome(value=list(ctx.left_dataset))

        # 分组
        groups: Dict[tuple, List[Dict[str, Any]]] = {}
        for row in ctx.left_dataset:
            key = tuple(row.get(g) for g in group_by) if group_by else ()
            if key not in groups:
                groups[key] = []
            groups[key].append(row)

        # 聚合
        result: List[Dict[str, Any]] = []
        for key, rows in groups.items():
            values = [r.get(agg_field, 0) for r in rows]
            agg_value = _apply_aggregate(agg_func, values)
            agg_row = dict(zip(group_by, key)) if group_by else {}
            agg_row[f"{agg_func.value}_{agg_field}"] = agg_value
            agg_row["count"] = len(rows)
            result.append(agg_row)

        return StepOutcome(value=result)


class JoinHandler:
    """连接操作处理器：按键连接两个数据集。

    单键形态（join_key，既有兼容）：行为与既有 _execute_join 逐字等价（B2）。
    多条件复合键（join_keys，T3.7 / FR-09 / AC-06）：
      - join_keys 有序复合键，right_join_keys 缺省与左侧同名；
      - join_keys 与 right_join_keys 长度不符 → DATA_TRANSFORM_INVALID_STEP_PARAMS；
      - 元组键建索引 O(n)；空值键不参与匹配（显式定义）；
      - 右表字段 right_ 前缀语义保持（CHG-B03）。
    副引用装配走 assemble_single_reference(expected_kind="dataset")，
    四态收窄语义保持（unified DC-10，INV-09 不回退）：失败透传装配层错误码。
    既有口径：join_key/right_dataset_reference 缺失 → 透传左表（无操作）。
    """

    operation = DataTransformOperation.JOIN
    degradable = False

    def apply(self, ctx: StepContext) -> StepOutcome:
        params = ctx.step.params
        if params.join_keys:
            return self._apply_multi_key(ctx, params)
        return self._apply_single_key(ctx, params)

    def _apply_multi_key(
        self, ctx: StepContext, params: DataTransformOperationParams
    ) -> StepOutcome:
        join_type = params.join_type or JoinType.INNER
        left_keys = list(params.join_keys)
        right_keys = list(params.right_join_keys or left_keys)
        right_ref = params.right_dataset_reference
        left_data = ctx.left_dataset

        if len(left_keys) != len(right_keys):
            return StepOutcome.failed(
                DATA_TRANSFORM_INVALID_STEP_PARAMS,
                (
                    f"join_keys（{len(left_keys)}）与 right_join_keys（{len(right_keys)}）"
                    "长度不符"
                ),
            )

        if not right_ref:
            return StepOutcome.failed(
                DATA_TRANSFORM_INVALID_STEP_PARAMS,
                "join 多条件复合键形态缺 right_dataset_reference",
            )

        right_outcome = assemble_single_reference(
            right_ref, ctx.shared_state_snapshot, expected_kind="dataset"
        )
        if isinstance(right_outcome, AssemblyFailure):
            return StepOutcome.failed(
                right_outcome.error_code,
                f"join 副引用装配失败：{right_outcome.error_message}",
            )
        right_data = right_outcome.value

        def _key_of(row: Dict[str, Any], keys: List[str]) -> Optional[Tuple[Any, ...]]:
            values = tuple(row.get(k) for k in keys)
            if any(v is None for v in values):
                return None  # 空值键不参与匹配（显式定义）
            return values

        # 右表元组键索引 O(n)
        right_index: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = {}
        for row in right_data:
            key = _key_of(row, right_keys)
            if key is None:
                continue
            if key not in right_index:
                right_index[key] = []
            right_index[key].append(row)

        result: List[Dict[str, Any]] = []
        for left_row in left_data:
            key = _key_of(left_row, left_keys)
            matches: List[Dict[str, Any]] = []
            if key is not None:
                matches = right_index.get(key, [])
            if matches:
                for right_row in matches:
                    merged = {**left_row, **{f"right_{k}": v for k, v in right_row.items()}}
                    result.append(merged)
            elif join_type in (JoinType.LEFT, JoinType.FULL):
                result.append(left_row)

        # 右连接补充无匹配的右表行（右键不在任何左键集合中）
        if join_type in (JoinType.RIGHT, JoinType.FULL):
            left_key_set = {
                key for key in (_key_of(row, left_keys) for row in left_data)
                if key is not None
            }
            for right_row in right_data:
                key = _key_of(right_row, right_keys)
                if key is None or key in left_key_set:
                    continue
                result.append({f"right_{k}": v for k, v in right_row.items()})

        return StepOutcome(value=result)

    def _apply_single_key(
        self, ctx: StepContext, params: DataTransformOperationParams
    ) -> StepOutcome:
        join_type = params.join_type or JoinType.INNER
        join_key = params.join_key
        right_ref = params.right_dataset_reference
        left_data = ctx.left_dataset

        if not join_key or not right_ref:
            return StepOutcome(value=list(left_data))

        # 副引用装配（DC-10 同规则：缺失/非 list fail-fast，不静默透传左表）
        right_outcome = assemble_single_reference(
            right_ref, ctx.shared_state_snapshot, expected_kind="dataset"
        )
        if isinstance(right_outcome, AssemblyFailure):
            return StepOutcome.failed(
                right_outcome.error_code,
                f"join 副引用装配失败：{right_outcome.error_message}",
            )
        right_data = right_outcome.value

        # 构建右表索引
        right_index: Dict[Any, List[Dict[str, Any]]] = {}
        for row in right_data:
            key = row.get(join_key)
            if key not in right_index:
                right_index[key] = []
            right_index[key].append(row)

        result: List[Dict[str, Any]] = []
        for left_row in left_data:
            key = left_row.get(join_key)
            matches = right_index.get(key, [])

            if matches:
                for right_row in matches:
                    merged = {**left_row, **{f"right_{k}": v for k, v in right_row.items()}}
                    result.append(merged)
            elif join_type in (JoinType.LEFT, JoinType.FULL):
                result.append(left_row)

        # 右连接补充无匹配的右表行
        if join_type in (JoinType.RIGHT, JoinType.FULL):
            left_keys = {r.get(join_key) for r in left_data}
            for right_row in right_data:
                if right_row.get(join_key) not in left_keys:
                    result.append({f"right_{k}": v for k, v in right_row.items()})

        return StepOutcome(value=result)


def _apply_aggregate(func: AggregateFunction, values: List[Any]) -> Any:
    """应用聚合函数（既有语义逐字保持）。"""
    numeric_values = [v for v in values if isinstance(v, (int, float))]
    if func == AggregateFunction.SUM:
        return sum(numeric_values)
    if func == AggregateFunction.COUNT:
        return len(values)
    if func == AggregateFunction.AVG:
        return sum(numeric_values) / len(numeric_values) if numeric_values else 0
    if func == AggregateFunction.MAX:
        return max(numeric_values) if numeric_values else None
    if func == AggregateFunction.MIN:
        return min(numeric_values) if numeric_values else None
    return None


# ---------------------------------------------------------------------------
# 结构化操作（B3：T3.1~T3.10，design §2.4.1（1）~（10））
# ---------------------------------------------------------------------------


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


def _make_order_cmp(
    keys: List[str], orders: List[str]
) -> Any:
    """构造多键比较器：空值统一排末尾（双向一致）；键缺失视为空值。

    orders 与 keys 长度不符按缺省 asc 补齐（design §2.4.1（1））；
    不可比较类型回退字符串比较（确定性，不失败）。
    """
    directions = [o if o in ("asc", "desc") else "asc" for o in orders[: len(keys)]]
    directions.extend(["asc"] * (len(keys) - len(directions)))

    def compare(left: Dict[str, Any], right: Dict[str, Any]) -> int:
        for key, direction in zip(keys, directions):
            lv = left.get(key)
            rv = right.get(key)
            if lv is None and rv is None:
                continue
            if lv is None:
                return 1  # 空值排末尾
            if rv is None:
                return -1
            if lv == rv:
                continue
            try:
                less = lv < rv
            except TypeError:
                ls, rs = str(lv), str(rv)
                if ls == rs:
                    continue
                less = ls < rs
            result = -1 if less else 1
            if direction == "desc":
                result = -result
            return result
        return 0

    return compare


class SortHandler:
    """排序操作处理器（T3.1 / FR-05 / AC-04）：多键升降序，稳定排序。

    边界：空值统一排末尾；键缺失视为空值；键不存在仅告警不失败；
    sort_orders 与 sort_keys 长度不符按缺省 asc 补齐。
    """

    operation = DataTransformOperation.SORT
    degradable = False

    def apply(self, ctx: StepContext) -> StepOutcome:
        params = ctx.step.params
        sort_keys = list(params.sort_keys)
        if not sort_keys:
            return StepOutcome(value=list(ctx.left_dataset))

        orders = list(params.sort_orders)
        warnings: List[str] = []
        columns = set(_dataset_columns(ctx.left_dataset))
        missing_keys = [k for k in sort_keys if k not in columns]
        if missing_keys:
            warnings.append(f"sort 键不存在（所有行缺失）：{', '.join(missing_keys)}")

        comparator = _make_order_cmp(sort_keys, orders)
        ordered = sorted(ctx.left_dataset, key=cmp_to_key(comparator))
        return StepOutcome(value=ordered, warnings=warnings)


class DistinctHandler:
    """去重操作处理器（T3.2 / FR-06 / AC-04）：按键或全列去重，保留首条。

    边界：复合值（dict/list）用稳定序列化键（排序键序 + 类型前缀）；
    keep 仅支持 first（缺省）；空 distinct_keys = 全列去重。
    """

    operation = DataTransformOperation.DISTINCT
    degradable = False

    def apply(self, ctx: StepContext) -> StepOutcome:
        params = ctx.step.params
        keep = params.keep
        if keep is not None and keep != "first":
            return StepOutcome.failed(
                DATA_TRANSFORM_INVALID_STEP_PARAMS,
                f"distinct keep 取值不支持：{keep}（仅支持 first）",
            )

        distinct_keys = list(params.distinct_keys)
        result: List[Dict[str, Any]] = []
        seen = set()
        for row in ctx.left_dataset:
            if distinct_keys:
                key_parts = [
                    (type(row.get(k)).__name__, _stable_serialize(row.get(k)))
                    for k in distinct_keys
                ]
            else:
                key_parts = [
                    (type(row.get(k)).__name__, _stable_serialize(row.get(k)))
                    for k in sorted(row.keys())
                ]
            dedup_key = tuple(key_parts)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            result.append(row)

        return StepOutcome(value=result)


def _stable_serialize(value: Any) -> str:
    """复合值稳定序列化（排序键序 + 确定性），供去重键使用。"""
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return repr(value)


class RenameHandler:
    """重命名列操作处理器（T3.3 / FR-07 / AC-04）。

    边界：目标名已存在（且未被同时改名移走）或目标重复 → 
    DATA_TRANSFORM_INVALID_STEP_PARAMS（不静默覆盖）；
    源列不存在 → 忽略 + 告警；同步改名（单趟映射，无级联）。
    """

    operation = DataTransformOperation.RENAME
    degradable = False

    def apply(self, ctx: StepContext) -> StepOutcome:
        params = ctx.step.params
        rename_map = dict(params.rename_map)
        if not rename_map:
            return StepOutcome(value=list(ctx.left_dataset))

        columns = set(_dataset_columns(ctx.left_dataset))
        warnings: List[str] = []

        # 目标重复 → 拒绝（避免静默覆盖）
        targets = list(rename_map.values())
        if len(set(targets)) != len(targets):
            return StepOutcome.failed(
                DATA_TRANSFORM_INVALID_STEP_PARAMS,
                "rename 目标名重复（多个源映射到同一目标），拒绝静默覆盖",
            )

        # 目标名已存在且未被同时改名移走 → 拒绝
        for old_name, new_name in rename_map.items():
            if new_name in columns and new_name not in rename_map:
                return StepOutcome.failed(
                    DATA_TRANSFORM_INVALID_STEP_PARAMS,
                    f"rename 目标名已存在：{old_name} → {new_name}",
                )

        missing = [old for old in rename_map if old not in columns]
        if missing:
            warnings.append(f"rename 源列不存在（已忽略）：{', '.join(missing)}")

        result: List[Dict[str, Any]] = []
        for row in ctx.left_dataset:
            new_row = {rename_map.get(k, k): v for k, v in row.items()}
            result.append(new_row)

        return StepOutcome(value=result, warnings=warnings)


class SelectColumnsHandler:
    """选择列操作处理器（T3.4 / FR-07 / AC-04）：有序保留子集，缺字段补 None。

    边界：不存在列忽略 + 告警；select_columns 为空 → 
    DATA_TRANSFORM_INVALID_STEP_PARAMS（空集语义破坏性歧义，拒绝）。
    """

    operation = DataTransformOperation.SELECT_COLUMNS
    degradable = False

    def apply(self, ctx: StepContext) -> StepOutcome:
        params = ctx.step.params
        selected = list(params.select_columns)
        if not selected:
            return StepOutcome.failed(
                DATA_TRANSFORM_INVALID_STEP_PARAMS,
                "select_columns 为空（拒绝输出空列集）",
            )

        columns = set(_dataset_columns(ctx.left_dataset))
        missing = [c for c in selected if c not in columns]
        warnings: List[str] = []
        if missing:
            warnings.append(f"select_columns 列不存在（已忽略）：{', '.join(missing)}")

        result: List[Dict[str, Any]] = []
        for row in ctx.left_dataset:
            result.append({c: row.get(c) for c in selected})

        return StepOutcome(value=result, warnings=warnings)


class DropColumnsHandler:
    """删除列操作处理器（T3.5 / FR-07 / AC-04）：不存在列忽略 + 告警。"""

    operation = DataTransformOperation.DROP_COLUMNS
    degradable = False

    def apply(self, ctx: StepContext) -> StepOutcome:
        params = ctx.step.params
        drop_set = set(params.drop_columns)
        if not drop_set:
            return StepOutcome(value=list(ctx.left_dataset))

        columns = set(_dataset_columns(ctx.left_dataset))
        missing = [c for c in params.drop_columns if c not in columns]
        warnings: List[str] = []
        if missing:
            warnings.append(f"drop_columns 列不存在（已忽略）：{', '.join(missing)}")

        result: List[Dict[str, Any]] = []
        for row in ctx.left_dataset:
            result.append({k: v for k, v in row.items() if k not in drop_set})

        return StepOutcome(value=result, warnings=warnings)


class CastHandler:
    """类型强转操作处理器（T3.6 / FR-08 / AC-05 / OP-11）。

    边界：cast_target=number → 文本数字转数值（int 优先，回退 float）；
    cast_target=date → 仅显式 strptime 格式（自然语言日期不可行，OP-11），
    成功产出 ISO 8601 字符串（JSON 安全、可排序）；
    失败单元格置空 + 计数告警（degraded），不中断链；
    date 缺 date_format / cast_target 非法 / cast_field 缺失 → 
    DATA_TRANSFORM_INVALID_STEP_PARAMS。
    """

    operation = DataTransformOperation.CAST
    degradable = True  # 单元格失败置空 + 计数，不中断链

    _VALID_TARGETS = ("number", "date")

    def apply(self, ctx: StepContext) -> StepOutcome:
        params = ctx.step.params
        field = params.cast_field
        target = params.cast_target
        date_format = params.date_format

        if not field:
            return StepOutcome.failed(
                DATA_TRANSFORM_INVALID_STEP_PARAMS, "cast 缺 cast_field"
            )
        if target not in self._VALID_TARGETS:
            return StepOutcome.failed(
                DATA_TRANSFORM_INVALID_STEP_PARAMS,
                f"cast_target 取值不支持：{target}（仅支持 number/date）",
            )
        if target == "date" and not date_format:
            return StepOutcome.failed(
                DATA_TRANSFORM_INVALID_STEP_PARAMS,
                "cast_target=date 缺 date_format（仅支持显式 strptime 格式）",
            )

        failed_cells = 0
        result: List[Dict[str, Any]] = []
        for row in ctx.left_dataset:
            new_row = dict(row)
            value = row.get(field)
            casted, ok = self._cast_value(value, target, date_format)
            if ok:
                new_row[field] = casted
            else:
                new_row[field] = None  # 置空 + 计数
                failed_cells += 1
            result.append(new_row)

        warnings: List[str] = []
        if failed_cells:
            warnings.append(f"cast {field} 失败单元格 {failed_cells} 个（已置空）")
        return StepOutcome(value=result, degraded=bool(failed_cells), warnings=warnings)

    @staticmethod
    def _cast_value(value: Any, target: str, date_format: Optional[str]) -> Tuple[Any, bool]:
        if value is None:
            return None, False
        if target == "number":
            if isinstance(value, bool):
                return value, True  # 布尔保持（非数值文本口径外，不计数失败）
            if isinstance(value, (int, float)):
                return value, True
            if isinstance(value, str):
                text = value.strip()
                try:
                    return int(text), True
                except ValueError:
                    try:
                        return float(text), True
                    except ValueError:
                        return None, False
            return None, False
        # target == "date"：仅显式 strptime 格式（OP-11）
        if not isinstance(value, str):
            return None, False
        from datetime import datetime

        try:
            parsed = datetime.strptime(value, date_format or "")
        except (ValueError, TypeError):
            return None, False
        return parsed.isoformat(), True


class RowNumberHandler:
    """分组窗口行号操作处理器（T3.8 / FR-10 / AC-07 / OP-07）。

    仅 Row Number（从 1 起、无并列、按组内排序键；并列按原行序）；
    dense/standard 并列名次、LAG/累计本期不纳入（OP-07）。
    行序保持原顺序，仅追加序号列（window_output_field 缺省 row_number）。
    """

    operation = DataTransformOperation.ROW_NUMBER
    degradable = False

    def apply(self, ctx: StepContext) -> StepOutcome:
        params = ctx.step.params
        group_by = list(params.window_group_by)
        order_by = list(params.window_order_by)
        orders = list(params.window_orders)
        output_field = params.window_output_field or "row_number"

        # 分组（保持组的首见顺序）
        groups: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = {}
        for row in ctx.left_dataset:
            key = tuple(_stable_serialize(row.get(g)) for g in group_by) if group_by else ()
            if key not in groups:
                groups[key] = []
            groups[key].append(row)

        # 组内按排序键编号（空值排末尾；并列按原行序）
        numbers: Dict[int, int] = {}
        for rows in groups.values():
            if order_by:
                comparator = _make_order_cmp(order_by, orders)
                ordered = sorted(rows, key=cmp_to_key(comparator))
            else:
                ordered = rows
            for number, row in enumerate(ordered, start=1):
                numbers[id(row)] = number

        result: List[Dict[str, Any]] = []
        for row in ctx.left_dataset:  # 行序保持原顺序
            result.append({**row, output_field: numbers[id(row)]})

        return StepOutcome(value=result)


class FlattenHandler:
    """打平嵌套对象操作处理器（T3.9 / FR-11 / AC-08）。

    边界：键路径点号拼接（如 用户.姓名）；max_depth 缺省 3（INV-15），
    超深度截断 + 告警（不失败）；非 dict 值原样保留；
    键名冲突加后缀消歧（_1、_2 …）；flatten_field 空 = 整行打平。
    """

    operation = DataTransformOperation.FLATTEN
    degradable = False

    def apply(self, ctx: StepContext) -> StepOutcome:
        params = ctx.step.params
        field = params.flatten_field
        max_depth = params.max_depth if params.max_depth is not None else DEFAULT_FLATTEN_MAX_DEPTH
        if max_depth < 1:
            return StepOutcome.failed(
                DATA_TRANSFORM_INVALID_STEP_PARAMS,
                f"flatten max_depth 须 ≥ 1（当前 {max_depth}）",
            )

        truncated = 0
        result: List[Dict[str, Any]] = []
        for row in ctx.left_dataset:
            new_row: Dict[str, Any] = {}
            if field:
                for key, value in row.items():
                    if key != field:
                        new_row[key] = value
                nested = row.get(field)
                if isinstance(nested, dict):
                    truncated += self._flatten_into(
                        new_row, field, nested, 1, max_depth
                    )
                else:
                    new_row[field] = nested  # 非 dict 值原样保留（含缺失 → None）
            else:
                truncated += self._flatten_into(new_row, "", row, 1, max_depth)
            result.append(new_row)

        warnings: List[str] = []
        if truncated:
            warnings.append(
                f"flatten 超深度截断 {truncated} 处（max_depth={max_depth}）"
            )
        return StepOutcome(value=result, warnings=warnings)

    @staticmethod
    def _flatten_into(
        target: Dict[str, Any],
        prefix: str,
        nested: Dict[str, Any],
        depth: int,
        max_depth: int,
    ) -> int:
        """递归打平嵌套 dict 到 target；返回截断计数。"""
        truncated = 0
        for key, value in nested.items():
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict) and depth < max_depth:
                truncated += FlattenHandler._flatten_into(
                    target, path, value, depth + 1, max_depth
                )
                continue
            if isinstance(value, dict) and depth >= max_depth:
                # 超深度：保留剩余 dict 原样 + 截断计数
                target[_disambiguate_key(target, path)] = value
                truncated += 1
                continue
            target[_disambiguate_key(target, path)] = value
        return truncated


def _disambiguate_key(target: Dict[str, Any], key: str) -> str:
    """键名冲突加后缀消歧（_1、_2 …，确定性）。"""
    if key not in target:
        return key
    index = 1
    while f"{key}_{index}" in target:
        index += 1
    return f"{key}_{index}"


class ExplodeHandler:
    """展开 JSON 数组为多行操作处理器（T3.10 / FR-11 / AC-08）。

    边界：每行展开为数组元素数量对应行，其余字段复制；
    非数组/空数组 → 该行该字段置空 + 计数（degraded）；
    展开后总行数超 max_rows（缺省 100000，上限 MAX_EXPLODE_ROWS）→
    DATA_TRANSFORM_ROW_LIMIT_EXCEEDED（fail-fast，INV-15）。
    """

    operation = DataTransformOperation.EXPLODE
    degradable = True  # 非数组/空数组置空 + 计数，不中断链

    def apply(self, ctx: StepContext) -> StepOutcome:
        params = ctx.step.params
        field = params.explode_field
        if not field:
            return StepOutcome.failed(
                DATA_TRANSFORM_INVALID_STEP_PARAMS, "explode 缺 explode_field"
            )

        max_rows = params.max_rows if params.max_rows is not None else MAX_EXPLODE_ROWS
        if max_rows < 1:
            return StepOutcome.failed(
                DATA_TRANSFORM_INVALID_STEP_PARAMS,
                f"explode max_rows 须 ≥ 1（当前 {max_rows}）",
            )
        effective_limit = min(max_rows, MAX_EXPLODE_ROWS)

        # 预计算展开规模（超限 fail-fast，不产生部分输出）
        total_rows = 0
        for row in ctx.left_dataset:
            value = row.get(field)
            if isinstance(value, list) and value:
                total_rows += len(value)
            else:
                total_rows += 1
        if total_rows > effective_limit:
            return StepOutcome.failed(
                DATA_TRANSFORM_ROW_LIMIT_EXCEEDED,
                f"explode 展开后总行数 {total_rows} 超上限 {effective_limit}",
            )

        empty_cells = 0
        result: List[Dict[str, Any]] = []
        for row in ctx.left_dataset:
            value = row.get(field)
            if isinstance(value, list) and value:
                for element in value:
                    result.append({**row, field: element})
            else:
                result.append({**row, field: None})  # 置空 + 计数
                empty_cells += 1

        warnings: List[str] = []
        if empty_cells:
            warnings.append(
                f"explode 非数组/空数组 {empty_cells} 行（字段已置空）"
            )
        return StepOutcome(value=result, degraded=bool(empty_cells), warnings=warnings)


# 模块导入时注册（DC-02：注册表为唯一分派权威）
register(MapHandler())
register(FilterHandler())
register(AggregateHandler())
register(JoinHandler())
register(SortHandler())
register(DistinctHandler())
register(RenameHandler())
register(SelectColumnsHandler())
register(DropColumnsHandler())
register(CastHandler())
register(RowNumberHandler())
register(FlattenHandler())
register(ExplodeHandler())
