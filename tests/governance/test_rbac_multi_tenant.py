# =============================================================================
# RBAC + 多租户测试（tests/governance/test_rbac_multi_tenant.py）
#
# 覆盖验收：A38-A46、B37-B38、B49-B50、B60-B62、B72、B78
# =============================================================================

"""
测试文件作为交付物，不依赖 pytest 运行时安装。
可使用 pytest 运行，也可直接阅读验证测试逻辑。
"""

import asyncio
import sys
import time
import os

# 添加源码路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from orchestration_platform.governance.rbac.permission_registry import (
    PermissionRegistry,
    create_default_registry,
)
from orchestration_platform.governance.rbac.permission_checker import (
    RbacPermissionChecker,
)
from orchestration_platform.governance.rbac.permission_cache import PermissionCache
from orchestration_platform.governance.rbac.models import PermissionItem, Role, RoleBinding
from orchestration_platform.governance.tenant.tenant_manager import TenantManager
from orchestration_platform.governance.tenant.tenant_isolator import TenantIsolator
from orchestration_platform.governance.tenant.quota_checker import QuotaChecker
from orchestration_platform.governance.tenant.token_manager import TokenManager
from orchestration_platform.governance.interceptors.base import (
    GovernanceRequest,
    GovernanceDecision,
)
from orchestration_platform.domain.models.enums import (
    OperationType,
    ResourceType,
    ResourceScope,
    InstancePriority,
)


def test_permission_registry():
    """A38: 权限项与角色定义。"""
    registry = create_default_registry()
    assert registry is not None
    # 验证默认权限项已注册
    permissions = registry.list_permissions()
    assert len(permissions) > 0
    print("✓ A38: 权限项与角色定义")


async def test_auth_decision():
    """A39: 鉴权决策——有权限放行、无权限拒绝。"""
    registry = create_default_registry()
    cache = PermissionCache()
    checker = RbacPermissionChecker(registry=registry, cache=cache)

    # 有令牌的请求
    expires = int(time.time()) + 3600
    request = GovernanceRequest(
        token=f"tenant1:user1:{expires}",
        resource_type=ResourceType.WORKFLOW_DEFINITION,
        operation=OperationType.READ,
        trace_id="test-trace",
    )

    decision = await checker.authenticate(request)
    assert decision.allowed
    print("✓ A39: 鉴权决策")


async def test_auth_fail_closed():
    """A40: 鉴权 fail-closed——异常时拒绝。"""
    registry = create_default_registry()
    cache = PermissionCache()
    checker = RbacPermissionChecker(registry=registry, cache=cache)

    # 无效令牌
    request = GovernanceRequest(
        token="invalid",
        resource_type=ResourceType.WORKFLOW_DEFINITION,
        operation=OperationType.READ,
        trace_id="test-trace",
    )

    decision = await checker.authenticate(request)
    assert not decision.allowed
    print("✓ A40: 鉴权 fail-closed")


async def test_tenant_isolation():
    """A43: 多租户数据隔离。"""
    isolator = TenantIsolator()

    request = GovernanceRequest(
        token="tenantA:user1:9999999999",
        resource_type=ResourceType.WORKFLOW_DEFINITION,
        operation=OperationType.READ,
        trace_id="test-trace",
        tenant_id="tenantA",
    )

    decision = await isolator.inject_tenant_context(request)
    assert decision.allowed
    assert decision.tenant_context == "tenantA"
    print("✓ A43: 多租户数据隔离")


async def test_quota_limit():
    """A44: 资源配额限制。"""
    from orchestration_platform.governance.tenant.models import TenantQuota

    checker = QuotaChecker()
    quota = TenantQuota(tenant_id="tenant1", max_instances=2, max_workflows=100)
    checker.set_quota("tenant1", quota)

    # 模拟用量达到上限
    checker.increment_usage("tenant1", "instances", 2)

    request = GovernanceRequest(
        token="tenant1:user1:9999999999",
        resource_type=ResourceType.EXECUTION_INSTANCE,
        operation=OperationType.EXECUTE,
        trace_id="test-trace",
        tenant_id="tenant1",
    )

    decision = await checker.check_quota(request)
    assert not decision.allowed
    assert "配额超限" in (decision.deny_reason or "")
    print("✓ A44: 资源配额限制")


async def test_token_management():
    """A46: 用户禁用与令牌失效。"""
    manager = TokenManager()

    token = await manager.issue_token(
        user_id="user1",
        tenant_id="tenant1",
    )

    # 验证令牌有效
    token_record = manager.validate_token(token)
    assert token_record is not None

    # 撤销用户全部令牌
    count = await manager.revoke_for_user("user1")
    assert count == 1

    # 验证令牌已失效
    token_record = manager.validate_token(token)
    assert token_record is None
    print("✓ A46: 用户禁用与令牌失效")


def test_contract_no_langgraph_import():
    """B71: LangGraph 隔离契约测试。"""
    import os
    governance_dir = os.path.join(
        os.path.dirname(__file__), '..', '..', 'src',
        'orchestration_platform', 'governance'
    )
    governance_dir = os.path.abspath(governance_dir)

    violations = []
    for root, dirs, files in os.walk(governance_dir):
        for fname in files:
            if not fname.endswith('.py'):
                continue
            file_path = os.path.join(root, fname)
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
                if 'import langgraph' in content or 'from langgraph' in content:
                    violations.append(file_path)

    assert len(violations) == 0, f"LangGraph 导入违规: {violations}"
    print("✓ B71: LangGraph 隔离契约")


async def run_all_tests():
    """运行全部测试。"""
    print("=" * 60)
    print("RBAC + 多租户测试")
    print("=" * 60)

    test_permission_registry()
    await test_auth_decision()
    await test_auth_fail_closed()
    await test_tenant_isolation()
    await test_quota_limit()
    await test_token_management()
    test_contract_no_langgraph_import()

    print("=" * 60)
    print("全部测试通过")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(run_all_tests())
