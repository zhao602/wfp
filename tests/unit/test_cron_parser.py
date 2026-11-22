# =============================================================================
# Cron 解析与 next_fire_at 纯函数单测（tests/unit/test_cron_parser.py）
#
# 覆盖：arch_review_v2_tasks_wave2 任务 4.6/4.7（CHG-55；AC-05 前半）
#       —— 4.1 契约模型 + 4.2 解析纯函数全用例。
# 分层：
#   A. TriggerSchedule 契约模型（CHG-46，design §4.2：组合键/消毒/二选一）；
#   B. 5 段式解析正反例（四语法/DOW 0=周日（7→0）/越界拒/受限标志）；
#   C. next_fire_at 纯函数（锚点/边界严格晚于/跨短月/闰年/Vixie OR/
#      naive-naive 与 aware-UTC 归一/恒假 96 月窗）；
#   D. resolve_next_fire 统一入口（10 预设映射/月末钳制/schedule 优先/双空抛错）。
# 基准日期：2026-11-19 为周四（11-20 周五 / 11-21 周六 / 11-22 周日 /
#      11-23 周一）——纯函数同步断言，零真实定时（spec FR-04 规则 2）。
# 执行命令：PYTHONPATH=src python -m pytest tests/unit/test_cron_parser.py -q
# =============================================================================

from datetime import datetime, timedelta, timezone

import pytest

from orchestration_platform.application.schedule.cron_parser import (
    CronExpression,
    next_fire_at,
    parse_expression,
    resolve_next_fire,
)
from orchestration_platform.application.schedule.models import TriggerSchedule
from orchestration_platform.domain.errors import (
    SCHEDULE_INVALID_CRON,
    WF_VALIDATION_FAILED,
    WorkflowValidationError,
)
from orchestration_platform.domain.models.enums import FrequencyPresetEnum

# 基准时刻：2026-11-19（周四）10:30（naive）
_BASE = datetime(2026, 11, 19, 10, 30)


# =============================================================================
# A. TriggerSchedule 契约模型（CHG-46，design §4.2）
# =============================================================================

class TestTriggerScheduleModel:
    """契约模型：组合键派生/一致性、标识消毒、cron 与预设二选一。"""

    def test_derives_composite_schedule_id(self):
        """schedule_id 缺省由 {workflow_id}:{trigger_node_id} 派生。"""
        schedule = TriggerSchedule(
            workflow_id="wf", trigger_node_id="trig", schedule="0 * * * *"
        )
        assert schedule.schedule_id == "wf:trig"

    def test_accepts_explicit_consistent_schedule_id(self):
        """显式传入组合键且一致时接受。"""
        schedule = TriggerSchedule(
            schedule_id="wf:trig",
            workflow_id="wf",
            trigger_node_id="trig",
            schedule="0 * * * *",
        )
        assert schedule.schedule_id == "wf:trig"

    def test_rejects_inconsistent_schedule_id(self):
        """显式 schedule_id 与派生值漂移 → WF_VALIDATION_FAILED。"""
        with pytest.raises(WorkflowValidationError) as exc_info:
            TriggerSchedule(
                schedule_id="other:thing",
                workflow_id="wf",
                trigger_node_id="trig",
                schedule="0 * * * *",
            )
        assert exc_info.value.code == WF_VALIDATION_FAILED

    def test_rejects_empty_identifiers(self):
        """空标识（strip 后为空）拒绝。"""
        with pytest.raises(WorkflowValidationError) as exc_info:
            TriggerSchedule(
                workflow_id="   ", trigger_node_id="trig", schedule="0 * * * *"
            )
        assert exc_info.value.code == WF_VALIDATION_FAILED

    def test_requires_schedule_or_preset(self):
        """cron 与预设双缺 → SCHEDULE_INVALID_CRON。"""
        with pytest.raises(WorkflowValidationError) as exc_info:
            TriggerSchedule(workflow_id="wf", trigger_node_id="trig")
        assert exc_info.value.code == SCHEDULE_INVALID_CRON

    def test_allows_schedule_and_preset_coexist(self):
        """并存允许（resolve 时 schedule 优先，DC-28 规则 3）。"""
        schedule = TriggerSchedule(
            workflow_id="wf",
            trigger_node_id="trig",
            schedule="0 12 * * *",
            frequency_preset=FrequencyPresetEnum.HOURLY,
        )
        assert schedule.schedule == "0 12 * * *"
        assert schedule.frequency_preset is FrequencyPresetEnum.HOURLY

    def test_strips_identifier_whitespace(self):
        """标识/表达式 strip 回写消毒。"""
        schedule = TriggerSchedule(
            workflow_id=" wf ",
            trigger_node_id=" trig ",
            schedule=" 0 * * * * ",
        )
        assert schedule.workflow_id == "wf"
        assert schedule.trigger_node_id == "trig"
        assert schedule.schedule == "0 * * * *"
        assert schedule.schedule_id == "wf:trig"

    def test_state_defaults(self):
        """注册表维护的派生/幂等状态构造期缺省。"""
        schedule = TriggerSchedule(
            workflow_id="wf",
            trigger_node_id="trig",
            frequency_preset=FrequencyPresetEnum.DAILY,
        )
        assert schedule.enabled is True
        assert schedule.next_fire_at is None
        assert schedule.last_fired_key is None


