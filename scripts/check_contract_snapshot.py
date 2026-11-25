# -*- coding: utf-8 -*-
# =============================================================================
# B14 T2 契约 snapshot 门禁（scripts/check_contract_snapshot.py）
#
# 职责：冻结后端 OpenAPI 契约面与三面提取断言，防止后端路由 / Python SDK /
# 前端客户端任何一侧的静默漂移：
#   1. openapi_v1.json          —— create_app() 全量 OpenAPI 文档金档（字节级比对）
#   2. contract_surfaces.json   —— 三面提取面金档：
#        - sdk_endpoints          sdk/wfp_sdk/client.py 的 (method, path) 全集
#        - web_client_endpoints   web/src/api/orchestrationApiClient.ts 端点全集
#        - api_types_declared_exports / _reexported_names
#                                  web/src/api/apiTypes.ts 导出类型面
#
# 用法：
#   python scripts/check_contract_snapshot.py            # 校验（默认，退出码 0/1）
#   python scripts/check_contract_snapshot.py --regen    # 评审后更新金档
#
# 钉扎环境（_PINNED_ENV）：21 个路由门控旗标全 ON（最大契约面）+
# conftest 等价基线（RBAC/TENANT 关闭、LLM 占位）。环境变量优先级高于 .env，
# 与 pydantic-settings 语义一致；校验与生成走同一钉扎路径，保证可复现。
#
# 破坏性契约变更流程：先评审（docs/sdk-contract.md §1），再 --regen 更新金档
# 并在提交说明中标注破坏性影响；TS 侧对拍待 F6 解冻，不在本脚本范围。
# =============================================================================
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

GOLDEN_DIR = REPO_ROOT / "tests" / "fixtures" / "contracts"
GOLDEN_OPENAPI = GOLDEN_DIR / "openapi_v1.json"
GOLDEN_SURFACES = GOLDEN_DIR / "contract_surfaces.json"

API_PREFIX = "/api/v1"
HTTP_METHODS = ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS")

# 钉扎环境（详见模块头注释）：
#   - 21 路由门控旗标对应 application_factory.py 路由注册块的全部条件路由
#     （event_export 管理路由嵌套于 event_export_enabled 装配块内，双开关均需 ON）；
#   - rbac_bootstrap / metering / schedule_scanner 等为装配级开关，不注册路由，不钉扎。
_PINNED_ENV: Dict[str, str] = {
    # ---- 21 路由门控旗标（全 ON）----
    "NL_ORCHESTRATION_ENABLED": "true",
    "ABAC_ENABLED": "true",
    "TOMBSTONE_ENABLED": "true",
    "SANDBOX_ENABLED": "true",
    "PLUGIN_SDK_ENABLED": "true",
    "NODE_CONTRIBUTION_ENABLED": "true",
    "SSO_ENABLED": "true",
    "ENTERPRISE_CONNECTOR_ENABLED": "true",
    "EVALUATION_ENABLED": "true",
    "AGENT_DISCOVERY_ENABLED": "true",
    "TRIGGER_TEMPLATE_ENABLED": "true",
    "AGENT_PERFORMANCE_ENABLED": "true",
    "AGENT_MARKET_ENABLED": "true",
    "AGENT_MARKET_DEV_ENABLED": "true",
    "TOOL_MARKET_ENABLED": "true",
    "VECTOR_KB_MANAGEMENT_ENABLED": "true",
    "VECTOR_KB_ANNOTATION_ENABLED": "true",
    "AUTH_TOKEN_ISSUANCE_ENABLED": "true",
    "EVENT_EXPORT_ENABLED": "true",
    "EVENT_EXPORT_API_ENABLED": "true",
    "TRIGGER_SCHEDULE_API_ENABLED": "true",
    # ---- conftest 等价基线（tests/conftest.py _TEST_ENV_BASELINE 对齐）----
    "RBAC_ENABLED": "false",
    "TENANT_ISOLATION_ENABLED": "false",
    "LLM_PRIMARY_BASE_URL": "http://127.0.0.1:9/v1",
    "LLM_PRIMARY_API_KEY": "contract-snapshot-placeholder-key",
    "LLM_PRIMARY_MODEL": "contract-snapshot-placeholder-model",
    # ---- 存储后端（与本地开发一致；不影响 schema，仅固定装配路径）----
    "STORAGE_BACKEND": "local_file",
}

