"""Instrumentation: structured logs, business metrics and tracing.

Written alongside the code it observes, not retrofitted (principle P7).
"""

from __future__ import annotations

from telecom_common.observability.logs import (
    JsonFormatter,
    bind_context,
    clear_context,
    configure_logging,
    get_logger,
)
from telecom_common.observability.metrics import (
    LATENCY_BUCKETS,
    PlatformMetrics,
    get_metrics,
)
from telecom_common.observability.tracing import (
    add_span_attributes,
    current_trace_id,
    get_tracer,
    record_exception,
    span,
)

__all__ = [
    "LATENCY_BUCKETS",
    "JsonFormatter",
    "PlatformMetrics",
    "add_span_attributes",
    "bind_context",
    "clear_context",
    "configure_logging",
    "current_trace_id",
    "get_logger",
    "get_metrics",
    "get_tracer",
    "record_exception",
    "span",
]
