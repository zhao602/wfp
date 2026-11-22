# =============================================================================
# 采样取值域唯一权威单元测试（llm_and_code_nodes 任务 2.2 / CHG-03）
# 执行命令：PYTHONPATH=src python -m pytest tests/unit/test_llm_sampling_spec.py -q
#
# 覆盖（spec §6.3 / DC-04 / DC-05 / D-01 / INV-12）：
#   - 登记表结构：六参数键序、逐项 kind/区间/缺省/步进/发送策略；
#   - validate_sampling 边界表：五数值参数与 stop 的通过/拒绝逐项边界，
#     拒绝消息含「参数名 + 实际值 + 允许区间」（不静默截断）；
#   - resolve_sampling 语义缺省：未配置 → 登记表缺省，显式值原样保留；
#   - to_request_payload 最小键集（G-06 判据）：A1 全 None 恰两键、
#     A2 逐参数显式、top_k=0 不发、stop 去重保序、越界抛 ValueError。
# =============================================================================

# 沙箱限制：本机 asyncio 事件循环不可用（socketpair 被网络策略拦截，
# WinError 10051 / _ssock AttributeError），run_coro 采用手动驱动协程
# 方式（send(None) 循环），不创建事件循环，故无需 import asyncio。
# 本轮尚无异步用例，按任务 2.8 要求保持文件结构一致，供后续批次复用。

import pytest

from orchestration_platform.domain.models.node_configurations import LlmSamplingParameters
from orchestration_platform.domain.values.llm_sampling_spec import (
    SAMPLING_PARAM_SPECS,
    STOP_ITEM_MAX_CHARS,
    STOP_ITEM_MIN_CHARS,
    STOP_MAX_ITEMS,
    SamplingParamSpec,
    resolve_sampling,
    to_request_payload,
    validate_sampling,
)


def run_coro(coro):
    """手动驱动协程至完成（沙箱事件循环不可用时的 fallback）。

    execute 的 await 链均为 AsyncMock 或纯同步逻辑协程，无真实挂起点，
    故以 send(None) 循环驱动至 StopIteration，环境无关。
    """
    try:
        while True:
            coro.send(None)
    except StopIteration as exc:
        return exc.value


def _params(**kwargs) -> LlmSamplingParameters:
    """构造采样参数值对象（未给字段保持 None=未配置）。"""
    return LlmSamplingParameters(**kwargs)


# =============================================================================
# 登记表结构（单一权威 INV-12）
# =============================================================================


class TestSamplingSpecTable:
    """取值域登记表：六参数键序与逐项登记值（spec §6.3 逐项对齐）。"""

    def test_six_params_in_canonical_order(self):
        assert list(SAMPLING_PARAM_SPECS.keys()) == [
            "temperature",
            "top_p",
            "top_k",
            "max_tokens",
            "stop",
            "seed",
        ]

    @pytest.mark.parametrize(
        "name",
        ["temperature", "top_p", "top_k", "max_tokens", "stop", "seed"],
    )
    def test_each_entry_is_sampling_param_spec(self, name):
        assert isinstance(SAMPLING_PARAM_SPECS[name], SamplingParamSpec)

    def test_temperature_spec(self):
        spec = SAMPLING_PARAM_SPECS["temperature"]
        assert spec.kind == "float"
        assert spec.minimum == 0.0
        assert spec.maximum == 2.0
        assert spec.exclusive_minimum is False
        assert spec.default == 0.7
        assert spec.step == 0.1
        assert spec.request_always is True

    def test_top_p_spec(self):
        spec = SAMPLING_PARAM_SPECS["top_p"]
        assert spec.kind == "float"
        assert spec.minimum == 0.0
        assert spec.maximum == 1.0
        assert spec.exclusive_minimum is True
        assert spec.default == 1.0
        assert spec.step == 0.05
        assert spec.request_always is False

    def test_top_k_spec(self):
        spec = SAMPLING_PARAM_SPECS["top_k"]
        assert spec.kind == "int"
        assert spec.minimum == 0
        assert spec.maximum == 100
        assert spec.exclusive_minimum is False
        assert spec.default == 0
        assert spec.step is None
        assert spec.request_always is False

    def test_max_tokens_spec(self):
        spec = SAMPLING_PARAM_SPECS["max_tokens"]
        assert spec.kind == "int"
        assert spec.minimum == 1
        assert spec.maximum == 32768
        assert spec.exclusive_minimum is False
        assert spec.default == 4096
        assert spec.step is None
        assert spec.request_always is True

    def test_stop_spec(self):
        spec = SAMPLING_PARAM_SPECS["stop"]
        assert spec.kind == "str_list"
        assert spec.minimum is None
        assert spec.maximum is None
        assert spec.exclusive_minimum is False
        assert spec.default == []
        assert spec.step is None
        assert spec.request_always is False

    def test_seed_spec(self):
        spec = SAMPLING_PARAM_SPECS["seed"]
        assert spec.kind == "int"
        assert spec.minimum == 0
        assert spec.maximum == 2147483647
        assert spec.exclusive_minimum is False
        assert spec.default is None
        assert spec.step is None
        assert spec.request_always is False

    def test_stop_limits_constants(self):
        assert STOP_MAX_ITEMS == 4
        assert STOP_ITEM_MIN_CHARS == 1
        assert STOP_ITEM_MAX_CHARS == 128


