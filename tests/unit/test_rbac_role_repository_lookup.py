# =============================================================================
# RBAC 落库接线用例（tests/unit/test_rbac_role_repository_lookup.py）
#
# 覆盖验收（arch_review_v2_tasks 2.12 / design DC-06 / 卡 A4，CHG-25）：
#   1. authorize 缓存未命中经角色仓储查库并写缓存；
#   2. 仓储缺失 → 告警 + 写空（fail-closed 语义不变）；
#   3. 仓储查询失败 → 告警 + 写空（fail-closed）；
#   4. bootstrap 幂等（重复执行无重复数据，摘要全 False/空）；
#   5. 管理员放行（AC-08：开启 RBAC + bootstrap 后不再全量 403）；
#   6. 组织继承路径（绑定于祖先组织节点 → 后代用户继承角色权限）。
# =============================================================================

import asyncio
import logging
import time
from typing import List, Optional, Tuple

import pytest

import orchestration_platform.governance.rbac.permission_checker as checker_module
from orchestration_platform.configuration.settings import Settings
from orchestration_platform.domain.models.enums import (
    OperationType,
    ResourceScope,
    ResourceType,
)
from orchestration_platform.governance.interceptors.base import GovernanceRequest
from orchestration_platform.governance.persistence.local_file.local_file_role_repository import (
    LocalFileRoleRepository,
)
from orchestration_platform.governance.persistence.local_file.local_file_user_repository import (
    LocalFileUserRepository,
)
from orchestration_platform.governance.rbac.bootstrap import (
    ADMIN_ROLE_ID,
    DEFAULT_TENANT_ID,
    initialize_rbac_bootstrap,
)
from orchestration_platform.governance.rbac.models import PermissionItem, RoleBinding
from orchestration_platform.governance.rbac.permission_cache import PermissionCache
from orchestration_platform.governance.rbac.permission_checker import (
    RbacPermissionChecker,
)
from orchestration_platform.governance.rbac.permission_registry import (
    create_default_registry,
)
from orchestration_platform.governance.tenant.models import User


def _bootstrap_settings(tmp_path, admin_user: str = "") -> Settings:
    return Settings(
        storage_backend="local_file",
        local_storage_root=str(tmp_path),
        rbac_bootstrap_admin_user=admin_user,
    )


def _perm(
    permission_id: str = "workflow_read",
    operation: OperationType = OperationType.READ,
) -> PermissionItem:
    return PermissionItem(
        permission_id=permission_id,
        resource_type=ResourceType.WORKFLOW_DEFINITION,
        operation=operation,
        scope=ResourceScope.TENANT,
    )


class _StubRoleRepository:
    """角色仓储桩：可控返回值 / 可控抛错，记录调用。"""

    def __init__(
        self,
        permissions: Optional[List[PermissionItem]] = None,
        error: Optional[Exception] = None,
    ) -> None:
        self._permissions = permissions or []
        self._error = error
        self.calls: List[Tuple[str, str]] = []

    async def list_permissions_for_user(
        self, tenant_id: str, user_id: str
    ) -> List[PermissionItem]:
        self.calls.append((tenant_id, user_id))
        if self._error is not None:
            raise self._error
        return list(self._permissions)


def _authorized_request(tenant_id: str, user_id: str) -> GovernanceRequest:
    """构造已认证状态的治理请求（authorize 输入）。"""
    return GovernanceRequest(
        resource_type=ResourceType.WORKFLOW_DEFINITION,
        operation=OperationType.READ,
        trace_id="trace-rbac-lookup",
    )


class TestAuthorizeRepositoryLookup:
    """authorize 缓存未命中查库写缓存。"""

    def test_cache_miss_queries_repository_and_writes_cache(self) -> None:
        stub = _StubRoleRepository(permissions=[_perm()])
        checker = RbacPermissionChecker(
            registry=create_default_registry(),
            cache=PermissionCache(),
            role_repository=stub,
        )
        request = _authorized_request(DEFAULT_TENANT_ID, "user_a")
        request.tenant_id = DEFAULT_TENANT_ID
        request.user_id = "user_a"

        decision = asyncio.run(checker.authorize(request))
        assert decision.allowed  # 权限匹配放行
        assert stub.calls == [(DEFAULT_TENANT_ID, "user_a")]
        # 缓存已写入：再次 authorize 不再查库
        decision2 = asyncio.run(checker.authorize(request))
        assert decision2.allowed
        assert len(stub.calls) == 1  # 未重复查库
        cached = checker._cache.get_permissions(DEFAULT_TENANT_ID, "user_a")
        assert cached is not None and len(cached) == 1

    def test_no_matching_permission_denied(self) -> None:
        # 权限项操作类型不匹配（DELETE ≠ READ）→ 拒绝
        stub = _StubRoleRepository(
            permissions=[_perm("workflow_delete", operation=OperationType.DELETE)]
        )
        checker = RbacPermissionChecker(
            registry=create_default_registry(),
            cache=PermissionCache(),
            role_repository=stub,
        )
        request = _authorized_request(DEFAULT_TENANT_ID, "user_a")
        request.tenant_id = DEFAULT_TENANT_ID
        request.user_id = "user_a"
        decision = asyncio.run(checker.authorize(request))
        assert not decision.allowed
        assert "权限不足" in decision.deny_reason


