# TODO(tech-debt): B档候选孤儿（族群 B11），待 APM 链路追踪专项排查确认无调用后清理，见 docs/tech-debt-ledger.md
# =============================================================================
# 本地调试运行器（plugin_sdk/debug_runner.py）
#
# 职责：命令行入口，不启动完整平台即可调试节点。
# =============================================================================

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict

from orchestration_platform.plugin_sdk.manifest_validator import (
    ManifestValidator,
)
from orchestration_platform.plugin_sdk.models import LifecycleLog, PluginManifest

_logger = logging.getLogger(__name__)


class LocalDebugRunner:
    """本地调试运行器。

    加载 manifest→动态导入节点类→setup→execute→teardown 顺序执行。
    """

    def run(
        self,
        manifest_path: str,
        input_path: str,
        config: Dict[str, Any] = None,
    ) -> int:
        """执行本地调试。

        参数：
            manifest_path：manifest 文件路径
            input_path：样例输入 JSON 文件路径
            config：节点配置（默认空字典）

        返回：
            退出码（0=成功，1=校验失败，2=执行失败）
        """
        config = config or {}

        # 1. 加载 manifest
        manifest = self._load_manifest(manifest_path)
        if manifest is None:
            print("manifest 加载失败")
            return 1

        # 2. 校验 manifest
        validator = ManifestValidator()
        result = validator.validate(manifest)
        if not result.valid:
            print("manifest 校验失败:")
            for err in result.errors:
                print(f"  - {err}")
            return 1

        # 3. 加载样例输入
        try:
            with open(input_path, "r", encoding="utf-8") as f:
                inputs = json.load(f)
        except Exception as exc:
            print(f"样例输入加载失败: {exc}")
            return 1

        # 4. 动态导入节点类
        try:
            node_instance = self._load_node_instance(manifest_path, manifest)
        except Exception as exc:
            print(f"节点类加载失败: {exc}")
            return 1

        # 5. 生命周期执行
        logs = []

        # setup
        log = self._run_phase("setup", node_instance.setup, config)
        logs.append(log)
        if not log.success:
            self._print_logs(logs)
            return 2

        # execute
        log = self._run_phase("execute", node_instance.execute, inputs)
        logs.append(log)
        if not log.success:
            # teardown 仍被调用
            td_log = self._run_phase("teardown", node_instance.teardown)
            logs.append(td_log)
            self._print_logs(logs)
            return 2

        # teardown
        log = self._run_phase("teardown", node_instance.teardown)
        logs.append(log)

        self._print_logs(logs)
        print("\n执行结果: 见上方日志")
        return 0

    def _load_manifest(self, manifest_path: str):
        """加载 manifest。"""
        try:
            import yaml
            with open(manifest_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            return PluginManifest(**data)
        except Exception as exc:
            _logger.error("manifest 加载失败: %s", exc)
            return None

    def _load_node_instance(self, manifest_path: str, manifest: PluginManifest):
        """动态导入节点类并实例化。"""
        import importlib

        pkg_dir = Path(manifest_path).parent
        sys.path.insert(0, str(pkg_dir))

        entry_module = manifest.entry_point.replace(".py", "")
        module = importlib.import_module(entry_module)

        # 查找 PluginNodeBase 子类
        from orchestration_platform.plugin_sdk.plugin_node_base import (
            PluginNodeBase,
        )
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (
                isinstance(attr, type)
                and issubclass(attr, PluginNodeBase)
                and attr is not PluginNodeBase
            ):
                return attr()

        raise ValueError("入口文件中未找到 PluginNodeBase 子类")

    @staticmethod
    def _run_phase(phase: str, func, *args) -> LifecycleLog:
        """执行单个生命周期阶段。"""
        start = time.monotonic()
        try:
            func(*args)
            return LifecycleLog(
                phase=phase,
                duration_seconds=round(time.monotonic() - start, 4),
                success=True,
            )
        except Exception as exc:
            return LifecycleLog(
                phase=phase,
                duration_seconds=round(time.monotonic() - start, 4),
                success=False,
                error=f"{type(exc).__name__}: {exc}",
            )

    @staticmethod
    def _print_logs(logs: list) -> None:
        """打印生命周期日志。"""
        print("\n生命周期日志:")
        for log in logs:
            status = "✓" if log.success else "✗"
            print(f"  {status} {log.phase}: {log.duration_seconds}s", end="")
            if log.error:
                print(f" - {log.error}", end="")
            print()


def main():
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="插件节点本地调试运行器")
    parser.add_argument("--manifest", required=True, help="manifest 文件路径")
    parser.add_argument("--input", required=True, help="样例输入 JSON 文件路径")
    args = parser.parse_args()

    runner = LocalDebugRunner()
    sys.exit(runner.run(args.manifest, args.input))


if __name__ == "__main__":
    main()