# =============================================================================
# validate_sampling 边界表（DC-04：拒绝时不静默截断）
# =============================================================================


class TestValidateSampling:
    """越界检测边界表（任务 2.2 验收：0/2/2.5、0/1/1.0001、0/1/100/101、
    0/1/32768/32769、5 条 stop、seed 边界逐项按预期通过或拒绝）。"""

    @pytest.mark.parametrize("value", [0.0, 0.7, 2.0])
    def test_temperature_in_range_passes(self, value):
        assert validate_sampling(_params(temperature=value)) == []

    @pytest.mark.parametrize("value", [-0.1, 2.5])
    def test_temperature_out_of_range_rejected(self, value):
        violations = validate_sampling(_params(temperature=value))
        assert violations == [f"温度 {value} 超出 [0.0, 2.0]"]

    @pytest.mark.parametrize("value", [0.05, 0.5, 1.0])
    def test_top_p_in_range_passes(self, value):
        assert validate_sampling(_params(top_p=value)) == []

    @pytest.mark.parametrize("value", [0.0, 1.0001])
    def test_top_p_out_of_range_rejected(self, value):
        violations = validate_sampling(_params(top_p=value))
        assert violations == [f"Top P {value} 超出 (0.0, 1.0]"]

    @pytest.mark.parametrize("value", [0, 1, 100])
    def test_top_k_in_range_passes(self, value):
        assert validate_sampling(_params(top_k=value)) == []

    @pytest.mark.parametrize("value", [-1, 101])
    def test_top_k_out_of_range_rejected(self, value):
        violations = validate_sampling(_params(top_k=value))
        assert violations == [f"Top K {value} 超出 [0, 100]"]

    @pytest.mark.parametrize("value", [1, 4096, 32768])
    def test_max_tokens_in_range_passes(self, value):
        assert validate_sampling(_params(max_tokens=value)) == []

    @pytest.mark.parametrize("value", [0, 32769])
    def test_max_tokens_out_of_range_rejected(self, value):
        violations = validate_sampling(_params(max_tokens=value))
        assert violations == [f"最大生成长度 {value} 超出 [1, 32768]"]

    @pytest.mark.parametrize("value", [0, 1, 2147483647])
    def test_seed_in_range_passes(self, value):
        assert validate_sampling(_params(seed=value)) == []

    @pytest.mark.parametrize("value", [-1, 2147483648])
    def test_seed_out_of_range_rejected(self, value):
        violations = validate_sampling(_params(seed=value))
        assert violations == [f"随机种子 {value} 超出 [0, 2147483647]"]

    def test_stop_four_items_passes(self):
        assert validate_sampling(_params(stop=["a", "b", "c", "d"])) == []

    def test_stop_five_items_rejected(self):
        violations = validate_sampling(_params(stop=["a", "b", "c", "d", "e"]))
        assert violations == ["停止序列条数 5 超出 [0, 4]"]

    def test_stop_item_128_chars_passes(self):
        assert validate_sampling(_params(stop=["x" * 128])) == []

    def test_stop_item_129_chars_rejected(self):
        violations = validate_sampling(_params(stop=["x" * 129]))
        assert violations == ["停止序列第 1 条长度 129 超出 [1, 128]"]

    def test_stop_empty_item_rejected(self):
        violations = validate_sampling(_params(stop=[""]))
        assert violations == ["停止序列第 1 条长度 0 超出 [1, 128]"]

    def test_all_none_yields_no_violations(self):
        assert validate_sampling(LlmSamplingParameters()) == []

    def test_multiple_violations_all_reported_in_order(self):
        violations = validate_sampling(
            _params(
                temperature=2.5,
                top_p=0.0,
                top_k=101,
                max_tokens=0,
                seed=-1,
                stop=["x" * 129, "y", "z", "w", "v"],
            )
        )
        assert violations == [
            "温度 2.5 超出 [0.0, 2.0]",
            "Top P 0.0 超出 (0.0, 1.0]",
            "Top K 101 超出 [0, 100]",
            "最大生成长度 0 超出 [1, 32768]",
            "随机种子 -1 超出 [0, 2147483647]",
            "停止序列条数 5 超出 [0, 4]",
            "停止序列第 1 条长度 129 超出 [1, 128]",
        ]


