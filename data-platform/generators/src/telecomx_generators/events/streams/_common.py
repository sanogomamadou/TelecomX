"""Helpers shared by every stream."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from telecomx_generators.events.config import PRODUCTION_RATES
from telecomx_generators.events.context import GenContext
from telecomx_generators.events.ids import uuid7
from telecomx_generators.events.rand import poisson
from telecomx_generators.events.seasonality import load_factor

__all__ = [
    "BUCKET_SECONDS",
    "bucket_start_of",
    "bucket_time",
    "envelope",
    "money",
    "poisson_count",
    "stamps_in_bucket",
]

BUCKET_SECONDS = 60


def bucket_time(minute: int) -> datetime:
    """Start of the bucket numbered ``minute`` (minutes since the Unix epoch)."""
    return datetime.fromtimestamp(minute * BUCKET_SECONDS, tz=UTC)


def bucket_start_of(when: datetime) -> datetime:
    """The start of the 60-second bucket containing ``when``."""
    return bucket_time(int(when.timestamp() // BUCKET_SECONDS))


def poisson_count(
    ctx: GenContext, rng: random.Random, kind: str, rate_key: str, bucket_start: datetime
) -> int:
    """How many events of a transactional stream happen in one bucket.

    The production rate (messages per second, PRD section 2.2) is scaled by
    ``speed_factor`` and shaped by the daily rhythm of ``kind``.
    """
    middle = bucket_start + timedelta(seconds=BUCKET_SECONDS / 2)
    lam = (
        PRODUCTION_RATES[rate_key]
        * ctx.config.speed_factor
        * BUCKET_SECONDS
        * load_factor(kind, middle)
    )
    return poisson(rng, lam)


def stamps_in_bucket(rng: random.Random, bucket_start: datetime, count: int) -> list[datetime]:
    """``count`` event times spread uniformly across the bucket, in order."""
    return sorted(
        bucket_start + timedelta(seconds=rng.uniform(0, BUCKET_SECONDS)) for _ in range(count)
    )


def envelope(ctx: GenContext, rng: random.Random, when: datetime, producer: str) -> dict[str, Any]:
    """The envelope fields common to every event (FR-M1-006)."""
    return {
        "event_id": uuid7(rng, when),
        "event_timestamp": when,
        "ingestion_timestamp": when + ctx.arrival_delay(rng),
        "producer_id": producer,
        "tenant_id": ctx.tenant_id,
    }


def money(value: float) -> Decimal:
    """A monetary amount with exactly two decimals (no binary floating point)."""
    return Decimal(f"{value:.2f}")
