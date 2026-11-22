# =============================================================================
# 大模型采样参数取值域唯一权威（domain/values/llm_sampling_spec.py）
#
# 职责：登记采样六参数的取值域与语义缺省，并提供三个纯函数
#       （llm_and_code_nodes CHG-03 / 任务 2.2）：
#         validate_sampling  —— 越界检测，返回可读违规清单（不静默截断）；
#         resolve_sampling   —— 语义缺省解析（未配置 → 登记表缺省）；
#         to_request_payload —— 请求体采样键组装（DC-05 最小键集）。
# 设计意图（spec §6.3 / design §2.3.2 / INV-12 / DC-04 / DC-05 / D-01）：
#   1. 单一权威：本表是后端校验、前端提示镜像、规格文档三处一致的唯一来源
#      （INV-12）；任何分支不得另立区间常量或就地复制缺省值；
#   2. 纯函数：只依赖入参与本表，不读 settings / 环境变量 / 时钟 / 网络，
#      供加载期校验器与请求体组装两处无副作用复用；
#   3. 越界不静默截断（DC-04）：违规消息形态「参数名 + 实际值 + 允许区间」，
#      如「温度 2.5 超出 [0.0, 2.0]」；多违规逐项全部返回；
#   4. 最小键集（DC-05）：temperature / max_tokens 恒发（未配置取语义缺省
#      0.7 / 4096）；top_p / top_k / stop / seed 仅显式设置时发；
#      top_k=0 不发；stop 去重保序后非空才发；seed 未设置不发送。
# =============================================================================

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from orchestration_platform.domain.models.node_configurations import LlmSamplingParameters

# 停止序列结构约束（spec §6.3：至多 4 条，每条 1..128 字符，去重）
STOP_MAX_ITEMS = 4
STOP_ITEM_MIN_CHARS = 1
STOP_ITEM_MAX_CHARS = 128

# 违规消息中的参数名（中文标签，与前端提示文案同源，INV-12）
_PARAM_LABELS: Dict[str, str] = {
    "temperature": "温度",
    "top_p": "Top P",
    "top_k": "Top K",
    "max_tokens": "最大生成长度",
    "stop": "停止序列",
    "seed": "随机种子",
}


@dataclass(frozen=True)
class SamplingParamSpec:
    """采样参数登记项（取值域 / 语义缺省 / 发送策略 / 提示描述）。"""

    name: str
    kind: str                    # "float" | "int" | "str_list"
    minimum: Optional[float]     # 数值型下界（str_list 为 None）
    maximum: Optional[float]     # 数值型上界（str_list 为 None）
    exclusive_minimum: bool      # True 时下界为开区间（如 top_p ∈ (0.0, 1.0]）
    default: Any                 # 语义缺省（seed 为 None=未设置）
    step: Optional[float]        # 前端步进器步长（None=无步进语义）
    request_always: bool         # True=请求体恒发该键（DC-05）
    description: str             # 短描述（提示文案同源，INV-12）


SAMPLING_PARAM_SPECS: Dict[str, SamplingParamSpec] = {
    "temperature": SamplingParamSpec(
        name="temperature",
        kind="float",
        minimum=0.0,
        maximum=2.0,
        exclusive_minimum=False,
        default=0.7,
        step=0.1,
        request_always=True,
        description="温度：控制采样随机性，取值 [0.0, 2.0]，缺省 0.7",
    ),
    "top_p": SamplingParamSpec(
        name="top_p",
        kind="float",
        minimum=0.0,
        maximum=1.0,
        exclusive_minimum=True,
        default=1.0,
        step=0.05,
        request_always=False,
        description="Top P：核采样保留概率质量，取值 (0.0, 1.0]，缺省 1.0（不裁剪）",
    ),
    "top_k": SamplingParamSpec(
        name="top_k",
        kind="int",
        minimum=0,
        maximum=100,
        exclusive_minimum=False,
        default=0,
        step=None,
        request_always=False,
        description="Top K：按概率保留前 K 个候选，取值 0 或 [1, 100]，0=不启用",
    ),
    "max_tokens": SamplingParamSpec(
        name="max_tokens",
        kind="int",
        minimum=1,
        maximum=32768,
        exclusive_minimum=False,
        default=4096,
        step=None,
        request_always=True,
        description="最大生成长度：取值 [1, 32768]，缺省 4096",
    ),
    "stop": SamplingParamSpec(
        name="stop",
        kind="str_list",
        minimum=None,
        maximum=None,
        exclusive_minimum=False,
        default=[],
        step=None,
        request_always=False,
        description="停止序列：至多 4 条、每条 1..128 字符，去重保序",
    ),
    "seed": SamplingParamSpec(
        name="seed",
        kind="int",
        minimum=0,
        maximum=2147483647,
        exclusive_minimum=False,
        default=None,
        step=None,
        request_always=False,
        description="随机种子：取值 [0, 2147483647]，未设置则不发送",
    ),
}


