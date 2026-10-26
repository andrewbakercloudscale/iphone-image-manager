"""Move files already in the cloud to where the current layout puts them.

Changing a destination or a channel's pattern changes where new backups go,
not where earlier ones are. This moves the earlier ones on the remote and
rewrites the ledger to match. Nothing is uploaded or downloaded: rclone moves
within one remote server-side, so 20,000 files cost API calls, not bandwidth.

Rules, in the spirit of `relocate`:

- **The ledger follows the remote, never leads it.** After each folder's move
  the new folder is listed with hashes, and only an asset whose SHA-256 is found
  at its new path has its `cloud_path` rewritten. A move that half happened, or
  happened and was then interrupted, is finished by re-running: the source is
  gone, the listing finds the file, and the ledger catches up.
- **Only files with no Mac copy.** An asset still in the archive would have its
  local file in one layout and its cloud copy in another. Release it first.
- **Never a pattern with {event}.** Place folders are clustered over what is on
  the phone now; an asset removed since would be re-filed into its month and
  leave the trip it was filed with.
"""

from __future__ import annotations

import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from .cloud import (
    CloudError,
    CloudProvider,
    RcloneProvider,
    destination_for,
    hash_name_of,
    recorded_digest,
)
from .config import Config
from .db.database import Database, utcnow
from .logs import get_logger
from .selector import channel_of
from .sync import archive_path_for

log = get_logger("refile")


@dataclass
class RemoteMove:
    asset_id: int
    channel: str
    sha256: str
    old: str
    new: str
    claim: str
    md5: str | None = None

    @property
    def folders(self) -> tuple[str, str]:
        return str(PurePosixPath(self.old).parent), str(PurePosixPath(self.new).parent)


@dataclass
class RefilePlan:
    moves: list[RemoteMove] = field(default_factory=list)
    already: int = 0
    skipped: dict[str, int] = field(default_factory=lambda: defaultdict(int))


@dataclass
class RefileResult:
    moved: int = 0
    failures: list[dict[str, str]] = field(default_factory=list)


def plan(config: Config, db: Database, channels: list[str]) -> RefilePlan:
    """Every cloud copy in `channels` that is not where the layout now says."""
    result = RefilePlan()
    archive = config.archive.local_path
    taken = {
        str(r[0])
        for r in db.conn.execute("SELECT cloud_path FROM assets WHERE cloud_path IS NOT NULL")
    }
    claimed: set[str] = set()
    rows = db.conn.execute(
        "SELECT * FROM assets WHERE cloud_status = 'CLOUD_VERIFIED' "
        "AND cloud_path IS NOT NULL AND cloud_path != '' ORDER BY id"
    ).fetchall()
    for row in rows:
        asset: dict[str, Any] = dict(row)
        channel = channel_of(asset)
        if channel not in channels:
            continue
        if "{event}" in config.organization.pattern_for(channel) and (
            channel not in config.organization.flat_channels
        ):
            result.skipped["its layout names places, which cannot be re-filed safely"] += 1
            continue
        if not asset.get("sha256"):
            result.skipped["no hash recorded, so the move could not be checked"] += 1
            continue
        old = str(asset["cloud_path"])
        name = PurePosixPath(old).name
        folder = archive_path_for(config, asset)
        try:
            relative = folder.relative_to(archive / channel)
        except ValueError:
            result.skipped["the layout does not start with the source folder"] += 1
            continue
        destination = destination_for(config, asset.get("media_type"), channel)
        new = str(PurePosixPath(destination) / PurePosixPath(*relative.parts) / name)
        if new == old:
            result.already += 1
            continue
        local = asset.get("local_path")
        if local and Path(local).exists():
            result.skipped["still has a copy on this Mac; release it first"] += 1
            continue
        if new in taken or new in claimed:
            result.skipped["another file already has that name in the new folder"] += 1
            continue
        claimed.add(new)
        result.moves.append(
            RemoteMove(
                asset_id=int(asset["id"]),
                channel=channel,
                sha256=str(asset["sha256"]),
                old=old,
                new=new,
                claim=str(PurePosixPath(channel) / PurePosixPath(*relative.parts) / name),
                md5=asset.get("md5"),
            )
        )
    return result


def run(
    config: Config,
    db: Database,
    moves: list[RemoteMove],
    *,
    provider: CloudProvider | None = None,
    on_progress: Any = None,
) -> RefileResult:
    """Move each folder's files, check them by hash, then update the ledger."""
    rclone = provider or RcloneProvider(config.cloud.remote)
    result = RefileResult()
    groups: dict[tuple[str, str], list[RemoteMove]] = defaultdict(list)
    for move in moves:
        groups[move.folders].append(move)
    for index, ((source, target), batch) in enumerate(sorted(groups.items()), 1):
        if on_progress:
            on_progress(f"[{index}/{len(groups)}] {len(batch)} files to {target}")
        try:
            _move(rclone, source, target, [PurePosixPath(m.old).name for m in batch])
        except CloudError as exc:
            # Some may have moved anyway, or moved on an earlier run: the
            # listing below decides, not the exit code.
            log.warning("move to %s reported: %s", target, exc)
        try:
            found = rclone.hashes(target)
        except CloudError as exc:
            result.failures += [
                {"file": m.old, "error": f"could not list {target}: {exc}"} for m in batch
            ]
            continue
        now = utcnow()
        with db.transaction():
            for move in batch:
                name = PurePosixPath(move.new).name
                expected = recorded_digest(move, hash_name_of(rclone))
                if expected is None or found.get(name) != expected:
                    result.failures.append({"file": move.old, "error": "not found at the new path"})
                    continue
                db.conn.execute(
                    "UPDATE assets SET cloud_path = ?, archive_claim = ?, updated_at = ? "
                    "WHERE id = ?",
                    (move.new, move.claim, now, move.asset_id),
                )
                db.conn.execute(
                    "UPDATE cloud_objects SET path = ?, updated_at = ? "
                    "WHERE asset_id = ? AND path = ?",
                    (move.new, now, move.asset_id, move.old),
                )
                result.moved += 1
    return result


def _move(rclone: Any, source: str, target: str, names: list[str]) -> None:
    move = getattr(rclone, "move", None)
    if move is not None:
        move(source, target, names)
        return
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as listing:
        listing.write("\n".join(names) + "\n")
    try:
        rclone._run(
            [
                "move",
                f"{rclone.remote}:{source}",
                f"{rclone.remote}:{target}",
                "--files-from",
                listing.name,
                "--no-traverse",
                "--transfers",
                "8",
            ],
            timeout=rclone.batch_timeout,
        )
    finally:
        Path(listing.name).unlink(missing_ok=True)