# =============================================================================
# B. 5 段式解析（CHG-47，DC-28 规则 2）
# =============================================================================

@pytest.mark.parametrize(
    "expression,expect",
    [
        ("*/15 * * * *", {"minutes": {0, 15, 30, 45}}),
        ("0 0 * * *", {"minutes": {0}, "hours": {0}}),
        ("30 8 * * 1-5", {"minutes": {30}, "hours": {8}, "dows": {1, 2, 3, 4, 5}}),
        ("0 0 1 1 *", {"minutes": {0}, "hours": {0}, "doms": {1}, "months": {1}}),
        ("0,30 9 * * *", {"minutes": {0, 30}, "hours": {9}}),
        ("10-30/5 * * * *", {"minutes": {10, 15, 20, 25, 30}}),
        ("0 0 * * 0", {"minutes": {0}, "hours": {0}, "dows": {0}}),
        ("0 12 15 * 5", {"minutes": {0}, "hours": {12}, "doms": {15}, "dows": {5}}),
        ("* * * * *", {}),
    ],
)
def test_parse_valid_expressions(expression, expect):
    """四语法（`*` `,` `-` `/`）正例：字段命中集合精确断言。"""
    expr = parse_expression(expression)
    assert isinstance(expr, CronExpression)
    assert expr.expression == expression
    assert expr.minutes == frozenset(expect.get("minutes", set(range(60))))
    assert expr.hours == frozenset(expect.get("hours", set(range(24))))
    assert expr.doms == frozenset(expect.get("doms", set(range(1, 32))))
    assert expr.months == frozenset(expect.get("months", set(range(1, 13))))
    assert expr.dows == frozenset(expect.get("dows", set(range(7))))


def test_parse_restricted_flags():
    """受限标志（Vixie OR 判据）：`*` 不受限，显式字段受限。"""
    full = parse_expression("* * * * *")
    assert full.dom_restricted is False
    assert full.dow_restricted is False
    both = parse_expression("0 12 15 * 5")
    assert both.dom_restricted is True
    assert both.dow_restricted is True
    only_dom = parse_expression("0 0 15 * *")
    assert only_dom.dom_restricted is True
    assert only_dom.dow_restricted is False


def test_dow_seven_normalized_to_zero():
    """DOW 7 视为 0（对齐主流 cron，DC-28 规则 2）。"""
    expr = parse_expression("0 0 * * 7")
    assert expr.dows == frozenset({0})


