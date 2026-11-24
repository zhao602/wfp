# =============================================================================
# 沙箱策略仓储本地实现（integrations/sandbox/policy_repository_local.py）
#
# 职责：进程内沙箱策略存储实现，用于单实例部署与测试。
# 设计意图（对应偏差修正 design 2.2.2.1）：
#   - 按 (tenant_id, policy_id) 索引存储
#   - 策略格式校验
#   - 软删除（is_active=False）
#   - 本地文件落盘 ./data/sandbox/policies/
# =============================================================================

import logging
from pathlib import Path
from typing import Dict, List, Optional

from orchestration_platform.integrations.sandbox.models import SandboxPolicy

_logger = logging.getLogger(__name__)


class LocalSandboxPolicyRepository:
    """本地内存+文件沙箱策略仓储。

    按 (tenant_id, policy_id) 索引存储，进程内有效，同时落盘至本地文件。
    """

    def __init__(self, storage_root: str = "./.data/sandbox/policies") -> None:
        """初始化仓储。

        参数：
            storage_root：策略文件存储根目录
        """
        self._store: Dict[str, Dict[str, SandboxPolicy]] = {}
        self._storage_root = Path(storage_root)
        self._storage_root.mkdir(parents=True, exist_ok=True)

    async def save(self, policy: SandboxPolicy) -> str:
        """保存策略。

        异常映射：抛出 ValueError（含 SANDBOX_POLICY_INVALID 语义）当格式非法。
        """
        self._validate_policy(policy)

        tenant_store = self._store.setdefault(policy.tenant_id, {})
        tenant_store[policy.policy_id] = policy
        self._persist(policy)
        _logger.info(
            "沙箱策略已保存: tenant=%s, policy=%s",
            policy.tenant_id, policy.policy_id,
        )
        return policy.policy_id

    async def get(
        self,
        policy_id: str,
        tenant_id: str,
    ) -> Optional[SandboxPolicy]:
        """按标识查询策略。"""
        tenant_store = self._store.get(tenant_id, {})
        return tenant_store.get(policy_id)

    async def list_by_tenant(
        self,
        tenant_id: str,
    ) -> List[SandboxPolicy]:
        """列出租户全部启用策略。"""
        tenant_store = self._store.get(tenant_id, {})
        active_policies = [p for p in tenant_store.values() if p.is_active]
        return active_policies

    async def delete(
        self,
        policy_id: str,
        tenant_id: str,
    ) -> bool:
        """软删除策略（is_active=False）。"""
        tenant_store = self._store.get(tenant_id, {})
        if policy_id not in tenant_store:
            return False
        policy = tenant_store[policy_id]
        tenant_store[policy_id] = policy.model_copy(update={"is_active": False})
        self._persist(tenant_store[policy_id])
        _logger.info(
            "沙箱策略已软删除: tenant=%s, policy=%s",
            tenant_id, policy_id,
        )
        return True

    def _persist(self, policy: SandboxPolicy) -> None:
        """将策略落盘至本地文件。"""
        tenant_dir = self._storage_root / policy.tenant_id
        tenant_dir.mkdir(parents=True, exist_ok=True)
        file_path = tenant_dir / f"{policy.policy_id}.json"
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(policy.model_dump_json(indent=2))
        except OSError as exc:
            _logger.warning("沙箱策略落盘失败: %s - %s", file_path, exc)

    def _validate_policy(self, policy: SandboxPolicy) -> None:
        """校验策略格式。

        异常映射：抛出 ValueError（含 SANDBOX_POLICY_INVALID 语义）。
        """
        if not policy.policy_id:
            raise ValueError("SANDBOX_POLICY_INVALID: 策略标识不能为空")
        if not policy.tenant_id:
            raise ValueError("SANDBOX_POLICY_INVALID: 租户标识不能为空")
        if not policy.workdir:
            raise ValueError("SANDBOX_POLICY_INVALID: 工作目录不能为空")
        if policy.cpu_seconds_limit <= 0:
            raise ValueError("SANDBOX_POLICY_INVALID: CPU 时间上限须为正数")
        if policy.memory_mb_limit <= 0:
            raise ValueError("SANDBOX_POLICY_INVALID: 内存上限须为正数")
        if policy.output_bytes_limit <= 0:
            raise ValueError("SANDBOX_POLICY_INVALID: 输出尺寸上限须为正数")
        if policy.execution_timeout_seconds <= 0:
            raise ValueError("SANDBOX_POLICY_INVALID: 执行超时须为正整数")
