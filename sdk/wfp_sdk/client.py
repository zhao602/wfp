# =============================================================================
# wfp-sdk 同步客户端（wfp_sdk/client.py）
#
# 职责：WorkflowClient 最小面 9 方法（docs/sdk-contract.md §1；roadmap B2
#       四能力：提交/触发/订阅/取消 + 取结果与等待）。同步优先 v0.1
#       （用户裁决 Q1），仅依赖 httpx，零 import orchestration_platform。
# 纪律：
#   1. 终态集合与后端 _TERMINAL_STATUSES 逐字一致（client 侧常量，防泄漏）；
#   2. SSE 流式请求 read 超时置空（事件稀疏，默认 30s 读超时会截断长流）；
#   3. 网络异常统一包装 WfpTransportError（合成码 TRANSPORT_ERROR）。
# =============================================================================

import time
from typing import Any, Dict, Iterator, List, Optional

import httpx

from wfp_sdk._sse import STREAM_END, parse_sse_frame
from wfp_sdk.errors import (
    CLIENT_TIMEOUT,
    TRANSPORT_ERROR,
    WfpTimeoutError,
    WfpTransportError,
    error_from_response,
)
from wfp_sdk.models import (
    DefinitionSubmission,
    ExecutionEvent,
    InstanceInfo,
    InstanceResults,
    ValidationReport,
)

_API_PREFIX = "/api/v1"


