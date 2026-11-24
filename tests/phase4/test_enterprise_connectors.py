# =============================================================================
# 企业连接器测试（tests/phase4/test_enterprise_connectors.py）
#
# 职责：验证 CRM/ERP/OA 连接器、凭证管理、数据范围守卫与降级。
# 设计意图（对应偏差修正 T181 / 验收 A127-A133, B111-B112）：
# =============================================================================

import asyncio
import unittest

from orchestration_platform.domain.errors import CONNECTOR_SCOPE_DENIED, DomainError
from orchestration_platform.domain.models.enums import DataSourceType
from orchestration_platform.integrations.data_source_connectors.enterprise.credential_store import (
    EnterpriseCredentialStore,
)
from orchestration_platform.integrations.data_source_connectors.enterprise.data_scope_guard import (
    DataScopeGuard,
)
from orchestration_platform.integrations.data_source_connectors.enterprise.models import (
    DataScope,
)


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestDataScopeGuard(unittest.TestCase):
    """数据范围守卫测试。"""

    def test_read_allowed(self):
        guard = DataScopeGuard()
        scope = DataScope(readable_resources=["customers"], writable_resources=[])
        guard.check_read("customers", scope)

    def test_read_denied(self):
        guard = DataScopeGuard()
        scope = DataScope(readable_resources=["customers"], writable_resources=[])
        with self.assertRaises(DomainError) as ctx:
            guard.check_read("orders", scope)
        self.assertEqual(ctx.exception.code, CONNECTOR_SCOPE_DENIED)

    def test_write_allowed(self):
        guard = DataScopeGuard()
        scope = DataScope(readable_resources=[], writable_resources=["customers"])
        guard.check_write("customers", scope)

    def test_write_denied(self):
        guard = DataScopeGuard()
        scope = DataScope(readable_resources=["customers"], writable_resources=[])
        with self.assertRaises(DomainError):
            guard.check_write("customers", scope)


class TestEnterpriseCredentialStore(unittest.TestCase):
    """企业凭证仓库测试。"""

    def test_put_and_get(self):
        store = EnterpriseCredentialStore()
        store.put("conn-1", "secret-1", "admin")
        plaintext = store.get_active("conn-1")
        self.assertEqual(plaintext, "secret-1")

    def test_version_increment(self):
        store = EnterpriseCredentialStore()
        store.put("conn-1", "secret-1", "admin")
        store.put("conn-1", "secret-2", "admin")
        versions = store.list_versions("conn-1")
        self.assertEqual(len(versions), 2)
        self.assertEqual(versions[0].version, 1)
        self.assertEqual(versions[1].version, 2)

    def test_hot_reload(self):
        store = EnterpriseCredentialStore()
        store.put("conn-1", "secret-1", "admin")
        self.assertEqual(store.get_active("conn-1"), "secret-1")
        store.put("conn-1", "secret-2", "admin")
        self.assertEqual(store.get_active("conn-1"), "secret-2")

    def test_list_versions_no_credential(self):
        store = EnterpriseCredentialStore()
        versions = store.list_versions("nonexistent")
        self.assertEqual(len(versions), 0)


class TestDataSourceTypeRegression(unittest.TestCase):
    """DataSourceType 枚举回归测试。"""

    def test_existing_types_unchanged(self):
        expected = {"csv", "json", "rest_api", "database", "hologres", "kafka", "flink", "spark"}
        all_values = {m.value for m in DataSourceType}
        self.assertTrue(expected.issubset(all_values))

    def test_new_types_added(self):
        self.assertEqual(DataSourceType.CRM.value, "crm")
        self.assertEqual(DataSourceType.ERP.value, "erp")
        self.assertEqual(DataSourceType.OA.value, "oa")

    def test_total_count(self):
        self.assertEqual(len(list(DataSourceType)), 11)


class TestEnterpriseConnectorCompatibility(unittest.TestCase):
    """企业连接器兼容性测试。"""

    def test_disabled_by_default(self):
        from orchestration_platform.configuration.settings import get_settings
        settings = get_settings()
        self.assertFalse(settings.enterprise_connector_enabled)


if __name__ == "__main__":
    unittest.main()