def _format_interval(spec: SamplingParamSpec) -> str:
    """格式化允许区间（闭/半开区间，与违规消息配套）。"""
    left = "(" if spec.exclusive_minimum else "["
    return f"{left}{spec.minimum}, {spec.maximum}]"


def _resolve_or_default(name: str, value: Any) -> Any:
    """未配置（None）时取登记表语义缺省（D-01）。"""
    return value if value is not None else SAMPLING_PARAM_SPECS[name].default


def validate_sampling(params: LlmSamplingParameters) -> List[str]:
    """越界检测（纯函数）：返回违规消息清单，空清单=全部合规。

    消息形态「参数名 + 实际值 + 允许区间」（DC-04 不静默截断），
    如「温度 2.5 超出 [0.0, 2.0]」；未配置字段（None）不参与检测；
    检测顺序与登记表一致，多违规逐项全部返回。
    """
    violations: List[str] = []

    def _check_numeric(name: str, value: Optional[float]) -> None:
        if value is None:
            return
        spec = SAMPLING_PARAM_SPECS[name]
        below = value <= spec.minimum if spec.exclusive_minimum else value < spec.minimum
        if below or value > spec.maximum:
            violations.append(f"{_PARAM_LABELS[name]} {value} 超出 {_format_interval(spec)}")

    _check_numeric("temperature", params.temperature)
    _check_numeric("top_p", params.top_p)
    _check_numeric("top_k", params.top_k)
    _check_numeric("max_tokens", params.max_tokens)
    _check_numeric("seed", params.seed)

    if params.stop is not None:
        stop_label = _PARAM_LABELS["stop"]
        if len(params.stop) > STOP_MAX_ITEMS:
            violations.append(f"{stop_label}条数 {len(params.stop)} 超出 [0, {STOP_MAX_ITEMS}]")
        for index, item in enumerate(params.stop):
            if not STOP_ITEM_MIN_CHARS <= len(item) <= STOP_ITEM_MAX_CHARS:
                violations.append(
                    f"{stop_label}第 {index + 1} 条长度 {len(item)} 超出 "
                    f"[{STOP_ITEM_MIN_CHARS}, {STOP_ITEM_MAX_CHARS}]"
                )
    return violations


def resolve_sampling(params: LlmSamplingParameters) -> Dict[str, Any]:
    """语义缺省解析（纯函数 / D-01）：未配置字段取登记表语义缺省。

    不做越界检测（越界检测唯一入口 validate_sampling）；不修改入参。
    seed 未配置保持 None（「未设置」语义，发送侧不发该键）；
    stop 未配置为 []，显式配置时去重保序。
    """
    return {
        "temperature": _resolve_or_default("temperature", params.temperature),
        "top_p": _resolve_or_default("top_p", params.top_p),
        "top_k": _resolve_or_default("top_k", params.top_k),
        "max_tokens": _resolve_or_default("max_tokens", params.max_tokens),
        "stop": list(dict.fromkeys(params.stop)) if params.stop is not None else [],
        "seed": params.seed,
    }


def to_request_payload(params: LlmSamplingParameters) -> Dict[str, Any]:
    """请求体采样键组装（纯函数 / DC-05 最小键集）。

    先整体校验：任一违规抛 ValueError（全部违规项以「；」连接），
    不静默截断（DC-04）；合规后按键集规则组装：
      - temperature / max_tokens 恒发（未配置取语义缺省 0.7 / 4096）；
      - top_p / seed 仅显式设置时发；
      - top_k 仅显式设置且 > 0 时发（0=不启用）；
      - stop 去重保序后非空才发。
    """
    violations = validate_sampling(params)
    if violations:
        raise ValueError("；".join(violations))
    resolved = resolve_sampling(params)
    payload: Dict[str, Any] = {
        "temperature": resolved["temperature"],
        "max_tokens": resolved["max_tokens"],
    }
    if params.top_p is not None:
        payload["top_p"] = resolved["top_p"]
    if params.top_k is not None and params.top_k > 0:
        payload["top_k"] = resolved["top_k"]
    if resolved["stop"]:
        payload["stop"] = resolved["stop"]
    if params.seed is not None:
        payload["seed"] = resolved["seed"]
    return payload
