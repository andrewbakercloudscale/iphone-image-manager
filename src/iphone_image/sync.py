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
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .config import Config
from .db.database import Database, utcnow
from .journal import Journal, Op
from .logs import get_logger
from .organize.events import assign
from .organize.paths import render_pattern, unique_filename
from .photos.helper import Helper
from .photos.places import read_places
from .selector import ChunkPlan, Selector, channel_of, plan_chunk

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
    #: Split at the asset, not at the run. A chunk that mixes resident assets
    #: with genuinely remote ones averages to a number that is neither rate:
    #: measured here at 10.12 MB/s while the instantaneous network rate was
    #: 2.6. Only these two feed any estimate.
    network_bytes: int = 0
    network_seconds: float = 0.0
    local_bytes: int = 0
    local_count: int = 0
    remaining_assets: int = 0
    remaining_bytes: int = 0
    too_large: int = 0
    #: Set when the chunk ended before its plan did, and why. A run that stopped
    #: early is not a run that finished, and the difference must survive into
    #: the report rather than looking like a smaller chunk than was planned.
    stopped_early: str | None = None
    failures: list[dict[str, Any]] = field(default_factory=list)
    by_type: dict[str, int] = field(default_factory=dict)

    @property
    def rate_mb_s(self) -> float:
        """Download rate, from the downloads alone."""
        if not self.network_seconds:
            return 0.0
        return self.network_bytes / self.network_seconds / 1_048_576

    @property
    def rate_text(self) -> str:
        """Readable rate.

        Three things this has to tell apart, and the first two were once the
        same bug at different scales. A chunk of already-resident assets is a
        disk read, and reporting "4696.44 MB/s" or "0.00 MB/s" for work that
        never touched the network is worse than reporting nothing. A chunk that
        mixes the two is the subtler one: its aggregate sits comfortably under
        any sane threshold and looks like a real measurement, while being an
        average of 4,000 MB/s and 1.5 MB/s. Only actual downloads count here.
        """
        if not self.fetched:
            return "-"
        if not self.network_bytes:
            return f"{self.fetched:,} from local disk, no download"
        text = f"{self.rate_mb_s:.2f} MB/s"
        if self.local_count:
            text += f" ({self.local_count:,} of {self.fetched:,} were already local)"
        return text


#: Used only until this installation has downloaded anything of its own.
FALLBACK_MB_S = 1.0

#: Stop a chunk after this many failures in a row.
#:
#: A real run lost its network partway and marked 1,435 assets FAILED in quick
#: succession, one per remaining asset in the chunk. Nothing was lost -- a
#: FAILED row is re-queued by the next run -- but attempting asset 1,435 after
#: 1,434 consecutive failures cannot succeed, and the state it leaves behind
#: reads as 1,435 individually broken photographs rather than one outage.
#:
#: Counted rather than diagnosed. Matching Apple's error strings to recognise
#: "offline" would be reading a message where a signal already exists: whatever
#: the cause, this many failures in a row means the next attempt is pointless.
CONSECUTIVE_FAILURE_LIMIT = 10


