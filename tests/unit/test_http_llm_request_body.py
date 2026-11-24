# =============================================================================
# build_chat_request_body 单元测试（tests/unit/test_http_llm_request_body.py）
#
# llm_and_code_nodes 任务 4.2 / 4.5（CHG-12 / DC-05 / DC-12 / FR-14 / FR-17 /
# FR-18 / AC-14 / AC-16 / AC-19）：
#   A1 未配置采样（sampling=None → temperature:0.7 / max_tokens:16384；
#      六参数全 None → 经 llm_sampling_spec 语义缺省 max_tokens:4096）
#      （AC-16 核心，断言键集合相等而非仅存在两键）；
#   A2 逐参数显式 → 对应键出现且值一致；top_k=0 / stop=[] 不发送；
#   越界 sampling → ValueError（不静默截断，DC-04）。
#
# 被测对象为模块级纯函数：同步断言，无需事件循环（沙箱无 pytest-asyncio 依赖）。
# =============================================================================

import pytest

from orchestration_platform.domain.models.node_configurations import (
    LlmSamplingParameters,
)
from orchestration_platform.integrations.abstractions.external_clients import (
    InferenceRequest,
)
from orchestration_platform.integrations.llm.http_llm_client import (
    build_chat_request_body,
)

# AC-16 缺省键集（temperature/max_tokens 恒发，其余四键不出现）
_BASE_KEYS = {"model", "messages", "temperature", "max_tokens"}
# 全参数显式时采样相关键全集（DC-05）
_FULL_KEYS = _BASE_KEYS | {"top_p", "top_k", "stop", "seed"}


def _make_request(**kwargs) -> InferenceRequest:
    defaults: dict = {
        "model_identifier": "gpt-4o-mini",
        "system_prompt": "sys-prompt",
        "user_message": "user-msg",
    }
    defaults.update(kwargs)
    return InferenceRequest(**defaults)


class TestBuildChatRequestBodyA1:
    """A1：未配置采样 → 与既有实现逐字节等价（AC-16 核心）。"""

    def test_a1_sampling_none_exact_keys(self):
        body = build_chat_request_body(_make_request())
        assert set(body.keys()) == _BASE_KEYS
        assert body["temperature"] == 0.7
        assert body["max_tokens"] == 16384
        for absent in ("top_p", "top_k", "stop", "seed"):
            assert absent not in body

    def test_a1_all_params_none_exact_keys(self):
        body = build_chat_request_body(_make_request(sampling=LlmSamplingParameters()))
        assert set(body.keys()) == _BASE_KEYS
        assert body["temperature"] == 0.7
        assert body["max_tokens"] == 4096
        for absent in ("top_p", "top_k", "stop", "seed"):
            assert absent not in body

    def test_messages_structure_exact(self):
        body = build_chat_request_body(_make_request())
        assert body["messages"] == [
            {"role": "system", "content": "sys-prompt"},
            {"role": "user", "content": "user-msg"},
        ]


class TestBuildChatRequestBodyModel:
    """model 取值：请求标识优先，为空回退调用方缺省（既有取值逻辑）。"""

    def test_model_from_request_identifier(self):
        body = build_chat_request_body(_make_request())
        assert body["model"] == "gpt-4o-mini"

    def test_model_fallback_to_fallback_model(self):
        body = build_chat_request_body(
            _make_request(model_identifier=""), fallback_model="gpt-4"
        )
        assert body["model"] == "gpt-4"

    def test_model_fallback_default_empty(self):
        body = build_chat_request_body(_make_request(model_identifier=""))
        assert body["model"] == ""