# ---------------------------------------------------------------------------
# 提取正则
# ---------------------------------------------------------------------------

# SDK：httpx 调用形如 "GET", f"{_API_PREFIX}/instances/{id}"（含跨行书写）
_SDK_CALL_RE = re.compile(
    r'"(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)"\s*,\s*f"\{_API_PREFIX\}([^"]*)"'
)

# web 客户端：对象字面量方法块（缩进 2）→ 请求配置 path/method
_WEB_OBJECT_START_RE = re.compile(r"export const orchestrationApiClient = \{")
_WEB_METHOD_RE = re.compile(r"^  (?:async\s+)?([A-Za-z_]\w*)\s*\(")
_WEB_PATH_RE = re.compile(r"^\s+path:\s*(.+?),?\s*$")
_WEB_VERB_RE = re.compile(r'^\s+method:\s*["\'](GET|POST|PUT|DELETE|PATCH)["\']')
_WEB_BACKTICK_RE = re.compile(r"`([^`]*)`")
_WEB_DQUOTE_RE = re.compile(r'"([^"]*)"')

# apiTypes.ts：具名导出 + export type { ... } 再导出块
_TYPES_DECLARED_RE = re.compile(r"^export\s+(?:interface|type)\s+([A-Za-z_]\w*)", re.M)
_TYPES_REEXPORT_RE = re.compile(r"export\s+type\s*\{([^}]*)\}")


# ---------------------------------------------------------------------------
# 路径归一化：strip 查询串 → ${expr} 与 {param} 统一为 {} → 补 /api/v1 前缀
# ---------------------------------------------------------------------------

def _normalize_path(raw: str, *, add_prefix: bool) -> str:
    path = raw.split("?", 1)[0].strip()
    path = re.sub(r"\$\{[^}]*\}", "{}", path)
    path = re.sub(r"\{[^}]*\}", "{}", path)
    if add_prefix and not path.startswith(API_PREFIX):
        path = API_PREFIX + path
    return path


# ---------------------------------------------------------------------------
# 三面提取
# ---------------------------------------------------------------------------

def extract_sdk_endpoints() -> List[Tuple[str, str]]:
    """提取 SDK 全部 HTTP 调用端点（归一化后去重排序）。"""
    text = (REPO_ROOT / "sdk" / "wfp_sdk" / "client.py").read_text(encoding="utf-8")
    endpoints: Set[Tuple[str, str]] = set()
    for method, raw_path in _SDK_CALL_RE.findall(text):
        endpoints.add((method, _normalize_path(raw_path, add_prefix=True)))
    if not endpoints:
        raise RuntimeError("SDK 端点提取结果为空：sdk/wfp_sdk/client.py 调用形态可能已变更")
    return sorted(endpoints)


def extract_web_client_endpoints() -> Tuple[List[Tuple[str, str]], int]:
    """提取 web 客户端端点全集与含 HTTP 调用的方法数。

    状态机：进入 orchestrationApiClient 对象字面量区域后，按缩进 2 的方法块
    切分；块内取首个 path: 表达式的全部字符串字面量（覆盖三元分支），
    method: 缺省 GET（与 createHttpClient 默认一致）。
    """
    text = (REPO_ROOT / "web" / "src" / "api" / "orchestrationApiClient.ts").read_text(
        encoding="utf-8"
    )
    endpoints: Set[Tuple[str, str]] = set()
    method_count = 0
    in_object = False
    pending_literals: Optional[List[str]] = None
    pending_verb: Optional[str] = None

    def _finalize() -> None:
        nonlocal pending_literals, pending_verb, method_count
        if pending_literals:
            verb = pending_verb or "GET"
            for literal in pending_literals:
                endpoints.add((verb, _normalize_path(literal, add_prefix=True)))
            method_count += 1
        pending_literals = None
        pending_verb = None

    for line in text.splitlines():
        stripped = line.strip()
        if not in_object:
            if _WEB_OBJECT_START_RE.search(line):
                in_object = True
            continue
        if stripped == "};":
            _finalize()
            in_object = False
            continue
        if _WEB_METHOD_RE.match(line):
            _finalize()
            continue
        path_match = _WEB_PATH_RE.match(line)
        if path_match:
            expr = path_match.group(1)
            literals = _WEB_BACKTICK_RE.findall(expr) + _WEB_DQUOTE_RE.findall(expr)
            if literals:
                pending_literals = (pending_literals or []) + literals
            continue
        verb_match = _WEB_VERB_RE.match(line)
        if verb_match and pending_literals:
            pending_verb = verb_match.group(1)
    _finalize()

    if not endpoints:
        raise RuntimeError(
            "web 客户端端点提取结果为空：orchestrationApiClient.ts 形态可能已变更"
        )
    return sorted(endpoints), method_count