class TestRepositoryMissingOrFailing:
    """仓储缺失 / 查询失败 → 告警写空（fail-closed）。"""

    def test_missing_repository_warns_and_writes_empty(self, caplog) -> None:
        """仓储未注入：既有写空行为保留 + 告警（task 2.12）。"""
        checker = RbacPermissionChecker(
            registry=create_default_registry(),
            cache=PermissionCache(),
        )
        request = _authorized_request(DEFAULT_TENANT_ID, "user_b")
        request.tenant_id = DEFAULT_TENANT_ID
        request.user_id = "user_b"
        with caplog.at_level(
            logging.WARNING,
            logger="orchestration_platform.governance.rbac.permission_checker",
        ):
            decision = asyncio.run(checker.authorize(request))
        assert not decision.allowed  # 写空 → 无权限 → 拒绝
        assert "角色仓储未注入" in caplog.text
        # 空权限已写缓存：第二次不再告警（缓存命中）
        caplog.clear()
        asyncio.run(checker.authorize(request))
        assert "角色仓储未注入" not in caplog.text

    def test_failing_repository_warns_and_writes_empty(self, caplog) -> None:
        """仓储查询失败：告警 + 写空（fail-closed，不吞异常放行）。"""
        stub = _StubRoleRepository(error=RuntimeError("db down"))
        checker = RbacPermissionChecker(
            registry=create_default_registry(),
            cache=PermissionCache(),
            role_repository=stub,
        )
        request = _authorized_request(DEFAULT_TENANT_ID, "user_c")
        request.tenant_id = DEFAULT_TENANT_ID
        request.user_id = "user_c"
        with caplog.at_level(
            logging.WARNING,
            logger="orchestration_platform.governance.rbac.permission_checker",
        ):
            decision = asyncio.run(checker.authorize(request))
        assert not decision.allowed
        assert "角色仓储查询失败" in caplog.text


class TestBootstrapIdempotent:
    """bootstrap 幂等（重复执行无重复数据）。"""

    def test_bootstrap_creates_defaults_then_noop_on_rerun(self, tmp_path) -> None:
        settings = _bootstrap_settings(tmp_path, admin_user="user_admin")
        first = asyncio.run(initialize_rbac_bootstrap(settings))
        assert first["tenant_created"] is True
        assert set(first["roles_created"]) == {
            "role_tenant_admin",
            "role_orchestrator",
            "role_auditor",
        }
        assert first["admin_user_created"] is True
        assert first["admin_binding_created"] is True

        # 幂等重跑：全部跳过（摘要全 False/空），无重复数据
        second = asyncio.run(initialize_rbac_bootstrap(settings))
        assert second == {
            "tenant_created": False,
            "roles_created": [],
            "admin_user_created": False,
            "admin_binding_created": False,
        }

    def test_bootstrap_without_admin_user_skips_binding(self, tmp_path) -> None:
        settings = _bootstrap_settings(tmp_path, admin_user="")
        summary = asyncio.run(initialize_rbac_bootstrap(settings))
        assert summary["admin_user_created"] is False
        assert summary["admin_binding_created"] is False
        repo = LocalFileRoleRepository(storage_root=str(tmp_path))
        bindings = asyncio.run(
            repo.list_bindings_for_user(DEFAULT_TENANT_ID, "user_admin")
        )
        assert bindings == []


