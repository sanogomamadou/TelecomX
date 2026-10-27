"""Hand-written foundation of every generated contract model.

All validation *logic* lives here, where it is reviewed and tested. The generated
models contain only declarations (types, bounds, which fields form a sum rule),
which means the code nobody is allowed to edit by hand never holds behaviour
worth reading.

Timestamps deserve the care taken below. A wrong time unit does not raise: it
shifts every event by years and quietly corrupts partitioning, windowing and
freshness. Two guards follow from that:

* a datetime must carry a timezone and is normalised to UTC;
* a bare number is **refused**, because Avro's ``timestamp-micros`` counts
  microseconds while most other systems count seconds or milliseconds, and a
  validator that guesses which one was meant would guess wrong silently.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, ClassVar, Final, Self

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    model_validator,
)

from telecom_common.timeutils import utc_now

__all__ = [
    "NOT_FUTURE_TOLERANCE",
    "SUM_TOLERANCE",
    "ContractModel",
    "UtcDatetime",
    "not_in_future",
]

#: Clock skew tolerated between a producer and the platform before an event is
#: judged to come from the future. Five minutes matches the streaming watermark
#: (FR-M1-010): anything tighter would reject legitimate events from a producer
#: whose clock runs slightly ahead.
NOT_FUTURE_TOLERANCE: Final = timedelta(minutes=5)

#: Absolute tolerance when checking ``eq_sum`` rules on floating-point fields.
#: Summing doubles never gives an exact result, so equality needs a margin.
SUM_TOLERANCE: Final = 0.01


def _reject_epoch_numbers(value: Any) -> Any:
    """Refuse a bare number where a timestamp is expected.

    Avro ``timestamp-micros`` is in microseconds; seconds and milliseconds are
    equally common elsewhere. Accepting a number would mean guessing the unit.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        msg = (
            "refusing a bare number as a timestamp: the unit (seconds, milliseconds, "
            "microseconds) is ambiguous. Pass a timezone-aware datetime or an "
            "ISO 8601 string."
        )
        raise ValueError(msg)
    return value


def _to_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


UtcDatetime = Annotated[
    AwareDatetime,
    BeforeValidator(_reject_epoch_numbers),
    AfterValidator(_to_utc),
]
"""A timezone-aware datetime, normalised to UTC. Naive datetimes and bare numbers are rejected."""


def not_in_future(value: datetime) -> datetime:
    """Reject a timestamp further ahead than :data:`NOT_FUTURE_TOLERANCE`.

    Implements the ``not_future`` contract constraint. It depends on the clock,
    which makes it the one rule here that is not a pure function of the record.
    """
    if value > utc_now() + NOT_FUTURE_TOLERANCE:
        msg = f"timestamp {value.isoformat()} is in the future (tolerance {NOT_FUTURE_TOLERANCE})"
        raise ValueError(msg)
    return value


class ContractModel(BaseModel):
    """Base class of every generated event model.

    * ``extra="forbid"``: an unknown field is a contract violation, not noise.
    * ``frozen=True``: an event is a fact about the past and is never mutated,
      which mirrors the immutability of the bronze zone (principle P2).

    Subclasses are generated. They declare class-level metadata (``__contract__``,
    ``__topic__``, ``__partition_key__``) and, when the contract has ``eq_sum``
    rules, ``__sum_rules__``, which this class turns into validation.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    __contract__: ClassVar[str]
    __topic__: ClassVar[str]
    __partition_key__: ClassVar[str]
    __sum_rules__: ClassVar[tuple[tuple[str, tuple[str, ...]], ...]] = ()

    @model_validator(mode="after")
    def _check_sum_rules(self) -> Self:
        """Enforce ``eq_sum``: a total field must equal the sum of its parts."""
        for total_name, part_names in self.__sum_rules__:
            total = getattr(self, total_name)
            expected = sum(getattr(self, part) for part in part_names)
            if abs(total - expected) > SUM_TOLERANCE:
                parts = " + ".join(part_names)
                msg = (
                    f"{total_name} ({total}) must equal {parts} ({expected}) within {SUM_TOLERANCE}"
                )
                raise ValueError(msg)
        return self
