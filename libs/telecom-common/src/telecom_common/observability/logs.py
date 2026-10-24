"""Structured JSON logging with trace correlation (NFR-OBS-002).

Every log line carries ``trace_id``, ``span_id``, ``tenant_id`` and ``user_id``
when applicable, so that a line found in Loki can be pivoted to the matching
trace in one click. Without that correlation, diagnosing a slow agent request
means comparing timestamps across six systems by hand -- which in practice
means it is not done.

Module name
-----------
Called ``logs`` rather than ``logging`` to avoid any ambiguity with the
standard library module it wraps.
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any, Final

from opentelemetry import trace

from telecom_common.security.pii import redact

__all__ = [
    "JsonFormatter",
    "bind_context",
    "clear_context",
    "configure_logging",
    "get_logger",
]

# Correlation fields propagate through async tasks and threads without being
# threaded through every function signature.
_tenant_id: ContextVar[str | None] = ContextVar("tenant_id", default=None)
_user_id: ContextVar[str | None] = ContextVar("user_id", default=None)
_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)

#: Attributes present on every LogRecord; anything else is caller-supplied
#: and promoted into the JSON payload.
_STANDARD_ATTRS: Final[frozenset[str]] = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    }
)


def bind_context(
    *,
    tenant_id: str | None = None,
    user_id: str | None = None,
    request_id: str | None = None,
) -> None:
    """Attach correlation identifiers to the current execution context.

    Call once per request or per message, typically in a middleware or at the
    top of a consumer loop. Every subsequent log line inherits the values.
    """
    if tenant_id is not None:
        _tenant_id.set(tenant_id)
    if user_id is not None:
        _user_id.set(user_id)
    if request_id is not None:
        _request_id.set(request_id)


def clear_context() -> None:
    """Reset correlation identifiers.

    Important in a worker that reuses threads: without this, one request's
    tenant can leak into the next request's log lines.
    """
    _tenant_id.set(None)
    _user_id.set(None)
    _request_id.set(None)


class JsonFormatter(logging.Formatter):
    """Render log records as single-line JSON.

    Args:
        redact_pii: when true, scan the rendered message for personal
            identifiers and replace them. Defaults to on -- a log file is an
            output boundary like any other (NFR-SEC-010), and the cost of a
            regex pass is far below the cost of a MSISDN in Loki.
    """

    def __init__(self, *, redact_pii: bool = True) -> None:
        super().__init__()
        self._redact_pii = redact_pii

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        if self._redact_pii:
            message = redact(message)

        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": message,
        }

        # Trace correlation, taken from the active OTel span when there is one.
        span = trace.get_current_span()
        span_context = span.get_span_context()
        if span_context.is_valid:
            payload["trace_id"] = format(span_context.trace_id, "032x")
            payload["span_id"] = format(span_context.span_id, "016x")

        for key, var in (
            ("tenant_id", _tenant_id),
            ("user_id", _user_id),
            ("request_id", _request_id),
        ):
            value = var.get()
            if value is not None:
                payload[key] = value

        # Caller-supplied structured fields: logger.info("...", extra={"cell_id": ...})
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        # default=str so that a datetime or Decimal in `extra` degrades to a
        # string instead of killing the log call it was meant to document.
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(
    *,
    level: str = "INFO",
    service_name: str = "telecomx",
    redact_pii: bool = True,
) -> None:
    """Install the JSON formatter on the root logger.

    Idempotent: existing handlers are replaced, so calling it twice (a library
    import plus an application entry point) does not duplicate every line.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(redact_pii=redact_pii))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Third-party loggers are noisy at INFO and drown the signal.
    for noisy in ("urllib3", "botocore", "s3transfer", "kafka", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logging.getLogger(__name__).debug(
        "logging configured", extra={"service_name": service_name, "level": level}
    )


def get_logger(name: str) -> logging.Logger:
    """Return a logger for ``name``.

    Thin wrapper over :func:`logging.getLogger`, kept so that call sites import
    from one place and the implementation can change without touching them.
    """
    return logging.getLogger(name)
