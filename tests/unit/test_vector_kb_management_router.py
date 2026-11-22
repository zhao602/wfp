# =============================================================================
# 向量知识库管理路由测试（tests/unit/test_vector_kb_management_router.py）
#
# 职责：验证 /api/v1/kb-management/** 端点契约（vector_kb_management
# design §2.2.2 T1~T9 / T7.4~T7.6）：开关门控（关闭 404 + 零副作用）、
# CRUD / 导入确认 / 分片列表 / 调整草稿 / 作业查询 / 诊断 HTTP 契约、
# 分页与错误码映射、零凭据响应、multipart 可选依赖降级。
# 执行命令：PYTHONPATH=src python -m pytest tests/unit/test_vector_kb_management_router.py -q
# =============================================================================

import os
import tempfile
import unittest
from typing import Any, Dict
from unittest.mock import patch

from fastapi.testclient import TestClient

from orchestration_platform.api import application_factory
from orchestration_platform.api.application_factory import create_app
from orchestration_platform.api.routers import vector_kb_management_router
from orchestration_platform.configuration.settings import Settings
from orchestration_platform.integrations.local_vector_database.config import (
    LocalVectorDatabaseConfig,
)
from orchestration_platform.integrations.local_vector_database.service import (
    LocalVectorDatabaseService,
)
from orchestration_platform.integrations.vector_kb_management.config import (
    VectorKbManagementConfig,
)
from orchestration_platform.integrations.vector_kb_management.service import (
    VectorKbManagementService,
)

BASE = "/api/v1/kb-management"


def _binding_payload() -> Dict[str, Any]:
    """api 模式不可达绑定 + 允许降级 → 确认链路走哈希兜底（密闭可测）。

    base_url 指向本机未监听端口（连接拒绝毫秒级失败），不依赖外部网络；
    响应侧经视图白名单剥离（零凭据，AC-45）。
    """
    return {
        "mode": "api",
        "model_identifier": "router-test-embedding",
        "base_url": "http://127.0.0.1:9",
        "allow_degraded_fallback": True,
    }


def _create_payload(kb_id: str, name: str = "路由测试知识库") -> Dict[str, Any]:
    return {
        "knowledge_base_id": kb_id,
        "name": name,
        "embedding_model": _binding_payload(),
    }


class _EnabledAppTestCase(unittest.TestCase):
    """启用态测试基类：真实服务注入 tmp 落点 + 开关启用 app。

    - 服务与向量存储均落 tmp（测试隔离，不触项目 .data）；
    - addCleanup 复位模块级服务持有（避免跨用例泄漏）。
    """

    def setUp(self) -> None:
        meta_td = tempfile.TemporaryDirectory()
        vector_td = tempfile.TemporaryDirectory()
        self.addCleanup(meta_td.cleanup)
        self.addCleanup(vector_td.cleanup)
        self.meta_root = meta_td.name
        self.vector_root = vector_td.name
        self.vector_service = LocalVectorDatabaseService(
            config=LocalVectorDatabaseConfig(storage_root=self.vector_root)
        )
        self.service = VectorKbManagementService(
            config=VectorKbManagementConfig(storage_root=self.meta_root),
            vector_service=self.vector_service,
        )
        vector_kb_management_router.set_vector_kb_management_service(self.service)
        self.addCleanup(
            vector_kb_management_router.set_vector_kb_management_service, None
        )
        with patch.object(
            application_factory,
            "get_settings",
            return_value=Settings(vector_kb_management_enabled=True),
        ):
            app = create_app()
        self.client = TestClient(app)

    def _create_kb(self, kb_id: str, name: str = "路由测试知识库"):
        return self.client.post(
            f"{BASE}/knowledge-bases", json=_create_payload(kb_id, name)
        )

    def _seed_import(self, kb_id: str, repeat: int = 30):
        """经门面受理导入（multipart 依赖缺失环境的密闭种子路径）。

        fixed_length + max_chunk_length=50 + overlap=0（校验取值域内）
        → 分片数确定 ≥ 3，后续断言取响应动态值。
        """
        text = ("知识库路由测试内容。" * repeat).encode("utf-8")
        return self.service.import_document(
            kb_id,
            "seed.txt",
            text,
            strategy="fixed_length",
            max_chunk_length=50,
            overlap=0,
        )

    def _confirm(self, kb_id: str, document_id: str, job_id: str):
        return self.client.post(
            f"{BASE}/knowledge-bases/{kb_id}/documents/{document_id}/confirm",
            json={
                "job_id": job_id,
                "snapshot_version": "v1",
                "allow_degraded_fallback": True,
            },
        )


