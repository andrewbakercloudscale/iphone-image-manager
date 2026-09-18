"""Preflight checks over everything outside this tool's control.

`config validate` checks our own configuration. This checks the machine: the
Photos library, the iCloud settings the whole design depends on, disk headroom
and the external tools.

Every check states what it found and, when it fails, exactly what to change. A
check that cannot run reports FAIL with the reason. None of them pass by default.
"""

from __future__ import annotations

import json
import plistlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .config import Config

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"

#: Below this, a chunked fetch has nowhere to work.
MIN_FREE_BYTES = 20 * 1024**3


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""
    remedy: str = ""
    data: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status != FAIL


def _copy_photos_db(library: Path) -> Path | None:
    """Copy the Photos database and its WAL so counts are current.

    Never opens the live file: Photos writes to it constantly and a reader that
    holds a lock on someone's photo library is a bad neighbour.
    """
    source = library / "database" / "Photos.sqlite"
    if not source.is_file():
        return None
    target_dir = Path(tempfile.mkdtemp(prefix="iim-doctor-"))
    target = target_dir / "Photos.sqlite"
    try:
        shutil.copy2(source, target)
        for suffix in ("-wal", "-shm"):
            extra = source.with_name(source.name + suffix)
            if extra.exists():
                shutil.copy2(extra, target.with_name(target.name + suffix))
    except OSError:
        return None
    return target


def _count(db: Path, sql: str) -> int | None:
    """A scalar that must be a number.

    Anything else means the Photos schema moved under us, and None makes the
    caller say so rather than doing arithmetic on a surprise.
    """
    value = _scalar(db, sql)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _scalar(db: Path, sql: str) -> int | str | None:
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            row = conn.execute(sql).fetchone()
            return row[0] if row else None
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def _rows(db: Path, sql: str) -> list[sqlite3.Row] | None:
    """Several rows, or None when the schema does not support the question.

    None and an empty list mean different things and callers must not confuse
    them: empty is "asked and answered, nothing matched", None is "could not
    ask". An undocumented schema changes between macOS releases, so a query
    that no longer parses has to degrade rather than crash or, worse, read as
    a clean result.
    """
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(sql).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024.0:
            return f"{n:,.1f} {unit}"
        n /= 1024.0
    return f"{n:,.1f} PB"


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def check_platform(config: Config) -> Check:
    if sys.platform != "darwin":
        return Check(
            "platform",
            FAIL,
            f"running on {sys.platform}",
            "This tool talks to the macOS Photos library. It only runs on macOS.",
        )
    return Check("platform", PASS, f"macOS, Python {sys.version.split()[0]}")


def check_tools(config: Config) -> Check:
    required = {"exiftool": "brew install exiftool", "ffprobe": "brew install ffmpeg"}
    if config.cloud.enabled:
        required["rclone"] = "brew install rclone"

    missing = {name: how for name, how in required.items() if not shutil.which(name)}
    if missing:
        return Check(
            "external tools",
            FAIL,
            "missing: " + ", ".join(sorted(missing)),
            "; ".join(sorted(missing.values())),
            {"missing": sorted(missing)},
        )
    return Check("external tools", PASS, "all present: " + ", ".join(sorted(required)))


def check_apple_account(config: Config) -> Check:
    path = Path("~/Library/Preferences/MobileMeAccounts.plist").expanduser()
    if not path.is_file():
        return Check(
            "Apple Account", FAIL, "not signed in", "Sign in: System Settings > Apple Account."
        )
    try:
        accounts = plistlib.loads(path.read_bytes()).get("Accounts", [])
    except Exception as exc:
        return Check(
            "Apple Account",
            WARN,
            f"could not read the account list: {exc}",
            "Harmless on its own, but verify in System Settings > Apple Account.",
        )
    if not accounts:
        return Check(
            "Apple Account",
            FAIL,
            "no account signed in",
            "Sign in: System Settings > Apple Account.",
        )
    account = accounts[0].get("AccountID", "(unknown)")
    return Check("Apple Account", PASS, str(account), "", {"accountID": str(account)})


def check_library(config: Config) -> Check:
    library = config.photos.library_path
    if not library.exists():
        return Check(
            "Photos library",
            FAIL,
            f"not found at {library}",
            "Set photos.library_path in your config, or open Photos once to create one.",
        )
    db = library / "database" / "Photos.sqlite"
    if not db.exists():
        return Check(
            "Photos library",
            FAIL,
            f"no database inside {library}",
            "The library looks damaged. Open it in Photos.",
        )
    try:
        db.open("rb").close()
    except PermissionError:
        return Check(
            "Photos library",
            FAIL,
            "found, but the database cannot be read",
            "Grant Full Disk Access to your terminal: System Settings > Privacy & "
            "Security > Full Disk Access. Then restart the terminal.",
        )
    return Check("Photos library", PASS, str(library))


