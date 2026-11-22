# =============================================================================
# RBAC 鉴权引擎（governance/rbac/permission_checker.py）
#
# 职责：校验访问令牌与操作权限。
# 设计意图（对应 spec 5.1、design 2.4.1、任务 T51；
#          arch_review_v2_tasks G2/G3，design DC-03/DC-06，CHG-05/06）：
#   1. authenticate 令牌校验三态（token_verify_mode，默认 both）：
#      both —— JWT 优先（三段式 + HS256 验签 + exp/aud/iss），失败回退既有明文
#              tenant:user:exp 解析（存量令牌零失效，GK-02）；
#      jwt  —— 仅 JWT（回退关闭，明文令牌拒 AUTH_TOKEN_FORMAT_UNSUPPORTED）；
#      legacy —— 仅明文（既有解析路径原样保留）；
#   2. authorize 缓存未命中时经 role_repository 查库（含组织继承）→ 写缓存；
#      仓储缺失或查询失败 → 记录告警并写空权限（fail-closed 语义不变）；
#   3. fail-closed：异常时拒绝而非放行。
# =============================================================================

import logging
import time
from typing import List, Optional

from orchestration_platform.configuration.settings import Settings, get_settings
from orchestration_platform.domain.errors import (
    AUTH_TOKEN_EXPIRED,
    AUTH_TOKEN_FORMAT_UNSUPPORTED,
    AUTH_TOKEN_INVALID,
)
from orchestration_platform.domain.models.enums import (
    OperationType,
    ResourceScope,
    ResourceType,
)
from orchestration_platform.governance.auth.jwt_tokens import (
    TokenExpiredError,
    TokenInvalidError,
    decode_token,
)
from orchestration_platform.governance.rbac.models import PermissionItem
from orchestration_platform.governance.rbac.permission_cache import PermissionCache
from orchestration_platform.governance.rbac.permission_registry import PermissionRegistry
from orchestration_platform.governance.interceptors.base import (
    GovernanceDecision,
    GovernanceRequest,
)
from orchestration_platform.governance.persistence.abstractions.role_repository import (
    IRoleRepository,
)

logger = logging.getLogger(__name__)


def _is_jwt_shaped(token: str) -> bool:
    """JWT 形态判定：三段式（两个 "." 分隔）。明文令牌 tenant:user:exp 无 "."。"""
    return token.count(".") == 2


