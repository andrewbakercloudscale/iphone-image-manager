"""Place names for assets, read from the Photos library.

Photos reverse-geocodes on device and stores the result. Reading it means no
network call, no bundled dataset and no new dependency, and the names match what
the user already sees in Photos. That satisfies the privacy rule in
`docs/PLAN.md` decision 15 without building a geocoder.

Two sources, because neither is complete on its own:

- `ZMOMENT.ZTITLE`, the name of the time-and-place cluster Photos groups an
  asset into. Human-facing and often the better name: "Home", "Hout Bay".
- `ZADDITIONALASSETATTRIBUTES.ZREVERSELOCATIONDATA`, a per-asset postal address
  holding point of interest, neighbourhood, city, province and country.

Measured on a real library, they overlap almost entirely: 69% and 67% of camera
photos respectively, 69% for either. The remaining 31% carry GPS that Photos has
never reverse-geocoded, and nothing here invents a name for them.
"""

from __future__ import annotations

import plistlib
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from ..logs import get_logger
from .library_db import LibraryDbError, _snapshot

log = get_logger("photos.places")

_MOMENTS_SQL = """
    SELECT a.ZUUID AS uuid, m.ZTITLE AS title
    FROM ZASSET a
    JOIN ZMOMENT m ON m.Z_PK = a.ZMOMENT
    WHERE a.ZTRASHEDSTATE = 0 AND m.ZTITLE IS NOT NULL
"""

_ADDRESS_SQL = """
    SELECT a.ZUUID AS uuid, x.ZREVERSELOCATIONDATA AS address
    FROM ZASSET a
    JOIN ZADDITIONALASSETATTRIBUTES x ON x.ZASSET = a.Z_PK
    WHERE a.ZTRASHEDSTATE = 0 AND x.ZREVERSELOCATIONDATA IS NOT NULL
"""


@dataclass
class Places:
    """Place name per asset UUID, and how much of the library it covered."""

    by_uuid: dict[str, str] = field(default_factory=dict)
    total: int = 0
    degraded_reason: str | None = None

    @property
    def coverage(self) -> float:
        return len(self.by_uuid) / self.total if self.total else 0.0

    def for_local_identifier(self, local_identifier: str) -> str | None:
        return self.by_uuid.get(local_identifier.split("/", 1)[0])


def tidy(name: str) -> str:
    """Normalise a place name so two spellings do not become two folders.

    Photos writes non-breaking spaces inside names ("Hout\\xa0Bay") and joins
    two places with either an ampersand or an en dash. On a real library that
    split one visit to Constantia and Hout Bay into folders of 19 and 23.
    """
    text = name.replace("\xa0", " ")
    # Escaped rather than literal: ruff flags ambiguous unicode dashes, and a
    # rule about dashes is easier to read when you can see which dash it means.
    for separator in ("&", "\u2013", "\u2014"):
        text = text.replace(separator, " and ")
    return " ".join(text.split())


def _city_from_address(blob: bytes) -> str | None:
    """Pull the city out of Photos' archived postal address.

    The blob is an NSKeyedArchiver plist, so rather than decode the object graph
    we take the one-line address Photos also stores -- "Victoria Wharf Shopping
    Centre, Cape Town, Western Cape, 8001, South Africa" -- and read the city
    from it. Fragile by nature, so every failure returns None and the asset
    simply has no place rather than a wrong one.
    """
    try:
        objects = plistlib.loads(blob).get("$objects", [])
    except Exception:  # an undocumented archive format; never fatal
        return None
    if not isinstance(objects, list):
        return None
    lines = [o for o in objects if isinstance(o, str) and o.count(",") >= 3]
    if not lines:
        return None
    parts = [p.strip() for p in max(lines, key=len).split(",")]
    # ... street, CITY, province, postcode, country
    return parts[-4] if len(parts) >= 4 and parts[-4] else None


def read_places(library: Path) -> Places:
    """Map asset UUID to a place name, preferring what Photos shows the user."""
    try:
        snapshot = _snapshot(library)
    except LibraryDbError as exc:
        log.warning("place names unavailable: %s", exc)
        return Places(degraded_reason=str(exc))

    by_uuid: dict[str, str] = {}
    try:
        conn = sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            total = conn.execute("SELECT COUNT(*) FROM ZASSET WHERE ZTRASHEDSTATE = 0").fetchone()[
                0
            ]
            # The per-asset address first, so the moment title can override it:
            # "Home" is more useful to a person than the suburb it sits in.
            for row in conn.execute(_ADDRESS_SQL):
                city = _city_from_address(row["address"])
                if city:
                    by_uuid[row["uuid"]] = tidy(city)
            for row in conn.execute(_MOMENTS_SQL):
                by_uuid[row["uuid"]] = tidy(row["title"])
        except sqlite3.OperationalError as exc:
            reason = (
                f"the Photos schema does not match what this build expects ({exc}). "
                f"Place names are unavailable, so assets will be filed by month."
            )
            log.warning("%s", reason)
            return Places(degraded_reason=reason)
        finally:
            conn.close()
    finally:
        import shutil

        shutil.rmtree(snapshot.parent, ignore_errors=True)

    log.info("read a place name for %d of %d assets", len(by_uuid), total)
    return Places(by_uuid=by_uuid, total=total)
