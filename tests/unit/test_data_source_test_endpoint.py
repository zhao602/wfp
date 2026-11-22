# =============================================================================
# 本地数据源连接测试端点测试（tests/unit/test_data_source_test_endpoint.py）
#
# 职责：验证 POST /api/v1/data-source/test 的路由分流与可达性语义（AC-3~AC-6）。
# 执行命令：pytest tests/unit/test_data_source_test_endpoint.py -v
# =============================================================================

import tempfile
import os
import unittest

from fastapi.testclient import TestClient

from orchestration_platform.api.application_factory import create_app


class TestDataSourceTestEndpoint(unittest.TestCase):
    """本地数据源连接测试端点（AC-3 ~ AC-6）。"""

    def setUp(self):
        self.client = TestClient(create_app())

    def test_json_file_reachable(self):
        """AC-5：文件存在 → reachable=true，不依赖企业连接器注册表（AC-3/INV-1）。"""
        with tempfile.NamedTemporaryFile(
            suffix=".json", mode="w", delete=False, encoding="utf-8"
        ) as f:
            f.write('[{"a": 1}]')
            path = f.name
        try:
            res = self.client.post(
                "/api/v1/data-source/test",
                json={
                    "source_type": "json",
                    "connection_reference": "local-json-1",
                    "file_path": path,
                },
            )
            self.assertEqual(res.status_code, 200)
            body = res.json()
            self.assertTrue(body["reachable"])
            self.assertGreaterEqual(body["response_latency_ms"], 0)
        finally:
            os.unlink(path)

    def test_json_file_unreachable(self):
        """AC-6：文件不存在 → reachable=false，diagnosis 含原因摘要（HTTP 200）。"""
        res = self.client.post(
            "/api/v1/data-source/test",
            json={
                "source_type": "json",
                "connection_reference": "local-json-1",
                "file_path": "Z:/no/such/file.json",
            },
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertFalse(body["reachable"])
        self.assertIn("不存在", body["diagnosis"])

    def test_database_unreachable_returns_false_with_diagnosis(self):
        """AC-8/AC-10：不可达数据库 → reachable=false + 分类诊断前缀。"""
        res = self.client.post(
            "/api/v1/data-source/test",
            json={
                "source_type": "database",
                "connection_reference": "db-1",
                "connection_string": "abc",
            },
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertFalse(body["reachable"])
        self.assertTrue(
            body["diagnosis"].startswith("参数错误：")
            or body["diagnosis"].startswith("连接失败：")
            or body["diagnosis"].startswith("无法探测："),
            f"诊断未含分类前缀: {body['diagnosis']}",
        )

    def test_rest_api_unreachable_returns_false_with_diagnosis(self):
        """AC-8/AC-11：不可达 REST API → reachable=false + 分类诊断前缀。"""
        res = self.client.post(
            "/api/v1/data-source/test",
            json={
                "source_type": "rest_api",
                "connection_reference": "api-1",
                "url": "http://127.0.0.1:1/no-such-endpoint",
            },
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertFalse(body["reachable"])
        self.assertTrue(
            body["diagnosis"].startswith("参数错误：")
            or body["diagnosis"].startswith("连接失败：")
            or body["diagnosis"].startswith("无法探测："),
            f"诊断未含分类前缀: {body['diagnosis']}",
        )

    def test_enterprise_type_rejected(self):
        """INV-2：企业连接器类型不被本地端点接受 → 422。"""
        res = self.client.post(
            "/api/v1/data-source/test",
            json={"source_type": "crm", "connection_reference": "crm-1"},
        )
        self.assertEqual(res.status_code, 422)

    def test_unknown_source_type(self):
        """请求体校验失败 → 422（source_type 不在枚举内）。"""
        res = self.client.post(
            "/api/v1/data-source/test",
            json={"source_type": "no_such_type", "connection_reference": "x"},
        )
        self.assertEqual(res.status_code, 422)


if __name__ == "__main__":
    unittest.main()
