# =============================================================================
# 架构规范单测：engine 域 import 隔离（tests/unit/test_engine_import_isolation.py）
#
# 职责（roadmap M4 B10 / design D5）：以 AST 语法级扫描固化引擎域六前缀
# import 禁令——engine/ 不得 import
#   orchestration_platform.{api, configuration, governance, application,
#   worker, cli}
# 引擎配置经 EngineSettings 构造注入（engine/abstractions/engine_config.py），
# 宿主映射单点 application/engine_wiring.py（B10 核心约束的守护测试）。
# 例外登记：persistence 不禁（scheduler 引 persistence.abstractions 为
# 登记接受依赖，design D5）。
# 逻辑单一源：断言逻辑复用 scripts/check_engine_imports.py（经 importlib
# 路径加载，与 CI 独立步/run_local_ci 镜像共享同一实现，防双源漂移）。
# 先例：tests/unit/test_langgraph_isolation.py。
# =============================================================================

import ast
import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_engine_imports.py"


def _load_gate_module():
    """按路径加载 check_engine_imports.py（scripts 非包，importlib 方式）。"""
    spec = importlib.util.spec_from_file_location("check_engine_imports", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


GATE = _load_gate_module()


class TestEngineImportIsolation:
    """架构规范：engine 域六前缀 import 禁令（design D5）。"""

    def test_engine_six_prefix_imports_banned(self):
        """AST 语法级断言：engine/ 全部 .py（含函数体内惰性 import）
        零 6 前缀导入；违规逐行输出 相对路径:行:模块。"""
        _files, violations = GATE.collect_violations()
        assert len(violations) == 0, (
            f"发现 {len(violations)} 处违规导入"
            f"（禁令面：orchestration_platform.{{api, configuration, "
            f"governance, application, worker, cli}}；persistence 例外登记）:\n"
            + "\n".join(violations)
        )

    def test_scan_pipeline_not_vacuously_green(self):
        """扫描管线正向对照（防 glob/解析假绿）：
        1. engine/ 扫描文件数非空且达计划口径（design D5 计划 66，
           实施以实数核——IWorkflowEngine 移除后 ≥60）；
        2. scheduler.py 在扫描集内且仍 import orchestration_platform.domain
           ——证明 glob 覆盖与 AST 解析均读到真实文件内容。"""
        files = GATE.collect_engine_files()
        assert len(files) >= 60, (
            f"engine/ 扫描文件数 {len(files)} 低于口径下限 60——glob 疑似空扫"
        )
        scheduler = [f for f in files if f.name == "scheduler.py"]
        assert len(scheduler) == 1, "scheduler.py 不在扫描集内（glob 断面异常）"
        tree = ast.parse(scheduler[0].read_text(encoding="utf-8"))
        domain_imports = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.startswith("orchestration_platform.domain")
        ]
        assert len(domain_imports) > 0, (
            "scheduler.py 未检出 domain import——正向对照失败，扫描假绿"
        )

    def test_ban_rule_fires_on_synthetic_violations(self):
        """禁令规则本体对照：合成违禁样例必须命中（规则不哑火）。
        绝对导入、相对导入（level 上溯）、子模块前缀三类各验一发。"""
        engine_nodes_file = (
            GATE.SRC_ROOT / "engine" / "nodes" / "sample_node.py"
        )
        cases = [
            ("from orchestration_platform.configuration import settings", 0,
             "absolute"),
            ("from orchestration_platform.api.application_factory import create_app", 0,
             "absolute-sub"),
            ("from ...configuration import settings", 3,
             "relative-escapes-engine"),
        ]
        for source, level, label in cases:
            tree = ast.parse(source)
            node = next(
                n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
            )
            assert node.level == level, f"用例构造错误：{label}"
            target = GATE.resolve_import_from(engine_nodes_file, node)
            assert GATE.is_banned(target), (
                f"禁令规则未命中 {label} 用例：target={target!r}"
            )

    def test_ban_rule_allows_registered_exceptions(self):
        """例外与白面前瞻对照：domain/persistence/plugin_sdk/integrations/
        context_engine 等现役允许前缀不误伤；裸包名与相近前缀不误判。"""
        allowed = [
            "orchestration_platform.domain.errors",
            "orchestration_platform.persistence.abstractions.repositories",
            "orchestration_platform.plugin_sdk.models",
            "orchestration_platform.integrations.sandbox.models",
            "orchestration_platform.context_engine.models",
            "orchestration_platform.engine.scheduler",
        ]
        for module in allowed:
            assert not GATE.is_banned(module), f"允许前缀被误伤：{module}"
        # 相近前缀不误判：banned 判定要求前缀完整段匹配
        assert not GATE.is_banned("orchestration_platform.application_layer")
        assert not GATE.is_banned("orchestration_platform.apis")
        # 边界等值命中：裸前缀本身即违规
        assert GATE.is_banned("orchestration_platform.api")
