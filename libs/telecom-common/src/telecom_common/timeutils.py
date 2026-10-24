"""Timezone-aware time helpers.

Every timestamp in the platform is UTC with an explicit tzinfo. Naive datetimes
are banned by the ``DTZ`` ruff ruleset (ADR-014) because on a platform whose
streaming windows, watermarks, partitioning and time series all key off
timestamps, a naive datetime produces silent, expensive corruption rather than
an exception.

This module is the only sanctioned way to obtain "now" in the codebase.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Final

__all__ = [
    "EPOCH",
    "floor_to",
    "from_epoch_micros",
    "is_utc",
    "parse_utc",
    "to_epoch_micros",
    "utc_now",
    "window_bounds",
]

EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)

#: Avro ``timestamp-micros`` is the wire format for every event timestamp
#: (see data-platform/schemas/avro/). Microseconds, not milliseconds.
_MICROS_PER_SECOND: Final = 1_000_000


def utc_now() -> datetime:
    """Return the current instant as a timezone-aware UTC datetime.

    Use this instead of ``datetime.now()`` or ``datetime.utcnow()``. The latter
    is particularly treacherous: it returns a *naive* datetime holding UTC
    wall-clock time, which compares incorrectly against aware datetimes.
    """
    return datetime.now(UTC)


def is_utc(value: datetime) -> bool:
    """Return whether ``value`` is timezone-aware and anchored to UTC."""
    return value.tzinfo is not None and value.utcoffset() == timedelta(0)


def parse_utc(value: str) -> datetime:
    """Parse an ISO 8601 string into a UTC datetime.

    Accepts a trailing ``Z``, which :meth:`datetime.fromisoformat` only handles
    from Python 3.11 onwards but which we normalise explicitly for clarity.

    Raises:
        ValueError: if the string is not ISO 8601, or carries no timezone.
            A timestamp without a timezone is rejected rather than assumed to
            be UTC -- guessing is how silent off-by-hours bugs are born.
    """
    normalised = value.replace("Z", "+00:00") if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalised)
    if parsed.tzinfo is None:
        msg = f"timestamp {value!r} has no timezone; refusing to assume UTC"
        raise ValueError(msg)
    return parsed.astimezone(UTC)


def to_epoch_micros(value: datetime) -> int:
    """Convert a datetime to microseconds since the Unix epoch (Avro wire format).

    Raises:
        ValueError: if ``value`` is naive.
    """
    if value.tzinfo is None:
        msg = "refusing to serialise a naive datetime; use utc_now() or parse_utc()"
        raise ValueError(msg)
    return int(value.timestamp() * _MICROS_PER_SECOND)


def from_epoch_micros(micros: int) -> datetime:
    """Convert microseconds since the Unix epoch into a UTC datetime."""
    return datetime.fromtimestamp(micros / _MICROS_PER_SECOND, tz=UTC)


def floor_to(value: datetime, granularity: timedelta) -> datetime:
    """Round ``value`` down to a multiple of ``granularity`` since the epoch.

    Used to assign an event to its aggregation bucket (5 min, 1 h, 1 day) so
    that Spark, dbt and the serving layer all agree on bucket boundaries.

    Raises:
        ValueError: if ``granularity`` is not strictly positive, or if
            ``value`` is naive.
    """
    if granularity <= timedelta(0):
        msg = f"granularity must be strictly positive, got {granularity!r}"
        raise ValueError(msg)
    if value.tzinfo is None:
        msg = "refusing to bucket a naive datetime"
        raise ValueError(msg)
    elapsed = value - EPOCH
    remainder = elapsed % granularity
    return value - remainder


def window_bounds(value: datetime, granularity: timedelta) -> tuple[datetime, datetime]:
    """Return the ``[start, end)`` bucket containing ``value``.

    The end bound is exclusive, matching the convention used by Spark window
    functions and by the ``from``/``to`` parameters of the agent tools.
    """
    start = floor_to(value, granularity)
    return start, start + granularity
