"""Dead-letter queue messages (FR-M1-008).

A message that does not conform to its contract is not dropped and does not stop the
stream: it is wrapped with the reason it was refused and published to ``<topic>.dlq``,
where it stays for 30 days and can be replayed after correction.

The wrapper is itself an Avro record (``dlq/dead-letter-v1.avsc``). The refused message
is stored inside it as UTF-8 JSON, because a message that failed validation cannot be
assumed to be encodable in its own schema.
"""

from __future__ import annotations

import base64
import json
import random
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from enum import Enum, StrEnum
from typing import Any, Final

from pydantic import ValidationError

from telecomx_generators.events.ids import uuid7

__all__ = ["Rejection", "RejectionCode", "classify", "dead_letter_record", "dead_letter_topic"]

#: Longest reason kept. A pathological message can fail on hundreds of fields.
MAX_REASON_CHARS: Final = 1000

#: Pydantic error types that mean "this value breaks a declared rule" rather than "this
#: value is not of the declared shape". The distinction decides how an alert is read: a
#: rising CONSTRAINT_VIOLATION rate is a data-quality issue, a rising SCHEMA_VIOLATION
#: rate is a producer that was deployed wrongly (docs/data/contracts/README.md section 5).
_CONSTRAINT_TYPES: Final = frozenset(
    {
        "greater_than",
        "greater_than_equal",
        "less_than",
        "less_than_equal",
        "string_pattern_mismatch",
        "string_too_short",
        "string_too_long",
        "value_error",
        "assertion_error",
        "finite_number",
        "multiple_of",
    }
)


class RejectionCode(StrEnum):
    """Why a message was refused. Mirrors the ``RejectionCode`` enum of the contract."""

    SCHEMA_VIOLATION = "SCHEMA_VIOLATION"
    CONSTRAINT_VIOLATION = "CONSTRAINT_VIOLATION"
    MISSING_TENANT = "MISSING_TENANT"
    SERIALIZATION_ERROR = "SERIALIZATION_ERROR"


class Rejection:
    """A refusal: a code and a reason that never contains the refused values."""

    __slots__ = ("code", "reason")

    def __init__(self, code: RejectionCode, reason: str) -> None:
        self.code = code
        self.reason = reason[:MAX_REASON_CHARS]

    def __repr__(self) -> str:
        return f"Rejection({self.code.value}, {self.reason!r})"


def dead_letter_topic(topic: str) -> str:
    """The dead-letter topic of ``topic``."""
    return f"{topic}.dlq"


def classify(error: ValidationError) -> Rejection:
    """Turn a validation failure into a :class:`Rejection`.

    The reason lists ``field: error_type`` pairs only. Pydantic's own messages and ``input``
    echo the refused value, which here is an MSISDN or an IMSI: it would end up in logs and
    metrics. The value is kept where it belongs, in the dead-letter payload.
    """
    errors = error.errors(include_input=False, include_url=False)
    pairs = [(".".join(str(part) for part in e["loc"]) or "<root>", e["type"]) for e in errors]
    reason = "; ".join(f"{where}: {kind}" for where, kind in pairs)

    if any(where == "tenant_id" and kind == "missing" for where, kind in pairs):
        return Rejection(RejectionCode.MISSING_TENANT, reason)
    if all(kind in _CONSTRAINT_TYPES for _, kind in pairs):
        return Rejection(RejectionCode.CONSTRAINT_VIOLATION, reason)
    return Rejection(RejectionCode.SCHEMA_VIOLATION, reason)


def _json_default(value: Any) -> Any:
    """Make any value that can appear in an event JSON-serialisable, losslessly enough to replay."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    return str(value)


def dead_letter_record(
    *,
    source_topic: str,
    rejection: Rejection,
    payload: Mapping[str, Any],
    key: bytes | None,
    now: datetime,
    rng: random.Random,
) -> dict[str, Any]:
    """Build the record of ``dlq/dead-letter-v1.avsc`` for one refused message."""
    producer = payload.get("producer_id")
    tenant = payload.get("tenant_id")
    return {
        "dead_letter_id": uuid7(rng, now),
        "rejected_at": now,
        "source_topic": source_topic,
        "rejection_code": rejection.code.value,
        "rejection_reason": rejection.reason,
        "producer_id": producer if isinstance(producer, str) and producer else "unknown",
        "tenant_id": tenant if isinstance(tenant, str) and tenant else None,
        "source_key": key,
        "raw_payload": json.dumps(
            dict(payload), default=_json_default, sort_keys=True, ensure_ascii=False
        ).encode("utf-8"),
        "schema_version": "1.0.0",
    }
