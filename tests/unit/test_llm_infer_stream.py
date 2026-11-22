# =============================================================================
# LLM 流式推理与 SSE 解析单元测试（tests/unit/test_llm_infer_stream.py）
#
# arch_review_gap_b2_observability_perf B2-C4（T4.3，DC-B2-04）：
#   S1 parse_sse_chunk 纯函数（分片提取/注释与 event 行忽略/空载荷/
#      [DONE] 哨兵原样/非字符串行容错）
#   S2 infer_stream 增量帧 + 结束帧（delta 拼接/finish_reason/usage/
#      非 JSON 帧容错/仅 usage 帧）
#   S3 非 200 → 单结束错误帧（状态码 + 脱敏摘要）
#   S4 错误帧脱敏（api_key/base_url 不泄露，INV-09 延伸）
#   S5 httpx 导入失败降级 / S6 超时收口 / S7 采样越界不发请求
#   S8 无 [DONE] 自然收口 / S9 请求体 stream=True（唯一权威 + 开关位）
#
# 流式不重试（已产出分片不可回滚）；InferenceResult 契约零改动（GK-14）。
# =============================================================================

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestration_platform.domain.models.node_configurations import (
    LlmSamplingParameters,
)
from orchestration_platform.integrations.abstractions.external_clients import (
    InferenceRequest,
)
from orchestration_platform.integrations.llm.http_llm_client import (
    HttpLlmClient,
    parse_sse_chunk,
)


def _make_request() -> InferenceRequest:
    return InferenceRequest(
        model_identifier="",
        system_prompt="You are a helpful assistant.",
        user_message="Hello",
    )


def _make_stream_mock(lines, status_code=200, response_text=""):
    """构造支持 client.stream(...) as response 的 mock 客户端。"""
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.text = response_text

    async def _aiter_lines():
        for line in lines:
            yield line

    mock_response.aiter_lines = _aiter_lines

    stream_cm = AsyncMock()
    stream_cm.__aenter__.return_value = mock_response
    stream_cm.__aexit__.return_value = None

    mock_client = AsyncMock()
    mock_client.stream = MagicMock(return_value=stream_cm)
    return mock_client


class TestParseSseChunk:
    """S1：parse_sse_chunk 纯函数（独立可测）。"""

    def test_s1a_extracts_data_payloads_only(self):
        lines = [
            ": keepalive",
            "event: message",
            'data: {"a":1}',
            "",
            "data:[DONE]",
        ]
        assert parse_sse_chunk(lines) == ['{"a":1}', "[DONE]"]

    def test_s1b_tolerates_spacing_and_strips(self):
        assert parse_sse_chunk(['data:   {"x": 2}  ']) == ['{"x": 2}']

    def test_s1c_empty_payload_skipped(self):
        assert parse_sse_chunk(["data:", "data:   "]) == []

    def test_s1d_non_string_lines_skipped(self):
        assert parse_sse_chunk([None, 123, "data: [DONE]"]) == ["[DONE]"]


class TestInferStreamHappyPath:
    """S2/S9：增量帧与请求体组装。"""

    @pytest.mark.asyncio
    async def test_s2_delta_frames_and_final(self):
        lines = [
            'data: {"choices":[{"delta":{"content":"Hel"},"finish_reason":null}]}',
            'data: {"choices":[{"delta":{"content":"lo"},"finish_reason":null}]}',
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}],'
            '"usage":{"total_tokens":12}}',
            "data: [DONE]",
        ]
        mock_client = _make_stream_mock(lines)

        with patch("httpx.AsyncClient", return_value=mock_client):
            client = HttpLlmClient("http://localhost", "sk-key", "gpt-4")
            chunks = [c async for c in client.infer_stream(_make_request())]

        assert "".join(c.delta for c in chunks) == "Hello"
        assert chunks[0].delta == "Hel"
        assert chunks[1].delta == "lo"
        assert chunks[2].finish_reason == "stop"
        assert chunks[2].usage == {"total_tokens": 12}
        assert chunks[-1].is_final is True
        assert chunks[-1].error_message is None
        assert len([c for c in chunks if c.is_final]) == 1

    @pytest.mark.asyncio
    async def test_s2b_non_json_frame_tolerated(self):
        mock_client = _make_stream_mock(["data: not-json", "data: [DONE]"])

        with patch("httpx.AsyncClient", return_value=mock_client):
            client = HttpLlmClient("http://localhost", "sk-key", "gpt-4")
            chunks = [c async for c in client.infer_stream(_make_request())]

        assert len(chunks) == 1
        assert chunks[0].is_final is True

    @pytest.mark.asyncio
    async def test_s2c_usage_only_frame_yielded(self):
        lines = [
            'data: {"choices":[],"usage":{"total_tokens":7}}',
            "data: [DONE]",
        ]
        mock_client = _make_stream_mock(lines)

        with patch("httpx.AsyncClient", return_value=mock_client):
            client = HttpLlmClient("http://localhost", "sk-key", "gpt-4")
            chunks = [c async for c in client.infer_stream(_make_request())]

        assert chunks[0].usage == {"total_tokens": 7}
        assert chunks[0].delta == ""
        assert chunks[-1].is_final is True

    @pytest.mark.asyncio
    async def test_s9_request_body_stream_flag(self):
        mock_client = _make_stream_mock(["data: [DONE]"])

        with patch("httpx.AsyncClient", return_value=mock_client):
            client = HttpLlmClient("http://localhost", "sk-key", "gpt-4")
            chunks = [c async for c in client.infer_stream(_make_request())]

        assert chunks[-1].is_final is True
        call = mock_client.stream.call_args
        # 组装仍经 build_chat_request_body 唯一权威 + stream 开关位
        assert call.args[0] == "POST"
        body = call.kwargs["json"]
        assert body["stream"] is True
        assert body["model"] == "gpt-4"
        assert set(body.keys()) == {
            "model",
            "messages",
            "temperature",
            "max_tokens",
            "stream",
        }
        assert call.kwargs["headers"]["Content-Type"] == "application/json"


