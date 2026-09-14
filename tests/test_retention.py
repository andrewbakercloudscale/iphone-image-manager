from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from iphone_image.retention import DurationError, cutoff, format_duration, parse_duration

SPEC_VALUES = ["7d", "30d", "60d", "90d", "180d", "365d", "never"]


@pytest.mark.parametrize("value", SPEC_VALUES)
def test_spec_values_round_trip_exactly(value: str) -> None:
    """config show must echo back what the user wrote, not an equivalent."""
    assert format_duration(parse_duration(value)) == value


@pytest.mark.parametrize(
    ("value", "days"),
    [("7d", 7), ("60d", 60), ("12w", 84), ("6m", 180), ("2y", 730), ("45", 45), (90, 90)],
)
def test_parses_units(value: str | int, days: int) -> None:
    assert parse_duration(value) == timedelta(days=days)


@pytest.mark.parametrize("value", ["never", "NEVER", "Never", None])
def test_never_means_none(value: str | None) -> None:
    assert parse_duration(value) is None


@pytest.mark.parametrize("value", ["", "soon", "-5d", "5x", "1.5d", "d", "60 days", "∞"])
def test_rejects_nonsense(value: str) -> None:
    with pytest.raises(DurationError):
        parse_duration(value)


def test_cutoff_is_none_for_never() -> None:
    """A 'never' policy must select nothing, which callers read from None."""
    assert cutoff(None) is None


def test_cutoff_subtracts_from_now() -> None:
    now = datetime(2026, 9, 14, tzinfo=UTC)
    assert cutoff(timedelta(days=60), now=now) == datetime(2026, 7, 16, tzinfo=UTC)
