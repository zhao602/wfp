# =============================================================================
# Webhook 签名（governance/event_export/signature.py）
#
# 职责：Webhook 投递的 HMAC-SHA256 签名与验签（roadmap B3）。
# 设计意图（线级契约冻结，guide 为准）：
#   1. canonical body = json.dumps(event.model_dump(mode="json"),
#      sort_keys=True, separators=(",", ":"))——确定性序列化，签名可复算；
#   2. 签名头 X-Orch-Signature: t=<unix_ts>,v1=<hex(hmac_sha256(secret,
#      f"{t}.{body}"))>——GitHub 风格时间戳前缀，防重放由接收方容差判断；
#   3. verify_webhook 任意失败（格式/容差/签名不匹配）均返回 False，不抛异常。
# =============================================================================

import hashlib
import hmac
import json
import time
from typing import Optional

from orchestration_platform.domain.models.domain_event import DomainEvent

# 投递请求头（线级契约）。
SIGNATURE_HEADER = "X-Orch-Signature"
EVENT_TYPE_HEADER = "X-Orch-Event-Type"
INSTANCE_ID_HEADER = "X-Orch-Instance-Id"

# 接收方验签时间戳容差建议值（防重放；guide 文档化）。
DEFAULT_TIMESTAMP_TOLERANCE_SECONDS = 300.0


def canonical_event_body(event: DomainEvent) -> str:
    """领域事件 canonical body：确定性 JSON 序列化（线级契约冻结口径）。"""
    return json.dumps(
        event.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )


def sign_webhook(secret: str, timestamp: int, body: str) -> str:
    """生成签名头值：t=<unix_ts>,v1=<hex(hmac_sha256(secret, f"{t}.{body}"))>。"""
    signed_payload = f"{timestamp}.{body}"
    digest = hmac.new(
        secret.encode("utf-8"),
        signed_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"t={timestamp},v1={digest}"


def verify_webhook(
    secret: str,
    header: str,
    body: str,
    tolerance_seconds: float = DEFAULT_TIMESTAMP_TOLERANCE_SECONDS,
    *,
    now: Optional[float] = None,
) -> bool:
    """验签：格式解析 → 时间戳容差 → 常量时间摘要比对；任意失败返回 False。

    now 可注入当前时间（测试用）；缺省 time.time()。
    """
    try:
        segments = header.split(",")
        if len(segments) != 2:
            return False
        t_segment, v1_segment = segments[0].strip(), segments[1].strip()
        if not t_segment.startswith("t=") or not v1_segment.startswith("v1="):
            return False
        timestamp = int(t_segment[2:])
        received = v1_segment[3:]
    except (AttributeError, ValueError):
        return False
    current = time.time() if now is None else now
    if abs(current - timestamp) > tolerance_seconds:
        return False
    signed_payload = f"{timestamp}.{body}"
    expected = hmac.new(
        secret.encode("utf-8"),
        signed_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, received)
