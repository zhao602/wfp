# =============================================================================
# CLI 参数解析器（cli/parser.py）
#
# 职责（arch_review_v2 5.1，CHG-17/22，DC-11）：
#   argparse 子命令注册 validate / diff / graph / run（零新增依赖，Python 3.10）；
#   公共 --json 旗标经 parent parser 注入四个子命令（机器读口径）。
# =============================================================================

import argparse

from orchestration_platform.cli.commands.diff import run_diff
from orchestration_platform.cli.commands.graph import run_graph
from orchestration_platform.cli.commands.run_local import run_run_local
from orchestration_platform.cli.commands.validate import run_validate


def build_main_parser() -> argparse.ArgumentParser:
    """构造 wfp 主解析器（四命令注册完整，--help 就绪）。"""
    parser = argparse.ArgumentParser(
        prog="wfp",
        description=(
            "工作流编排平台 CLI（wfp）：validate 校验 / diff 语义对比 / "
            "graph Mermaid 图 / run 本地执行"
        ),
    )
    subparsers = parser.add_subparsers(
        dest="command", metavar="{validate,diff,graph,run}"
    )

    # 公共 --json 旗标（四个子命令统一注入）
    json_parent = argparse.ArgumentParser(add_help=False)
    json_parent.add_argument(
        "--json", action="store_true", help="机器可读 JSON 输出（默认人读文本）"
    )

    # validate：文件/目录全链校验（三层校验链只读复用）
    parser_validate = subparsers.add_parser(
        "validate",
        parents=[json_parent],
        help="校验工作流定义（解析 → 拓扑 → 节点配置 → 编译）",
    )
    parser_validate.add_argument("target", help="工作流 YAML 文件或目录（目录递归 *.yaml）")
    parser_validate.set_defaults(handler=run_validate)

    # diff：双侧解析编译 → 语义 diff 引擎（骨架，6.3 合入后功能完整）
    parser_diff = subparsers.add_parser(
        "diff",
        parents=[json_parent],
        help="对比两份工作流定义的语义差异（拓扑/参数/布局三类）",
    )
    parser_diff.add_argument("base", help="基准侧工作流 YAML 文件")
    parser_diff.add_argument("head", help="对比侧工作流 YAML 文件")
    parser_diff.set_defaults(handler=run_diff)

    # graph：编译产物 → mermaid flowchart TD
    parser_graph = subparsers.add_parser(
        "graph",
        parents=[json_parent],
        help="输出工作流 Mermaid 流程图（flowchart TD）",
    )
    parser_graph.add_argument("target", help="工作流 YAML 文件")
    parser_graph.set_defaults(handler=run_graph)

    # run：本地执行（--local 当前唯一模式）
    parser_run = subparsers.add_parser(
        "run",
        parents=[json_parent],
        help="执行工作流（当前仅支持 --local 本地单进程模式）",
    )
    parser_run.add_argument("target", help="工作流 YAML 文件")
    parser_run.add_argument(
        "--local",
        action="store_true",
        help="本地单进程执行（必选；不触达 MCP/LLM 外部服务）",
    )
    parser_run.add_argument(
        "--checkpoint",
        action="store_true",
        help="启用本地文件检查点持久化（默认关闭）",
    )
    parser_run.set_defaults(handler=run_run_local)

    return parser
