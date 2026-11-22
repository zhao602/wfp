# =============================================================================
# 模型候选端点测试（tests/unit/test_model_catalog_endpoint.py）
#
# 职责：验证 GET /api/v1/models/available 的候选派生与只读语义
# （agent_model_selector TC-B1~TC-B9；AC-01/AC-03/AC-04、INV-D08/INV-D12）。
# 执行命令：PYTHONPATH=src python -m pytest tests/unit/test_model_catalog_endpoint.py -q
# =============================================================================

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from orchestration_platform.api.application_factory import create_app
from orchestration_platform.api.routers import model_catalog_router
from orchestration_platform.configuration.settings import Settings


def _fake_settings(primary: str, fallback: str) -> Settings:
    """构造仅指定主/备模型标识的测试配置（其余字段走默认值/环境）。"""
    return Settings(llm_primary_model=primary, llm_fallback_model=fallback)


class TestModelCatalogEndpoint(unittest.TestCase):
    """模型候选端点（GET /api/v1/models/available）。"""

    def setUp(self):
        self.client = TestClient(create_app())

    def _get(self, primary: str, fallback: str):
        """以 mock 配置请求候选端点（请求期即时派生，AC-04）。"""
        settings = _fake_settings(primary, fallback)
        with patch.object(model_catalog_router, "get_settings", return_value=settings):
            return self.client.get("/api/v1/models/available")

    def test_tc_b1_both_configured_two_items(self):
        """TC-B1/AC-01：主备均配置 → 200，2 项，primary 在前。"""
        res = self._get("m-primary", "m-fallback")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["total"], 2)
        self.assertEqual(
            [m["model_identifier"] for m in body["models"]],
            ["m-primary", "m-fallback"],
        )
        self.assertEqual([m["source"] for m in body["models"]], ["primary", "fallback"])

    def test_tc_b2_primary_only_single_item(self):
        """TC-B2/AC-01：仅主配置 → 1 项，source=primary。"""
        res = self._get("m-primary", "")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["models"][0]["model_identifier"], "m-primary")
        self.assertEqual(body["models"][0]["source"], "primary")

    def test_tc_b3_duplicate_identifier_dedup(self):
        """TC-B3/AC-01：主备同标识 → 去重 1 项，primary 优先。"""
        res = self._get("same-model", "same-model")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["models"][0]["model_identifier"], "same-model")
        self.assertEqual(body["models"][0]["source"], "primary")

    def test_tc_b4_both_empty_returns_empty_list(self):
        """TC-B4/INV-D12：主备皆空 → 200，空列表（降级契约）。"""
        res = self._get("", "")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["models"], [])
        self.assertEqual(body["total"], 0)

    def test_tc_b5_no_credential_fields_in_response(self):
        """TC-B5/INV-D08：响应字段白名单，不含 api_key/base_url/timeout*（凭据零泄露）。"""
        res = self._get("m-primary", "m-fallback")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        for item in body["models"]:
            self.assertEqual(set(item.keys()), {"model_identifier", "source"})
        raw = res.text
        for forbidden in ("api_key", "base_url", "timeout_seconds"):
            self.assertNotIn(forbidden, raw)

    def test_tc_b6_write_methods_not_allowed(self):
        """TC-B6/AC-03：仅注册 GET → POST/PUT/DELETE 返回 405。"""
        for method in ("post", "put", "delete"):
            res = getattr(self.client, method)("/api/v1/models/available")
            self.assertEqual(res.status_code, 405, method)

    def test_tc_b7_response_shape(self):
        """TC-B7/AC-01：响应结构 {models, total}，models 为列表。"""
        res = self._get("m-primary", "m-fallback")
        self.assertTrue(200 <= res.status_code < 300)
        body = res.json()
        self.assertIn("models", body)
        self.assertIn("total", body)
        self.assertIsInstance(body["models"], list)
        self.assertEqual(body["total"], len(body["models"]))

    def test_tc_b8_source_values_limited(self):
        """TC-B8：source 取值限定 primary/fallback。"""
        res = self._get("m-primary", "m-fallback")
        body = res.json()
        for item in body["models"]:
            self.assertIn(item["source"], {"primary", "fallback"})

    def test_tc_b9_settings_change_reflected_on_next_request(self):
        """TC-B9/AC-04：请求期即时派生——mock 配置变更后再次请求，候选随之更新。"""
        res1 = self._get("m-old", "")
        self.assertEqual(res1.status_code, 200)
        self.assertEqual(
            [m["model_identifier"] for m in res1.json()["models"]], ["m-old"]
        )
        res2 = self._get("m-new", "m-new-fb")
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(
            [m["model_identifier"] for m in res2.json()["models"]],
            ["m-new", "m-new-fb"],
        )


if __name__ == "__main__":
    unittest.main()
