# =============================================================================
# 插件节点加载器（engine/plugin_nodes/plugin_node_loader.py）
#
# 职责：动态导入插件节点包、manifest 校验、实例池管理与健康标记。
# 设计意图（对应偏差修正 spec 5.2.1.3 / design 2.2.2.2）：
#   1. 单节点失败隔离：单节点加载失败仅该节点不可用，返回错误详情，
#      不影响其余节点加载（spec 5.2.1.3）。
#   2. setup 失败的实例标记不健康并从实例池剔除（spec 5.2.3.2）。
#   3. 卸载时逐实例调用 teardown 后移除注册。
#   4. plugin_sdk_enabled=False 时不加载任何节点包（降级形态）。
#   5. 平台最大插件节点数受 plugin_sdk_max_nodes 约束。
#   6. 安装时复核包内容摘要与 manifest 一致性（防上架后包被篡改）。
# =============================================================================

import importlib
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from orchestration_platform.domain.errors import (
    NODE_DEPENDENCY_MISSING,
    NODE_LIFECYCLE_ERROR,
    NODE_MANIFEST_INVALID,
    NODE_PACKAGE_INVALID,
    DomainError,
)
from orchestration_platform.engine.abstractions.engine_config import EngineSettings
from orchestration_platform.plugin_sdk.manifest_validator import (
    ManifestValidator,
)
from orchestration_platform.plugin_sdk.models import (
    LifecycleLog,
    PluginManifest,
    PluginNodeRegistration,
    ValidationResult,
)
from orchestration_platform.plugin_sdk.package_validator import (
    NodePackageValidator,
)

_logger = logging.getLogger(__name__)


