# =============================================================================
# wfp-sdk 单元测试（tests/unit/test_wfp_sdk.py）
#
# 职责：验证 SDK 9 方法契约（docs/sdk-contract.md §1）与错误同构（§2）：
#       validate/submit 报告与 issues 同构、错误映射（422/409/404/401）、
#       未知码透传、SSE 帧解析与 STREAM_END 收尾、cancel/results 模型、
#       wait_for_completion 轮询与超时、网络异常包装。
# 设计意图：httpx.MockTransport 注入（客户端 transport 参数），零真实网络、
#       零平台运行时依赖；全同步用例（SDK v0.1 同步优先，用户裁决 Q1）。
# 执行命令：python -m pytest tests/unit/test_wfp_sdk.py -q
#           （主 pyproject pythonpath 含 sdk，全量套件内亦直接可收集）
# =============================================================================

import json

import httpx
import pytest
import wfp_sdk
from wfp_sdk import (
    CLIENT_TIMEOUT,
    INSTANCE_CANCEL_CONFLICT,
    INSTANCE_NOT_FOUND,
    TRANSPORT_ERROR,
    WF_VALIDATION_FAILED,
    WorkflowClient,
    WfpAuthError,
    WfpNotFoundError,
    WfpStateConflictError,
    WfpTimeoutError,
    WfpTransportError,
    WfpValidationError,
)

BASE_URL = "http://test-server"

# 最小合法报告（契约 §4：issues 携 node_id/location 定位）
_REPORT_OK = {
    "valid": True,
    "issues": [],
    "definition_id": "wf_sdk_test",
}

_REPORT_BAD = {
    "valid": False,
    "issues": [
        {
            "code": "WF_CYCLIC_GRAPH",
            "severity": "error",
            "message": "存在环",
            "node_id": "node_b",
            "location": {"line": 12, "column": 3},
        }
    ],
    "definition_id": None,
}


def _client(handler) -> WorkflowClient:
    """构造注入 MockTransport 的客户端（零真实网络）。"""
    return WorkflowClient(BASE_URL, transport=httpx.MockTransport(handler))


# =============================================================================
# A. 提交域（validate/submit 同构）
# =============================================================================


class TestSubmitDomain:
    def test_validate_definition_parses_report_with_issue_location(self):
        with _client(
            lambda request: httpx.Response(200, json=_REPORT_BAD)
        ) as client:
            report = client.validate_definition("yaml: ...")
        assert report.valid is False
        assert report.definition_id is None
        issue = report.issues[0]
        assert issue.code == "WF_CYCLIC_GRAPH"
        assert issue.severity == "error"
        assert issue.node_id == "node_b"
        assert issue.location.line == 12
        assert issue.location.column == 3

    def test_validate_definition_ok(self):
        with _client(lambda request: httpx.Response(200, json=_REPORT_OK)) as client:
            report = client.validate_definition("yaml: ...")
        assert report.valid is True
        assert report.issues == []

    def test_submit_definition_returns_receipt(self):
        with _client(
            lambda request: httpx.Response(
                200, json={"definition_id": "wf_sdk_test", "definition_version": 3}
            )
        ) as client:
            receipt = client.submit_definition("yaml: ...")
        assert receipt.definition_id == "wf_sdk_test"
        assert receipt.definition_version == 3

    def test_submit_definition_422_raises_with_isomorphic_issues(self):
        # W2 wire 形态：detail 内嵌 DomainError.to_dict()（含 details.issues）
        body = {
            "detail": {
                "code": WF_VALIDATION_FAILED,
                "message": "工作流定义校验未通过",
                "details": {"issues": _REPORT_BAD["issues"]},
            }
        }
        with _client(lambda request: httpx.Response(422, json=body)) as client:
            with pytest.raises(WfpValidationError) as excinfo:
                client.submit_definition("yaml: broken")
        exc = excinfo.value
        assert exc.code == WF_VALIDATION_FAILED
        assert exc.http_status == 422
        assert exc.details["issues"][0]["code"] == "WF_CYCLIC_GRAPH", "issues 同构"


# =============================================================================
# B. 错误映射（§2.2/§2.3：三 wire 形态 + 未知码透传）
# =============================================================================