@pytest.mark.parametrize(
    "expression",
    [
        "",
        "   ",
        "* * * *",
        "* * * * * *",
        "60 * * * *",
        "* 24 * * *",
        "* * 0 * *",
        "* * 32 * *",
        "* * * 0 *",
        "* * * 13 *",
        "* * * * 8",
        "*/0 * * * *",
        "5-3 * * * *",
        "a * * * *",
    ],
)
def test_parse_invalid_expressions(expression):
    """反例：段数/越界/步长/区间序/非法字符 → SCHEDULE_INVALID_CRON。"""
    with pytest.raises(WorkflowValidationError) as exc_info:
        parse_expression(expression)
    assert exc_info.value.code == SCHEDULE_INVALID_CRON


# =============================================================================
# C. next_fire_at 纯函数（DC-28 规则 1；基准 2026-11-19 周四推演）
# =============================================================================

@pytest.mark.parametrize(
    "expression,after,expected",
    [
        # daily 锚点：次日 00:00
        ("0 0 * * *", _BASE, datetime(2026, 11, 20, 0, 0)),
        # hourly 锚点：下一整点
        ("0 * * * *", _BASE, datetime(2026, 11, 19, 11, 0)),
        # 边界严格晚于：恰在触发时刻 → 下一窗口
        ("0 * * * *", datetime(2026, 11, 19, 10, 0), datetime(2026, 11, 19, 11, 0)),
        # 步长通配
        ("*/15 * * * *", datetime(2026, 11, 19, 10, 20), datetime(2026, 11, 19, 10, 30)),
        # 工作日区间：周四 10:30 → 周五 08:30
        ("30 8 * * 1-5", _BASE, datetime(2026, 11, 20, 8, 30)),
        # 周一锚点跳过周末
        ("0 9 * * 1", _BASE, datetime(2026, 11, 23, 9, 0)),
        # 周日（DOW 0）
        ("0 0 * * 0", _BASE, datetime(2026, 11, 22, 0, 0)),
        # 周六（DOW 6）
        ("0 0 * * 6", _BASE, datetime(2026, 11, 21, 0, 0)),
        # 月锚点
        ("0 0 1 * *", _BASE, datetime(2026, 12, 1, 0, 0)),
        # 年锚点
        ("0 0 1 1 *", _BASE, datetime(2027, 1, 1, 0, 0)),
        # 31 日跨短月：2027-01-31 已过 31 日 00:00 → 下一个有 31 日的月
        ("0 0 31 * *", datetime(2027, 1, 31, 12, 0), datetime(2027, 3, 31, 0, 0)),
        # 闰年：2-29 在 2028 才可触发
        ("0 0 29 2 *", datetime(2026, 1, 1), datetime(2028, 2, 29, 0, 0)),
        # Vixie OR（dom 侧）：11-15 周日命中 dom=15
        ("0 0 15 * 5", datetime(2026, 11, 14, 10, 0), datetime(2026, 11, 15, 0, 0)),
        # Vixie OR（dow 侧）：11-19 周四不命中 dom/dow → 11-20 周五命中 dow=5
        ("0 0 15 * 5", _BASE, datetime(2026, 11, 20, 0, 0)),
    ],
)
def test_next_fire_at(expression, after, expected):
    """纯函数边界：锚点/跳搜/跨短月/闰年/Vixie OR 全推演。"""
    assert next_fire_at(expression, after) == expected


def test_naive_input_returns_naive():
    """naive 输入返回 naive（时区注入边界一致）。"""
    result = next_fire_at("0 0 * * *", _BASE)
    assert result.tzinfo is None


def test_aware_input_normalized_to_utc():
    """aware 输入（非 UTC 偏移）归一 UTC 后计算。"""
    cst = timezone(timedelta(hours=8))
    after = datetime(2026, 11, 19, 18, 30, tzinfo=cst)  # = 10:30 UTC
    result = next_fire_at("0 * * * *", after)
    assert result.tzinfo is not None
    assert result.utcoffset().total_seconds() == 0
    assert result.replace(tzinfo=None) == datetime(2026, 11, 19, 11, 0)


