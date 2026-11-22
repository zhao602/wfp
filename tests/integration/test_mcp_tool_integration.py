# =============================================================================
# MCP 工具与主备模型集成测试（tests/integration/test_mcp_tool_integration.py）
#
# 覆盖验收：A8（MCP 工具调用成功与超时映射）
#             A9（主备模型切换）
# 接口对齐：LocalFunctionToolInvoker 单工具绑定模式 + async invoke
#           PrimaryFallbackLlmClient 接受 ILlmClient 对象 + async infer
# =============================================================================

import asyncio

import pytest

from orchestration_platform.integrations.llm.primary_fallback_llm_client import (
    PrimaryFallbackLlmClient,
)
from orchestration_platform.integrations.tools.local_function_tool_invoker import (
    LocalFunctionToolInvoker,
)
from orchestration_platform.integrations.abstractions.external_clients import (
    InferenceRequest,
    InferenceResult,
)


class TestMcpToolIntegration:
    """MCP 工具与主备模型集成测试。"""

    @pytest.mark.asyncio
    async def test_local_tool_invocation_success(self):
        """本地工具调用应成功并返回结果。"""
        def echo_tool(text: str) -> dict:
            return {"echoed": text}

        invoker = LocalFunctionToolInvoker("echo", echo_tool)
        result = await invoker.invoke({"text": "hello"}, timeout_seconds=10)
        assert result.success
        assert result.output == {"echoed": "hello"}

    @pytest.mark.asyncio
    async def test_tool_argument_mismatch(self):
        """入参不匹配应返回失败。"""
        def echo_tool(text: str) -> dict:
            return {"echoed": text}

        invoker = LocalFunctionToolInvoker("echo", echo_tool)
        result = await invoker.invoke({"nonexistent_param": "value"}, timeout_seconds=10)
        assert not result.success

    @pytest.mark.asyncio
    async def test_tool_execution_error(self):
        """工具执行异常应映射为失败结果。"""
        def error_tool() -> dict:
            raise RuntimeError("tool error")

        invoker = LocalFunctionToolInvoker("error", error_tool)
        result = await invoker.invoke({}, timeout_seconds=10)
        assert not result.success
        assert "tool error" in (result.error_message or "")


class MockLlmClient:
    """ILlmClient mock 实现，用于主备切换测试。"""

    def __init__(self, model: str, fail: bool = False):
        self._model = model
        self._fail = fail

    async def infer(self, request: InferenceRequest) -> InferenceResult:
        if self._fail:
            return InferenceResult(success=False, error_message=f"{self._model} unavailable")
        return InferenceResult(success=True, content=f"{self._model} ok")


class TestPrimaryFallbackLlmClient:
    """主备 LLM 客户端测试。"""

    @pytest.mark.asyncio
    async def test_primary_success_no_fallback(self):
        """主模型成功时不触发备用模型。"""
        primary = MockLlmClient("primary")
        fallback = MockLlmClient("fallback")
        client = PrimaryFallbackLlmClient(primary, fallback)
        request = InferenceRequest(model_identifier="test-model", user_message="test")
        result = await client.infer(request)
        assert result.success
        assert "primary" in result.content

    @pytest.mark.asyncio
    async def test_fallback_on_primary_failure(self):
        """主模型失败时切换到备用模型。"""
        primary = MockLlmClient("primary", fail=True)
        fallback = MockLlmClient("fallback")
        client = PrimaryFallbackLlmClient(primary, fallback)
        request = InferenceRequest(model_identifier="test-model", user_message="test")
        result = await client.infer(request)
        assert result.success
        assert "fallback" in result.content

    @pytest.mark.asyncio
    async def test_both_fail_returns_failure(self):
        """主备均失败时应返回失败。"""
        primary = MockLlmClient("primary", fail=True)
        fallback = MockLlmClient("fallback", fail=True)
        client = PrimaryFallbackLlmClient(primary, fallback)
        request = InferenceRequest(model_identifier="test-model", user_message="test")
        result = await client.infer(request)
        assert not result.success