class TestAdminAuthorizedAfterBootstrap:
    """管理员放行（AC-08：开启 RBAC + bootstrap 后不再全量 403）。"""

    def test_admin_plain_flow_allowed(self, tmp_path, monkeypatch) -> None:
        settings = _bootstrap_settings(tmp_path, admin_user="user_admin")
        asyncio.run(initialize_rbac_bootstrap(settings))

        repo = LocalFileRoleRepository(storage_root=str(tmp_path))
        checker = RbacPermissionChecker(
            registry=create_default_registry(),
            cache=PermissionCache(),
            role_repository=repo,
        )
        # checker.authenticate 读取三态配置 → 注入测试 settings
        monkeypatch.setattr(checker_module, "get_settings", lambda: settings)

        request = GovernanceRequest(
            token=f"{DEFAULT_TENANT_ID}:user_admin:{int(time.time()) + 600}",
            resource_type=ResourceType.WORKFLOW_DEFINITION,
            operation=OperationType.READ,
            trace_id="trace-admin",
        )
        auth_decision = asyncio.run(checker.authenticate(request))
        assert auth_decision.allowed

        authz_decision = asyncio.run(checker.authorize(request))
        assert authz_decision.allowed  # AC-08：管理员不再全量 403
        # 管理员角色权限已从库加载并写缓存（含租户管理员全部权限项）
        cached = checker._cache.get_permissions(DEFAULT_TENANT_ID, "user_admin")
        assert cached is not None and len(cached) > 0

    def test_non_bootstrapped_user_denied_fail_closed(self, tmp_path) -> None:
        """未绑定角色的用户 → 查库为空 → 403（fail-closed 正确语义，DC-06 规则 4）。"""
        settings = _bootstrap_settings(tmp_path, admin_user="user_admin")
        asyncio.run(initialize_rbac_bootstrap(settings))

        repo = LocalFileRoleRepository(storage_root=str(tmp_path))
        checker = RbacPermissionChecker(
            registry=create_default_registry(),
            cache=PermissionCache(),
            role_repository=repo,
        )
        request = _authorized_request(DEFAULT_TENANT_ID, "user_stranger")
        request.tenant_id = DEFAULT_TENANT_ID
        request.user_id = "user_stranger"
        decision = asyncio.run(checker.authorize(request))
        assert not decision.allowed
        assert "权限不足" in decision.deny_reason


class TestOrgInheritance:
    """组织继承：绑定于祖先组织节点 → 后代用户继承角色权限。"""

    def test_ancestor_org_binding_inherited(self, tmp_path) -> None:
        import json

        repo = LocalFileRoleRepository(storage_root=str(tmp_path))
        tenant_dir = (
            tmp_path / "governance" / "tenants" / DEFAULT_TENANT_ID
        )
        org_dir = tenant_dir / "org_nodes"
        org_dir.mkdir(parents=True, exist_ok=True)
        # 组织树：org_root → org_child（user_org 挂载于 org_child）
        (org_dir / "org_root.json").write_text(
            json.dumps({"org_node_id": "org_root", "node_path": "/org_root"}),
            encoding="utf-8",
        )
        (org_dir / "org_child.json").write_text(
            json.dumps(
                {
                    "org_node_id": "org_child",
                    "node_path": "/org_root/org_child",
                }
            ),
            encoding="utf-8",
        )

        user_repo = LocalFileUserRepository(storage_root=str(tmp_path))
        asyncio.run(
            user_repo.create_user(
                User(
                    user_id="user_org",
                    tenant_id=DEFAULT_TENANT_ID,
                    username="user_org",
                    org_node_id="org_child",
                )
            )
        )

        # 审计员角色 + 绑定于祖先节点 org_root（绑定主体为另一用户，证明继承路径）
        registry = create_default_registry()
        auditor_role = registry.get_default_role("role_auditor")
        assert auditor_role is not None
        asyncio.run(repo.create_role(auditor_role))
        asyncio.run(
            repo.create_binding(
                RoleBinding(
                    binding_id="binding_org_root_auditor",
                    role_id="role_auditor",
                    user_id="user_parent",
                    org_node_id="org_root",
                    tenant_id=DEFAULT_TENANT_ID,
                )
            )
        )

        permissions = asyncio.run(
            repo.list_permissions_for_user(DEFAULT_TENANT_ID, "user_org")
        )
        permission_ids = {p.permission_id for p in permissions}
        # 继承审计员角色权限（组织继承沿 node_path 向上汇聚）
        assert "audit_log_read" in permission_ids
        assert "audit_log_export" in permission_ids
        # admin 角色未绑定 → 不应出现其独有权限扩散
        assert ADMIN_ROLE_ID not in {b.role_id for b in asyncio.run(
            repo.list_bindings_for_user(DEFAULT_TENANT_ID, "user_org")
        )}
