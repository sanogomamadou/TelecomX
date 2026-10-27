"""Configuration of the event generators."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

__all__ = ["PRODUCTION_RATES", "GeneratorConfig"]

#: Production message rates (PRD section 2.2), messages per second, as daily averages.
#: ``network.metrics`` and ``device.events`` are periodic and handled by entity count,
#: not by rate: see :class:`GeneratorConfig`.
PRODUCTION_RATES: Final[dict[str, float]] = {
    "cdr": 1150.0,
    "data": 580.0,
    "sms": 320.0,
    "ussd": 210.0,
    "recharge": 95.0,
    "bundle": 30.0,
    "momo": 25.0,
    "customer": 12.0,
    "alarm": 3.0,
}


@dataclass(frozen=True, slots=True)
class GeneratorConfig:
    """Everything that shapes a generated dataset, besides the scenarios.

    Two scaling policies coexist, on purpose:

    * **Transactional streams** (calls, sessions, SMS, USSD, recharges, offers, Mobile
      Money, customer events, alarms) are Poisson processes. Their rate is the production
      rate times :attr:`speed_factor`.
    * **Periodic streams** (``network.metrics``: one record per cell per minute;
      ``device.events``: one per piece of equipment every few minutes) cannot be thinned
      without destroying the time series a forecasting model needs. They are scaled by
      **entity count**: :attr:`periodic_fraction` of the cells and equipment, cadence
      unchanged. The cells and equipment the demonstration scenario names are always
      included.
    """

    seed: int = 42
    speed_factor: float = 0.001
    periodic_fraction: float = 0.01
    population_size: int = 20_000
    #: Share of events that reach the platform late, and by how long at most (FR-M1-010).
    late_fraction: float = 0.02
    max_delay_seconds: int = 300
    #: A rarer, longer delay, beyond the 5-minute watermark: exercises the late-arrivals table.
    very_late_fraction: float = 0.001
    very_late_max_seconds: int = 900

    def __post_init__(self) -> None:
        if not 0 < self.speed_factor <= 1:
            msg = f"speed_factor must be in (0, 1], got {self.speed_factor}"
            raise ValueError(msg)
        if not 0 < self.periodic_fraction <= 1:
            msg = f"periodic_fraction must be in (0, 1], got {self.periodic_fraction}"
            raise ValueError(msg)
        if self.population_size < 100:
            msg = f"population_size must be at least 100, got {self.population_size}"
            raise ValueError(msg)
        if not 0 <= self.late_fraction <= 1 or not 0 <= self.very_late_fraction <= 1:
            msg = "late fractions must be in [0, 1]"
            raise ValueError(msg)
        if self.max_delay_seconds < 1 or self.very_late_max_seconds < self.max_delay_seconds:
            msg = "delays must satisfy 1 <= max_delay_seconds <= very_late_max_seconds"
            raise ValueError(msg)

    @property
    def lookback_seconds(self) -> int:
        """How far before a window an event may have been *generated* and still arrive in it."""
        return self.very_late_max_seconds + 60
