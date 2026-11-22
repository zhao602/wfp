# =============================================================================
# wfp CLI 包入口（orchestration_platform/cli/__init__.py）
#
# 职责（arch_review_v2 5.1，CHG-17/22，DC-11）：
#   main(argv) → int：pyproject [project.scripts] wfp = "...cli:main" 入口；
#   `python -m orchestration_platform.cli` 经 __main__.py 直跑；
#   exit code 约定见 output.py（0 成功 / 1 校验或执行失败 / 2 加载异常）。
# =============================================================================

from typing import Optional, Sequence

from orchestration_platform.cli.output import (
    EXIT_FAIL,
    EXIT_LOAD_ERROR,
    EXIT_OK,
)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI 主入口：解析参数 → 分派子命令 → 返回进程退出码。

    未带子命令时打印帮助并返回 1（EXIT_FAIL）；
    子命令 handler 统一签名 handler(args) -> int。
    """
    # 懒导入解析器链：保持 `import orchestration_platform.cli` 轻量，
    # 同时避免包初始化期的模块环（parser → commands → cli.output）。
    from orchestration_platform.cli.parser import build_main_parser

    parser = build_main_parser()
    args = parser.parse_args(argv)

    handler = getattr(args, "handler", None)
    if handler is None:
        # 裸 wfp（无子命令）：打印帮助而非报错退出
        parser.print_help()
        return EXIT_FAIL
    return handler(args)


__all__ = ["main", "EXIT_OK", "EXIT_FAIL", "EXIT_LOAD_ERROR"]