# ---------------------------------------------------------------------------
# 开关门控（关闭 → 404 + 零副作用，INV-D23 / INV-D04）
# ---------------------------------------------------------------------------


class TestVectorKbManagementSwitchDisabled(unittest.TestCase):
    """开关关闭：端点不注册（404）、服务不构造、目录零创建。"""

    def test_disabled_switch_returns_404_and_creates_no_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage_root = os.path.join(tmp, "meta")
            with patch.object(
                application_factory,
                "get_settings",
                return_value=Settings(
                    vector_kb_management_enabled=False,
                    vector_kb_management_storage_root=storage_root,
                ),
            ):
                app = create_app()
            client = TestClient(app)
            # 既有路由不受影响（开关只门控本模块）
            self.assertEqual(client.get("/healthz").status_code, 200)
            # 管理端点全部 404（未注册）
            self.assertEqual(
                client.get(f"{BASE}/knowledge-bases").status_code, 404
            )
            self.assertEqual(
                client.get(f"{BASE}/knowledge-bases/kb-1").status_code, 404
            )
            self.assertEqual(
                client.get(f"{BASE}/embedding-models").status_code, 404
            )
            self.assertEqual(
                client.get(f"{BASE}/knowledge-bases/kb-1/diagnosis").status_code,
                404,
            )
            self.assertEqual(
                client.get(
                    f"{BASE}/knowledge-bases/kb-1/jobs/job-1"
                ).status_code,
                404,
            )
            self.assertEqual(
                client.get(
                    f"{BASE}/knowledge-bases/kb-1/documents/doc-1/chunks"
                ).status_code,
                404,
            )
            self.assertEqual(
                client.post(
                    f"{BASE}/knowledge-bases", json=_create_payload("kb-1")
                ).status_code,
                404,
            )
            self.assertEqual(
                client.post(
                    f"{BASE}/knowledge-bases/kb-1/documents/doc-1/confirm",
                    json={
                        "job_id": "job-1",
                        "snapshot_version": "v1",
                    },
                ).status_code,
                404,
            )
            self.assertEqual(
                client.post(
                    f"{BASE}/knowledge-bases/kb-1/documents/doc-1/chunk-drafts",
                    json={"base_version": "v1", "operations": []},
                ).status_code,
                404,
            )
            self.assertEqual(
                client.post(
                    f"{BASE}/knowledge-bases/kb-1/documents/doc-1"
                    "/chunk-drafts/confirm",
                    json={"draft_version": "v1"},
                ).status_code,
                404,
            )
            self.assertEqual(
                client.post(
                    f"{BASE}/knowledge-bases/kb-1/documents",
                    files={"file": ("a.txt", b"x", "text/plain")},
                ).status_code,
                404,
            )
            # INV-D23：关闭时服务不构造，落点目录零创建
            self.assertFalse(os.path.exists(storage_root))


# ---------------------------------------------------------------------------
# T1 / T2：知识库 CRUD HTTP 契约（视图零凭据 + 错误码映射 + 分页）
# ---------------------------------------------------------------------------


