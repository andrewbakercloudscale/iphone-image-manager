"""Re-file the archive to match the configured root and pattern.

Changing `archive.local_path` or `organization.pattern` changes where the tool
thinks every asset belongs, but not where the files already are. Until they
agree, `sync` writes new assets to the new layout while the old ones sit
somewhere the tool would no longer put them.

This moves the files and rewrites the ledger to match. Three rules:

- **Rename, never copy-then-delete.** `shutil.move` falls back to copying
  across volumes and then unlinking the source, and unlinking user media is
  banned here. `os.rename` cannot do that: it either moves atomically or
  raises, so a cross-volume archive move is refused with an explanation rather
  than performed unsafely.
- **The ledger is updated per file, immediately after that file's rename.** An
  interruption therefore leaves both halves consistent for every asset, and
  re-running finishes the job. There is no window where the ledger points at a
  path that does not exist.
- **Destinations are planned against the whole plan, not just the disk.** Two
  assets whose names collide only after re-filing would otherwise both be
  handed the same free-looking name; a file about to move away is likewise not
  an obstacle.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Config
from .db.database import Database, utcnow
from .journal import Journal, Op
from .logs import get_logger
from .organize.paths import sanitize_segment, unique_filename
from .sync import archive_claim_for, archive_path_for, event_folders

log = get_logger("relocate")


@dataclass
class Move:
    asset_id: int
    source: Path
    destination: Path
    size_bytes: int = 0


def _is_month_folder(folder: str) -> bool:
    """A bare month bucket like "11", or "09-12" for a span."""
    return bool(folder) and all(part.isdigit() for part in folder.split("-"))


def unnaming(moves: list[Move]) -> int:
    """Moves that would take a file out of a named folder into a bare month.

    The check that should have existed before this was ever run. Place names
    come from Photos' own reverse geocoding, read live, and that data is not
    stable: the library here grew from 78,806 assets to 94,678 as an iCloud
    backfill completed, and Photos had not yet analysed the arrivals, so
    coverage fell from 69% to 24.8%. A relocate run at that moment would have
    moved 8,590 files out of "2019/11-12 Cape Town" and into "2019/11",
    cheerfully, because every input it had said the month was correct.

    It would also have desynced the archive from the cloud, where those files
    are already stored under the named path.

    So a plan that *removes* names is refused rather than performed. Adding
    names is normal and always allowed; taking them away means the place data
    got worse, which is a reason to wait, not to rewrite the archive.
    """
    count = 0
    for move in moves:
        if _is_month_folder(move.destination.parent.name) and not _is_month_folder(
            move.source.parent.name
        ):
            count += 1
    return count


@dataclass
class RelocateResult:
    examined: int = 0
    unnaming: int = 0
    already_in_place: int = 0
    planned: int = 0
    moved: int = 0
    missing: int = 0
    failed: int = 0
    bytes_moved: int = 0
    directories_removed: int = 0
    failures: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.failed == 0


def plan(config: Config, db: Database) -> tuple[list[Move], RelocateResult]:
    """Work out every move, touching nothing."""
    rows = db.conn.execute(
        "SELECT * FROM assets WHERE local_path IS NOT NULL AND local_path != '' ORDER BY id"
    ).fetchall()

    result = RelocateResult()
    moves: list[Move] = []
    events = event_folders(config, db)

    # Two passes, and the split between them is the whole point.
    #
    # A file sitting on a name we want is not an obstacle if it is itself moving
    # away in the same plan. But an asset that is *already where it belongs* is
    # not moving away, and treating it as though it were is how a mover gets
    # handed a stayer's path and `os.rename` destroys the file on it. That
    # happened: 36 files, 91 MB, silently overwritten, and the ledger was left
    # with two assets pointing at one file. Pass one therefore decides who stays
    # before pass two decides where anyone goes.
    staying: set[str] = set()
    movers: list[tuple[dict[str, Any], Path, Path, str]] = []

    for row in rows:
        asset = dict(row)
        current = Path(asset["local_path"])
        result.examined += 1

        if not current.exists():
            # Say so; do not invent a move for a file that is not there. sync's
            # own reconcile_missing is what puts such a row back in the queue.
            result.missing += 1
            continue

        directory = archive_path_for(config, asset, events=events)
        preferred = sanitize_segment(asset.get("filename") or current.name, fallback="unnamed")

        if directory / preferred == current:
            result.already_in_place += 1
            staying.add(str(current))
            continue

        movers.append((asset, current, directory, preferred))

    # Only a mover vacates. Only a mover can be given a name.
    vacating = {str(current) for _, current, _, _ in movers}
    claimed: set[str] = set(staying)

    # Names held by assets this plan says nothing about: the released ones,
    # whose file is gone but whose claim on the name is not. Moving a file onto
    # one of those would put two assets on one archive path, and then on one
    # remote path. Rows with a live local_path are all above, as movers or
    # stayers, so this is exactly the set the plan cannot see.
    archive = config.archive.local_path
    released_claims = {
        str(row[0])
        for row in db.conn.execute(
            "SELECT archive_claim FROM assets WHERE archive_claim IS NOT NULL "
            "AND archive_claim != '' AND (local_path IS NULL OR local_path = '')"
        ).fetchall()
    }

    for asset, current, directory, preferred in movers:

        def taken(candidate: Path, *, _self: str = str(current)) -> bool:
            if str(candidate) in claimed:
                return True
            if str(candidate) == _self:
                return False
            if archive_claim_for(archive, candidate) in released_claims:
                return True
            return candidate.exists() and str(candidate) not in vacating

        name = unique_filename(
            directory,
            preferred,
            asset.get("sha256") or str(asset.get("identity_key") or "").replace("-", ""),
            taken=taken,
        )
        destination = directory / name
        if destination == current:
            result.already_in_place += 1
            claimed.add(str(current))
            continue

        claimed.add(str(destination))
        moves.append(
            Move(
                asset_id=int(asset["id"]),
                source=current,
                destination=destination,
                size_bytes=int(asset.get("size_bytes") or 0),
            )
        )

    result.planned = len(moves)
    result.unnaming = unnaming(moves)
    return moves, result


def prune_empty_directories(vacated: set[Path], *, keep: Path) -> int:
    """Remove the folders a move left empty, walking up from each one.

    Only ever `rmdir`, which refuses a non-empty directory, so this cannot take
    anything still in use with it. It stops at the configured archive root, and
    it will not remove a directory sitting directly in the home folder: emptying
    the last file out of ~/Pictures must not then delete ~/Pictures.
    """
    home = Path.home()
    removed = 0
    # Deepest first, so a parent emptied by its children going is seen after them.
    for directory in sorted(vacated, key=lambda p: len(p.parts), reverse=True):
        current = directory
        while current != keep and current.parent not in (current, home):
            try:
                current.rmdir()
            except OSError:
                break  # not empty, or already gone. Either way, stop climbing.
            removed += 1
            current = current.parent
    return removed


class RelocateError(Exception):
    """Relocation cannot safely proceed."""


def run(
    config: Config,
    *,
    db: Database | None = None,
    allow_unnaming: bool = False,
    on_move: Callable[[Move, RelocateResult], None] | None = None,
) -> RelocateResult:
    """Execute the plan, unless it would take names away.

    `allow_unnaming` is the deliberate override, and it is off by default
    because the situation it guards is indistinguishable from normal input:
    Photos simply reports fewer places than it did, and every folder the plan
    proposes looks correct.
    """
    owned = db is None
    db = db or Database(config.database.path).connect()
    if owned:
        db.migrate()

    try:
        moves, result = plan(config, db)
        if result.unnaming and not allow_unnaming:
            raise RelocateError(
                f"{result.unnaming:,} file(s) would move out of a named folder into a bare "
                f"month, which means Photos is currently reporting fewer places than when "
                f"the archive was filed -- not that the archive is wrong.\n"
                f"  Place names are read live from the Photos library and its coverage moves: "
                f"it fell from 69% to 24.8% here when an iCloud backfill added 15,000 "
                f"unanalysed assets.\n"
                f"  Cloud copies are already stored under the named paths, so renaming now "
                f"would desync them. Wait for Photos to finish analysing, or pass "
                f"allow_unnaming to override."
            )
        if not moves:
            return result

        journal = Journal(db)
        old_roots = {move.source.parent for move in moves}

        with journal.operation(
            Op.RELOCATE,
            command="relocate",
            detail={
                "planned": len(moves),
                "root": str(config.archive.local_path),
                "pattern": config.organization.pattern,
            },
        ) as operation:
            for move in moves:
                if _move_one(db, config.archive.local_path, move, result) and on_move:
                    on_move(move, result)
            operation.note(moved=result.moved, failed=result.failed, bytes=result.bytes_moved)

        # The year/month shells the move emptied, and the archive root itself if
        # this was a move to a new one.
        result.directories_removed = prune_empty_directories(
            old_roots, keep=config.archive.local_path
        )
    finally:
        if owned:
            db.close()

    return result


def _move_one(db: Database, archive: Path, move: Move, result: RelocateResult) -> bool:
    try:
        move.destination.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        result.failed += 1
        result.failures.append({"file": str(move.source), "error": f"cannot create folder: {exc}"})
        return False

    if not move.source.exists():
        result.missing += 1
        return False

    try:
        source_device = move.source.stat().st_dev
        target_device = move.destination.parent.stat().st_dev
    except OSError as exc:
        result.failed += 1
        result.failures.append({"file": str(move.source), "error": str(exc)})
        return False

    if source_device != target_device:
        # Refused rather than attempted: the only way to cross a volume is copy
        # then delete, and deleting user media is not something this does.
        result.failed += 1
        result.failures.append(
            {
                "file": str(move.source),
                "error": (
                    "the destination is on a different volume. Moving would mean copying "
                    "and then deleting the original, which this tool does not do to media. "
                    "Move the archive with Finder, then run this again to rewrite the ledger."
                ),
            }
        )
        return False

    try:
        size = move.source.stat().st_size
        os.rename(move.source, move.destination)
    except OSError as exc:
        result.failed += 1
        result.failures.append({"file": str(move.source), "error": str(exc)})
        return False

    # Immediately, so an interruption never leaves the ledger pointing at a path
    # that no longer holds the file.
    db.conn.execute(
        # The claim moves with the file. It is what stops a released asset's
        # name being handed to another asset, so leaving it on the old path
        # would protect a name nothing uses and free one something does.
        "UPDATE assets SET local_path = ?, archive_claim = ?, updated_at = ? WHERE id = ?",
        (
            str(move.destination),
            archive_claim_for(archive, move.destination),
            utcnow(),
            move.asset_id,
        ),
    )
    result.moved += 1
    result.bytes_moved += size
    return True
