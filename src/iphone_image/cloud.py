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
import re
import subprocess
import tempfile
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import IO, Any, Protocol

from .config import Config
from .db.database import Database, utcnow
from .journal import Journal, Op
from .logs import get_logger
from .selector import Selector, channel_of

log = get_logger("cloud")


class CloudError(Exception):
    """The mirror cannot safely proceed."""


class _Expired(Exception):
    """rclone ran out of time or went quiet. Internal to `_run`."""

    def __init__(self, why: str) -> None:
        super().__init__(why)
        self.why = why


#: How often the waiter looks up from `process.wait` to check for silence.
_WAIT_TICK = 5.0

#: rclone's one-line stats: "  6.027 MiB / 60 MiB, 10%, 3.027 MiB/s, ETA 17s".
#: Only the first figure is wanted -- what has actually been sent.
_TRANSFERRED = re.compile(r"([\d.]+)\s*([KMGTP]?i?B)\s*/\s*[\d.]+\s*[KMGTP]?i?B")
_UNITS = {
    "B": 1,
    "KiB": 1024,
    "MiB": 1024**2,
    "GiB": 1024**3,
    "TiB": 1024**4,
    "PiB": 1024**5,
    "kB": 1000,
    "MB": 1000**2,
    "GB": 1000**3,
    "TB": 1000**4,
    "PB": 1000**5,
}


def _transferred_bytes(line: str) -> int | None:
    """Bytes sent, from a stats line, or None if it does not say.

    None is not zero. A stats format we cannot read must report itself as
    unreadable, because silently calling it zero would produce a confident
    rate of 0 MB/s for a transfer that was working perfectly well.
    """
    match = _TRANSFERRED.search(line)
    if not match:
        return None
    unit = _UNITS.get(match.group(2))
    if unit is None:
        return None
    try:
        return int(float(match.group(1)) * unit)
    except ValueError:
        return None


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
    #: Bytes rclone actually put on the wire, which on a resumed run is far
    #: less than `bytes_uploaded`: most of a re-run is skipped by checksum.
    bytes_transferred: int = 0
    seconds: float = 0.0
    #: Only the time spent in batches that sent something. A batch that sent
    #: nothing took real seconds and contributes no bandwidth evidence, so
    #: averaging it in would understate the rate exactly as including it in
    #: the numerator overstates it.
    seconds_transferring: float = 0.0
    batches: int = 0
    batches_done: int = 0
    stopped_early: str = ""
    failures: list[dict[str, Any]] = field(default_factory=list)

    @property
    def rate_mb_s(self) -> float:
        """Transfer rate over the batches that actually transferred.

        The first resumed run reported `76.99 MB/s` for a folder where rclone
        sent nothing at all: the bytes were already on the remote and the
        figure was planned-size over elapsed-time. A rate whose numerator
        never crossed the network is the same error as averaging local disk
        reads into a download rate, which this project has now made twice.
        """
        if not self.seconds_transferring:
            return 0.0
        return self.bytes_transferred / self.seconds_transferring / 1_048_576


