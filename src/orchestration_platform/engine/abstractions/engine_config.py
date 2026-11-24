# =============================================================================
# 引擎类型化配置（engine/abstractions/engine_config.py）
#
# 职责（roadmap B10 / M4 design D1）：engine-owned 运行配置缝隙。
#   - frozen dataclass EngineSettings，17 字段与 configuration.settings 中
#     引擎消费字段同名同类型同默认；
#   - 引擎内禁止服务定位器与 get_settings 回退：宿主（api/worker/cli）经
#     application.engine_wiring.build_engine_settings() 构造后 ctor 注入；
#     组件缺省 None → EngineSettings()（默认值 == settings 默认，对默认
#     部署零行为差异）；
#   - 默认值对齐由 tests/unit/test_engine_settings_parity.py 锁定（防双源漂移）。
# =============================================================================

from dataclasses import dataclass


@dataclass(frozen=True)
class EngineSettings:
    """引擎运行配置（17 字段镜像 configuration.settings 默认值）。

    分组与消费组件（M4 design D1 表）：
      调度器/编译器/适配器 5 项、插件节点 SDK 3 项、代码节点 4 项、
      Agent 节点 4 项、LLM 节点 1 项。
    """

    # ---- 调度器 / 编译器 / 适配器 ----
    parallel_branch_max: int = 10
    workflow_step_guard_enabled: bool = False
    workflow_max_steps: int = 500
    node_default_timeout_seconds: int = 60
    loop_max_iterations: int = 10

    # ---- 插件节点 SDK ----
    plugin_sdk_enabled: bool = False
    plugin_sdk_max_nodes: int = 200
    plugin_sdk_setup_timeout_seconds: int = 30

    # ---- 代码节点 ----
    code_node_enabled: bool = False
    code_execution_timeout_seconds: int = 10
    code_output_max_bytes: int = 1048576
    code_execution_channel: str = "inprocess"

    # ---- Agent 节点 ----
    agent_output_max_retries: int = 2
    agent_max_tool_rounds: int = 5
    structural_lossless_pruning_enabled: bool = False
    llm_fallback_model: str = ""

    # ---- LLM 节点 ----
    llm_node_enabled: bool = False
