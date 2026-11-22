# =============================================================================
# 自定义脚本静态合规校验守卫（engine/nodes/data_transform_ops/custom_code_guard.py）
#
# 职责：以 ast.parse（**只解析、绝不执行**）对登记的脚本文本做静态合规扫描
#       （expand_data_transform_ops design DC-08 / CHG-B11 / T5.3，AC-15）。
# 扫描禁项：
#   - 敏感 import（os/sys/socket/subprocess/importlib/shutil 等网络/文件系统/
#     系统命令/动态加载相关模块）；
#   - 禁用调用（动态执行：exec/eval/compile/__import__；文件系统：open；
#     调试后门：breakpoint）；
#   - 敏感属性访问（__globals__/__builtins__ 等逃逸通道）。
# 边界声明（DC-08 / INV-07 / INV-08）：
#   - 静态校验 **不等于运行期安全性证明**（UI 与 spec 双重声明）；
#   - 保守口径：宁可误报（如用户自定义 .open() 方法被命中），
#     不漏报已知高危形态；
#   - 本模块自身仅 import ast（机检口径：脚本相关模块不得 import/调用
#     subprocess/exec/eval/compile/os.system/importlib）。
# =============================================================================

import ast
from typing import List, NamedTuple


class CustomCodeVerdict(NamedTuple):
    """静态合规校验结论（compliant=False 时 findings 逐条列出命中项）。"""

    compliant: bool
    findings: List[str]


# 敏感 import 模块根名（DC-08：os/sys/socket/subprocess/importlib/shutil 等；
# 含网络/文件系统/系统命令/动态加载/序列化逃逸相关标准库）
_BANNED_MODULES = frozenset(
    {
        "os",
        "sys",
        "socket",
        "subprocess",
        "importlib",
        "shutil",
        "pathlib",
        "glob",
        "io",
        "ctypes",
        "multiprocessing",
        "threading",
        "signal",
        "tempfile",
        "pickle",
        "urllib",
        "http",
        "ftplib",
        "smtplib",
        "imaplib",
        "poplib",
        "telnetlib",
        "webbrowser",
        "code",
        "codeop",
        "runpy",
        "pkgutil",
        "builtins",
    }
)

# 禁用调用名（动态执行/文件系统/调试后门；os.system 等经敏感 import 覆盖）
_BANNED_CALLS = frozenset({"exec", "eval", "compile", "__import__", "open", "breakpoint"})

# 敏感属性名（解释器逃逸通道）
_BANNED_ATTRIBUTES = frozenset(
    {
        "__globals__",
        "__builtins__",
        "__subclasses__",
        "__code__",
        "__closure__",
        "__loader__",
        "__spec__",
    }
)


def validate_custom_code(code: str, language: str) -> CustomCodeVerdict:
    """脚本静态合规校验（ast.parse 只解析不执行；命中禁项即不合规）。

    - language 仅支持 python（本期 JS 无运行时不登记）；
    - 语法解析失败 / 空脚本文本 → 不合规（录入期拒绝）；
    - **不等于运行期安全性证明**（DC-08 显式声明）。
    """
    if language != "python":
        return CustomCodeVerdict(
            False,
            [f"脚本语言不支持登记：{language}（本期仅支持 python 静态校验）"],
        )
    if not code or not code.strip():
        return CustomCodeVerdict(False, ["脚本文本为空"])

    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return CustomCodeVerdict(
            False, [f"脚本语法解析失败：{exc.msg}（第 {exc.lineno} 行）"]
        )

    findings: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in _BANNED_MODULES:
                    findings.append(f"敏感模块导入：import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in _BANNED_MODULES:
                findings.append(f"敏感模块导入：from {node.module} import ...")
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in _BANNED_CALLS:
                findings.append(f"禁用调用：{func.id}()")
            elif isinstance(func, ast.Attribute) and func.attr in _BANNED_CALLS:
                findings.append(f"禁用调用：{func.attr}()")
        elif isinstance(node, ast.Attribute) and node.attr in _BANNED_ATTRIBUTES:
            findings.append(f"敏感属性访问：{node.attr}")

    return CustomCodeVerdict(not findings, findings)
