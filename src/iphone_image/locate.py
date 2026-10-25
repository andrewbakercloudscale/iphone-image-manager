"""Where each asset was taken, from Photos' own reverse geocoding, into the ledger.

`places` already reads place names to name archive folders, but keeps them in
memory. This writes country, region, city and suburb to `locations`, linked by
`assets.location_id`, so the selector can filter on them (`--country`,
`--city`) and the Mac app can chart the library by place with one query.

Metadata only: it never touches a file, the phone or the cloud, and re-running
it is safe. A row is keyed by the asset's coordinates rounded to three places,
about 100 m, as migration 0001 laid out, so nearby photos share one row.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import Config
from .db.database import Database, utcnow
from .logs import get_logger
from .photos.places import Address, read_addresses

log = get_logger("locate")

SOURCE = "photos"


@dataclass
class LocateResult:
    assets: int = 0
    located: int = 0
    no_coordinates: int = 0
    places: int = 0
    degraded: str | None = None


def run(
    config: Config, db: Database, *, addresses: dict[str, Address] | None = None
) -> LocateResult:
    result = LocateResult()
    if addresses is None:
        addresses, result.degraded = read_addresses(config.photos.library_path)
    rows = db.conn.execute(
        "SELECT id, identity_key, latitude, longitude, location_id FROM assets"
    ).fetchall()
    result.assets = len(rows)
    now = utcnow()
    cells: dict[tuple[float, float], int] = {}
    db.conn.execute("BEGIN")
    try:
        for row in rows:
            uuid = str(row["identity_key"] or "").split("/", 1)[0]
            address = addresses.get(uuid)
            if address is None:
                continue
            if row["latitude"] is None or row["longitude"] is None:
                # An address with nothing to key it on. Photos had GPS once;
                # the ledger never recorded it, so the place is not invented.
                result.no_coordinates += 1
                continue
            cell = (round(float(row["latitude"]), 3), round(float(row["longitude"]), 3))
            location_id = cells.get(cell) or _location(db, cell, address, now)
            cells[cell] = location_id
            if row["location_id"] != location_id:
                db.conn.execute(
                    "UPDATE assets SET location_id = ?, updated_at = ? WHERE id = ?",
                    (location_id, now, row["id"]),
                )
            result.located += 1
        db.conn.execute("COMMIT")
    except Exception:
        db.conn.execute("ROLLBACK")
        raise
    result.places = len(cells)
    log.info("located %d of %d assets in %d places", result.located, result.assets, result.places)
    return result


def _location(db: Database, cell: tuple[float, float], address: Address, now: str) -> int:
    values: dict[str, Any] = {
        "country": address.country,
        "region": address.region,
        "city": address.city,
        "suburb": address.suburb,
    }
    db.conn.execute(
        "INSERT INTO locations (lat_rounded, lon_rounded, country, region, city, suburb, "
        "source, looked_up_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (lat_rounded, lon_rounded) DO UPDATE SET country = excluded.country, "
        "region = excluded.region, city = excluded.city, suburb = excluded.suburb, "
        "source = excluded.source, looked_up_at = excluded.looked_up_at",
        (*cell, values["country"], values["region"], values["city"], values["suburb"], SOURCE, now),
    )
    row = db.conn.execute(
        "SELECT id FROM locations WHERE lat_rounded = ? AND lon_rounded = ?", cell
    ).fetchone()
    return int(row["id"])


def summary(db: Database, *, limit: int = 30) -> dict[str, Any]:
    """On-phone bytes by country and city, with how much of each is in the cloud."""
    verified = "a.cloud_status = 'CLOUD_VERIFIED'"
    cols = (
        f"COUNT(*) n, COALESCE(SUM(a.size_bytes), 0) b, "
        f"COALESCE(SUM(CASE WHEN {verified} THEN a.size_bytes END), 0) vb"
    )
    base = (
        "FROM assets a LEFT JOIN locations l ON l.id = a.location_id WHERE a.present_on_phone = 1"
    )
    countries = db.conn.execute(
        f"SELECT l.country, {cols} {base} AND l.country IS NOT NULL "
        f"GROUP BY l.country ORDER BY b DESC LIMIT ?",
        (limit,),
    ).fetchall()
    cities = db.conn.execute(
        f"SELECT l.country, l.city, {cols} {base} AND l.city IS NOT NULL "
        f"GROUP BY l.country, l.city ORDER BY b DESC LIMIT ?",
        (limit,),
    ).fetchall()
    unplaced = db.conn.execute(f"SELECT {cols} {base} AND l.id IS NULL").fetchone()

    def tally(r: Any) -> dict[str, int]:
        return {"count": int(r["n"]), "bytes": int(r["b"]), "verifiedBytes": int(r["vb"])}

    return {
        "countries": [{"country": r["country"], **tally(r)} for r in countries],
        "cities": [{"country": r["country"], "city": r["city"], **tally(r)} for r in cities],
        "noLocation": tally(unplaced),
    }
