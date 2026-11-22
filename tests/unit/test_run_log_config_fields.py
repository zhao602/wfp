# =============================================================================
# 运行日志配置与常量用例（tests/unit/test_run_log_config_fields.py）
#
# 覆盖验收（tasks T1.5 / design 2.2.3、2.0.1 / INV-D-06）：
#   1. Settings 含全部 log_* 默认值（与 design 2.2.3 配置表一致）；
#   2. .env.example 运行日志段为占位默认值（无真实密钥）；
#   3. fields.py 常量与 design 2.0.1 字段规范一致；
#   4. run_log 包导入零副作用（不落盘、不引入 langgraph）。
# =============================================================================

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_EXAMPLE = REPO_ROOT / ".env.example"

LOG_ENV_KEYS = [
    "LOG_DIR",
    "LOG_ROTATION_ENABLED",
    "LOG_ROTATION_WHEN",
    "LOG_ROTATION_MAX_BYTES",
    "LOG_RETENTION_DAYS",
    "LOG_MAX_PAYLOAD_BYTES",
    "LOG_REDACTION_ENABLED",
    "LOG_ASYNC_ENABLED",
    "LOG_QUEUE_MAX_SIZE",
    "LOG_TENANT_DEFAULT",
]

EXPECTED_ENV_LINES = [
    "LOG_DIR=./logs",
    "LOG_ROTATION_ENABLED=true",
    "LOG_ROTATION_WHEN=midnight",
    "LOG_ROTATION_MAX_BYTES=52428800",
    "LOG_RETENTION_DAYS=14",
    "LOG_MAX_PAYLOAD_BYTES=65536",
    "LOG_REDACTION_ENABLED=true",
    "LOG_ASYNC_ENABLED=true",
    "LOG_QUEUE_MAX_SIZE=10000",
    "LOG_TENANT_DEFAULT=default",
]


class TestRunLogSettingsDefaults:
    """Settings 的 10 个 log_* 字段默认值与 design 2.2.3 一致。"""

    @pytest.fixture
    def settings(self, monkeypatch):
        from orchestration_platform.configuration.settings import Settings

        for key in LOG_ENV_KEYS + ["LOG_LEVEL"]:
            monkeypatch.delenv(key, raising=False)
        return Settings(_env_file=None)

    def test_defaults_match_design_table(self, settings):
        assert settings.log_dir == "./logs"
        assert settings.log_rotation_enabled is True
        assert settings.log_rotation_when == "midnight"
        assert settings.log_rotation_max_bytes == 52428800
        assert settings.log_retention_days == 14
        assert settings.log_max_payload_bytes == 65536
        assert settings.log_redaction_enabled is True
        assert settings.log_async_enabled is True
        assert settings.log_queue_max_size == 10000
        assert settings.log_tenant_default == "default"

    def test_rotation_when_rejects_unknown_value(self, monkeypatch):
        from pydantic import ValidationError

        from orchestration_platform.configuration.settings import Settings

        for key in LOG_ENV_KEYS:
            monkeypatch.delenv(key, raising=False)
        with pytest.raises(ValidationError):
            Settings(_env_file=None, log_rotation_when="bogus")

    def test_get_settings_exposes_run_log_fields(self):
        from orchestration_platform.configuration.settings import get_settings

        instance = get_settings()
        for attr in LOG_ENV_KEYS:
            assert hasattr(instance, attr.lower())


class TestEnvExampleRunLogSection:
    """.env.example 运行日志段含 10 个占位键，值均为默认占位（无真实密钥）。"""

    def test_run_log_section_placeholders(self):
        content = ENV_EXAMPLE.read_text(encoding="utf-8")
        assert "# ---------------- 运行日志（workflow_run_logging） ----------------" in content
        for line in EXPECTED_ENV_LINES:
            assert line in content, f"缺少占位行：{line}"

    def test_placeholder_values_not_secret_like(self):
        allowed = {
            "./logs", "true", "midnight", "52428800", "14",
            "65536", "10000", "default",
        }
        for line in EXPECTED_ENV_LINES:
            value = line.split("=", 1)[1]
            assert value in allowed, f"占位值异常：{line}"


