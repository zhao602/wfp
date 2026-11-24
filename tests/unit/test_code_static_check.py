# =============================================================================
# 静态合规校验分流映射单元测试（tests/unit/test_code_static_check.py）
#
# 覆盖验收：llm_code_nodes_v2 任务 3.2/3.3 T6（DC-03 / FR-21/29 / AC-21/29 /
# FR-33 同因同码 / ZD-10 / G-09）
# =============================================================================

from orchestration_platform.domain.errors import (
    CODE_BANNED_CONSTRUCT,
    CODE_SYNTAX_ERROR,
)
from orchestration_platform.engine.nodes.code_execution.static_check import (
    FINDINGS_DISPLAY_LIMIT,
    SYNTAX_FINDING_PREFIX,
    classify_static_verdict,
)
from orchestration_platform.engine.nodes.data_transform_ops.custom_code_guard import (
    validate_custom_code,
)


class TestClassifyStaticVerdictRouting:
    """分流映射：语法失败形态 → CODE_SYNTAX_ERROR；其余 → CODE_BANNED_CONSTRUCT。"""

    def test_compliant_verdict_returns_none(self):
        """合规脚本 → None（无执行期错误）。"""
        verdict = validate_custom_code("result = 1 + 2", "python")
        assert verdict.compliant
        assert classify_static_verdict(verdict) is None

    def test_banned_import_maps_to_banned_construct(self):
        """脚本含 import os → CODE_BANNED_CONSTRUCT 且消息含命中项（AC-21）。"""
        verdict = validate_custom_code("import os\nresult = 1", "python")
        mapping = classify_static_verdict(verdict)

        assert mapping is not None
        assert mapping[0] == CODE_BANNED_CONSTRUCT
        assert "os" in mapping[1]
        assert SYNTAX_FINDING_PREFIX not in mapping[1]

    def test_syntax_error_maps_to_syntax_error_with_lineno(self):
        """脚本含 def f(: → CODE_SYNTAX_ERROR 且消息含行号（AC-29）。"""
        verdict = validate_custom_code("def f(:\n    pass\n", "python")
        mapping = classify_static_verdict(verdict)

        assert mapping is not None
        assert mapping[0] == CODE_SYNTAX_ERROR
        assert SYNTAX_FINDING_PREFIX in mapping[1]
        assert "第 1 行" in mapping[1]

    def test_banned_call_finding_in_message(self):
        """禁用调用（eval）命中项进入消息。"""
        verdict = validate_custom_code("x = eval('1')\nresult = x", "python")
        mapping = classify_static_verdict(verdict)

        assert mapping is not None
        assert mapping[0] == CODE_BANNED_CONSTRUCT
        assert "eval" in mapping[1]

    def test_banned_attribute_finding_in_message(self):
        """逃逸属性（__subclasses__）命中项进入消息。"""
        verdict = validate_custom_code(
            "x = [].__subclasses__\nresult = 1", "python"
        )
        mapping = classify_static_verdict(verdict)

        assert mapping is not None
        assert mapping[0] == CODE_BANNED_CONSTRUCT
        assert "__subclasses__" in mapping[1]

    def test_empty_script_routes_to_banned_bucket(self):
        """空脚本文本：守卫「脚本文本为空」→ 其余桶（CODE_BANNED_CONSTRUCT）。"""
        verdict = validate_custom_code("   ", "python")
        mapping = classify_static_verdict(verdict)

        assert mapping is not None
        assert mapping[0] == CODE_BANNED_CONSTRUCT
        assert "脚本文本为空" in mapping[1]

    def test_unsupported_language_routes_to_banned_bucket(self):
        """语言不支持：守卫定制消息 → 其余桶（加载期已拒绝，运行期防御分流）。"""
        verdict = validate_custom_code("result = 1", "javascript")
        mapping = classify_static_verdict(verdict)

        assert mapping is not None
        assert mapping[0] == CODE_BANNED_CONSTRUCT
        assert "javascript" in mapping[1]


class TestClassifyStaticVerdictDeterminism:
    """同因同码（FR-33）：同一脚本重复分流，结果逐项一致。"""

    def test_same_cause_same_code_three_runs(self):
        script = "import os\nresult = 1"
        mappings = [
            classify_static_verdict(validate_custom_code(script, "python"))
            for _ in range(3)
        ]

        assert all(mapping is not None for mapping in mappings)
        assert mappings[0][0] == CODE_BANNED_CONSTRUCT
        assert mappings[1] == mappings[0]
        assert mappings[2] == mappings[0]

    def test_same_syntax_cause_same_code_three_runs(self):
        script = "def f(:\n    pass"
        mappings = [
            classify_static_verdict(validate_custom_code(script, "python"))
            for _ in range(3)
        ]

        assert all(mapping is not None for mapping in mappings)
        assert mappings[0][0] == CODE_SYNTAX_ERROR
        assert mappings[1] == mappings[0]
        assert mappings[2] == mappings[0]


class TestClassifyStaticVerdictMessageHygiene:
    """消息卫生：不含脚本全文；命中项展示上限与余量计数。"""

    def test_message_excludes_script_full_text(self):
        """消息只含命中项摘要，不含脚本全文（防日志泄漏）。"""
        secret_marker = "UNIQUE_MARKER_MUST_NOT_LEAK"
        script = f"import os\nvalue = '{secret_marker}'\nresult = 1"
        verdict = validate_custom_code(script, "python")
        mapping = classify_static_verdict(verdict)

        assert mapping is not None
        assert secret_marker not in mapping[1]

    def test_findings_display_limit_capped_with_remainder_count(self):
        """命中项超过展示上限：至多 FINDINGS_DISPLAY_LIMIT 条 + 余量计数。"""
        script = "\n".join(["import os"] * 7) + "\nresult = 1"
        verdict = validate_custom_code(script, "python")
        mapping = classify_static_verdict(verdict)

        assert mapping is not None
        assert mapping[0] == CODE_BANNED_CONSTRUCT
        assert "共 7 项命中" in mapping[1]
        assert f"等 {7 - FINDINGS_DISPLAY_LIMIT} 项" in mapping[1]
        assert mapping[1].count("敏感模块导入") == FINDINGS_DISPLAY_LIMIT


class TestGuardParityWithDataTransform:
    """对照断言（任务 3.2 验收）：分流映射消息派生自同一守卫命中集合。"""

    def test_message_derives_from_guard_findings(self):
        """消息覆盖守卫全部命中项（data_transform 与 code 节点同源守卫）。"""
        script = "import os\nx = eval('2')\nresult = x"
        verdict = validate_custom_code(script, "python")
        mapping = classify_static_verdict(verdict)

        assert mapping is not None
        assert not verdict.compliant
        for finding in verdict.findings:
            assert finding in mapping[1]

    def test_guard_reused_unmodified(self):
        """守卫行为基线：既有禁项判定保持（custom_code_guard.py 零修改佐证）。"""
        assert validate_custom_code("import sys\nresult = 1", "python").compliant is False
        assert validate_custom_code("result = abs(-1)", "python").compliant is True
        assert validate_custom_code("x = open('/etc/passwd')\n", "python").compliant is False
        assert validate_custom_code("result = __import__('os')", "python").compliant is False
