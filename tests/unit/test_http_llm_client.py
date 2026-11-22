# =============================================================================
# HttpLlmClient 单元测试（tests/unit/test_http_llm_client.py）
#
# 覆盖 fix_agent_executor_llm_client TC-1~TC-11：
#   TC-1 推理成功 / TC-2 httpx导入失败 / TC-3 网络错误 / TC-4 超时
#   TC-5 非200 / TC-6 JSON解析失败 / TC-7 结构缺失
#   TC-8 api_key不入日志 / TC-9 构造不建连
#   TC-10 model_identifier优先 / TC-11 model回退
#
# llm_and_code_nodes（任务 4.1 / 4.3 / 4.4 / 4.5，CHG-11 / CHG-12 / CHG-13）：
#   - InferenceRequest/InferenceResult 契约（字段集/默认值/infer 签名不变）；
#   - infer 请求体经 build_chat_request_body 组装（AC-16 集成 + AC-14 透传）；
#   - 非 200 保留 error_status_code 与脱敏摘要 error_detail（DC-10 / INV-09 /
#     AC-20：不静默丢弃参数后重试，状态码/摘要供上游判定）；
#   - PrimaryFallbackLlmClient 切换标记 model_switched（DC-08 / AC-22）与
#     聚合失败（保留更可诊断一方的状态码/摘要）。
#
# 沙箱限制：pytest 无法安装，本文件作为交付物编写，待 CI 执行。
# =============================================================================

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestration_platform.domain.models.node_configurations import (
    LlmSamplingParameters,
)
from orchestration_platform.integrations.abstractions.external_clients import (
    ILlmClient,
    InferenceRequest,
    InferenceResult,
)
from orchestration_platform.integrations.llm.http_llm_client import HttpLlmClient
from orchestration_platform.integrations.llm.primary_fallback_llm_client import (
    PrimaryFallbackLlmClient,
)


def _make_request(model_identifier: str = "", sampling=None) -> InferenceRequest:
    return InferenceRequest(
        model_identifier=model_identifier,
        system_prompt="You are a helpful assistant.",
        user_message="Hello",
        sampling=sampling,
    )


class TestInferSuccess:
    """TC-1：推理成功"""

    @pytest.mark.asyncio
    async def test_t01_infer_success(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "hello world"}}]
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            client = HttpLlmClient(
                base_url="http://localhost:8080",
                api_key="sk-secret-key",
                model="gpt-4",
            )
            result = await client.infer(_make_request())

        assert result.success is True
        assert result.content == "hello world"


class TestInferImportError:
    """TC-2：httpx 导入失败"""

    @pytest.mark.asyncio
    async def test_t02_httpx_import_error(self):
        client = HttpLlmClient("http://localhost", "sk-key", "gpt-4")
        with patch.dict("sys.modules", {"httpx": None}):
            result = await client.infer(_make_request())
        assert result.success is False
        assert "不可用" in (result.error_message or "")


