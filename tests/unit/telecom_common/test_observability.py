"""Tests for structured logging, metrics and tracing helpers."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from prometheus_client import CollectorRegistry

from telecom_common.config import get_config
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
    current_trace_id,
    record_exception,
    span,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_log_context() -> Iterator[None]:
    """Correlation state must not leak between tests, as it must not between requests."""
    clear_context()
    yield
    clear_context()


def _emit(formatter: JsonFormatter, message: str, **extra: object) -> dict[str, object]:
    """Format one record through ``formatter`` and return the parsed payload."""
    record = logging.LogRecord(
        name="test.logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return json.loads(formatter.format(record))


class TestJsonFormatter:
    def test_emits_valid_json(self) -> None:
        payload = _emit(JsonFormatter(), "pipeline started")
        assert payload["message"] == "pipeline started"
        assert payload["level"] == "INFO"
        assert payload["logger"] == "test.logger"

    def test_timestamp_is_utc_iso8601(self) -> None:
        payload = _emit(JsonFormatter(), "x")
        assert str(payload["timestamp"]).endswith("+00:00")

    def test_promotes_extra_fields(self) -> None:
        """Structured fields must be queryable, not buried in the message string."""
        payload = _emit(JsonFormatter(), "cell degraded", cell_id="CELL-0421", drop_rate=12.6)
        assert payload["cell_id"] == "CELL-0421"
        assert payload["drop_rate"] == 12.6

    def test_serialises_unsupported_types_instead_of_failing(self) -> None:
        """A bad `extra` value must not kill the log call it was meant to document."""
        payload = _emit(JsonFormatter(), "x", weird=object())
        assert "object object" in str(payload["weird"])

    def test_includes_exception_when_present(self) -> None:
        try:
            msg = "backhaul unreachable"
            raise RuntimeError(msg)
        except RuntimeError:
            record = logging.LogRecord(
                name="t",
                level=logging.ERROR,
                pathname=__file__,
                lineno=1,
                msg="failed",
                args=(),
                exc_info=True,
            )
            import sys

            record.exc_info = sys.exc_info()
            payload = json.loads(JsonFormatter().format(record))
        assert "RuntimeError" in str(payload["exception"])


class TestLogPiiRedaction:
    def test_redacts_by_default(self) -> None:
        """A log file is an output boundary like any other (NFR-SEC-010)."""
        payload = _emit(JsonFormatter(), "call from +22370123456 dropped")
        assert "+22370123456" not in str(payload["message"])
        assert "[MSISDN]" in str(payload["message"])

    def test_can_be_disabled(self) -> None:
        payload = _emit(JsonFormatter(redact_pii=False), "call from +22370123456")
        assert "+22370123456" in str(payload["message"])

    def test_keeps_non_pii_content_intact(self) -> None:
        payload = _emit(JsonFormatter(), "CELL-0421 drop_rate 12.6 %")
        assert payload["message"] == "CELL-0421 drop_rate 12.6 %"


class TestLogContext:
    def test_binds_correlation_fields(self) -> None:
        bind_context(tenant_id="telecomx-ml", user_id="awa.noc", request_id="req-1")
        payload = _emit(JsonFormatter(), "x")
        assert payload["tenant_id"] == "telecomx-ml"
        assert payload["user_id"] == "awa.noc"
        assert payload["request_id"] == "req-1"

    def test_absent_fields_are_omitted_not_null(self) -> None:
        payload = _emit(JsonFormatter(), "x")
        assert "tenant_id" not in payload
        assert "user_id" not in payload

    def test_clear_prevents_leakage_between_requests(self) -> None:
        """A worker reusing threads must not carry one tenant into the next request."""
        bind_context(tenant_id="telecomx-ml")
        clear_context()
        assert "tenant_id" not in _emit(JsonFormatter(), "x")

    def test_partial_bind_leaves_others_untouched(self) -> None:
        bind_context(tenant_id="telecomx-ml")
        bind_context(user_id="awa.noc")
        payload = _emit(JsonFormatter(), "x")
        assert payload["tenant_id"] == "telecomx-ml"
        assert payload["user_id"] == "awa.noc"


class TestConfigureLogging:
    def test_installs_single_handler(self) -> None:
        configure_logging(level="DEBUG")
        root = logging.getLogger()
        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0].formatter, JsonFormatter)

    def test_is_idempotent(self) -> None:
        """Called from both a library import and an entry point, lines must not double."""
        configure_logging()
        configure_logging()
        assert len(logging.getLogger().handlers) == 1

    def test_quietens_noisy_third_parties(self) -> None:
        configure_logging(level="DEBUG")
        assert logging.getLogger("urllib3").level == logging.WARNING

    def test_get_logger_returns_named_logger(self) -> None:
        assert get_logger("telecomx.test").name == "telecomx.test"


class TestMetrics:
    def test_metrics_register_on_a_custom_registry(self) -> None:
        registry = CollectorRegistry()
        metrics = PlatformMetrics(registry=registry)
        metrics.events_produced.labels(topic="cdr.events", tenant_id="telecomx-ml").inc(3)
        value = registry.get_sample_value(
            "telecomx_events_produced_total",
            {"topic": "cdr.events", "tenant_id": "telecomx-ml"},
        )
        assert value == 3

    def test_two_instances_on_separate_registries_do_not_clash(self) -> None:
        """Module-level metric globals would make this impossible, hence the class."""
        PlatformMetrics(registry=CollectorRegistry())
        PlatformMetrics(registry=CollectorRegistry())

    def test_gauge_can_go_down(self) -> None:
        registry = CollectorRegistry()
        metrics = PlatformMetrics(registry=registry)
        metrics.kafka_consumer_lag.labels(topic="cdr.events", consumer_group="g1").set(12_000)
        metrics.kafka_consumer_lag.labels(topic="cdr.events", consumer_group="g1").set(40)
        value = registry.get_sample_value(
            "telecomx_kafka_consumer_lag",
            {"topic": "cdr.events", "consumer_group": "g1"},
        )
        assert value == 40

    def test_latency_buckets_cover_the_slo_thresholds(self) -> None:
        """A p95 is only as precise as the nearest bucket boundary."""
        for slo in (0.2, 8.0, 20.0):  # ML inference, simple agent, supervisor
            assert slo in LATENCY_BUCKETS

    def test_buckets_are_monotonically_increasing(self) -> None:
        assert list(LATENCY_BUCKETS) == sorted(LATENCY_BUCKETS)


class TestTracing:
    def test_span_context_manager_yields_a_span(self) -> None:
        with span("pipeline.test") as active:
            assert active is not None

    def test_span_reraises_exceptions_unchanged(self) -> None:
        """Instrumentation observes; it never alters control flow."""
        with pytest.raises(ValueError, match="boom"), span("pipeline.test"):
            msg = "boom"
            raise ValueError(msg)

    def test_span_accepts_attributes_and_skips_none(self) -> None:
        with span("tool.query", attributes={"cell_id": "CELL-0421", "absent": None}):
            pass

    def test_current_trace_id_is_none_without_sdk(self) -> None:
        """Without a configured SDK the API is a no-op, and that must be safe."""
        assert current_trace_id() is None


@pytest.fixture
def otel_sdk() -> Iterator[InMemorySpanExporter]:
    """Install a real in-memory tracer provider for the duration of a test.

    The OpenTelemetry API is a no-op until an SDK is configured, so the
    correlation paths cannot be exercised without one. The provider is global
    and cannot be unset, so the fixture restores the previous one by hand.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    previous = trace.get_tracer_provider()
    trace._TRACER_PROVIDER = provider
    yield exporter
    trace._TRACER_PROVIDER = previous