class TestVectorKbManagementCrudEndpoints(_EnabledAppTestCase):
    """创建 / 列表 / 详情 / 更新 / 删除端点。"""

    def test_create_returns_view_without_credentials(self) -> None:
        res = self._create_kb("crud-kb")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["knowledge_base_id"], "crud-kb")
        self.assertEqual(body["name"], "路由测试知识库")
        self.assertEqual(body["document_count"], 0)
        self.assertEqual(body["chunk_count"], 0)
        self.assertIn("created_at", body)
        self.assertEqual(body["embedding_model"]["mode"], "api")
        self.assertEqual(
            body["embedding_model"]["model_identifier"], "router-test-embedding"
        )
        # 零凭据（AC-45/INV-D10）：base_url 仅写入不回显
        self.assertNotIn("base_url", res.text)
        self.assertNotIn("api_key", res.text)

    def test_create_duplicate_returns_409(self) -> None:
        self.assertEqual(self._create_kb("dup-kb").status_code, 200)
        res = self._create_kb("dup-kb")
        self.assertEqual(res.status_code, 409)
        self.assertEqual(
            res.json()["code"], "VECTOR_KB_KNOWLEDGE_BASE_ALREADY_EXISTS"
        )

    def test_create_missing_binding_returns_422(self) -> None:
        payload = _create_payload("nobinding-kb")
        del payload["embedding_model"]
        res = self.client.post(f"{BASE}/knowledge-bases", json=payload)
        self.assertEqual(res.status_code, 422)

    def test_create_invalid_identifier_returns_422(self) -> None:
        res = self._create_kb("UPPER_Invalid")
        self.assertEqual(res.status_code, 422)
        self.assertEqual(
            res.json()["code"], "VECTOR_KB_INVALID_KNOWLEDGE_BASE_ID"
        )

    def test_list_pagination_contracts(self) -> None:
        self._create_kb("list-kb-1")
        self._create_kb("list-kb-2")
        res = self.client.get(f"{BASE}/knowledge-bases")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["total"], 2)
        self.assertEqual(len(body["items"]), 2)
        # 分页切片
        res_page = self.client.get(
            f"{BASE}/knowledge-bases", params={"page": 2, "page_size": 1}
        )
        self.assertEqual(len(res_page.json()["items"]), 1)
        # 非法分页 → 422 VECTOR_KB_INVALID_PAGINATION（领域错误码，统一错误体）
        res_bad_page = self.client.get(
            f"{BASE}/knowledge-bases", params={"page": 0}
        )
        self.assertEqual(res_bad_page.status_code, 422)
        self.assertEqual(
            res_bad_page.json()["code"], "VECTOR_KB_INVALID_PAGINATION"
        )
        res_bad_size = self.client.get(
            f"{BASE}/knowledge-bases", params={"page_size": 201}
        )
        self.assertEqual(res_bad_size.status_code, 422)
        self.assertEqual(
            res_bad_size.json()["code"], "VECTOR_KB_INVALID_PAGINATION"
        )

    def test_get_detail_and_missing_404(self) -> None:
        self._create_kb("detail-kb")
        res = self.client.get(f"{BASE}/knowledge-bases/detail-kb")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["knowledge_base_id"], "detail-kb")
        res_missing = self.client.get(f"{BASE}/knowledge-bases/no-such-kb")
        self.assertEqual(res_missing.status_code, 404)
        self.assertEqual(
            res_missing.json()["code"], "VECTOR_KB_KNOWLEDGE_BASE_NOT_FOUND"
        )

    def test_update_name_partial_update(self) -> None:
        self._create_kb("update-kb")
        res = self.client.put(
            f"{BASE}/knowledge-bases/update-kb", json={"name": "改名后的知识库"}
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["name"], "改名后的知识库")
        # 未提交字段不变（部分更新）
        self.assertEqual(
            body["embedding_model"]["model_identifier"], "router-test-embedding"
        )
        self.assertNotIn("base_url", res.text)
        res_missing = self.client.put(
            f"{BASE}/knowledge-bases/no-such-kb", json={"name": "x"}
        )
        self.assertEqual(res_missing.status_code, 404)

    def test_delete_default_metadata_only(self) -> None:
        self._create_kb("del-kb")
        res = self.client.delete(f"{BASE}/knowledge-bases/del-kb")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["knowledge_base_id"], "del-kb")
        self.assertFalse(res.json()["purged_vectors"])
        # 删除后详情 404；重复删除 404
        self.assertEqual(
            self.client.get(f"{BASE}/knowledge-bases/del-kb").status_code, 404
        )
        res_again = self.client.delete(f"{BASE}/knowledge-bases/del-kb")
        self.assertEqual(res_again.status_code, 404)
        self.assertEqual(
            res_again.json()["code"], "VECTOR_KB_KNOWLEDGE_BASE_NOT_FOUND"
        )

    def test_embedding_models_endpoint_contract(self) -> None:
        res = self.client.get(f"{BASE}/embedding-models")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["candidates"], [])
        self.assertEqual(body["total"], 0)
        self.assertTrue(body["allow_manual_input"])


# ---------------------------------------------------------------------------
# T3 / T4 / T5 / T9 + 作业：导入确认链路 HTTP 契约（哈希兜底密闭）
# ---------------------------------------------------------------------------