class TestErrorMapping:
    def test_404_plain_detail_falls_back_to_instance_not_found(self):
        # W1 wire 形态：裸 detail 字符串 → 码缺失按 404 语义合成 INSTANCE_NOT_FOUND
        with _client(
            lambda request: httpx.Response(404, json={"detail": "实例 x 不存在"})
        ) as client:
            with pytest.raises(WfpNotFoundError) as excinfo:
                client.get_instance("inst-x")
        assert excinfo.value.code == INSTANCE_NOT_FOUND
        assert "实例 x 不存在" in excinfo.value.message

    def test_409_top_level_code_maps_to_state_conflict(self):
        # W3 wire 形态：顶层 {code, message, details}（全局异常处理器）
        body = {
            "code": INSTANCE_CANCEL_CONFLICT,
            "message": "实例 inst-y 已处于终态 succeeded，无法取消",
            "details": {},
        }
        with _client(lambda request: httpx.Response(409, json=body)) as client:
            with pytest.raises(WfpStateConflictError) as excinfo:
                client.cancel_instance("inst-y")
        assert excinfo.value.code == INSTANCE_CANCEL_CONFLICT

    def test_401_maps_to_auth_error(self):
        body = {"code": "AUTH_TOKEN_MISSING", "message": "未提供访问令牌", "details": {}}
        with _client(lambda request: httpx.Response(401, json=body)) as client:
            with pytest.raises(WfpAuthError):
                client.get_instance("inst-z")

    def test_unknown_code_passthrough(self):
        # 未知平台码原样透传（isomorphic passthrough），异常类走 422 语义分支
        body = {"code": "FUTURE_CODE_X", "message": "平台未来新增", "details": {}}
        with _client(lambda request: httpx.Response(422, json=body)) as client:
            with pytest.raises(WfpValidationError) as excinfo:
                client.submit_definition("yaml: ...")
        assert excinfo.value.code == "FUTURE_CODE_X"


# =============================================================================
# C. 实例域（start/cancel/results 模型）
# =============================================================================


class TestInstanceDomain:
    def test_start_instance_sends_body_and_parses_response(self):
        requests: list = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                json={"instance_id": "inst-1", "status": "running", "started_at": "t0"},
            )

        with _client(handler) as client:
            info = client.start_instance(
                "wf_sdk_test", "yaml: ...", inputs={"q": 1}, business_category="api"
            )
        assert info.instance_id == "inst-1"
        assert info.status == "running"
        sent = json.loads(requests[0].content.decode("utf-8"))
        assert sent["definition_id"] == "wf_sdk_test"
        assert sent["inputs"] == {"q": 1}
        assert sent["trigger_type"] == "api", "business_category 经 trigger_type 通道（契约 §1）"

    def test_cancel_instance_parses_snapshot(self):
        body = {
            "instance_id": "inst-2",
            "definition_id": "wf_sdk_test",
            "status": "running",
            "started_at": "t0",
        }
        with _client(lambda request: httpx.Response(200, json=body)) as client:
            info = client.cancel_instance("inst-2")
        assert info.instance_id == "inst-2"
        assert info.definition_id == "wf_sdk_test"

    def test_get_results_running_null_and_terminal_outputs(self):
        running = {"instance_id": "inst-3", "status": "running", "outputs": None}
        terminal = {
            "instance_id": "inst-3",
            "status": "succeeded",
            "outputs": {"node_a": {"answer": 42}},
            "finished_at": "t1",
        }
        responses = [httpx.Response(200, json=running), httpx.Response(200, json=terminal)]
        with _client(lambda request: responses.pop(0)) as client:
            first = client.get_results("inst-3")
            second = client.get_results("inst-3")
        assert first.outputs is None and first.status == "running"
        assert second.outputs == {"node_a": {"answer": 42}}
        assert second.finished_at == "t1"

    def test_poll_events_parses_list(self):
        events = [
            {
                "event_id": "e1",
                "instance_id": "inst-4",
                "event_type": "INSTANCE_STARTED",
                "node_id": None,
                "timestamp": "t0",
                "payload": {},
            }
        ]
        with _client(lambda request: httpx.Response(200, json=events)) as client:
            polled = client.poll_events("inst-4")
        assert len(polled) == 1
        assert polled[0].event_type == "INSTANCE_STARTED"


