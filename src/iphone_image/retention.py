"""Duration parsing for retention policies.

The specification lists 7d, 30d, 60d, 90d, 180d, 365d and never, and says
arbitrary durations should also be supported. So this parses a general
<number><unit> form rather than matching a fixed list.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

NEVER = "never"

_UNITS = {
    "d": 1,
    "w": 7,
    "m": 30,  # calendar months are not a fixed length; 30 days is the documented meaning
    "y": 365,
}

_PATTERN = re.compile(r"^\s*(\d+)\s*([dwmy])?\s*$", re.IGNORECASE)


class DurationError(ValueError):
    """Raised when a retention value cannot be understood."""


def parse_duration(value: str | int | None) -> timedelta | None:
    """Parse a retention value.

    Returns None for "never", meaning nothing ever ages out. A bare number is
    read as days, so "60" and "60d" agree.
    """
    if value is None:
        return None
    if isinstance(value, int):
        if value < 0:
            raise DurationError(f"duration cannot be negative: {value}")
        return timedelta(days=value)

    text = str(value).strip()
    if text.lower() == NEVER:
        return None

    match = _PATTERN.match(text)
    if not match:
        raise DurationError(
            f"cannot parse duration {value!r}. Use a number with an optional unit "
            f"of d, w, m or y (for example 60d, 12w, 6m, 2y), or 'never'."
        )

    amount = int(match.group(1))
    unit = (match.group(2) or "d").lower()
    return timedelta(days=amount * _UNITS[unit])


def format_duration(delta: timedelta | None) -> str:
    """Render a duration in days, the canonical form.

    Always days, never the largest fitting unit. Collapsing 60d to "2m" would
    mean `config show` echoes back something the user never wrote, and every
    value the specification lists (7d, 30d, 60d, 90d, 180d, 365d) is in days.
    """
    if delta is None:
        return NEVER
    return f"{int(delta.total_seconds() // 86400)}d"


def cutoff(delta: timedelta | None, *, now: datetime | None = None) -> datetime | None:
    """The instant before which an asset is older than the retention window.

    Returns None when the policy is "never", which callers must read as
    "nothing qualifies", never as "everything qualifies".
    """
    if delta is None:
        return None
    return (now or datetime.now(UTC)) - delta
