"""Populate the ledger from the Photos library.

Read-only with respect to the library, and costs no bandwidth: everything here
comes from metadata PhotoKit answers without downloading a single original.

A rescan is an update, not a re-import. Assets are keyed by their PhotoKit local
identifier, and anything not seen by the newest completed scan is marked absent
with a timestamp rather than deleted, because "it was here last week and is gone
now" is evidence and deleting the row would destroy it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Config
from .db.database import Database, utcnow
from .journal import Journal, Op
from .logs import get_logger
from .photos.helper import Helper
from .photos.library_db import LibraryDbError, SourceApps, read_source_apps
from .selector import PROXY_SUSPICION_BLOCK

log = get_logger("scanner")

#: Below this many bytes per pixel a still image is very unlikely to be a
#: full-resolution original. See docs/SAFETY.md section 2.
PROXY_BYTES_PER_PIXEL = 0.12
PROXY_MIN_PIXELS = 1_000_000

MEDIA_TYPES = {1: "PHOTO", 2: "VIDEO", 3: "AUDIO"}


@dataclass
class ScanResult:
    scan_id: int
    seen: int = 0
    inserted: int = 0
    updated: int = 0
    disappeared: int = 0
    total_bytes: int = 0
    proxy_suspects: int = 0
    source_coverage: float = 0.0
    degraded: str | None = None
    by_channel: dict[str, int] = field(default_factory=dict)


def proxy_suspicion(record: dict[str, Any]) -> tuple[float, list[str]]:
    """Score how likely this asset is an iCloud proxy rather than an original."""
    evidence: list[str] = []
    width = int(record.get("width") or 0)
    height = int(record.get("height") or 0)
    size = int(record.get("sizeBytes") or 0)
    duration = float(record.get("durationSeconds") or 0)

    if duration > 0 or not width or not height or not size:
        return 0.0, evidence

    pixels = width * height
    if pixels < PROXY_MIN_PIXELS:
        return 0.0, evidence

    bytes_per_pixel = size / pixels
    if bytes_per_pixel < PROXY_BYTES_PER_PIXEL:
        evidence.append(
            f"{bytes_per_pixel:.3f} bytes per pixel at {width}x{height}, "
            f"below the {PROXY_BYTES_PER_PIXEL} threshold"
        )
        # Scale the score: the further below, the more confident.
        return min(1.0, (PROXY_BYTES_PER_PIXEL - bytes_per_pixel) / PROXY_BYTES_PER_PIXEL), evidence

    return 0.0, evidence


def _device_id(db: Database, library: Path) -> int:
    """One device row per Photos library, keyed by its path."""
    row = db.conn.execute("SELECT id FROM devices WHERE udid = ?", (str(library),)).fetchone()
    if row:
        db.conn.execute(
            "UPDATE devices SET last_seen_at = ?, updated_at = ? WHERE id = ?",
            (utcnow(), utcnow(), row["id"]),
        )
        return int(row["id"])
    cursor = db.conn.execute(
        "INSERT INTO devices (udid, name, product_kind, first_seen_at, last_seen_at, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (str(library), library.name, "PhotosLibrary", utcnow(), utcnow(), utcnow(), utcnow()),
    )
    return int(cursor.lastrowid or 0)


def scan(
    config: Config, *, limit: int = 0, helper: Helper | None = None, progress: Any = None
) -> ScanResult:
    """Scan the library into the ledger."""
    library = config.photos.library_path
    helper = helper or Helper()
    helper.check()

    # Source application is the field PhotoKit does not expose and the one that
    # makes channel filters possible at all.
    try:
        sources: SourceApps = read_source_apps(library)
    except LibraryDbError as exc:
        log.warning("source application unavailable: %s", exc)
        sources = SourceApps(by_uuid={}, total=0, with_bundle_id=0, degraded_reason=str(exc))

    db = Database(config.database.path).connect()
    db.migrate()
    journal = Journal(db)
    device_id = _device_id(db, library)

    cursor = db.conn.execute(
        "INSERT INTO scans (device_id, started_at, status, media_presentation) "
        "VALUES (?, ?, 'RUNNING', 'original')",
        (device_id, utcnow()),
    )
    scan_id = int(cursor.lastrowid or 0)
    result = ScanResult(scan_id=scan_id)
    result.degraded = sources.degraded_reason
    result.source_coverage = sources.coverage

    with journal.operation(
        Op.SCAN_STARTED, command="scan", device_id=device_id, scan_id=scan_id
    ) as operation:
        try:
            for record in helper.scan(limit=limit):
                event = record.get("event")
                if event == "asset":
                    _upsert(db, device_id, scan_id, record, sources, result, library)
                    if progress and result.seen % 2000 == 0:
                        progress(result)
                elif event == "error":
                    raise RuntimeError(record.get("message", "helper reported an error"))
        except BaseException as exc:
            db.conn.execute(
                "UPDATE scans SET status = 'FAILED', finished_at = ?, error = ? WHERE id = ?",
                (utcnow(), f"{type(exc).__name__}: {exc}", scan_id),
            )
            raise

        # Anything the newest scan did not see is gone from the library. Record
        # that rather than deleting the row: the disappearance is the evidence.
        disappeared = db.conn.execute(
            "UPDATE assets SET present_on_phone = 0, updated_at = ? "
            "WHERE device_id = ? AND present_on_phone = 1 "
            "AND (last_seen_scan_id IS NULL OR last_seen_scan_id != ?)",
            (utcnow(), device_id, scan_id),
        )
        result.disappeared = disappeared.rowcount if limit == 0 else 0

        db.conn.execute(
            "UPDATE scans SET status = 'COMPLETED', finished_at = ?, assets_seen = ?, "
            "bytes_seen = ? WHERE id = ?",
            (utcnow(), result.seen, result.total_bytes, scan_id),
        )
        operation.note(
            seen=result.seen,
            inserted=result.inserted,
            updated=result.updated,
            disappeared=result.disappeared,
        )

    db.close()
    return result


def _upsert(
    db: Database,
    device_id: int,
    scan_id: int,
    record: dict[str, Any],
    sources: SourceApps,
    result: ScanResult,
    library: Path,
) -> None:
    identifier = record["localIdentifier"]
    bundle_id = sources.for_local_identifier(identifier)
    suspicion, evidence = proxy_suspicion(record)
    size = int(record.get("sizeBytes") or 0)

    result.seen += 1
    result.total_bytes += size
    if suspicion >= PROXY_SUSPICION_BLOCK:
        result.proxy_suspects += 1
    channel = bundle_id or "(unattributed)"
    result.by_channel[channel] = result.by_channel.get(channel, 0) + 1

    values = {
        "device_id": device_id,
        "identity_key": identifier,
        "device_asset_id": identifier,
        "filename": record.get("filename"),
        "original_filename": record.get("filename"),
        "media_type": MEDIA_TYPES.get(int(record.get("mediaType") or 0), "OTHER"),
        "uti": record.get("uti"),
        "created_at_device": record.get("createdAt"),
        "modified_at_device": record.get("modifiedAt"),
        "size_bytes": size,
        "width": record.get("width"),
        "height": record.get("height"),
        "duration_seconds": record.get("durationSeconds"),
        "latitude": record.get("latitude"),
        "longitude": record.get("longitude"),
        "source_bundle_id": bundle_id,
        "burst_uuid": record.get("burstIdentifier"),
        "subtypes": json.dumps(record.get("subtypes") or []),
        "is_favourite": 1 if record.get("isFavorite") else 0,
        "is_hidden": 1 if record.get("isHidden") else 0,
        "source_type": record.get("sourceType"),
        "album_names": json.dumps(record.get("albums") or []),
        "proxy_suspicion": suspicion,
        "proxy_evidence": json.dumps(evidence) if evidence else None,
        "media_presentation": "original",
        "present_on_phone": 1,
        "last_seen_at": utcnow(),
        "last_seen_scan_id": scan_id,
        "library_path": str(library),
        "updated_at": utcnow(),
    }

    existing = db.conn.execute(
        "SELECT id FROM assets WHERE device_id = ? AND identity_key = ?",
        (device_id, identifier),
    ).fetchone()

    if existing:
        assignments = ", ".join(f"{k} = ?" for k in values)
        db.conn.execute(
            f"UPDATE assets SET {assignments} WHERE id = ?",
            [*values.values(), existing["id"]],
        )
        result.updated += 1
    else:
        values["first_seen_at"] = utcnow()
        values["created_at"] = utcnow()
        columns = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        db.conn.execute(
            f"INSERT INTO assets ({columns}) VALUES ({placeholders})",
            list(values.values()),
        )
        result.inserted += 1
