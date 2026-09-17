"""Remove assets from the iPhone, which is the only irreversible thing here.

P10. Everything else in this tool copies, verifies or lets go of a local file
that can be fetched again. This deletes the user's photographs from the device
they live on, and `docs/SAFETY.md` is the specification rather than the
commentary. Read section 6 before changing any of it.

The shape is deliberately the same as `release.py`, because the danger is the
same shape: a stored record is a claim about when it was written, and this acts
on the strength of it. So every fact that authorises a deletion is re-read in
the same invocation that performs it.

- **A fresh scan, always** (SAFETY section 7). The plan is computed against a
  scan taken in this run. A ledger row saying an asset is on the phone is not
  evidence that it is on the phone now.
- **Positive evidence per condition** (SAFETY section 6). Never a filename,
  never a path, never an inference. Every blocked asset is individually
  listable with its reason, because a count alone hides which ones.
- **The local copy is re-hashed**, not trusted. A verified copy that has since
  been corrupted or truncated is not a copy.
- **An incomplete asset group is never removable.** A Live Photo whose motion
  half was never archived loses that half forever, and the still image passing
  every other check is exactly what makes it dangerous.
- **Deletion goes through PhotoKit**, so assets land in Recently Deleted and
  stay restorable for 30 days. That window is the second copy the safety model
  counts on while the Mac copy is being released.
- **The ledger is written per asset, after that asset is confirmed gone**, and
  what confirms it is re-fetching from the library rather than a zero exit.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .cloud import CloudProvider, RcloneProvider, split_cloud_path
from .config import Config, RemovalPolicy
from .db.database import Database, utcnow
from .journal import Journal, Op
from .logs import get_logger
from .photos.helper import Helper, HelperError
from .scanner import scan as run_scan
from .selector import Selector

log = get_logger("remove")

#: Read in chunks so a huge file does not come into memory whole.
_HASH_CHUNK = 1024 * 1024

#: One PhotoKit change request per batch. macOS raises a confirmation dialog
#: per invocation, so this is also how many prompts the user sees.
BATCH_SIZE = 500


class RemoveError(Exception):
    """Removal cannot safely proceed."""


#: Policy is compared with `==` and never with `is`. RemovalPolicy is a
#: StrEnum, so a config that reached here carrying the raw string "never"
#: rather than the member -- `model_copy` does not validate, and neither does
#: hand-built config -- would pass an identity check and be treated as *not*
#: never. The failure mode is deleting photographs from a phone whose owner
#: set the policy to never. A destructive test holds this.


@dataclass
class Candidate:
    asset_id: int
    local_identifier: str
    filename: str
    local_path: Path
    sha256: str
    size_bytes: int
    cloud_path: str
    evidence: dict[str, Any]


@dataclass
class RemoveResult:
    scan_id: int = 0
    examined: int = 0
    eligible: int = 0
    removed: int = 0
    already_gone: int = 0
    bytes_removed: int = 0
    blocked: int = 0
    failed: int = 0
    blocks: dict[str, int] = field(default_factory=dict)
    #: Every blocked asset, with its reason. SAFETY section 6: "a count alone
    #: is not acceptable output".
    blocked_assets: list[dict[str, Any]] = field(default_factory=list)
    failures: list[dict[str, Any]] = field(default_factory=list)

    def block(self, asset: dict[str, Any], reason: str) -> None:
        self.blocked += 1
        self.blocks[reason] = self.blocks.get(reason, 0) + 1
        self.blocked_assets.append(
            {
                "id": asset.get("id"),
                "filename": asset.get("filename") or asset.get("original_filename"),
                "reason": reason,
            }
        )


def _digest(path: Path) -> str | None:
    """SHA256 of what is on disk right now, or None if it cannot be read."""
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(_HASH_CHUNK):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError as exc:
        log.warning("cannot read %s: %s", path, exc)
        return None


def _incomplete_group(asset: dict[str, Any], db: Database) -> str | None:
    """Why this asset's group is not fully archived, or None if it is.

    SAFETY section 6: "Any resource in the asset group unverified. A Live Photo
    whose `.MOV` half is not verified is not removable, even if the `.HEIC`
    is."

    Measured on the real archive before this was written: 528 Live Photos
    archived, **zero** `.MOV` files anywhere in the tree, and `asset_resources`
    empty. The motion half of every one of them has never been fetched. The
    still image passes every other check in this module, which is precisely
    what makes it dangerous -- so the check is on the asset's own subtypes,
    which are populated, rather than on the resource table, which is not. A
    gate that cannot see what it is meant to check must block, not wave
    through.
    """
    try:
        subtypes = json.loads(asset.get("subtypes") or "[]")
    except (TypeError, ValueError):
        return "subtypes could not be read, so group completeness is unknown"

    if "live" in subtypes:
        verified = db.conn.execute(
            "SELECT COUNT(*) FROM asset_resources WHERE asset_id = ? "
            "AND resource_type = 'LIVE_PHOTO_VIDEO' AND local_status = 'LOCAL_VERIFIED'",
            (asset["id"],),
        ).fetchone()[0]
        if not verified:
            return "Live Photo whose motion half is not archived"

    burst = asset.get("burst_uuid")
    if burst:
        total, done = db.conn.execute(
            "SELECT COUNT(*), SUM(local_status = 'LOCAL_VERIFIED') FROM assets "
            "WHERE burst_uuid = ?",
            (burst,),
        ).fetchone()
        if (done or 0) < total:
            return f"burst with {total - (done or 0)} of {total} frames not archived"

    return None


def plan(
    config: Config,
    db: Database,
    selector: Selector,
    *,
    scan_id: int,
    provider: CloudProvider | None = None,
) -> tuple[list[Candidate], RemoveResult]:
    """Everything with positive evidence for every condition the policy needs."""
    policy = config.remove_from_iphone.policy
    if policy == RemovalPolicy.NEVER:
        raise RemoveError(
            "remove_from_iphone.policy is 'never', which is the default and means "
            "nothing is removed from the phone. Change it in the config to "
            "'cloud_verified' (or 'local_verified') if that is what you intend."
        )

    # The protections removal cannot waive, whatever the caller asked for:
    # suspected proxies and favourites, neither overridable. SAFETY sections 2
    # and 6.
    selector = selector.for_removal()
    where, params = selector.where()
    # --limit and --order applied in SQL, exactly as `cloud` and `sync` do. The
    # first cut of this hardcoded the ordering and dropped the limit entirely,
    # so `--limit 6831` planned the whole 24,481 and offered to delete 22.4 GB
    # when 15 GB had been asked for. cloud.py's own comment had already named
    # the hazard -- "a verb that quietly ignored it would be worse than one
    # that did not offer it" -- and this is the verb where it is worst, since
    # the flag is how a caller bounds how many photographs get deleted.
    limit = f" LIMIT {int(selector.limit)}" if selector.limit else ""
    rows = db.conn.execute(
        f"SELECT a.* FROM assets a WHERE {where} ORDER BY {selector.order_by()}{limit}",
        params,
    ).fetchall()

    result = RemoveResult(scan_id=scan_id)
    wanted: list[Candidate] = []

    for row in rows:
        asset = dict(row)
        result.examined += 1

        # 1. Present in the scan taken in this same invocation (SAFETY 7).
        if int(asset.get("last_seen_scan_id") or 0) != scan_id:
            result.block(asset, "not present in the scan taken just now")
            continue
        if not asset.get("present_on_phone"):
            result.block(asset, "the scan says it is no longer on the phone")
            continue
        if asset.get("removed_from_phone_at"):
            result.block(asset, "already recorded as removed")
            continue

        # 2. A local copy that exists and still hashes to what we recorded.
        if config.safety.require_local_verification:
            if asset.get("local_status") != "LOCAL_VERIFIED":
                result.block(asset, "no verified local copy")
                continue
            if not asset.get("local_path"):
                result.block(asset, "no local path recorded")
                continue
            local = Path(asset["local_path"])
            if not local.exists():
                result.block(asset, "the local copy is missing")
                continue
            recorded = asset.get("sha256")
            if not recorded:
                result.block(asset, "no local hash recorded")
                continue
            actual = _digest(local)
            if actual is None:
                result.block(asset, "the local copy could not be read")
                continue
            if actual != recorded:
                result.block(asset, "the local copy no longer matches its recorded hash")
                continue
        else:
            local = Path(asset.get("local_path") or "")

        # 3. Proxy suspicion. The selector already excludes these and cannot be
        #    told not to, so reaching here means something changed underneath.
        if config.safety.block_suspected_proxies and float(asset.get("proxy_suspicion") or 0) > 0:
            result.block(asset, "flagged as a suspected proxy")
            continue

        # 4. The whole asset group, not just the part that is easy to check.
        incomplete = _incomplete_group(asset, db)
        if incomplete:
            result.block(asset, incomplete)
            continue

        # 5. The cloud copy, when the policy asks for one.
        if policy == RemovalPolicy.CLOUD_VERIFIED:
            if asset.get("cloud_status") != "CLOUD_VERIFIED":
                result.block(asset, "not verified in the cloud")
                continue
            if not asset.get("cloud_path"):
                result.block(asset, "no cloud path recorded")
                continue

        if not asset.get("device_asset_id"):
            result.block(asset, "no device identifier, so it cannot be addressed")
            continue

        wanted.append(
            Candidate(
                asset_id=int(asset["id"]),
                local_identifier=str(asset["device_asset_id"]),
                filename=str(asset.get("filename") or ""),
                local_path=local,
                sha256=str(asset.get("sha256") or ""),
                size_bytes=int(asset.get("size_bytes") or 0),
                cloud_path=str(asset.get("cloud_path") or ""),
                evidence={
                    "scan_id": scan_id,
                    "local_status": asset.get("local_status"),
                    "local_hash_rechecked": bool(config.safety.require_local_verification),
                    "cloud_status": asset.get("cloud_status"),
                    "policy": str(policy),
                },
            )
        )

    if not wanted or policy != RemovalPolicy.CLOUD_VERIFIED:
        result.eligible = len(wanted)
        return wanted, result

    # 6. Ask the remote what it holds *now*. Same rule as release: the ledger
    #    records what was true when the upload ran, and this is about to delete
    #    the copy on the device.
    provider = provider or RcloneProvider(config.cloud.remote)
    provider.check()
    # One listing per destination in play, for the reason release gives: a
    # video checked against the photo archive is reported missing, and this is
    # the code path where "missing" decides whether a photograph is deleted.
    wanted_destinations = sorted({split_cloud_path(config, c.cloud_path)[0] for c in wanted})
    remote: dict[str, dict[str, str]] = {}
    for destination in wanted_destinations:
        remote[destination] = provider.hashes(destination)
        log.info("the remote reports %d file(s) at %s", len(remote[destination]), destination)

    eligible: list[Candidate] = []
    for candidate in wanted:
        destination, relative = split_cloud_path(config, candidate.cloud_path)
        digest = remote.get(destination, {}).get(relative)
        asset_row = {"id": candidate.asset_id, "filename": candidate.filename}
        if digest is None:
            result.block(asset_row, "not at the remote right now")
            continue
        if digest != candidate.sha256:
            result.block(asset_row, "the remote copy does not match the recorded hash")
            continue
        candidate.evidence["remote_hash_confirmed"] = True
        eligible.append(candidate)

    result.eligible = len(eligible)
    return eligible, result


def _record_request(
    db: Database, candidate: Candidate, policy: str, operation_id: int | None
) -> int:
    cursor = db.conn.execute(
        "INSERT INTO deletion_events (asset_id, device_id, operation_id, requested_at, "
        "status, policy, evidence, size_bytes) "
        "SELECT ?, a.device_id, ?, ?, 'REQUESTED', ?, ?, ? FROM assets a WHERE a.id = ?",
        (
            candidate.asset_id,
            operation_id,
            utcnow(),
            policy,
            json.dumps(candidate.evidence),
            candidate.size_bytes,
            candidate.asset_id,
        ),
    )
    return int(cursor.lastrowid or 0)


def run(
    config: Config,
    selector: Selector,
    *,
    provider: CloudProvider | None = None,
    db: Database | None = None,
    helper: Helper | None = None,
    scanner: Callable[[Config], Any] | None = None,
    prepared: tuple[list[Candidate], RemoveResult] | None = None,
    on_progress: Callable[[RemoveResult], None] | None = None,
) -> RemoveResult:
    """Delete from the device everything that proved it is safe to delete."""
    if not config.safety.require_final_scan:
        # The validator forbids setting this to false, so reaching here means
        # the config was bypassed rather than configured.
        raise RemoveError("safety.require_final_scan is false. Removal will not run.")

    policy = config.remove_from_iphone.policy
    if policy == RemovalPolicy.NEVER:
        raise RemoveError(
            "remove_from_iphone.policy is 'never'. Nothing will be removed from the phone."
        )

    helper = helper or Helper()
    helper.check()

    # SAFETY section 7: the plan is computed against a scan taken in this same
    # invocation. This is the expensive part and it is not optional.
    #
    # The scanner is injectable so the destructive tests can drive it, and that
    # is deliberately the *only* seam: there is no `scan_id` argument that
    # would let a caller skip scanning and hand in a number instead. A fresh
    # scan is the one thing this function will not be talked out of.
    # `prepared` carries a scan and plan already made in this same invocation,
    # which is what the CLI passes. Without it this scanned the whole 95,000
    # asset library twice per run -- once for the preview the user reads and
    # once here -- and the two plans could then disagree, which is how a
    # confirmation phrase bound to the first count met a second, different one.
    if prepared is None:
        log.info("scanning the library before planning any removal")
        scan_result = (scanner or run_scan)(config)

    owned = db is None
    db = db or Database(config.database.path).connect()
    if owned:
        db.migrate()
    journal = Journal(db)

    try:
        if prepared is None:
            eligible, result = plan(
                config, db, selector, scan_id=scan_result.scan_id, provider=provider
            )
        else:
            eligible, result = prepared
        if not eligible:
            return result

        with journal.operation(
            Op.REMOVE,
            command="remove-from-iphone",
            scan_id=result.scan_id,
            detail={
                "eligible": len(eligible),
                "bytes": sum(c.size_bytes for c in eligible),
                "policy": str(policy),
            },
        ) as operation:
            for start in range(0, len(eligible), BATCH_SIZE):
                batch = eligible[start : start + BATCH_SIZE]
                events = {
                    c.asset_id: _record_request(db, c, str(policy), getattr(operation, "id", None))
                    for c in batch
                }

                deleted: set[str] = set()
                missing: set[str] = set()
                failures: dict[str, str] = {}
                try:
                    for record in helper.delete([c.local_identifier for c in batch]):
                        event = record.get("event")
                        if event == "deleted":
                            deleted.add(str(record["localIdentifier"]))
                        elif event == "deleteSkipped":
                            missing.add(str(record["localIdentifier"]))
                        elif event == "deleteFailed":
                            failures[str(record["localIdentifier"])] = str(
                                record.get("error") or "unknown"
                            )
                except HelperError as exc:
                    # A non-zero exit means some asset survived. The events
                    # already collected are still true, so they are recorded;
                    # what did not come back stays on the phone and in the
                    # ledger as it was.
                    log.warning("the helper reported a problem: %s", exc)

                for candidate in batch:
                    identifier = candidate.local_identifier
                    event_id = events[candidate.asset_id]
                    if identifier in deleted:
                        db.conn.execute(
                            "UPDATE assets SET present_on_phone = 0, removed_from_phone_at = ?, "
                            "updated_at = ? WHERE id = ?",
                            (utcnow(), utcnow(), candidate.asset_id),
                        )
                        db.conn.execute(
                            "UPDATE deletion_events SET status = 'REMOVED', completed_at = ? "
                            "WHERE id = ?",
                            (utcnow(), event_id),
                        )
                        result.removed += 1
                        result.bytes_removed += candidate.size_bytes
                    elif identifier in missing:
                        db.conn.execute(
                            "UPDATE assets SET present_on_phone = 0, updated_at = ? WHERE id = ?",
                            (utcnow(), candidate.asset_id),
                        )
                        db.conn.execute(
                            "UPDATE deletion_events SET status = 'ALREADY_GONE', completed_at = ? "
                            "WHERE id = ?",
                            (utcnow(), event_id),
                        )
                        result.already_gone += 1
                    else:
                        error = failures.get(
                            identifier, "the helper did not confirm it was deleted"
                        )
                        db.conn.execute(
                            "UPDATE deletion_events SET status = 'FAILED', completed_at = ?, "
                            "error = ? WHERE id = ?",
                            (utcnow(), error, event_id),
                        )
                        result.failed += 1
                        result.failures.append({"file": candidate.filename, "error": error})

                if on_progress:
                    on_progress(result)

            operation.note(
                removed=result.removed,
                already_gone=result.already_gone,
                failed=result.failed,
                bytes=result.bytes_removed,
            )
    finally:
        if owned:
            db.close()

    return result