class TestInferNetworkError:
    """TC-3：网络错误"""

    @pytest.mark.asyncio
    async def test_t03_network_error(self):
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=Exception("connect failed"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            with patch("httpx.ConnectError", Exception, create=True):
                with patch("httpx.NetworkError", Exception, create=True):
                    with patch("httpx.TimeoutException", Exception, create=True):
                        client = HttpLlmClient("http://localhost", "sk-key", "gpt-4")
                        result = await client.infer(_make_request())
        assert result.success is False


class TestInferTimeout:
    """TC-4：超时"""

    @pytest.mark.asyncio
    async def test_t04_timeout(self):
        import httpx

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            client = HttpLlmClient("http://localhost", "sk-key", "gpt-4", timeout_seconds=5.0)
            result = await client.infer(_make_request())
        assert result.success is False
        assert "超时" in (result.error_message or "")


class TestInferTransportError:
    """传输层异常（ProxyError / ProtocolError 等）回落失败结果，不得抛出。

    回归背景：此前只捕获 TimeoutException / ConnectError / NetworkError，
    ProxyError 会以异常逃出 infer 的返回契约，调用方会把网络故障当成崩溃。
    """

    @pytest.mark.asyncio
    async def test_proxy_error_returns_failure_result(self):
        import httpx

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.ProxyError("502 Bad Gateway"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            client = HttpLlmClient("http://localhost", "sk-key", "gpt-4")
            result = await client.infer(_make_request())

        assert result.success is False
        assert "ProxyError" in (result.error_message or "")


class TestInferNon200:
    """TC-5：非 200 状态码"""

    @pytest.mark.asyncio
    async def test_t05_non_200(self):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.json.return_value = {}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            client = HttpLlmClient("http://localhost", "sk-key", "gpt-4")
            result = await client.infer(_make_request())
        assert result.success is False
        assert "500" in (result.error_message or "")


class TestInferJsonParseError:
    """TC-6：JSON 解析失败"""

    @pytest.mark.asyncio
    async def test_t06_json_parse_error(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.side_effect = ValueError("invalid json")

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            client = HttpLlmClient("http://localhost", "sk-key", "gpt-4")
            result = await client.infer(_make_request())
        assert result.success is False
        assert "解析失败" in (result.error_message or "")


class TestInferEmptyChoices:
    """TC-7：choices 为空"""

    @pytest.mark.asyncio
    async def test_t07_empty_choices(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"choices": []}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            client = HttpLlmClient("http://localhost", "sk-key", "gpt-4")
            result = await client.infer(_make_request())
        assert result.success is False


class TestCredentialProtection:
    """TC-8：api_key 不入日志"""

    @pytest.mark.asyncio
    async def test_t08_api_key_not_in_logs(self, caplog):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.json.return_value = {}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        secret = "sk-super-secret-key-12345"
        with patch("httpx.AsyncClient", return_value=mock_client):
            with caplog.at_level("WARNING"):
                client = HttpLlmClient("http://localhost", secret, "gpt-4")
                result = await client.infer(_make_request())

        for record in caplog.records:
            assert secret not in record.getMessage()
        assert secret not in (result.error_message or "")


class TestConstructorNoConnection:
    """TC-9：构造不建连"""

    @pytest.mark.asyncio
    async def test_t09_constructor_no_connection(self):
        with patch("httpx.AsyncClient") as mock_async_client:
            HttpLlmClient("http://localhost", "sk-key", "gpt-4")
            mock_async_client.assert_not_called()


class TestModelIdentifierPriority:
    """TC-10/TC-11：model_identifier 优先 / model 回退"""

    @pytest.mark.asyncio
    async def test_t10_model_identifier_priority(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "ok"}}]
        }

        captured_body = {}

        async def capture_post(url, json=None, headers=None):
            captured_body.update(json or {})
            return mock_response

        mock_client = AsyncMock()
        mock_client.post = capture_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            client = HttpLlmClient("http://localhost", "sk-key", "gpt-4")
            await client.infer(_make_request(model_identifier="gpt-3.5-turbo"))

        assert captured_body["model"] == "gpt-3.5-turbo"

    @pytest.mark.asyncio
    async def test_t11_model_fallback(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "ok"}}]
        }

        captured_body = {}

        async def capture_post(url, json=None, headers=None):
            captured_body.update(json or {})
            return mock_response

        mock_client = AsyncMock()
        mock_client.post = capture_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            client = HttpLlmClient("http://localhost", "sk-key", "gpt-4")
            await client.infer(_make_request(model_identifier=""))

        assert captured_body["model"] == "gpt-4"


# =============================================================================
# llm_and_code_nodes 任务 4 扩充（P3 参数通路）
# =============================================================================


class _ScriptedLlmClient:
    """ILlmClient 测试桩：按脚本依次返回结果，并记录收到的请求对象。"""

    def __init__(self, results) -> None:
        self._results = list(results)
        self.requests = []

    async def infer(self, request: InferenceRequest) -> InferenceResult:
        self.requests.append(request)
        return self._results.pop(0)


class TestInferenceDataContract:
    """任务 4.1：InferenceRequest/InferenceResult 契约（CHG-11，既有字段零变更）。"""

    def test_inference_request_field_set(self):
        assert set(InferenceRequest.model_fields.keys()) == {
            "model_identifier",
            "system_prompt",
            "user_message",
            "available_tools",
            "output_schema",
            "sampling",
        }

    def test_inference_request_defaults(self):
        request = InferenceRequest(model_identifier="m")
        assert request.sampling is None
        assert request.system_prompt == ""
        assert request.user_message == ""
        assert request.available_tools == []
        assert request.output_schema is None

    def test_inference_result_new_field_defaults(self):
        result = InferenceResult(success=True)
        assert result.model_switched is False
        assert result.error_status_code is None
        assert result.error_detail is None

    def test_inference_result_field_set(self):
        # 2026-11-22 B7 W1b：新增 usage（provider 用量透传，CHG-11 追加先例，
        # 既有字段零变更）——字段集锚点同步更新（出口记录已登记）。
        assert set(InferenceResult.model_fields.keys()) == {
            "success",
            "content",
            "tool_call_intent",
            "structured_output",
            "error_message",
            "model_switched",
            "error_status_code",
            "error_detail",
            "usage",
        }

    def test_ilmm_client_infer_signature_unchanged(self):
        import inspect

        signature = inspect.signature(ILlmClient.infer)
        assert list(signature.parameters) == ["self", "request"]
        assert signature.parameters["request"].annotation is InferenceRequest
        assert signature.return_annotation is InferenceResult


