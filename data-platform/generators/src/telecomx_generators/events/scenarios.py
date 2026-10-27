"""Injectable scenarios: what makes the synthetic data tell a story.

A scenario is a **function of time**, not a list of events. Every stream asks it how
degraded a cell is *right now*, so the metrics, the dropped calls, the failed sessions,
the router CPU and the customer complaints of that cell all degrade together, which is
what a real incident does. Injecting events independently into each stream would give
five unrelated anomalies.

Scenarios also produce a **ground truth**: what was injected, where and when. It is the
only way to evaluate the anomaly and fraud detectors of Phase 3 honestly, since the
events themselves carry no label (the contracts forbid it).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

__all__ = [
    "CELL_DEGRADED",
    "CELL_POST_FIX",
    "CellDegradation",
    "FraudBurst",
    "GroundTruth",
    "Scenario",
]

#: KPI levels of the PRD scenario (section 61): normal -> degraded -> after the fix.
CELL_DEGRADED = {
    "packet_loss_pct": 14.8,
    "latency_ms": 184.0,
    "drop_rate_pct": 12.6,
    "call_setup_failure_pct": 9.0,
    "throughput_factor": 0.30,
    "availability_pct": 98.9,
}
CELL_POST_FIX = {
    "packet_loss_pct": 2.1,
    "latency_ms": 41.0,
    "drop_rate_pct": 1.3,
    "call_setup_failure_pct": 1.6,
    "throughput_factor": 0.95,
    "availability_pct": 100.0,
}


def _smoothstep(x: float) -> float:
    """0 -> 1 with zero slope at both ends: a ramp without visible corners."""
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


@dataclass(frozen=True, slots=True)
class CellDegradation:
    """A radio cell degrading under backhaul congestion, then possibly repaired.

    Phases: normal, ramp to the degraded levels, plateau, and (when ``resolved_at`` is
    set) recovery to the post-fix levels, then settling back towards the cell's own
    baseline. The post-fix levels are slightly worse than the baseline on purpose: a
    repaired link does not return to its exact previous state instantly.
    """

    cell_id: str
    site_id: str
    start: datetime
    ramp_minutes: int = 45
    resolved_at: datetime | None = None
    recovery_minutes: int = 15
    settle_minutes: int = 90
    root_cause: str = "BACKHAUL_CONGESTION"
    router_id: str = "ROUTER-082"

    kind: str = field(default="cell_degradation", init=False)

    def weights(self, when: datetime) -> tuple[float, float]:
        """``(w_degraded, w_post_fix)``; the baseline weight is ``1 - sum``."""
        if when < self.start:
            return 0.0, 0.0
        ramp_end = self.start + timedelta(minutes=self.ramp_minutes)
        if when < ramp_end:
            return _smoothstep((when - self.start) / (ramp_end - self.start)), 0.0
        if self.resolved_at is None or when < self.resolved_at:
            return 1.0, 0.0
        recovery_end = self.resolved_at + timedelta(minutes=self.recovery_minutes)
        if when < recovery_end:
            progress = _smoothstep((when - self.resolved_at) / (recovery_end - self.resolved_at))
            return 1.0 - progress, progress
        settle_end = recovery_end + timedelta(minutes=self.settle_minutes)
        if when < settle_end:
            return 0.0, 1.0 - _smoothstep((when - recovery_end) / (settle_end - recovery_end))
        return 0.0, 0.0

    def intensity(self, when: datetime) -> float:
        """How degraded the cell is (0 normal, 1 fully degraded)."""
        return self.weights(when)[0]

    def truth(self) -> dict[str, Any]:
        ramp_end = self.start + timedelta(minutes=self.ramp_minutes)
        return {
            "scenario": self.kind,
            "cell_id": self.cell_id,
            "site_id": self.site_id,
            "router_id": self.router_id,
            "root_cause": self.root_cause,
            "start": self.start.isoformat(),
            "degraded_from": ramp_end.isoformat(),
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
        }


@dataclass(frozen=True, slots=True)
class FraudBurst:
    """A Mobile Money account takeover: SIM swap, then a burst of rising withdrawals.

    The shape follows PRD section 23: an account whose usual transactions are a few
    thousand XOF suddenly moves 950 000, from another region, to a new counterparty,
    from a new device, shortly after a SIM swap.
    """

    subscriber_index: int
    start: datetime
    sim_swap_lead_minutes: int = 30
    transactions: int = 9
    span_minutes: int = 20
    final_amount: int = 950_000
    destination_region: str = "KDL"

    kind: str = field(default="fraud_burst", init=False)

    def truth(self) -> dict[str, Any]:
        return {
            "scenario": self.kind,
            "subscriber_index": self.subscriber_index,
            "sim_swap_at": (self.start - timedelta(minutes=self.sim_swap_lead_minutes)).isoformat(),
            "start": self.start.isoformat(),
            "end": (self.start + timedelta(minutes=self.span_minutes)).isoformat(),
            "transactions": self.transactions,
            "final_amount": self.final_amount,
        }


#: Any scenario the engine accepts.
Scenario = CellDegradation | FraudBurst


@dataclass(slots=True)
class GroundTruth:
    """Collects what was injected, event by event, while the streams emit it."""

    scenarios: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)

    def record(self, **fields: Any) -> None:
        """Register an injected event once, however many times its bucket is generated."""
        if fields not in self.events:
            self.events.append(fields)