class PluginNodeLoader:
    """插件节点加载器：实现 INodePackageLoader。

    动态导入（importlib）+ manifest 校验 + 实例池管理 + 健康标记。
    单节点失败隔离：单节点加载失败仅该节点不可用，不影响其余节点。
    """

    def __init__(
        self,
        manifest_validator: Optional[ManifestValidator] = None,
        package_validator: Optional[NodePackageValidator] = None,
        settings: Optional[EngineSettings] = None,
    ) -> None:
        """构造加载器。

        参数：
            manifest_validator —— manifest 校验器（缺省自动创建）
            package_validator  —— 包规范校验器（缺省自动创建）
            settings           —— 引擎运行配置（M4 B10：缺省 None →
                                  EngineSettings() 默认值；宿主注入生效 env 覆盖）
        """
        self._manifest_validator = manifest_validator or ManifestValidator()
        self._package_validator = package_validator or NodePackageValidator()
        self._settings = settings or EngineSettings()
        # 已安装节点注册表：node_name -> PluginNodeRegistration
        self._registrations: Dict[str, PluginNodeRegistration] = {}
        # 实例池：node_name -> PluginNodeBase 实例
        self._instances: Dict[str, Any] = {}
        # 包摘要记录：node_name -> SHA-256 摘要（防篡改复核）
        self._digests: Dict[str, str] = {}
        # 生命周期日志：node_name -> [LifecycleLog]
        self._lifecycle_logs: Dict[str, List[LifecycleLog]] = {}

    # ------------------------------------------------------------------
    # INodePackageLoader 实现
    # ------------------------------------------------------------------

    def load_package(self, package_path: str) -> PluginNodeRegistration:
        """加载并安装节点包。

        流程：功能开关检查 → 包规范校验 → manifest 校验 → 摘要复核 →
              依赖检查 → 动态导入 → setup → 注册。

        异常：
            DomainError(NODE_PACKAGE_INVALID)    —— 包规范不符
            DomainError(NODE_MANIFEST_INVALID)   —— manifest 校验失败
            DomainError(NODE_DEPENDENCY_MISSING) —— 依赖缺失
            DomainError(NODE_LIFECYCLE_ERROR)    —— setup 失败
        """
        settings = self._settings

        # 降级形态：功能开关关闭时不加载任何节点包
        if not settings.plugin_sdk_enabled:
            _logger.warning(
                "插件 SDK 未启用（plugin_sdk_enabled=False），跳过节点包加载: %s",
                package_path,
            )
            raise DomainError(
                NODE_PACKAGE_INVALID,
                "插件 SDK 未启用，无法加载节点包",
                {"package_path": package_path},
            )

        # 平台最大插件节点数约束
        if len(self._registrations) >= settings.plugin_sdk_max_nodes:
            raise DomainError(
                NODE_PACKAGE_INVALID,
                f"已达到平台最大插件节点数上限（{settings.plugin_sdk_max_nodes}）",
                {"current_count": len(self._registrations)},
            )

        # 1. 包规范校验
        pkg_result = self._package_validator.validate(package_path)
        if not pkg_result.valid:
            raise DomainError(
                NODE_PACKAGE_INVALID,
                "节点包规范校验失败",
                {"errors": pkg_result.errors, "warnings": pkg_result.warnings},
            )

        # 2. 加载 manifest
        manifest = self._load_manifest(package_path)
        if manifest is None:
            raise DomainError(
                NODE_MANIFEST_INVALID,
                "manifest 加载失败",
                {"package_path": package_path},
            )

        # 3. manifest 格式校验
        manifest_result = self._manifest_validator.validate(manifest)
        if not manifest_result.valid:
            raise DomainError(
                NODE_MANIFEST_INVALID,
                "manifest 校验失败",
                {"errors": manifest_result.errors, "warnings": manifest_result.warnings},
            )

        # 4. 摘要复核（防上架后包被篡改）
        digest = self._package_validator.compute_digest(package_path)
        existing_digest = self._digests.get(manifest.node_name)
        if existing_digest is not None and existing_digest != digest:
            raise DomainError(
                NODE_PACKAGE_INVALID,
                "包内容摘要与已安装版本不一致（疑似篡改）",
                {"expected": existing_digest, "actual": digest},
            )

        # 5. 依赖检查
        missing_deps = self._check_dependencies(manifest.dependencies)
        if missing_deps:
            raise DomainError(
                NODE_DEPENDENCY_MISSING,
                "节点依赖缺失",
                {"missing_dependencies": missing_deps},
            )

        # 6. 动态导入节点类并实例化
        try:
            node_instance = self._import_node_class(package_path, manifest)
        except DomainError:
            raise
        except Exception as exc:
            _logger.error("节点类加载失败: %s", exc, exc_info=True)
            raise DomainError(
                NODE_MANIFEST_INVALID,
                f"节点类加载失败: {exc}",
                {"package_path": package_path},
            )

        # 7. setup 生命周期（带超时）
        logs: List[LifecycleLog] = []
        setup_log = self._run_phase_with_timeout(
            "setup",
            node_instance.setup,
            manifest,
            timeout_seconds=settings.plugin_sdk_setup_timeout_seconds,
        )
        logs.append(setup_log)

        if not setup_log.success:
            # setup 失败：实例标记不健康并从实例池剔除（spec 5.2.3.2）
            _logger.error(
                "节点 %s setup 失败: %s",
                manifest.node_name,
                setup_log.error,
            )
            registration = PluginNodeRegistration(
                node_name=manifest.node_name,
                version=manifest.version,
                manifest=manifest,
                healthy=False,
                instance_count=0,
            )
            self._registrations[manifest.node_name] = registration
            self._lifecycle_logs[manifest.node_name] = logs
            raise DomainError(
                NODE_LIFECYCLE_ERROR,
                f"节点 setup 失败: {setup_log.error}",
                {"node_name": manifest.node_name, "phase": "setup"},
            )

        # 8. 注册
        registration = PluginNodeRegistration(
            node_name=manifest.node_name,
            version=manifest.version,
            manifest=manifest,
            healthy=True,
            instance_count=1,
        )
        self._registrations[manifest.node_name] = registration
        self._instances[manifest.node_name] = node_instance
        self._digests[manifest.node_name] = digest
        self._lifecycle_logs[manifest.node_name] = logs

        _logger.info(
            "插件节点加载成功: node_name=%s version=%s",
            manifest.node_name,
            manifest.version,
        )

        return registration

    def list_installed(self) -> List[PluginNodeRegistration]:
        """列出已安装节点（含健康标记与版本）。"""
        return list(self._registrations.values())

    def unload_package(self, node_name: str) -> None:
        """卸载节点包：逐实例调用 teardown 后移除注册。

        卸载失败（teardown 异常）不阻断移除注册，仅记录日志。
        """
        instance = self._instances.pop(node_name, None)
        registration = self._registrations.pop(node_name, None)
        self._digests.pop(node_name, None)
        logs = self._lifecycle_logs.get(node_name, [])

        if instance is not None:
            teardown_log = self._run_phase("teardown", instance.teardown)
            logs.append(teardown_log)
            if not teardown_log.success:
                _logger.warning(
                    "节点 %s teardown 失败（已移除注册）: %s",
                    node_name,
                    teardown_log.error,
                )

        self._lifecycle_logs.pop(node_name, None)
        _logger.info("插件节点卸载完成: node_name=%s", node_name)

    # ------------------------------------------------------------------
    # 查询方法
    # ------------------------------------------------------------------

    def get_instance(self, node_name: str) -> Any:
        """获取节点实例（不存在或不健康时返回 None）。"""
        registration = self._registrations.get(node_name)
        if registration is None or not registration.healthy:
            return None
        return self._instances.get(node_name)

    def get_registration(self, node_name: str) -> Optional[PluginNodeRegistration]:
        """获取节点注册信息。"""
        return self._registrations.get(node_name)

    def get_lifecycle_logs(self, node_name: str) -> List[LifecycleLog]:
        """获取节点生命周期日志。"""
        return self._lifecycle_logs.get(node_name, [])

    def is_healthy(self, node_name: str) -> bool:
        """检查节点是否健康。"""
        registration = self._registrations.get(node_name)
        return registration is not None and registration.healthy

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _load_manifest(self, package_path: str) -> Optional[PluginManifest]:
        """从包目录加载 manifest。"""
        manifest_path = Path(package_path) / "manifest.yaml"
        if not manifest_path.exists():
            return None
        try:
            import yaml
            with open(manifest_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            return PluginManifest(**data)
        except Exception as exc:
            _logger.error("manifest 加载失败: %s", exc)
            return None

    def _check_dependencies(self, dependencies: List[str]) -> List[str]:
        """检查依赖清单，返回缺失依赖列表。"""
        missing: List[str] = []
        for dep in dependencies:
            try:
                importlib.import_module(dep)
            except ImportError:
                missing.append(dep)
        return missing

    def _import_node_class(self, package_path: str, manifest: PluginManifest):
        """动态导入节点类并实例化。

        从入口文件中查找 PluginNodeBase 子类。
        """
        pkg_dir = Path(package_path).resolve()
        pkg_dir_str = str(pkg_dir)
        if pkg_dir_str not in sys.path:
            sys.path.insert(0, pkg_dir_str)

        entry_module_name = manifest.entry_point.replace(".py", "")
        module = importlib.import_module(entry_module_name)

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

        raise DomainError(
            NODE_MANIFEST_INVALID,
            f"入口文件中未找到 PluginNodeBase 子类: {manifest.entry_point}",
            {"entry_point": manifest.entry_point},
        )

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

    def _run_phase_with_timeout(
        self,
        phase: str,
        func,
        manifest: PluginManifest,
        timeout_seconds: int,
    ) -> LifecycleLog:
        """执行生命周期阶段（带超时）。

        超时以 LifecycleLog(success=False, error="timeout") 返回。
        """
        start = time.monotonic()
        try:
            func(manifest)
            elapsed = time.monotonic() - start
            if elapsed > timeout_seconds:
                return LifecycleLog(
                    phase=phase,
                    duration_seconds=round(elapsed, 4),
                    success=False,
                    error=f"超时（{timeout_seconds} 秒）",
                )
            return LifecycleLog(
                phase=phase,
                duration_seconds=round(elapsed, 4),
                success=True,
            )
        except Exception as exc:
            return LifecycleLog(
                phase=phase,
                duration_seconds=round(time.monotonic() - start, 4),
                success=False,
                error=f"{type(exc).__name__}: {exc}",
            )