class TestVectorKbManagementIngestionEndpoints(_EnabledAppTestCase):
    """确认导入 / 分片列表 / 作业查询 / 诊断端点。"""

    def test_confirm_import_marks_chunks_and_collection_healthy(self) -> None:
        self._create_kb("ingest-kb")
        draft = self._seed_import("ingest-kb")
        self.assertGreaterEqual(draft.chunk_count, 3)
        # 受理零写存储（AC-38/INV-D19）：集合未创建
        res_diag0 = self.client.get(
            f"{BASE}/knowledge-bases/ingest-kb/diagnosis"
        )
        self.assertEqual(res_diag0.status_code, 200)
        self.assertFalse(res_diag0.json()["ok"])
        self.assertEqual(res_diag0.json()["record_count"], 0)
        # 确认前分片未标注向量化
        res_chunks0 = self.client.get(
            f"{BASE}/knowledge-bases/ingest-kb"
            f"/documents/{draft.document_id}/chunks"
        )
        self.assertEqual(res_chunks0.json()["vectorized_count"], 0)
        # HTTP 确认（绑定声明 + 请求级均允许降级 → 哈希兜底）
        res = self._confirm("ingest-kb", draft.document_id, draft.job_id)
        self.assertEqual(res.status_code, 200)
        report = res.json()
        self.assertEqual(report["status"], "succeeded")
        self.assertEqual(report["chunk_count"], draft.chunk_count)
        self.assertEqual(report["provenance"], "hash_fallback")
        self.assertTrue(report["local_degraded"])
        # 分片标注回填（T5 可见性：vectorized/degraded 全量）
        res_chunks = self.client.get(
            f"{BASE}/knowledge-bases/ingest-kb"
            f"/documents/{draft.document_id}/chunks"
        )
        self.assertEqual(res_chunks.status_code, 200)
        body = res_chunks.json()
        self.assertEqual(body["total"], draft.chunk_count)
        self.assertEqual(body["vectorized_count"], draft.chunk_count)
        self.assertEqual(body["degraded_count"], draft.chunk_count)
        self.assertEqual(body["items"][0]["provenance"], "hash_fallback")
        self.assertTrue(body["items"][0]["local_degraded"])
        # 诊断：集合健康 + 计数一致
        res_diag = self.client.get(
            f"{BASE}/knowledge-bases/ingest-kb/diagnosis"
        )
        diag = res_diag.json()
        self.assertTrue(diag["ok"])
        self.assertEqual(diag["record_count"], draft.chunk_count)
        self.assertEqual(diag["collection_id"], "ingest-kb")

    def test_confirm_snapshot_version_conflict_409(self) -> None:
        self._create_kb("conflict-kb")
        draft = self._seed_import("conflict-kb")
        res = self.client.post(
            f"{BASE}/knowledge-bases/conflict-kb"
            f"/documents/{draft.document_id}/confirm",
            json={
                "job_id": draft.job_id,
                "snapshot_version": "v99",
                "allow_degraded_fallback": True,
            },
        )
        self.assertEqual(res.status_code, 409)
        self.assertEqual(
            res.json()["code"], "VECTOR_KB_DRAFT_VERSION_CONFLICT"
        )

    def test_confirm_unknown_job_404(self) -> None:
        self._create_kb("job-kb")
        draft = self._seed_import("job-kb")
        res = self.client.post(
            f"{BASE}/knowledge-bases/job-kb"
            f"/documents/{draft.document_id}/confirm",
            json={
                "job_id": "job_does_not_exist",
                "snapshot_version": "v1",
            },
        )
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()["code"], "VECTOR_KB_UNKNOWN_INGESTION_JOB")

    def test_jobs_endpoint_kb_scoped(self) -> None:
        self._create_kb("jobs-kb")
        draft = self._seed_import("jobs-kb")
        self._confirm("jobs-kb", draft.document_id, draft.job_id)
        res = self.client.get(
            f"{BASE}/knowledge-bases/jobs-kb/jobs/{draft.job_id}"
        )
        self.assertEqual(res.status_code, 200)
        job = res.json()
        self.assertEqual(job["job_id"], draft.job_id)
        self.assertEqual(job["knowledge_base_id"], "jobs-kb")
        self.assertEqual(job["status"], "succeeded")
        # 归属不符 → 404
        res_other = self.client.get(
            f"{BASE}/knowledge-bases/jobs-other/jobs/{draft.job_id}"
        )
        self.assertEqual(res_other.status_code, 404)
        self.assertEqual(
            res_other.json()["code"], "VECTOR_KB_UNKNOWN_INGESTION_JOB"
        )
        # 标识非法 → 422（INV-D11）
        res_invalid = self.client.get(
            f"{BASE}/knowledge-bases/UPPER/jobs/{draft.job_id}"
        )
        self.assertEqual(res_invalid.status_code, 422)
        self.assertEqual(
            res_invalid.json()["code"], "VECTOR_KB_INVALID_KNOWLEDGE_BASE_ID"
        )

    def test_chunks_pagination_and_document_404(self) -> None:
        self._create_kb("chunks-kb")
        draft = self._seed_import("chunks-kb")
        res_bad = self.client.get(
            f"{BASE}/knowledge-bases/chunks-kb"
            f"/documents/{draft.document_id}/chunks",
            params={"page": 0},
        )
        self.assertEqual(res_bad.status_code, 422)
        self.assertEqual(res_bad.json()["code"], "VECTOR_KB_INVALID_PAGINATION")
        # 分页切片
        res_page = self.client.get(
            f"{BASE}/knowledge-bases/chunks-kb"
            f"/documents/{draft.document_id}/chunks",
            params={"page": 2, "page_size": 1},
        )
        body = res_page.json()
        self.assertEqual(len(body["items"]), 1)
        self.assertEqual(body["page"], 2)
        self.assertEqual(body["page_size"], 1)
        # 未知文档 → 404 DOCUMENT_NOT_FOUND
        res_missing = self.client.get(
            f"{BASE}/knowledge-bases/chunks-kb/documents/doc_missing/chunks"
        )
        self.assertEqual(res_missing.status_code, 404)
        self.assertEqual(
            res_missing.json()["code"], "VECTOR_KB_DOCUMENT_NOT_FOUND"
        )


