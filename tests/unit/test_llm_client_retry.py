# =============================================================================
# LLM 连接池单例与指数退避重试单元测试（tests/unit/test_llm_client_retry.py）
#
# arch_review_gap_b2_observability_perf B2-C4（T4.1/T4.2，DC-B2-04）：
#   R1 关闭态逐字节等价（默认不重试；超时错误文案与现状一致，GK-11）
#   R2 超时后重试成功（幂等失败才重试；退避序列 base*2^n+jitter）
#   R3 重试耗尽（默认上限 3 次；末次异常文案不变）
#   R4 ConnectError 可重试 / R5 ProxyError 不重试 / R6 非 200 响应不重试
#   R7 max_attempts=1 无重试语义 / R8 默认 jitter 界
#   P1 连接池单例复用（两次 infer 仅一次构造；timeout 构造口径原样）
#   P2 aclose 幂等与重建 / P3 构造不建连 + configure_retry 后调覆盖
#
# 既有单测（test_http_llm_client.py / test_build_llm_client.py）零修改通过
# 为本卡验收前提，本文件仅追加新用例。
# =============================================================================

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestration_platform.integrations.abstractions.external_clients import (
    InferenceRequest,
)
from orchestration_platform.integrations.llm.http_llm_client import HttpLlmClient


def _make_request() -> InferenceRequest:
    return InferenceRequest(
        model_identifier="",
        system_prompt="You are a helpful assistant.",
        user_message="Hello",
    )


def _ok_response():
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
    return response


class _SleepRecorder:
    """假 sleep：记录每次退避时长（V 类机械验证，可注入）。"""

    def __init__(self) -> None:
        self.delays: list = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def _patched_client(mock_post_client):
    """构造 patch 上下文：httpx.AsyncClient → 预置 mock 实例。"""
    return patch("httpx.AsyncClient", return_value=mock_post_client)


def _mock_http_client(post):
    """构造 mock AsyncClient 实例（post 可为 AsyncMock 或函数）。"""
    mock_client = AsyncMock()
    mock_client.post = post
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client


class TestRetryDisabledByteEquivalence:
    """R1：默认关闭态与现状逐字节等价（GK-11）。"""

    @pytest.mark.asyncio
    async def test_r1_disabled_timeout_single_attempt_same_message(self):
        import httpx

        mock_client = _mock_http_client(
            AsyncMock(side_effect=httpx.TimeoutException("timed out"))
        )

        with _patched_client(mock_client):
            client = HttpLlmClient(
                "http://localhost", "sk-key", "gpt-4", timeout_seconds=5.0
            )
            result = await client.infer(_make_request())

        assert result.success is False
        # 与改造前文案逐字节一致（关闭态等价断言）
        assert result.error_message == "LLM 请求超时（上限 5.0s）"
        assert mock_client.post.await_count == 1

    @pytest.mark.asyncio
    async def test_r1b_disabled_no_sleep_machinery(self):
        import httpx

        sleep = _SleepRecorder()
        mock_client = _mock_http_client(
            AsyncMock(side_effect=httpx.TimeoutException("timed out"))
        )

        with _patched_client(mock_client):
            client = HttpLlmClient("http://localhost", "sk-key", "gpt-4")
            # 显式注入 sleep 但开关关闭：不进入任何退避等待
            client.configure_retry(enabled=False, sleep=sleep)
            await client.infer(_make_request())

        assert sleep.delays == []
        assert mock_client.post.await_count == 1


