# =============================================================================
# 运行日志子包入口（observability/run_log/__init__.py，workflow_run_logging CHG-01）
#
# 职责：包级导出（WorkflowRunLogger / RunLogSink / LogPathResolver /
#       RunLogRedactor / Truncator / LogRetentionCleaner）。
# 设计意图（design 2.1.2、INV-D-06）：
#   1. 采用 PEP 562 模块级 __getattr__ 懒导出——导入期零副作用
#      （不创建目录、不构造 handler、不启动线程）；
#   2. 延迟解析避免子模块间导入期循环依赖。
# =============================================================================

import importlib

_EXPORTS: dict = {
    "WorkflowRunLogger": ".recorder",
    "RunLogSink": ".sink",
    "LogPathResolver": ".pathing",
    "RunLogRedactor": ".redactor",
    "Truncator": ".truncation",
    "LogRetentionCleaner": ".retention",
}


def __getattr__(name: str):
    module_path = _EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(module_path, __package__)
    attr = getattr(module, name)
    globals()[name] = attr
    return attr


def __dir__() -> list:
    return sorted(set(list(globals().keys()) + list(_EXPORTS.keys())))