# ---------------------------------------------------------------------------
# T6 / T7：调整草稿 / 确认调整 HTTP 契约
# ---------------------------------------------------------------------------


class TestVectorKbManagementChunkDraftEndpoints(_EnabledAppTestCase):
    """分片编辑（merge/split/edit）→ 草稿 → 确认链路。"""

    def test_edit_draft_and_confirm_http(self) -> None:
        self._create_kb("draft-kb")
        draft = self._seed_import("draft-kb")
        self.assertEqual(
            self._confirm("draft-kb", draft.document_id, draft.job_id).status_code,
            200,
        )
        first_chunk_id = draft.chunks[0].chunk_id
        # T6：提交调整草稿（edit → v2，不生效）
        res = self.client.post(
            f"{BASE}/knowledge-bases/draft-kb"
            f"/documents/{draft.document_id}/chunk-drafts",
            json={
                "base_version": "v1",
                "operations": [
                    {
                        "op": "edit",
                        "chunk_id": first_chunk_id,
                        "text": "已编辑的分片文本",
                    }
                ],
            },
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["draft_version"], "v2")
        self.assertEqual(body["chunks"][0]["text"], "已编辑的分片文本")
        # T7：确认调整（仅重算受影响分片）
        res_confirm = self.client.post(
            f"{BASE}/knowledge-bases/draft-kb"
            f"/documents/{draft.document_id}/chunk-drafts/confirm",
            json={"draft_version": "v2"},
        )
        self.assertEqual(res_confirm.status_code, 200)
        confirm_body = res_confirm.json()
        self.assertEqual(confirm_body["status"], "succeeded")
        self.assertIn(first_chunk_id, confirm_body["rewritten"])
        # 编辑文本生效 + 编辑条目重算后标注回填（全量 vectorized）
        res_chunks = self.client.get(
            f"{BASE}/knowledge-bases/draft-kb"
            f"/documents/{draft.document_id}/chunks"
        )
        chunks_body = res_chunks.json()
        self.assertEqual(chunks_body["items"][0]["text"], "已编辑的分片文本")
        self.assertEqual(chunks_body["total"], draft.chunk_count)
        self.assertEqual(chunks_body["vectorized_count"], draft.chunk_count)

    def test_chunk_draft_base_version_conflict_409(self) -> None:
        self._create_kb("draftc-kb")
        draft = self._seed_import("draftc-kb")
        res = self.client.post(
            f"{BASE}/knowledge-bases/draftc-kb"
            f"/documents/{draft.document_id}/chunk-drafts",
            json={
                "base_version": "v9",
                "operations": [
                    {"op": "edit", "chunk_id": draft.chunks[0].chunk_id, "text": "x"}
                ],
            },
        )
        self.assertEqual(res.status_code, 409)
        self.assertEqual(
            res.json()["code"], "VECTOR_KB_DRAFT_VERSION_CONFLICT"
        )

    def test_confirm_unknown_draft_version_409(self) -> None:
        self._create_kb("draftu-kb")
        draft = self._seed_import("draftu-kb")
        res = self.client.post(
            f"{BASE}/knowledge-bases/draftu-kb"
            f"/documents/{draft.document_id}/chunk-drafts/confirm",
            json={"draft_version": "v9"},
        )
        self.assertEqual(res.status_code, 409)
        self.assertEqual(
            res.json()["code"], "VECTOR_KB_DRAFT_VERSION_CONFLICT"
        )

    def test_empty_operations_rejected_422(self) -> None:
        self._create_kb("drafte-kb")
        draft = self._seed_import("drafte-kb")
        res = self.client.post(
            f"{BASE}/knowledge-bases/drafte-kb"
            f"/documents/{draft.document_id}/chunk-drafts",
            json={"base_version": "v1", "operations": []},
        )
        self.assertEqual(res.status_code, 422)


