# =============================================================================
# Cron 定时触发纯函数内核（application/schedule/cron_parser.py）
#
# 职责：5 段式 cron（分/时/日/月/周）解析 + next_fire_at 纯函数 +
#       FrequencyPresetEnum 10 种预设统一映射入口
#       （arch_review_v2_tasks_wave2 G3，CHG-47，DC-28 规则 1/2/3）。
# 设计意图：
#   1. 零新增主依赖（HC-06，否决第三方调度框架）——解析/下次触发/预设映射
#      全部标准库纯函数，无全局态，同步可测（spec FR-04 规则 2）；
#   2. 四语法（`*` `,` `-` `/`）+ DOW 0=周日（7 视为 0）；DOM 与 DOW 双受限
#      时按 Vixie cron OR 语义匹配（主流 cron 对齐，DC-28 规则 2）；
#   3. 与既有校验器（node_configuration_validator.py 仅非空校验，E-W2-10）
#      互补不冲突——校验链零改动（HC-05 / Z-10），解析失败抛
#      SCHEDULE_INVALID_CRON（注册期 HTTP 422，扫描期仅告警跳过）；
#   4. naive 输入返回 naive，aware 输入归一 UTC 后计算（时区边界一致）。
# =============================================================================

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, FrozenSet, Optional, Set, Tuple, Union

from orchestration_platform.domain.errors import (
    SCHEDULE_INVALID_CRON,
    WorkflowValidationError,
)
from orchestration_platform.domain.models.enums import FrequencyPresetEnum

# 智能跳搜的月度窗口上限：超过视为恒假表达式（如「0 0 31 2 *」——2 月 31 日不存在）
_MAX_SEARCH_MONTHS = 96

# 各字段取值边界（DOW 为 0-7，7 归一为 0——对齐主流 cron 与 Quartz 差异取舍）
_FIELD_BOUNDS: Dict[str, Tuple[int, int]] = {
    "minute": (0, 59),
    "hour": (0, 23),
    "dom": (1, 31),
    "month": (1, 12),
    "dow": (0, 7),
}

# 固定频率预设 → 等价 5 段式 cron（下一锚点整点语义，DC-28 规则 3）
_PRESET_CRON: Dict[FrequencyPresetEnum, str] = {
    FrequencyPresetEnum.HOURLY: "0 * * * *",
    FrequencyPresetEnum.DAILY: "0 0 * * *",
    FrequencyPresetEnum.WEEKLY: "0 0 * * 0",
    FrequencyPresetEnum.MONTHLY: "0 0 1 * *",
    FrequencyPresetEnum.YEARLY: "0 0 1 1 *",
}

# 间隔频率预设 → 等价时间差（interval 类 = now + period，DC-28 规则 3）
_PRESET_INTERVALS: Dict[FrequencyPresetEnum, timedelta] = {
    FrequencyPresetEnum.INTERVAL_MINUTES: timedelta(minutes=1),
    FrequencyPresetEnum.INTERVAL_HOURS: timedelta(hours=1),
    FrequencyPresetEnum.INTERVAL_DAYS: timedelta(days=1),
    FrequencyPresetEnum.INTERVAL_WEEKS: timedelta(weeks=1),
}


def _invalid(message: str, expression: str = "") -> WorkflowValidationError:
    """构造 SCHEDULE_INVALID_CRON 解析失败异常（注册期 422 / 扫描期告警跳过）。"""
    details = {"expression": expression} if expression else None
    return WorkflowValidationError(SCHEDULE_INVALID_CRON, message, details)


def _days_in_month(year: int, month: int) -> int:
    """返回指定年月的天数（标准库 datetime 推算）。"""
    if month == 12:
        next_year, next_month = year + 1, 1
    else:
        next_year, next_month = year, month + 1
    return (datetime(next_year, next_month, 1) - timedelta(days=1)).day


def _add_one_month(value: datetime) -> datetime:
    """加一个月（月末钳制：目标月无对应日时取当月最后一天）。

    用途：① cron 搜索的月份跳进（日已归 1，钳制不生效）；
         ② INTERVAL_MONTHS 预设（after=1-31 → 次月 28/29，钳制生效）。
    """
    year = value.year + (1 if value.month == 12 else 0)
    month = 1 if value.month == 12 else value.month + 1
    day = min(value.day, _days_in_month(year, month))
    return value.replace(year=year, month=month, day=day)


@dataclass(frozen=True)
class CronExpression:
    """解析后的 5 段式 cron 表达式（不可变值对象，纯函数产物）。

    属性：
        expression                    —— 原始表达式文本（诊断/日志定位）
        minutes/hours/doms/months/dows —— 各字段命中值集合（DOW 已 7→0 归一）
        dom_restricted/dow_restricted —— 字段是否非 `*`（Vixie OR 判据）
    """

    expression: str
    minutes: FrozenSet[int]
    hours: FrozenSet[int]
    doms: FrozenSet[int]
    months: FrozenSet[int]
    dows: FrozenSet[int]
    dom_restricted: bool
    dow_restricted: bool


