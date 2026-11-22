# =============================================================================
# 抽象隔离契约测试（tests/unit/test_no_direct_framework_dependency.py）
#
# 覆盖验收：B7（业务层对 LangGraph 零依赖）
# =============================================================================

import pytest

from orchestration_platform.domain.validation.graph_topology_validator import (
    GraphTopologyValidator,
)


class TestNoDirectFrameworkDependency:
    """契约测试：扫描源码断言除 engine/adapter/ 外无 langgraph 导入。"""

    def test_no_langgraph_import_outside_adapter(self):
        """除 engine/adapter/ 外，全仓库不得出现 import langgraph。"""
        from pathlib import Path

        src_root = Path(__file__).resolve().parent.parent.parent / "src" / "orchestration_platform"
        adapter_dir = src_root / "engine" / "adapter"

        violations = []
        for py_file in src_root.rglob("*.py"):
            if adapter_dir in py_file.parents or py_file.parent == adapter_dir:
                continue
            text = py_file.read_text(encoding="utf-8")
            for line_num, line in enumerate(text.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("import langgraph") or "from langgraph" in stripped:
                    if stripped.startswith("#"):
                        continue
                    violations.append(f"{py_file}:{line_num}: {stripped}")

        assert len(violations) == 0, (
            f"发现 {len(violations)} 处违规 langgraph 导入:\n" + "\n".join(violations)
        )

    def test_adapter_isolation_boundary_exists(self):
        """engine/adapter/ 目录应存在，作为隔离边界。"""
        from pathlib import Path
        adapter_dir = (
            Path(__file__).resolve().parent.parent.parent
            / "src"
            / "orchestration_platform"
            / "engine"
            / "adapter"
        )
        assert adapter_dir.exists(), "engine/adapter/ 隔离层目录不存在"
        adapter_files = list(adapter_dir.glob("*.py"))
        assert len(adapter_files) >= 2, "隔离层应至少包含适配器与状态映射两个文件"