class CloudProvider(Protocol):
    """What the engine needs from any cloud backend."""

    name: str

    #: Bytes the last `upload` actually sent, or None if it cannot say. None
    #: means "unknown" and is reported as unknown; it is never treated as 0.
    last_transfer_bytes: int | None

    def check(self) -> None:
        """Raise CloudError if this provider cannot be used at all."""

    def upload(self, root: Path, relatives: list[str], destination: str) -> None:
        """Copy `relatives`, resolved under `root`, to `destination`."""

    def hashes(self, destination: str, subdir: str | None = None) -> dict[str, str]:
        """SHA256 per path already at `destination`, keyed relative to it.

        `subdir` narrows the listing to one folder. Keys stay relative to
        `destination` either way, so a caller never has to know which was
        used -- one key space, whatever the question was.
        """


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
        batch_timeout: float = 7200,
        stall_timeout: float = 900,
        tps_limit: float = 0.0,
    ) -> None:
        self.remote = remote.rstrip(":")
        self.binary = binary
        self.transfers = transfers
        self.chunk_size = chunk_size
        self.extra = list(extra)
        self.on_progress = on_progress
        self.stats_interval = stats_interval
        self.batch_timeout = batch_timeout
        self.stall_timeout = stall_timeout
        self.tps_limit = tps_limit
        self.last_transfer_bytes: int | None = None

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

    def _run(self, args: list[str], *, timeout: float, stall_timeout: float | None = None) -> str:
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

        `stall_timeout` kills a process that has produced no output at all for
        that long. It is a better question than "has this run too long?",
        which cannot tell a slow transfer from a dead one and killed a real
        upload at 62% for the crime of being throttled. Silence is the signal:
        rclone is asked for stats every 30s and prints them even at 0 B/s.
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

        last_output = time.monotonic()

        def drain(stream: IO[str]) -> None:
            nonlocal last_output
            for raw in stream:
                line = raw.rstrip("\n")
                if not line:
                    continue
                last_output = time.monotonic()
                sent = _transferred_bytes(line)
                if sent is not None:
                    self.last_transfer_bytes = sent
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
                code = self._wait(process, timeout, stall_timeout, lambda: last_output)
            except _Expired as expiry:
                process.kill()
                process.wait()
                pump.join(timeout=5)
                raise CloudError(
                    f"rclone {args[0]} {expiry.why} and was killed. Last output: {diagnostic()}"
                ) from None
            finally:
                pump.join(timeout=5)
                process.stderr.close()
            sink.seek(0)
            stdout = sink.read()

        if code != 0:
            raise CloudError(f"rclone {args[0]} exited {code}: {diagnostic()}")
        return stdout

    @staticmethod
    def _wait(
        process: subprocess.Popen[str],
        timeout: float,
        stall_timeout: float | None,
        last_output: Callable[[], float],
    ) -> int:
        """Wait for rclone, watching both the clock and its silence."""
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise _Expired(f"did not finish within {timeout:.0f}s")
            try:
                return process.wait(timeout=min(remaining, _WAIT_TICK))
            except subprocess.TimeoutExpired:
                pass
            if stall_timeout is not None:
                silent = time.monotonic() - last_output()
                if silent >= stall_timeout:
                    raise _Expired(f"said nothing for {silent:.0f}s")

    def upload(self, root: Path, relatives: list[str], destination: str) -> None:
        # Cleared per call: a stale figure from the previous folder would be
        # reported as this one's, which is worse than reporting nothing.
        self.last_transfer_bytes = None
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
                    *self._pacing(),
                ],
                timeout=self.batch_timeout,
                stall_timeout=self.stall_timeout,
            )
        finally:
            Path(listing).unlink(missing_ok=True)

    def _pacing(self) -> list[str]:
        return ["--tpslimit", str(self.tps_limit)] if self.tps_limit else []

    def hashes(self, destination: str, subdir: str | None = None) -> dict[str, str]:
        where = f"{destination}/{subdir}" if subdir else destination
        try:
            out = self._run(
                [
                    "lsjson",
                    f"{self.remote}:{where}",
                    "--recursive",
                    "--files-only",
                    "--hash",
                    *self._pacing(),
                ],
                timeout=self.batch_timeout,
                stall_timeout=None,  # lsjson is silent by design; only the clock applies
            )
        except CloudError as exc:
            # A folder that is not there yet is an empty folder, not an error:
            # the first upload of a batch creates it, and a verification that
            # blew up here would fail the batch it was meant to be checking.
            if "directory not found" in str(exc).lower():
                return {}
            raise
        found: dict[str, str] = {}
        for row in json.loads(out or "[]"):
            digest = (row.get("Hashes") or {}).get("sha256")
            if not digest:
                continue
            path = f"{subdir}/{row['Path']}" if subdir else row["Path"]
            found[path] = digest
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
        batch_timeout=config.cloud.batch_timeout_seconds,
        stall_timeout=config.cloud.stall_timeout_seconds,
        tps_limit=config.cloud.tps_limit,
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

        batches = _batches(uploads)
        result.batches = len(batches)
        announced: set[str] = set()
        consecutive_failures = 0

        with journal.operation(
            Op.CLOUD_UPLOAD,
            command="cloud",
            detail={
                "planned": len(uploads),
                "bytes": sum(u.size_bytes for u in uploads),
                "batches": len(batches),
                "destination": config.cloud.destination,
            },
        ) as operation:
            for index, ((channel, folder), group) in enumerate(batches.items(), start=1):
                if on_channel and channel not in announced:
                    announced.add(channel)
                    on_channel(
                        channel,
                        sum(len(g) for (c, _f), g in batches.items() if c == channel),
                    )

                size = sum(u.size_bytes for u in group)
                note(
                    f"[batch {index}/{len(batches)}] {channel}/{folder or '.'}: "
                    f"{len(group):,} file(s), {size / 1e9:.2f} GB"
                )
                began = time.monotonic()
                try:
                    provider.upload(
                        config.archive.local_path / channel,
                        [u.relative for u in group],
                        config.cloud.destination,
                    )
                    result.seconds += time.monotonic() - began
                    result.uploaded += len(group)
                    result.bytes_uploaded += size
                    sent = getattr(provider, "last_transfer_bytes", None)
                    _verify_batch(config, db, provider, group, folder, result, note)
                except CloudError as exc:
                    # One folder failing is not the run failing. Whatever was
                    # banked before this stays banked, and a re-run replans
                    # from the ledger and skips it.
                    result.seconds += time.monotonic() - began
                    consecutive_failures += 1
                    result.failed += len(group)
                    result.failures.append({"file": f"{channel}/{folder}", "error": str(exc)})
                    note(f"  batch failed: {exc}")
                else:
                    consecutive_failures = 0
                    result.batches_done += 1
                    took = max(time.monotonic() - began, 1e-9)
                    if sent is None:
                        how = "rate not reported"
                    elif sent == 0:
                        how = "nothing sent, already on the remote"
                    else:
                        result.bytes_transferred += sent
                        result.seconds_transferring += took
                        how = f"{sent / 1e9:.2f} GB sent at {sent / took / 1e6:.2f} MB/s"
                    note(f"  banked {result.verified:,}/{len(uploads):,} verified, {how}")

                operation.note(
                    uploaded=result.uploaded,
                    verified=result.verified,
                    failed=result.failed,
                    bytes=result.bytes_uploaded,
                    bytes_transferred=result.bytes_transferred,
                    batches_done=result.batches_done,
                )

                if consecutive_failures >= CONSECUTIVE_BATCH_FAILURE_LIMIT:
                    result.stopped_early = (
                        f"stopped after {consecutive_failures} batches failed in a row, which "
                        f"is an outage rather than {consecutive_failures} bad folders. "
                        f"{result.verified:,} asset(s) are verified and recorded; the next "
                        f"run replans from the ledger and resumes where this one stopped."
                    )
                    log.warning("%s", result.stopped_early)
                    note(result.stopped_early)
                    break
    finally:
        if owned:
            db.close()

    return result