def test_constant_false_expression_raises():
    """恒假表达式（2 月 30 日）在 96 月窗口内无解 → SCHEDULE_INVALID_CRON。"""
    with pytest.raises(WorkflowValidationError) as exc_info:
        next_fire_at("0 0 30 2 *", datetime(2026, 1, 1))
    assert exc_info.value.code == SCHEDULE_INVALID_CRON


# =============================================================================
# D. resolve_next_fire 统一入口（DC-28 规则 3，PC-8：10 预设零改造消费）
# =============================================================================

@pytest.mark.parametrize(
    "preset,expected",
    [
        # 固定频率：下一锚点整点（等价 cron）
        (FrequencyPresetEnum.HOURLY, datetime(2026, 11, 19, 11, 0)),
        (FrequencyPresetEnum.DAILY, datetime(2026, 11, 20, 0, 0)),
        (FrequencyPresetEnum.WEEKLY, datetime(2026, 11, 22, 0, 0)),
        (FrequencyPresetEnum.MONTHLY, datetime(2026, 12, 1, 0, 0)),
        (FrequencyPresetEnum.YEARLY, datetime(2027, 1, 1, 0, 0)),
        # 间隔频率：now + period
        (FrequencyPresetEnum.INTERVAL_MINUTES, datetime(2026, 11, 19, 10, 31)),
        (FrequencyPresetEnum.INTERVAL_HOURS, datetime(2026, 11, 19, 11, 30)),
        (FrequencyPresetEnum.INTERVAL_DAYS, datetime(2026, 11, 20, 10, 30)),
        (FrequencyPresetEnum.INTERVAL_WEEKS, datetime(2026, 11, 26, 10, 30)),
        (FrequencyPresetEnum.INTERVAL_MONTHS, datetime(2026, 12, 19, 10, 30)),
    ],
)
def test_resolve_preset_mapping(preset, expected):
    """10 种频率预设映射（前端选择器产物零改造消费）。"""
    assert resolve_next_fire("", preset, _BASE) == expected


def test_interval_months_month_end_clamp():
    """INTERVAL_MONTHS 月末钳制：1-31 → 2-28（目标月无对应日取最后一天）。"""
    after = datetime(2026, 1, 31, 10, 0)
    result = resolve_next_fire("", FrequencyPresetEnum.INTERVAL_MONTHS, after)
    assert result == datetime(2026, 2, 28, 10, 0)


def test_schedule_takes_priority_over_preset():
    """并存时 schedule 优先（不走预设映射）。"""
    result = resolve_next_fire(
        "0 12 * * *", FrequencyPresetEnum.HOURLY, _BASE
    )
    assert result == datetime(2026, 11, 19, 12, 0)


def test_schedule_whitespace_still_priority():
    """strip 后非空的 schedule 仍优先。"""
    result = resolve_next_fire("  0 12 * * *  ", None, _BASE)
    assert result == datetime(2026, 11, 19, 12, 0)


def test_blank_schedule_falls_back_to_preset():
    """schedule 为空白时回退预设映射。"""
    result = resolve_next_fire("   ", FrequencyPresetEnum.HOURLY, _BASE)
    assert result == datetime(2026, 11, 19, 11, 0)


def test_both_missing_raises():
    """schedule 与预设双空 → SCHEDULE_INVALID_CRON。"""
    with pytest.raises(WorkflowValidationError) as exc_info:
        resolve_next_fire("", None, _BASE)
    assert exc_info.value.code == SCHEDULE_INVALID_CRON


def test_invalid_schedule_raises_even_with_preset():
    """schedule 优先意味着非法 schedule 直接抛错（不回退预设）。"""
    with pytest.raises(WorkflowValidationError) as exc_info:
        resolve_next_fire("bad cron", FrequencyPresetEnum.HOURLY, _BASE)
    assert exc_info.value.code == SCHEDULE_INVALID_CRON