class TestRetryEnabledBehaviors:
    """R2~R8：开启态重试语义（仅幂等失败、指数退避、上限可配）。"""

    @pytest.mark.asyncio
    async def test_r2_timeout_then_success_retries_with_backoff(self):
        import httpx

        sleep = _SleepRecorder()
        mock_client = _mock_http_client(
            AsyncMock(
                side_effect=[httpx.TimeoutException("t"), _ok_response()]
            )
        )

        with _patched_client(mock_client):
            client = HttpLlmClient("http://localhost", "sk-key", "gpt-4")
            client.configure_retry(
                enabled=True,
                max_attempts=3,
                backoff_base_seconds=0.5,
                sleep=sleep,
                jitter=lambda: 0.0,
            )
            result = await client.infer(_make_request())

        assert result.success is True
        assert result.content == "ok"
        assert mock_client.post.await_count == 2
        assert sleep.delays == [0.5]  # base * 2^0 + jitter(0)

    @pytest.mark.asyncio
    async def test_r3_retry_exhausted_keeps_last_error_message(self):
        import httpx

        sleep = _SleepRecorder()
        mock_client = _mock_http_client(
            AsyncMock(side_effect=httpx.TimeoutException("t"))
        )

        with _patched_client(mock_client):
            client = HttpLlmClient(
                "http://localhost", "sk-key", "gpt-4", timeout_seconds=5.0
            )
            client.configure_retry(
                enabled=True,
                max_attempts=3,
                backoff_base_seconds=0.5,
                sleep=sleep,
                jitter=lambda: 0.0,
            )
            result = await client.infer(_make_request())

        assert result.success is False
        assert result.error_message == "LLM 请求超时（上限 5.0s）"
        assert mock_client.post.await_count == 3  # 默认上限 3 次（含首次）
        assert sleep.delays == [0.5, 1.0]  # base * 2^0, base * 2^1

    @pytest.mark.asyncio
    async def test_r4_connect_error_retried(self):
        import httpx

        sleep = _SleepRecorder()
        mock_client = _mock_http_client(
            AsyncMock(side_effect=[httpx.ConnectError("c"), _ok_response()])
        )

        with _patched_client(mock_client):
            client = HttpLlmClient("http://localhost", "sk-key", "gpt-4")
            client.configure_retry(
                enabled=True,
                max_attempts=3,
                backoff_base_seconds=0.5,
                sleep=sleep,
                jitter=lambda: 0.0,
            )
            result = await client.infer(_make_request())

        assert result.success is True
        assert mock_client.post.await_count == 2
        assert sleep.delays == [0.5]

    @pytest.mark.asyncio
    async def test_r5_proxy_error_not_retried(self):
        import httpx

        sleep = _SleepRecorder()
        mock_client = _mock_http_client(
            AsyncMock(side_effect=httpx.ProxyError("502 Bad Gateway"))
        )

        with _patched_client(mock_client):
            client = HttpLlmClient("http://localhost", "sk-key", "gpt-4")
            client.configure_retry(
                enabled=True,
                max_attempts=3,
                backoff_base_seconds=0.5,
                sleep=sleep,
                jitter=lambda: 0.0,
            )
            result = await client.infer(_make_request())

        assert result.success is False
        assert "ProxyError" in (result.error_message or "")
        assert mock_client.post.await_count == 1
        assert sleep.delays == []

    @pytest.mark.asyncio
    async def test_r6_non_200_response_not_retried(self):
        response = MagicMock()
        response.status_code = 500
        response.json.return_value = {}
        sleep = _SleepRecorder()
        mock_client = _mock_http_client(AsyncMock(side_effect=[response]))

        with _patched_client(mock_client):
            client = HttpLlmClient("http://localhost", "sk-key", "gpt-4")
            client.configure_retry(
                enabled=True,
                max_attempts=3,
                backoff_base_seconds=0.5,
                sleep=sleep,
                jitter=lambda: 0.0,
            )
            result = await client.infer(_make_request())

        assert result.success is False
        assert result.error_status_code == 500
        assert mock_client.post.await_count == 1
        assert sleep.delays == []

    @pytest.mark.asyncio
    async def test_r7_max_attempts_one_no_retry(self):
        import httpx

        sleep = _SleepRecorder()
        mock_client = _mock_http_client(
            AsyncMock(side_effect=[httpx.TimeoutException("t"), _ok_response()])
        )

        with _patched_client(mock_client):
            client = HttpLlmClient("http://localhost", "sk-key", "gpt-4")
            client.configure_retry(
                enabled=True,
                max_attempts=1,
                backoff_base_seconds=0.5,
                sleep=sleep,
                jitter=lambda: 0.0,
            )
            result = await client.infer(_make_request())

        assert result.success is False
        assert mock_client.post.await_count == 1
        assert sleep.delays == []

    @pytest.mark.asyncio
    async def test_r8_default_jitter_bounded(self):
        import httpx

        sleep = _SleepRecorder()
        mock_client = _mock_http_client(
            AsyncMock(side_effect=[httpx.TimeoutException("t"), _ok_response()])
        )

        with _patched_client(mock_client):
            client = HttpLlmClient("http://localhost", "sk-key", "gpt-4")
            # jitter 不注入：默认 [0, 0.5) 抖动
            client.configure_retry(
                enabled=True,
                max_attempts=2,
                backoff_base_seconds=0.5,
                sleep=sleep,
            )
            result = await client.infer(_make_request())

        assert result.success is True
        assert len(sleep.delays) == 1
        # base*2^0 + U[0, 0.5) → [0.5, 1.0)
        assert 0.5 <= sleep.delays[0] < 1.0