def check_icloud_mode(config: Config) -> Check:
    """Optimise Mac Storage versus Download Originals.

    The single most expensive setting to get wrong, and the Photos UI gives no
    warning either way. `ZLOCALAVAILABILITYTARGET` records what Photos is trying
    to achieve: 0 means it is content to keep originals in iCloud, 1 means it
    wants every original resident on this Mac.
    """
    db = _copy_photos_db(config.photos.library_path)
    if db is None:
        return Check(
            "iCloud storage mode",
            FAIL,
            "could not read the Photos database",
            "Grant Full Disk Access to your terminal, then restart it.",
        )

    wants_local = _count(
        db, "SELECT COUNT(*) FROM ZINTERNALRESOURCE WHERE ZLOCALAVAILABILITYTARGET = 1"
    )
    total = _count(db, "SELECT COUNT(*) FROM ZINTERNALRESOURCE")
    if total is None:
        return Check(
            "iCloud storage mode",
            FAIL,
            "the database has an unexpected schema",
            "This macOS version may have changed the Photos schema. Report it.",
        )
    if not total:
        return Check(
            "iCloud storage mode",
            WARN,
            "the library has no resources yet",
            "Nothing has synced. Check again once Photos has started.",
        )

    local_count = wants_local or 0
    ratio = local_count / total
    data = {"resources": total, "targetingLocal": local_count, "ratio": round(ratio, 4)}

    if ratio > 0.5:
        return Check(
            "iCloud storage mode",
            FAIL,
            f"Download Originals: {local_count:,} of {total:,} resources are targeted local",
            "Switch to Optimise Mac Storage: Photos > Settings > iCloud. "
            "Download Originals will pull your entire library onto this Mac.",
            data,
        )
    return Check(
        "iCloud storage mode",
        PASS,
        f"Optimise Mac Storage ({local_count:,} of {total:,} targeted local)",
        "",
        data,
    )


#: A year holding this many assets is a year the phone was in real use.
_GAP_MIN_ASSETS = 500
#: Below this share of screenshots, a year looks like it never received them.
_GAP_LOW_SHARE = 0.01
#: ...but only claim that when another year proves the library does hold them.
_GAP_HEALTHY_SHARE = 0.05


def _screenshot_gap(db: Path) -> list[str] | None:
    """Years holding plenty of assets but almost no screenshots.

    This is self-evidence: it needs nothing the user has to type, which is the
    point. A phone in daily use produces screenshots every year, so a year with
    8,411 assets and none at all is not a change of habit, it is a year the
    library never received.

    Written because the Mac held 7,684 of the phone's 14,605 screenshots, every
    missing one predated 2024, and `doctor` called the sync `[ok]` throughout.
    The total alone could not show it -- the library held 84% of the assets and
    looked plausible -- but the composition could, and did.

    Returns None when the subtype column is absent. An unreadable schema is not
    evidence of a complete library, and must not read as one.
    """
    rows = _rows(
        db,
        "SELECT strftime('%Y', ZDATECREATED + 978307200, 'unixepoch') AS year, "
        "COUNT(*) AS assets, "
        "SUM(CASE WHEN ZKINDSUBTYPE = 10 THEN 1 ELSE 0 END) AS shots "
        "FROM ZASSET WHERE ZTRASHEDSTATE = 0 AND ZDATECREATED IS NOT NULL "
        "GROUP BY year ORDER BY year",
    )
    if rows is None:
        return None

    years = [(r["year"], int(r["assets"] or 0), int(r["shots"] or 0)) for r in rows if r["year"]]
    substantial = [(y, n, s) for y, n, s in years if n >= _GAP_MIN_ASSETS]
    if not any(s / n >= _GAP_HEALTHY_SHARE for _, n, s in substantial):
        # No year is screenshot-rich, so there is no baseline to judge against.
        # Someone who never screenshots anything is not a broken sync.
        return []
    return [y for y, n, s in substantial if s / n < _GAP_LOW_SHARE]