# =============================================================================
# resolve_sampling 语义缺省（D-01：未配置与显式缺省可区分）
# =============================================================================


class TestResolveSampling:
    """语义缺省解析：未配置字段取登记表缺省，显式值原样保留。"""

    def test_all_none_resolves_to_semantic_defaults(self):
        assert resolve_sampling(LlmSamplingParameters()) == {
            "temperature": 0.7,
            "top_p": 1.0,
            "top_k": 0,
            "max_tokens": 4096,
            "stop": [],
            "seed": None,
        }

    def test_explicit_values_are_preserved(self):
        resolved = resolve_sampling(
            _params(
                temperature=0.2,
                top_p=0.9,
                top_k=50,
                max_tokens=1024,
                stop=["END"],
                seed=42,
            )
        )
        assert resolved == {
            "temperature": 0.2,
            "top_p": 0.9,
            "top_k": 50,
            "max_tokens": 1024,
            "stop": ["END"],
            "seed": 42,
        }

    def test_stop_dedup_preserves_order(self):
        resolved = resolve_sampling(_params(stop=["b", "a", "b", "a"]))
        assert resolved["stop"] == ["b", "a"]

    def test_seed_none_stays_none(self):
        assert resolve_sampling(LlmSamplingParameters())["seed"] is None


# =============================================================================
# to_request_payload 最小键集（DC-05 / G-06 判据）
# =============================================================================


class TestToRequestPayload:
    """请求体采样键组装：最小键集矩阵逐项复现。"""

    def test_a1_all_none_minimal_keyset(self):
        assert to_request_payload(LlmSamplingParameters()) == {
            "temperature": 0.7,
            "max_tokens": 4096,
        }

    def test_a2_all_explicit_sends_all_keys(self):
        payload = to_request_payload(
            _params(
                temperature=0.2,
                top_p=0.9,
                top_k=50,
                max_tokens=1024,
                stop=["END"],
                seed=42,
            )
        )
        assert payload == {
            "temperature": 0.2,
            "top_p": 0.9,
            "top_k": 50,
            "max_tokens": 1024,
            "stop": ["END"],
            "seed": 42,
        }

    def test_top_k_zero_not_sent(self):
        payload = to_request_payload(_params(top_k=0))
        assert "top_k" not in payload

    def test_top_p_explicit_default_still_sent(self):
        payload = to_request_payload(_params(top_p=1.0))
        assert payload["top_p"] == 1.0

    def test_empty_stop_not_sent(self):
        payload = to_request_payload(_params(stop=[]))
        assert "stop" not in payload

    def test_stop_dedup_before_send(self):
        payload = to_request_payload(_params(stop=["a", "b", "a"]))
        assert payload["stop"] == ["a", "b"]

    def test_seed_zero_explicit_sent(self):
        payload = to_request_payload(_params(seed=0))
        assert payload["seed"] == 0

    def test_out_of_range_raises_value_error(self):
        with pytest.raises(ValueError, match="温度 2.5 超出"):
            to_request_payload(_params(temperature=2.5))

    def test_multiple_violations_joined_in_message(self):
        with pytest.raises(ValueError) as excinfo:
            to_request_payload(_params(temperature=2.5, top_k=101))
        message = str(excinfo.value)
        assert "温度 2.5 超出 [0.0, 2.0]" in message
        assert "Top K 101 超出 [0, 100]" in message
        assert "；" in message