class TestBuildChatRequestBodyA2:
    """A2：逐参数显式 → 对应键出现且值一致（AC-14 六参数支持）。"""

    @pytest.mark.parametrize(
        ("sampling_kwargs", "expected_key", "expected_value"),
        [
            ({"temperature": 0.3}, "temperature", 0.3),
            ({"top_p": 0.9}, "top_p", 0.9),
            ({"top_k": 40}, "top_k", 40),
            ({"max_tokens": 1024}, "max_tokens", 1024),
            ({"stop": ["END"]}, "stop", ["END"]),
            ({"seed": 42}, "seed", 42),
        ],
    )
    def test_a2_param_explicit(self, sampling_kwargs, expected_key, expected_value):
        body = build_chat_request_body(
            _make_request(sampling=LlmSamplingParameters(**sampling_kwargs))
        )
        assert body[expected_key] == expected_value
        if expected_key in ("temperature", "max_tokens"):
            assert set(body.keys()) == _BASE_KEYS
        else:
            assert set(body.keys()) == _BASE_KEYS | {expected_key}

    def test_top_k_zero_not_sent(self):
        body = build_chat_request_body(
            _make_request(sampling=LlmSamplingParameters(top_k=0))
        )
        assert set(body.keys()) == _BASE_KEYS

    def test_stop_empty_not_sent(self):
        body = build_chat_request_body(
            _make_request(sampling=LlmSamplingParameters(stop=[]))
        )
        assert set(body.keys()) == _BASE_KEYS

    def test_stop_dedup_preserves_order(self):
        body = build_chat_request_body(
            _make_request(sampling=LlmSamplingParameters(stop=["A", "B", "A", "C"]))
        )
        assert body["stop"] == ["A", "B", "C"]

    def test_all_params_set_full_keys(self):
        sampling = LlmSamplingParameters(
            temperature=0.2,
            top_p=0.95,
            top_k=64,
            max_tokens=2048,
            stop=["END"],
            seed=7,
        )
        body = build_chat_request_body(_make_request(sampling=sampling))
        assert set(body.keys()) == _FULL_KEYS
        assert body["temperature"] == 0.2
        assert body["top_p"] == 0.95
        assert body["top_k"] == 64
        assert body["max_tokens"] == 2048
        assert body["stop"] == ["END"]
        assert body["seed"] == 7

    def test_boundary_values_pass(self):
        sampling = LlmSamplingParameters(
            temperature=2.0,
            top_p=1.0,
            top_k=100,
            max_tokens=32768,
            stop=["A", "B", "C", "D"],
            seed=2147483647,
        )
        body = build_chat_request_body(_make_request(sampling=sampling))
        assert set(body.keys()) == _FULL_KEYS
        assert body["temperature"] == 2.0
        assert body["top_p"] == 1.0
        assert body["top_k"] == 100
        assert body["max_tokens"] == 32768
        assert body["stop"] == ["A", "B", "C", "D"]
        assert body["seed"] == 2147483647


class TestBuildChatRequestBodyViolations:
    """越界 sampling → ValueError，违规消息含参数名/实际值/允许区间（DC-04）。"""

    @pytest.mark.parametrize(
        ("sampling_kwargs", "match"),
        [
            ({"temperature": 2.5}, r"温度 2\.5 超出 \[0\.0, 2\.0\]"),
            ({"top_p": 0.0}, r"Top P 0\.0 超出 \(0\.0, 1\.0\]"),
            ({"top_k": 101}, r"Top K 101 超出 \[0, 100\]"),
            ({"max_tokens": 0}, r"最大生成长度 0 超出 \[1, 32768\]"),
            ({"seed": -1}, r"随机种子 -1 超出 \[0, 2147483647\]"),
            ({"stop": ["A", "B", "C", "D", "E"]}, r"停止序列条数 5 超出 \[0, 4\]"),
            ({"stop": ["x" * 129]}, r"停止序列第 1 条长度 129 超出 \[1, 128\]"),
        ],
    )
    def test_out_of_range_raises(self, sampling_kwargs, match):
        with pytest.raises(ValueError, match=match):
            build_chat_request_body(
                _make_request(sampling=LlmSamplingParameters(**sampling_kwargs))
            )

    def test_multiple_violations_all_reported(self):
        sampling = LlmSamplingParameters(temperature=3.0, top_p=0.0)
        with pytest.raises(ValueError) as excinfo:
            build_chat_request_body(_make_request(sampling=sampling))
        message = str(excinfo.value)
        assert "温度 3.0" in message
        assert "Top P 0.0" in message
