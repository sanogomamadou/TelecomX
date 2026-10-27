"""Tests for typed platform configuration."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from telecom_common.config import Environment, LogLevel, PlatformConfig
from telecom_common.domain.enums import TenantId

pytestmark = pytest.mark.unit


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate from the developer's real .env and exported variables."""
    for name in (
        "ENV",
        "LOG_LEVEL",
        "SERVICE_NAME",
        "DEFAULT_TENANT_ID",
        "PII_TOKENIZATION_KEY",
        "PII_KEY_VERSION",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_REDACT_PROMPTS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir("/")  # avoid picking up the repository .env


@pytest.mark.usefixtures("clean_env")
class TestDefaults:
    def test_defaults_to_development(self) -> None:
        assert PlatformConfig().env is Environment.DEVELOPMENT

    def test_default_log_level_is_info(self) -> None:
        assert PlatformConfig().log_level is LogLevel.INFO

    def test_default_tenant(self) -> None:
        assert PlatformConfig().default_tenant_id is TenantId.MALI

    def test_telemetry_disabled_when_no_endpoint(self) -> None:
        assert PlatformConfig().otel_exporter_otlp_endpoint is None


@pytest.mark.usefixtures("clean_env")
class TestEnvironmentParsing:
    def test_reads_from_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ENV", "production")
        monkeypatch.setenv("LOG_LEVEL", "ERROR")
        config = PlatformConfig()
        assert config.env is Environment.PRODUCTION
        assert config.log_level is LogLevel.ERROR

    def test_is_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("env", "staging")
        assert PlatformConfig().env is Environment.STAGING

    def test_rejects_unknown_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ENV", "preprod")
        with pytest.raises(ValidationError):
            PlatformConfig()

    def test_unknown_constructor_argument_is_flagged_by_mypy_not_at_runtime(self) -> None:
        """mypy reports the unknown argument (hence the `type: ignore`); at runtime it is ignored.

        `mypy tests/unit/telecom_common/test_config.py` fails with an unused-ignore error if
        mypy ever stops reporting it. CI type-checks libs/ and the generators only.
        """
        config = PlatformConfig(kafka_bootsrap_servers="localhost:9092")  # type: ignore[call-arg]
        assert config.env is Environment.DEVELOPMENT

    def test_ignores_unmapped_environment_variable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Documents a known limitation rather than pretending it does not exist.

        A mistyped environment variable is NOT caught: the process environment
        holds hundreds of variables belonging to other tools, and failing on
        them would make the application unstartable. Missing values are caught
        at the point of use instead (see tokenization_key_bytes).
        """
        monkeypatch.setenv("KAFKA_BOOTSRAP_SERVERS", "localhost:9092")
        assert PlatformConfig().env is Environment.DEVELOPMENT


class TestEnvironmentFlags:
    @pytest.mark.parametrize(
        ("env", "expected"),
        [
            (Environment.DEVELOPMENT, False),
            (Environment.STAGING, True),
            (Environment.PRODUCTION, True),
        ],
    )
    def test_is_production_like(self, env: Environment, expected: bool) -> None:
        """Guards such as the AUTH_MODE=dev refusal key off this (ADR-012)."""
        assert env.is_production_like is expected


@pytest.mark.usefixtures("clean_env")
class TestTokenizationKey:
    def test_absent_key_raises_rather_than_silently_skipping(self) -> None:
        """Silently skipping tokenization would write MSISDNs into silver."""
        with pytest.raises(RuntimeError, match="PII_TOKENIZATION_KEY is not set"):
            _ = PlatformConfig().tokenization_key_bytes

    def test_returns_key_as_bytes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PII_TOKENIZATION_KEY", "x" * 32)
        assert PlatformConfig().tokenization_key_bytes == b"x" * 32

    def test_key_is_not_exposed_in_repr(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SecretStr keeps the value out of logs and tracebacks."""
        monkeypatch.setenv("PII_TOKENIZATION_KEY", "super-secret-key-material-here!!")
        assert "super-secret" not in repr(PlatformConfig())

    def test_rejects_malformed_key_version(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PII_KEY_VERSION", "version-one")
        with pytest.raises(ValidationError):
            PlatformConfig()

    def test_accepts_rotated_key_version(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PII_KEY_VERSION", "v2")
        assert PlatformConfig().pii_key_version == "v2"


class TestDotenvFile:
    """Regression: one .env serves every component, so it holds variables a given
    component's model does not know. extra="forbid" made such a file unloadable."""

    def test_variables_the_model_does_not_know_are_ignored(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        content = "ENV=staging\nKAFKA_BOOTSTRAP_SERVERS=kafka:19092\n"
        (tmp_path / ".env").write_text(content, encoding="utf-8")
        monkeypatch.delenv("ENV", raising=False)
        monkeypatch.chdir(tmp_path)
        assert PlatformConfig().env is Environment.STAGING

    def test_the_repository_template_loads(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        template = Path(__file__).resolve().parents[3] / ".env.example"
        (tmp_path / ".env").write_bytes(template.read_bytes())
        for name in ("ENV", "LOG_LEVEL", "DEFAULT_TENANT_ID", "PII_KEY_VERSION"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.chdir(tmp_path)
        config = PlatformConfig()
        assert config.env is Environment.DEVELOPMENT
        assert config.default_tenant_id is TenantId.MALI