class TestConnectionPoolSingleton:
    """P1~P3：连接池单例复用与生命周期（T4.1）。"""

    @pytest.mark.asyncio
    async def test_p1_two_infers_construct_client_once(self):
        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = _mock_http_client(AsyncMock(return_value=_ok_response()))
            mock_cls.return_value = mock_client

            client = HttpLlmClient("http://localhost", "sk-key", "gpt-4")
            first = await client.infer(_make_request())
            second = await client.infer(_make_request())

        assert first.success is True
        assert second.success is True
        # 连接池单例断言：两次 infer 仅构造一次 AsyncClient
        mock_cls.assert_called_once()
        assert mock_client.post.await_count == 2

    @pytest.mark.asyncio
    async def test_p1b_client_constructed_with_instance_timeout(self):
        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = _mock_http_client(AsyncMock(return_value=_ok_response()))
            mock_cls.return_value = mock_client

            client = HttpLlmClient(
                "http://localhost", "sk-key", "gpt-4", timeout_seconds=7.5
            )
            await client.infer(_make_request())

        # 构造口径沿用既有 infer 的 timeout 参数（DC-B2-04 ①）
        mock_cls.assert_called_once_with(timeout=7.5)

    @pytest.mark.asyncio
    async def test_p2_aclose_closes_pool_idempotent_and_rebuilds(self):
        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = _mock_http_client(AsyncMock(return_value=_ok_response()))
            mock_cls.return_value = mock_client

            client = HttpLlmClient("http://localhost", "sk-key", "gpt-4")
            await client.infer(_make_request())

            await client.aclose()
            assert mock_client.aclose.await_count == 1

            await client.aclose()  # 幂等：无池再关不抛、不重复关闭
            assert mock_client.aclose.await_count == 1

            await client.infer(_make_request())  # 关闭后重建新池
            assert mock_cls.call_count == 2

    @pytest.mark.asyncio
    async def test_p2b_aclose_without_infer_noop(self):
        client = HttpLlmClient("http://localhost", "sk-key", "gpt-4")
        await client.aclose()  # 从未建池：无操作不抛异常

    def test_p3_constructor_never_builds_client(self):
        with patch("httpx.AsyncClient") as mock_cls:
            HttpLlmClient("http://localhost", "sk-key", "gpt-4")
            mock_cls.assert_not_called()

    def test_p3b_configure_retry_last_call_wins(self):
        client = HttpLlmClient("http://localhost", "sk-key", "gpt-4")
        client.configure_retry(
            enabled=True, max_attempts=5, backoff_base_seconds=1.0
        )
        client.configure_retry(enabled=False)
        # 后调覆盖先调：回落关闭态（GK-11）
        assert client._retry_enabled is False
        assert client._retry_max_attempts == 3
        assert client._retry_backoff_base_seconds == 0.5