def extract_api_types_exports() -> Tuple[List[str], List[str]]:
    """提取 apiTypes.ts 具名导出面与再导出块名单。"""
    text = (REPO_ROOT / "web" / "src" / "api" / "apiTypes.ts").read_text(encoding="utf-8")
    declared = sorted(set(_TYPES_DECLARED_RE.findall(text)))
    reexported: Set[str] = set()
    for body in _TYPES_REEXPORT_RE.findall(text):
        for token in re.split(r"[\s,]+", body):
            if re.fullmatch(r"[A-Za-z_]\w*", token) and token not in ("as", "from", "type"):
                reexported.add(token)
    return declared, sorted(reexported)


# ---------------------------------------------------------------------------
# OpenAPI 文档生成（钉扎环境 + 进程内 create_app）
# ---------------------------------------------------------------------------

@contextmanager
def _pinned_settings_env() -> Iterator[None]:
    """钉扎环境变量并失效 settings 缓存；退出时精确还原并再次失效。

    还原后再次 cache_clear，保证本进程后续 get_settings() 调用方
    重新按环境变量构造，不泄漏钉扎值（pytest 进程内运行时的洁净性）。
    """
    from orchestration_platform.configuration.settings import get_settings

    saved: Dict[str, Optional[str]] = {k: os.environ.get(k) for k in _PINNED_ENV}
    try:
        for key, value in _PINNED_ENV.items():
            os.environ[key] = value
        get_settings.cache_clear()
        yield
    finally:
        for key, old in saved.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
        get_settings.cache_clear()


def build_openapi_document() -> Dict[str, Any]:
    """钉扎环境下进程内构造应用并导出 OpenAPI 文档。

    与子进程方案（uvicorn + /openapi.json）等价，但免端口/免网络且更快；
    create_app 调用期取 settings（application_factory.py :184），钉扎经
    get_settings.cache_clear() 生效（tests/integration/test_event_export_api.py
    夹具范式）。
    """
    from orchestration_platform.api.application_factory import create_app

    app = create_app()
    return app.openapi()


# ---------------------------------------------------------------------------
# 汇总与不变量
# ---------------------------------------------------------------------------

def _serialize_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def _verify_subset_invariants(
    doc: Dict[str, Any],
    sdk_endpoints: List[Tuple[str, str]],
    web_endpoints: List[Tuple[str, str]],
) -> List[str]:
    """SDK / web 端点必须是后端 OpenAPI 路径的子集（防金档自身不自洽）。"""
    problems: List[str] = []
    path_methods: Dict[str, Set[str]] = {}
    for path, item in doc.get("paths", {}).items():
        normalized = _normalize_path(path, add_prefix=False)
        methods = {
            key.upper() for key in item.keys() if key.upper() in HTTP_METHODS
        }
        path_methods.setdefault(normalized, set()).update(methods)
    for method, path in sdk_endpoints:
        if method not in path_methods.get(path, set()):
            problems.append(f"SDK 端点在后端 OpenAPI 中不存在：{method} {path}")
    for method, path in web_endpoints:
        if method not in path_methods.get(path, set()):
            problems.append(f"web 客户端端点在后端 OpenAPI 中不存在：{method} {path}")
    return problems


def collect_current_state() -> Tuple[str, Dict[str, Any], List[str]]:
    """生成当前 openapi 文本与三面提取结果，并完成子集不变量自检。"""
    with _pinned_settings_env():
        doc = build_openapi_document()

    sdk_endpoints = extract_sdk_endpoints()
    web_endpoints, web_method_count = extract_web_client_endpoints()
    declared, reexported = extract_api_types_exports()

    surfaces: Dict[str, Any] = {
        "sdk_endpoints": [[m, p] for m, p in sdk_endpoints],
        "web_client_endpoints": [[m, p] for m, p in web_endpoints],
        "api_types_declared_exports": declared,
        "api_types_reexported_names": reexported,
        "web_client_method_count": web_method_count,
        "sdk_http_call_count": len(sdk_endpoints),
    }
    problems = _verify_subset_invariants(doc, sdk_endpoints, web_endpoints)
    return _serialize_json(doc), surfaces, problems