def check_sync(config: Config) -> Check:
    db = _copy_photos_db(config.photos.library_path)
    if db is None:
        return Check(
            "iCloud sync",
            FAIL,
            "could not read the Photos database",
            "Grant Full Disk Access to your terminal, then restart it.",
        )

    assets = _count(db, "SELECT COUNT(*) FROM ZASSET WHERE ZTRASHEDSTATE = 0") or 0
    videos = _count(db, "SELECT COUNT(*) FROM ZASSET WHERE ZTRASHEDSTATE = 0 AND ZKIND = 1") or 0
    newest_raw = _scalar(db, "SELECT MAX(ZDATECREATED) FROM ZASSET WHERE ZTRASHEDSTATE = 0")
    newest = None
    if isinstance(newest_raw, (int, float)):
        # Photos stores seconds since 2001-01-01.
        newest = datetime(2001, 1, 1, tzinfo=UTC) + timedelta(seconds=float(newest_raw))

    # Trend, not a guess. A count that has not moved in hours is a fact; a
    # percentage against a number somebody typed in is not. Recorded here so a
    # later run can tell "finished" from "stuck" instead of inferring a cause
    # from a static number, which is exactly the mistake this replaces.
    history = _record_count(config, assets)
    expected = config.photos.expected_assets
    data: dict[str, Any] = {
        "assets": assets,
        "videos": videos,
        "newest": newest.isoformat() if newest else None,
        "expected": expected,
    }

    if assets == 0:
        return Check(
            "iCloud sync",
            FAIL,
            "the library is empty",
            "Enable iCloud Photos: Photos > Settings > iCloud.",
            data,
        )

    data["history"] = history

    if videos == 0:
        return Check(
            "iCloud sync",
            WARN,
            f"{assets:,} assets but zero videos",
            "Video syncs late, so zero videos almost always means the sync has not "
            "finished. Leave Photos open and the Mac awake.",
            data,
        )

    pending = _pending_uploads(config.photos.library_path)
    if pending:
        return Check(
            "iCloud sync",
            WARN,
            f"{assets:,} assets, {pending:,} upload job(s) still queued",
            "Photos has work outstanding. Leave it open and the Mac awake.",
            data,
        )

    stale_days = (datetime.now(UTC) - newest).days if newest else None
    if stale_days is not None and stale_days > 7:
        return Check(
            "iCloud sync",
            WARN,
            f"{assets:,} assets, newest is {stale_days} days old ({newest:%Y-%m-%d})",
            "The library looks detached from the phone. Check iCloud Photos is on "
            "and that both devices use the same Apple Account.",
            data,
        )

    if expected and assets < expected * 0.98:
        stable = _hours_unchanged(history)
        if stable is not None and stable >= 1:
            return Check(
                "iCloud sync",
                WARN,
                f"{assets:,} assets, unchanged for {stable:.1f} hours, "
                f"against photos.expected_assets of {expected:,}",
                "Either the sync has finished and expected_assets is wrong, or it "
                "has stopped. Photos has no upload work queued, which points at the "
                "first. Check the count in the Photos app and correct "
                "photos.expected_assets, or clear it to stop comparing.",
                data,
            )
        return Check(
            "iCloud sync",
            WARN,
            f"{assets:,} of ~{expected:,} expected ({assets * 100 // expected}%), still moving",
            "Still syncing. Leave Photos open and the Mac awake.",
            data,
        )

    gaps = _screenshot_gap(db)
    data["screenshotGapYears"] = gaps
    if gaps:
        return Check(
            "iCloud sync",
            WARN,
            f"{assets:,} assets, but {len(gaps)} year(s) hold almost no screenshots: "
            f"{', '.join(gaps)}",
            "A phone in daily use produces screenshots every year, so this library "
            "is very likely missing those years rather than recording a change of "
            "habit. Anything the library has not received cannot be backed up or "
            "removed by this tool. Leave Photos open until it catches up, and check "
            "free disk space: macOS throttles iCloud downloads when the disk is full.",
            data,
        )

    detail = f"{assets:,} assets, {videos:,} videos"
    if newest:
        detail += f", newest {newest:%Y-%m-%d}"

    if not expected:
        # The failure this check was built to stop. With no reference it cannot
        # tell a complete library from a partial one, and it used to say [ok]
        # anyway -- on a library holding 84% of the phone. Absence of a
        # reference is not a pass.
        return Check(
            "iCloud sync",
            WARN,
            f"{detail} -- completeness not verified",
            "Set photos.expected_assets to the number your phone reports, from "
            "Photos > Library on the iPhone. Until it is set this check can see "
            "that the library has assets but not whether it has all of them, and "
            "planning a cleanup against a partial library silently does part of "
            "the job.",
            data,
        )

    return Check("iCloud sync", PASS, detail, "", data)