def observed_rate(db: Database, *, minimum_bytes: int = 20 * 1024 * 1024) -> float | None:
    """Download rate in MB/s from this installation's own history.

    A hardcoded constant was wrong by nearly three times within a day: the first
    measurement was taken while Apple's metadata sync competed for bandwidth. An
    estimate that learns from what actually happened here beats one encoding a
    number from somebody else's afternoon.

    It reads `networkBytes` and `networkSeconds`, which count only assets this
    installation actually downloaded. Rows without them predate that split and
    are **ignored rather than approximated**: their `bytes` covers local reads
    and downloads together, and there is no way to recover the proportion after
    the fact. That is deliberate. An older row recorded a mixed chunk at
    10 MB/s while the real rate was 1.5, and using it would have predicted the
    next all-iCloud chunk at 0.4 hours instead of 2.8. Until a chunk completes
    under the new measurement this returns None, and the caller says the rate is
    assumed rather than measured, which is the true statement.

    Runs that downloaded almost nothing are still skipped, because a handful of
    small files says nothing about throughput.
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
        if "networkBytes" not in detail:
            continue  # predates the split; its composition is unrecoverable
        moved = int(detail.get("networkBytes") or 0)
        seconds = float(detail.get("networkSeconds") or 0)
        if moved < minimum_bytes or seconds <= 0:
            continue
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


def event_folders(config: Config, db: Database, *, also: list[str] | None = None) -> dict[str, str]:
    """The folder every asset belongs in, for the `{event}` token.

    Clustered over **what the archive will actually hold**: assets already
    archived, plus `also`, the chunk about to be fetched. Both halves of that
    matter and the first draft got it wrong in each direction.

    Clustering over one chunk alone would name the same trip differently
    depending on which chunk it fell in -- five Cape Town photos become a month
    bucket, two hundred become a folder.

    Clustering over the whole library is worse, and this is the one that
    shipped for a few minutes: a place passes the minimum on WhatsApp images and
    screenshots that are never archived, so the folder is created for the two
    camera photos that are. It produced 46 folders holding fewer than the ten
    photos configured as the minimum, including "2019/11 London" holding two.
    **The threshold has to count the files that will exist, not the assets that
    inspired them.**

    Skipped entirely when the pattern does not ask for it, because reading the
    Photos database costs a copy of it.
    """
    if "{event}" not in config.organization.pattern:
        return {}
    places = read_places(config.photos.library_path)
    placeholders = ",".join("?" for _ in (also or []))
    extra = f" OR identity_key IN ({placeholders})" if also else ""
    rows = db.conn.execute(
        f"SELECT identity_key, created_at_device FROM assets "
        # RELEASED counts too: its folder is already fixed in the cloud, so
        # dropping it from the clustering could rename a trip that is by then
        # only reachable under its old name.
        f"WHERE present_on_phone = 1 "
        f"AND (local_status IN ('LOCAL_VERIFIED', 'RELEASED'){extra})",
        list(also or []),
    ).fetchall()
    folders, _ = assign(
        [dict(r) for r in rows],
        places.by_uuid,
        gap=timedelta(days=config.organization.event_gap_days),
        min_photos=config.organization.event_min_photos,
    )
    return folders


def archive_path_for(
    config: Config, asset: dict[str, Any], *, events: dict[str, str] | None = None
) -> Path:
    """Where this asset belongs in the archive, per the configured pattern."""
    created = asset.get("created_at_device") or ""
    event = (events or {}).get(str(asset.get("identity_key") or ""))
    if event is None and created:
        # No assignment for this asset: its month, which is what an unnamed
        # asset gets anyway. Never a different answer from the one `assign` gives.
        event = created[5:7] or None
    values: dict[str, str | None] = {
        "event": event,
        "source": channel_of(asset),
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
        f"SELECT a.* FROM assets a WHERE {where} "
        # RELEASED is excluded as firmly as LOCAL_VERIFIED. Its file was deleted
        # on purpose, because a verified cloud copy replaced it; selecting it
        # again would download it, release it, and download it forever.
        f"AND a.local_status NOT IN ('LOCAL_VERIFIED', 'RELEASED') "
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
        # The chunk counts toward its own clustering: these assets are about to
        # be archived, so they are part of what the folders will hold.
        events = event_folders(config, db, also=[str(a["identity_key"]) for a in chunk.included])
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
            consecutive_failures = 0
            for asset in chunk.included:
                failed_before = result.failed
                _fetch_one(config, db, journal, helper, asset, result, events)
                if on_asset:
                    on_asset(asset, result)

                if result.failed > failed_before:
                    consecutive_failures += 1
                else:
                    consecutive_failures = 0

                if consecutive_failures >= CONSECUTIVE_FAILURE_LIMIT:
                    result.stopped_early = (
                        f"stopped after {consecutive_failures} failures in a row, which is "
                        f"an outage rather than {consecutive_failures} bad assets. Nothing "
                        f"is lost: the next run picks up where this one stopped."
                    )
                    log.warning("%s", result.stopped_early)
                    break
                # Re-checked between assets: a chunk that would breach the floor
                # part way through stops cleanly rather than filling the disk.
                if free_bytes(config.archive.local_path) < floor:
                    result.stopped_early = (
                        "stopped: free space reached the configured floor. Free some disk, "
                        "lower chunking.chunk_bytes, or point archive.local_path elsewhere."
                    )
                    log.warning("%s", result.stopped_early)
                    break
            operation.note(
                fetched=result.fetched,
                failed=result.failed,
                bytes=result.bytes_fetched,
                transferSeconds=round(result.seconds, 3),
                networkBytes=result.network_bytes,
                networkSeconds=round(result.network_seconds, 3),
                localBytes=result.local_bytes,
                localCount=result.local_count,
                stoppedEarly=result.stopped_early,
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
    events: dict[str, str] | None = None,
) -> None:
    identifier = asset["identity_key"]
    directory = archive_path_for(config, asset, events=events)
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
    seconds = float(outcome.get("seconds") or 0)
    result.seconds += seconds
    # The threshold applied to this asset rather than to the run. Applied to the
    # run it only catches a chunk that was entirely local; a mixed chunk lands
    # under it and is recorded as though every byte came over the network.
    if seconds > 0 and actual / seconds / 1_048_576 <= LOCAL_READ_MB_S:
        result.network_bytes += actual
        result.network_seconds += seconds
    else:
        result.local_bytes += actual
        result.local_count += 1
    media = asset.get("media_type") or "OTHER"
    result.by_type[media] = result.by_type.get(media, 0) + 1
