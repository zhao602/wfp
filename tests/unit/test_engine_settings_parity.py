# =============================================================================
# EngineSettings 默认值对齐守护（tests/unit/test_engine_settings_parity.py）
#
# 职责（M4 design D1）：防双源漂移——configuration/settings.py 改默认而
# engine/abstractions/engine_config.py 未同步时本测试红。
#
# 口径：Settings(_env_file=None)（跳 .env）+ 清 17 字段对应 OS env
# （Settings case_sensitive=False，大小写变体均清）后逐字段比对默认值。
# =============================================================================

import dataclasses

import pytest

from orchestration_platform.configuration.settings import Settings
from orchestration_platform.engine.abstractions.engine_config import EngineSettings

# 17 字段清单（与 engine_config.py 一一对应；增删字段须同步本清单）
ENGINE_SETTINGS_FIELDS = [
    "parallel_branch_max",
    "workflow_step_guard_enabled",
    "workflow_max_steps",
    "node_default_timeout_seconds",
    "loop_max_iterations",
    "plugin_sdk_enabled",
    "plugin_sdk_max_nodes",
    "plugin_sdk_setup_timeout_seconds",
    "code_node_enabled",
    "code_execution_timeout_seconds",
    "code_output_max_bytes",
    "code_execution_channel",
    "agent_output_max_retries",
    "agent_max_tool_rounds",
    "structural_lossless_pruning_enabled",
    "llm_fallback_model",
    "llm_node_enabled",
]


@pytest.fixture()
def clean_env(monkeypatch):
    """清除 17 字段对应 OS env（大写与原样两种形态；Settings 大小写不敏感）。"""
    for name in ENGINE_SETTINGS_FIELDS:
        monkeypatch.delenv(name.upper(), raising=False)
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


@pytest.mark.parametrize("field_name", ENGINE_SETTINGS_FIELDS)
def test_engine_settings_default_matches_settings(clean_env, field_name):
    """逐字段：EngineSettings 默认 == Settings(_env_file=None) 默认。"""
    settings = Settings(_env_file=None)
    assert getattr(EngineSettings(), field_name) == getattr(settings, field_name)


def test_engine_settings_field_set_is_locked():
    """字段集合守护：dataclass 字段与清单双向一致（防单侧漂移）。"""
    dataclass_fields = {field.name for field in dataclasses.fields(EngineSettings)}
    assert dataclass_fields == set(ENGINE_SETTINGS_FIELDS)
    settings_fields = set(Settings.model_fields)
    assert set(ENGINE_SETTINGS_FIELDS) <= settings_fields


def test_engine_settings_is_frozen():
    """frozen 守护：运行期不可变（design D1）。"""
    with pytest.raises(dataclasses.FrozenInstanceError):
        EngineSettings().parallel_branch_max = 99
