# =============================================================================
# 审计字段级内容哈希器（governance/audit/content_hasher.py）
#
# 职责：为审计条目的请求载荷与响应摘要计算字段级内容哈希（G5/T207）。
# 设计意图（对应 spec 5.5、design D7）：
#   1. 与既有 entry_hash 同族算法（默认 SHA-256，读 settings.audit_hash_algorithm）；
#   2. canonical 化：键排序 JSON 序列化，保证"同内容必同哈希"；
#   3. 无状态纯函数组件：写入侧（AuditLogger）与校验侧（HashChainVerifier）
#      复用同一实现，保证口径全链路一致；
#   4. 空载荷口径统一：None / 空 dict / 空串均按空串哈希。
#
# 兼容性红线：
#   - 本哈希器产出的 input_hash/output_hash 不参与既有 _serialize_entry
#     哈希链计算，entry_hash 链值与修正前逐字节一致。
# =============================================================================

import hashlib
import json
from typing import Any, Dict, Optional

from orchestration_platform.configuration.settings import get_settings


class ContentHasher:
    """审计条目字段级内容哈希器。

    对审计条目的 request_payload（dict）与 response_summary（str）分别计算
    内容哈希，用于字段级篡改定位（区别于既有条目级哈希链）。

    前置条件：无（无状态组件，可在任意上下文实例化）。
    后置条件：同内容必得同哈希；空载荷统一按空串口径。
    """

    def __init__(self, algorithm: Optional[str] = None) -> None:
        """初始化内容哈希器。

        Args:
            algorithm: 哈希算法名（如 "sha256"）；缺省读取全局配置
                settings.audit_hash_algorithm，与 entry_hash 保持同族。
        """
        self._algorithm = algorithm or get_settings().audit_hash_algorithm

    @property
    def algorithm(self) -> str:
        """当前使用的哈希算法名。"""
        return self._algorithm

    def _hash_text(self, text: str) -> str:
        """对规范化后的文本计算哈希。

        Args:
            text: 已 canonical 化的文本内容。

        Returns:
            十六进制哈希摘要。
        """
        hasher = hashlib.new(self._algorithm)
        hasher.update(text.encode("utf-8"))
        return hasher.hexdigest()

    def _canonicalize_payload(self, payload: Optional[Dict[str, Any]]) -> str:
        """将请求载荷 canonical 化为稳定字符串。

        规则：JSON 序列化时按键名排序（sort_keys=True），并固定分隔符
        与 ensure_ascii，保证跨进程/跨时间同内容同串。

        空载荷口径：None / 空 dict 统一映射为空串。
        """
        if not payload:
            return ""
        return json.dumps(
            payload,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def compute_input_hash(self, request_payload: Optional[Dict[str, Any]]) -> str:
        """计算请求载荷的字段级内容哈希。

        Args:
            request_payload: 审计条目的请求载荷（已脱敏）。

        Returns:
            内容哈希（与 entry_hash 同族算法）；空载荷按空串哈希。
        """
        return self._hash_text(self._canonicalize_payload(request_payload))

    def compute_output_hash(self, response_summary: Optional[str]) -> str:
        """计算响应摘要的字段级内容哈希。

        Args:
            response_summary: 审计条目的响应摘要（已脱敏）。

        Returns:
            内容哈希；None/空串统一按空串哈希（口径与 input 侧一致）。
        """
        # 空载荷口径统一：None 与空串等价，均按空串哈希
        text = response_summary or ""
        return self._hash_text(text)
