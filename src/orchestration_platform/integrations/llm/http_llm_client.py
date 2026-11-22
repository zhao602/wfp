# =============================================================================
# HTTP LLM 客户端（integrations/llm/http_llm_client.py）
#
# 职责：通过 HTTP 协议调用 OpenAI 兼容 LLM 服务，实现 ILlmClient Protocol。
# 设计意图（对应 fix_agent_executor_llm_client spec.md / design.md）：
#   1. httpx 延迟导入（infer 方法内部），沙箱无 httpx 时降级返回失败结果；
#   2. 构造函数仅保存参数，不建立网络连接；
#   3. 凭据（api_key）仅用于请求头，不出现在日志或 error_message 中；
#   4. 所有异常封装为 InferenceResult(success=False)，不向调用方抛出未封装异常。
#
# llm_and_code_nodes（CHG-12 / DC-05 / DC-10 / DC-12 / AC-16 / AC-20）：
#   5. build_chat_request_body 是请求体组装的**唯一权威**（模块级纯函数），
#      infer 内不得有任何第二处就地拼 body（G-06 唯一性 grep）；
#   6. 非 200 响应保留 error_status_code 与脱敏摘要 error_detail
#      （≤512 字符、去换行、去除凭据/base_url，DC-10/INV-09），
#      不得为摘要打印完整响应体到日志。
#
# arch_review_gap_b2_observability_perf B2-C4（DC-B2-04）：
#   7. 实例级惰性连接池单例 _get_async_client()（T4.1）：构造口径沿用
#      既有 infer 的 timeout 参数；三处生产调用点（执行器注册表/NL 服务/
#      DI 容器）均为应用级长生命周期实例，实例级单例与模块级单例的
#      连接池复用等效（出口记录登记）；aclose() 关闭幂等；
#   8. 指数退避重试 _post_with_retry()（T4.2）：仅重试幂等失败
#      （httpx.TimeoutException / httpx.ConnectError），重试耗尽重抛末次
#      异常；退避 base*2^n + jitter（sleep/jitter 可注入）；开关经
#      configure_retry 配置、默认关闭（GK-11），关闭态与现状逐字节等价；
#   9. 流式 infer_stream() + parse_sse_chunk() 纯函数（T4.3）：产出
#      InferenceStreamChunk；流式不重试（已产出分片不可回滚）；错误帧过
#      _scrub_credentials 纪律（INV-09 延伸）；InferenceResult 契约与
#      infer() 签名零改动（GK-14）。
# =============================================================================

import json
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional

from orchestration_platform.domain.values.llm_sampling_spec import to_request_payload
from orchestration_platform.integrations.abstractions.external_clients import (
    InferenceRequest,
    InferenceResult,
    InferenceStreamChunk,
)

# 上游响应体摘要硬上界（DC-10：≤512 字符）
ERROR_DETAIL_MAX_CHARS = 512


def _sanitize_error_detail(text: str) -> str:
    """响应体摘要规范化：去换行 + 截断至 512 字符（纯函数）。"""
    flattened = text.replace("\r", " ").replace("\n", " ")
    if len(flattened) > ERROR_DETAIL_MAX_CHARS:
        return flattened[:ERROR_DETAIL_MAX_CHARS]
    return flattened


def build_chat_request_body(
    request: InferenceRequest, fallback_model: str = ""
) -> Dict[str, Any]:
    """组装 OpenAI 兼容聊天请求体（模块级纯函数 / DC-12 唯一权威）。

    - model：取 request.model_identifier，为空时回退 fallback_model
      （既有取值逻辑，AC-16 逐字节等价）；
    - messages：组装不变（system + user 两条）；
    - sampling is None：走既有两键分支（temperature=0.7 / max_tokens=4096，
      无任何额外参数键，AC-16 与既有单轮路径逐字节等价）；
    - sampling 已配置：经 to_request_payload 按 DC-05 最小键集发送
      （temperature/max_tokens 恒发；top_p/top_k/stop/seed 仅显式设置时发；
      top_k=0 不发；stop 去重保序后非空才发）；
    - 越界 sampling 抛 ValueError（DC-04 不静默截断），由 infer 捕获为失败结果。
    """
    model = request.model_identifier if request.model_identifier else fallback_model

    body: Dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": request.system_prompt},
            {"role": "user", "content": request.user_message},
        ],
    }

    if request.sampling is None:
        # 既有两键分支（AC-16：未配置采样时与既有实现逐字节等价）
        body["temperature"] = 0.7
        body["max_tokens"] = 4096
    else:
        # 唯一采样键集权威（INV-12 / DC-05）；越界时抛 ValueError
        body.update(to_request_payload(request.sampling))

    return body


