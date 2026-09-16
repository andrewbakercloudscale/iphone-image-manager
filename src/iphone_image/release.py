"""Release the Mac copy of assets that are safely in the cloud.

The step that makes the whole product work on a Mac with no room for the
library. `docs/PLAN.md` decision 14b: the archive is a staging buffer, not a
destination, so once an asset is verified in the cloud the local copy is let go
and the disk is handed back to the next chunk.

It is also the most dangerous code written so far, because it deletes the user's
only local copy of a photograph. Four rules, and none of them is optional:

- **The ledger saying CLOUD_VERIFIED is not enough.** The remote is asked, in
  this same invocation, what it currently holds, and the hash it reports must
  match. A record written last week is a claim about last week. This is the same
  rule `docs/SAFETY.md` section 7 applies to device removal: plans are computed
  against a fresh reading, never against stored state.
- **Never `unlink`.** Files go to the macOS Trash through the Swift helper, so
  everything released is restorable from Finder. Decision 12.
- **Anything never uploaded keeps its bytes.** Junk deleted from the phone has no
  cloud copy, so the recycle bin is its only backup. Only cloud-verified assets
  are eligible here.
- **The ledger is updated per file, after that file has gone**, so an
  interruption can never leave a row saying a file is present when it is not.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .cloud import CloudProvider, RcloneProvider
from .config import Config
from .db.database import Database, utcnow
from .journal import Journal, Op
from .logs import get_logger
from .photos.helper import DEFAULT_HELPER, HelperError
from .selector import Selector

log = get_logger("release")

#: What a released asset's local_status becomes. It is deliberately not
#: DISCOVERED: the file is gone on purpose and must never be fetched again.
RELEASED = "RELEASED"


class ReleaseError(Exception):
    """Release cannot safely proceed."""


@dataclass
class Candidate:
    asset_id: int
    local_path: Path
    relative: str
    cloud_path: str
    sha256: str
    size_bytes: int


@dataclass
class ReleaseResult:
    examined: int = 0
    eligible: int = 0
    released: int = 0
    bytes_freed: int = 0
    blocked: int = 0
    failed: int = 0
    blocks: dict[str, int] = field(default_factory=dict)
    failures: list[dict[str, Any]] = field(default_factory=list)

    def block(self, reason: str) -> None:
        self.blocked += 1
        self.blocks[reason] = self.blocks.get(reason, 0) + 1


def plan(
    config: Config,
    db: Database,
    selector: Selector,
    *,
    provider: CloudProvider | None = None,
) -> tuple[list[Candidate], ReleaseResult]:
    """Everything provably safe to let go of, and why the rest is not."""
    where, params = selector.where()
    rows = db.conn.execute(
        f"SELECT a.* FROM assets a WHERE {where} "
        f"AND a.local_status = 'LOCAL_VERIFIED' AND a.local_path IS NOT NULL "
        f"ORDER BY a.created_at_device",
        params,
    ).fetchall()

    result = ReleaseResult()
    archive = config.archive.local_path
    wanted: list[Candidate] = []

    for row in rows:
        asset = dict(row)
        result.examined += 1
        if asset.get("cloud_status") != "CLOUD_VERIFIED":
            result.block("not verified in the cloud")
            continue
        if not asset.get("cloud_path"):
            result.block("no cloud path recorded")
            continue
        if not asset.get("sha256"):
            result.block("no local hash recorded")
            continue
        local = Path(asset["local_path"])
        if not local.exists():
            result.block("the local file is already gone")
            continue
        relative = str(asset["cloud_path"]).removeprefix(f"{config.cloud.destination}/")
        wanted.append(
            Candidate(
                asset_id=int(asset["id"]),
                local_path=local,
                relative=relative,
                cloud_path=str(asset["cloud_path"]),
                sha256=str(asset["sha256"]),
                size_bytes=int(asset.get("size_bytes") or 0),
            )
        )

    if not wanted:
        return [], result

    # Ask the remote what it holds *now*. The ledger records what was true when
    # the upload ran, and this is about to delete the only other copy.
    provider = provider or RcloneProvider(config.cloud.remote)
    provider.check()
    remote = provider.hashes(config.cloud.destination)
    log.info("the remote reports %d file(s) at %s", len(remote), config.cloud.destination)

    eligible: list[Candidate] = []
    for candidate in wanted:
        digest = remote.get(candidate.relative)
        if digest is None:
            result.block("not at the remote right now")
            continue
        if digest != candidate.sha256:
            result.block("the remote copy does not match the recorded hash")
            continue
        eligible.append(candidate)

    result.eligible = len(eligible)
    _ = archive  # kept for symmetry with the other engines
    return eligible, result


def _trash(paths: list[Path], *, binary: Path | None = None) -> dict[str, int]:
    """Move files to the macOS Trash. Returns bytes freed per path that went."""
    helper = Path(binary) if binary else DEFAULT_HELPER
    if not helper.exists():
        raise HelperError(
            f"the helper is not built at {helper}. "
            f"Build it with: cd spikes/iimphotos && swift build -c release"
        )
    payload = "".join(f"{p}\n" for p in paths)
    process = subprocess.run(
        [str(helper), "trash"], input=payload, capture_output=True, text=True, timeout=3600
    )
    freed: dict[str, int] = {}
    for line in process.stdout.splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("event") == "trashed":
            freed[record["path"]] = int(record.get("bytes") or 0)
        elif record.get("event") == "trashFailed":
            log.warning("could not trash %s: %s", record.get("path"), record.get("error"))
    return freed


def run(
    config: Config,
    selector: Selector,
    *,
    provider: CloudProvider | None = None,
    db: Database | None = None,
    helper: Path | None = None,
    on_progress: Callable[[ReleaseResult], None] | None = None,
) -> ReleaseResult:
    """Trash the local copy of everything proved to be in the cloud."""
    if not config.safety.require_cloud_verification:
        raise ReleaseError(
            "safety.require_cloud_verification is false. Releasing a local copy "
            "without a verified cloud copy is not something this will do."
        )

    owned = db is None
    db = db or Database(config.database.path).connect()
    if owned:
        db.migrate()
    journal = Journal(db)

    try:
        eligible, result = plan(config, db, selector, provider=provider)
        if not eligible:
            return result

        with journal.operation(
            Op.TRASH,
            command="release",
            detail={"eligible": len(eligible), "bytes": sum(c.size_bytes for c in eligible)},
        ) as operation:
            # In batches, so an interruption costs one batch rather than the run,
            # and so the ledger is never far behind the filesystem.
            batch_size = 500
            for start in range(0, len(eligible), batch_size):
                batch = eligible[start : start + batch_size]
                freed = _trash([c.local_path for c in batch], binary=helper)
                for candidate in batch:
                    if str(candidate.local_path) not in freed:
                        result.failed += 1
                        result.failures.append(
                            {"file": str(candidate.local_path), "error": "could not be trashed"}
                        )
                        continue
                    # After the file has gone, never before: a row must not say
                    # a file is present when it is not.
                    db.conn.execute(
                        "UPDATE assets SET local_status = ?, local_path = NULL, "
                        "local_verified_at = NULL, updated_at = ? WHERE id = ?",
                        (RELEASED, utcnow(), candidate.asset_id),
                    )
                    result.released += 1
                    result.bytes_freed += freed[str(candidate.local_path)]
                if on_progress:
                    on_progress(result)
            operation.note(released=result.released, failed=result.failed, bytes=result.bytes_freed)
    finally:
        if owned:
            db.close()

    return result