def _parse_field(field: str, name: str) -> Tuple[FrozenSet[int], bool]:
    """解析单字段：`*` `,` `-` `/` 四语法（步长 ≥1 / 区间 a<=b / 越界拒）。

    返回 (命中值集合, 是否受限)；受限 = 字段文本非 `*`（Vixie OR 判据）。
    """
    lo, hi = _FIELD_BOUNDS[name]
    text = field.strip()
    if not text:
        raise _invalid(f"cron {name} 字段为空", field)
    values: Set[int] = set()
    for item in text.split(","):
        item = item.strip()
        if not item:
            raise _invalid(f"cron {name} 字段含空列表段", text)
        base, step = item, 1
        if "/" in item:
            base, _, step_text = item.partition("/")
            if not step_text:
                raise _invalid(f"cron {name} 字段步长缺失：{item}", text)
            try:
                step = int(step_text)
            except ValueError:
                raise _invalid(f"cron {name} 字段步长非整数：{step_text}", text) from None
            if step < 1:
                raise _invalid(f"cron {name} 字段步长须 ≥ 1：{item}", text)
        if base == "*":
            start, end = lo, hi
        else:
            if "-" in base:
                left, _, right = base.partition("-")
                try:
                    start, end = int(left), int(right)
                except ValueError:
                    raise _invalid(f"cron {name} 字段区间非整数：{base}", text) from None
                if start > end:
                    raise _invalid(f"cron {name} 字段区间下界须 ≤ 上界：{base}", text)
            else:
                try:
                    start = end = int(base)
                except ValueError:
                    raise _invalid(f"cron {name} 字段含非法字符：{base}", text) from None
            if start < lo or end > hi:
                raise _invalid(f"cron {name} 字段越界（{lo}-{hi}）：{base}", text)
        values.update(range(start, end + 1, step))
    if name == "dow":
        # DOW 7 视为 0（对齐主流 cron，DC-28 规则 2）
        values = {value % 7 for value in values}
    return frozenset(values), text != "*"


def parse_expression(expression: str) -> CronExpression:
    """解析 5 段式 cron 表达式（分/时/日/月/周），失败抛 SCHEDULE_INVALID_CRON。"""
    text = (expression or "").strip()
    if not text:
        raise _invalid("cron 表达式为空", expression or "")
    parts = text.split()
    if len(parts) != 5:
        raise _invalid(
            f"cron 表达式须为 5 段式（分/时/日/月/周），实际 {len(parts)} 段",
            text,
        )
    minutes, _ = _parse_field(parts[0], "minute")
    hours, _ = _parse_field(parts[1], "hour")
    doms, dom_restricted = _parse_field(parts[2], "dom")
    months, _ = _parse_field(parts[3], "month")
    dows, dow_restricted = _parse_field(parts[4], "dow")
    return CronExpression(
        expression=text,
        minutes=minutes,
        hours=hours,
        doms=doms,
        months=months,
        dows=dows,
        dom_restricted=dom_restricted,
        dow_restricted=dow_restricted,
    )


def _day_matches(expression: CronExpression, candidate: datetime) -> bool:
    """日匹配：DOM 与 DOW 双受限时按 Vixie cron OR 语义（DC-28 规则 2）。"""
    dom_match = candidate.day in expression.doms
    # Python weekday()：周一=0..周日=6 → cron 周日=0..周六=6（+1 后取模归一）
    dow_match = (candidate.weekday() + 1) % 7 in expression.dows
    if expression.dom_restricted and expression.dow_restricted:
        return dom_match or dow_match
    return dom_match and dow_match


def next_fire_at(expression: Union[str, CronExpression], after: datetime) -> datetime:
    """计算严格晚于 after 的下一次触发时刻（纯函数，无全局态）。

    naive 输入返回 naive；aware 输入归一 UTC 后计算并返回 aware UTC。
    恒假表达式（如 2 月 30 日）在 _MAX_SEARCH_MONTHS 月窗口内无解时抛错。
    """
    parsed = expression if isinstance(expression, CronExpression) else parse_expression(expression)
    if after.tzinfo is not None:
        work = after.astimezone(timezone.utc)
    else:
        work = after
    # 候选起点：秒/微秒归零后 +1 分钟——保证严格晚于 after
    candidate = work.replace(second=0, microsecond=0) + timedelta(minutes=1)
    months_searched = 1
    while True:
        if candidate.month not in parsed.months:
            candidate = _add_one_month(candidate.replace(day=1, hour=0, minute=0))
            months_searched += 1
            if months_searched > _MAX_SEARCH_MONTHS:
                raise _invalid(
                    f"cron 表达式在 {_MAX_SEARCH_MONTHS} 个月内无可触发时刻（恒假）："
                    f"{parsed.expression}",
                    parsed.expression,
                )
            continue
        if not _day_matches(parsed, candidate):
            candidate = candidate.replace(hour=0, minute=0) + timedelta(days=1)
            continue
        if candidate.hour not in parsed.hours:
            candidate = candidate.replace(minute=0) + timedelta(hours=1)
            continue
        if candidate.minute not in parsed.minutes:
            candidate = candidate + timedelta(minutes=1)
            continue
        return candidate


def _next_fire_for_preset(preset: FrequencyPresetEnum, after: datetime) -> datetime:
    """10 种频率预设映射：固定频率走等价 cron 锚点，间隔频率走 now + period。"""
    cron_text = _PRESET_CRON.get(preset)
    if cron_text is not None:
        return next_fire_at(cron_text, after)
    interval = _PRESET_INTERVALS.get(preset)
    if interval is not None:
        return after + interval
    if preset == FrequencyPresetEnum.INTERVAL_MONTHS:
        return _add_one_month(after)
    raise _invalid(f"未知频率预设：{preset.value}")


def resolve_next_fire(
    schedule_value: str,
    preset: Optional[FrequencyPresetEnum],
    after: datetime,
) -> datetime:
    """统一入口（DC-28 规则 3）：5 段式优先，其次预设映射；双空抛错。

    前端频率选择器产物（trigger_schedule_frequency_selector）经此零改造消费。
    """
    schedule_text = (schedule_value or "").strip()
    if schedule_text:
        return next_fire_at(schedule_text, after)
    if preset is not None:
        return _next_fire_for_preset(preset, after)
    raise _invalid("schedule（5 段式 cron）与 frequency_preset 须至少提供一项")