def _print_diff(golden_text: str, current_text: str, label: str) -> None:
    diff = list(
        difflib.unified_diff(
            golden_text.splitlines(),
            current_text.splitlines(),
            f"golden/{label}",
            f"current/{label}",
            lineterm="",
        )
    )
    print(f"---- {label} 差异（unified diff，截断前 60 行 / 共 {len(diff)} 行）----")
    for line in diff[:60]:
        print(line)


# ---------------------------------------------------------------------------
# 校验 / 生成
# ---------------------------------------------------------------------------

def check() -> int:
    openapi_text, surfaces, problems = collect_current_state()

    if not GOLDEN_OPENAPI.exists() or not GOLDEN_SURFACES.exists():
        print("[FAIL] 契约金档缺失：tests/fixtures/contracts/ 下 openapi_v1.json 或 contract_surfaces.json 不存在")
        print("       请先运行：python scripts/check_contract_snapshot.py --regen")
        return 1

    golden_openapi_text = GOLDEN_OPENAPI.read_text(encoding="utf-8")
    if openapi_text != golden_openapi_text:
        problems.insert(0, "openapi_v1.json 与金档不一致（后端路由/模型契约漂移）")
        _print_diff(golden_openapi_text, openapi_text, "openapi_v1.json")

    golden_surfaces = json.loads(GOLDEN_SURFACES.read_text(encoding="utf-8"))
    surfaces_text = _serialize_json(surfaces)
    golden_surfaces_text = _serialize_json(golden_surfaces)
    if surfaces_text != golden_surfaces_text:
        problems.insert(0, "contract_surfaces.json 与金档不一致（SDK/web/apiTypes 契约面漂移）")
        _print_diff(golden_surfaces_text, surfaces_text, "contract_surfaces.json")

    if problems:
        for problem in problems:
            print(f"[FAIL] {problem}")
        print("契约 snapshot 校验未通过：若为预期内契约变更，请评审后以 --regen 更新金档")
        return 1

    path_count = len(json.loads(openapi_text).get("paths", {}))
    print(
        f"[PASS] 契约 snapshot 校验通过：openapi 路径 {path_count}，"
        f"SDK 端点 {surfaces['sdk_http_call_count']}，"
        f"web 端点 {surfaces['web_client_method_count']} 方法 / "
        f"{len(surfaces['web_client_endpoints'])} 端点，"
        f"apiTypes 具名导出 {len(surfaces['api_types_declared_exports'])} / "
        f"再导出 {len(surfaces['api_types_reexported_names'])}"
    )
    return 0


def regen() -> int:
    openapi_text, surfaces, problems = collect_current_state()
    if problems:
        for problem in problems:
            print(f"[FAIL] {problem}")
        print("金档拒绝生成：提取面与后端 OpenAPI 不自洽，请先修复上列问题")
        return 1

    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    GOLDEN_OPENAPI.write_text(openapi_text, encoding="utf-8", newline="\n")
    GOLDEN_SURFACES.write_text(
        _serialize_json(surfaces), encoding="utf-8", newline="\n"
    )
    path_count = len(json.loads(openapi_text).get("paths", {}))
    print(f"[PASS] 契约金档已生成：openapi 路径 {path_count}，端点面与导出面自洽")
    print(f"       {GOLDEN_OPENAPI.relative_to(REPO_ROOT)}")
    print(f"       {GOLDEN_SURFACES.relative_to(REPO_ROOT)}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="B14 契约 snapshot 门禁：校验或重建 openapi/三面金档"
    )
    parser.add_argument(
        "--regen",
        action="store_true",
        help="重新生成金档（预期内契约变更经评审后使用）",
    )
    args = parser.parse_args(argv)
    return regen() if args.regen else check()


if __name__ == "__main__":
    # GBK 控制台防崩（与 run_local_ci.py 同类问题）：不可编码字符替换而非抛异常
    try:
        sys.stdout.reconfigure(errors="replace")
        sys.stderr.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass
    sys.exit(main())