class WorkflowClient:
    """工作流编排平台同步客户端。

    用法::

        with WorkflowClient("http://127.0.0.1:8310") as client:
            report = client.validate_definition(yaml_text)
            info = client.start_instance("wf_demo", yaml_text, inputs={"q": 1})
            for event in client.stream_events(info.instance_id):
                print(event.event_type)
            client.wait_for_completion(info.instance_id)
            results = client.get_results(info.instance_id)
    """

    # 终态集合（与后端 execution_instance_router._TERMINAL_STATUSES 一致）
    TERMINAL_STATUSES = ("succeeded", "failed", "rolled_back", "cancelled")

    def __init__(
        self,
        base_url: str,
        token: Optional[str] = None,
        timeout: float = 30.0,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        """初始化客户端。

        Args:
            base_url: 平台地址（如 http://127.0.0.1:8310）。
            token: 预签发访问令牌（非 None 时发送 Authorization: Bearer 头；
                租户身份随令牌解析，SDK 不设租户参数——契约 §3）。
            timeout: 普通请求超时秒数（SSE 流式请求读超时不受限）。
            transport: httpx 传输注入点（测试用 MockTransport；None 走默认）。
        """
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers=headers,
            transport=transport,
        )

    # ------------------------------------------------------------------
    # 上下文管理
    # ------------------------------------------------------------------

    def close(self) -> None:
        """关闭底层 HTTP 连接池。"""
        self._client.close()

    def __enter__(self) -> "WorkflowClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _request_json(
        self,
        method: str,
        path: str,
        json_body: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """发起请求并返回 JSON；非 2xx 统一抛同构异常（契约 §2）。"""
        try:
            response = self._client.request(method, path, json=json_body, params=params)
        except httpx.HTTPError as exc:
            raise WfpTransportError(
                TRANSPORT_ERROR, f"网络请求失败: {exc}"
            ) from exc
        if response.status_code >= 400:
            try:
                body: Any = response.json()
            except ValueError:
                body = None
            raise error_from_response(response.status_code, body)
        return response.json()

    # ------------------------------------------------------------------
    # 1/2. 提交域
    # ------------------------------------------------------------------

    def validate_definition(self, yaml_text: str) -> ValidationReport:
        """校验工作流定义（恒返报告，非法定义不抛错——看 report.valid）。"""
        data = self._request_json(
            "POST", f"{_API_PREFIX}/workflows/validate", {"yaml_text": yaml_text}
        )
        return ValidationReport.from_dict(data)

    def submit_definition(self, yaml_text: str) -> DefinitionSubmission:
        """提交工作流定义；校验未通过抛 WfpValidationError（issues 同构）。"""
        data = self._request_json(
            "POST", f"{_API_PREFIX}/workflows", {"yaml_text": yaml_text}
        )
        return DefinitionSubmission.from_dict(data)

    # ------------------------------------------------------------------
    # 3/4. 实例域
    # ------------------------------------------------------------------

    def start_instance(
        self,
        definition_id: str,
        yaml_text: str,
        inputs: Optional[Dict[str, Any]] = None,
        business_category: Optional[str] = None,
    ) -> InstanceInfo:
        """触发工作流执行（后台异步运行，返回 instance_id 即受理成功）。

        Args:
            definition_id: 工作流定义标识。
            yaml_text: 工作流 YAML 文本（后端按此解析编译）。
            inputs: 初始共享状态 node_outputs（可选）。
            business_category: 业务类别（经后端 trigger_type 通道传递，
                契约 §1 映射约定）。
        """
        body: Dict[str, Any] = {
            "definition_id": definition_id,
            "yaml_text": yaml_text,
        }
        if inputs:
            body["inputs"] = inputs
        if business_category:
            body["trigger_type"] = business_category
        data = self._request_json("POST", f"{_API_PREFIX}/instances", body)
        return InstanceInfo.from_dict(data)

    def get_instance(self, instance_id: str) -> InstanceInfo:
        """查询实例状态快照。"""
        data = self._request_json("GET", f"{_API_PREFIX}/instances/{instance_id}")
        return InstanceInfo.from_dict(data)

    # ------------------------------------------------------------------
    # 5/6. 订阅域
    # ------------------------------------------------------------------

    def stream_events(self, instance_id: str) -> Iterator[ExecutionEvent]:
        """SSE 订阅实例事件（生成器；终态收尾帧后迭代自然结束）。

        注意：SSE 流式请求读超时不设限（事件可能稀疏）；消费方中途
        break/关闭生成器即断流（with 块随生成器退出收尾）。
        """
        try:
            with self._client.stream(
                "GET",
                f"{_API_PREFIX}/instances/{instance_id}/events/stream",
                timeout=httpx.Timeout(self._client.timeout.connect, read=None),
            ) as response:
                if response.status_code >= 400:
                    try:
                        body: Any = response.json()
                    except ValueError:
                        body = None
                    raise error_from_response(response.status_code, body)
                for line in response.iter_lines():
                    parsed = parse_sse_frame(line)
                    if parsed is STREAM_END:
                        return
                    if parsed is None:
                        continue
                    yield ExecutionEvent.from_dict(parsed)
        except httpx.HTTPError as exc:
            raise WfpTransportError(TRANSPORT_ERROR, f"SSE 流式请求失败: {exc}") from exc

    def poll_events(
        self, instance_id: str, include_voided: bool = False
    ) -> List[ExecutionEvent]:
        """轮询实例事件全量（SSE 不可用环境的降级通道）。"""
        data = self._request_json(
            "GET",
            f"{_API_PREFIX}/instances/{instance_id}/events",
            params={"include_voided": include_voided},
        )
        if isinstance(data, list):
            return [ExecutionEvent.from_dict(e) for e in data if isinstance(e, dict)]
        return []

    # ------------------------------------------------------------------
    # 7/8/9. 取消与结果域
    # ------------------------------------------------------------------

    def cancel_instance(self, instance_id: str) -> InstanceInfo:
        """请求取消执行中的实例（契约 §1-7：终态/无任务 → WfpStateConflictError；
        取消已投递返回请求时刻快照，终态以 get_instance 轮询为准）。"""
        data = self._request_json(
            "POST", f"{_API_PREFIX}/instances/{instance_id}/cancel"
        )
        return InstanceInfo.from_dict(data)

    def get_results(self, instance_id: str) -> InstanceResults:
        """查询实例执行结果（非终态 outputs=None 轮询友好，契约 §1-8）。"""
        data = self._request_json(
            "GET", f"{_API_PREFIX}/instances/{instance_id}/results"
        )
        return InstanceResults.from_dict(data)

    def wait_for_completion(
        self,
        instance_id: str,
        timeout: float = 120.0,
        poll_interval: float = 1.0,
    ) -> InstanceInfo:
        """轮询直至实例终态（契约 §1-9）。

        Args:
            instance_id: 实例标识。
            timeout: 最长等待秒数（超时抛 WfpTimeoutError，合成码 CLIENT_TIMEOUT）。
            poll_interval: 轮询间隔秒数。

        Returns:
            终态实例快照（status ∈ TERMINAL_STATUSES）。
        """
        deadline = time.monotonic() + timeout
        while True:
            info = self.get_instance(instance_id)
            if info.status in self.TERMINAL_STATUSES:
                return info
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise WfpTimeoutError(
                    CLIENT_TIMEOUT,
                    f"实例 {instance_id} 在 {timeout}s 内未达终态"
                    f"（当前状态 {info.status}）",
                )
            time.sleep(min(poll_interval, remaining))