def parse_sse_chunk(lines: List[Any]) -> List[str]:
    """解析 SSE 行序列提取 data: 载荷（B2-C4 T4.3 模块级纯函数，独立可测）。

    - 仅提取 data: 前缀行（容忍 data: 与载荷间任意空白）；
    - 注释行（: 开头）/ event: 行 / 空载荷行忽略；
    - "[DONE]" 哨兵原样返回，语义由调用方决定。
    """
    payloads: List[str] = []
    for line in lines:
        if not isinstance(line, str) or not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if payload:
            payloads.append(payload)
    return payloads


async def _default_retry_sleep(delay_seconds: float) -> None:
    """默认重试退避等待（asyncio 延迟导入，与 httpx 同纪律）。"""
    import asyncio

    await asyncio.sleep(delay_seconds)


def _default_retry_jitter() -> float:
    """默认重试抖动：[0, 0.5) 区间（防同步重试风暴）。"""
    import random

    return random.uniform(0.0, 0.5)


class HttpLlmClient:
    """HTTP LLM 客户端：通过 OpenAI 兼容 API 调用 LLM 服务。

    实现 ILlmClient Protocol 的 infer 方法。httpx 在 infer 内部延迟导入，
    沙箱无 httpx 时返回 InferenceResult(success=False)。
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 60.0,
    ) -> None:
        """构造 HTTP LLM 客户端（不建立网络连接）。"""
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds
        # B2-C4 T4.1：实例级惰性连接池（首次 infer 时构造，aclose 后可重建）
        self._async_client: Optional[Any] = None
        # B2-C4 T4.2：重试配置（默认关闭，GK-11；经 configure_retry 配置）
        self._retry_enabled: bool = False
        self._retry_max_attempts: int = 3
        self._retry_backoff_base_seconds: float = 0.5
        self._retry_sleep: Optional[Callable[[float], Any]] = None
        self._retry_jitter: Optional[Callable[[], float]] = None

    def _scrub_credentials(self, text: str) -> str:
        """凭据脱敏：api_key 与 base_url 不得出现在任何返回消息中（INV-09）。"""
        scrubbed = text
        if self._api_key:
            scrubbed = scrubbed.replace(self._api_key, "***")
        if self._base_url:
            scrubbed = scrubbed.replace(self._base_url, "***")
        return scrubbed

    def _summarize_response_body(self, response: Any) -> Optional[str]:
        """提取响应体脱敏摘要（≤512 字符、去换行）；不可得时返回 None。"""
        try:
            raw = response.text
        except Exception:
            return None
        if not isinstance(raw, str):
            return None
        return _sanitize_error_detail(self._scrub_credentials(raw))

    def configure_retry(
        self,
        *,
        enabled: bool,
        max_attempts: int = 3,
        backoff_base_seconds: float = 0.5,
        sleep: Optional[Callable[[float], Any]] = None,
        jitter: Optional[Callable[[], float]] = None,
    ) -> None:
        """配置指数退避重试（B2-C4 T4.2；幂等，后调覆盖先调）。

        enabled=False（缺省态）时 infer 与现状逐字节等价（GK-11）；
        sleep/jitter 可注入（假时钟单测，V 类机械验证）。
        经构造后配置而非构造参数传递：构造签名零改动（既有单测
        faulty_init 桩兼容，tests/unit/test_build_llm_client.py TC-18）。
        """
        self._retry_enabled = bool(enabled)
        self._retry_max_attempts = max(1, int(max_attempts))
        self._retry_backoff_base_seconds = float(backoff_base_seconds)
        self._retry_sleep = sleep
        self._retry_jitter = jitter

    def _get_async_client(self) -> Any:
        """惰性构造实例级 AsyncClient（B2-C4 T4.1 连接池单例）。

        首次调用构造 httpx.AsyncClient 并记忆化复用（连接池跨 infer
        调用共享）；构造参数沿用既有 infer 的 timeout 口径；aclose()
        关闭后置空可重建。三处生产调用点（执行器注册表/NL 服务/DI
        容器）均为应用级长生命周期实例，实例级单例与模块级单例的
        连接池复用等效（出口记录登记）。
        """
        import httpx

        if self._async_client is None:
            self._async_client = httpx.AsyncClient(timeout=self._timeout_seconds)
        return self._async_client

    async def aclose(self) -> None:
        """关闭底层连接池（幂等；生命周期挂钩过渡登记见批次出口记录）。"""
        client = self._async_client
        self._async_client = None
        if client is not None:
            await client.aclose()

    async def _retry_wait(self, attempt_index: int) -> None:
        """第 attempt_index 次失败后的退避等待：base * 2^n + jitter（可注入）。"""
        delay = self._retry_backoff_base_seconds * (2 ** attempt_index)
        jitter_fn = self._retry_jitter or _default_retry_jitter
        sleep_fn = self._retry_sleep or _default_retry_sleep
        await sleep_fn(delay + jitter_fn())

    async def _post_with_retry(
        self,
        client: Any,
        url: str,
        request_body: Dict[str, Any],
        headers: Dict[str, str],
    ) -> Any:
        """带指数退避重试的 POST（B2-C4 T4.2 内包装）。

        仅重试幂等失败（httpx.TimeoutException / httpx.ConnectError）；
        重试耗尽重抛末次异常（保既有错误文案，由 infer 既有异常链封装）；
        其余异常（响应已抵达或不可重试传输错误）原样上抛不重试。
        关闭态直发单次 POST——与现状逐字节等价（GK-11）。
        """
        import httpx

        if not self._retry_enabled:
            return await client.post(url, json=request_body, headers=headers)

        attempts = self._retry_max_attempts
        for attempt in range(attempts):
            try:
                return await client.post(url, json=request_body, headers=headers)
            except (httpx.TimeoutException, httpx.ConnectError):
                if attempt + 1 >= attempts:
                    raise
                await self._retry_wait(attempt)
        raise AssertionError("重试循环不应到达此处")

    async def infer(self, request: InferenceRequest) -> InferenceResult:
        """执行 LLM 推理：POST {base_url}/v1/chat/completions。

        httpx 延迟导入；所有异常封装为 InferenceResult(success=False)。
        请求体组装唯一入口为 build_chat_request_body（不得就地拼 body）。
        """
        try:
            import httpx
        except ImportError:
            return InferenceResult(
                success=False,
                error_message="HTTP 客户端库不可用: httpx 导入失败",
            )

        try:
            request_body: Dict[str, Any] = build_chat_request_body(
                request, self._model
            )
        except ValueError as exc:
            # 越界采样参数（调用方绕过加载期校验）：不静默截断（DC-04）
            return InferenceResult(
                success=False,
                error_message=f"LLM 采样参数越界: {exc}",
            )

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        url = f"{self._base_url}/v1/chat/completions"

        try:
            # B2-C4：连接池单例（T4.1，构造口径原样）+ 重试内包装（T4.2）
            client = self._get_async_client()
            response = await self._post_with_retry(
                client, url, request_body, headers
            )
        except httpx.TimeoutException:
            return InferenceResult(
                success=False,
                error_message=f"LLM 请求超时（上限 {self._timeout_seconds}s）",
            )
        except httpx.ConnectError:
            return InferenceResult(
                success=False,
                error_message="LLM 请求网络错误: ConnectError",
            )
        except httpx.NetworkError:
            return InferenceResult(
                success=False,
                error_message="LLM 请求网络错误: NetworkError",
            )
        except httpx.TransportError as exc:
            # TransportError 覆盖 ProxyError / ProtocolError / ReadError 等
            # 前两分支未命中的传输层异常：必须回落失败结果，不得让异常逃出
            # infer 的返回契约（否则调用方会把网络故障当成未处理崩溃）
            return InferenceResult(
                success=False,
                error_message=f"LLM 请求网络错误: {type(exc).__name__}",
            )

        if response.status_code != 200:
            return InferenceResult(
                success=False,
                error_message=f"LLM 服务返回状态码 {response.status_code}",
                error_status_code=response.status_code,
                error_detail=self._summarize_response_body(response),
            )

        try:
            resp_json = response.json()
            choices = resp_json["choices"]
            if not choices:
                return InferenceResult(
                    success=False,
                    error_message="LLM 响应解析失败: choices 为空",
                    error_status_code=response.status_code,
                    error_detail=self._summarize_response_body(response),
                )
            content = choices[0]["message"]["content"]
        except Exception as exc:
            return InferenceResult(
                success=False,
                error_message=f"LLM 响应解析失败: {type(exc).__name__}",
                error_status_code=response.status_code,
                error_detail=self._summarize_response_body(response),
            )

        tool_call_intent: Optional[Dict[str, Any]] = None
        try:
            tool_calls = choices[0]["message"].get("tool_calls")
            if tool_calls and isinstance(tool_calls, list):
                tool_call_intent = {"tool_calls": tool_calls}
        except Exception:
            pass

        structured_output: Optional[Dict[str, Any]] = None
        if request.output_schema is not None:
            try:
                import json
                parsed = json.loads(content)
                if isinstance(parsed, dict):
                    structured_output = parsed
            except Exception:
                pass

        # roadmap B7 W1b：OpenAI 兼容响应体顶层 usage 透传
        # （{"prompt_tokens","completion_tokens","total_tokens"}；缺省 None）
        raw_usage = resp_json.get("usage")
        usage: Optional[Dict[str, Any]] = (
            raw_usage if isinstance(raw_usage, dict) else None
        )

        return InferenceResult(
            success=True,
            content=content,
            tool_call_intent=tool_call_intent,
            structured_output=structured_output,
            usage=usage,
        )

    def _parse_stream_frame(self, payload: str) -> Optional[InferenceStreamChunk]:
        """解析单条 SSE data 载荷为增量块（B2-C4 T4.3）。

        不可解析（非 JSON）或无信息帧返回 None（容错跳过）；
        错误帧（{"error": …}）返回结束错误块——载荷经脱敏纪律
        （_scrub_credentials + ≤512 字符去换行，INV-09 延伸）。
        """
        try:
            frame = json.loads(payload)
        except ValueError:
            return None
        if not isinstance(frame, dict):
            return None

        if "error" in frame:
            detail = _sanitize_error_detail(
                self._scrub_credentials(str(frame.get("error")))
            )
            return InferenceStreamChunk(
                is_final=True,
                error_message="LLM 流式响应错误帧",
                error_detail=detail,
            )

        choices = frame.get("choices")
        delta_obj: Any = None
        finish_reason: Optional[str] = None
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            delta_obj = choices[0].get("delta")
            raw_finish = choices[0].get("finish_reason")
            if isinstance(raw_finish, str):
                finish_reason = raw_finish

        delta_text = ""
        if isinstance(delta_obj, dict):
            raw_delta = delta_obj.get("content")
            if isinstance(raw_delta, str):
                delta_text = raw_delta

        raw_usage = frame.get("usage")
        usage = raw_usage if isinstance(raw_usage, dict) else None

        if not delta_text and finish_reason is None and usage is None:
            return None
        return InferenceStreamChunk(
            delta=delta_text,
            finish_reason=finish_reason,
            usage=usage,
        )

    async def infer_stream(
        self, request: InferenceRequest
    ) -> AsyncGenerator[InferenceStreamChunk, None]:
        """流式推理（B2-C4 T4.3 可选扩展；不加入 ILlmClient 协议）。

        参数沿用 infer；请求体追加 stream=True（组装仍经
        build_chat_request_body 唯一权威）。产出 InferenceStreamChunk：
        增量文本帧 + 结束帧（is_final）；错误路径以单结束帧收口，不向
        调用方抛出未封装异常（与 infer 返回契约同纪律）。流式不重试
        （已产出分片不可回滚，DC-B2-04 ②）。
        """
        try:
            import httpx
        except ImportError:
            yield InferenceStreamChunk(
                is_final=True,
                error_message="HTTP 客户端库不可用: httpx 导入失败",
            )
            return

        try:
            request_body: Dict[str, Any] = build_chat_request_body(
                request, self._model
            )
        except ValueError as exc:
            yield InferenceStreamChunk(
                is_final=True,
                error_message=f"LLM 采样参数越界: {exc}",
            )
            return
        request_body["stream"] = True

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        url = f"{self._base_url}/v1/chat/completions"

        try:
            client = self._get_async_client()
            async with client.stream(
                "POST", url, json=request_body, headers=headers
            ) as response:
                if response.status_code != 200:
                    yield InferenceStreamChunk(
                        is_final=True,
                        error_message=f"LLM 服务返回状态码 {response.status_code}",
                        error_status_code=response.status_code,
                        error_detail=self._summarize_response_body(response),
                    )
                    return

                async for line in response.aiter_lines():
                    for payload in parse_sse_chunk([line]):
                        if payload == "[DONE]":
                            yield InferenceStreamChunk(is_final=True)
                            return
                        chunk = self._parse_stream_frame(payload)
                        if chunk is None:
                            continue
                        if chunk.error_message is not None:
                            yield chunk
                            return
                        yield chunk

                # 流自然结束（上游未发 [DONE] 帧）：补发结束帧收口
                yield InferenceStreamChunk(is_final=True)
        except httpx.TimeoutException:
            yield InferenceStreamChunk(
                is_final=True,
                error_message=f"LLM 请求超时（上限 {self._timeout_seconds}s）",
            )
        except httpx.ConnectError:
            yield InferenceStreamChunk(
                is_final=True,
                error_message="LLM 请求网络错误: ConnectError",
            )
        except httpx.NetworkError:
            yield InferenceStreamChunk(
                is_final=True,
                error_message="LLM 请求网络错误: NetworkError",
            )
        except httpx.TransportError as exc:
            yield InferenceStreamChunk(
                is_final=True,
                error_message=f"LLM 请求网络错误: {type(exc).__name__}",
            )
