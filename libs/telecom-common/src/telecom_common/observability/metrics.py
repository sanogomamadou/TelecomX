"""Business metrics exported to Prometheus (ADR-017).

Why these live here, in Phase 1
-------------------------------
The dashboards that consume them only arrive in Phase 7. Defining the metrics
now costs minutes; retrofitting instrumentation into a finished pipeline costs
days, and in practice never happens -- which is how systems end up
unobservable in production.

Why a class rather than module-level globals
--------------------------------------------
``prometheus_client`` raises ``Duplicated timeseries`` when the same metric is
registered twice on a registry. Module-level globals make that unavoidable in
tests, which import repeatedly and want isolation. Binding metrics to an
explicit registry keeps tests independent and lets a process expose several
registries if it ever needs to.
"""

from __future__ import annotations

from typing import Final

from prometheus_client import REGISTRY, CollectorRegistry, Counter, Gauge, Histogram

__all__ = [
    "LATENCY_BUCKETS",
    "PlatformMetrics",
    "get_metrics",
]

#: Buckets tuned to the platform's SLOs rather than the library defaults.
#: The SLO thresholds that matter -- 0.2 s for ML inference, 8 s for a simple
#: agent, 20 s for the supervisor -- each need a bucket boundary nearby, or the
#: p95 computed from the histogram is too coarse to tell pass from fail.
LATENCY_BUCKETS: Final = (
    0.01,
    0.05,
    0.1,
    0.2,
    0.5,
    1.0,
    2.0,
    5.0,
    8.0,
    15.0,
    20.0,
    30.0,
    60.0,
)


class PlatformMetrics:
    """Every business metric the platform exports.

    One instance per process. Pass a dedicated :class:`CollectorRegistry` in
    tests to avoid clashing with the global default registry.
    """

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        reg = registry if registry is not None else REGISTRY

        # --- Ingestion and streaming (Phase 1) -----------------------------
        self.events_produced = Counter(
            "telecomx_events_produced_total",
            "Events published to Kafka by the synthetic generators.",
            ["topic", "tenant_id"],
            registry=reg,
        )
        self.events_consumed = Counter(
            "telecomx_events_consumed_total",
            "Events consumed from Kafka.",
            ["topic", "consumer_group"],
            registry=reg,
        )
        self.kafka_consumer_lag = Gauge(
            "telecomx_kafka_consumer_lag",
            "Messages behind the log end offset. Health indicator number one.",
            ["topic", "consumer_group"],
            registry=reg,
        )
        self.dlq_messages = Counter(
            "telecomx_dlq_messages_total",
            "Messages routed to a dead-letter queue, by rejection reason.",
            ["topic", "reason"],
            registry=reg,
        )

        # --- Data quality and freshness (Phase 1-2) ------------------------
        self.data_freshness_seconds = Gauge(
            "telecomx_data_freshness_seconds",
            "Age of the most recent record in a table.",
            ["table", "zone"],
            registry=reg,
        )
        self.dq_violations = Counter(
            "telecomx_dq_violations_total",
            "Data quality rule violations, by rule and severity.",
            ["table", "rule", "severity"],
            registry=reg,
        )
        self.records_written = Counter(
            "telecomx_records_written_total",
            "Records written to a lakehouse table.",
            ["table", "zone"],
            registry=reg,
        )
        self.pipeline_duration_seconds = Histogram(
            "telecomx_pipeline_duration_seconds",
            "Wall-clock duration of a pipeline run.",
            ["pipeline", "status"],
            buckets=LATENCY_BUCKETS,
            registry=reg,
        )

        # --- ML platform (Phase 3) -----------------------------------------
        self.model_prediction_latency_seconds = Histogram(
            "telecomx_model_prediction_latency_seconds",
            "Inference latency.",
            ["model", "version"],
            buckets=LATENCY_BUCKETS,
            registry=reg,
        )
        self.model_predictions = Counter(
            "telecomx_model_predictions_total",
            "Predictions served.",
            ["model", "version", "status"],
            registry=reg,
        )
        self.model_drift_score = Gauge(
            "telecomx_model_drift_score",
            "Drift score per feature (PSI). Alerts above 0.25.",
            ["model", "feature"],
            registry=reg,
        )

        # --- Agent platform (Phase 4) --------------------------------------
        self.agent_executions = Counter(
            "telecomx_agent_executions_total",
            "Agent graph executions, by outcome.",
            ["agent", "status"],
            registry=reg,
        )
        self.agent_duration_seconds = Histogram(
            "telecomx_agent_duration_seconds",
            "End-to-end agent execution time.",
            ["agent"],
            buckets=LATENCY_BUCKETS,
            registry=reg,
        )
        self.agent_tool_calls = Counter(
            "telecomx_agent_tool_calls_total",
            "Tool invocations, by tool and outcome.",
            ["agent", "tool", "status"],
            registry=reg,
        )
        self.llm_tokens = Counter(
            "telecomx_llm_tokens_total",
            "Tokens consumed, split by direction.",
            ["provider", "model", "direction"],
            registry=reg,
        )
        self.llm_cost_eur = Counter(
            "telecomx_llm_cost_eur_total",
            "Cumulative LLM spend in EUR.",
            ["provider", "model"],
            registry=reg,
        )
        self.llm_cache_hits = Counter(
            "telecomx_llm_cache_hits_total",
            "Semantic cache outcomes in the LLM gateway.",
            ["provider", "result"],
            registry=reg,
        )
        self.approvals_pending = Gauge(
            "telecomx_approvals_pending",
            "Human approval requests awaiting a decision, by risk class.",
            ["risk_class"],
            registry=reg,
        )

        # --- Security (cross-cutting) --------------------------------------
        self.authz_denials = Counter(
            "telecomx_authz_denials_total",
            "Authorization refusals. A spike signals probing or a broken role.",
            ["subject_role", "resource", "reason"],
            registry=reg,
        )
        self.pii_detections = Counter(
            "telecomx_pii_detections_total",
            "PII found at an output boundary. Any non-zero value is a defect.",
            ["surface", "kind"],
            registry=reg,
        )


_default: PlatformMetrics | None = None


def get_metrics() -> PlatformMetrics:
    """Return the process-wide metrics instance, created on first use.

    Tests should construct :class:`PlatformMetrics` with their own registry
    instead of calling this.
    """
    global _default
    if _default is None:
        _default = PlatformMetrics()
    return _default