class _CaptureTransport:
    """mock 传输层：捕获请求体并返回预设响应。"""

    def __init__(self, response) -> None:
        self._response = response
        self.captured_body = {}
        self.post_called = False

    def make_mock_client(self):
        transport = self

        async def capture_post(url, json=None, headers=None):
            transport.post_called = True
            transport.captured_body.update(json or {})
            return transport._response

        mock_client = AsyncMock()
        mock_client.post = capture_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        return mock_client


def _success_response():
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
    return response


class TestInferBodyIntegration:
    """任务 4.2 集成：infer 请求体经 build_chat_request_body 组装（AC-16/AC-14）。"""

    @pytest.mark.asyncio
    async def test_infer_unconfigured_sampling_body_exact(self):
        transport = _CaptureTransport(_success_response())
        with patch("httpx.AsyncClient", return_value=transport.make_mock_client()):
            client = HttpLlmClient("http://localhost", "sk-key", "gpt-4")
            result = await client.infer(_make_request())

        assert result.success is True
        assert set(transport.captured_body.keys()) == {
            "model",
            "messages",
            "temperature",
            "max_tokens",
        }
        assert transport.captured_body["temperature"] == 0.7
        assert transport.captured_body["max_tokens"] == 4096

    @pytest.mark.asyncio
    async def test_infer_sampling_forwarded_to_body(self):
        transport = _CaptureTransport(_success_response())
        sampling = LlmSamplingParameters(
            temperature=0.2,
            top_p=0.95,
            top_k=64,
            max_tokens=2048,
            stop=["END"],
            seed=7,
        )
        with patch("httpx.AsyncClient", return_value=transport.make_mock_client()):
            client = HttpLlmClient("http://localhost", "sk-key", "gpt-4")
            result = await client.infer(_make_request(sampling=sampling))

        assert result.success is True
        assert set(transport.captured_body.keys()) == {
            "model",
            "messages",
            "temperature",
            "top_p",
            "top_k",
            "max_tokens",
            "stop",
            "seed",
        }
        assert transport.captured_body["stop"] == ["END"]
        assert transport.captured_body["seed"] == 7


class TestInferInvalidSampling:
    """越界 sampling 经 infer → success=False（不静默截断），不发起网络请求。"""

    @pytest.mark.asyncio
    async def test_invalid_sampling_returns_failure_without_call(self):
        transport = _CaptureTransport(_success_response())
        with patch("httpx.AsyncClient", return_value=transport.make_mock_client()):
            client = HttpLlmClient("http://localhost", "sk-key", "gpt-4")
            result = await client.infer(
                _make_request(sampling=LlmSamplingParameters(temperature=2.5))
            )

        assert result.success is False
        assert "越界" in (result.error_message or "")
        assert "温度 2.5" in (result.error_message or "")
        assert transport.post_called is False


class TestNon200ErrorDetail:
    """任务 4.3：非 200 保留状态码与脱敏摘要（DC-10 / INV-09 / AC-20）。"""

    @staticmethod
    def _response(status_code, text=""):
        response = MagicMock()
        response.status_code = status_code
        response.text = text
        response.json.return_value = {}
        return response

    @pytest.mark.asyncio
    async def test_non_200_status_and_detail(self):
        response = self._response(400, "Unsupported parameter: 'top_k'")
        transport = _CaptureTransport(response)
        with patch("httpx.AsyncClient", return_value=transport.make_mock_client()):
            client = HttpLlmClient("http://localhost", "sk-key", "gpt-4")
            result = await client.infer(_make_request())

        assert result.success is False
        assert result.error_status_code == 400
        assert result.error_detail == "Unsupported parameter: 'top_k'"
        assert "400" in (result.error_message or "")

    @pytest.mark.asyncio
    async def test_error_detail_truncated_and_flattened(self):
        long_text = ("line-1\n" * 200) + "x" * 2000
        response = self._response(502, long_text)
        transport = _CaptureTransport(response)
        with patch("httpx.AsyncClient", return_value=transport.make_mock_client()):
            client = HttpLlmClient("http://localhost", "sk-key", "gpt-4")
            result = await client.infer(_make_request())

        assert result.success is False
        assert result.error_status_code == 502
        assert result.error_detail is not None
        assert len(result.error_detail) <= 512
        assert "\n" not in result.error_detail
        assert "\r" not in result.error_detail

    @pytest.mark.asyncio
    async def test_error_detail_scrubs_api_key_and_base_url(self):
        secret = "sk-super-secret-key-12345"
        base_url = "http://llm-gateway"
        body_text = f'{{"error": "auth failed for {secret} at {base_url}/v1/chat"}}'
        response = self._response(401, body_text)
        transport = _CaptureTransport(response)
        with patch("httpx.AsyncClient", return_value=transport.make_mock_client()):
            client = HttpLlmClient(base_url, secret, "gpt-4")
            result = await client.infer(_make_request())

        assert result.success is False
        assert secret not in (result.error_message or "")
        assert secret not in (result.error_detail or "")
        assert base_url not in (result.error_detail or "")

    @pytest.mark.asyncio
    async def test_transport_failure_status_none(self):
        import httpx

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            client = HttpLlmClient("http://localhost", "sk-key", "gpt-4")
            result = await client.infer(_make_request())

        assert result.success is False
        assert result.error_status_code is None
        assert result.error_detail is None


