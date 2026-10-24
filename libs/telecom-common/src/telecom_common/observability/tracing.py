"""OpenTelemetry tracing helpers (ADR-017).

Span naming follows the convention fixed in ADR-017, so that a waterfall is
readable without knowing which component produced each span::

    agent.graph.<name>     an agent execution
    agent.node.<name>      a LangGraph node
    tool.<name>            a tool invocation
    llm.<provider>.<model> an LLM call
    db.trino.query         a Trino query
    ml.predict.<model>     an inference
    pipeline.<name>        a data pipeline run

Degradation rule
----------------
Telemetry must never break the business path. The exporter is configured with
batching and failures are swallowed: a collector outage degrades observability,
it does not degrade the platform (ADR-017, risk A8).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

__all__ = [
    "add_span_attributes",
    "current_trace_id",
    "get_tracer",
    "record_exception",
    "span",
]


def get_tracer(name: str) -> trace.Tracer:
    """Return a tracer for ``name``, typically ``__name__``."""
    return trace.get_tracer(name)


@contextmanager
def span(
    name: str,
    *,
    kind: SpanKind = SpanKind.INTERNAL,
    attributes: Mapping[str, Any] | None = None,
    tracer_name: str = "telecom_common",
) -> Iterator[Span]:
    """Open a span, recording any exception that escapes it.

    The span is marked ``ERROR`` on exception and the exception is re-raised
    unchanged -- instrumentation observes, it never alters control flow.

    Example:
        >>> with span("pipeline.bronze_ingest", attributes={"topic": "cdr.events"}):
        ...     pass
    """
    tracer = get_tracer(tracer_name)
    with tracer.start_as_current_span(name, kind=kind) as active:
        if attributes:
            add_span_attributes(active, attributes)
        try:
            yield active
        except Exception as exc:
            active.set_status(Status(StatusCode.ERROR, str(exc)))
            active.record_exception(exc)
            raise
        else:
            active.set_status(Status(StatusCode.OK))


def add_span_attributes(target: Span, attributes: Mapping[str, Any]) -> None:
    """Set attributes on ``target``, skipping ``None`` values.

    OpenTelemetry rejects ``None`` attribute values; dropping them here lets
    callers pass optional fields without guarding each one.
    """
    for key, value in attributes.items():
        if value is not None:
            target.set_attribute(key, value)


def record_exception(exc: Exception, *, escaped: bool = False) -> None:
    """Record ``exc`` on the active span, if there is one.

    Args:
        exc: the exception to record.
        escaped: whether the exception propagated out of the span's scope.
            Set it when an exception is caught and handled but still worth
            recording -- a swallowed error that nobody sees is how a silent
            failure mode is born.
    """
    active = trace.get_current_span()
    if active.get_span_context().is_valid:
        active.record_exception(exc, escaped=escaped)
        active.set_status(Status(StatusCode.ERROR, str(exc)))


def current_trace_id() -> str | None:
    """Return the active trace id as a 32-character hex string, if any.

    Used to stamp the id onto API error payloads and Langfuse metadata so a
    user-visible failure can be traced back to its spans.
    """
    span_context = trace.get_current_span().get_span_context()
    if not span_context.is_valid:
        return None
    return format(span_context.trace_id, "032x")
