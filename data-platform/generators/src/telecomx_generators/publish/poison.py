"""Deliberately corrupted messages, to exercise the dead-letter path.

Nothing is wrong with the generators' own events, so without help the DLQ would stay empty
and its routing, its alerting threshold (SLO: below 0.1 %) and its replay tool would never be
exercised before production. ``--poison-fraction`` corrupts a share of the events in the five
ways a real producer fails, so each rejection code has data to prove it.
"""

from __future__ import annotations

import random
from collections.abc import Mapping
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from telecom_common.contracts.constraints import ConstraintKind
from telecom_common.contracts.spec import ContractSpec, FieldType

__all__ = ["Poison", "applicable", "corrupt", "pick"]


class Poison(StrEnum):
    """The ways a message is corrupted, with the rejection each one must produce."""

    MISSING_TENANT = "missing_tenant"  # -> MISSING_TENANT
    MISSING_FIELD = "missing_field"  # -> SCHEMA_VIOLATION
    UNKNOWN_SYMBOL = "unknown_symbol"  # -> SCHEMA_VIOLATION
    WRONG_TYPE = "wrong_type"  # -> SCHEMA_VIOLATION
    OUT_OF_RANGE = "out_of_range"  # -> CONSTRAINT_VIOLATION
    FUTURE_TIMESTAMP = "future_timestamp"  # -> CONSTRAINT_VIOLATION


def _lower_bounded(spec: ContractSpec) -> list[str]:
    return [
        f.name
        for f in spec.fields
        if f.type.is_numeric
        and (
            f.constraint(ConstraintKind.GTE) is not None
            or f.constraint(ConstraintKind.GT) is not None
        )
    ]


def applicable(spec: ContractSpec) -> list[Poison]:
    """The corruptions that make sense for this contract."""
    kinds = [Poison.MISSING_TENANT, Poison.MISSING_FIELD, Poison.FUTURE_TIMESTAMP]
    if any(f.type is FieldType.ENUM for f in spec.fields):
        kinds.append(Poison.UNKNOWN_SYMBOL)
    if any(f.type.is_numeric for f in spec.fields):
        kinds.append(Poison.WRONG_TYPE)
    if _lower_bounded(spec):
        kinds.append(Poison.OUT_OF_RANGE)
    return kinds


def pick(spec: ContractSpec, rng: random.Random) -> Poison:
    """Choose a corruption for this contract."""
    return rng.choice(applicable(spec))


def corrupt(
    spec: ContractSpec, payload: Mapping[str, Any], kind: Poison, rng: random.Random
) -> dict[str, Any]:
    """Return a copy of ``payload`` damaged in the way ``kind`` describes."""
    broken = dict(payload)
    if kind is Poison.MISSING_TENANT:
        del broken["tenant_id"]
    elif kind is Poison.MISSING_FIELD:
        business = [f.name for f in spec.fields if f.is_required and not f.is_envelope]
        del broken[rng.choice(business)]
    elif kind is Poison.UNKNOWN_SYMBOL:
        broken[rng.choice([f.name for f in spec.fields if f.type is FieldType.ENUM])] = (
            "NOT_A_SYMBOL"
        )
    elif kind is Poison.WRONG_TYPE:
        broken[rng.choice([f.name for f in spec.fields if f.type.is_numeric])] = "not-a-number"
    elif kind is Poison.OUT_OF_RANGE:
        broken[rng.choice(_lower_bounded(spec))] = -1
    elif kind is Poison.FUTURE_TIMESTAMP:
        timestamp = broken["event_timestamp"]
        assert isinstance(timestamp, datetime)  # noqa: S101 - generator events carry a datetime
        broken["event_timestamp"] = timestamp + timedelta(days=365)
    return broken
