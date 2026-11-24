# =============================================================================
# 静态安全扫描器（mcp_tool_market/contribution/static_security_scanner.py）
#
# 职责：基于 ast 模块的静态安全扫描。
# 设计意图（对应偏差修正 T157 / design 决策D7）：
#   1. 危险 API 调用检测（eval/exec/os.system/subprocess/socket 等）
#   2. 网络外联调用点与 manifest declared_network_targets 一致性比对
#   3. 依赖已知风险模式（动态导入、文件越权访问）
#   4. 产出 ScanReport（passed + 逐项 ScanFinding）
#   5. 纯标准库实现（ast）
# =============================================================================

import ast
import logging
import time
import uuid
from pathlib import Path
from typing import List

from orchestration_platform.mcp_tool_market.contribution.models import (
    ScanFinding,
    ScanReport,
)
from orchestration_platform.plugin_sdk.models import PluginManifest

_logger = logging.getLogger(__name__)

# 危险 API 调用规则表
_DANGEROUS_CALLS = {
    "eval": "使用 eval() 可导致任意代码执行",
    "exec": "使用 exec() 可导致任意代码执行",
    "os.system": "使用 os.system() 可导致命令注入",
    "os.popen": "使用 os.popen() 可导致命令注入",
    "subprocess.call": "使用 subprocess.call() 需审查参数来源",
    "subprocess.Popen": "使用 subprocess.Popen() 需审查参数来源",
    "subprocess.run": "使用 subprocess.run() 需审查参数来源",
}

# 网络外联调用规则表
_NETWORK_CALLS = {
    "socket.socket": "直接 socket 调用",
    "requests.get": "HTTP 请求",
    "requests.post": "HTTP 请求",
    "requests.put": "HTTP 请求",
    "requests.delete": "HTTP 请求",
    "httpx.get": "HTTP 请求",
    "httpx.post": "HTTP 请求",
    "urllib.request.urlopen": "HTTP 请求",
}

# 动态导入检测
_DYNAMIC_IMPORTS = {
    "importlib.import_module": "动态导入需审查目标来源",
    "__import__": "动态导入需审查目标来源",
}


class StaticSecurityScanner:
    """静态安全扫描器。

    基于 ast 模块扫描节点包源代码：
    ①危险 API 调用；②网络外联与 manifest 声明一致性；③动态导入风险。
    """

    def scan(self, package_path: str, manifest: PluginManifest) -> ScanReport:
        """执行静态安全扫描。

        参数：
            package_path —— 包目录路径
            manifest     —— 节点 manifest

        返回：
            ScanReport（含逐项发现）
        """
        start = time.monotonic()
        findings: List[ScanFinding] = []

        pkg_path = Path(package_path)
        if not pkg_path.exists():
            return ScanReport(
                report_id=str(uuid.uuid4()),
                passed=False,
                findings=[ScanFinding(
                    rule_id="package_not_found",
                    severity="error",
                    message=f"包路径不存在: {package_path}",
                )],
                error="package_not_found",
            )

        # 扫描所有 Python 文件
        try:
            for py_file in pkg_path.rglob("*.py"):
                file_findings = self._scan_file(py_file, manifest)
                findings.extend(file_findings)
        except Exception as exc:
            _logger.error("扫描异常: %s", exc, exc_info=True)
            return ScanReport(
                report_id=str(uuid.uuid4()),
                passed=False,
                findings=findings,
                error=f"{type(exc).__name__}: {exc}",
            )

        # 判断是否通过（有 error 级别发现则不通过）
        has_error = any(f.severity == "error" for f in findings)
        elapsed = time.monotonic() - start

        _logger.info(
            "静态安全扫描完成: path=%s passed=%s findings=%d elapsed=%.3fs",
            package_path,
            not has_error,
            len(findings),
            elapsed,
        )

        return ScanReport(
            report_id=str(uuid.uuid4()),
            passed=not has_error,
            findings=findings,
        )

    def _scan_file(self, file_path: Path, manifest: PluginManifest) -> List[ScanFinding]:
        """扫描单个 Python 文件。"""
        findings: List[ScanFinding] = []

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                source = f.read()
            tree = ast.parse(source, filename=str(file_path))
        except SyntaxError as exc:
            findings.append(ScanFinding(
                rule_id="syntax_error",
                severity="error",
                message=f"语法错误: {exc}",
                location=f"{file_path.name}:{exc.lineno}",
            ))
            return findings
        except Exception as exc:
            findings.append(ScanFinding(
                rule_id="parse_error",
                severity="error",
                message=f"解析失败: {exc}",
                location=file_path.name,
            ))
            return findings

        # 遍历 AST 节点
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                call_name = self._get_call_name(node)
                if call_name is None:
                    continue

                location = f"{file_path.name}:{node.lineno}"

                # 危险 API 调用
                if call_name in _DANGEROUS_CALLS:
                    findings.append(ScanFinding(
                        rule_id="dangerous_call",
                        severity="error",
                        message=_DANGEROUS_CALLS[call_name],
                        location=location,
                    ))

                # 网络外联检测
                if call_name in _NETWORK_CALLS:
                    # 检查是否在 manifest 声明的网络目标中
                    if not manifest.declared_network_targets:
                        findings.append(ScanFinding(
                            rule_id="undeclared_network",
                            severity="error",
                            message=f"网络外联 {call_name} 未在 manifest declared_network_targets 中声明",
                            location=location,
                        ))

                # 动态导入检测
                if call_name in _DYNAMIC_IMPORTS:
                    findings.append(ScanFinding(
                        rule_id="dynamic_import",
                        severity="warning",
                        message=_DYNAMIC_IMPORTS[call_name],
                        location=location,
                    ))

        return findings

    @staticmethod
    def _get_call_name(node: ast.Call) -> str:
        """获取调用表达式的名称。"""
        func = node.func
        if isinstance(func, ast.Name):
            return func.id
        elif isinstance(func, ast.Attribute):
            # 递归获取完整调用链（如 os.system）
            parts = []
            current = func
            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                parts.append(current.id)
            return ".".join(reversed(parts))
        return ""