# =============================================================================
# D. SSE 订阅（帧解析 + STREAM_END 收尾）
# =============================================================================


class TestStreamEvents:
    def test_stream_events_yields_until_stream_end(self):
        sse_text = (
            'data: {"event_id": "e1", "instance_id": "inst-5", "event_type": "INSTANCE_STARTED", "node_id": null, "timestamp": "t0", "payload": {}}\n\n'
            ': keepalive 注释行应被跳过\n\n'
            'data: {"event_id": "e2", "instance_id": "inst-5", "event_type": "NODE_STARTED", "node_id": "node_a", "timestamp": "t1", "payload": {}}\n\n'
            'data: {"event_type": "STREAM_END", "instance_id": "inst-5"}\n\n'
            'data: {"event_id": "e3", "instance_id": "inst-5", "event_type": "NEVER", "payload": {}}\n\n'
        )
        with _client(
            lambda request: httpx.Response(
                200, text=sse_text, headers={"content-type": "text/event-stream"}
            )
        ) as client:
            received = [event for event in client.stream_events("inst-5")]
        assert [e.event_id for e in received] == ["e1", "e2"], "STREAM_END 后帧不产出"
        assert received[1].node_id == "node_a"

    def test_stream_events_404_raises_not_found(self):
        with _client(
            lambda request: httpx.Response(404, json={"detail": "实例不存在"})
        ) as client:
            with pytest.raises(WfpNotFoundError):
                list(client.stream_events("inst-missing"))


# =============================================================================
# E. 等待与传输
# =============================================================================


class TestWaitAndTransport:
    def test_wait_for_completion_polls_until_terminal(self):
        requests: list = []
        states = ["running", "running", "succeeded"]
        responses = [
            httpx.Response(
                200,
                json={
                    "instance_id": "inst-6",
                    "definition_id": "wf",
                    "status": status,
                    "started_at": "t0",
                },
            )
            for status in states
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return responses.pop(0)

        with _client(handler) as client:
            info = client.wait_for_completion("inst-6", timeout=5.0, poll_interval=0.0)
        assert info.status == "succeeded"
        assert len(requests) == 3, "轮询三次后终态"

    def test_wait_for_completion_timeout_raises_client_timeout(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "instance_id": "inst-7",
                    "definition_id": "wf",
                    "status": "running",
                    "started_at": "t0",
                },
            )

        with _client(handler) as client:
            with pytest.raises(WfpTimeoutError) as excinfo:
                client.wait_for_completion("inst-7", timeout=0.05, poll_interval=0.01)
        assert excinfo.value.code == CLIENT_TIMEOUT
        assert "inst-7" in excinfo.value.message

    def test_network_failure_wraps_transport_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        with _client(handler) as client:
            with pytest.raises(WfpTransportError) as excinfo:
                client.get_instance("inst-8")
        assert excinfo.value.code == TRANSPORT_ERROR


# =============================================================================
# F. 隔离性护栏：SDK 顶层导入面零平台依赖（roadmap B2「不泄漏内部模块」）
# =============================================================================


class TestIsolation:
    def test_sdk_imports_standalone_without_platform(self):
        """干净子进程导入 wfp_sdk：PYTHONPATH 仅含 sdk/、无 src/。

        roadmap B2 验收「不泄漏内部模块」的护栏——全量套件中其他用例
        早已污染本进程 sys.modules，进程内判定不可信，故以独立子进程
        为唯一判定现场（SDK 若 import 平台模块会因 src 不在路径而失败）。
        """
        import os
        import subprocess
        import sys
        from pathlib import Path

        sdk_root = Path(wfp_sdk.__file__).resolve().parent.parent
        env = os.environ.copy()
        env["PYTHONPATH"] = str(sdk_root)
        code = (
            "import sys, wfp_sdk; "
            "leaked = [m for m in sys.modules if m.startswith('orchestration_platform')]; "
            "assert not leaked, leaked; "
            "print('ISOLATED_OK', wfp_sdk.__version__)"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, f"SDK 独立导入失败: {result.stderr}"
        assert "ISOLATED_OK" in result.stdout