def _pending_uploads(library: Path) -> int | None:
    """Photos' own upload queue. Empty means it has nothing left to send."""
    db = _copy_photos_db(library)
    if db is None:
        return None
    return _count(db, "SELECT COUNT(*) FROM ZASSETRESOURCEUPLOADJOBREQUEST")


def _record_count(config: Config, assets: int) -> list[dict[str, Any]]:
    """Append the current count to a short history kept in the ledger."""
    from .db.database import Database, DatabaseError, utcnow

    try:
        db = Database(config.database.path).connect()
        db.migrate()
    except (DatabaseError, sqlite3.Error):
        return []
    try:
        raw = db.get_setting("library_count_history") or "[]"
        history = json.loads(raw)
        if not isinstance(history, list):
            history = []
        if not history or history[-1].get("assets") != assets:
            history.append({"at": utcnow(), "assets": assets})
        history = history[-40:]
        db.set_setting("library_count_history", json.dumps(history))
        return history
    except (json.JSONDecodeError, sqlite3.Error):
        return []
    finally:
        db.close()


def _hours_unchanged(history: list[dict[str, Any]]) -> float | None:
    """How long the asset count has sat at its current value."""
    if len(history) < 1:
        return None
    try:
        last_change = datetime.fromisoformat(history[-1]["at"])
    except (KeyError, ValueError):
        return None
    if last_change.tzinfo is None:
        last_change = last_change.replace(tzinfo=UTC)
    return (datetime.now(UTC) - last_change).total_seconds() / 3600


def check_disk(config: Config) -> Check:
    target = config.archive.local_path
    probe = target if target.exists() else target.parent
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    usage = shutil.disk_usage(probe)
    data = {"free": usage.free, "total": usage.total, "path": str(probe)}

    if usage.free < MIN_FREE_BYTES:
        return Check(
            "disk headroom",
            FAIL,
            f"{_human(usage.free)} free on {probe}",
            f"A chunked fetch needs at least {_human(MIN_FREE_BYTES)} to work in. "
            "Free space, or point archive.local_path at a larger volume.",
            data,
        )
    return Check("disk headroom", PASS, f"{_human(usage.free)} free on {probe}", "", data)


def check_archive_path(config: Config) -> Check:
    target = config.archive.local_path
    try:
        target.mkdir(parents=True, exist_ok=True)
        probe = target / ".iim-write-probe"
        probe.write_text("ok")
        probe.unlink()
    except OSError as exc:
        return Check(
            "archive path",
            FAIL,
            f"{target} is not writable: {exc}",
            "Choose a writable archive.local_path, or fix the permissions.",
        )
    return Check("archive path", PASS, str(target))


def check_helper(config: Config) -> Check:
    """The PhotoKit helper, which is built from source rather than shipped."""
    root = Path(__file__).resolve().parent.parent.parent
    binary = root / "spikes" / "iimphotos" / ".build" / "release" / "iimphotos"
    if not binary.exists():
        return Check(
            "PhotoKit helper", FAIL, "not built", "cd spikes/iimphotos && swift build -c release"
        )
    if not shutil.which("swift"):
        return Check(
            "PhotoKit helper",
            WARN,
            "built, but Swift is no longer installed",
            "Install Xcode command line tools: xcode-select --install",
        )
    return Check("PhotoKit helper", PASS, str(binary))