@pytest.mark.usefixtures("otel_sdk")
class TestTraceCorrelation:
    """NFR-OBS-002: a log line must be pivotable to its trace in one click."""

    def test_log_carries_trace_and_span_id_inside_a_span(self) -> None:
        with span("pipeline.test"):
            payload = _emit(JsonFormatter(), "ingesting batch")
        assert len(str(payload["trace_id"])) == 32
        assert len(str(payload["span_id"])) == 16

    def test_log_outside_a_span_has_no_trace_fields(self) -> None:
        payload = _emit(JsonFormatter(), "startup")
        assert "trace_id" not in payload

    def test_log_trace_id_matches_current_trace_id(self) -> None:
        """The id in the log must be the one an operator looks up in Tempo."""
        with span("pipeline.test"):
            payload = _emit(JsonFormatter(), "x")
            assert payload["trace_id"] == current_trace_id()

    def test_nested_spans_share_a_trace_but_differ_by_span(self) -> None:
        with span("agent.graph.network"):
            outer = _emit(JsonFormatter(), "outer")
            with span("agent.node.plan"):
                inner = _emit(JsonFormatter(), "inner")
        assert outer["trace_id"] == inner["trace_id"]
        assert outer["span_id"] != inner["span_id"]

    def test_span_records_exception_and_error_status(self, otel_sdk: InMemorySpanExporter) -> None:
        with pytest.raises(ValueError, match="backhaul"), span("tool.run_diagnostic"):
            msg = "backhaul unreachable"
            raise ValueError(msg)
        finished = otel_sdk.get_finished_spans()
        assert finished[0].status.status_code is StatusCode.ERROR
        assert finished[0].events[0].name == "exception"

    def test_successful_span_is_marked_ok(self, otel_sdk: InMemorySpanExporter) -> None:
        with span("pipeline.ok"):
            pass
        assert otel_sdk.get_finished_spans()[0].status.status_code is StatusCode.OK

    def test_attributes_are_set_and_none_is_skipped(self, otel_sdk: InMemorySpanExporter) -> None:
        with span("tool.query", attributes={"cell_id": "CELL-0421", "absent": None}):
            pass
        attributes = otel_sdk.get_finished_spans()[0].attributes or {}
        assert attributes["cell_id"] == "CELL-0421"
        assert "absent" not in attributes

    def test_record_exception_on_handled_error(self, otel_sdk: InMemorySpanExporter) -> None:
        """A swallowed error that nobody sees is how a silent failure mode is born."""
        with span("tool.optional"):
            try:
                msg = "model unavailable"
                raise RuntimeError(msg)
            except RuntimeError as exc:
                record_exception(exc, escaped=False)
        finished = otel_sdk.get_finished_spans()[0]
        assert any(event.name == "exception" for event in finished.events)


class TestSingletons:
    def test_get_metrics_returns_the_same_instance(self) -> None:
        assert get_metrics() is get_metrics()

    def test_get_config_is_cached(self) -> None:
        assert get_config() is get_config()
