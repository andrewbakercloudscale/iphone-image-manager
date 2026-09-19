"""Checking the ledger against what the remote actually holds.

Every other check in this tool reads the ledger. This one does not trust it.

On 2026-09-17 the ledger said 22,888 assets were CLOUD_VERIFIED. Google Drive
held 22,880 files. Eight pairs of distinct photographs -- different capture
dates, different hashes -- shared one remote file each, because releasing the
Mac copy freed the archive filename and the next asset with the same camera
filename was handed it, then overwrote its twin on upload. Both rows of each
pair read CLOUD_VERIFIED. Only one of each was there.

Nothing was lost; all sixteen were still on the phone. But CLOUD_VERIFIED is
what `remove_from_iphone.policy: cloud_verified` consults before deleting from
the phone, so the next removal pass would have made it permanent.

No existing check could have found it, and the reason is the point: **the
ledger cannot verify the ledger.** `release` re-checks the remote, but only for
the file it is about to delete, and only while a local copy still exists. Once
an asset is released it is never looked at again.

Two checks, deliberately separated by cost:

- `conflicts` is offline and instant. It finds rows that contradict *each
  other* -- two assets claiming one remote path, two claiming one archive name,
  a verified asset with nothing recorded to verify against. Cheap enough to run
  inside `doctor` on every invocation, and it alone would have caught the 2026-09-17
  defect: the eight pairs were eight duplicate `cloud_path` values.
- `remote` asks the provider for a recursive listing with hashes and diffs it
  against every CLOUD_VERIFIED row. It is the only check that can find a file
  that has gone missing at the remote, or whose bytes have changed, and it
  costs one listing of the whole archive -- minutes, not seconds.

Both report how much they examined. A check that has silently stopped covering
anything must look different from one that found nothing wrong.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .cloud import (
    CloudProvider,
    RcloneProvider,
    destinations,
    nested_destinations,
    split_cloud_path,
)
from .config import Config
from .db.database import Database
from .logs import get_logger

log = get_logger("audit")


@dataclass
class Conflict:
    """Two rows that cannot both be true."""

    kind: str
    detail: str
    asset_ids: list[int] = field(default_factory=list)


@dataclass
class ConflictReport:
    rows_examined: int = 0
    conflicts: list[Conflict] = field(default_factory=list)
    #: Checks that could not run, and why. Never silently empty-skipped: a
    #: check absent from a report reads as a check that passed, which is the
    #: failure mode this whole module exists to answer.
    skipped: list[str] = field(default_factory=list)
    #: Checks that did run, so "no contradictions" can be read alongside how
    #: many questions were actually asked.
    ran: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.conflicts

    @property
    def complete(self) -> bool:
        return not self.skipped


def _columns(db: Database) -> set[str]:
    return {str(row[1]) for row in db.conn.execute("PRAGMA table_info(assets)").fetchall()}


def conflicts(db: Database) -> ConflictReport:
    """Rows that contradict each other. No network, no filesystem.

    A ledger older than migration 0004 has no `archive_claim`, so that check
    cannot run. It is reported as skipped rather than passed -- an unmigrated
    database is exactly when someone is most likely to be looking.
    """
    report = ConflictReport()
    report.rows_examined = int(db.conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0] or 0)
    columns = _columns(db)

    # Two assets recorded at one remote path. This is the shape of the
    # 2026-09-17 defect, and the cheapest thing that finds it.
    for row in db.conn.execute(
        "SELECT cloud_path, COUNT(*) n, GROUP_CONCAT(id) ids FROM assets "
        "WHERE cloud_path IS NOT NULL AND cloud_path != '' "
        "GROUP BY cloud_path HAVING n > 1 ORDER BY n DESC"
    ).fetchall():
        report.conflicts.append(
            Conflict(
                "duplicate cloud path",
                f"{row['n']} assets recorded at {row['cloud_path']}",
                [int(i) for i in str(row["ids"]).split(",")],
            )
        )

    report.ran.append("duplicate cloud path")

    # Two assets claiming one archive name. The same defect one step earlier,
    # before anything has been uploaded.
    if "archive_claim" not in columns:
        report.skipped.append(
            "duplicate archive name: this ledger has no archive_claim column "
            "(migration 0004). Run any command to migrate it."
        )
    else:
        report.ran.append("duplicate archive name")
        for row in db.conn.execute(
            "SELECT archive_claim, COUNT(*) n, GROUP_CONCAT(id) ids FROM assets "
            "WHERE archive_claim IS NOT NULL AND archive_claim != '' "
            "GROUP BY archive_claim HAVING n > 1 ORDER BY n DESC"
        ).fetchall():
            report.conflicts.append(
                Conflict(
                    "duplicate archive name",
                    f"{row['n']} assets claiming {row['archive_claim']}",
                    [int(i) for i in str(row["ids"]).split(",")],
                )
            )

    # Verified against nothing. A row in this state cannot be checked by any
    # later run, so it would stay wrong forever while reading as safe.
    for column, kind in (
        ("cloud_path", "verified with no cloud path"),
        ("sha256", "verified with no hash"),
    ):
        ids = [
            int(r["id"])
            for r in db.conn.execute(
                f"SELECT id FROM assets WHERE cloud_status = 'CLOUD_VERIFIED' "
                f"AND ({column} IS NULL OR {column} = '') LIMIT 200"
            ).fetchall()
        ]
        report.ran.append(kind)
        if ids:
            report.conflicts.append(Conflict(kind, f"{len(ids)} asset(s)", ids))

    return report


@dataclass
class RemoteReport:
    """What the remote holds, against what the ledger claims it holds."""

    remote_files: int = 0
    claimed: int = 0
    matched: int = 0
    missing: list[dict[str, Any]] = field(default_factory=list)
    #: No separate size class. The provider reports SHA256 and a size
    #: difference always implies a hash difference, so a `size_mismatch` field
    #: would read 0 forever and look like evidence.
    hash_mismatch: list[dict[str, Any]] = field(default_factory=list)
    #: Paths the remote holds that no CLOUD_VERIFIED row points at. Not an
    #: error on its own -- an interrupted upload leaves these behind -- but it
    #: is the other half of the arithmetic and hiding it makes the two counts
    #: look inexplicable.
    unclaimed: int = 0
    #: Remote entries whose hash the provider did not report. Counted, never
    #: silently treated as a match: an unanswered question is not a yes.
    unhashed: int = 0
    destinations_listed: list[str] = field(default_factory=list)

    @property
    def disagreements(self) -> int:
        return len(self.missing) + len(self.hash_mismatch)

    @property
    def ok(self) -> bool:
        return self.disagreements == 0

    def summary(self) -> str:
        return (
            f"{self.remote_files:,} file(s) at {len(self.destinations_listed)} "
            f"destination(s) against {self.claimed:,} verified row(s)"
        )


def remote(
    config: Config,
    db: Database,
    *,
    provider: CloudProvider | None = None,
) -> RemoteReport:
    """Diff every CLOUD_VERIFIED row against a live listing of the remote.

    One recursive listing per destination in use, then a dictionary comparison.
    The listing is what costs; the comparison is free, so there is no reason to
    sample.
    """
    report = RemoteReport()
    provider = provider or RcloneProvider(config.cloud.remote)
    provider.check()

    holdings: dict[str, dict[str, str]] = {}
    for destination in destinations(config):
        log.info("listing %s", destination)
        listing = provider.hashes(destination)
        # A recursive listing of a parent contains its nested destinations'
        # files. Left in, every screenshot is counted twice as a file at the
        # remote and once as "unclaimed" at the photo archive, which makes the
        # two counts the audit exists to reconcile look inexplicable.
        inner = [n + "/" for n in nested_destinations(config, destination)]
        holdings[destination] = {
            path: digest
            for path, digest in listing.items()
            if not any(path.startswith(n) for n in inner)
        }
        report.destinations_listed.append(destination)
        report.remote_files += len(holdings[destination])

    seen: set[tuple[str, str]] = set()
    rows = db.conn.execute(
        "SELECT id, filename, cloud_path, sha256, size_bytes FROM assets "
        "WHERE cloud_status = 'CLOUD_VERIFIED'"
    ).fetchall()
    report.claimed = len(rows)

    for row in rows:
        asset = dict(row)
        recorded = str(asset.get("cloud_path") or "")
        entry = {"id": int(asset["id"]), "file": asset.get("filename"), "path": recorded}
        if not recorded:
            report.missing.append({**entry, "why": "no cloud path recorded"})
            continue
        destination, relative = split_cloud_path(config, recorded)
        digest = holdings.get(destination, {}).get(relative)
        if digest is None:
            report.missing.append({**entry, "why": "not at the remote"})
            continue
        seen.add((destination, relative))
        if not digest:
            # The provider listed it but would not say what it holds. Neither a
            # match nor a mismatch, and reported as neither.
            report.unhashed += 1
            continue
        if digest != str(asset.get("sha256") or ""):
            report.hash_mismatch.append(
                {**entry, "ledger": str(asset.get("sha256") or "")[:16], "remote": digest[:16]}
            )
            continue
        report.matched += 1

    report.unclaimed = sum(
        1
        for destination, files in holdings.items()
        for relative in files
        if (destination, relative) not in seen
    )
    return report