class TestRunLogFieldsConstants:
    """fields.py 常量与 design 2.0.1 字段规范一致。"""

    def test_record_types_and_schema_version(self):
        from orchestration_platform.observability.run_log import fields

        assert fields.SCHEMA_VERSION == "1.0"
        assert fields.RECORD_TYPE_NODE_RUN == "node_run"
        assert fields.RECORD_TYPE_WORKFLOW_RUN == "workflow_run"
        assert set(fields.RECORD_TYPES) == {"node_run", "workflow_run"}

    def test_status_and_level_domains(self):
        from orchestration_platform.observability.run_log import fields

        assert set(fields.NODE_RUN_STATUSES) == {"succeeded", "failed", "timeout"}
        assert set(fields.WORKFLOW_RUN_STATUSES) == {"succeeded", "failed", "running", "unknown"}
        assert set(fields.LEVELS) == {"DEBUG", "INFO", "WARN", "ERROR"}

    def test_business_categories_align_trigger_types(self):
        from orchestration_platform.observability.run_log import fields

        assert set(fields.BUSINESS_CATEGORIES) == {
            "manual", "scheduled", "data_event", "default",
        }

    def test_common_fields_twelve_required(self):
        from orchestration_platform.observability.run_log import fields

        assert len(fields.COMMON_FIELDS) == 12
        assert fields.COMMON_REQUIRED_FIELDS == fields.COMMON_FIELDS
        assert set(fields.COMMON_FIELDS) == {
            "schema_version", "record_type", "run_id", "trace_id", "tenant_id",
            "business_category", "level", "timestamp", "status", "started_at",
            "finished_at", "duration_ms",
        }

    def test_node_run_field_sets(self):
        from orchestration_platform.observability.run_log import fields

        assert set(fields.NODE_RUN_REQUIRED_FIELDS) == {"node_id", "node_type", "round_index", "input"}
        assert set(fields.NODE_RUN_FIELDS).issuperset(set(fields.NODE_RUN_REQUIRED_FIELDS))
        assert set(fields.NODE_ERROR_REQUIRED_FIELDS) == {"error_type", "error_code", "error_message"}
        assert set(fields.NODE_ERROR_FIELDS).issuperset(set(fields.NODE_ERROR_REQUIRED_FIELDS))

    def test_workflow_run_field_sets(self):
        from orchestration_platform.observability.run_log import fields

        assert set(fields.WORKFLOW_RUN_REQUIRED_FIELDS) == {"workflow_id"}
        assert set(fields.WORKFLOW_RUN_FIELDS) == {
            "workflow_id", "definition_version", "trigger_type", "node_summary",
        }
        assert set(fields.NODE_SUMMARY_KEYS) == {"total", "succeeded", "failed", "timeout"}


class TestRunLogPackageImport:
    """run_log 包导入零副作用：不落盘、不引入 langgraph、懒导出可用。"""

    def test_import_package_in_subprocess_no_logs_dir(self, tmp_path):
        src_root = REPO_ROOT / "src"
        code = (
            "import sys\n"
            f"sys.path.insert(0, r'{src_root}')\n"
            "import orchestration_platform.observability.run_log as rl\n"
            "assert 'langgraph' not in sys.modules, 'INV-D-06 violation'\n"
            "from pathlib import Path\n"
            "assert not Path('logs').exists(), 'import created logs/'\n"
            "print('IMPORT_OK')\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        assert "IMPORT_OK" in result.stdout

    def test_lazy_dir_and_unknown_attribute(self):
        import orchestration_platform.observability.run_log as rl

        names = dir(rl)
        for expected in (
            "WorkflowRunLogger", "RunLogSink", "LogPathResolver",
            "RunLogRedactor", "Truncator", "LogRetentionCleaner",
        ):
            assert expected in names
        with pytest.raises(AttributeError):
            getattr(rl, "definitely_not_exported")
