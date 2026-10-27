"""Daily, weekly, monthly and calendar rhythms of telecom traffic.

The shape follows PRD requirement FR-M1-002: a trough between 03:00 and 05:00, a peak
around midday (12:00-14:00) and a larger one in the evening (19:00-22:00). Each curve is
normalised to a mean of 1.0 over the day, so that the production rates of the PRD
(section 2.2) remain *daily averages* and the curve only redistributes them in time.

All times are UTC. Mali observes UTC+0 all year, so UTC hours are local hours.

These are plausible shapes chosen by hand, not fitted to any real network.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Final

__all__ = ["KINDS", "load_factor"]

# 24 hourly values per traffic kind, before normalisation.
_HOURLY_RAW: Final[dict[str, list[float]]] = {
    "voice": [
        0.25,
        0.15,
        0.10,
        0.10,
        0.15,
        0.35,
        0.70,
        1.10,
        1.40,
        1.50,
        1.50,
        1.55,
        1.60,
        1.55,
        1.45,
        1.40,
        1.45,
        1.50,
        1.55,
        1.50,
        1.30,
        0.95,
        0.60,
        0.40,
    ],
    "data": [
        0.45,
        0.30,
        0.20,
        0.18,
        0.20,
        0.30,
        0.50,
        0.80,
        1.00,
        1.10,
        1.10,
        1.20,
        1.35,
        1.30,
        1.20,
        1.15,
        1.20,
        1.35,
        1.60,
        1.85,
        1.90,
        1.70,
        1.20,
        0.75,
    ],
    "sms": [
        0.40,
        0.25,
        0.15,
        0.12,
        0.15,
        0.30,
        0.70,
        1.10,
        1.40,
        1.50,
        1.50,
        1.55,
        1.60,
        1.50,
        1.40,
        1.35,
        1.40,
        1.50,
        1.55,
        1.50,
        1.20,
        0.90,
        0.65,
        0.50,
    ],
    "ussd": [
        0.20,
        0.12,
        0.08,
        0.08,
        0.12,
        0.30,
        0.80,
        1.30,
        1.60,
        1.60,
        1.50,
        1.50,
        1.55,
        1.45,
        1.35,
        1.30,
        1.35,
        1.45,
        1.50,
        1.40,
        1.10,
        0.80,
        0.50,
        0.30,
    ],
    "recharge": [
        0.15,
        0.10,
        0.06,
        0.06,
        0.10,
        0.30,
        0.80,
        1.30,
        1.70,
        1.80,
        1.70,
        1.70,
        1.60,
        1.40,
        1.30,
        1.30,
        1.40,
        1.60,
        1.60,
        1.40,
        1.00,
        0.60,
        0.35,
        0.20,
    ],
    "momo": [
        0.15,
        0.10,
        0.06,
        0.06,
        0.10,
        0.30,
        0.80,
        1.40,
        1.80,
        1.90,
        1.80,
        1.70,
        1.60,
        1.40,
        1.30,
        1.30,
        1.40,
        1.60,
        1.50,
        1.20,
        0.80,
        0.50,
        0.30,
        0.20,
    ],
    "customer": [
        0.05,
        0.03,
        0.02,
        0.02,
        0.03,
        0.10,
        0.50,
        1.50,
        2.20,
        2.40,
        2.30,
        2.00,
        1.60,
        1.60,
        1.80,
        1.90,
        1.60,
        1.10,
        0.70,
        0.40,
        0.25,
        0.15,
        0.10,
        0.06,
    ],
    "alarm": [1.00] * 24,
}

#: Traffic kinds a generator can ask for.
KINDS: Final = frozenset(_HOURLY_RAW)

HOURLY: Final[dict[str, list[float]]] = {
    kind: [v * len(values) / sum(values) for v in values] for kind, values in _HOURLY_RAW.items()
}

# Monday..Sunday.
_WEEKDAY: Final[dict[str, list[float]]] = {
    "voice": [1.06, 1.06, 1.06, 1.06, 1.06, 0.92, 0.78],
    "data": [0.95, 0.95, 0.95, 0.98, 1.02, 1.10, 1.05],
    "sms": [1.0, 1.0, 1.0, 1.0, 1.05, 1.00, 0.95],
    "ussd": [1.05, 1.05, 1.05, 1.05, 1.05, 0.95, 0.80],
    "recharge": [1.00, 1.00, 1.00, 1.00, 1.10, 1.05, 0.85],
    "momo": [1.00, 1.00, 1.00, 1.00, 1.15, 1.10, 0.75],
    "customer": [1.15, 1.10, 1.05, 1.05, 1.05, 0.55, 0.25],
    "alarm": [1.0] * 7,
}


def _hourly(kind: str, when: datetime) -> float:
    """Hourly factor, interpolated linearly across the hour to avoid step artefacts."""
    values = HOURLY[kind]
    fraction = (when.minute + when.second / 60) / 60
    return values[when.hour] * (1 - fraction) + values[(when.hour + 1) % 24] * fraction


def _month_day(kind: str, when: datetime) -> float:
    """Pay-cycle effect: money-related traffic rises around the 1st and the 15th."""
    if kind not in {"recharge", "momo"}:
        return 1.0
    day = when.day
    return 1.0 + 0.6 * math.exp(-(day - 1) / 2) + 0.3 * math.exp(-abs(day - 15) / 1.5)


def _special(kind: str, when: datetime) -> float:
    """Exceptional events. New Year's Eve is the extreme: SMS peak at about 28 times
    the normal rate around midnight (PRD section 2.2)."""
    minutes_from_new_year = (
        (when.hour * 60 + when.minute) - 24 * 60
        if (when.month, when.day) == (12, 31)
        else (when.hour * 60 + when.minute)
        if (when.month, when.day) == (1, 1)
        else None
    )
    if minutes_from_new_year is None:
        return 1.0
    bump = math.exp(-((minutes_from_new_year / 45) ** 2))
    peak = {"sms": 27.0, "voice": 2.5, "data": 0.6, "ussd": 0.3, "momo": 0.4}.get(kind, 0.0)
    return 1.0 + peak * bump


def load_factor(kind: str, when: datetime) -> float:
    """Multiplier applied to the average rate of ``kind`` at ``when`` (mean about 1).

    Raises:
        KeyError: if ``kind`` is unknown, which would otherwise silently give flat traffic.
    """
    if kind not in HOURLY:
        raise KeyError(kind)
    return (
        _hourly(kind, when)
        * _WEEKDAY[kind][when.weekday()]
        * _month_day(kind, when)
        * _special(kind, when)
    )
