"""Typed configuration shared by every component.

Scope rule: this module holds only settings that are genuinely *shared*
(environment, logging, tenant, the tokenization key). Component-specific
settings -- Kafka brokers, Trino host, LLM models -- belong to the component
that uses them, which subclasses :class:`BaseConfig`.

Putting every setting here would make the shared library the place where an
unrelated change to the agent service breaks the Spark job.

Secrets are read from the environment only (NFR-SEC-007). They are typed as
:class:`~pydantic.SecretStr` so that an accidental log line or traceback
prints ``**********`` rather than the value.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from telecom_common.domain.enums import TenantId

__all__ = [
    "BaseConfig",
    "Environment",
    "LogLevel",
    "PlatformConfig",
    "get_config",
]


class Environment(StrEnum):
    """Deployment environment.

    Several guards key off this value -- notably the refusal to run with
    ``AUTH_MODE=dev`` outside development (ADR-012).
    """

    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"

    @property
    def is_production_like(self) -> bool:
        """Whether relaxed development behaviour must be refused."""
        return self in {Environment.STAGING, Environment.PRODUCTION}


class LogLevel(StrEnum):
    """Standard logging levels."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class BaseConfig(BaseSettings):
    """Base class for every component's configuration.

    Subclass it to add component-specific settings; the shared behaviour
    (``.env`` loading, case-insensitive names) comes for free.

    ``extra="ignore"`` is required, not a convenience. The repository has **one**
    ``.env`` shared by every component (Kafka, MinIO, Trino, the LLM gateway...), and
    each component's configuration models only its own subset. With
    ``extra="forbid"``, pydantic-settings rejects every variable of that file the
    model does not know, so the project's own ``.env.example`` could not be loaded
    (``Extra inputs are not permitted``). The process environment is ignored in the
    same way, for the same reason: it holds hundreds of unrelated variables.

    Two consequences, stated rather than hidden:

    * a typo in a *constructor* argument is no longer rejected at runtime; it is
      caught statically instead, since ``mypy`` flags unknown arguments of a
      pydantic model (pinned by a test that relies on ``warn_unused_ignores``);
    * a typo in a *variable name* (``KAFKA_BOOTSRAP_SERVERS``) is silently ignored.
      Guarding against it would need an ``env_prefix`` and an explicit scan of the
      environment, which is not worth the cost: values are validated where they
      are used, and a missing one raises there.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


class PlatformConfig(BaseConfig):
    """Settings shared by all components."""

    env: Environment = Field(
        default=Environment.DEVELOPMENT,
        description="Deployment environment.",
    )
    log_level: LogLevel = Field(
        default=LogLevel.INFO,
        description="Minimum level emitted by the structured logger.",
    )
    service_name: str = Field(
        default="telecomx",
        description="Logical service name, used as the OTel service.name resource.",
    )
    default_tenant_id: TenantId = Field(
        default=TenantId.MALI,
        description="Tenant assumed when none is supplied (development only).",
    )

    pii_tokenization_key: SecretStr | None = Field(
        default=None,
        description=(
            "HMAC key for PII tokenization (ADR-013). Required wherever "
            "tokenization runs; absent elsewhere, because the read path must "
            "never hold it."
        ),
    )
    pii_key_version: str = Field(
        default="v1",
        description="Key version embedded in emitted tokens, enabling rotation.",
    )

    otel_exporter_otlp_endpoint: str | None = Field(
        default=None,
        description="OTLP collector endpoint. Telemetry is disabled when unset.",
    )
    otel_redact_prompts: bool = Field(
        default=False,
        description="Strip LLM prompt contents before export (default on in production).",
    )

    @field_validator("pii_key_version")
    @classmethod
    def _validate_key_version(cls, value: str) -> str:
        if not (value.startswith("v") and value[1:].isdigit()):
            msg = f"pii_key_version must look like 'v1', got {value!r}"
            raise ValueError(msg)
        return value

    @property
    def tokenization_key_bytes(self) -> bytes:
        """Return the tokenization key as bytes.

        Raises:
            RuntimeError: if the key is absent. Failing loudly here is the
                point: a component that silently skipped tokenization because
                a variable was unset would write MSISDNs into silver.
        """
        if self.pii_tokenization_key is None:
            msg = (
                "PII_TOKENIZATION_KEY is not set. Refusing to continue: "
                "a component that tokenizes must hold the key (ADR-013)."
            )
            raise RuntimeError(msg)
        return self.pii_tokenization_key.get_secret_value().encode("utf-8")


@lru_cache(maxsize=1)
def get_config() -> PlatformConfig:
    """Return the process-wide configuration, parsed once.

    Cached because configuration is immutable for the lifetime of a process.
    Tests that need a different configuration should instantiate
    :class:`PlatformConfig` directly rather than clearing this cache.
    """
    return PlatformConfig()