#: Counted rather than diagnosed, the same rule `sync` uses for an outage.
#: Three folders failing in a row is the remote being unavailable, not three
#: unlucky folders, and the next attempt is pointless.
CONSECUTIVE_BATCH_FAILURE_LIMIT = 3


def _batches(uploads: list[Upload]) -> dict[tuple[str, str], list[Upload]]:
    """One batch per remote folder, in plan order.

    The unit of work is a folder because it is also the unit of *checking*:
    verifying a batch lists that one folder instead of re-listing the whole
    archive, which is what made end-of-run verification too expensive to do
    more than once. Measured against the real archive: 236 folders, median 25
    files, the largest 965 files and 2.70 GB -- an hour's work even at the
    1.12 MB/s Drive throttled us to, so nothing banked is ever far behind
    what has been sent.
    """
    batches: dict[tuple[str, str], list[Upload]] = {}
    for upload in uploads:
        folder = str(PurePosixPath(upload.relative).parent)
        batches.setdefault((upload.channel, "" if folder == "." else folder), []).append(upload)
    return batches


def _verify_batch(
    config: Config,
    db: Database,
    provider: CloudProvider,
    uploads: list[Upload],
    subdir: str,
    result: CloudResult,
    note: Callable[[str], None] = lambda _line: None,
) -> None:
    """Compare what the remote says it holds against what we recorded.

    rclone exiting zero is not evidence. The remote is asked what it has, and
    only a matching SHA256 marks an asset verified, because the whole point of
    the cloud copy is that the local one can then be released.

    This runs per folder rather than once at the end. The first real upload
    ran for six hours, put 11,748 files on Drive, was killed by a wall-clock
    timeout before it reached verification, and recorded **nothing** -- the
    connection is in autocommit, so every batch that gets here is durable the
    moment it is written, and an interruption costs at most the batch in
    flight.
    """
    remote = provider.hashes(config.cloud.destination, subdir or None)
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
