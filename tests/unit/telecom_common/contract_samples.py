"""Build valid and invalid events directly from a contract specification.

Used by the contract tests to exercise *every* generated model without writing a
hand-made fixture per contract. The valid sample satisfies every constraint of the
spec; each invalid variant breaks exactly one rule. A model that accepts the first
and rejects all the others implements its contract.

The helper reads the *spec*, not the generated model, on purpose. If it read the
model it would test the generator against itself and agree with its own mistakes.

When a contract introduces a new ``pattern``, :data:`PATTERN_SAMPLES` must learn one
value matching it. The helper raises rather than guessing, so the gap surfaces as a
clear test failure instead of a silently weakened test.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from telecom_common.contracts import ConstraintKind, ContractSpec, FieldSpec, FieldType
from telecom_common.timeutils import utc_now

__all__ = ["invalid_variants", "valid_sample"]

#: One value per regular expression used in the contracts.
PATTERN_SAMPLES = ("CELL-0421", "SITE-102")


def _number(fld: FieldSpec, kind: ConstraintKind) -> float | None:
    constraint = fld.constraint(kind)
    if constraint is None or not isinstance(constraint.value, (int, float)):
        return None
    return float(constraint.value)


def _numeric_candidate(fld: FieldSpec) -> float:
    """A number that satisfies the field's ordering constraints."""
    gte, gt = _number(fld, ConstraintKind.GTE), _number(fld, ConstraintKind.GT)
    lte, lt = _number(fld, ConstraintKind.LTE), _number(fld, ConstraintKind.LT)

    if gte is not None:
        candidate = gte
    elif gt is not None:
        candidate = gt + 1
    else:
        candidate = 10.0

    upper = lte if lte is not None else (lt - 1 if lt is not None else None)
    if upper is not None and candidate > upper:
        candidate = upper
    return candidate


def _as_number(fld: FieldSpec, value: float) -> int | float | Decimal:
    """Express ``value`` in the Python type the field expects."""
    if fld.type in {FieldType.INT, FieldType.LONG}:
        return int(value)
    if fld.type is FieldType.DECIMAL:
        return Decimal(str(value)).quantize(Decimal(1).scaleb(-(fld.scale or 0)))
    return float(value)


def _valid_value(fld: FieldSpec, now: datetime) -> Any:
    if fld.type is FieldType.TIMESTAMP:
        # Whole seconds: Avro stores microseconds as an integer and a float round
        # trip through it can be off by one microsecond, which would make the
        # round-trip test flaky for no meaningful reason.
        return now.replace(microsecond=0) - timedelta(minutes=1)
    if fld.type is FieldType.BOOLEAN:
        return False
    if fld.type is FieldType.ENUM:
        return fld.enum_symbols[0]
    if fld.type.is_numeric:
        return _as_number(fld, _numeric_candidate(fld))

    pattern = fld.constraint(ConstraintKind.PATTERN)
    if pattern is not None and isinstance(pattern.value, str):
        for candidate in PATTERN_SAMPLES:
            if re.match(pattern.value, candidate):
                return candidate
        msg = f"no sample value for pattern {pattern.value!r}; extend PATTERN_SAMPLES"
        raise AssertionError(msg)
    return f"{fld.name}-sample"


def valid_sample(spec: ContractSpec, now: datetime | None = None) -> dict[str, Any]:
    """An event that satisfies every constraint of ``spec``.

    Optional fields are filled too, so that their constraints are exercised.
    """
    moment = now if now is not None else utc_now()
    sample = {fld.name: _valid_value(fld, moment) for fld in spec.fields}
    # A sum rule overrides the independently chosen total.
    for total, parts in spec.sum_rules:
        sample[total] = sum(sample[part] for part in parts)
    return sample


def invalid_variants(
    spec: ContractSpec, valid: dict[str, Any], now: datetime | None = None
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield ``(label, event)`` pairs, each breaking exactly one rule of ``spec``."""
    moment = now if now is not None else utc_now()

    for fld in spec.fields:
        name = fld.name

        if not fld.has_default:
            yield f"{name}:missing", {k: v for k, v in valid.items() if k != name}

        yield f"{name}:wrong-type", {**valid, name: object()}

        gte, gt = _number(fld, ConstraintKind.GTE), _number(fld, ConstraintKind.GT)
        lte, lt = _number(fld, ConstraintKind.LTE), _number(fld, ConstraintKind.LT)
        if gte is not None:
            yield f"{name}:below-gte", {**valid, name: _as_number(fld, gte - 1)}
        if gt is not None:
            yield f"{name}:not-above-gt", {**valid, name: _as_number(fld, gt)}
        if lte is not None:
            yield f"{name}:above-lte", {**valid, name: _as_number(fld, lte + 1)}
        if lt is not None:
            yield f"{name}:not-below-lt", {**valid, name: _as_number(fld, lt)}

        if fld.constraint(ConstraintKind.PATTERN) is not None:
            yield f"{name}:bad-pattern", {**valid, name: "does-not-match"}
        if fld.type is FieldType.ENUM:
            yield f"{name}:bad-enum", {**valid, name: "NOT_A_SYMBOL"}
        if fld.constraint(ConstraintKind.NOT_FUTURE) is not None:
            yield f"{name}:in-the-future", {**valid, name: moment + timedelta(days=1)}

    for total, _parts in spec.sum_rules:
        yield f"{total}:sum-mismatch", {**valid, total: valid[total] + 1}

    yield "extra-field", {**valid, "unexpected_field": 1}
