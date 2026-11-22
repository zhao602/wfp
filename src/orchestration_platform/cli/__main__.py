# =============================================================================
# wfp CLI 模块直跑入口（cli/__main__.py）
#
# 职责（arch_review_v2 5.1）：支持 `python -m orchestration_platform.cli` 直跑，
# 退出码透传 main() 返回值（0/1/2，见 output.py 约定）。
# =============================================================================

import sys

from orchestration_platform.cli import main

if __name__ == "__main__":
    sys.exit(main())
