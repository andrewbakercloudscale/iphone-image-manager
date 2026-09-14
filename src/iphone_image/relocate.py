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
from .sync import archive_path_for

log = get_logger("relocate")


@dataclass
class Move:
    asset_id: int
    source: Path
    destination: Path
    size_bytes: int = 0


@dataclass
class RelocateResult:
    examined: int = 0
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

    # Every path this plan will vacate. A file sitting on a name we want is not
    # an obstacle if it is itself moving away in the same plan.
    vacating = {str(Path(row["local_path"])) for row in rows}
    claimed: set[str] = set()

    for row in rows:
        asset = dict(row)
        current = Path(asset["local_path"])
        result.examined += 1

        if not current.exists():
            # Say so; do not invent a move for a file that is not there. sync's
            # own reconcile_missing is what puts such a row back in the queue.
            result.missing += 1
            continue

        directory = archive_path_for(config, asset)
        preferred = sanitize_segment(asset.get("filename") or current.name, fallback="unnamed")

        if directory / preferred == current:
            result.already_in_place += 1
            continue

        def taken(candidate: Path, *, _self: str = str(current)) -> bool:
            if str(candidate) in claimed:
                return True
            if str(candidate) == _self:
                return False
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


def run(
    config: Config,
    *,
    db: Database | None = None,
    on_move: Callable[[Move, RelocateResult], None] | None = None,
) -> RelocateResult:
    """Execute the plan."""
    owned = db is None
    db = db or Database(config.database.path).connect()
    if owned:
        db.migrate()

    try:
        moves, result = plan(config, db)
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
                if _move_one(db, move, result) and on_move:
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


def _move_one(db: Database, move: Move, result: RelocateResult) -> bool:
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
        "UPDATE assets SET local_path = ?, updated_at = ? WHERE id = ?",
        (str(move.destination), utcnow(), move.asset_id),
    )
    result.moved += 1
    result.bytes_moved += size
    return True
