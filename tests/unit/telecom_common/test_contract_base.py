"""Tests for the hand-written validation logic shared by every generated model."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Annotated, ClassVar

import pytest
from pydantic import AfterValidator, ValidationError

from telecom_common.contracts.base import (
    NOT_FUTURE_TOLERANCE,
    SUM_TOLERANCE,
    ContractModel,
    UtcDatetime,
    not_in_future,
)
from telecom_common.timeutils import utc_now

pytestmark = pytest.mark.unit


class _Event(ContractModel):
    """Minimal model exercising every behaviour of the base class."""

    __contract__: ClassVar[str] = "probe-v1"
    __topic__: ClassVar[str] = "probe.events"
    __partition_key__: ClassVar[str] = "name"
    __sum_rules__: ClassVar[tuple[tuple[str, tuple[str, ...]], ...]] = (("total", ("a", "b")),)

    name: str
    when: UtcDatetime
    checked: Annotated[UtcDatetime, AfterValidator(not_in_future)]
    a: float
    b: float
    total: float


def _event(**overrides: object) -> _Event:
    data: dict[str, object] = {
        "name": "n",
        "when": datetime(2026, 1, 1, tzinfo=UTC),
        "checked": utc_now() - timedelta(minutes=1),
        "a": 1.0,
        "b": 2.0,
        "total": 3.0,
    }
    data.update(overrides)
    return _Event.model_validate(data)


class TestUtcDatetime:
    def test_accepts_an_aware_datetime(self) -> None:
        assert _event(when=datetime(2026, 1, 1, 12, tzinfo=UTC)).when.hour == 12

    def test_converts_other_offsets_to_utc(self) -> None:
        """Same instant, normalised: partitioning must not depend on the producer's offset."""
        plus_two = timezone(timedelta(hours=2))
        event = _event(when=datetime(2026, 1, 1, 14, tzinfo=plus_two))
        assert event.when == datetime(2026, 1, 1, 12, tzinfo=UTC)
        assert event.when.utcoffset() == timedelta(0)

    def test_parses_an_iso_string_with_z(self) -> None:
        assert _event(when="2026-01-01T12:00:00Z").when == datetime(2026, 1, 1, 12, tzinfo=UTC)

    def test_rejects_a_naive_datetime(self) -> None:
        with pytest.raises(ValidationError, match="timezone"):
            _event(when=datetime(2026, 1, 1, 12))  # noqa: DTZ001

    def test_rejects_a_naive_iso_string(self) -> None:
        with pytest.raises(ValidationError, match="timezone"):
            _event(when="2026-01-01T12:00:00")

    @pytest.mark.parametrize(
        "number",
        [1_767_225_600, 1_767_225_600_000, 1_767_225_600_000_000, 1_767_225_600.5],
        ids=["seconds", "milliseconds", "microseconds", "float-seconds"],
    )
    def test_rejects_a_bare_number_whatever_its_unit(self, number: float) -> None:
        """The unit is ambiguous, and a wrong guess shifts events by decades, silently."""
        with pytest.raises(ValidationError, match="ambiguous"):
            _event(when=number)

    def test_rejects_a_boolean(self) -> None:
        with pytest.raises(ValidationError):
            _event(when=True)


class TestNotInFuture:
    def test_accepts_the_past(self) -> None:
        assert not_in_future(utc_now() - timedelta(days=30))

    def test_accepts_the_present(self) -> None:
        assert not_in_future(utc_now())

    def test_tolerates_clock_skew_within_the_window(self) -> None:
        assert not_in_future(utc_now() + NOT_FUTURE_TOLERANCE - timedelta(seconds=30))

    def test_rejects_beyond_the_tolerance(self) -> None:
        with pytest.raises(ValueError, match="in the future"):
            not_in_future(utc_now() + NOT_FUTURE_TOLERANCE + timedelta(minutes=1))

    def test_tolerance_matches_the_streaming_watermark(self) -> None:
        """Five minutes, as in FR-M1-010; tighter would reject skewed but honest producers."""
        assert timedelta(minutes=5) == NOT_FUTURE_TOLERANCE

    def test_is_applied_through_a_model(self) -> None:
        with pytest.raises(ValidationError, match="in the future"):
            _event(checked=utc_now() + timedelta(days=1))


class TestSumRules:
    def test_accepts_an_exact_sum(self) -> None:
        _event(a=1.5, b=2.5, total=4.0)

    def test_tolerates_floating_point_noise(self) -> None:
        """0.1 + 0.2 is not 0.3 in binary floating point; equality needs a margin."""
        _event(a=0.1, b=0.2, total=0.3)

    def test_accepts_a_difference_inside_the_tolerance(self) -> None:
        _event(a=1.0, b=2.0, total=3.0 + SUM_TOLERANCE / 2)

    def test_rejects_a_difference_beyond_the_tolerance(self) -> None:
        with pytest.raises(ValidationError, match=r"total .* must equal a \+ b"):
            _event(a=1.0, b=2.0, total=3.0 + SUM_TOLERANCE * 2)

    def test_error_message_names_the_fields_and_values(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            _event(a=1.0, b=2.0, total=9.0)
        message = str(excinfo.value)
        assert "total (9.0)" in message
        assert "a + b (3.0)" in message

    def test_a_model_without_rules_is_unaffected(self) -> None:
        class _Plain(ContractModel):
            __contract__: ClassVar[str] = "plain-v1"
            __topic__: ClassVar[str] = "plain.events"
            __partition_key__: ClassVar[str] = "x"
            x: int

        assert _Plain(x=1).x == 1
        assert _Plain.__sum_rules__ == ()


class TestModelConfiguration:
    def test_unknown_fields_are_a_contract_violation(self) -> None:
        with pytest.raises(ValidationError, match="Extra inputs"):
            _event(unexpected=1)

    def test_events_are_immutable(self) -> None:
        """An event is a fact about the past (principle P2)."""
        event = _event()
        with pytest.raises(ValidationError, match="frozen"):
            event.name = "other"  # type: ignore[misc]

    def test_equal_events_hash_equal(self) -> None:
        # `checked` is fixed: the default uses utc_now(), which would differ between
        # the two calls and make the events genuinely unequal.
        checked = utc_now() - timedelta(minutes=1)
        first, second = _event(checked=checked), _event(checked=checked)
        assert first == second
        assert hash(first) == hash(second)
