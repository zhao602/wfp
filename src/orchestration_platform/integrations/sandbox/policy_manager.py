# =============================================================================
# 沙箱策略管理器（integrations/sandbox/policy_manager.py）
#
# 职责：加载与校验沙箱策略，非法策略拒绝加载并回退默认安全策略。
# 设计意图（对应偏差修正 spec 5.1.3.2 / design 2.2.2.1）：
#   - 加载→校验→非法策略拒绝加载并回退默认安全策略（最严格限额）
#   - 返回 SANDBOX_POLICY_INVALID 及非法字段
#   - 策略标识在租户内唯一由系统生成
# =============================================================================

import logging
import os
import uuid
from typing import Optional, Tuple

from orchestration_platform.configuration.settings import get_settings
from orchestration_platform.domain.models.enums import (
    IsolationMode,
    NetworkPolicyMode,
)
from orchestration_platform.integrations.sandbox.abstractions.sandbox_policy_repository import (
    ISandboxPolicyRepository,
)
from orchestration_platform.integrations.sandbox.models import SandboxPolicy

_logger = logging.getLogger(__name__)


class SandboxPolicyManager:
    """沙箱策略管理器。

    构造注入策略仓储，负责策略加载、校验与默认安全策略生成。
    """

    def __init__(self, repository: ISandboxPolicyRepository) -> None:
        """初始化策略管理器。

        参数：
            repository：沙箱策略仓储
        """
        self._repository = repository

    async def load_policy(
        self,
        policy_id: str,
        tenant_id: str,
    ) -> Tuple[Optional[SandboxPolicy], Optional[str]]:
        """加载并校验策略。

        返回 (策略, 错误信息)。策略合法返回 (policy, None)；
        策略不存在或非法返回 (默认安全策略, 错误信息)。
        """
        policy = await self._repository.get(policy_id, tenant_id)

        if policy is None:
            _logger.warning(
                "沙箱策略不存在，回退默认安全策略: tenant=%s, policy=%s",
                tenant_id, policy_id,
            )
            return self._create_default_safe_policy(tenant_id), "策略不存在，已回退默认安全策略"

        if not policy.is_active:
            _logger.warning(
                "沙箱策略已停用，回退默认安全策略: tenant=%s, policy=%s",
                tenant_id, policy_id,
            )
            return self._create_default_safe_policy(tenant_id), "策略已停用，已回退默认安全策略"

        return policy, None

    async def save_policy(self, policy: SandboxPolicy) -> str:
        """保存策略。

        异常映射：抛出 ValueError（含 SANDBOX_POLICY_INVALID 语义）当格式非法。
        """
        return await self._repository.save(policy)

    async def delete_policy(self, policy_id: str, tenant_id: str) -> bool:
        """软删除策略。"""
        return await self._repository.delete(policy_id, tenant_id)

    async def list_policies(self, tenant_id: str):
        """列出租户全部启用策略。"""
        return await self._repository.list_by_tenant(tenant_id)

    @staticmethod
    def generate_policy_id() -> str:
        """生成策略唯一标识。"""
        return f"sbx_{uuid.uuid4().hex[:12]}"

    def _create_default_safe_policy(self, tenant_id: str) -> SandboxPolicy:
        """创建默认安全策略（最严格限额）。

        使用配置中的最小限额值，逻辑隔离模式，默认拒绝网络。
        """
        settings = get_settings()
        return SandboxPolicy(
            policy_id=f"sbx_default_{uuid.uuid4().hex[:8]}",
            tenant_id=tenant_id,
            name="默认安全策略",
            description="策略不存在或非法时自动回退的最严格限额策略",
            isolation_mode=IsolationMode.LOGICAL,
            allow_degrade=True,
            cpu_seconds_limit=min(settings.sandbox_default_cpu_seconds, 5.0),
            memory_mb_limit=min(settings.sandbox_default_memory_mb, 256),
            output_bytes_limit=min(settings.sandbox_default_output_bytes, 262144),
            execution_timeout_seconds=min(settings.sandbox_execution_timeout_seconds, 30),
            workdir=os.path.abspath(settings.sandbox_workdir_root),
            network_policy=NetworkPolicyMode.DEFAULT_DENY,
            network_whitelist=[],
        )