def check_photos_authorisation(config: Config) -> Check:
    root = Path(__file__).resolve().parent.parent.parent
    binary = root / "spikes" / "iimphotos" / ".build" / "release" / "iimphotos"
    if not binary.exists():
        return Check(
            "Photos authorisation",
            WARN,
            "cannot check, the helper is not built",
            "cd spikes/iimphotos && swift build -c release",
        )
    try:
        result = subprocess.run([str(binary), "auth"], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Check(
            "Photos authorisation",
            FAIL,
            f"the helper did not answer: {exc}",
            "Run it directly to see the prompt: spikes/iimphotos/.build/release/iimphotos auth",
        )
    if result.returncode == 0:
        return Check("Photos authorisation", PASS, "authorized")
    return Check(
        "Photos authorisation",
        FAIL,
        (result.stdout or result.stderr).strip()[:120],
        "Grant full Photos access: System Settings > Privacy & Security > Photos. "
        "Limited access is not enough.",
    )


def check_cloud(config: Config) -> Check:
    if not config.cloud.enabled:
        return Check("cloud remote", PASS, "cloud backup is off")
    if not shutil.which("rclone"):
        return Check("cloud remote", FAIL, "rclone is not installed", "brew install rclone")
    try:
        result = subprocess.run(
            ["rclone", "listremotes"], capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Check(
            "cloud remote", FAIL, f"rclone did not answer: {exc}", "Check your rclone install."
        )
    remotes = [r.rstrip(":") for r in result.stdout.split()]
    if config.cloud.remote not in remotes:
        return Check(
            "cloud remote",
            FAIL,
            f"rclone has no remote named {config.cloud.remote!r}",
            f"Configure it with: rclone config. Known remotes: {', '.join(remotes) or 'none'}",
            {"known": remotes},
        )
    return Check("cloud remote", PASS, f"{config.cloud.remote} ({config.cloud.provider})")


def check_ledger_conflicts(config: Config) -> Check:
    """Rows of the ledger that contradict each other.

    Offline and instant, and it is the check that would have caught the
    2026-09-17 collision on the day it happened: eight pairs of photographs
    sharing one remote path each, every row reading CLOUD_VERIFIED. See
    `audit.conflicts`. The full remote diff is `iphone-image audit`, which
    costs a listing of the whole archive and so is not run from here.
    """
    from .audit import conflicts
    from .db.database import Database

    path = config.database.path
    if not path.is_file():
        return Check("ledger consistency", PASS, "no ledger yet, nothing to check")
    try:
        db = Database(path).connect()
    except Exception as exc:
        return Check(
            "ledger consistency",
            FAIL,
            f"the ledger could not be opened: {exc}",
            "A check that cannot run is not a pass. Fix the path in database.path.",
        )
    try:
        report = conflicts(db)
    finally:
        db.close()

    # The count is part of the result, not decoration: a check reporting OK
    # over 0 rows has stopped covering anything and must not look identical to
    # one that examined the whole ledger.
    scope = (
        f"{len(report.ran)} check(s) over {report.rows_examined:,} row(s)"
        if report.rows_examined
        else "0 rows -- the ledger is empty"
    )
    if report.skipped:
        # An unavailable check is not a pass. Say which one and why.
        return Check(
            "ledger consistency",
            WARN,
            f"{len(report.skipped)} check(s) could not run, {scope}. {report.skipped[0]}",
            "Run any command against the ledger to apply pending migrations, then re-run.",
            {"skipped": report.skipped, "rowsExamined": report.rows_examined},
        )
    if report.ok:
        return Check("ledger consistency", PASS, f"no contradictions, {scope}")
    worst = report.conflicts[0]
    return Check(
        "ledger consistency",
        FAIL,
        f"{len(report.conflicts)} contradiction(s), {scope}. First: {worst.kind} -- {worst.detail}",
        "Run: iphone-image audit. Do not run remove-from-iphone until this is "
        "resolved: a duplicate cloud path means a row reads CLOUD_VERIFIED over "
        "a file that belongs to a different photograph.",
        {
            "conflicts": [
                {"kind": c.kind, "detail": c.detail, "assetIds": c.asset_ids[:20]}
                for c in report.conflicts[:20]
            ],
            "rowsExamined": report.rows_examined,
        },
    )


def check_logging(config: Config) -> Check:
    """Logs are the only record of what happened outside the ledger."""
    directory = config.logging.path
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / ".iim-log-probe"
        probe.write_text("ok")
        probe.unlink()
    except OSError as exc:
        return Check(
            "logging",
            WARN,
            f"{directory} is not writable: {exc}",
            "The tool still runs, but nothing is recorded. Fix the permissions "
            "or point logging.path somewhere writable.",
        )
    return Check("logging", PASS, f"{config.logging.level} to {directory}")


CHECKS: list[Callable[[Config], Check]] = [
    check_platform,
    check_tools,
    check_apple_account,
    check_library,
    check_icloud_mode,
    check_sync,
    check_disk,
    check_archive_path,
    check_logging,
    check_helper,
    check_photos_authorisation,
    check_cloud,
    check_ledger_conflicts,
]


def run_all(config: Config, *, skip_slow: bool = False) -> list[Check]:
    """Run every check. A crashing check reports FAIL rather than taking the run down."""
    results: list[Check] = []
    for func in CHECKS:
        if skip_slow and func is check_photos_authorisation:
            continue
        try:
            results.append(func(config))
        except Exception as exc:  # a broken check must not hide the others
            results.append(
                Check(
                    func.__name__.removeprefix("check_").replace("_", " "),
                    FAIL,
                    f"the check itself failed: {type(exc).__name__}: {exc}",
                    "This is a bug. Please report it.",
                )
            )
    return results
