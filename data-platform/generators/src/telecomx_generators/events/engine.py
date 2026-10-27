"""The generation engine: turns a time window into an ordered stream of events.

Semantics are those of an **arrival window**: ``generate(ctx, start, end)`` returns the
events that *reach the platform* in ``[start, end)``, ordered by ingestion time. An event
that happened at 11:59:50 and arrived at 12:03 belongs to a window starting at 12:00, which
is exactly what a streaming job sees and what a watermark must cope with. To find them, the
engine also generates the buckets just before ``start`` (the look-back) and keeps what
arrives late enough.

Because every bucket draws from its own seeded generator, the events of one bucket do not
depend on which window asked for them: two windows covering the same period, however they
are cut, yield identical events.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta

from telecom_common.contracts.base import ContractModel
from telecom_common.timeutils import is_utc
from telecomx_generators.events.context import GenContext
from telecomx_generators.events.streams import STREAMS
from telecomx_generators.events.streams._common import BUCKET_SECONDS, bucket_start_of

__all__ = ["Emitted", "generate", "generate_topic"]


@dataclass(frozen=True, slots=True)
class Emitted:
    """One event with the topic it belongs to."""

    topic: str
    event: ContractModel

    @property
    def arrival(self) -> datetime:
        return self.event.ingestion_timestamp  # type: ignore[attr-defined,no-any-return]


def _buckets(first: datetime, last: datetime) -> Iterator[datetime]:
    current = bucket_start_of(first)
    step = timedelta(seconds=BUCKET_SECONDS)
    while current < last:
        yield current
        current += step


def generate_topic(
    ctx: GenContext, topic: str, start: datetime, end: datetime
) -> list[ContractModel]:
    """Events of one topic arriving in ``[start, end)``, ordered by arrival."""
    if not (is_utc(start) and is_utc(end)):
        msg = "start and end must be timezone-aware UTC datetimes"
        raise ValueError(msg)
    if end <= start:
        msg = "end must be after start"
        raise ValueError(msg)
    stream = STREAMS[topic]
    lookback = timedelta(seconds=ctx.config.lookback_seconds)
    found: list[ContractModel] = []
    for bucket in _buckets(start - lookback, end):
        for event in stream(ctx, bucket):
            if start <= event.ingestion_timestamp < end:  # type: ignore[attr-defined]
                found.append(event)
    found.sort(key=lambda e: (e.ingestion_timestamp, e.event_id))  # type: ignore[attr-defined]
    return found


def generate(
    ctx: GenContext,
    start: datetime,
    end: datetime,
    topics: Iterable[str] | None = None,
) -> list[Emitted]:
    """All requested topics merged into one sequence ordered by arrival time."""
    selected = list(topics) if topics is not None else list(STREAMS)
    unknown = [t for t in selected if t not in STREAMS]
    if unknown:
        msg = f"unknown topics: {', '.join(sorted(unknown))}"
        raise KeyError(msg)
    merged = [Emitted(t, e) for t in selected for e in generate_topic(ctx, t, start, end)]
    merged.sort(key=lambda m: (m.arrival, m.event.event_id))  # type: ignore[attr-defined]
    return merged
