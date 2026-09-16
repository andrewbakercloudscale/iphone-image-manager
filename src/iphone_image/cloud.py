"""Mirroring the archive to cloud storage.

The archive is a staging buffer, not a destination: once an asset is verified in
the cloud, the Mac copy can be released and the disk handed back to the next
chunk. That makes this the phase everything else waits on, and it makes
verification the part that matters. A copy nobody has checked is not a second
copy, and releasing the local file on the strength of one would be the single
worst bug this tool could have.

So verification is end to end on **the same hash the download was checked with**.
PhotoKit gives the original, sync hashes it to SHA256 on the way into the
archive, and Google Drive reports SHA256 for what it holds. Confirmed against
the real remote: the value Drive returns is byte-identical to the one in the
ledger. Nothing is marked verified on the strength of rclone exiting zero.

The tool never holds a credential. rclone owns the token, which is decision 14,
and the provider is a protocol so a fake can exercise every path in CI with no
network and no account.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any, Protocol

from .config import Config
from .db.database import Database, utcnow
from .journal import Journal, Op
from .logs import get_logger
from .selector import Selector, channel_of

log = get_logger("cloud")


class CloudError(Exception):
    """The mirror cannot safely proceed."""


@dataclass
class Upload:
    """One file, and where it belongs in the remote."""

    asset_id: int
    local_path: Path
    #: Relative to the channel root locally, and to the destination remotely.
    relative: str
    sha256: str
    size_bytes: int
    channel: str


@dataclass
class CloudResult:
    planned: int = 0
    uploaded: int = 0
    verified: int = 0
    failed: int = 0
    already_there: int = 0
    bytes_uploaded: int = 0
    seconds: float = 0.0
    failures: list[dict[str, Any]] = field(default_factory=list)

    @property
    def rate_mb_s(self) -> float:
        return (self.bytes_uploaded / self.seconds / 1_048_576) if self.seconds else 0.0


class CloudProvider(Protocol):
    """What the engine needs from any cloud backend."""

    name: str

    def check(self) -> None:
        """Raise CloudError if this provider cannot be used at all."""

    def upload(self, root: Path, relatives: list[str], destination: str) -> None:
        """Copy `relatives`, resolved under `root`, to `destination`."""

    def hashes(self, destination: str) -> dict[str, str]:
        """SHA256 per relative path already at `destination`."""


class RcloneProvider:
    """Google Drive and everything else rclone speaks.

    Exit codes decide, never the absence of output. rclone prints its errors on
    stderr and carries on to the next file, so a run that copied nothing can
    look like a quiet success if you only read stdout.
    """

    name = "rclone"

    #: Measured, not guessed. rclone's default of 4 transfers gave 0.33 MB/s
    #: against Drive -- about five seconds per file, which is per-file API
    #: overhead rather than bandwidth. The same files at 16 transfers with a
    #: 32M chunk gave 2.55 MB/s, turning 59.5 GB from 51 hours into under 7.
    DEFAULT_TRANSFERS = 16
    DEFAULT_CHUNK = "32M"

    #: How often rclone reports where it has got to. A 59.5 GB upload runs for
    #: hours; at 30s that is a few hundred lines, which is cheap against a log
    #: that otherwise says nothing at all until the run ends.
    DEFAULT_STATS_INTERVAL = "30s"

    #: Enough stderr kept for a diagnostic without holding hours of stats.
    STDERR_TAIL_LINES = 50

    #: How much of that reaches the error message.
    DIAGNOSTIC_CHARS = 500

    def __init__(
        self,
        remote: str,
        *,
        binary: str = "rclone",
        transfers: int = DEFAULT_TRANSFERS,
        chunk_size: str = DEFAULT_CHUNK,
        extra: Iterable[str] = (),
        on_progress: Callable[[str], None] | None = None,
        stats_interval: str = DEFAULT_STATS_INTERVAL,
    ) -> None:
        self.remote = remote.rstrip(":")
        self.binary = binary
        self.transfers = transfers
        self.chunk_size = chunk_size
        self.extra = list(extra)
        self.on_progress = on_progress
        self.stats_interval = stats_interval

    def check(self) -> None:
        try:
            result = subprocess.run(
                [self.binary, "listremotes"], capture_output=True, text=True, timeout=60
            )
        except FileNotFoundError as exc:
            raise CloudError(
                f"{self.binary} is not installed. Install it with: brew install rclone"
            ) from exc
        if result.returncode != 0:
            raise CloudError(f"rclone failed: {result.stderr.strip() or 'no diagnostic'}")
        remotes = {line.strip().rstrip(":") for line in result.stdout.splitlines()}
        if self.remote not in remotes:
            raise CloudError(
                f"rclone has no remote called {self.remote!r}. "
                f"Configured: {', '.join(sorted(remotes)) or 'none'}. "
                f"Create one with: rclone config"
            )

    def _run(self, args: list[str], *, timeout: float) -> str:
        """Run rclone and return its stdout, reporting stderr as it arrives.

        `capture_output` hands back stderr only once the process has exited,
        which for a multi-hour upload means the log is empty for the whole run
        and reads exactly the same whether the transfer is moving or died an
        hour ago. That is the shape of failure this project keeps meeting:
        absence of output taken for absence of trouble. So stderr is drained
        line by line while rclone works, and the tail of it is still kept for
        the diagnostic on a non-zero exit.

        stdout goes to a file rather than a pipe. `lsjson` over 20,000 files is
        megabytes, and a pipe nobody is reading fills and deadlocks the child.
        """
        command = [self.binary, *args, *self.extra]
        log.debug("running %s", " ".join(command[:6]))
        tail: deque[str] = deque(maxlen=self.STDERR_TAIL_LINES)

        def diagnostic() -> str:
            """The end of what rclone said, not the beginning.

            Now that progress is streamed, most of stderr is stats, and the
            reason a run failed is the last thing on it. Truncating from the
            front produced 'NOTICE: stats 351 ... stats 375' and dropped the
            'ERROR: giving up' that followed -- a diagnostic made entirely of
            the noise, with the signal cut off the end.
            """
            joined = " | ".join(tail)
            if len(joined) <= self.DIAGNOSTIC_CHARS:
                return joined or "no diagnostic on stderr"
            return "..." + joined[-self.DIAGNOSTIC_CHARS :]

        def drain(stream: IO[str]) -> None:
            for raw in stream:
                line = raw.rstrip("\n")
                if not line:
                    continue
                tail.append(line)
                if self.on_progress:
                    self.on_progress(line)

        with tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as sink:
            process = subprocess.Popen(
                command,
                stdout=sink,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
                bufsize=1,
            )
            assert process.stderr is not None
            pump = threading.Thread(target=drain, args=(process.stderr,), daemon=True)
            pump.start()
            try:
                code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                pump.join(timeout=5)
                raise CloudError(
                    f"rclone {args[0]} did not finish within {timeout:.0f}s and was killed. "
                    f"Last output: {diagnostic()}"
                ) from None
            finally:
                pump.join(timeout=5)
                process.stderr.close()
            sink.seek(0)
            stdout = sink.read()

        if code != 0:
            raise CloudError(f"rclone {args[0]} exited {code}: {diagnostic()}")
        return stdout

    def upload(self, root: Path, relatives: list[str], destination: str) -> None:
        if not relatives:
            return
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
            handle.write("\n".join(relatives))
            listing = handle.name
        try:
            self._run(
                [
                    "copy",
                    str(root),
                    f"{self.remote}:{destination}",
                    "--files-from-raw",
                    listing,
                    # Compare by checksum rather than modification time: a
                    # resumed run must not re-send a file it already sent, and
                    # must not skip one whose timestamp merely happens to match.
                    "--checksum",
                    "--transfers",
                    str(self.transfers),
                    "--drive-chunk-size",
                    self.chunk_size,
                    "--retries",
                    "3",
                    # Without these rclone copies 59 GB in total silence. All
                    # three are needed and the last is the one easily missed:
                    # measured against rclone 1.74.1, --stats with
                    # --stats-one-line still prints nothing, because stats are
                    # logged at INFO and the default log level is NOTICE.
                    # --progress is the wrong tool here: it redraws the
                    # terminal with control codes, which a log file cannot use.
                    "--stats",
                    self.stats_interval,
                    "--stats-one-line",
                    "--stats-log-level",
                    "NOTICE",
                ],
                timeout=6 * 3600,
            )
        finally:
            Path(listing).unlink(missing_ok=True)

    def hashes(self, destination: str) -> dict[str, str]:
        out = self._run(
            ["lsjson", f"{self.remote}:{destination}", "--recursive", "--files-only", "--hash"],
            timeout=3600,
        )
        found: dict[str, str] = {}
        for row in json.loads(out or "[]"):
            digest = (row.get("Hashes") or {}).get("sha256")
            if digest:
                found[row["Path"]] = digest
        return found


def plan(config: Config, selector: Selector, db: Database) -> list[Upload]:
    """Every archived file the selector matches that is not yet verified above."""
    where, params = selector.where()
    # --limit is applied in SQL, not by truncating afterwards, so "the first 20"
    # means the first 20 the selector would have acted on. The CLI offers the
    # flag for every verb; a verb that quietly ignored it would be worse than
    # one that did not offer it.
    limit = f" LIMIT {int(selector.limit)}" if selector.limit else ""
    rows = db.conn.execute(
        f"SELECT a.* FROM assets a WHERE {where} "
        f"AND a.local_status = 'LOCAL_VERIFIED' AND a.local_path IS NOT NULL "
        f"AND (a.cloud_status IS NULL OR a.cloud_status != 'CLOUD_VERIFIED') "
        f"ORDER BY {selector.order_by()}{limit}",
        params,
    ).fetchall()

    archive = config.archive.local_path
    uploads: list[Upload] = []
    for row in rows:
        asset = dict(row)
        local = Path(asset["local_path"])
        channel = channel_of(asset)
        try:
            # The channel directory is the local root, so the remote layout is
            # the archive layout with that level removed: camera/2019/x -> 2019/x.
            relative = local.relative_to(archive / channel)
        except ValueError:
            log.warning("%s is not under %s, skipping", local, archive / channel)
            continue
        if not asset.get("sha256"):
            log.warning("%s has no recorded hash, skipping", local)
            continue
        uploads.append(
            Upload(
                asset_id=int(asset["id"]),
                local_path=local,
                relative=str(relative),
                sha256=str(asset["sha256"]),
                size_bytes=int(asset.get("size_bytes") or 0),
                channel=channel,
            )
        )
    return uploads


def run(
    config: Config,
    selector: Selector,
    *,
    provider: CloudProvider | None = None,
    db: Database | None = None,
    on_channel: Callable[[str, int], None] | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> CloudResult:
    """Upload, then prove what arrived before recording anything as verified."""
    if not config.cloud.enabled:
        raise CloudError("cloud.enabled is false. Nothing will be uploaded.")
    if not config.cloud.remote:
        raise CloudError("cloud.remote is empty. Name an rclone remote in the config.")

    def note(line: str) -> None:
        """Progress goes to the logfile as well as the screen.

        `--json` silences the screen, and the run that most needs watching is
        the detached one nobody is looking at.
        """
        log.info("%s", line)
        if on_progress:
            on_progress(line)

    provider = provider or RcloneProvider(
        config.cloud.remote,
        transfers=config.performance.cloud_upload_workers,
        on_progress=note,
    )
    provider.check()

    owned = db is None
    db = db or Database(config.database.path).connect()
    if owned:
        db.migrate()
    journal = Journal(db)
    result = CloudResult()

    try:
        uploads = plan(config, selector, db)
        result.planned = len(uploads)
        if not uploads:
            return result

        by_channel: dict[str, list[Upload]] = {}
        for upload in uploads:
            by_channel.setdefault(upload.channel, []).append(upload)

        with journal.operation(
            Op.CLOUD_UPLOAD,
            command="cloud",
            detail={
                "planned": len(uploads),
                "bytes": sum(u.size_bytes for u in uploads),
                "destination": config.cloud.destination,
            },
        ) as operation:
            for channel, group in by_channel.items():
                if on_channel:
                    on_channel(channel, len(group))
                began = time.monotonic()
                provider.upload(
                    config.archive.local_path / channel,
                    [u.relative for u in group],
                    config.cloud.destination,
                )
                result.seconds += time.monotonic() - began
                result.uploaded += len(group)
                result.bytes_uploaded += sum(u.size_bytes for u in group)

            _verify(config, db, provider, uploads, result, note)
            operation.note(
                uploaded=result.uploaded,
                verified=result.verified,
                failed=result.failed,
                bytes=result.bytes_uploaded,
            )
    finally:
        if owned:
            db.close()

    return result


def _verify(
    config: Config,
    db: Database,
    provider: CloudProvider,
    uploads: list[Upload],
    result: CloudResult,
    note: Callable[[str], None] = lambda _line: None,
) -> None:
    """Compare what the remote says it holds against what we recorded.

    rclone exiting zero is not evidence. The remote is asked what it has, and
    only a matching SHA256 marks an asset verified, because the whole point of
    the cloud copy is that the local one can then be released.
    """
    # Listing 20,000 remote files takes minutes and rclone prints nothing
    # while it does, so say what is happening before going quiet.
    note(f"asking the remote what it holds at {config.cloud.destination} ...")
    remote = provider.hashes(config.cloud.destination)
    note(f"remote reports {len(remote):,} file(s); comparing {len(uploads):,} hash(es)")
    verified_at = utcnow()

    for upload in uploads:
        digest = remote.get(upload.relative)
        if digest is None:
            result.failed += 1
            result.failures.append(
                {"file": upload.relative, "error": "not found at the destination after upload"}
            )
            continue
        if digest != upload.sha256:
            result.failed += 1
            result.failures.append(
                {
                    "file": upload.relative,
                    "error": f"hash mismatch: remote {digest[:12]}, local {upload.sha256[:12]}",
                }
            )
            continue
        db.conn.execute(
            "UPDATE assets SET cloud_status = 'CLOUD_VERIFIED', cloud_verified_at = ?, "
            "cloud_path = ?, updated_at = ? WHERE id = ?",
            (
                verified_at,
                f"{config.cloud.destination}/{upload.relative}",
                verified_at,
                upload.asset_id,
            ),
        )
        result.verified += 1
