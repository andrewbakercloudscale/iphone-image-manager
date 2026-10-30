"""Google Photos: an extra copy of what is already backed up, for looking at.

Google Drive is the backup. Google Photos re-encodes and strips what it is sent
and its API reports no hash, so a copy there can never be checked, and nothing
is ever released from the Mac or removed from the phone on the strength of one.
`release` and `remove` read nothing this module writes; `gphotos_status` says
only that rclone reported a file sent.

Since March 2025 Google lets an app list only what it uploaded itself. This can
therefore know about what Photo Sentinel sent and nothing else: the owner's own
Google Photos library is invisible to it, and nothing here can say whether a
photograph is already in Google Photos by some other route.

Only items Google Drive has verified are sent, so this is always a copy of a
safe backup. The bytes come from the Mac copy while it is there and still
matches its recorded SHA-256, and otherwise straight from Google Drive, which
is only read: `rclone copy` from it, never move, sync or delete.

Nothing is sent unless the owner asked: a click on Send, or a source they chose
to have follow its backups (`google_photos.sources`, run by cycle.sh).
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from .cloud import CloudError, RcloneProvider, split_cloud_path
from .config import Config
from .db.database import Database, utcnow
from .digests import file_digests
from .journal import Journal, Op
from .logs import get_logger
from .selector import Selector, channel_of
from .storage import is_google_photos, parse_remotes

log = get_logger("google_photos")

SENT = "SENT"
DEFAULT_REMOTE = "gphotos"
#: rclone's own name for the backend, the type `rclone config create` is given.
RCLONE_TYPE = "google photos"
#: Between the album name and what it holds: "Photo Sentinel - Camera".
SEPARATOR = " \u2013 "  # an en dash

#: The sources the app offers, as `--source` names them, with the titles it shows.
SOURCE_TITLES = {
    "camera": "Camera",
    "whatsapp": "WhatsApp",
    "screenshot": "Screenshots",
    "screenrecording": "Screen recordings",
    "unattributed": "No app recorded",
}

#: Counted, like `cloud`: three folders failing in a row is Google Photos being
#: unavailable, and a fourth attempt only spends quota.
CONSECUTIVE_FAILURE_LIMIT = 3


class GooglePhotosError(Exception):
    """Google Photos cannot be used as asked."""


def source_title(source: str) -> str:
    return SOURCE_TITLES.get(source, source)


def album_for(base: str, *, source: str | None = None, folder: str | None = None) -> str:
    """The album an item goes to: one per source, or one per Google Drive folder sent.

    A folder is named by its last two parts, "Photo Sentinel - Camera - 2019".
    rclone reads a slash in an album name as a folder, so none survives.
    """
    if folder:
        parts = list(PurePosixPath(folder.strip("/")).parts[-2:])
    elif source:
        parts = [source_title(source)]
    else:
        parts = []
    name = SEPARATOR.join([base.strip(), *parts])
    return " ".join(name.replace("/", "-").split())


@dataclass
class Item:
    """One verified backup, and the album it goes to."""

    asset_id: int
    cloud_path: str
    size_bytes: int
    sha256: str
    channel: str
    album: str
    local_path: Path | None = None

    @property
    def name(self) -> str:
        return PurePosixPath(self.cloud_path).name

    @property
    def on_mac(self) -> bool:
        return self.local_path is not None and self.local_path.is_file()


def plan(
    config: Config,
    db: Database,
    selector: Selector,
    *,
    folder: str | None = None,
    album: str | None = None,
    only_on_mac: bool = False,
    following: bool = False,
) -> list[Item]:
    """What a send would send: verified in Google Drive, not yet in Google Photos.

    `for_release`, because whether an item is still on the phone has no bearing
    on copying its backup. `folder` is a Google Drive path; everything under it
    goes, nested folders included. `following` keeps only the sources the owner
    chose to have follow their backups.
    """
    where, params = selector.for_release().where()
    clauses = [
        where,
        "a.cloud_status = 'CLOUD_VERIFIED'",
        "a.cloud_path IS NOT NULL",
        "a.gphotos_status IS NULL",
    ]
    if folder and folder.strip("/"):
        prefix = folder.strip("/") + "/"
        clauses.append("substr(a.cloud_path, 1, ?) = ?")
        params = [*params, len(prefix), prefix]
    if only_on_mac:
        clauses.append("a.local_status = 'LOCAL_VERIFIED' AND a.local_path IS NOT NULL")
    limit = f" LIMIT {int(selector.limit)}" if selector.limit else ""
    rows = db.conn.execute(
        f"SELECT a.* FROM assets a WHERE {' AND '.join(clauses)} "
        f"ORDER BY {selector.order_by()}{limit}",
        params,
    ).fetchall()

    wanted = set(config.google_photos.sources)
    base = config.google_photos.album
    items: list[Item] = []
    for row in rows:
        asset = dict(row)
        channel = channel_of(asset)
        if following and channel not in wanted:
            continue
        items.append(
            Item(
                asset_id=int(asset["id"]),
                cloud_path=str(asset["cloud_path"]),
                size_bytes=int(asset.get("size_bytes") or 0),
                sha256=str(asset.get("sha256") or ""),
                channel=channel,
                album=album
                or (album_for(base, folder=folder) if folder else album_for(base, source=channel)),
                local_path=Path(asset["local_path"]) if asset.get("local_path") else None,
            )
        )
    return items


def albums(items: list[Item]) -> list[dict[str, Any]]:
    """Each album a plan fills, with how much goes into it."""
    found: dict[str, dict[str, Any]] = {}
    for item in items:
        entry = found.setdefault(item.album, {"album": item.album, "count": 0, "bytes": 0})
        entry["count"] += 1
        entry["bytes"] += item.size_bytes
    return sorted(found.values(), key=lambda e: e["album"])


# -- sending -----------------------------------------------------------------


@dataclass
class Sent:
    """What one rclone run reported: the names it copied, and why it stopped if it did."""

    names: set[str] = field(default_factory=set)
    error: str | None = None
    #: Google refused on quota, in words for the owner. The run stops: retrying
    #: only spends what is left of the quota.
    quota: str | None = None


class Sender(Protocol):
    def check(self) -> None:
        """Raise GooglePhotosError if Google Photos cannot be used at all."""

    def send(
        self,
        source: str,
        names: list[str],
        album: str,
        on_copied: Callable[[str], None] | None = None,
    ) -> Sent:
        """Copy `names` from `source` (a folder on this Mac, or `remote:path` in
        Google Drive) into `album`. Reports only what rclone said it copied."""


_STORAGE_FULL = re.compile(
    r"storage (quota|is full|limit)|not enough storage|insufficient storage|storage.*exceeded",
    re.IGNORECASE,
)
_SLOW_DOWN = re.compile(
    r"quota|RESOURCE_EXHAUSTED|rateLimitExceeded|Too Many Requests|\b429\b", re.IGNORECASE
)


def quota_reason(text: str) -> str | None:
    if _STORAGE_FULL.search(text):
        return "Your Google storage is full. Google Photos uses the same storage as Google Drive."
    if _SLOW_DOWN.search(text):
        return "Google Photos asked to slow down. Try again later; what was sent is kept."
    return None


def parse_log(lines: list[str], error: str | None = None) -> Sent:
    """rclone's `--use-json-log` lines to what it copied.

    Exit codes are not enough here, in either direction: rclone exits 0 when a
    name in `--files-from-raw` is not found at all, and non-zero when one file
    of a hundred failed. So the evidence is per file, `Copied (new)` with its
    object name, and a file named in a later error line does not count.
    """
    copied: set[str] = set()
    failed: set[str] = set()
    problems: list[str] = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        message = str(entry.get("msg") or "")
        name = entry.get("object")
        level = entry.get("level")
        if level == "info" and message.startswith("Copied") and name:
            copied.add(str(name))
        elif level in ("error", "critical", "fatal"):
            problems.append(message.strip())
            if name:
                failed.add(str(name))
    text = " ".join([*problems, error or ""])
    return Sent(
        names=copied - failed,
        error=error or (problems[-1][:500] if problems else None),
        quota=quota_reason(text),
    )


class RcloneGooglePhotos(RcloneProvider):
    """rclone's Google Photos backend, writing into albums it creates.

    Built on `RcloneProvider` for its watchdogs: a transfer that goes silent or
    stops moving bytes is killed exactly as a Google Drive upload would be.
    """

    name = "google photos"

    #: Google Photos takes uploads in batches of up to 50; rclone's default
    #: batch is the transfer count. Not measured yet, so a modest figure.
    TRANSFERS = 8

    def __init__(
        self,
        remote: str,
        *,
        binary: str = "rclone",
        on_progress: Callable[[str], None] | None = None,
        batch_timeout: float = 7200,
        stall_timeout: float = 900,
        no_progress_timeout: float = 1800,
        tps_limit: float = 0.0,
    ) -> None:
        super().__init__(
            remote,
            binary=binary,
            transfers=self.TRANSFERS,
            batch_timeout=batch_timeout,
            stall_timeout=stall_timeout,
            no_progress_timeout=no_progress_timeout,
            tps_limit=tps_limit,
        )
        self._outer = on_progress
        self._lines: list[str] = []
        self._on_copied: Callable[[str], None] | None = None
        self.on_progress = self._hear

    def _hear(self, line: str) -> None:
        self._lines.append(line)
        if self._on_copied and '"Copied' in line:
            try:
                entry = json.loads(line)
            except ValueError:
                entry = None
            if isinstance(entry, dict) and entry.get("object"):
                self._on_copied(str(entry["object"]))
        if self._outer:
            self._outer(line)

    def check(self) -> None:
        try:
            result = subprocess.run(
                [self.binary, "listremotes", "--long"], capture_output=True, text=True, timeout=60
            )
        except FileNotFoundError as exc:
            raise GooglePhotosError(
                f"{self.binary} is not installed. Install it with: brew install rclone"
            ) from exc
        if result.returncode != 0:
            raise GooglePhotosError(f"rclone failed: {result.stderr.strip() or 'no diagnostic'}")
        kinds = {a.name: a.kind for a in parse_remotes(result.stdout)}
        if self.remote not in kinds:
            raise GooglePhotosError(
                f"rclone has no remote called {self.remote!r}. "
                f"Set it up with: iphone-image google-photos setup"
            )
        if not is_google_photos(kinds[self.remote]):
            raise GooglePhotosError(
                f"{self.remote!r} is a {kinds[self.remote]!r} remote, not Google Photos."
            )

    def send(
        self,
        source: str,
        names: list[str],
        album: str,
        on_copied: Callable[[str], None] | None = None,
    ) -> Sent:
        self._lines = []
        self._on_copied = on_copied
        if not names:
            return Sent()
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
            handle.write("\n".join(names))
            listing = handle.name
        error = None
        try:
            self._run(
                [
                    # copy, never move or sync: the source may be Google Drive,
                    # and this feature only ever reads it.
                    "copy",
                    source,
                    f"{self.remote}:album/{album}",
                    "--files-from-raw",
                    listing,
                    # Google Photos lists no hash and no size by default, so a
                    # destination check could only compare names, and two
                    # different IMG_0001.HEIC in one album would be one. Each
                    # file is sent; the ledger is what keeps it from going twice,
                    # and Google Photos keeps one item for identical bytes.
                    "--no-check-dest",
                    # One attempt: a retry of the whole copy would re-send what
                    # the first attempt already sent. rclone's low-level retries
                    # still cover a dropped request.
                    "--retries",
                    "1",
                    "--transfers",
                    str(self.transfers),
                    "--use-json-log",
                    "--log-level",
                    "INFO",
                    "--stats",
                    self.stats_interval,
                    "--stats-one-line",
                    "--stats-log-level",
                    "NOTICE",
                    *self._pacing(),
                ],
                timeout=self.batch_timeout,
                stall_timeout=self.stall_timeout,
                no_progress_timeout=self.no_progress_timeout,
            )
        except CloudError as exc:
            error = str(exc)
        finally:
            self._on_copied = None
            Path(listing).unlink(missing_ok=True)
        return parse_log(self._lines, error)


@dataclass
class SendResult:
    planned: int = 0
    planned_bytes: int = 0
    sent: int = 0
    bytes_sent: int = 0
    from_mac: int = 0
    from_drive: int = 0
    failed: int = 0
    stopped: str = ""
    failures: list[dict[str, Any]] = field(default_factory=list)


def _groups(
    config: Config, items: list[Item], result: SendResult
) -> dict[tuple[str, str], list[tuple[Item, str]]]:
    """One rclone run per album and source folder, each item with the name it is sent as.

    The Mac copy is used only if it still hashes to the ledger's SHA-256: a
    file that changed on disk is not the backup, and the backup is what goes.
    """
    groups: dict[tuple[str, str], list[tuple[Item, str]]] = {}
    drive = config.cloud.remote.rstrip(":")
    for item in items:
        if item.on_mac and item.local_path is not None:
            try:
                sha, _ = file_digests(item.local_path)
            except OSError:
                sha = ""
            if sha and sha == item.sha256:
                result.from_mac += 1
                key = (item.album, str(item.local_path.parent))
                groups.setdefault(key, []).append((item, item.local_path.name))
                continue
            log.info("%s no longer matches its hash; reading Google Drive", item.local_path)
        if not drive:
            result.failed += 1
            result.failures.append(
                {"file": item.cloud_path, "error": "no Mac copy and no Google Drive to read"}
            )
            continue
        result.from_drive += 1
        parent = str(PurePosixPath(item.cloud_path).parent)
        key = (item.album, f"{drive}:{parent}")
        groups.setdefault(key, []).append((item, item.name))
    return groups


def _counter(names: set[str], album: str, report: Callable[..., None]) -> Callable[[str], None]:
    """Reports each file as rclone says it is copied, so a long folder visibly moves."""
    seen = 0

    def copied(name: str) -> None:
        nonlocal seen
        if name in names:
            seen += 1
            report(album=album, inFlight=seen)

    return copied


def send(
    config: Config,
    db: Database,
    items: list[Item],
    *,
    sender: Sender,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> SendResult:
    """Send `items`, recording SENT only for what rclone reported copied."""
    sender.check()
    result = SendResult(planned=len(items), planned_bytes=sum(i.size_bytes for i in items))
    if not items:
        return result

    def report(**extra: Any) -> None:
        if on_progress:
            on_progress(
                {
                    "phase": "send",
                    "done": result.sent,
                    "total": result.planned,
                    "bytes": result.bytes_sent,
                    "totalBytes": result.planned_bytes,
                    **extra,
                }
            )

    report(album=items[0].album)
    groups = _groups(config, items, result)
    journal = Journal(db)
    consecutive = 0
    with journal.operation(
        Op.GPHOTOS_SEND,
        command="google-photos send",
        detail={"planned": result.planned, "bytes": result.planned_bytes, "folders": len(groups)},
    ) as operation:
        for (album, source), group in groups.items():
            names = {name: item for item, name in group}
            outcome = sender.send(
                source, list(names), album, on_copied=_counter(set(names), album, report)
            )
            stamp = utcnow()
            for name, item in names.items():
                if name in outcome.names:
                    db.conn.execute(
                        "UPDATE assets SET gphotos_status = ?, gphotos_album = ?, gphotos_at = ? "
                        "WHERE id = ? AND gphotos_status IS NULL",
                        (SENT, album, stamp, item.asset_id),
                    )
                    result.sent += 1
                    result.bytes_sent += item.size_bytes
                else:
                    result.failed += 1
                    result.failures.append(
                        {
                            "file": item.cloud_path,
                            "error": outcome.error or "rclone did not report it sent",
                        }
                    )
            operation.note(sent=result.sent, failed=result.failed, bytes=result.bytes_sent)
            report(album=album)
            if outcome.quota:
                result.stopped = outcome.quota
                break
            consecutive = consecutive + 1 if not outcome.names else 0
            if consecutive >= CONSECUTIVE_FAILURE_LIMIT:
                result.stopped = (
                    f"stopped after {consecutive} folders failed in a row. What was sent is "
                    f"recorded; sending again carries on from there."
                )
                break
    return result


# -- reading the ledger --------------------------------------------------------


def _verified_rows(db: Database) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in db.conn.execute(
            "SELECT id, source_bundle_id, filename, subtypes, size_bytes, cloud_path, "
            "gphotos_status FROM assets WHERE cloud_status = 'CLOUD_VERIFIED' "
            "AND cloud_path IS NOT NULL"
        )
    ]


def source_counts(config: Config, db: Database) -> list[dict[str, Any]]:
    """Per source: how many backups are in Google Photos, and how many are not yet."""
    shown = list(SOURCE_TITLES) + [
        s for s in config.google_photos.sources if s not in SOURCE_TITLES
    ]
    counts: dict[str, dict[str, Any]] = {
        s: {
            "source": s,
            "title": source_title(s),
            "following": s in config.google_photos.sources,
            "sent": 0,
            "sentBytes": 0,
            "notSent": 0,
            "notSentBytes": 0,
        }
        for s in shown
    }
    for row in _verified_rows(db):
        entry = counts.get(channel_of(row))
        if entry is None:
            continue
        size = int(row.get("size_bytes") or 0)
        if row.get("gphotos_status") == SENT:
            entry["sent"] += 1
            entry["sentBytes"] += size
        else:
            entry["notSent"] += 1
            entry["notSentBytes"] += size
    return list(counts.values())


def folders(config: Config, db: Database, depth: int = 2) -> list[dict[str, Any]]:
    """Google Drive backup folders, down to `depth` below each destination, with counts.

    Counted by prefix, as `send --folder` selects, so a folder's figures include
    everything under it -- a destination nested inside another included.
    """
    rows = _verified_rows(db)
    entries: dict[str, dict[str, Any]] = {}
    for row in rows:
        destination, relative = split_cloud_path(config, str(row["cloud_path"]))
        parts = PurePosixPath(relative).parts[:-1]
        for level in range(0, min(depth, len(parts)) + 1):
            path = "/".join([destination, *parts[:level]])
            if path not in entries:
                entries[path] = {
                    "path": path,
                    "destination": destination,
                    "relative": "/".join(parts[:level]),
                    "depth": level,
                    "count": 0,
                    "bytes": 0,
                    "sent": 0,
                    "notSent": 0,
                    "notSentBytes": 0,
                }
    for row in rows:
        size = int(row.get("size_bytes") or 0)
        sent = row.get("gphotos_status") == SENT
        parent = PurePosixPath(str(row["cloud_path"])).parent
        for ancestor in [parent, *parent.parents]:
            entry = entries.get(str(ancestor))
            if entry is None:
                continue
            entry["count"] += 1
            entry["bytes"] += size
            if sent:
                entry["sent"] += 1
            else:
                entry["notSent"] += 1
                entry["notSentBytes"] += size
    return sorted(entries.values(), key=lambda e: e["path"])