class TestInferStreamErrorPaths:
    """S3~S8：错误路径收口（单结束帧，不抛未封装异常）。"""

    @pytest.mark.asyncio
    async def test_s3_non_200_single_error_chunk(self):
        mock_client = _make_stream_mock(
            [], status_code=429, response_text="rate limited"
        )

        with patch("httpx.AsyncClient", return_value=mock_client):
            client = HttpLlmClient("http://localhost", "sk-key", "gpt-4")
            chunks = [c async for c in client.infer_stream(_make_request())]

        assert len(chunks) == 1
        chunk = chunks[0]
        assert chunk.is_final is True
        assert chunk.error_status_code == 429
        assert "429" in (chunk.error_message or "")
        assert chunk.error_detail == "rate limited"

    @pytest.mark.asyncio
    async def test_s4_error_frame_scrubbed(self):
        secret = "sk-super-secret-key-12345"
        base_url = "http://llm-gateway"
        error_payload = json.dumps(
            {"error": {"message": f"auth failed for {secret} at {base_url}"}}
        )
        mock_client = _make_stream_mock([f"data: {error_payload}"])

        with patch("httpx.AsyncClient", return_value=mock_client):
            client = HttpLlmClient(base_url, secret, "gpt-4")
            chunks = [c async for c in client.infer_stream(_make_request())]

        assert len(chunks) == 1
        chunk = chunks[0]
        assert chunk.is_final is True
        assert chunk.error_message == "LLM 流式响应错误帧"
        # INV-09 延伸：凭据与 base_url 不泄露
        assert secret not in (chunk.error_detail or "")
        assert base_url not in (chunk.error_detail or "")

    @pytest.mark.asyncio
    async def test_s5_httpx_import_error_degrades(self):
        client = HttpLlmClient("http://localhost", "sk-key", "gpt-4")
        with patch.dict("sys.modules", {"httpx": None}):
            chunks = [c async for c in client.infer_stream(_make_request())]

        assert len(chunks) == 1
        assert chunks[0].is_final is True
        assert "不可用" in (chunks[0].error_message or "")

    @pytest.mark.asyncio
    async def test_s6_timeout_yields_error_chunk(self):
        import httpx

        mock_client = AsyncMock()
        mock_client.stream = MagicMock(side_effect=httpx.TimeoutException("t"))

        with patch("httpx.AsyncClient", return_value=mock_client):
            client = HttpLlmClient(
                "http://localhost", "sk-key", "gpt-4", timeout_seconds=5.0
            )
            chunks = [c async for c in client.infer_stream(_make_request())]

        assert len(chunks) == 1
        assert chunks[0].is_final is True
        assert "超时" in (chunks[0].error_message or "")

    @pytest.mark.asyncio
    async def test_s7_invalid_sampling_no_request(self):
        mock_client = _make_stream_mock(["data: [DONE]"])

        with patch("httpx.AsyncClient", return_value=mock_client):
            client = HttpLlmClient("http://localhost", "sk-key", "gpt-4")
            request = InferenceRequest(
                model_identifier="",
                user_message="hi",
                sampling=LlmSamplingParameters(temperature=2.5),
            )
            chunks = [c async for c in client.infer_stream(request)]

        assert len(chunks) == 1
        assert chunks[0].is_final is True
        assert "越界" in (chunks[0].error_message or "")
        mock_client.stream.assert_not_called()

    @pytest.mark.asyncio
    async def test_s8_stream_end_without_done_final_chunk(self):
        lines = ['data: {"choices":[{"delta":{"content":"hi"},"finish_reason":null}]}']
        mock_client = _make_stream_mock(lines)

        with patch("httpx.AsyncClient", return_value=mock_client):
            client = HttpLlmClient("http://localhost", "sk-key", "gpt-4")
            chunks = [c async for c in client.infer_stream(_make_request())]

        assert chunks[0].delta == "hi"
        assert len(chunks) == 2
        assert chunks[-1].is_final is True
