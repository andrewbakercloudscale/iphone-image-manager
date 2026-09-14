"""Asset selection and chunk planning.

One vocabulary drives `list`, `sync` and `remove`, so what you previewed is what
acts. A filter can only ever narrow; it can never make a blocked asset eligible.

Chunk budgets are a *selection-time* decision, not a stop-when-full one. PhotoKit
reports an exact `fileSize` for every asset without downloading it, so the whole
chunk is known before the first byte moves. That is what makes "50 GB of camera
photos" a plan you can read rather than a thing you discover afterwards.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .retention import DurationError, parse_duration

#: Friendly channel names to the bundle identifiers iOS records.
CHANNELS: dict[str, str | None] = {
    # Measured against a real 79,024-asset library rather than assumed. An
    # earlier version mapped "camera" to "no importing app", which was wrong:
    # the camera identifies itself like any other app.
    "camera": "com.apple.camera",
    "screenshot": "com.apple.springboard",
    "screenrecording": "com.apple.replayd",
    "whatsapp": "net.whatsapp.WhatsApp",
    "snapchat": "com.toyopagroup.picaboo",
    "safari": "com.apple.mobilesafari",
    "chrome": "com.google.chrome.ios",
    "messages": "com.apple.MobileSMS",
    "twitter": "com.atebits.Tweetie2",
    "instagram": "com.burbn.instagram",
    "facebook": "com.facebook.Facebook",
    "telegram": "ph.telegra.Telegraph",
    "airdrop": "com.apple.sharingd",
    "gmail": "com.google.Gmail",
    "drive": "com.google.Drive",
    "chatgpt": "com.openai.chat",
    "grok": "ai.x.GrokApp",
    # Assets old enough to predate the field: 18,932 of 79,024 in the library
    # this was measured against. Selectable, but not attributable to any app.
    "unattributed": None,
}

MEDIA_TYPES = {"photo", "video", "live", "screenshot", "raw", "burst", "favourite"}

ORDERS = {"oldest", "newest", "largest", "smallest"}

_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([KMGT]?B?)\s*$", re.IGNORECASE)
_SIZE_UNITS = {
    "": 1,
    "B": 1,
    "K": 1024,
    "KB": 1024,
    "M": 1024**2,
    "MB": 1024**2,
    "G": 1024**3,
    "GB": 1024**3,
    "T": 1024**4,
    "TB": 1024**4,
}


class SelectorError(ValueError):
    """Raised when a selector cannot be understood."""


def parse_size(value: str | int | None) -> int | None:
    """Parse '50GB', '10MB', '500KB', or a bare byte count."""
    if value is None:
        return None
    if isinstance(value, int):
        if value < 0:
            raise SelectorError(f"size cannot be negative: {value}")
        return value
    match = _SIZE_RE.match(str(value))
    if not match:
        raise SelectorError(
            f"cannot parse size {value!r}. Use a number with an optional unit, "
            f"for example 500KB, 10MB, 50GB."
        )
    amount, unit = float(match.group(1)), match.group(2).upper()
    return int(amount * _SIZE_UNITS[unit])


def resolve_channel(name: str) -> str | None:
    """Map a friendly channel name to a bundle id, or pass a raw id through."""
    key = name.strip().lower()
    if key in CHANNELS:
        return CHANNELS[key]
    if "." in name:
        return name  # a raw bundle identifier
    known = ", ".join(sorted(CHANNELS))
    raise SelectorError(
        f"unknown channel {name!r}. Known: {known}. A raw bundle identifier also works."
    )


@dataclass
class Selector:
    """What to act on. Every field narrows; none widen."""

    source: str | None = None
    media_type: str | None = None
    older_than: str | None = None
    newer_than: str | None = None
    year: int | None = None
    min_size: str | int | None = None
    max_size: str | int | None = None
    order: str = "oldest"
    include_favourites: bool = True
    include_proxy_suspects: bool = False
    limit: int | None = None

    def __post_init__(self) -> None:
        if self.media_type and self.media_type not in MEDIA_TYPES:
            raise SelectorError(
                f"unknown type {self.media_type!r}. Known: {', '.join(sorted(MEDIA_TYPES))}"
            )
        if self.order not in ORDERS:
            raise SelectorError(f"unknown order {self.order!r}. Known: {', '.join(sorted(ORDERS))}")
        if self.source is not None:
            resolve_channel(self.source)
        # Durations come from a shared parser that raises its own error type.
        # Callers should only ever have to catch SelectorError.
        for field_name in ("older_than", "newer_than"):
            try:
                parse_duration(getattr(self, field_name))
            except DurationError as exc:
                raise SelectorError(f"--{field_name.replace('_', '-')}: {exc}") from exc
        parse_size(self.min_size)
        parse_size(self.max_size)

    # -- compilation -------------------------------------------------------

    def where(self, *, now: datetime | None = None) -> tuple[str, list[Any]]:
        """Compile to a SQL fragment and bound parameters. Never interpolates."""
        now = now or datetime.now(UTC)
        clauses: list[str] = ["a.present_on_phone = 1"]
        params: list[Any] = []

        if self.source is not None:
            clause, extra = self._source_clause()
            clauses.append(clause)
            params.extend(extra)

        if self.media_type == "photo":
            clauses.append("a.media_type = 'PHOTO'")
        elif self.media_type == "video":
            clauses.append("a.media_type = 'VIDEO'")
        elif self.media_type in ("live", "screenshot", "burst"):
            clauses.append("a.subtypes LIKE ?")
            params.append(f"%{self.media_type}%")
        elif self.media_type == "raw":
            clauses.append("a.is_raw = 1")
        elif self.media_type == "favourite":
            clauses.append("a.is_favourite = 1")

        if (delta := parse_duration(self.older_than)) is not None:
            clauses.append("a.created_at_device < ?")
            params.append((now - delta).isoformat())
        if (delta := parse_duration(self.newer_than)) is not None:
            clauses.append("a.created_at_device > ?")
            params.append((now - delta).isoformat())
        if self.year is not None:
            clauses.append("a.created_at_device LIKE ?")
            params.append(f"{self.year}-%")

        if (size := parse_size(self.min_size)) is not None:
            clauses.append("a.size_bytes >= ?")
            params.append(size)
        if (size := parse_size(self.max_size)) is not None:
            clauses.append("a.size_bytes <= ?")
            params.append(size)

        # Protections. These subtract from the set and are never flags that add.
        if not self.include_favourites:
            clauses.append("a.is_favourite = 0")
        if not self.include_proxy_suspects:
            clauses.append("a.proxy_suspicion < 0.5")

        return " AND ".join(clauses), params

    def _source_clause(self) -> tuple[str, list[Any]]:
        """Compile the channel filter.

        `camera` is not a simple equality. iOS only began recording
        com.apple.camera relatively recently: on a real 78,806-asset library it
        covered assets from 2024-08 onwards, while 13,787 older camera photos
        carried no source app at all. Matching the bundle id alone would have
        missed 39 GB of someone's own photographs, which is the worst possible
        way for a backup filter to be wrong.
        """
        assert self.source is not None
        if self.source.strip().lower() == "camera":
            return (
                "(a.source_bundle_id = ? OR (a.source_bundle_id IS NULL "
                "AND a.filename LIKE 'IMG\\_%' ESCAPE '\\' "
                "AND COALESCE(a.subtypes, '') NOT LIKE '%screenshot%'))",
                ["com.apple.camera"],
            )
        bundle = resolve_channel(self.source)
        if bundle is None:
            return "a.source_bundle_id IS NULL", []
        return "a.source_bundle_id = ?", [bundle]

    def order_by(self) -> str:
        return {
            "oldest": "a.created_at_device ASC, a.id ASC",
            "newest": "a.created_at_device DESC, a.id DESC",
            "largest": "a.size_bytes DESC, a.id ASC",
            "smallest": "a.size_bytes ASC, a.id ASC",
        }[self.order]

    def describe(self) -> str:
        bits = []
        if self.source:
            bits.append(f"source={self.source}")
        if self.media_type:
            bits.append(f"type={self.media_type}")
        if self.older_than:
            bits.append(f"older than {self.older_than}")
        if self.newer_than:
            bits.append(f"newer than {self.newer_than}")
        if self.year:
            bits.append(f"year {self.year}")
        if self.min_size:
            bits.append(f"at least {self.min_size}")
        if self.max_size:
            bits.append(f"at most {self.max_size}")
        if not self.include_favourites:
            bits.append("excluding favourites")
        bits.append(f"{self.order} first")
        return ", ".join(bits)


# ---------------------------------------------------------------------------
# Chunk planning
# ---------------------------------------------------------------------------


@dataclass
class ChunkPlan:
    """A decided chunk. Computed before any byte is transferred."""

    included: list[dict[str, Any]] = field(default_factory=list)
    skipped_too_large: list[dict[str, Any]] = field(default_factory=list)
    budget_bytes: int | None = None
    remaining_assets: int = 0
    remaining_bytes: int = 0

    @property
    def total_bytes(self) -> int:
        return sum(int(a.get("size_bytes") or 0) for a in self.included)

    @property
    def count(self) -> int:
        return len(self.included)

    @property
    def exhausted_budget(self) -> bool:
        return self.remaining_assets > 0


def plan_chunk(
    candidates: list[dict[str, Any]],
    *,
    budget_bytes: int | None = None,
    limit: int | None = None,
) -> ChunkPlan:
    """Decide one chunk from an ordered candidate list.

    Selection stops at whichever of the byte budget or the count limit is hit
    first. An asset larger than the entire budget is never silently dropped: it
    is reported separately, because a 60 GB video quietly skipped on every run
    would look like the tool was finished when it was not.
    """
    plan = ChunkPlan(budget_bytes=budget_bytes)
    used = 0

    for index, asset in enumerate(candidates):
        size = int(asset.get("size_bytes") or 0)

        if limit is not None and len(plan.included) >= limit:
            plan.remaining_assets = len(candidates) - index
            plan.remaining_bytes = sum(int(a.get("size_bytes") or 0) for a in candidates[index:])
            break

        if budget_bytes is not None and used + size > budget_bytes:
            if size > budget_bytes:
                # Bigger than any chunk could ever hold. Say so rather than
                # skipping it silently on every future run.
                plan.skipped_too_large.append(asset)
                continue
            plan.remaining_assets = len(candidates) - index
            plan.remaining_bytes = sum(int(a.get("size_bytes") or 0) for a in candidates[index:])
            break

        plan.included.append(asset)
        used += size

    return plan
