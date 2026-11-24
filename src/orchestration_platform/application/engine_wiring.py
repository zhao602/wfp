# =============================================================================
# 引擎装配辅助（application/engine_wiring.py）
#
# 职责（roadmap M4 B10 / design D2）：宿主配置 Settings → 引擎类型化配置
# EngineSettings 的单点显式映射。
# 设计意图：
#   1. api/worker/cli 三宿主共用单一导入点，装配层收敛；
#   2. 依赖 DAG 干净：仅 configuration + engine.abstractions，无 api；
#   3. 不缓存——get_settings() 自身 lru_cache 单例，每次调用读同一实例；
#   4. get_settings 延迟到调用期解析（T7 加固）：模块级 from-import 会在
#      首次导入时冻结绑定——若首导入发生在测试 patch
#      configuration.settings.get_settings 的上下文内（如先跑
#      test_executor_registry_wiring 再跑 test_scheduler_suspend 的子集
#      运行），Mock 将被永久绑定、污染此后全部 build_engine_settings()
#      产出（实测 EngineSettings 字段变 MagicMock）。调用期解析使 patch
#      仅在其作用期内生效、退出后自动还原真实配置。
# =============================================================================

from orchestration_platform.engine.abstractions.engine_config import EngineSettings


def build_engine_settings() -> EngineSettings:
    """读取宿主全局配置并显式映射为 EngineSettings（17 字段）。

    env 覆盖经 get_settings() 生效（.env + OS env，M1 口径）；引擎组件
    不感知 configuration 模块（B10 门禁域约束），本函数是唯一映射点。
    """
    from orchestration_platform.configuration.settings import get_settings

    settings = get_settings()
    return EngineSettings(
        parallel_branch_max=settings.parallel_branch_max,
        workflow_step_guard_enabled=settings.workflow_step_guard_enabled,
        workflow_max_steps=settings.workflow_max_steps,
        node_default_timeout_seconds=settings.node_default_timeout_seconds,
        loop_max_iterations=settings.loop_max_iterations,
        plugin_sdk_enabled=settings.plugin_sdk_enabled,
        plugin_sdk_max_nodes=settings.plugin_sdk_max_nodes,
        plugin_sdk_setup_timeout_seconds=settings.plugin_sdk_setup_timeout_seconds,
        code_node_enabled=settings.code_node_enabled,
        code_execution_timeout_seconds=settings.code_execution_timeout_seconds,
        code_output_max_bytes=settings.code_output_max_bytes,
        code_execution_channel=settings.code_execution_channel,
        agent_output_max_retries=settings.agent_output_max_retries,
        agent_max_tool_rounds=settings.agent_max_tool_rounds,
        structural_lossless_pruning_enabled=settings.structural_lossless_pruning_enabled,
        llm_fallback_model=settings.llm_fallback_model,
        llm_node_enabled=settings.llm_node_enabled,
    )
