# =============================================================================
# 数据源连接探测支撑模块（integrations/data_source_connectors/probe_support.py）
#
# 职责：为 8 类数据源连接器的 probe() 提供共享的探测支撑能力——
#       超时常量、三类失败分类构造器、异常类型名脱敏映射、驱动导入守卫。
# 设计意图：
#   1. 单一出处：三类失败（参数错误 / 连接失败 / 无法探测）的构造集中于此
#      （CHG-2 / DC-2 / DC-11），保证诊断文本前缀一致；
#   2. 凭据脱敏：连接异常→可读短语映射仅使用 type(exc).__name__，
#      严禁 str(exc)，防止连接串/AccessKey 随诊断信息外泄（INV-4 / R-4）；
#   3. 不假阳性：无驱动环境抛"无法探测"而非默认放行（INV-8 / AC-7 / R-1）。
# 约束：模块顶层仅使用标准库与 domain/errors.py，不得在顶层导入
#       asyncpg/httpx/aiokafka/psycopg2 等重依赖（延迟导入，SV-2）。
# =============================================================================

from typing import Optional

from orchestration_platform.domain.errors import (
    DATASOURCE_CONNECTION_FAILED,
    DATASOURCE_PROBE_PARAM_INVALID,
    DATASOURCE_PROBE_UNAVAILABLE,
    DomainError,
)

# 探测超时（秒）：统一 10 秒，与前端 ConnectionTestPanel 一致（Q-5 / DC-9）
PROBE_TIMEOUT_SECONDS = 10.0

# 后端实际超时余量（秒）：略大于前端超时，避免前端先取消（R-5）
PROBE_TIMEOUT_MARGIN_SECONDS = 0.5

# 三类失败诊断文本前缀（唯一出处，前端按此前缀差异化展示，Q-4=A / CHG-15）
PARAM_ERROR_PREFIX = "参数错误："
CONNECTION_ERROR_PREFIX = "连接失败："
UNAVAILABLE_ERROR_PREFIX = "无法探测："

# 异常类型名 → 中文短语白名单映射（仅用 type(exc).__name__，禁止 str(exc)）
_EXCEPTION_PHRASE_MAP = {
    "ConnectionRefusedError": "连接被拒绝",
    "ConnectionResetError": "连接被重置",
    "ConnectionAbortedError": "连接被中断",
    "TimeoutError": "连接超时",
    "ConnectTimeout": "连接超时",
    "ReadTimeout": "读取超时",
    "ConnectError": "连接失败",
    "gaierror": "无法解析主机名",
    "InvalidURL": "URL 格式无效",
    "InvalidCredentialsError": "认证失败",
    "AuthenticationError": "认证失败",
    "NoBrokersAvailable": "Kafka broker 不可达",
    "KafkaConnectionError": "Kafka 连接失败",
    "OSError": "网络或系统错误",
}


def build_param_error(reason: str) -> DomainError:
    """构造参数错误（连接参数缺失或格式非法）。"""
    return DomainError(
        DATASOURCE_PROBE_PARAM_INVALID,
        f"{PARAM_ERROR_PREFIX}{reason}",
    )


def build_connection_error(exc: BaseException, reason: Optional[str] = None) -> DomainError:
    """构造连接失败（真实探测过程失败）。

    凭据脱敏：仅使用 type(exc).__name__ 映射为中文短语，
    严禁将 str(exc) 纳入诊断（防连接串/AccessKey 外泄，INV-4）。
    """
    if reason is not None:
        return DomainError(DATASOURCE_CONNECTION_FAILED, f"{CONNECTION_ERROR_PREFIX}{reason}")
    phrase = _EXCEPTION_PHRASE_MAP.get(type(exc).__name__, "连接失败")
    return DomainError(DATASOURCE_CONNECTION_FAILED, f"{CONNECTION_ERROR_PREFIX}{phrase}")


def build_unavailable_error(driver: str) -> DomainError:
    """构造无法探测（驱动或网络环境不可用）。

    不得默认放行返回可达（INV-8 / AC-7）。
    """
    return DomainError(
        DATASOURCE_PROBE_UNAVAILABLE,
        f"{UNAVAILABLE_ERROR_PREFIX}驱动/网络不可用（{driver}）",
    )


def require_driver(module_name: str):
    """延迟导入并返回目标驱动模块；导入失败抛"无法探测"（不假阳性）。

    Args:
        module_name: 目标驱动模块名（如 "asyncpg" / "httpx" / "aiokafka"）。

    Returns:
        导入成功的驱动模块对象。

    Raises:
        DomainError: code == DATASOURCE_PROBE_UNAVAILABLE。
    """
    try:
        import importlib

        return importlib.import_module(module_name)
    except ImportError:
        raise build_unavailable_error(module_name)