class RbacPermissionChecker:
    """RBAC 鉴权引擎。

    通过令牌认证与权限校验，决定请求是否放行。
    """

    def __init__(
        self,
        registry: PermissionRegistry,
        cache: PermissionCache,
        role_repository: Optional[IRoleRepository] = None,
    ) -> None:
        """初始化鉴权引擎。

        Args:
            registry: 权限项注册表。
            cache: 权限缓存。
            role_repository: 角色仓储（CHG-06 追加可选参数，既有两参调用零破坏）；
                提供时 authorize 缓存未命中经 list_permissions_for_user 查库
                （含组织继承）；缺失时保持既有写空行为（fail-closed）。
        """
        self._registry = registry
        self._cache = cache
        self._role_repository = role_repository

    async def authenticate(
        self,
        request: GovernanceRequest,
    ) -> GovernanceDecision:
        """认证：从令牌解析用户与租户标识。

        令牌校验三态（token_verify_mode，DC-03）：
        - both（默认）：JWT 形态先按 JWT 校验，失败回退既有明文
          "tenant_id:user_id:expires_timestamp" 解析；非 JWT 形态直接走明文解析；
        - jwt：仅 JWT；明文令牌拒绝（AUTH_TOKEN_FORMAT_UNSUPPORTED）；
        - legacy：仅明文解析（既有路径原样保留）。
        方法签名不变（HC-01），仅内部扩展。
        """
        if not request.token:
            return GovernanceDecision(allowed=False, deny_reason="令牌缺失")

        settings = get_settings()
        mode = settings.token_verify_mode
        token: str = request.token

        if _is_jwt_shaped(token):
            if mode in ("jwt", "both"):
                decision = self._authenticate_jwt(request, settings)
                if decision.allowed:
                    return decision
                if mode == "jwt":
                    # jwt 模式：回退关闭，直接拒绝
                    return decision
                # both 模式：JWT 校验失败，回退既有明文解析路径
                logger.debug(
                    "JWT 校验失败（both 模式回退明文解析）: %s",
                    decision.deny_reason,
                )
            # legacy 模式（或 both 回退）：既有明文解析路径（原样保留）
            return await self._authenticate_legacy(request)

        # 非 JWT 形态（明文令牌）
        if mode == "jwt":
            return GovernanceDecision(
                allowed=False,
                deny_reason=(
                    f"{AUTH_TOKEN_FORMAT_UNSUPPORTED}: "
                    "当前校验模式仅接受 JWT 令牌（token_verify_mode=jwt）"
                ),
            )
        return await self._authenticate_legacy(request)

    async def _authenticate_legacy(
        self,
        request: GovernanceRequest,
    ) -> GovernanceDecision:
        """既有明文令牌解析路径（tenant_id:user_id:expires_timestamp）。

        原实现字节级保留（arch_review_v2_tasks 2.5：:54-67 不动）。
        生产环境应使用 JWT。
        """
        try:
            parts = request.token.split(":")
            if len(parts) < 3:
                return GovernanceDecision(allowed=False, deny_reason="令牌格式无效")

            tenant_id = parts[0]
            user_id = parts[1]
            expires_at = int(parts[2])

            if time.time() > expires_at:
                return GovernanceDecision(allowed=False, deny_reason="令牌已过期")

            request.tenant_id = tenant_id
            request.user_id = user_id
            return GovernanceDecision(
                allowed=True,
                tenant_context=tenant_id,
            )
        except Exception as exc:
            return GovernanceDecision(
                allowed=False,
                deny_reason=f"令牌解析失败: {type(exc).__name__}",
            )

    @staticmethod
    def _authenticate_jwt(
        request: GovernanceRequest,
        settings: Settings,
    ) -> GovernanceDecision:
        """JWT 校验前置步：三段式验签 + exp（含偏移容忍）+ aud/iss + claims 必填。

        成功注入 GovernanceRequest.tenant_id/user_id；失败返回携带 AUTH_* 错误码
        前缀的拒绝决策（deny_reason 通道，HC-01，不新开 HTTP 异常类）。
        """
        try:
            claims = decode_token(
                request.token or "",
                settings.token_signing_secret,
                audience=settings.token_audience,
                issuer=settings.token_issuer,
                clock_skew_seconds=settings.token_clock_skew_seconds,
            )
        except TokenExpiredError as exc:
            return GovernanceDecision(
                allowed=False,
                deny_reason=f"{AUTH_TOKEN_EXPIRED}: {exc}",
            )
        except TokenInvalidError as exc:
            return GovernanceDecision(
                allowed=False,
                deny_reason=f"{AUTH_TOKEN_INVALID}: {exc}",
            )
        except Exception as exc:
            # fail-closed：未预期异常同样拒绝（不吞异常放行）
            return GovernanceDecision(
                allowed=False,
                deny_reason=f"{AUTH_TOKEN_INVALID}: 令牌解析失败: {type(exc).__name__}",
            )

        request.tenant_id = claims.tenant_id
        request.user_id = claims.user_id
        return GovernanceDecision(
            allowed=True,
            tenant_context=claims.tenant_id,
        )

    async def authorize(
        self,
        request: GovernanceRequest,
    ) -> GovernanceDecision:
        """鉴权：校验用户对目标资源的操作权限。"""
        if not request.tenant_id or not request.user_id:
            return GovernanceDecision(allowed=False, deny_reason="未认证")

        # 从缓存加载用户权限
        permissions = self._cache.get_permissions(
            request.tenant_id, request.user_id
        )
        if permissions is None:
            # 缓存未命中：经角色仓储查库（含组织继承），仓储缺失/查询失败写空（fail-closed）
            permissions = await self._load_permissions_from_repository(
                request.tenant_id, request.user_id
            )
            self._cache.set_permissions(
                request.tenant_id, request.user_id, permissions
            )

        # 匹配权限项
        for perm in permissions:
            if (
                perm.resource_type == request.resource_type
                and perm.operation == request.operation
            ):
                # 按资源范围生成过滤条件
                scope_filter = self._build_scope_filter(perm.scope, request)
                return GovernanceDecision(
                    allowed=True,
                    resource_scope_filter=scope_filter,
                )

        return GovernanceDecision(
            allowed=False,
            deny_reason=f"权限不足: 需要 {request.resource_type.value}:{request.operation.value}",
        )

    async def _load_permissions_from_repository(
        self,
        tenant_id: str,
        user_id: str,
    ) -> List[PermissionItem]:
        """缓存未命中时经角色仓储加载用户有效权限（CHG-06，DC-06 规则 2）。

        - 仓储存在 → list_permissions_for_user（含组织节点继承与角色继承）；
        - 仓储缺失或查询失败 → 记录告警并返回空权限（既有写空行为，fail-closed）。
        """
        if self._role_repository is None:
            logger.warning(
                "角色仓储未注入：用户 %s/%s 权限缓存未命中，写入空权限（fail-closed）",
                tenant_id,
                user_id,
            )
            return []
        try:
            permissions = await self._role_repository.list_permissions_for_user(
                tenant_id, user_id
            )
            return list(permissions)
        except Exception as exc:
            logger.warning(
                "角色仓储查询失败：用户 %s/%s 写入空权限（fail-closed）: %s",
                tenant_id,
                user_id,
                type(exc).__name__,
            )
            return []

    @staticmethod
    def _build_scope_filter(
        scope: ResourceScope,
        request: GovernanceRequest,
    ) -> dict:
        """按资源范围生成过滤条件。"""
        if scope == ResourceScope.TENANT:
            return {"tenant_id": request.tenant_id}
        if scope == ResourceScope.SELF:
            return {"tenant_id": request.tenant_id, "created_by": request.user_id}
        return {"tenant_id": request.tenant_id}
