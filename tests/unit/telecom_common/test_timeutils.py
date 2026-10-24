"""Tests for timezone-aware time helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from telecom_common.timeutils import (
    EPOCH,
    floor_to,
    from_epoch_micros,
    is_utc,
    parse_utc,
    to_epoch_micros,
    utc_now,
    window_bounds,
)

pytestmark = pytest.mark.unit


class TestUtcNow:
    def test_returns_aware_utc_datetime(self) -> None:
        now = utc_now()
        assert now.tzinfo is not None
        assert is_utc(now)


class TestIsUtc:
    def test_accepts_utc(self) -> None:
        assert is_utc(datetime(2026, 9, 10, 18, 0, tzinfo=UTC))

    def test_accepts_equivalent_zero_offset(self) -> None:
        assert is_utc(datetime(2026, 9, 10, 18, 0, tzinfo=timezone(timedelta(0))))

    def test_rejects_naive(self) -> None:
        assert not is_utc(datetime(2026, 9, 10, 18, 0))  # noqa: DTZ001

    def test_rejects_non_utc_offset(self) -> None:
        assert not is_utc(datetime(2026, 9, 10, 18, 0, tzinfo=timezone(timedelta(hours=2))))


class TestParseUtc:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("2026-09-10T18:00:00Z", datetime(2026, 9, 10, 18, 0, tzinfo=UTC)),
            ("2026-09-10T18:00:00+00:00", datetime(2026, 9, 10, 18, 0, tzinfo=UTC)),
            # Offsets are normalised to UTC, not merely accepted.
            ("2026-09-10T20:00:00+02:00", datetime(2026, 9, 10, 18, 0, tzinfo=UTC)),
        ],
    )
    def test_parses_and_normalises(self, raw: str, expected: datetime) -> None:
        assert parse_utc(raw) == expected

    def test_rejects_naive_timestamp(self) -> None:
        """A timestamp without a timezone must fail, never be assumed UTC."""
        with pytest.raises(ValueError, match="no timezone"):
            parse_utc("2026-09-10T18:00:00")

    def test_rejects_garbage(self) -> None:
        with pytest.raises(ValueError, match=r"Invalid isoformat|no timezone"):
            parse_utc("not-a-date")


class TestEpochMicros:
    def test_epoch_is_zero(self) -> None:
        assert to_epoch_micros(EPOCH) == 0

    def test_round_trip_preserves_instant(self) -> None:
        original = datetime(2026, 9, 10, 18, 30, 15, 123456, tzinfo=UTC)
        assert from_epoch_micros(to_epoch_micros(original)) == original

    def test_rejects_naive_datetime(self) -> None:
        with pytest.raises(ValueError, match="naive datetime"):
            to_epoch_micros(datetime(2026, 9, 10, 18, 0))  # noqa: DTZ001

    def test_resolution_is_microseconds_not_milliseconds(self) -> None:
        """Avro declares timestamp-micros; a x1000 error would shift dates by years."""
        one_second_after_epoch = datetime(1970, 1, 1, 0, 0, 1, tzinfo=UTC)
        assert to_epoch_micros(one_second_after_epoch) == 1_000_000


class TestFloorTo:
    @pytest.mark.parametrize(
        ("granularity", "expected_minute"),
        [(timedelta(minutes=5), 30), (timedelta(minutes=15), 30), (timedelta(hours=1), 0)],
    )
    def test_buckets_to_granularity(self, granularity: timedelta, expected_minute: int) -> None:
        value = datetime(2026, 9, 10, 18, 34, 27, tzinfo=UTC)
        result = floor_to(value, granularity)
        assert result.minute == expected_minute
        assert result.second == 0
        assert result.microsecond == 0

    def test_is_idempotent(self) -> None:
        value = datetime(2026, 9, 10, 18, 34, 27, tzinfo=UTC)
        once = floor_to(value, timedelta(hours=1))
        assert floor_to(once, timedelta(hours=1)) == once

    def test_daily_granularity_lands_on_midnight(self) -> None:
        value = datetime(2026, 9, 10, 18, 34, 27, tzinfo=UTC)
        assert floor_to(value, timedelta(days=1)) == datetime(2026, 9, 10, tzinfo=UTC)

    def test_rejects_zero_granularity(self) -> None:
        with pytest.raises(ValueError, match="strictly positive"):
            floor_to(utc_now(), timedelta(0))

    def test_rejects_negative_granularity(self) -> None:
        with pytest.raises(ValueError, match="strictly positive"):
            floor_to(utc_now(), timedelta(minutes=-5))

    def test_rejects_naive_datetime(self) -> None:
        with pytest.raises(ValueError, match="naive datetime"):
            floor_to(datetime(2026, 9, 10, 18, 0), timedelta(hours=1))  # noqa: DTZ001


class TestWindowBounds:
    def test_end_is_exclusive_and_contiguous(self) -> None:
        value = datetime(2026, 9, 10, 18, 34, tzinfo=UTC)
        start, end = window_bounds(value, timedelta(hours=1))
        assert start == datetime(2026, 9, 10, 18, 0, tzinfo=UTC)
        assert end == datetime(2026, 9, 10, 19, 0, tzinfo=UTC)

    def test_value_falls_within_its_own_window(self) -> None:
        value = datetime(2026, 9, 10, 18, 59, 59, 999999, tzinfo=UTC)
        start, end = window_bounds(value, timedelta(hours=1))
        assert start <= value < end

    def test_consecutive_windows_do_not_overlap(self) -> None:
        """The boundary instant belongs to the next window, not both."""
        granularity = timedelta(minutes=5)
        _, first_end = window_bounds(datetime(2026, 9, 10, 18, 2, tzinfo=UTC), granularity)
        second_start, _ = window_bounds(first_end, granularity)
        assert second_start == first_end
