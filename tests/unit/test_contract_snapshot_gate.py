# -*- coding: utf-8 -*-
# =============================================================================
# B14 T2 契约 snapshot 门禁薄包装（tests/unit/test_contract_snapshot_gate.py）
#
# 单一事实源：importlib 复用 scripts/check_contract_snapshot.py 的完整校验
# （openapi 全量金档字节比对 + SDK/web/apiTypes 三面提取断言 + 子集不变量），
# 免子进程、免重复实现；金档漂移 = 本测试红 = 主门禁拦截。
#
# 覆盖：
#   1. check() 退出码 0（金档与当前契约面一致）；
#   2. 钉扎环境变量在 check 后精确还原（pytest 进程内运行的洁净性——
#      不泄漏钉扎值给同进程后续测试，get_settings 缓存失效由脚本负责）。
#
# 金档重建：python scripts/check_contract_snapshot.py --regen（评审后使用，
# 见 docs/sdk-contract.md「契约 snapshot 门禁」节）。
# =============================================================================
import importlib.util
import os
from pathlib import Path

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "check_contract_snapshot.py"
)


def _load_check_module():
    spec = importlib.util.spec_from_file_location(
        "check_contract_snapshot_gate_ref", _SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_contract_snapshot_gate_passes_and_env_restored():
    """金档校验通过 + 钉扎环境精确还原（单一门禁入口，免子进程）。"""
    module = _load_check_module()
    before = {key: os.environ.get(key) for key in module._PINNED_ENV}

    assert module.main([]) == 0, "契约 snapshot 金档漂移：评审后以 --regen 更新"

    after = {key: os.environ.get(key) for key in module._PINNED_ENV}
    assert before == after, "钉扎环境变量在 check 后未还原（进程内洁净性破坏）"