# ---------------------------------------------------------------------------
# T2 删除 purge 语义（显式破坏性操作，登记实验）
# ---------------------------------------------------------------------------


class TestVectorKbManagementDeletePurge(_EnabledAppTestCase):
    """删除知识库：默认仅元数据；purge_vectors=true 清除向量记录。"""

    def test_delete_with_purge_clears_vector_records(self) -> None:
        self._create_kb("purge-kb")
        draft = self._seed_import("purge-kb")
        self.assertEqual(
            self._confirm("purge-kb", draft.document_id, draft.job_id).status_code,
            200,
        )
        self.assertEqual(
            self.vector_service.diagnose("purge-kb").record_count,
            draft.chunk_count,
        )
        res = self.client.delete(
            f"{BASE}/knowledge-bases/purge-kb", params={"purge_vectors": True}
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["purged_vectors"])
        # 向量集合记录已清除（集合文件保留，记录数归零）
        self.assertEqual(
            self.vector_service.diagnose("purge-kb").record_count, 0
        )

    def test_delete_without_purge_keeps_vector_records(self) -> None:
        self._create_kb("keep-kb")
        draft = self._seed_import("keep-kb")
        self.assertEqual(
            self._confirm("keep-kb", draft.document_id, draft.job_id).status_code,
            200,
        )
        res = self.client.delete(f"{BASE}/knowledge-bases/keep-kb")
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.json()["purged_vectors"])
        # 元数据已删（详情 404），向量集合保留
        self.assertEqual(
            self.client.get(f"{BASE}/knowledge-bases/keep-kb").status_code, 404
        )
        self.assertEqual(
            self.vector_service.diagnose("keep-kb").record_count,
            draft.chunk_count,
        )


# ---------------------------------------------------------------------------
# T3 multipart：python-multipart 可选依赖（可用 → HTTP 契约；缺失 → 404 降级）
# ---------------------------------------------------------------------------


class TestVectorKbManagementMultipartImport(_EnabledAppTestCase):
    """文件导入端点（multipart；CHG-DEP 可选依赖条件注册）。"""

    @unittest.skipUnless(
        vector_kb_management_router.MULTIPART_AVAILABLE,
        "python-multipart 未安装（CHG-DEP 缺失降级，门面链路另测）",
    )
    def test_import_document_multipart_http(self) -> None:
        self._create_kb("import-kb")
        res = self.client.post(
            f"{BASE}/knowledge-bases/import-kb/documents",
            files={
                "file": (
                    "notes.txt",
                    ("多段导入文本内容。" * 20).encode("utf-8"),
                    "text/plain",
                )
            },
            data={"strategy": "fixed_length", "max_chunk_length": "50", "overlap": "0"},
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["parse_status"], "parsed")
        self.assertGreaterEqual(body["chunk_count"], 3)
        self.assertTrue(body["job_id"])
        self.assertTrue(body["document_id"])
        # 受理零写存储（AC-38/INV-D19）：集合未创建
        diag = self.client.get(
            f"{BASE}/knowledge-bases/import-kb/diagnosis"
        ).json()
        self.assertFalse(diag["ok"])
        # 零凭据
        self.assertNotIn("base_url", res.text)

    def test_import_endpoint_degrades_404_when_multipart_missing(self) -> None:
        if vector_kb_management_router.MULTIPART_AVAILABLE:
            self.skipTest("python-multipart 已安装（条件注册生效，走完整契约测试）")
        self._create_kb("import-kb")
        res = self.client.post(
            f"{BASE}/knowledge-bases/import-kb/documents",
            files={"file": ("notes.txt", b"content", "text/plain")},
        )
        # 缺失降级：端点未注册（404），不影响其余端点
        self.assertEqual(res.status_code, 404)
        self.assertEqual(
            self.client.get(f"{BASE}/knowledge-bases/import-kb").status_code,
            200,
        )


if __name__ == "__main__":
    unittest.main()
