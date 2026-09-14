"""Chunked local synchronisation.

One chunk is: select by the selector until the byte budget is reached, fetch
each original, hash it, verify it, move it into the archive, record it. The next
run resumes from the ledger.

Three rules this module exists to enforce:

- The budget is decided before any byte moves. Sizes are exact, so a chunk is a
  plan you can read rather than something you discover at the end.
- A partially transferred file is never mistaken for a verified one. Transfers
  land on a `.partial` path and are only renamed after the hash is computed and
  the size checked.
- Video is never fetched unless asked for by name. It is 83.5 GB from 3% of the
  items in the library this was built against.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import Config
from .db.database import Database, utcnow
from .journal import Journal, Op
from .logs import get_logger
from .organize.paths import render_pattern, unique_filename
from .photos.helper import Helper
from .selector import ChunkPlan, Selector, plan_chunk

log = get_logger("sync")

HASH_BLOCK = 1024 * 1024

#: Above this, a 'transfer' was a local disk read rather than an iCloud
#: download. Real downloads measured 0.45 to 1.24 MB/s; local reads
#: exceeded 4,000, and averaging the two understated every estimate.
LOCAL_READ_MB_S = 25.0


class SyncError(Exception):
    """Sync cannot safely proceed."""


@dataclass
class SyncResult:
    planned: int = 0
    missing_recovered: int = 0
    partials_swept: int = 0
    fetched: int = 0
    skipped_existing: int = 0
    failed: int = 0
    bytes_fetched: int = 0
    seconds: float = 0.0
    remaining_assets: int = 0
    remaining_bytes: int = 0
    too_large: int = 0
    failures: list[dict[str, Any]] = field(default_factory=list)
    by_type: dict[str, int] = field(default_factory=dict)

    @property
    def rate_mb_s(self) -> float:
        return (self.bytes_fetched / self.seconds / 1_048_576) if self.seconds else 0.0

    @property
    def rate_text(self) -> str:
        """Readable rate.

        Assets already resident read from disk in near-zero time, which made a
        naive rate print "0.00 MB/s" for a transfer that in fact never touched
        the network. Reporting a speed that low for work that was instant is
        worse than reporting nothing.
        """
        if not self.fetched:
            return "-"
        # The same threshold the estimator uses. A whole chunk of already
        # resident assets reported "4696.44 MB/s", which is a disk read wearing
        # a network rate's clothes and tells the user nothing useful.
        if self.seconds < 0.05 or self.rate_mb_s > LOCAL_READ_MB_S:
            return f"{self.fetched:,} from local disk, no download"
        return f"{self.rate_mb_s:.2f} MB/s"


#: Used only until this installation has downloaded anything of its own.
FALLBACK_MB_S = 1.0


def observed_rate(db: Database, *, minimum_bytes: int = 20 * 1024 * 1024) -> float | None:
    """Download rate in MB/s from this installation's own history.

    A hardcoded constant was wrong by nearly three times within a day: the first
    measurement was taken while Apple's metadata sync competed for bandwidth. An
    estimate that learns from what actually happened here beats one encoding a
    number from somebody else's afternoon.

    Two exclusions, the first of which cost a wrong answer before it existed:

    - Assets already resident are read from local disk at hundreds of MB/s.
      Mixed into the average they produced 3.77 MB/s where the real network rate
      was 1.24, which would have understated every estimate.
    - Runs that moved almost nothing, because a handful of small files says
      nothing about throughput.
    """
    rows = db.conn.execute(
        "SELECT detail FROM operations "
        "WHERE operation = ? AND status = 'COMPLETED' ORDER BY id DESC LIMIT 20",
        (Op.DOWNLOAD,),
    ).fetchall()

    total_bytes = 0
    total_seconds = 0.0
    for row in rows:
        try:
            detail = json.loads(row["detail"] or "{}")
        except json.JSONDecodeError:
            continue
        moved = int(detail.get("bytes") or 0)
        seconds = float(detail.get("transferSeconds") or 0)
        if moved < minimum_bytes or seconds <= 0:
            continue
        if moved / seconds / 1_048_576 > LOCAL_READ_MB_S:
            continue  # a disk read, not a download
        total_bytes += moved
        total_seconds += seconds

    if total_bytes < minimum_bytes or total_seconds <= 0:
        return None
    return total_bytes / total_seconds / 1_048_576


def estimate_hours(byte_count: int, rate_mb_s: float | None) -> float:
    rate = rate_mb_s or FALLBACK_MB_S
    return byte_count / 1_048_576 / rate / 3600


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(HASH_BLOCK):
            digest.update(block)
    return digest.hexdigest()


def archive_path_for(config: Config, asset: dict[str, Any]) -> Path:
    """Where this asset belongs in the archive, per the configured pattern."""
    created = asset.get("created_at_device") or ""
    values: dict[str, str | None] = {
        "year": created[:4] or None,
        "month": created[5:7] or None,
        "day": created[8:10] or None,
        "country": asset.get("country"),
        "region": asset.get("region"),
        "city": asset.get("city"),
        "suburb": asset.get("suburb"),
        "type": (asset.get("media_type") or "").lower() or None,
        "device": None,
        "camera_make": asset.get("camera_make"),
        "camera_model": asset.get("camera_model"),
    }
    relative = render_pattern(config.organization.pattern, values)
    return config.archive.local_path / relative


def sweep_partials(archive: Path) -> tuple[int, int]:
    """Delete leftover .partial files. Returns (count, bytes reclaimed).

    A .partial is by definition never a valid archive file: the rename only
    happens after the hash and size are checked. A hard kill leaves one behind
    for whatever was in flight, and left alone they accumulate one per
    interruption and quietly consume disk.
    """
    count = 0
    reclaimed = 0
    if not archive.exists():
        return 0, 0
    for stale in archive.rglob("*.partial"):
        try:
            reclaimed += stale.stat().st_size
            stale.unlink()
            count += 1
        except OSError as exc:
            log.warning("could not remove %s: %s", stale, exc)
    if count:
        log.info("swept %d leftover partial file(s), %d bytes", count, reclaimed)
    return count, reclaimed


def free_bytes(path: Path) -> int:
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return shutil.disk_usage(probe).free


def reconcile_missing(db: Database, selector: Selector) -> int:
    """Reset assets recorded as verified whose archive file has gone.

    Spec section 9 requires sync to identify missing local files. Without this a
    row marked LOCAL_VERIFIED is excluded from every future chunk, so a file
    deleted or lost from the archive would never be fetched again and the ledger
    would keep claiming a backup that does not exist.

    One stat() per verified asset in the selector's scope, which is bounded.
    """
    where, params = selector.where()
    rows = db.conn.execute(
        f"SELECT a.id, a.local_path FROM assets a WHERE {where} "
        f"AND a.local_status = 'LOCAL_VERIFIED'",
        params,
    ).fetchall()

    missing = [
        row["id"] for row in rows if not row["local_path"] or not Path(row["local_path"]).exists()
    ]
    for asset_id in missing:
        db.conn.execute(
            "UPDATE assets SET local_status = 'DISCOVERED', local_path = NULL, "
            "local_verified_at = NULL, updated_at = ? WHERE id = ?",
            (utcnow(), asset_id),
        )
    if missing:
        log.warning("%d archive file(s) are missing and will be fetched again", len(missing))
    return len(missing)


def _candidates(db: Database, selector: Selector) -> list[dict[str, Any]]:
    where, params = selector.where()
    rows = db.conn.execute(
        f"SELECT a.* FROM assets a WHERE {where} AND a.local_status != 'LOCAL_VERIFIED' "
        f"ORDER BY {selector.order_by()}",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def plan(
    config: Config,
    selector: Selector,
    *,
    budget_bytes: int | None = None,
    db: Database | None = None,
) -> tuple[ChunkPlan, list[dict[str, Any]]]:
    """Decide the chunk without fetching anything."""
    owned = db is None
    db = db or Database(config.database.path).connect()
    if owned:
        db.migrate()
    try:
        reconcile_missing(db, selector)
        candidates = _candidates(db, selector)
    finally:
        if owned:
            db.close()
    budget = config.chunking.chunk_bytes if budget_bytes is None else budget_bytes
    return plan_chunk(candidates, budget_bytes=budget, limit=selector.limit), candidates


def run(
    config: Config,
    selector: Selector,
    *,
    budget_bytes: int | None = None,
    helper: Helper | None = None,
    on_asset: Callable[[dict[str, Any], SyncResult], None] | None = None,
) -> SyncResult:
    """Fetch one chunk."""
    helper = helper or Helper()
    helper.check()

    db = Database(config.database.path).connect()
    db.migrate()
    journal = Journal(db)
    result = SyncResult()

    try:
        # Anything left by a previous interruption. Doing this first means a
        # kill never costs more than the asset that was in flight.
        result.partials_swept, _ = sweep_partials(config.archive.local_path)
        result.missing_recovered = reconcile_missing(db, selector)
        chunk, _ = plan(config, selector, budget_bytes=budget_bytes, db=db)
        result.planned = chunk.count
        result.remaining_assets = chunk.remaining_assets
        result.remaining_bytes = chunk.remaining_bytes
        result.too_large = len(chunk.skipped_too_large)

        if not chunk.included:
            return result

        # Refuse to start rather than fail part way through a chunk.
        floor = config.chunking.free_space_floor
        available = free_bytes(config.archive.local_path)
        if available - chunk.total_bytes < floor:
            raise SyncError(
                f"this chunk needs {chunk.total_bytes / 1024**3:.1f} GB and only "
                f"{available / 1024**3:.1f} GB is free, which would leave less than the "
                f"{floor / 1024**3:.0f} GB floor. Lower chunking.chunk_bytes, free space, "
                f"or point archive.local_path at a larger volume."
            )

        with journal.operation(
            Op.DOWNLOAD, command="sync", detail={"planned": chunk.count, "bytes": chunk.total_bytes}
        ) as operation:
            for asset in chunk.included:
                _fetch_one(config, db, journal, helper, asset, result)
                if on_asset:
                    on_asset(asset, result)
                # Re-checked between assets: a chunk that would breach the floor
                # part way through stops cleanly rather than filling the disk.
                if free_bytes(config.archive.local_path) < floor:
                    log.warning("free space fell below the floor, stopping this chunk")
                    result.failures.append(
                        {
                            "filename": "(chunk)",
                            "error": "stopped: free space reached the configured floor",
                        }
                    )
                    break
            operation.note(
                fetched=result.fetched,
                failed=result.failed,
                bytes=result.bytes_fetched,
                transferSeconds=round(result.seconds, 3),
            )
    finally:
        db.close()

    return result


def _fetch_one(
    config: Config,
    db: Database,
    journal: Journal,
    helper: Helper,
    asset: dict[str, Any],
    result: SyncResult,
) -> None:
    identifier = asset["identity_key"]
    directory = archive_path_for(config, asset)
    directory.mkdir(parents=True, exist_ok=True)

    # An asset already recorded keeps its path, so a resumed run overwrites
    # nothing and never makes a second copy.
    existing = asset.get("local_path")
    if existing and Path(existing).exists() and asset.get("local_status") == "LOCAL_VERIFIED":
        result.skipped_existing += 1
        return

    name = unique_filename(
        directory,
        asset.get("filename") or identifier,
        asset.get("sha256") or identifier.replace("-", ""),
    )
    final = directory / name
    partial = final.with_name(final.name + ".partial")

    records = list(helper.export([(identifier, partial)], timeout=1800))
    outcome = next((r for r in records if r.get("event") == "exported"), None)

    if outcome is None or outcome.get("error"):
        message = (outcome or {}).get("error", "the helper returned no result")
        result.failed += 1
        result.failures.append({"filename": asset.get("filename"), "error": message})
        partial.unlink(missing_ok=True)
        db.conn.execute(
            "UPDATE assets SET local_status = 'FAILED', updated_at = ? WHERE id = ?",
            (utcnow(), asset["id"]),
        )
        log.warning("fetch failed for %s: %s", asset.get("filename"), message)
        return

    # Verify before the file is allowed to look like a backup.
    actual = partial.stat().st_size
    declared = int(asset.get("size_bytes") or 0)
    if declared and actual != declared:
        partial.unlink(missing_ok=True)
        result.failed += 1
        result.failures.append(
            {
                "filename": asset.get("filename"),
                "error": f"size mismatch: got {actual}, expected {declared}",
            }
        )
        return

    digest = sha256_file(partial)
    partial.replace(final)

    # Preserve the capture date on the file, per spec section 37.
    created = asset.get("created_at_device")
    if created:
        try:
            stamp = datetime.fromisoformat(created).timestamp()
            import os

            os.utime(final, (stamp, stamp))
        except (ValueError, OSError):
            pass

    db.conn.execute(
        "UPDATE assets SET local_path = ?, local_status = 'LOCAL_VERIFIED', "
        "sha256 = ?, local_verified_at = ?, updated_at = ? WHERE id = ?",
        (str(final), digest, utcnow(), utcnow(), asset["id"]),
    )

    result.fetched += 1
    result.bytes_fetched += actual
    result.seconds += float(outcome.get("seconds") or 0)
    media = asset.get("media_type") or "OTHER"
    result.by_type[media] = result.by_type.get(media, 0) + 1
