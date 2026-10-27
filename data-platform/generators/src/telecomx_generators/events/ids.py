"""Deterministic identifiers for generated events.

``event_id`` is a UUID v7 (RFC 9562): 48 bits of millisecond timestamp, then random
bits. The timestamp prefix makes ids sortable in time, which keeps the deduplication
state and the indexes local (data-platform/schemas/avro/README, envelope section). The
random bits come from the caller's seeded generator, so the id is reproducible.

Business identifiers (``CALL-...``, ``ALM-...``, ``TRX-...``) are derived from the
minute bucket and the position inside it, so they are unique within the simulated period
and identical across regenerations.
"""

from __future__ import annotations

import random
import uuid
from datetime import datetime

__all__ = ["alarm_id", "call_id", "prefixed_id", "transaction_id", "uuid7"]

_EPOCH_MINUTES = 29_000_000  # offset keeping CALL ids within nine digits for years


def uuid7(rng: random.Random, when: datetime) -> str:
    """A reproducible UUID v7 whose timestamp is ``when``."""
    millis = int(when.timestamp() * 1000) & ((1 << 48) - 1)
    value = (
        (millis << 80)
        | (0x7 << 76)
        | (rng.getrandbits(12) << 64)
        | (0b10 << 62)
        | rng.getrandbits(62)
    )
    return str(uuid.UUID(int=value))


def _minute_index(when: datetime) -> int:
    return int(when.timestamp() // 60)


def call_id(when: datetime, position: int) -> str:
    """``CALL-NNNNNNNNN`` from the minute and the position within it (below 1 000)."""
    sequence = ((_minute_index(when) - _EPOCH_MINUTES) % 1_000_000) * 1_000 + (position % 1_000)
    return f"CALL-{sequence:09d}"


def alarm_id(when: datetime, position: int) -> str:
    """``ALM-YYYYMMDD-NNNNNN``: the day, then minute-of-day and position (below 100)."""
    minute_of_day = when.hour * 60 + when.minute
    return f"ALM-{when:%Y%m%d}-{minute_of_day * 100 + (position % 100):06d}"


def transaction_id(when: datetime, position: int) -> str:
    """``TRX-YYYYMMDD-NNNNNNNN``: the day, then minute-of-day and position (below 10 000)."""
    minute_of_day = when.hour * 60 + when.minute
    return f"TRX-{when:%Y%m%d}-{minute_of_day * 10_000 + (position % 10_000):08d}"


def prefixed_id(prefix: str, when: datetime, position: int) -> str:
    """A generic ``<PREFIX>-<minute><position>`` identifier for sessions, messages..."""
    sequence = ((_minute_index(when) - _EPOCH_MINUTES) % 10_000_000) * 100 + (position % 100)
    return f"{prefix}-{sequence:09d}"