class TestPrimaryFallbackModelSwitched:
    """任务 4.4：主备切换标记与聚合失败（CHG-13 / DC-08 / AC-22 / D-06）。"""

    @pytest.mark.asyncio
    async def test_primary_success_no_switch(self):
        primary = _ScriptedLlmClient([InferenceResult(success=True, content="primary-ok")])
        fallback = _ScriptedLlmClient([InferenceResult(success=True, content="fallback-ok")])
        client = PrimaryFallbackLlmClient(primary, fallback)

        result = await client.infer(InferenceRequest(model_identifier="m"))

        assert result.success is True
        assert result.model_switched is False
        assert result.content == "primary-ok"
        assert len(fallback.requests) == 0

    @pytest.mark.asyncio
    async def test_fallback_success_marks_switched(self):
        primary = _ScriptedLlmClient([InferenceResult(success=False, error_message="down")])
        fallback = _ScriptedLlmClient([InferenceResult(success=True, content="fallback-ok")])
        client = PrimaryFallbackLlmClient(primary, fallback)

        result = await client.infer(InferenceRequest(model_identifier="m"))

        assert result.success is True
        assert result.model_switched is True
        assert result.content == "fallback-ok"

    @pytest.mark.asyncio
    async def test_no_fallback_returns_primary_failure(self):
        primary = _ScriptedLlmClient([InferenceResult(success=False, error_message="down")])
        client = PrimaryFallbackLlmClient(primary)

        result = await client.infer(InferenceRequest(model_identifier="m"))

        assert result.success is False
        assert result.model_switched is False

    @pytest.mark.asyncio
    async def test_both_fail_preserves_diagnosable_status(self):
        primary = _ScriptedLlmClient([
            InferenceResult(
                success=False,
                error_message="bad-param",
                error_status_code=400,
                error_detail="Unsupported parameter: 'top_k'",
            )
        ])
        fallback = _ScriptedLlmClient([InferenceResult(success=False, error_message="network")])
        client = PrimaryFallbackLlmClient(primary, fallback)

        result = await client.infer(InferenceRequest(model_identifier="m"))

        assert result.success is False
        assert result.error_status_code == 400
        assert result.error_detail == "Unsupported parameter: 'top_k'"
        assert "主=" in (result.error_message or "")
        assert "备=" in (result.error_message or "")

    @pytest.mark.asyncio
    async def test_both_fail_prefers_fallback_status_when_primary_none(self):
        primary = _ScriptedLlmClient([InferenceResult(success=False, error_message="network")])
        fallback = _ScriptedLlmClient([
            InferenceResult(
                success=False,
                error_message="server",
                error_status_code=503,
                error_detail="overloaded",
            )
        ])
        client = PrimaryFallbackLlmClient(primary, fallback)

        result = await client.infer(InferenceRequest(model_identifier="m"))

        assert result.success is False
        assert result.error_status_code == 503
        assert result.error_detail == "overloaded"

    @pytest.mark.asyncio
    async def test_request_object_reused_no_extra_roundtrips(self):
        primary = _ScriptedLlmClient([InferenceResult(success=False, error_message="down")])
        fallback = _ScriptedLlmClient([InferenceResult(success=True, content="ok")])
        client = PrimaryFallbackLlmClient(primary, fallback)
        request = InferenceRequest(model_identifier="m")

        result = await client.infer(request)

        assert result.success is True
        assert len(primary.requests) == 1
        assert len(fallback.requests) == 1
        assert primary.requests[0] is request
        assert fallback.requests[0] is request